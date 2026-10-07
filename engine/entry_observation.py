"""0g-1 -- observational logging for entry timing and execution validity.

This module OBSERVES. It does not gate, resize, retarget or reschedule anything
live. Every function here is pure with respect to Hermes' decision: it reads a
signal and a quote and returns what execution *would* look like, so that the
replay contract can be built against recorded fact instead of reconstruction.

The fields it produces cannot be backfilled. Scheduling delay, quote latency
and the bid/ask spread at the moment of decision are properties of the running
process, not of the bar data, so every cycle logged without them is
permanently unrecoverable. That is why this lands before the replay engine
rather than after it.

BASELINE ENTRY POLICY observed here (frozen, research baseline only -- this is
a research policy, NOT authorisation for live execution):

    Setup confirmation uses completed M15 bars.
    Original structure-derived SL remains fixed.
    Original signal-time TP remains fixed.
    Proposed entry uses the contemporaneous executable quote.
    Volume is recalculated from that entry to the original SL,
    subject to existing risk and broker constraints.
    No adverse-displacement threshold is introduced.
    No minimum-available-RR threshold is introduced.
    TP is not moved to restore nominal 2R.
    Invalid or non-executable opportunities are recorded separately.
    No pre-entry stop or target touch counts as a trade outcome.

Fixed TP is the control. Recomputing it to restore 2R would change the target
at the same time as the entry, making it impossible to attribute any change to
corrected execution rather than altered exits. That recomputation is a
strategy-policy change and remains a separately preregistered candidate.

Resizing is NOT "take everything". Execution-validity failures are catalogued
in their own namespace, deliberately disjoint from strategy rejection reasons,
because an unexecutable opportunity is not evidence that a degraded-RR setup
is unprofitable. Conflating the two would corrupt both populations.

"1% risk" throughout Hermes means PLANNED stop-loss risk: the loss if the stop
fills exactly at its price. It is not a guaranteed maximum. Gaps, slippage and
adverse fills can exceed it, and the spread already makes realised risk exceed
the plan slightly even on a clean fill.
"""
import time
from datetime import datetime, timezone

