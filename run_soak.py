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


def load_soak_data():
    if SOAK_LOG.exists():
        with open(SOAK_LOG) as f:
            return json.load(f)
    return {"cycles": [], "first_run": None, "symbol_info_logged": False}


def save_soak_data(data):
    with open(SOAK_LOG, "w") as f:
        json.dump(data, f, indent=2, default=str)


def main():
    soak = load_soak_data()
    now = datetime.now(timezone.utc)

    if soak["first_run"] is None:
        soak["first_run"] = now.isoformat()

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
        "error": None,
    }

    connected, reconnect_info = mt5_connector.ensure_connected()

    if reconnect_info and reconnect_info.get("reconnected"):
        telegram.alert_reconnected(reconnect_info["downtime_seconds"])

    if not connected:
        cycle["error"] = "Connection failed"
        failures = reconnect_info.get("consecutive_failures", 1) if reconnect_info else 1
        telegram.alert_disconnected(failures)
        soak["cycles"].append(cycle)
        save_soak_data(soak)
        logger.error("Soak cycle failed: MT5 not connected")
        mt5_connector.shutdown()
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

    if total % 24 == 0:
        spreads = [c["spread"] for c in soak["cycles"] if c["spread"] is not None]
        telegram.alert_soak_status({
            "uptime_pct": uptime_pct,
            "total_cycles": total,
            "success_cycles": success,
            "fail_cycles": fail,
            "symbol": sym["name"] if sym else "N/A",
            "spread": cycle.get("spread", "N/A"),
            "time_offset": f"{offset:.2f}s" if offset else "N/A",
        })

    logger.info(f"Soak cycle #{total}: uptime={uptime_pct:.1f}% ({success}/{total})")

    mt5_connector.shutdown()


if __name__ == "__main__":
    main()
