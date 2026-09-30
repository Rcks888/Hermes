#!/usr/bin/env python3
"""Return-path and dead-branch catalogue (Priority 0f).

Every decision exit from the strategy and risk layers, catalogued so that
"exhaustive coverage" is a checked claim rather than an assertion.

WHY THIS IS EXECUTABLE RATHER THAN A DOCUMENT
A markdown table would drift from the source the first time a gate was added,
and the drift would be invisible. This module parses the real functions with
ast and the tests assert the catalogue count equals the actual return count.
Add a return without cataloguing it and the suite fails.

Line numbers are recorded as they stood at LINES_AUDITED_AT and are NOT
asserted, because asserting them would break the suite on every unrelated edit
and train everyone to update the number without reading. Identity is carried by
reason key, which is stable. refresh_lines() reports current positions.

RECONCILIATION
strategy.evaluate has 16 return statements but 15 distinct reason codes.
vwap_misaligned is emitted from two direction-specific sites -- LONG below VWAP
and SHORT above VWAP -- under one code. That accounts for the discrepancy
exactly, and the tests verify the arithmetic rather than trusting this
paragraph.
"""

import ast
from pathlib import Path

LINES_AUDITED_AT = "1d5c42e"

PROJECT_ROOT = Path(__file__).parent.parent

# Classes mirror research_schema.BLOCK_CLASSES. The cross-check in the tests is
# deliberate: two modules holding the same gate list is exactly how a rename
# ends up silently miscounted.
RULE = "rule_violation"
CANNOT = "cannot_evaluate"
OK = "ok"
RISK = "risk_block"


# --------------------------------------------------------------------------
# strategy.evaluate -- 16 return sites, 15 reason codes
# --------------------------------------------------------------------------

STRATEGY_PATHS = (
    dict(line=64, code="trend_not_confirmed", cls=RULE, reachable=True,
         inputs=("structure.trend", "structure.trend_confirmed"),
         diag=("trend", "trend_confirmed", "bos_up", "bos_down"),
         test="test_every_rule_gate_classifies",
         note="Top blocker at 64% of tradeable cycles. trend is derived over "
              "the whole frame while bos counts span only 50 bars, so a stale "
              "trend can be reported against a window that cannot see the "
              "break that set it. Measured via bars_since_last_bos, unresolved "
              "by design until the replay can quantify it."),
    dict(line=69, code="insufficient_bars_for_pullback", cls=CANNOT, reachable=True,
         inputs=("len(df)",), diag=("pullback",),
         test=None,
         note="_is_pullback returns None. Distinct from a failed pullback."),
    dict(line=71, code="no_pullback", cls=RULE, reachable=True,
         inputs=("df.close", "trend"), diag=("pullback",),
         test="test_every_rule_gate_classifies", note=""),
    dict(line=109, code="no_candle_confirmation", cls=RULE, reachable=True,
         inputs=("last_candle", "prev_candle", "avg_body", "zones"),
         diag=("has_momentum", "engulfing", "reaction", "candle_confirmed",
               "avg_body", "near_sr_zone"),
         test="test_every_rule_gate_classifies",
         note="87% kill rate before the forming-bar fix in cf4e3d4. A "
              "half-built bar could not exceed 2x the average completed body."),
    dict(line=116, code="vwap_unavailable", cls=CANNOT, reachable=True,
         inputs=("last_candle.vwap",), diag=("vwap",), test=None, note=""),
    dict(line=118, code="vwap_misaligned", cls=RULE, reachable=True,
         inputs=("trend", "price", "vwap"), diag=("vwap", "price", "vwap_aligned"),
         test="test_every_rule_gate_classifies",
         note="LONG variant: price below VWAP. Shares a code with line 120."),
    dict(line=120, code="vwap_misaligned", cls=RULE, reachable=True,
         inputs=("trend", "price", "vwap"), diag=("vwap", "price", "vwap_aligned"),
         test="test_every_rule_gate_classifies",
         note="SHORT variant: price above VWAP. This is the sixteenth site "
              "that reconciles 16 returns to 15 codes."),
    dict(line=136, code="volume_average_unavailable", cls=CANNOT, reachable=True,
         inputs=("last_candle.vol_avg",), diag=("vol_avg",), test=None,
         note="Also fires when vol_avg <= 0, which is a data fault not a "
              "quiet market."),
    dict(line=138, code="volume_too_low", cls=RULE, reachable=True,
         inputs=("volume", "vol_avg", "volume_threshold_pullback"),
         diag=("volume", "vol_avg", "vol_ratio", "vol_threshold"),
         test="test_every_rule_gate_classifies",
         note="100% kill rate before cf4e3d4: a forming bar's partial volume "
              "was compared against an average of completed bars."),
    dict(line=147, code="atr_unavailable", cls=CANNOT, reachable=True,
         inputs=("last_candle.atr",), diag=("atr",), test=None, note=""),
    dict(line=149, code="atr_invalid", cls=RULE, reachable=True,
         inputs=("atr",), diag=("atr",), test="test_every_rule_gate_classifies",
         note="atr <= 0. Arguably a data fault rather than a rule violation, "
              "but classified as a gate because the value was present."),
    dict(line=181, code="sl_distance_invalid", cls=RULE, reachable=True,
         inputs=("entry", "sl_price"),
         diag=("direction", "entry", "sl_raw", "sl_distance"),
         test="test_every_rule_gate_classifies",
         note="SL on the wrong side of entry. Reachable when swing structure "
              "places the stop beyond the entry price."),
    dict(line=203, code="atr_too_low", cls=RULE, reachable=True,
         inputs=("atr", "min_atr"), diag=("atr", "min_atr"),
         test="test_every_rule_gate_classifies",
         note="Checked AFTER SL and TP are computed, so tp_distance exists on "
              "this path. Ordering is behaviourally irrelevant but matters for "
              "replay parity."),
    dict(line=208, code="sl_too_tight", cls=RULE, reachable=True,
         inputs=("sl_distance", "atr", "sl_atr_low"),
         diag=("sl_distance", "sl_atr_ratio", "atr"),
         test="test_every_rule_gate_classifies", note=""),
    dict(line=210, code="sl_too_wide", cls=RULE, reachable=True,
         inputs=("sl_distance", "atr", "sl_atr_high"),
         diag=("sl_distance", "sl_atr_ratio", "atr"),
         test="test_every_rule_gate_classifies",
         note="Drives the minimum viable capital requirement: a 2.5x ATR stop "
              "is the widest the account must be able to express."),
    dict(line=241, code="OK", cls=OK, reachable=True,
         inputs=("all gates passed",),
         diag=("direction", "entry", "sl_raw", "tp", "tp_distance",
               "tp_rr_multiple", "sl_atr_ratio"),
         test="test_tp_distance_unchanged",
         note="Zero occurrences in 14 days of live running. Not yet exercised "
              "against real data."),
)