# Execution validity. Deliberately NOT drawn from the strategy or risk gate
# vocabularies; these describe whether an opportunity could be acted on at all,
# not whether it should have been.
EXEC_OK = "executable"
EXEC_STATUSES = (
    EXEC_OK,
    "quote_unavailable",
    "quote_unusable",
    "original_sl_already_crossed",
    "original_tp_already_crossed",
    "entry_outside_sl_tp_band",
    "volume_below_broker_minimum",
    "volume_above_broker_maximum",
    "contract_size_unavailable",
    "sl_distance_not_positive",
    "margin_not_checked",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Stopwatch:
    """Monotonic for elapsed durations, UTC wall clock for reconciliation.

    Wall-clock deltas are not safe for measuring elapsed time: NTP correction
    or a clock step produces negative or inflated latencies that look like
    bridge faults. Durations therefore come from time.monotonic(), while the
    timestamps that must join against Ares, MT5 and the broker come from UTC.
    """

    def __init__(self):
        self._marks = {}
        self._mono = {}

    def mark(self, name):
        self._marks[name] = utc_now()
        self._mono[name] = time.monotonic()
        return self._marks[name]

    def stamp(self, name):
        return self._marks.get(name)

    def elapsed_ms(self, start, end):
        a, b = self._mono.get(start), self._mono.get(end)
        if a is None or b is None:
            return None
        return round((b - a) * 1000.0, 3)

    def all_stamps(self):
        return dict(self._marks)


def capture_quote(mt5, symbol="XAUUSD", stopwatch=None):
    """Read one executable quote, recording the broker's clock and ours apart.

    tick.time_msc is the broker's own timestamp for the quote. The instant we
    received it is a different quantity, and the gap between them is bridge and
    terminal latency. Collapsing the two would hide exactly the delay 0g exists
    to measure, so they are recorded as separate fields.
    """
    sw = stopwatch
    if sw is not None:
        sw.mark("quote_request_start_time")
    out = {"quote_bid": None, "quote_ask": None, "quote_time_msc": None,
           "quote_broker_time": None, "quote_received_time": None,
           "quote_spread_price": None, "quote_latency_ms": None,
           "quote_error": None}
    try:
        tick = mt5.symbol_info_tick(symbol)
    except Exception as e:  # noqa: BLE001 - bridge faults are data, not crashes
        out["quote_error"] = f"{type(e).__name__}: {e}"
        tick = None
    if sw is not None:
        sw.mark("quote_received_time")
        out["quote_received_time"] = sw.stamp("quote_received_time")
        out["quote_latency_ms"] = sw.elapsed_ms("quote_request_start_time",
                                                "quote_received_time")
    else:
        out["quote_received_time"] = utc_now()
    if tick is None:
        if out["quote_error"] is None:
            out["quote_error"] = "symbol_info_tick returned None"
        return out
    try:
        bid, ask = float(tick.bid), float(tick.ask)
        tmsc = int(getattr(tick, "time_msc", 0) or 0)
    except Exception as e:  # noqa: BLE001
        out["quote_error"] = f"tick_unreadable: {type(e).__name__}: {e}"
        return out
    out["quote_bid"], out["quote_ask"] = bid, ask
    out["quote_time_msc"] = tmsc
    if tmsc:
        # Server clock. Labelled as such rather than as UTC: the demo server
        # runs UTC+3 and data_feed already mislabels this elsewhere.
        out["quote_broker_time"] = datetime.fromtimestamp(
            tmsc / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "+00:00(server_clock)")
    if bid > 0 and ask > 0:
        out["quote_spread_price"] = round(ask - bid, 5)
    return out


def propose_execution(signal, quote, account_balance, params, symbol_info,
                      mt5=None):
    """What execution would look like under the frozen baseline policy.

    Observational. Never mutates `signal`, never places an order, never
    influences the live decision. Returns a flat dict for the event record.

    SL and TP are carried through unchanged. Only volume is recalculated, from
    the executable entry to the ORIGINAL stop, so a delayed entry costs reward
    rather than silently widening risk.
    """
    direction = signal.get("direction")
    original_sl = signal.get("sl")
    original_tp = signal.get("tp")
    signal_close = signal.get("entry")
    long = direction == "LONG"

    out = {
        "baseline_policy": "sl_fixed_tp_fixed_volume_from_executable_entry",
        "signal_close": signal_close,
        "original_sl": original_sl,
        "original_tp": original_tp,
        "entry_side": "ask" if long else "bid",
        "proposed_executable_entry": None,
        "executable_sl_distance": None,
        "executable_tp_distance": None,
        "rr_at_executable_entry": None,
        "rr_at_signal_close": None,
        "entry_displacement": None,
        "adverse_displacement": None,
        "proposed_volume": None,
        "planned_stop_loss_cash": None,
        "planned_stop_loss_pct_of_balance": None,
        "execution_validity_status": None,
        "execution_validity_detail": None,
    }

    def fail(status, detail=None):
        out["execution_validity_status"] = status
        out["execution_validity_detail"] = detail
        return out

    if quote is None or quote.get("quote_error"):
        return fail("quote_unavailable",
                    (quote or {}).get("quote_error", "no quote supplied"))
    bid, ask = quote.get("quote_bid"), quote.get("quote_ask")
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return fail("quote_unusable",
                    f"bid={bid} ask={ask}; non-positive or inverted")

    entry = ask if long else bid
    out["proposed_executable_entry"] = round(entry, 2)

    if signal_close is not None:
        disp = entry - float(signal_close)
        out["entry_displacement"] = round(disp, 4)
        # Adverse is direction-aware: a higher fill hurts a long, helps a short.
        out["adverse_displacement"] = round(disp if long else -disp, 4)

    if original_sl is None or original_tp is None:
        return fail("entry_outside_sl_tp_band", "signal missing sl or tp")

    sl, tp = float(original_sl), float(original_tp)

    # The stop is evaluated on the bid for a long. If the bid has already
    # reached it, the opportunity is gone rather than merely degraded.
    if (bid <= sl) if long else (bid >= sl):
        return fail("original_sl_already_crossed",
                    f"bid={bid} vs original_sl={sl}")
    if (bid >= tp) if long else (bid <= tp):
        return fail("original_tp_already_crossed",
                    f"bid={bid} vs original_tp={tp}")
    if not ((sl < entry < tp) if long else (tp < entry < sl)):
        return fail("entry_outside_sl_tp_band",
                    f"entry={entry} not between sl={sl} and tp={tp}")

    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return fail("sl_distance_not_positive", f"entry={entry} sl={sl}")
    tp_distance = abs(tp - entry)
    out["executable_sl_distance"] = round(sl_distance, 2)
    out["executable_tp_distance"] = round(tp_distance, 2)
    out["rr_at_executable_entry"] = round(tp_distance / sl_distance, 3)
    if signal_close:
        d0 = abs(float(signal_close) - sl)
        if d0 > 0:
            out["rr_at_signal_close"] = round(abs(tp - float(signal_close)) / d0, 3)

    # Sizing. contract_size is authoritative; trade_tick_value is not, and
    # trusting it previously oversized every position tenfold.
    contract_size = None
    if symbol_info and symbol_info.get("contract_size"):
        contract_size = float(symbol_info["contract_size"])
    elif params.get("contract_size"):
        contract_size = float(params["contract_size"])
    if not contract_size or contract_size <= 0:
        return fail("contract_size_unavailable",
                    "failing closed rather than assuming a value")

    risk_pct = params.get("risk_pct", 0.01)
    risk_budget = float(account_balance) * risk_pct
    loss_per_lot = sl_distance * contract_size
    raw_lots = risk_budget / loss_per_lot

    lot_min = float((symbol_info or {}).get("volume_min")
                    or params.get("lot_min", 0.01))
    lot_step = float((symbol_info or {}).get("volume_step")
                     or params.get("lot_step", 0.01))
    lot_max = float((symbol_info or {}).get("volume_max")
                    or params.get("lot_max", 100.0))

    # Round DOWN to a broker step. Rounding up to reach the minimum would
    # breach the risk budget to force a trade, which is the error that makes a
    # small account untradeable rather than merely unprofitable.
    lots = int(raw_lots / lot_step) * lot_step
    lots = round(lots, 2)

    if lots < lot_min:
        return fail("volume_below_broker_minimum",
                    f"raw={round(raw_lots, 4)} rounds to {lots} below "
                    f"minimum {lot_min}; the broker minimum would risk "
                    f"{round(lot_min * loss_per_lot, 2)} against a budget of "
                    f"{round(risk_budget, 2)}. Not rounded up.")
    if lots > lot_max:
        return fail("volume_above_broker_maximum",
                    f"{lots} exceeds maximum {lot_max}")

    out["proposed_volume"] = lots
    planned = lots * loss_per_lot
    out["planned_stop_loss_cash"] = round(planned, 2)
    if account_balance:
        out["planned_stop_loss_pct_of_balance"] = round(
            100.0 * planned / float(account_balance), 4)

    # Margin needs the terminal. Where it cannot be asked, say so rather than
    # implying the check passed.
    status, detail = EXEC_OK, None
    if mt5 is not None:
        try:
            otype = mt5.ORDER_TYPE_BUY if long else mt5.ORDER_TYPE_SELL
            margin = mt5.order_calc_margin(otype, signal.get("symbol", "XAUUSD"),
                                           lots, entry)
            out["required_margin"] = (round(float(margin), 2)
                                      if margin is not None else None)
        except Exception as e:  # noqa: BLE001
            out["required_margin"] = None
            status, detail = "margin_not_checked", f"{type(e).__name__}: {e}"
    else:
        status, detail = "margin_not_checked", "mt5 handle not supplied"

    return fail(status, detail)
