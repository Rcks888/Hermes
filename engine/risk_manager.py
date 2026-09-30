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


def calculate_position_size(signal, account_balance, params, symbol_info=None):
    """Return (lot_size, reason). reason is None on success, a string on reject.

    One return shape on every path so callers cannot mistake a rejection for a
    size of zero.
    """
    risk_pct = params.get("risk_pct", 0.01)
    risk_amount = account_balance * risk_pct
    sl_distance = signal["sl_distance"]
    if sl_distance <= 0:
        return 0.0, "sl_distance not positive"

    # Value of a 1.00 price move for 1.00 lot equals contract_size, verified
    # against the terminal: order_calc_profit returned $100.00 for a $1.00 move
    # on 1.00 lot with contract_size 100.
    #
    # Do NOT use the broker's trade_tick_value. MetaQuotes-Demo reports 0.1 per
    # 0.01 tick, which predicts $10.00 for the same move -- ten times low, and
    # contradicted by the terminal's own profit calculation. The previous
    # hardcoded default of 0.1 therefore oversized every position by 10x.
    contract_size = None
    if symbol_info and symbol_info.get("contract_size"):
        contract_size = float(symbol_info["contract_size"])
    elif params.get("contract_size"):
        contract_size = float(params["contract_size"])
    if not contract_size or contract_size <= 0:
        return 0.0, ("cannot_evaluate: contract_size unavailable — failing "
                     "closed rather than assuming a value")

    loss_per_lot = sl_distance * contract_size
    if loss_per_lot <= 0:
        return 0.0, "loss_per_lot not positive"
    raw_lots = risk_amount / loss_per_lot

    lot_min = float((symbol_info or {}).get("volume_min") or params.get("lot_min", 0.01))
    lot_step = float((symbol_info or {}).get("volume_step") or params.get("lot_step", 0.01))
    lot_max = float((symbol_info or {}).get("volume_max") or params.get("lot_max", 100.0))

    # Round DOWN to a step so the realised risk never exceeds the budget.
    lot_size = int(raw_lots / lot_step) * lot_step
    lot_size = round(lot_size, 2)

    # Below the minimum tradeable size the account cannot express this risk.
    # Rounding up to lot_min would silently exceed the intended risk -- on a
    # $100 account at 1%, a 6.53 stop needs 0.0015 lots, so lot_min 0.01 would
    # risk 6.5% instead of 1%. Reject instead.
    if lot_size < lot_min:
        implied = lot_min * loss_per_lot
        return 0.0, (
            f"position_size_below_minimum (need {raw_lots:.4f} lots for "
            f"{risk_pct * 100:.1f}% risk, broker minimum {lot_min}; taking the "
            f"minimum would risk {implied:.2f} = "
            f"{100 * implied / account_balance:.2f}% of balance)")

    if lot_size > lot_max:
        return 0.0, f"position_size_above_maximum ({lot_size} > {lot_max})"

    return lot_size, None


def approve(signal, account, spread_points, session, params, symbol_info=None):
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

    filter_reasons = check_no_trade_filters(signal, spread_points, params, session)
    if filter_reasons:
        logger.warning(f"No-trade filter: {'; '.join(filter_reasons)}")
        return False, "; ".join(filter_reasons), {}

    balance = account.get("balance", 0)
    lot_size, size_reason = calculate_position_size(
        signal, balance, params, symbol_info)
    if size_reason:
        logger.warning(f"Position sizing rejected: {size_reason}")
        return False, size_reason, {}
    risk_amount = balance * params.get("risk_pct", 0.01)

    details = {
        "position_size": lot_size,
        "risk_amount": round(risk_amount, 2),
        "daily_losses": daily_losses,
    }

    logger.info(f"APPROVED: {signal['direction']} {lot_size} lots, risk=${risk_amount:.2f}")
    return True, "Approved", details
