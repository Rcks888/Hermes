#!/usr/bin/env python3
"""
Hermes Gate 0: MT5 Stability Soak

Runs every 15 minutes via cron. Logs connection health, symbol specs,
spread samples, and server time offset. No strategy logic.

After 3-7 days, review logs/soak.json to determine:
  - Whether Wine + MT5 is stable enough
  - Exact symbol string and contract specs
  - Realistic spread distribution by session
  - Server time vs VPS time drift
"""

import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import mt5_connector
from engine import data_feed
from alerts import telegram

SOAK_LOG = PROJECT_ROOT / "logs" / "soak.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(PROJECT_ROOT / "logs" / "hermes.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("hermes.soak")


def get_session_tag(utc_hour):
    myt_hour = (utc_hour + 8) % 24
    if 7 <= myt_hour < 15:
        return "ASIA"
    elif 15 <= myt_hour < 20:
        return "LONDON"
    elif 20 <= myt_hour or myt_hour < 5:
        return "NY"
    else:
        return "OFF"


def get_resource_stats():
    stats = {}
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                meminfo[parts[0].rstrip(":")] = int(parts[1])
            stats["ram_total_mb"] = meminfo.get("MemTotal", 0) // 1024
            stats["ram_free_mb"] = meminfo.get("MemAvailable", 0) // 1024
            stats["swap_used_mb"] = (meminfo.get("SwapTotal", 0) - meminfo.get("SwapFree", 0)) // 1024
    except Exception:
        pass
    try:
        load1, load5, load15 = os.getloadavg()
        stats["load_avg_1m"] = round(load1, 2)
        stats["load_avg_5m"] = round(load5, 2)
    except Exception:
        pass
    return stats


def rotate_logs_if_needed():
    log_dir = PROJECT_ROOT / "logs"
    hermes_log = log_dir / "hermes.log"
    if hermes_log.exists() and hermes_log.stat().st_size > 5 * 1024 * 1024:
        archive = log_dir / f"hermes.log.{datetime.now().strftime('%Y%m%d')}"
        shutil.move(str(hermes_log), str(archive))
        logger.info(f"Rotated hermes.log to {archive.name}")

    if SOAK_LOG.exists():
        with open(SOAK_LOG) as f:
            data = json.load(f)
        if len(data.get("cycles", [])) > 2000:
            archive = log_dir / f"soak.{datetime.now().strftime('%Y%m%d')}.json"
            shutil.copy2(str(SOAK_LOG), str(archive))
            data["cycles"] = data["cycles"][-500:]
            with open(SOAK_LOG, "w") as f:
                json.dump(data, f, indent=2, default=str)
            logger.info(f"Archived soak.json (kept last 500 cycles)")


def load_soak_data():
    if SOAK_LOG.exists():
        with open(SOAK_LOG) as f:
            return json.load(f)
    return {"cycles": [], "first_run": None, "symbol_info_logged": False}


def save_soak_data(data):
    with open(SOAK_LOG, "w") as f:
        json.dump(data, f, indent=2, default=str)


def main():
    rotate_logs_if_needed()
    soak = load_soak_data()
    now = datetime.now(timezone.utc)

    if soak["first_run"] is None:
        soak["first_run"] = now.isoformat()

    resources = get_resource_stats()

    cycle = {
        "timestamp": now.isoformat(),
        "session": get_session_tag(now.hour),
        "connected": False,
        "candle_pull": False,
        "account_info": False,
        "symbol_info": None,
        "spread": None,
        "spread_usd": None,
        "time_offset_seconds": None,
        "init_latency_s": None,
        "rpyc_restarted": False,
        "consecutive_failures": 0,
        "resources": resources,
        "error": None,
    }

    import time as _time
    t0 = _time.monotonic()
    connected, reconnect_info = mt5_connector.ensure_connected()
    init_latency = round(_time.monotonic() - t0, 2)
    cycle["init_latency_s"] = init_latency

    if reconnect_info:
        if reconnect_info.get("reconnected"):
            telegram.alert_reconnected(reconnect_info["downtime_seconds"])
        if reconnect_info.get("rpyc_restarted"):
            cycle["rpyc_restarted"] = True
        cycle["consecutive_failures"] = reconnect_info.get("consecutive_failures", 0)

    if not connected:
        cycle["error"] = "Connection failed"
        failures = reconnect_info.get("consecutive_failures", 1) if reconnect_info else 1
        telegram.alert_disconnected(failures)
        soak["cycles"].append(cycle)
        save_soak_data(soak)
        logger.error(f"Soak cycle failed: MT5 not connected (latency={init_latency}s)")
        return

    cycle["connected"] = True

    candles = data_feed.get_candles("XAUUSD", count=10)
    if candles is not None and len(candles) > 0:
        cycle["candle_pull"] = True
        logger.info(f"Candle pull OK: {len(candles)} bars, latest={candles.iloc[-1]['datetime']}")
    else:
        cycle["error"] = "Candle pull failed"

    account = mt5_connector.get_account_info()
    if account:
        cycle["account_info"] = True
        logger.info(f"Account OK: balance={account['balance']} equity={account['equity']}")
    else:
        cycle["error"] = (cycle.get("error") or "") + "; Account info failed"

    sym = mt5_connector.get_symbol_info("XAUUSD")
    if sym:
        cycle["symbol_info"] = sym
        cycle["spread"] = sym["spread"]
        if sym["point"] and sym["spread"]:
            cycle["spread_usd"] = round(sym["spread"] * sym["point"], 4)
        logger.info(
            f"Symbol OK: {sym['name']} point={sym['point']} digits={sym['digits']} "
            f"tick_size={sym['tick_size']} tick_value={sym['tick_value']} "
            f"lot_min={sym['volume_min']} lot_step={sym['volume_step']} "
            f"spread={sym['spread']} ({cycle['spread_usd']} USD)"
        )

        if not soak["symbol_info_logged"]:
            soak["symbol_specs"] = sym
            soak["symbol_info_logged"] = True
    else:
        cycle["error"] = (cycle.get("error") or "") + "; Symbol info failed"

    offset = mt5_connector.get_server_time_offset()
    if offset is not None:
        cycle["time_offset_seconds"] = round(offset, 2)
        logger.info(f"Server time offset: {offset:.2f}s (server - VPS)")

    soak["cycles"].append(cycle)
    save_soak_data(soak)

    total = len(soak["cycles"])
    success = sum(1 for c in soak["cycles"] if c["connected"])
    fail = total - success
    uptime_pct = (success / total * 100) if total > 0 else 0

    ram_free = resources.get("ram_free_mb", "?")
    swap_used = resources.get("swap_used_mb", "?")
    load1 = resources.get("load_avg_1m", "?")
    logger.info(f"Soak cycle #{total}: uptime={uptime_pct:.1f}% ({success}/{total}) init={init_latency}s RAM_free={ram_free}MB swap={swap_used}MB load={load1}")

    if isinstance(ram_free, int) and ram_free < 150:
        logger.warning(f"LOW RAM: {ram_free}MB free — Ares may be affected")
        telegram.send_message(f"⚠️ HERMES — Low RAM: {ram_free}MB free, swap={swap_used}MB. Ares may be affected.")


if __name__ == "__main__":
    main()
