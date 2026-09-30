#!/usr/bin/env python3
"""Tests for the research dataset schema (0d).

The schema's job is to make certain mistakes impossible to record silently.
These tests assert the validators actually catch them, because a validator
that passes everything is worse than none -- it creates false confidence.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import research_schema as rs

# A real event, as written by run_hermes on 2026-09-30.
LIVE_EVENT = {
    "ts": "2026-09-30T07:47:26.239706+00:00",
    "code_version": "0ff9e3c",
    "event": "NO_SIGNAL",
    "session": "LONDON",
    "blocked_at": "trend_not_confirmed (trend=DOWN, bos_up=1, bos_down=1)",
    "blocked_class": "rule_violation",
    "bar_time": "2026-09-30 10:30:00+00:00",
    "candle_hash": "92304fb7543ccd9b",
    "config_hash": "265c3dd24d68",
    "bar_count": 200,
    "close": 4191.85,
    "spread_points": 38,
    "ohlc": {"open": 4195.13, "high": 4198.87, "low": 4191.23,
             "close": 4191.85, "tick_volume": 3667.0, "bar_spread": 8},
    "diag": {"trend": "DOWN", "trend_confirmed": False, "bos_up": 1,
             "bos_down": 1, "swing_high_count": 18, "swing_low_count": 19,
             "sr_zone_count": 4},
}


def test_real_event_projects_and_validates():
    row = rs.evaluation_row_from_event(
        LIVE_EVENT, bar_time_utc="2026-09-30T07:30:00+00:00")
    problems = rs.validate_evaluation(row)
    assert problems == [], problems
    assert row["block_class"] == "rule_violation"
    assert row["blocked_at"] == "trend_not_confirmed"
    assert row["bar_spread"] == 8
    assert row["live_spread"] == 38


def test_gate_name_separated_from_detail():
    """The gate must be a bare name, with the prose kept separately."""
    row = rs.evaluation_row_from_event(LIVE_EVENT, bar_time_utc="x")
    assert row["blocked_at"] == "trend_not_confirmed"
    assert "bos_up=1" in row["blocked_detail"]


def test_cannot_evaluate_is_its_own_class():
    cls, detail = rs.classify_blocked_at("cannot_evaluate: vwap unavailable")
    assert cls == "cannot_evaluate"
    assert detail == "vwap unavailable"
    assert cls != "rule_violation", "absent evidence must never be a rule violation"


def test_every_rule_gate_classifies():
    for gate in rs.RULE_GATES:
        cls, name = rs.classify_blocked_at(f"{gate} (some detail here)")
        assert cls == "rule_violation", gate
        assert name == gate


def test_unknown_gate_does_not_silently_pass():
    """A gate renamed without updating the schema must be visible."""
    row = rs.evaluation_row_from_event(
        {**LIVE_EVENT, "blocked_at": "brand_new_gate (x)", "blocked_class": None},
        bar_time_utc="x")
    assert row["block_class"] == "unknown", row["block_class"]
    problems = rs.validate_evaluation(row)
    assert any("unrecognised block reason" in p for p in problems), problems


def test_counterfactual_must_declare_itself():
    row = {
        "schema_version": rs.SCHEMA_VERSION, "source": "REPLAY",
        "setup_id": "s1", "symbol": "XAUUSD", "timeframe": "M15",
        "code_version": "abc1234", "config_hash": "def567",
        "population": "A", "outcome_type": "COUNTERFACTUAL_DIAGNOSTIC",
        "first_bar_time": "t0", "entry_is_hypothetical": False,
        "horizon_bars": 32, "direction": "LONG", "sl": 4180.0, "tp": 4220.0,
        "sl_distance": 10.0, "tp_rr_multiple": 2.0, "session": "LONDON",
        "intrabar_ambiguous": False,
    }
    problems = rs.validate_setup(row)
    assert any("entry_is_hypothetical" in p for p in problems), problems


def test_counterfactual_cannot_carry_cash():
    """No capital was risked, so a cash result would be fiction."""
    row = {
        "schema_version": rs.SCHEMA_VERSION, "source": "REPLAY",
        "setup_id": "s1", "symbol": "XAUUSD", "timeframe": "M15",
        "code_version": "abc1234", "config_hash": "def567",
        "population": "A", "outcome_type": "COUNTERFACTUAL_DIAGNOSTIC",
        "first_bar_time": "t0", "entry_is_hypothetical": True,
        "horizon_bars": 32, "direction": "LONG", "sl": 4180.0, "tp": 4220.0,
        "sl_distance": 10.0, "tp_rr_multiple": 2.0, "session": "LONDON",
        "intrabar_ambiguous": False, "net_cash": 250.0,
    }
    problems = rs.validate_setup(row)
    assert any("net_cash" in p for p in problems), problems


def test_actual_simulated_requires_population_c():
    row = {
        "schema_version": rs.SCHEMA_VERSION, "source": "REPLAY",
        "setup_id": "s2", "symbol": "XAUUSD", "timeframe": "M15",
        "code_version": "abc1234", "config_hash": "def567",
        "population": "A", "outcome_type": "ACTUAL_SIMULATED",
        "first_bar_time": "t0", "entry_is_hypothetical": False,
        "horizon_bars": 32, "direction": "LONG", "sl": 4180.0, "tp": 4220.0,
        "sl_distance": 10.0, "tp_rr_multiple": 2.0, "session": "LONDON",
        "intrabar_ambiguous": False,
    }
    problems = rs.validate_setup(row)
    assert any("population C" in p for p in problems), problems


def test_excursion_without_window_is_rejected():
    """MFE with no measurement window has unlimited hindsight."""
    row = {
        "schema_version": rs.SCHEMA_VERSION, "source": "REPLAY",
        "setup_id": "s3", "symbol": "XAUUSD", "timeframe": "M15",
        "code_version": "abc1234", "config_hash": "def567",
        "population": "C", "outcome_type": "ACTUAL_SIMULATED",
        "first_bar_time": "t0", "entry_is_hypothetical": False,
        "horizon_bars": 32, "direction": "LONG", "sl": 4180.0, "tp": 4220.0,
        "sl_distance": 10.0, "tp_rr_multiple": 2.0, "session": "LONDON",
        "intrabar_ambiguous": False, "mfe_r": 1.8,
    }
    problems = rs.validate_setup(row)
    assert any("MFE/MAE" in p for p in problems), problems


def test_diag_fields_all_have_schema_homes():
    """Every field the strategy emits must have somewhere to land."""
    emitted = {
        "trend", "trend_confirmed", "bos_up", "bos_down", "bos_total",
        "last_bos_direction", "bars_since_last_bos", "trend_scope_bars",
        "bos_window_bars", "swing_high_count", "swing_low_count",
        "sr_zone_count", "pullback", "avg_body", "has_momentum", "engulfing",
        "reaction", "near_sr_zone", "candle_confirmed", "price", "vwap",
        "vwap_aligned", "volume", "vol_avg", "vol_threshold", "vol_ratio",
        "atr", "at_zone", "min_atr", "direction", "entry", "sl_raw",
        "sl_buffer", "sl_distance", "sl_atr_ratio", "tp_rr_multiple",
        "tp_distance", "tp",
    }
    missing = emitted - set(rs.EVALUATION_FIELD_NAMES)
    assert not missing, f"strategy emits fields the schema cannot store: {sorted(missing)}"


def test_source_is_mandatory_and_checked():
    row = rs.evaluation_row_from_event(LIVE_EVENT, bar_time_utc="x")
    row["source"] = "GUESSWORK"
    assert any("source" in p for p in rs.validate_evaluation(row))


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                print(f"FAIL  {name}: {e}")
                failures += 1
    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)