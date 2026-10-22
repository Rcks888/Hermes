import fcntl
import json
import time
import logging
import subprocess
import os
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import rpyc

logger = logging.getLogger("hermes.mt5")

mt5 = None
_config = None
_conn = None
_rpyc_process = None

RETRY_BACKOFF = [2, 5, 10]
RPYC_HOST = "localhost"
RPYC_PORT = 18812

# Cross-process failure state. run_soak (:07) and run_hermes (:08) are separate
# cron processes, so module globals reset every run: the failure count was
# always 1 and no run ever saw a reconnect. Escalation tiers and the reconnect
# alert were therefore dead (found 20 Oct). Failures are counted per 15-minute
# slot, so the soak/strategy pair of one slot counts once.
FAILURE_STATE = Path(__file__).parent.parent / "logs" / "mt5_failure_state.json"
SLOT_SECONDS = 900

# 22 Oct pile-up controls. After needrestart restarted xvfb.service, every
# relaunch hit the 60 s IPC timeout and left another terminal64.exe behind;
# runs took up to 968 s, overlapped, and stacked terminals until the host
# stopped answering SSH. One MT5 run at a time, a wall-clock retry budget that
# keeps a run inside its slot, and a stuck terminal is killed before retrying.
MT5_LOCK = Path(__file__).parent.parent / "logs" / "mt5.lock"
MT5_LOCK_WAIT_SECONDS = 50
RETRY_BUDGET_SECONDS = 240
IPC_TIMEOUT_CODE = -10003

_lock_handle = None
_last_init_error = None
_monotonic = time.monotonic


def _utcnow():
    return datetime.now(timezone.utc)


def acquire_run_lock(wait_seconds=None):
    """Take the cross-process MT5 lock for the life of this process.

    Returns False if another run still holds it after wait_seconds; the caller
    must then skip its cycle rather than start a second terminal. The kernel
    releases the lock when the process exits, including on a crash.
    """
    global _lock_handle
    if _lock_handle is not None:
        return True
    if wait_seconds is None:
        wait_seconds = MT5_LOCK_WAIT_SECONDS
    MT5_LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = open(MT5_LOCK, "a")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_handle = handle
            return True
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                handle.close()
                logger.error(f"MT5 lock still held by another run after {wait_seconds}s")
                return False
            time.sleep(min(0.2, remaining))


def _is_ipc_timeout(error):
    try:
        return error is not None and int(error[0]) == IPC_TIMEOUT_CODE
    except (TypeError, ValueError, IndexError):
        return False


def _kill_stuck_terminals():
    """Kill terminal64.exe after an IPC timeout so the next attempt starts one
    fresh terminal instead of adding another to the hung one."""
    try:
        result = subprocess.run(["pkill", "-f", r"terminal64\.exe"],
                                capture_output=True, timeout=10)
        logger.warning(f"Killed stuck MT5 terminal after IPC timeout (pkill rc={result.returncode})")
        time.sleep(3)
    except Exception as exc:
        logger.warning(f"Could not kill stuck MT5 terminal: {exc}")


def _load_failure_state():
    if FAILURE_STATE.exists():
        try:
            with open(FAILURE_STATE) as f:
                state = json.load(f)
            if isinstance(state, dict):
                return state
        except Exception as exc:
            logger.warning(f"MT5 failure state unreadable ({exc}); treating as empty")
    return {}


def _save_failure_state(state):
    try:
        FAILURE_STATE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(FAILURE_STATE.parent), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, str(FAILURE_STATE))
    except Exception as exc:
        logger.warning(f"Could not persist MT5 failure state: {exc}")


