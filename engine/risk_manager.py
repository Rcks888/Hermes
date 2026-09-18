import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("hermes.risk")

TRADES_LOG = Path(__file__).parent.parent / "logs" / "trades.json"


def _load_trades():
    if TRADES_LOG.exists():
        with open(TRADES_LOG) as f:
            return json.load(f)
    return []


def _daily_loss_count(trades):
    now = datetime.now(timezone.utc)
    today = now.date()
    count = 0
    unparseable = 0
    for t in trades:
        if t.get("outcome") == "SL_HIT":
            ts = t.get("signal_bar_time", "")
            try:
                trade_date = datetime.fromisoformat(ts).date()
                if trade_date == today:
                    count += 1
            except (ValueError, TypeError):
                unparseable += 1
    if unparseable:
        logger.warning(f"{unparseable} SL_HIT trades had unparseable timestamps — daily loss count may be understated")
    return count


def check_no_trade_filters(signal, spread_points, params, session):
    reasons = []

    max_spread = params.get("max_spread_points")
    if max_spread:
        if spread_points is None:
            reasons.append("cannot_evaluate: spread unknown (symbol_info failed) — failing closed")
        elif spread_points > max_spread:
            reasons.append(f"Spread too wide: {spread_points} > {max_spread}")

    allowed_sessions = params.get("trading_sessions", ["LONDON", "NY"])
    if session not in allowed_sessions:
        reasons.append(f"Session {session} not in allowed {allowed_sessions}")

    atr = signal.get("atr", 0)
    min_atr = params.get("min_atr", 3.0)
    if atr < min_atr:
        reasons.append(f"ATR too low: {atr} < {min_atr}")

    sl_distance = signal.get("sl_distance", 0)
    sl_atr_low = params.get("sl_atr_low", 0.5)
    sl_atr_high = params.get("sl_atr_high", 2.5)
    if atr > 0:
        sl_atr_ratio = sl_distance / atr
        if sl_atr_ratio < sl_atr_low:
            reasons.append(f"SL too tight: {sl_distance} < {sl_atr_low}x ATR")
        if sl_atr_ratio > sl_atr_high:
            reasons.append(f"SL too wide: {sl_distance} > {sl_atr_high}x ATR")

    return reasons


def calculate_position_size(signal, account_balance, params):
    risk_pct = params.get("risk_pct", 0.01)
    risk_amount = account_balance * risk_pct
    sl_distance = signal["sl_distance"]
    if sl_distance <= 0:
        return 0.0

    tick_value = params.get("tick_value", 0.1)
    tick_size = params.get("tick_size", 0.01)

    ticks = sl_distance / tick_size
    if ticks <= 0:
        return 0.0

    lot_size = risk_amount / (ticks * tick_value)

    lot_min = params.get("lot_min", 0.01)
    lot_step = params.get("lot_step", 0.01)
    lot_size = max(lot_min, round(lot_size / lot_step) * lot_step)
    lot_size = round(lot_size, 2)

    return lot_size


def approve(signal, account, spread_points, session, params):
    trades = _load_trades()

    daily_losses = _daily_loss_count(trades)
    daily_limit = params.get("daily_loss_limit", 3)
    if daily_losses >= daily_limit:
        logger.warning(f"Daily loss limit reached: {daily_losses}/{daily_limit}")
        return False, "Daily loss limit reached", {}

    from engine import data_feed
    positions = data_feed.get_positions(params.get("instrument", "XAUUSD"))
    max_open = params.get("max_open_trades", 1)
    if positions is None:
        logger.error("Cannot determine open positions — failing closed to avoid double entry")
        return False, "cannot_evaluate: open positions unknown (positions_get failed) — failing closed", {}
    if len(positions) >= max_open:
        logger.warning(f"Max open trades reached: {len(positions)}/{max_open}")
        return False, "Max open trades reached", {}

    rr = signal.get("rr_ratio", 0)
    min_rr = params.get("min_rr_ratio", 2.0)
    if rr < min_rr:
        logger.warning(f"RR too low: {rr} < {min_rr}")
        return False, f"RR too low: {rr}", {}

    filter_reasons = check_no_trade_filters(signal, spread_points, params, session)
    if filter_reasons:
        logger.warning(f"No-trade filter: {'; '.join(filter_reasons)}")
        return False, "; ".join(filter_reasons), {}

    balance = account.get("balance", 0)
    lot_size = calculate_position_size(signal, balance, params)
    risk_amount = balance * params.get("risk_pct", 0.01)

    details = {
        "position_size": lot_size,
        "risk_amount": round(risk_amount, 2),
        "daily_losses": daily_losses,
    }

    logger.info(f"APPROVED: {signal['direction']} {lot_size} lots, risk=${risk_amount:.2f}")
    return True, "Approved", details
