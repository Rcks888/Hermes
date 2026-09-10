import json
import time
import logging
import subprocess
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

import rpyc

logger = logging.getLogger("hermes.mt5")

mt5 = None
_config = None
_conn = None
_consecutive_failures = 0
_last_disconnect_time = None
_rpyc_process = None

RETRY_BACKOFF = [2, 5, 10]
RPYC_HOST = "localhost"
RPYC_PORT = 18812

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
    global mt5, _conn
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
            logger.error(f"MT5 initialize failed: {mt5.last_error()}")
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
    global _consecutive_failures, _last_disconnect_time

    if is_connected():
        if _consecutive_failures > 0:
            downtime = 0
            if _last_disconnect_time:
                downtime = (datetime.now(timezone.utc) - _last_disconnect_time).total_seconds()
            _consecutive_failures = 0
            _last_disconnect_time = None
            logger.info("MT5 reconnected")
            return True, {"reconnected": True, "downtime_seconds": downtime}
        return True, None

    if _last_disconnect_time is None:
        _last_disconnect_time = datetime.now(timezone.utc)

    try:
        if mt5 is not None:
            mt5.shutdown()
    except Exception:
        pass

    for i, delay in enumerate(RETRY_BACKOFF):
        logger.warning(f"MT5 reconnect attempt {i+1}/3 (backoff {delay}s)")
        time.sleep(delay)
        if initialize() and is_connected():
            downtime = (datetime.now(timezone.utc) - _last_disconnect_time).total_seconds()
            _consecutive_failures = 0
            _last_disconnect_time = None
            logger.info(f"MT5 reconnected after {downtime:.0f}s")
            return True, {"reconnected": True, "downtime_seconds": downtime}

    _consecutive_failures += 1
    logger.error(f"MT5 reconnect failed. Consecutive failures: {_consecutive_failures}")
    return False, {"consecutive_failures": _consecutive_failures}


def get_server_time_offset():
    info = mt5.symbol_info_tick("XAUUSD")
    if info is None:
        return None
    server_time = datetime.fromtimestamp(info.time, tz=timezone.utc)
    vps_time = datetime.now(timezone.utc)
    offset = (server_time - vps_time).total_seconds()
    return offset


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