# --------------------------------------------------------------------------
# risk_manager.approve -- 6 return sites
# --------------------------------------------------------------------------

RISK_PATHS = (
    dict(line=136, code="daily_loss_limit", cls=RISK, reachable=True,
         inputs=("trades.json", "daily_loss_limit"),
         diag=("risk_reason",), test=None,
         note="Counts trades with outcome SL_HIT today. Count-based, so the "
              "monetary size of those losses floats. Three losses at 1% is 3%, "
              "but was 30% before the sizing fix in 495e078."),
    dict(line=143, code="positions_unknown", cls=CANNOT, reachable=True,
         inputs=("data_feed.get_positions",), diag=("risk_reason",), test=None,
         note="Fails closed to avoid double entry. Correct direction: this "
              "layer errs toward lost opportunity, not lost capital."),
    dict(line=146, code="max_open_trades", cls=RISK, reachable=True,
         inputs=("positions", "max_open_trades"), diag=("risk_reason",),
         test=None, note=""),
    dict(line=151, code="no_trade_filters", cls=RISK, reachable=True,
         inputs=("spread_points", "session", "max_spread_points",
                 "trading_sessions"),
         diag=("risk_reason",), test=None,
         note="KNOWN GAP: returns '; '.join(filter_reasons), so one row can "
              "carry several causes in a single free-form string and cannot be "
              "attributed to one. Structured codes are needed before the risk "
              "layer can appear in a gate funnel."),
    dict(line=158, code="position_sizing", cls=RISK, reachable=True,
         inputs=("balance", "sl_distance", "contract_size", "volume_min"),
         diag=("position_size", "balance", "contract_size", "volume_min"),
         test="test_small_account_rejects_rather_than_overrisks",
         note="Added in 495e078. Previously sub-minimum sizes were rounded up, "
              "silently exceeding the risk budget."),
    dict(line=168, code="approved", cls=OK, reachable=True,
         inputs=("all risk checks passed",),
         diag=("position_size", "risk_amount", "daily_losses"),
         test=None,
         note="Zero occurrences live. Never exercised against real data."),
)


# --------------------------------------------------------------------------
# risk_manager.calculate_position_size -- 6 return sites
# --------------------------------------------------------------------------

