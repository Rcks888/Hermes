#!/usr/bin/env python3
"""C2.1 parity: engine.pipeline.evaluate_closed_bars must reproduce the
sequence run_hermes.main() ran inline before it was lifted out.

REFERENCE below is that inline sequence, copied verbatim from run_hermes.py at
119abd4. Each window is run through both and every output is compared: signal,
reason, diag, structure, zones and the indicator columns.

Windows: synthetic random walks (always), plus real M15 bars after the sealed
holdout (2026-09-12 onward) when the local snapshot is present. Holdout bars
are never used (PREREGISTRATION F6).

Control: --controls runs the same comparison against a broken variant that
drops the last closed bar, and requires it to fail.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import indicators, market_structure, pipeline, strategy  # noqa: E402

PARAMS = json.loads((ROOT / "config" / "strategy_params.json").read_text())
SNAPSHOT = ROOT / "data" / "history" / "20261027T070105Z" / "XAUUSD_M15.csv"
POST_HOLDOUT = 1789171200  # server 2026-09-12 00:00, export_history.HOLDOUT_END
# Live signal bars inside the snapshot (server clock, bar open): 2 Oct, 7 Oct,
# 20 Oct and 23 Oct 2026. Always included so the signal path is compared.
LIVE_SIGNAL_BARS = (1790934300, 1791394200, 1792509300, 1792773900)
FULL = "--full" in sys.argv


def reference(candles, params):
    structure = market_structure.analyze(candles, params.get("swing_lookback", 3))
    candles = indicators.calculate_vwap(candles)
    candles = indicators.volume_average(candles, params.get("volume_avg_period", 20))
    candles = indicators.calculate_atr(candles)
    zones = indicators.find_sr_zones(
        candles, structure["swing_highs"], structure["swing_lows"],
        params.get("sr_zone_cluster_pct", 0.5),
        params.get("sr_min_touches", 2),
    )
    diag = {}
    signal, reason = strategy.evaluate(candles, structure, zones, params, diag)
    return {"candles": candles, "structure": structure, "zones": zones,
            "signal": signal, "reason": reason, "diag": diag}


def broken_drop_last(candles, params):
    return pipeline.evaluate_closed_bars(candles.iloc[:-1].reset_index(drop=True), params)


def frame(t, o, h, l, c, v, s):
    df = pd.DataFrame({"datetime": pd.to_datetime(t, unit="s", utc=True),
                       "open": o, "high": h, "low": l, "close": c,
                       "volume": v, "spread": s, "real_volume": 0})
    return df


def synthetic_windows(n=40, bars=200):
    rng = np.random.default_rng(20261112)
    out = []
    for k in range(n):
        steps = rng.normal(0, 3.0, bars) + (0.6 if k % 2 else -0.6)
        close = 4000 + np.cumsum(steps)
        open_ = np.r_[close[0] - steps[0], close[:-1]]
        hi = np.maximum(open_, close) + rng.uniform(0, 4, bars)
        lo = np.minimum(open_, close) - rng.uniform(0, 4, bars)
        t = 1790000000 + 900 * np.arange(bars) + k * 900 * bars
        out.append(frame(t, open_.round(2), hi.round(2), lo.round(2), close.round(2),
                         rng.integers(500, 6000, bars), rng.integers(10, 60, bars)))
    return out


def real_windows(bars=200):
    """Every 25th post-holdout window plus the live signal bars; --full takes every window."""
    if not SNAPSHOT.exists():
        return []
    df = pd.read_csv(SNAPSHOT)
    df = df[df["time"] >= POST_HOLDOUT].reset_index(drop=True)
    ends = set(range(bars, len(df) + 1, 1 if FULL else 25))
    for t in LIVE_SIGNAL_BARS:
        hit = df.index[df["time"] == t]
        if len(hit):
            ends.add(int(hit[0]) + 1)
    out = []
    for end in sorted(e for e in ends if e >= bars):
        w = df.iloc[end - bars:end]
        out.append(frame(w["time"].values, w["open"].values, w["high"].values,
                         w["low"].values, w["close"].values, w["tick_volume"].values,
                         w["spread"].values))
    return out


def same(a, b):
    if a["reason"] != b["reason"] or a["signal"] != b["signal"]:
        return "signal/reason"
    if json.dumps(a["diag"], sort_keys=True, default=str) != json.dumps(b["diag"], sort_keys=True, default=str):
        return "diag"
    if repr(a["structure"]) != repr(b["structure"]) or repr(a["zones"]) != repr(b["zones"]):
        return "structure/zones"
    if not a["candles"].equals(b["candles"]):
        return "indicator columns"
    return None


def compare(fn, windows):
    diffs, signals = [], 0
    for i, w in enumerate(windows):
        ref = reference(w.copy(), PARAMS)
        new = fn(w.copy(), PARAMS)
        signals += ref["signal"] is not None
        d = same(ref, new)
        if d:
            diffs.append((i, d))
    return diffs, signals


def test_pipeline_matches_inline_sequence():
    syn, real = synthetic_windows(), real_windows()
    d1, s1 = compare(pipeline.evaluate_closed_bars, syn)
    d2, s2 = compare(pipeline.evaluate_closed_bars, real)
    print(f"      synthetic {len(syn)} windows ({s1} signals), real post-holdout "
          f"{len(real)} windows ({s2} signals)")
    assert not d1 and not d2, f"differences: synthetic {d1[:3]} real {d2[:3]}"
    assert not real or s2 > 0, "no window produced a signal, so the signal path was not compared"


def test_stopwatch_marks_as_inline():
    from engine import entry_observation as eo
    sw = eo.Stopwatch()
    pipeline.evaluate_closed_bars(synthetic_windows(1)[0], PARAMS, stopwatch=sw)
    assert list(sw.all_stamps()) == ["evaluation_start_time", "evaluation_end_time"]
    assert sw.elapsed_ms("evaluation_start_time", "evaluation_end_time") >= 0


def test_session_tag_unchanged():
    expect = {h: ("ASIA" if h in (23, 0, 1, 2, 3, 4, 5, 6) else "LONDON" if 7 <= h < 12
                  else "NY" if 12 <= h < 21 else "OFF") for h in range(24)}
    assert {h: pipeline.get_session_tag(h) for h in range(24)} == expect


def controls():
    windows = synthetic_windows() + real_windows()
    diffs, _ = compare(broken_drop_last, windows)
    ok = len(diffs) > 0
    print(f"{'PASS' if ok else 'FAIL'}  control drop_last_closed_bar: "
          f"{len(diffs)} of {len(windows)} windows differ (must be > 0)")
    return ok


if __name__ == "__main__":
    if "--controls" in sys.argv:
        sys.exit(0 if controls() else 1)
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as e:
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
                failures += 1
    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)
