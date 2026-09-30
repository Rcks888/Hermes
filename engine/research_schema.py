#!/usr/bin/env python3
"""Research dataset schema (Priority 0d).

Two linked tables, deliberately not one.

    evaluations   one row per completed-bar evaluation, whatever the outcome
    setups        one row per unique opportunity, qualified or counterfactual

Collapsing these would label a single developing pullback as many separate
setups: the same structural opportunity is re-evaluated on every bar while it
persists, so a per-bar table cannot answer "how many setups occurred" and a
per-setup table cannot answer "which gate blocks most often". Both questions
matter, so both tables exist and are joined on setup_id.

The live path and the replay engine must both write this shape. That is the
entire point -- conformance is only checkable if the two produce comparable
rows, and the schema is the contract that makes the comparison meaningful
rather than a coincidence of column names.
"""

SCHEMA_VERSION = "1.0"

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

# How a bar evaluation terminated. cannot_evaluate is kept strictly separate
# from rule_violation: absent evidence is not a passed check, and conflating
# them would let missing data masquerade as market conditions.
# "unknown" exists so a gate renamed in strategy.py without updating this
# module surfaces loudly instead of being miscounted as something else.
BLOCK_CLASSES = ("ok", "cannot_evaluate", "rule_violation", "risk_block",
                 "unknown")

# The ten gates that represent a genuinely failed condition.
RULE_GATES = (
    "trend_not_confirmed",
    "no_pullback",
    "no_candle_confirmation",
    "vwap_misaligned",
    "volume_too_low",
    "atr_invalid",
    "atr_too_low",
    "sl_too_tight",
    "sl_too_wide",
    "sl_distance_invalid",
)

# The four conditions under which evaluation could not complete.
CANNOT_EVALUATE_REASONS = (
    "insufficient_bars_for_pullback",
    "vwap_unavailable",
    "volume_average_unavailable",
    "atr_unavailable",
)

# Provenance of an outcome. Mixing these in a single deployable metric is
# prohibited: a counterfactual outcome assumes a hypothetical entry that was
# never eligible, so including it in a reported Profit Factor would overstate
# performance with hindsight the system never had.
OUTCOME_TYPES = (
    "ACTUAL_SIMULATED",           # replay, passed every gate and risk check
    "COUNTERFACTUAL_DIAGNOSTIC",  # rejected setup, tracked for research only
    "LIVE_ALERT_ONLY",            # live signal, alerted, not executed
    "LIVE_EXECUTED",              # live signal, order actually placed
)

# How a tracked setup finished.
EXIT_REASONS = (
    "TP_HIT", "SL_HIT", "TIME_EXIT", "SESSION_EXIT",
    "HORIZON_EXIT", "PATH_UNKNOWN", "OPEN",
)

# Where the row came from. Required on every row so a mixed dataset can always
# be separated again.
SOURCES = ("LIVE", "REPLAY")


# --------------------------------------------------------------------------
# evaluations -- one row per completed-bar evaluation
# --------------------------------------------------------------------------
# (name, type, required, description)

