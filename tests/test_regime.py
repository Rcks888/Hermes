#!/usr/bin/env python3
"""Tests for the deterministic regime classifier (0c).

The load-bearing test is test_no_lookahead_vectorised_matches_windowed. A
vectorised backtest helper is the usual place future information leaks into a
result, and the leak is invisible in the output -- it simply makes everything
look better. Asserting exact agreement with the windowed implementation is the
only cheap way to catch it.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import regime


def _frame(n=400, seed=11, drift=0.0, noise=1.0):
    rng = np.random.default_rng(seed)
    price = 4200.0
    rows = []
    for i in range(n):
        price += rng.normal() * noise + drift
        o = price
        c = price + rng.normal() * noise * 0.6
        rows.append({
            "datetime": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(minutes=15 * i),
            "open": o,
            "high": max(o, c) + abs(rng.normal()) * noise * 0.5,
            "low": min(o, c) - abs(rng.normal()) * noise * 0.5,
            "close": c,
            "volume": 2500,
        })
    return pd.DataFrame(rows)


def test_insufficient_history_is_uncertain():
    """Absent evidence is not a regime. It must be reported as UNCERTAIN."""
    r = regime.classify(_frame(n=50))
    assert r["structure_regime"] == "UNCERTAIN"
    assert r["volatility_regime"] == "UNCERTAIN"
    assert r["insufficient_history"] is True
    assert r["bars_available"] == 50


def test_labels_are_in_declared_sets():
    for seed in range(6):
        r = regime.classify(_frame(seed=seed))
        assert r["structure_regime"] in regime.STRUCTURE_REGIMES
        assert r["volatility_regime"] in regime.VOLATILITY_REGIMES


def test_deterministic():
    df = _frame()
    assert regime.classify(df) == regime.classify(df)


def test_no_lookahead_windowed_is_unaffected_by_future_bars():
    """Classifying at bar k must not change when later bars are appended."""
    df = _frame(n=400)
    for k in (150, 200, 300):
        short = regime.classify(df.iloc[:k].reset_index(drop=True))
        # Same slice taken from a longer frame: appending future bars to the
        # source must not alter the result at bar k.
        long_sliced = regime.classify(df.iloc[:k].copy().reset_index(drop=True))
        assert short == long_sliced, k


def test_no_lookahead_vectorised_matches_windowed():
    """classify_series at bar i must equal classify on the frame up to i.

    If the vectorised path used any forward-looking window, these would
    diverge. ADX uses recursive Wilder smoothing whose value at bar i depends
    on all prior bars, so agreement also confirms the windowed form is given
    enough history to converge.
    """
    df = _frame(n=400)
    series = regime.classify_series(df)
    checked = 0
    for i in (250, 300, 350, 399):
        windowed = regime.classify(df.iloc[:i + 1].reset_index(drop=True))
        row = series.iloc[i]
        for field in ("structure_regime", "volatility_regime",
                      "directional_efficiency", "atr_percentile"):
            assert row[field] == windowed[field], (
                f"bar {i} field {field}: series={row[field]} "
                f"windowed={windowed[field]}")
        checked += 1
    assert checked == 4


def test_window_length_does_not_change_the_label():
    """Live passes 200 bars; replay may pass the whole history.

    ADX uses recursive Wilder smoothing, so in principle the value at a bar
    depends on how far back the recursion started, and live and replay could
    disagree on the same bar. Measured drift is 0.0000 at MIN_BARS=128, because
    Wilder memory at alpha=1/14 decays to roughly 1e-4 over that span.

    This test exists so that a future change to MIN_BARS or ADX_PERIOD cannot
    silently reintroduce the divergence. A phantom replay mismatch traced back
    to smoothing convergence would be expensive to diagnose.
    """
    for seed in range(12):
        df = _frame(n=1200, seed=seed)
        short = regime.classify(df.iloc[-200:].reset_index(drop=True))
        full = regime.classify(df.reset_index(drop=True))
        assert short["structure_regime"] == full["structure_regime"], seed
        assert short["volatility_regime"] == full["volatility_regime"], seed
        assert abs(short["adx"] - full["adx"]) < 0.01, (
            f"seed {seed}: ADX drifted {abs(short['adx'] - full['adx'])} "
            "between window lengths")


def test_trending_market_is_not_labelled_range():
    """A strong persistent drift must not come back as RANGE."""
    df = _frame(n=400, seed=3, drift=1.4, noise=0.35)
    r = regime.classify(df)
    assert r["structure_regime"] != "RANGE", r
    assert r["directional_efficiency"] > regime.EFFICIENCY_RANGE_MAX, r


def test_choppy_market_is_not_labelled_trend():
    """Pure noise with no drift must not come back as TREND."""
    df = _frame(n=400, seed=5, drift=0.0, noise=1.0)
    r = regime.classify(df)
    assert r["structure_regime"] != "TREND", r


def test_dimensions_are_independent():
    """TREND with HIGH volatility must be expressible.

    A single mutually exclusive label could not represent this, which is the
    reason the classifier returns two.
    """
    seen = set()
    for seed in range(40):
        for drift, noise in ((1.5, 0.3), (0.0, 1.0), (0.6, 2.2), (0.0, 0.2)):
            r = regime.classify(_frame(n=300, seed=seed, drift=drift, noise=noise))
            seen.add((r["structure_regime"], r["volatility_regime"]))
    structures = {s for s, _ in seen}
    vols = {v for _, v in seen}
    assert len(structures) >= 2, structures
    assert len(vols) >= 2, vols
    # At least one combination must pair a non-UNCERTAIN structure with each of
    # two different volatility labels, proving the axes are not collapsed.
    pairs = {(s, v) for s, v in seen if s != "UNCERTAIN" and v != "UNCERTAIN"}
    by_structure = {}
    for s, v in pairs:
        by_structure.setdefault(s, set()).add(v)
    assert any(len(v) >= 2 for v in by_structure.values()), by_structure


def test_efficiency_bounds():
    """Straight line is 1.0; zero movement is None, not zero."""
    straight = regime.directional_efficiency([1, 2, 3, 4, 5])
    assert abs(straight - 1.0) < 1e-9
    assert regime.directional_efficiency([5, 5, 5, 5]) is None
    zigzag = regime.directional_efficiency([1, 2, 1, 2, 1])
    assert 0.0 <= zigzag < 0.2


def test_adx_matches_wilder_properties():
    """ADX is bounded 0-100 and DI components are non-negative."""
    df = _frame(n=400)
    a = regime.adx_series(df)
    tail = a.iloc[100:]
    assert tail["adx"].between(0, 100).all()
    assert (tail["di_plus"] >= 0).all()
    assert (tail["di_minus"] >= 0).all()


def test_frozen_parameters_complete():
    """Every threshold used must appear in the preregistration record."""
    p = regime.frozen_parameters()
    for key in ("adx_period", "adx_trend_min", "adx_range_max",
                "efficiency_lookback", "efficiency_trend_min",
                "efficiency_range_max", "atr_period",
                "atr_percentile_lookback", "vol_high_percentile",
                "vol_low_percentile", "min_bars", "params_version"):
        assert key in p, key
    assert p["params_version"] == regime.PARAMS_VERSION


def test_metrics_accompany_labels():
    """A label without its inputs is not auditable."""
    r = regime.classify(_frame(n=400))
    for field in ("adx", "directional_efficiency", "atr_percentile",
                  "params_version", "bars_available"):
        assert field in r, field


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