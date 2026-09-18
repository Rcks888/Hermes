#!/usr/bin/env python3
"""
Hermes V1 — Pullback Strategy (Alert Only)

Runs every 15 minutes via cron. Detects pullback setups on XAU/USD M15
and sends Telegram alerts. No auto-execution in V1.
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
from engine import market_structure
from engine import indicators
from engine import strategy
from engine import risk_manager
from alerts import telegram

TRADES_LOG = PROJECT_ROOT / "logs" / "trades.json"
SIGNAL_KEYS_FILE = PROJECT_ROOT / "logs" / "signal_keys.json"
EVENTS_LOG = PROJECT_ROOT / "logs" / "events.jsonl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(PROJECT_ROOT / "logs" / "hermes.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("hermes.main")


def log_event(payload):
    """Append-only audit trail. One JSON object per line, never rewritten,
    so a crash mid-write cannot truncate prior history."""
    record = {"ts": datetime.now(timezone.utc).isoformat()}
    record.update(payload)
    try:
        with open(EVENTS_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        logger.error(f"Failed to append event log: {e}")


def load_params():
    with open(PROJECT_ROOT / "config" / "strategy_params.json") as f:
        return json.load(f)


def load_settings():
    with open(PROJECT_ROOT / "config" / "settings.json") as f:
        return json.load(f)


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


def is_duplicate_signal(signal_key):
    if SIGNAL_KEYS_FILE.exists():
        with open(SIGNAL_KEYS_FILE) as f:
            keys = json.load(f)
    else:
        keys = []
    return signal_key in keys


def record_signal_key(signal_key):
    if SIGNAL_KEYS_FILE.exists():
        with open(SIGNAL_KEYS_FILE) as f:
            keys = json.load(f)
    else:
        keys = []
    keys.append(signal_key)
    keys = keys[-500:]
    with open(SIGNAL_KEYS_FILE, "w") as f:
        json.dump(keys, f)


def log_trade(signal, approved, reject_reason, risk_details, spread, session):
    if TRADES_LOG.exists():
        with open(TRADES_LOG) as f:
            trades = json.load(f)
    else:
        trades = []

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal_bar_time": signal["signal_bar_time"],
        "strategy": signal["strategy"],
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],
        "rr_ratio": signal["rr_ratio"],
        "atr": signal["atr"],
        "trend": signal["trend"],
        "bos_count": signal["bos_count"],
        "vwap": signal["vwap"],
        "volume": signal["volume"],
        "vol_avg": signal["vol_avg"],
        "at_sr_zone": signal["at_sr_zone"],
        "candle_pattern": signal["candle_pattern"],
        "spread_at_signal": spread,
        "session_tag": session,
        "approved": approved,
        "reject_reason": reject_reason,
        "position_size": risk_details.get("position_size") if risk_details else None,
        "risk_amount": risk_details.get("risk_amount") if risk_details else None,
        "outcome": None,
        "notes": "",
    }
    trades.append(entry)

    with open(TRADES_LOG, "w") as f:
        json.dump(trades, f, indent=2, default=str)


def send_signal_alert(signal, risk_details, spread, session):
    direction_emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    patterns = ", ".join(signal["candle_pattern"]) if signal["candle_pattern"] else "—"

    text = (
        f"🔱 HERMES — SIGNAL DETECTED\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Strategy: Pullback Setup ({signal['direction']})\n"
        f"Pair: XAU/USD\n"
        f"Entry: {signal['entry']}\n"
        f"Stop Loss: {signal['sl']} ({signal['sl_distance']:+.2f})\n"
        f"Take Profit: {signal['tp']} ({signal['tp_distance']:+.2f})\n"
        f"R:R: {signal['rr_ratio']}:1\n"
        f"Risk: {risk_details.get('risk_amount', 'N/A')}\n"
        f"Position Size: {risk_details.get('position_size', 'N/A')} lots\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Volume: {'✅' if signal['volume'] > (signal['vol_avg'] or 0) else '⚠️'} {signal['volume']}/{signal['vol_avg']}\n"
        f"VWAP: {'✅' if signal['vwap'] else '—'} {signal['vwap']}\n"
        f"Trend: {'✅' if signal['trend'] else '—'} {signal['trend']} ({signal['bos_count']} BOS)\n"
        f"Pattern: {patterns}\n"
        f"S/R Zone: {'✅' if signal['at_sr_zone'] else '❌'}\n"
        f"Spread: {spread}\n"
        f"Session: {session}\n"
        f"ATR: {signal['atr']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    telegram.send_message(text)


def main():
    params = load_params()
    settings = load_settings()
    now = datetime.now(timezone.utc)
    session = get_session_tag(now.hour)

    connected, reconnect_info = mt5_connector.ensure_connected()
    if reconnect_info and reconnect_info.get("reconnected"):
        telegram.alert_reconnected(reconnect_info["downtime_seconds"])
    if not connected:
        failures = reconnect_info.get("consecutive_failures", 1) if reconnect_info else 1
        telegram.alert_disconnected(failures)
        logger.error("Hermes cycle skipped: MT5 not connected")
        log_event({"event": "CYCLE_ABORTED", "session": session, "reason": "mt5_not_connected", "consecutive_failures": failures})
        return

    instrument = params.get("instrument", "XAUUSD")
    candle_count = params.get("candle_count", 200)

    candles = data_feed.get_candles(instrument, count=candle_count)
    if candles is None or len(candles) < 50:
        got = 0 if candles is None else len(candles)
        logger.error(f"Not enough candle data (got {got}, need 50)")
        log_event({"event": "CYCLE_ABORTED", "session": session, "reason": f"insufficient_candles ({got})"})
        return

    account = mt5_connector.get_account_info()
    if not account:
        logger.error("Failed to get account info")
        log_event({"event": "CYCLE_ABORTED", "session": session, "reason": "account_info_unavailable"})
        return

    sym_info = mt5_connector.get_symbol_info(instrument)
    spread_points = sym_info["spread"] if sym_info else None

    structure = market_structure.analyze(candles, params.get("swing_lookback", 3))

    candles = indicators.calculate_vwap(candles)
    candles = indicators.volume_average(candles, params.get("volume_avg_period", 20))
    candles = indicators.calculate_atr(candles)

    zones = indicators.find_sr_zones(
        candles, structure["swing_highs"], structure["swing_lows"],
        params.get("sr_zone_cluster_pct", 0.5),
        params.get("sr_min_touches", 2),
    )

    signal, reason = strategy.evaluate(candles, structure, zones, params)

    if signal is None:
        logger.info(f"No signal | session={session} | blocked_at={reason}")
        log_event({
            "event": "NO_SIGNAL",
            "session": session,
            "blocked_at": reason,
            "trend": structure["trend"],
            "trend_confirmed": structure["trend_confirmed"],
            "bar_time": str(candles.iloc[-1]["datetime"]),
            "close": round(float(candles.iloc[-1]["close"]), 2),
            "spread_points": spread_points,
        })
        return

    signal_key = f"{signal['signal_bar_time']}_{signal['strategy']}"
    if is_duplicate_signal(signal_key):
        logger.info(f"Duplicate signal skipped: {signal_key}")
        return

    sym_params = {}
    sym_params.update(params)
    if sym_info:
        sym_params["tick_value"] = sym_info.get("tick_value", 0.1)
        sym_params["tick_size"] = sym_info.get("tick_size", 0.01)
        sym_params["lot_min"] = sym_info.get("volume_min", 0.01)
        sym_params["lot_step"] = sym_info.get("volume_step", 0.01)

    approved, risk_reason, risk_details = risk_manager.approve(
        signal, account, spread_points, session, sym_params
    )

    log_trade(signal, approved, risk_reason, risk_details, spread_points, session)
    record_signal_key(signal_key)

    log_event({
        "event": "SIGNAL_APPROVED" if approved else "SIGNAL_REJECTED",
        "session": session,
        "reason": risk_reason,
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],
        "rr_ratio": signal["rr_ratio"],
        "atr": signal["atr"],
        "volume": signal["volume"],
        "vol_avg": signal["vol_avg"],
        "spread_points": spread_points,
        "position_size": risk_details.get("position_size"),
        "bar_time": signal["signal_bar_time"],
    })

    if approved:
        send_signal_alert(signal, risk_details, spread_points, session)
        logger.info(f"SIGNAL SENT: {signal['direction']} @ {signal['entry']}")
    else:
        logger.info(f"Signal rejected: {risk_reason}")


if __name__ == "__main__":
    main()