EVALUATIONS_FIELDS = (
    # Identity. Sufficient to locate the exact bar and the exact system state.
    ("schema_version", "str", True, "Schema version this row conforms to"),
    ("source", "str", True, f"One of {SOURCES}"),
    ("symbol", "str", True, "Instrument, e.g. XAUUSD"),
    ("timeframe", "str", True, "Bar interval, e.g. M15"),
    ("bar_time", "str", True, "Bar open time, server clock, ISO 8601 with offset"),
    ("bar_time_utc", "str", True, "Same instant in true UTC, for cross-source joins"),
    ("candle_hash", "str", True, "Hash of bar OHLCV -- proves the same bar was read"),
    ("code_version", "str", True, "Short git SHA of the evaluating code"),
    ("config_hash", "str", True, "Fingerprint of effective strategy parameters"),
    ("bar_count", "int", True, "Bars supplied to the evaluation"),

    # Raw bar. Recorded so an evaluation can be reproduced without the source.
    ("open", "float", True, ""),
    ("high", "float", True, ""),
    ("low", "float", True, ""),
    ("close", "float", True, ""),
    ("tick_volume", "float", True, ""),
    ("bar_spread", "int", False, "Broker bar-recorded spread, points. NOT a cost basis"),
    ("live_spread", "int", False, "Spread measured at evaluation. LIVE rows only"),
    ("modelled_spread", "int", False, "Spread applied by the cost model. REPLAY rows only"),
    ("session", "str", True, "ASIA / LONDON / NY / OFF"),

    # Outcome of the evaluation.
    ("block_class", "str", True, f"One of {BLOCK_CLASSES}"),
    ("blocked_at", "str", False, "Gate name or cannot_evaluate reason. Null when ok"),
    ("blocked_detail", "str", False, "Full human-readable reason string"),

    # Gate inputs, in evaluation order. Null means the gate was never reached;
    # that is distinct from a value that was reached but unavailable, which is
    # why cannot_evaluate exists as a separate class.
    ("trend", "str", False, "UP / DOWN / None"),
    ("trend_confirmed", "bool", False, ""),
    ("bos_up", "int", False, "BOS up count within bos_window_bars"),
    ("bos_down", "int", False, "BOS down count within bos_window_bars"),
    ("bos_total", "int", False, "BOS events across trend_scope_bars"),
    ("last_bos_direction", "str", False, ""),
    ("bars_since_last_bos", "int", False, "Staleness of the trend label"),
    ("trend_scope_bars", "int", False, "Window trend was derived over"),
    ("bos_window_bars", "int", False, "Window BOS was counted over"),
    ("swing_high_count", "int", False, ""),
    ("swing_low_count", "int", False, ""),
    ("sr_zone_count", "int", False, ""),
    ("pullback", "bool", False, ""),
    ("avg_body", "float", False, ""),
    ("has_momentum", "bool", False, ""),
    ("engulfing", "str", False, "BULLISH / BEARISH / None"),
    ("reaction", "str", False, "BULLISH / BEARISH / None"),
    ("near_sr_zone", "bool", False, ""),
    ("candle_confirmed", "bool", False, ""),
    ("price", "float", False, ""),
    ("vwap", "float", False, ""),
    ("vwap_aligned", "bool", False, ""),
    ("volume", "float", False, ""),
    ("vol_avg", "float", False, ""),
    ("vol_ratio", "float", False, ""),
    ("vol_threshold", "float", False, ""),
    ("atr", "float", False, ""),
    ("at_zone", "bool", False, ""),
    ("min_atr", "float", False, ""),

    # Proposed trade, when evaluation reached that point.
    ("direction", "str", False, "LONG / SHORT"),
    ("entry", "float", False, ""),
    ("sl_raw", "float", False, "SL before any risk adjustment"),
    ("sl_buffer", "float", False, ""),
    ("sl_distance", "float", False, ""),
    ("sl_atr_ratio", "float", False, ""),
    ("tp", "float", False, ""),
    ("tp_distance", "float", False, ""),
    ("tp_rr_multiple", "float", False, ""),

    # Risk layer. Applied after the strategy, so these are null when the
    # strategy itself blocked.
    ("risk_result", "str", False, "approved / rejected / not_reached"),
    ("risk_reason", "str", False, ""),
    ("position_size", "float", False, "Lots. 0.0 with a reason is a rejection"),
    ("balance", "float", False, ""),
    ("equity", "float", False, ""),
    ("contract_size", "float", False, ""),
    ("volume_min", "float", False, ""),

    # Link to the setups table. Null when no setup was identified.
    ("setup_id", "str", False, ""),
    ("is_setup_first_bar", "bool", False, "True on the bar that opened the setup"),
)


# --------------------------------------------------------------------------
# setups -- one row per unique opportunity
# --------------------------------------------------------------------------

