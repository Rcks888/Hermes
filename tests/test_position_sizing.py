#!/usr/bin/env python3
"""Regression tests for position sizing.

Two defects, both confirmed against the live terminal on MetaQuotes-Demo:

1. Value per point came from a hardcoded tick_value of 0.1. order_calc_profit
   returned $100.00 for a $1.00 move on 1.00 lot with contract_size 100, so the
   true figure is $1.00 per point per lot, not $0.10. Every position was sized
   ten times too large: at 1% intended risk on a 6.53 stop, Hermes produced
   15.31 lots where 1.53 was correct, realising 10% risk at 64:1 leverage.

2. Sub-minimum sizes were rounded UP to the broker minimum, silently exceeding
   the risk budget. On a $100 account at 1% risk a 6.53 stop needs 0.0015 lots;
   rounding to 0.01 risks $6.53, or 6.5% of balance. This matters directly
   because live trading is planned to start near $100.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import risk_manager

# As reported by MetaQuotes-Demo for XAUUSD. trade_tick_value is deliberately
# excluded: the broker reports 0.1, contradicted by its own terminal.
SPEC = {
    "contract_size": 100.0,
    "volume_min": 0.01,
    "volume_step": 0.01,
    "volume_max": 100.0,
    "point": 0.01,
}
PARAMS = {"risk_pct": 0.01}


def test_matches_terminal_ground_truth():
    """1% of 100k on a 6.53 stop is 1.53 lots, not the previous 15.31."""
    lots, reason = risk_manager.calculate_position_size(
        {"sl_distance": 6.53}, 100_000.0, PARAMS, SPEC)
    assert reason is None, reason
    assert abs(lots - 1.53) < 0.01, f"got {lots}, expected 1.53"


def test_realised_risk_matches_intent():
    """Realised risk must not exceed the budget at any plausible stop."""
    balance = 100_000.0
    for sl in [3.0, 6.53, 10.0, 25.4]:
        lots, reason = risk_manager.calculate_position_size(
            {"sl_distance": sl}, balance, PARAMS, SPEC)
        assert reason is None, reason
        realised = lots * sl * SPEC["contract_size"]
        assert realised <= balance * 0.01 + 0.01, (
            f"sl={sl}: realised {realised} exceeds 1% budget")


def test_small_account_rejects_rather_than_overrisks():
    """A $100 account cannot express 1% risk on a 6.53 stop. Reject, not round up."""
    lots, reason = risk_manager.calculate_position_size(
        {"sl_distance": 6.53}, 100.0, PARAMS, SPEC)
    assert lots == 0.0
    assert reason is not None
    assert "position_size_below_minimum" in reason


def test_missing_contract_size_fails_closed():
    """No contract_size means no sizing. Never assume a value."""
    lots, reason = risk_manager.calculate_position_size(
        {"sl_distance": 6.53}, 100_000.0, {"risk_pct": 0.01}, {})
    assert lots == 0.0
    assert "cannot_evaluate" in reason


def test_broker_tick_value_is_not_used():
    """A wrong trade_tick_value must not influence the result."""
    poisoned = dict(SPEC)
    poisoned["tick_value"] = 0.1
    poisoned["trade_tick_value"] = 0.1
    a, _ = risk_manager.calculate_position_size({"sl_distance": 6.53}, 100_000.0, PARAMS, SPEC)
    b, _ = risk_manager.calculate_position_size({"sl_distance": 6.53}, 100_000.0, PARAMS, poisoned)
    assert a == b, "sizing changed when a bogus tick_value was supplied"


def test_rounds_down_not_up():
    """Step rounding must never push realised risk above the budget."""
    lots, reason = risk_manager.calculate_position_size(
        {"sl_distance": 7.77}, 50_000.0, PARAMS, SPEC)
    assert reason is None, reason
    assert lots * 7.77 * 100.0 <= 500.0 + 0.01


def test_one_return_shape():
    """Every path returns a 2-tuple."""
    cases = [
        ({"sl_distance": 6.53}, 100_000.0, PARAMS, SPEC),
        ({"sl_distance": 0}, 100_000.0, PARAMS, SPEC),
        ({"sl_distance": 6.53}, 100.0, PARAMS, SPEC),
        ({"sl_distance": 6.53}, 100_000.0, PARAMS, {}),
    ]
    for sig, bal, p, spec in cases:
        r = risk_manager.calculate_position_size(sig, bal, p, spec)
        assert isinstance(r, tuple) and len(r) == 2, f"bad shape: {r}"


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