SIZING_PATHS = (
    dict(line=79, code="sl_distance_not_positive", cls=RISK, reachable=False,
         inputs=("signal.sl_distance",), diag=(), test="test_one_return_shape",
         note="UNREACHABLE via approve(): strategy.evaluate already rejects "
              "sl_distance <= 0 at line 181, so this cannot fire in the live "
              "path. Retained as a contract guard because the function is "
              "directly callable and tested in isolation."),
    dict(line=95, code="contract_size_unavailable", cls=CANNOT, reachable=True,
         inputs=("symbol_info.contract_size", "params.contract_size"),
         diag=(), test="test_missing_contract_size_fails_closed",
         note="Fails closed rather than assuming a value. The previous "
              "hardcoded tick_value default is exactly what this prevents."),
    dict(line=100, code="loss_per_lot_not_positive", cls=RISK, reachable=False,
         inputs=("sl_distance", "contract_size"), diag=(), test=None,
         note="UNREACHABLE: both factors are already proven positive above. "
              "Defensive only."),
    dict(line=117, code="position_size_below_minimum", cls=RISK, reachable=True,
         inputs=("risk_amount", "loss_per_lot", "volume_min"), diag=(),
         test="test_small_account_rejects_rather_than_overrisks",
         note="The binding constraint on minimum viable capital."),
    dict(line=124, code="position_size_above_maximum", cls=RISK, reachable=True,
         inputs=("lot_size", "volume_max"), diag=(),
         test=None,
         note="Reachable on a large balance with a tight stop. Untested."),
    dict(line=126, code="sized", cls=OK, reachable=True,
         inputs=("all sizing checks passed",), diag=("position_size",),
         test="test_matches_terminal_ground_truth", note=""),
)


# --------------------------------------------------------------------------
# Helper returns, deliberately NOT decision paths
# --------------------------------------------------------------------------

HELPER_RETURNS = (
    dict(func="strategy._is_pullback", count=4,
         note="Returns None / True / False to evaluate, which converts them "
              "into two of its own return paths at lines 69 and 71. Counting "
              "these as decision paths would double-count and was the source "
              "of the original 16-versus-15 confusion."),
    dict(func="risk_manager.check_no_trade_filters", count=1,
         note="Returns a list of reason strings, not a decision. approve() "
              "turns a non-empty list into its line 151 return."),
)


# Branches removed because they could never fire. Kept so the removal is
# discoverable and cannot be quietly reintroduced.
REMOVED_DEAD_BRANCHES = (
    dict(code="min_rr_ratio_check", removed_in="17e05e8",
         was_in="risk_manager.approve",
         note="if rr < min_rr, where rr was computed as tp_distance / "
              "sl_distance and tp_distance as sl_distance * min_rr. The "
              "comparison was a value against itself and could never fire. A "
              "believed-in protection that does not exist is worse than none, "
              "because it changes behaviour. See tests/test_rr_semantics.py::"
              "test_old_rr_check_was_unreachable."),
)


CATALOGUES = {
    ("engine/strategy.py", "evaluate"): STRATEGY_PATHS,
    ("engine/risk_manager.py", "approve"): RISK_PATHS,
    ("engine/risk_manager.py", "calculate_position_size"): SIZING_PATHS,
}


def count_returns(rel_path, func_name):
    """Count return statements belonging to func_name, excluding nested defs."""
    tree = ast.parse((PROJECT_ROOT / rel_path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            nested = {c for c in ast.walk(node)
                      if isinstance(c, ast.FunctionDef) and c is not node}
            nested_returns = {r for n in nested
                              for r in ast.walk(n) if isinstance(r, ast.Return)}
            return len([r for r in ast.walk(node)
                        if isinstance(r, ast.Return) and r not in nested_returns])
    raise LookupError(f"{func_name} not found in {rel_path}")


def refresh_lines(rel_path, func_name):
    """Current line numbers of every return, for updating the catalogue."""
    tree = ast.parse((PROJECT_ROOT / rel_path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return sorted(r.lineno for r in ast.walk(node)
                          if isinstance(r, ast.Return))
    raise LookupError(f"{func_name} not found in {rel_path}")


def reason_codes(paths):
    """Distinct reason codes, which is fewer than the path count where one
    code is emitted from several sites."""
    return sorted({p["code"] for p in paths})


def coverage_gaps():
    """Catalogued paths with no referenced unit test.

    Reported rather than hidden. An honest gap list is more useful than a
    coverage percentage, because it names what to write next.
    """
    gaps = []
    for (path, func), entries in CATALOGUES.items():
        for e in entries:
            if not e["test"]:
                gaps.append(f"{func}:{e['line']} {e['code']}")
    return gaps


def summary():
    lines = [f"Return-path catalogue (lines as at {LINES_AUDITED_AT})", ""]
    for (path, func), entries in CATALOGUES.items():
        actual = count_returns(path, func)
        codes = reason_codes(entries)
        by_class = {}
        for e in entries:
            by_class[e["cls"]] = by_class.get(e["cls"], 0) + 1
        unreachable = [e for e in entries if not e["reachable"]]
        lines.append(f"{func}")
        lines.append(f"  return sites     {actual} in source, {len(entries)} catalogued")
        lines.append(f"  distinct codes   {len(codes)}")
        lines.append(f"  by class         {dict(sorted(by_class.items()))}")
        lines.append(f"  unreachable      {len(unreachable)}"
                     + (f" ({', '.join(e['code'] for e in unreachable)})"
                        if unreachable else ""))
        lines.append("")
    for h in HELPER_RETURNS:
        lines.append(f"helper (excluded)  {h['func']}: {h['count']} returns")
    lines.append("")
    gaps = coverage_gaps()
    lines.append(f"paths without a named test: {len(gaps)}")
    for g in gaps:
        lines.append(f"  - {g}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())