SETUPS_FIELDS = (
    ("schema_version", "str", True, ""),
    ("source", "str", True, f"One of {SOURCES}"),
    ("setup_id", "str", True, "Stable identity across the bars it spans"),
    ("symbol", "str", True, ""),
    ("timeframe", "str", True, ""),
    ("code_version", "str", True, ""),
    ("config_hash", "str", True, ""),

    # Which population this belongs to. Never collapse these.
    #   A  strategy signal      all ten gates passed
    #   B  risk-eligible        session, spread, account, safety passed
    #   C  simulated execution  size, margin and fill succeeded
    ("population", "str", True, "A / B / C"),
    ("outcome_type", "str", True, f"One of {OUTCOME_TYPES}"),

    # Definition boundaries. Required on counterfactual rows, because without
    # a frozen hypothetical entry and horizon a rejected setup carries
    # unlimited hindsight and its MFE is meaningless.
    ("first_bar_time", "str", True, "Bar on which the setup was first identified"),
    ("entry_time", "str", False, "Executable entry instant. Null if never entered"),
    ("entry_price", "float", False, ""),
    ("entry_is_hypothetical", "bool", True, "True for counterfactual rows"),
    ("horizon_bars", "int", True, "Max bars tracked before forced measurement end"),
    ("measurement_end_time", "str", False, ""),
    ("measurement_end_reason", "str", False, f"One of {EXIT_REASONS}"),

    ("direction", "str", True, ""),
    ("sl", "float", True, ""),
    ("tp", "float", True, ""),
    ("sl_distance", "float", True, ""),
    ("tp_rr_multiple", "float", True, ""),
    ("position_size", "float", False, "Null on counterfactual rows"),

    # Excursions, measured from entry_time to measurement_end_time only.
    ("mfe_price", "float", False, "Most favourable price reached"),
    ("mae_price", "float", False, "Least favourable price reached"),
    ("mfe_r", "float", False, "MFE expressed in R of initial risk"),
    ("mae_r", "float", False, "MAE expressed in R"),

    # Result. Null on any row where outcome_type is counterfactual and no
    # simulated fill occurred.
    ("exit_time", "str", False, ""),
    ("exit_price", "float", False, ""),
    ("exit_reason", "str", False, f"One of {EXIT_REASONS}"),
    ("gross_r", "float", False, "Result in R before costs"),
    ("net_r", "float", False, "Result in R after spread, slippage, commission"),
    ("gross_cash", "float", False, ""),
    ("net_cash", "float", False, ""),
    ("cost_spread", "float", False, ""),
    ("cost_slippage", "float", False, ""),
    ("cost_commission", "float", False, ""),
    ("holding_bars", "int", False, ""),

    # Context for conditioning. Independent dimensions, deliberately not a
    # single mutually exclusive label -- TREND with HIGH volatility is a real
    # and important state for gold that one label cannot express.
    ("structure_regime", "str", False, "TREND / RANGE / TRANSITION / UNCERTAIN"),
    ("volatility_regime", "str", False, "HIGH / NORMAL / LOW / UNCERTAIN"),
    ("session", "str", True, ""),
    ("event_risk_window", "bool", False, "Inside a scheduled-event blackout"),

    # Ambiguity flag. True when SL and TP both fell inside one bar and the
    # tick path was unavailable, so ordering had to be assumed. Such rows must
    # be reportable separately -- silently assuming TP came first is how
    # backtests flatter themselves.
    ("intrabar_ambiguous", "bool", True, ""),
)


REQUIRED_EVALUATION_FIELDS = tuple(f[0] for f in EVALUATIONS_FIELDS if f[2])
REQUIRED_SETUP_FIELDS = tuple(f[0] for f in SETUPS_FIELDS if f[2])
EVALUATION_FIELD_NAMES = tuple(f[0] for f in EVALUATIONS_FIELDS)
SETUP_FIELD_NAMES = tuple(f[0] for f in SETUPS_FIELDS)


def classify_blocked_at(reason, layer=None):
    """Return (block_class, gate_name) for a reason string.

    The layer argument is "strategy" or "risk" and says which component
    produced the reason. It matters: risk reasons are free-form prose, so
    without knowing the layer an unrecognised strategy gate is
    indistinguishable from a risk block and would be silently miscounted as
    one. The event type already carries this fact, so pass it rather than
    inferring from the text.

    With layer omitted, classification is best-effort and an unrecognised
    reason returns "unknown" rather than being assumed to be a risk block.
    """
    if reason is None:
        return None, None
    if reason == "OK":
        return "ok", None
    if reason.startswith("cannot_evaluate"):
        return "cannot_evaluate", reason.split(":", 1)[-1].strip() or None
    for gate in RULE_GATES:
        if reason.startswith(gate):
            return "rule_violation", gate
    if layer == "risk":
        return "risk_block", reason.split(" ", 1)[0]
    # A strategy reason matching no known gate is schema drift, not a risk
    # block. Say so rather than filing it under the nearest plausible class.
    return "unknown", reason.split(" ", 1)[0]


def evaluation_row_from_event(event, symbol="XAUUSD", timeframe="M15",
                              source="LIVE", bar_time_utc=None):
    """Project an events.jsonl record onto the evaluations schema.

    Unreached fields are omitted rather than set to null, so a consumer can
    still distinguish "evaluation stopped before this" from "this was null".
    Callers wanting a dense row should fill the gap explicitly.
    """
    diag = event.get("diag") or {}
    ohlc = event.get("ohlc") or {}
    # The event type states which layer blocked. Trust it over the prose.
    layer = "risk" if event.get("event") in (
        "SIGNAL_APPROVED", "SIGNAL_REJECTED") else "strategy"
    block_class, gate = classify_blocked_at(
        event.get("blocked_at") or event.get("reason"), layer=layer)

    row = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "bar_time": event.get("bar_time"),
        "bar_time_utc": bar_time_utc,
        "candle_hash": event.get("candle_hash"),
        "code_version": event.get("code_version"),
        "config_hash": event.get("config_hash"),
        "bar_count": event.get("bar_count"),
        "session": event.get("session"),
        # Computed here rather than read from the event, so one implementation
        # governs the dataset and the two cannot drift apart.
        "block_class": block_class,
        "blocked_at": gate,
        "blocked_detail": event.get("blocked_at") or event.get("reason"),
        "bar_spread": ohlc.get("bar_spread"),
        "live_spread": event.get("spread_points") if source == "LIVE" else None,
        "modelled_spread": event.get("spread_points") if source == "REPLAY" else None,
    }
    for src, dst in (("open", "open"), ("high", "high"), ("low", "low"),
                     ("close", "close"), ("tick_volume", "tick_volume")):
        if src in ohlc:
            row[dst] = ohlc[src]
    for key in diag:
        if key in EVALUATION_FIELD_NAMES:
            row[key] = diag[key]

    ev = event.get("event")
    if ev == "SIGNAL_APPROVED":
        row["risk_result"] = "approved"
    elif ev == "SIGNAL_REJECTED":
        row["risk_result"] = "rejected"
    else:
        row["risk_result"] = "not_reached"
    for key in ("risk_reason", "position_size", "balance", "equity",
                "contract_size", "volume_min"):
        if key in event:
            row[key] = event[key]
    if "reason" in event and ev in ("SIGNAL_APPROVED", "SIGNAL_REJECTED"):
        row["risk_reason"] = event["reason"]

    return row


