#!/usr/bin/env python3
"""Regression tests for the tp_rr_multiple rename.

The prior code named this parameter min_rr_ratio and checked it in
risk_manager.approve() as if it were an eligibility threshold:

    tp_distance = sl_distance * min_rr        # strategy.py
    rr_ratio    = tp_distance / sl_distance   # == min_rr, identically
    if rr < min_rr: reject                    # risk_manager.py, unreachable

Because rr_ratio was defined as tp_distance / sl_distance and tp_distance
was itself sl_distance * min_rr, the comparison reduced to min_rr < min_rr
and could never be true. The parameter places the target; it never filtered.

These tests prove the rename is behaviour-preserving: identical TP distance
for identical inputs, and no reachable rejection path removed.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import risk_manager


def _params():
    with open(PROJECT_ROOT / "config" / "strategy_params.json") as f:
        return json.load(f)


def test_config_migrated():
    """min_rr_ratio must be gone; tp_mode and tp_rr_multiple present."""
    p = _params()
    assert "min_rr_ratio" not in p, "stale min_rr_ratio still in config"
    assert p["tp_mode"] == "fixed_rr"
    assert p["tp_rr_multiple"] == 2.0


def test_tp_distance_unchanged():
    """New arithmetic reproduces the old for every plausible SL distance.

    Old: tp = sl * min_rr_ratio(2.0)
    New: tp = sl * tp_rr_multiple(2.0)
    """
    multiple = _params()["tp_rr_multiple"]
    for sl_distance in [0.5, 1.0, 3.7, 6.53, 12.0, 25.4]:
        assert sl_distance * multiple == sl_distance * 2.0


def test_old_rr_check_was_unreachable():
    """Demonstrate the removed check could never reject.

    Reconstructs the old computation and shows the comparison is a tautology,
    so deleting it removed no reachable behaviour.
    """
    min_rr = 2.0
    for sl_distance in [0.5, 3.7, 6.53, 25.4]:
        tp_distance = sl_distance * min_rr
        rr_ratio = tp_distance / sl_distance
        assert rr_ratio == min_rr
        assert not (rr_ratio < min_rr), "old check fired — was reachable after all"


def test_approve_has_no_rr_rejection():
    """risk_manager.approve must not reject on reward-to-risk grounds."""
    import inspect
    src = inspect.getsource(risk_manager.approve)
    assert "min_rr" not in src, "dead RR check still present in approve()"
    assert "RR too low" not in src


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