def _record_failure(now):
    """Count this failed run; return the number of consecutive failed slots."""
    state = _load_failure_state()
    if not state.get("first_failure"):
        state = {"first_failure": now.isoformat(), "slots": 0, "runs": 0}
    slot = int(now.timestamp() // SLOT_SECONDS)
    if state.get("last_slot") != slot:
        state["slots"] = state.get("slots", 0) + 1
        state["last_slot"] = slot
    state["runs"] = state.get("runs", 0) + 1
    state["last_failure"] = now.isoformat()
    _save_failure_state(state)
    return state["slots"]


def _record_success(now):
    """Clear any outage; return the outage record if one was open, else None."""
    state = _load_failure_state()
    if not state.get("first_failure"):
        return None
    try:
        FAILURE_STATE.unlink()
    except OSError as exc:
        logger.warning(f"Could not clear MT5 failure state: {exc}")
    try:
        first = datetime.fromisoformat(state["first_failure"])
        state["downtime_seconds"] = max(0.0, (now - first).total_seconds())
    except (TypeError, ValueError):
        state["downtime_seconds"] = 0.0
    return state


def _success_info(rpyc_restarted):
    info = {"reconnected": False, "rpyc_restarted": rpyc_restarted, "consecutive_failures": 0}
    outage = _record_success(_utcnow())
    if outage is not None:
        info["reconnected"] = True
        info["downtime_seconds"] = outage["downtime_seconds"]
        logger.info(
            f"MT5 reconnected: first failed check {outage['first_failure']}, "
            f"{outage.get('slots', 0)} failed slot(s), {outage.get('runs', 0)} failed run(s)")
    return info

RPYC_SERVER_CODE = """
import rpyc
from rpyc.utils.server import ThreadedServer
import MetaTrader5 as mt5
class MT5Service(rpyc.Service):
    exposed_mt5 = mt5
t = ThreadedServer(MT5Service, hostname='localhost', port={port},
    protocol_config={{'allow_all_attrs':True,'allow_public_attrs':True,'allow_pickle':True}})
t.start()
"""


def _load_config():
    global _config
    if _config is None:
        cfg_path = Path(__file__).parent.parent / "config" / "settings.json"
        with open(cfg_path) as f:
            _config = json.load(f)["mt5"]
    return _config


def _is_port_open(host, port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def start_rpyc_server():
    global _rpyc_process

    if _is_port_open(RPYC_HOST, RPYC_PORT):
        logger.info("rpyc server already running")
        return True

    if _rpyc_process and _rpyc_process.poll() is None:
        return True

    logger.info("Starting rpyc MT5 bridge server via Wine Python...")
    env = os.environ.copy()
    env["DISPLAY"] = ":99"
    code = RPYC_SERVER_CODE.format(port=RPYC_PORT)
    _rpyc_process = subprocess.Popen(
        ["wine", "C:\\Program Files\\Python312\\python.exe", "-c", code],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for i in range(10):
        time.sleep(1)
        if _is_port_open(RPYC_HOST, RPYC_PORT):
            logger.info(f"rpyc server started on port {RPYC_PORT}")
            return True
        if _rpyc_process.poll() is not None:
            logger.error("rpyc server process exited prematurely")
            return False

    logger.error("rpyc server did not start within 10 seconds")
    return False


def initialize():
    global mt5, _conn, _last_init_error
    _last_init_error = None
    cfg = _load_config()

    if not start_rpyc_server():
        return False

    try:
        _conn = rpyc.connect(
            RPYC_HOST, RPYC_PORT,
            config={
                "allow_all_attrs": True,
                "allow_public_attrs": True,
                "allow_pickle": True,
                "sync_request_timeout": 120,
            }
        )
        mt5 = _conn.root.exposed_mt5
    except Exception as e:
        logger.error(f"rpyc connect failed: {e}")
        return False

    try:
        if not mt5.initialize(path=cfg.get("path", "")):
            _last_init_error = tuple(mt5.last_error())
            logger.error(f"MT5 initialize failed: {_last_init_error}")
            return False
    except Exception as e:
        logger.error(f"MT5 initialize error: {e}")
        return False

    try:
        if not mt5.login(login=cfg["login"], password=cfg["password"], server=cfg["server"]):
            logger.error(f"MT5 login failed: {mt5.last_error()}")
            mt5.shutdown()
            return False
    except Exception as e:
        logger.error(f"MT5 login error: {e}")
        return False

    logger.info(f"MT5 connected: account={cfg['login']} server={cfg['server']}")
    return True


def is_connected():
    try:
        info = mt5.terminal_info()
        if info is None:
            return False
        return info.connected
    except Exception:
        return False


def ensure_connected():
    rpyc_restarted = False
    cold_start = (mt5 is None)

    if is_connected():
        return True, _success_info(rpyc_restarted)

    try:
        if mt5 is not None:
            mt5.shutdown()
    except Exception:
        pass

    if not _is_port_open(RPYC_HOST, RPYC_PORT):
        rpyc_restarted = True
        logger.warning("rpyc server down, restarting...")

    start = _monotonic()
    for i, delay in enumerate(RETRY_BACKOFF):
        spent = _monotonic() - start
        if i > 0 and spent + delay > RETRY_BUDGET_SECONDS:
            logger.warning(
                f"MT5 retry budget {RETRY_BUDGET_SECONDS}s spent ({spent:.0f}s, "
                f"{i} attempt(s)); giving up this run")
            break
        logger.warning(f"MT5 reconnect attempt {i+1}/3 (backoff {delay}s)")
        time.sleep(delay)
        if initialize() and is_connected():
            if cold_start:
                logger.info("MT5 connected (cold start)")
            return True, _success_info(rpyc_restarted)
        if _is_ipc_timeout(_last_init_error):
            try:
                mt5.shutdown()
            except Exception:
                pass
            _kill_stuck_terminals()

    failures = _record_failure(_utcnow())
    logger.error(f"MT5 reconnect failed. Consecutive failures: {failures} (15-min slots)")

    if failures >= 4:
        logger.critical(f"CRITICAL: {failures} consecutive failed slots. Manual intervention needed.")

    return False, {"consecutive_failures": failures, "rpyc_restarted": rpyc_restarted}


def get_server_time_offset():
    """Server clock minus VPS clock, in SECONDS.

    The unit is documented because it has already caused a defect. A caller
    fed this value to timedelta(hours=...) and produced a 38,867,525 second
    scheduling delay from a true one of roughly three hours: 10799 seconds is
    UTC+3, and it reads as an entirely plausible number of hours. Use
    get_server_time_offset_hours() where hours are wanted.
    """
    info = mt5.symbol_info_tick("XAUUSD")
    if info is None:
        return None
    server_time = datetime.fromtimestamp(info.time, tz=timezone.utc)
    vps_time = datetime.now(timezone.utc)
    offset = (server_time - vps_time).total_seconds()
    return offset


def get_server_time_offset_hours():
    """Server clock minus VPS clock, in hours. None when unavailable."""
    secs = get_server_time_offset()
    return None if secs is None else secs / 3600.0


def get_symbol_info(symbol="XAUUSD"):
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol {symbol} not found: {mt5.last_error()}")
        return None
    return {
        "name": info.name,
        "point": info.point,
        "digits": info.digits,
        "tick_size": info.trade_tick_size,
        "tick_value": info.trade_tick_value,
        "contract_size": info.trade_contract_size,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "spread": info.spread,
        "trade_mode": info.trade_mode,
    }


def get_account_info():
    info = mt5.account_info()
    if info is None:
        logger.error(f"Account info failed: {mt5.last_error()}")
        return None
    return {
        "login": info.login,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "margin_free": info.margin_free,
        "leverage": info.leverage,
        "currency": info.currency,
        "server": info.server,
        "trade_mode": info.trade_mode,
    }


def shutdown():
    global _conn
    try:
        mt5.shutdown()
    except Exception:
        pass
    try:
        if _conn:
            _conn.close()
            _conn = None
    except Exception:
        pass
    logger.info("MT5 shutdown")
