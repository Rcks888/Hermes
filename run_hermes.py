#!/usr/bin/env python3
"""
Hermes V1 — Pullback Strategy (Alert Only)

Runs every 15 minutes via cron. Detects pullback setups on XAU/USD M15
and sends Telegram alerts. No auto-execution in V1.
"""

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import mt5_connector
from engine import data_feed
from engine import market_structure
from engine import indicators
from engine import strategy
from engine import risk_manager
from engine import version
from engine import regime as regime_mod
from engine import research_schema
from engine import entry_observation
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


def classify_block(reason, layer="strategy"):
    """Separate missing evidence from a genuinely failed condition.

    Absent evidence is not a pass and must never be counted as one. Delegates
    to research_schema so the live path and the research dataset share one
    implementation: two copies of this logic would drift, and the drift would
    show up as a phantom replay mismatch rather than as an obvious bug.
    """
    return research_schema.classify_blocked_at(reason, layer=layer)[0]


def config_fingerprint(params):
    """Stable hash of the effective strategy configuration.

    Identical code under different parameters is not the same system, so
    conformance must pin configuration as well as code version.
    """
    try:
        blob = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]
    except Exception:
        return "unknown"


def log_event(payload):
    """Append-only audit trail. One JSON object per line, never rewritten,
    so a crash mid-write cannot truncate prior history."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "code_version": version.get_code_version(),
    }
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


def bar_close_time(bar_time, params):
    """When the evaluated bar actually closed.

    MT5 timestamps identify the bar OPEN, so the close is open + one period.
    Recording it explicitly removes the ambiguity that makes scheduling delay
    impossible to reconstruct after the fact.
    """
    try:
        bt = pd.Timestamp(str(bar_time))
        minutes = int(params.get("timeframe_minutes", 15))
        return str(bt + pd.Timedelta(minutes=minutes))
    except Exception:  # noqa: BLE001 - never break a cycle over a log field
        return None


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
        "tp_rr_multiple": signal["tp_rr_multiple"],
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
        f"Target: {signal['tp_rr_multiple']}R (fixed-R exit policy)\n"
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
    config_hash = config_fingerprint(params)
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

    # 0g-1. Observational timing only. The stopwatch records; it decides
    # nothing. Durations come from a monotonic clock so an NTP correction
    # cannot masquerade as bridge latency.
    sw = entry_observation.Stopwatch()
    sw.mark("evaluation_start_time")

    diag = {}
    signal, reason = strategy.evaluate(candles, structure, zones, params, diag)
    sw.mark("evaluation_end_time")

    # Bar identity and raw inputs. A candle hash lets replay prove it read the
    # same bar rather than merely a bar with the same timestamp.
    last = candles.iloc[-1]
    ohlc = {
        "open": round(float(last["open"]), 2),
        "high": round(float(last["high"]), 2),
        "low": round(float(last["low"]), 2),
        "close": round(float(last["close"]), 2),
        "tick_volume": float(last.get("volume") or 0),
        "bar_spread": (int(last["spread"]) if last.get("spread") is not None
                       and not pd.isna(last.get("spread")) else None),
    }
    # 0g-1. Scheduling delay needs the server offset applied explicitly: bar
    # timestamps carry a +00:00 suffix but hold the server clock, so a naive
    # subtraction yields ~10800s of pure artefact.
    closed_at = bar_close_time(last["datetime"], params)
    server_offset = mt5_connector.get_server_time_offset()
    sched_delay, sched_note = entry_observation.scheduling_delay_seconds(
        closed_at, sw.stamp("evaluation_start_time"), server_offset or 0)
    if sched_note:
        logger.warning(f"scheduling delay suspect: {sched_delay}s ({sched_note})")

    # Advisory regime context. Deterministic, logged, and deliberately not
    # consulted by any gate: a classifier that blocks before it has been
    # measured against a rule-only baseline destroys that baseline.
    try:
        regime_ctx = regime_mod.classify(candles)
    except Exception as exc:
        logger.warning(f"Regime classification failed: {exc}")
        regime_ctx = {"structure_regime": "UNCERTAIN",
                      "volatility_regime": "UNCERTAIN",
                      "error": str(exc)}

    candle_hash = hashlib.sha256(
        f"{last['datetime']}|{ohlc['open']}|{ohlc['high']}|"
        f"{ohlc['low']}|{ohlc['close']}|{ohlc['tick_volume']}".encode()
    ).hexdigest()[:16]

    if signal is None:
        logger.info(f"No signal | session={session} | blocked_at={reason}")
        log_event({
            "event": "NO_SIGNAL",
            "session": session,
            "blocked_at": reason,
            "blocked_class": classify_block(reason),
            "bar_time": str(last["datetime"]),
            "candle_hash": candle_hash,
            "config_hash": config_hash,
            "bar_count": len(candles),
            "close": ohlc["close"],
            "spread_points": spread_points,
            "ohlc": ohlc,
            "diag": diag,
            "regime": regime_ctx,
            # 0g-1 on every cycle. Scheduling delay is the core 0g quantity and
            # is directly observable here; sampling it only on signal cycles
            # would take a year to characterise at 1.6 signals per week. No
            # exec_observation: there is no entry to propose without a signal.
            "signal_bar_close_time": closed_at,
            "timing": sw.all_stamps(),
            "evaluation_ms": sw.elapsed_ms("evaluation_start_time",
                                           "evaluation_end_time"),
            "scheduling_delay_seconds": sched_delay,
            "scheduling_delay_note": sched_note,
            "server_offset_hours": server_offset,
            "quote": entry_observation.capture_quote(
                mt5_connector.mt5, instrument, sw),
        })
        return

    signal_key = f"{signal['signal_bar_time']}_{signal['strategy']}"
    if is_duplicate_signal(signal_key):
        logger.info(f"Duplicate signal skipped: {signal_key}")
        return

    sym_params = {}
    sym_params.update(params)
    # tick_value and tick_size are deliberately NOT propagated. MetaQuotes-Demo
    # reports trade_tick_value 0.1 where the terminal's own order_calc_profit
    # gives $1.00 per point, and trusting it oversized every position 10x.
    # Sizing reads contract_size from sym_info directly. See 495e078.
    if sym_info:
        sym_params["lot_min"] = sym_info.get("volume_min", 0.01)
        sym_params["lot_step"] = sym_info.get("volume_step", 0.01)

    approved, risk_reason, risk_details = risk_manager.approve(
        signal, account, spread_points, session, sym_params, sym_info
    )

    # 0g-1. Capture a contemporaneous quote and record what execution WOULD
    # look like under the frozen baseline policy. Purely additive: nothing
    # below reads exec_obs, so approval, entry, SL, TP, sizing and the alert
    # are bit-identical to the previous revision. Switching the live decision
    # onto these figures is 0g-5 and is not done here.
    quote = entry_observation.capture_quote(mt5_connector.mt5, instrument, sw)
    exec_obs = entry_observation.propose_execution(
        signal, quote, account.get("balance", 0), sym_params, sym_info,
        mt5=mt5_connector.mt5,
    )

    log_trade(signal, approved, risk_reason, risk_details, spread_points, session)
    record_signal_key(signal_key)

    log_event({
        "event": "SIGNAL_APPROVED" if approved else "SIGNAL_REJECTED",
        "session": session,
        "reason": risk_reason,
        "blocked_class": None if approved else classify_block(risk_reason, layer="risk"),
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],
        "tp_rr_multiple": signal["tp_rr_multiple"],
        "atr": signal["atr"],
        "volume": signal["volume"],
        "vol_avg": signal["vol_avg"],
        "spread_points": spread_points,
        "position_size": risk_details.get("position_size"),
        "bar_time": signal["signal_bar_time"],
        "candle_hash": candle_hash,
        "config_hash": config_hash,
        "bar_count": len(candles),
        "ohlc": ohlc,
        "diag": diag,
        "regime": regime_ctx,
        "balance": round(float(account.get("balance", 0)), 2),
        "equity": round(float(account.get("equity", 0)), 2),
        "contract_size": (sym_info or {}).get("contract_size"),
        "volume_min": (sym_info or {}).get("volume_min"),
        # 0g-1 observation block. Unrecoverable if not captured now: quote
        # latency and the decision-time spread are properties of this process,
        # not of the bar data.
        "signal_bar_close_time": closed_at,
        "timing": sw.all_stamps(),
        "evaluation_ms": sw.elapsed_ms("evaluation_start_time",
                                       "evaluation_end_time"),
        "scheduling_delay_seconds": sched_delay,
        "scheduling_delay_note": sched_note,
        "server_offset_hours": server_offset,
        "quote": quote,
        "exec_observation": exec_obs,
    })

    if approved:
        send_signal_alert(signal, risk_details, spread_points, session)
        sw.mark("alert_sent_time")
        log_event({
            "event": "ALERT_SENT",
            "bar_time": signal["signal_bar_time"],
            "candle_hash": candle_hash,
            "alert_sent_time": sw.stamp("alert_sent_time"),
            "decision_to_alert_ms": sw.elapsed_ms("evaluation_start_time",
                                                  "alert_sent_time"),
            "quote_to_alert_ms": sw.elapsed_ms("quote_received_time",
                                               "alert_sent_time"),
        })
        logger.info(f"SIGNAL SENT: {signal['direction']} @ {signal['entry']}")
    else:
        logger.info(f"Signal rejected: {risk_reason}")


if __name__ == "__main__":
    main()