def validate_evaluation(row):
    """Return a list of problems. Empty means conformant."""
    problems = []
    for field in REQUIRED_EVALUATION_FIELDS:
        if row.get(field) is None:
            problems.append(f"missing required field: {field}")
    unknown = set(row) - set(EVALUATION_FIELD_NAMES)
    if unknown:
        problems.append(f"unknown fields: {sorted(unknown)}")
    if row.get("block_class") not in BLOCK_CLASSES:
        problems.append(f"block_class not recognised: {row.get('block_class')!r}")
    if row.get("source") not in SOURCES:
        problems.append(f"source not recognised: {row.get('source')!r}")
    if row.get("block_class") == "ok" and row.get("blocked_at") is not None:
        problems.append("block_class ok but blocked_at is set")
    if row.get("block_class") == "rule_violation" and row.get("blocked_at") not in RULE_GATES:
        problems.append(f"rule_violation with unknown gate: {row.get('blocked_at')!r}")
    if row.get("block_class") == "unknown":
        problems.append(
            f"unrecognised block reason {row.get('blocked_detail')!r} -- "
            "RULE_GATES is probably out of step with strategy.py")
    return problems


def validate_setup(row):
    """Return a list of problems. Empty means conformant."""
    problems = []
    for field in REQUIRED_SETUP_FIELDS:
        if row.get(field) is None:
            problems.append(f"missing required field: {field}")
    unknown = set(row) - set(SETUP_FIELD_NAMES)
    if unknown:
        problems.append(f"unknown fields: {sorted(unknown)}")
    if row.get("outcome_type") not in OUTCOME_TYPES:
        problems.append(f"outcome_type not recognised: {row.get('outcome_type')!r}")
    if row.get("population") not in ("A", "B", "C"):
        problems.append(f"population not recognised: {row.get('population')!r}")

    # A counterfactual row must declare its entry hypothetical, or it will be
    # mistaken for a real result downstream.
    if row.get("outcome_type") == "COUNTERFACTUAL_DIAGNOSTIC":
        if not row.get("entry_is_hypothetical"):
            problems.append(
                "counterfactual row must set entry_is_hypothetical")
        if row.get("net_cash") is not None:
            problems.append(
                "counterfactual row must not carry net_cash -- no capital was risked")
    if row.get("outcome_type") == "ACTUAL_SIMULATED":
        if row.get("entry_is_hypothetical"):
            problems.append("actual row must not set entry_is_hypothetical")
        if row.get("population") != "C":
            problems.append("ACTUAL_SIMULATED requires population C")
    if row.get("exit_reason") is not None and row["exit_reason"] not in EXIT_REASONS:
        problems.append(f"exit_reason not recognised: {row['exit_reason']!r}")

    # An excursion measured without a defined window is meaningless.
    if (row.get("mfe_r") is not None or row.get("mae_r") is not None):
        if row.get("entry_time") is None or row.get("measurement_end_time") is None:
            problems.append(
                "MFE/MAE present without both entry_time and measurement_end_time")
    return problems


def describe():
    """Human-readable field listing, for the roadmap and reviews."""
    lines = [f"Research dataset schema v{SCHEMA_VERSION}", ""]
    for title, fields in (("evaluations", EVALUATIONS_FIELDS),
                          ("setups", SETUPS_FIELDS)):
        req = sum(1 for f in fields if f[2])
        lines.append(f"{title}: {len(fields)} fields, {req} required")
        for name, typ, required, desc in fields:
            mark = "*" if required else " "
            lines.append(f"  {mark} {name:<24} {typ:<6} {desc}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())