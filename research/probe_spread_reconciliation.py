#!/usr/bin/env python3
"""Priority 0a follow-up — reconcile bar-recorded spread against live spread.

The feasibility probe found bar-recorded spread at p50 8 / p95 16 points,
while live soak measurement puts London at P50 22 and Asia at P50 41, and a
live reading at probe time returned 39. Both are in points with point=0.01,
so the units agree and the gap is a measurement-basis difference -- the bar
field likely records spread at bar open, or the minimum within the bar,
rather than a time-weighted average.

This matters because max_spread_points is 50. Against bar spread with p95 16
that filter would essentially never fire in replay, while live it sits close
to triggering. Athena would under-cost every trade and over-count eligible
opportunities -- the same optimism that makes public backtests unreliable.

This probe performs the paired comparison: for each soak cycle that recorded
a live spread, find the bar that was current at that instant and compare.
A systematic ratio means bar spread must be calibrated or rejected. Tier 3
with soak-anchored distributions is the more honest choice if it fails.

Run on the VPS:
    cd /root/Hermes && source venv/bin/activate && export DISPLAY=:99
    python research/probe_spread_reconciliation.py
"""

import json
import statistics
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import mt5_connector
from engine import data_feed
from engine import version

SYMBOL = "XAUUSD"
BAR_FETCH = 2500  # ~26 days of M15, enough to cover the soak window
SOAK_FILE = PROJECT_ROOT / "logs" / "soak.json"
OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"


def floor_15m(dt):
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


def main():
    result = {
        "probe_ts": datetime.now(timezone.utc).isoformat(),
        "code_version": version.get_code_version(),
        "symbol": SYMBOL,
    }

    if not SOAK_FILE.exists():
        print(f"No soak data at {SOAK_FILE}")
        return 1
    with open(SOAK_FILE) as f:
        soak = json.load(f)
    cycles = [c for c in soak.get("cycles", []) if c.get("spread") is not None]
    result["soak_cycles_with_spread"] = len(cycles)
    if not cycles:
        print("No soak cycles carry a spread measurement.")
        return 1

    ok, _ = mt5_connector.ensure_connected()
    if not ok:
        print("MT5 not connected — inconclusive, not a negative result.")
        return 1

    offset = mt5_connector.get_server_time_offset()
    result["server_offset_seconds"] = offset
    offset_h = (offset or 0) / 3600.0
    result["server_offset_hours"] = round(offset_h, 2)
    print(f"Server time offset: {offset_h:+.2f}h")

    print(f"Fetching {BAR_FETCH} bars (slow over the bridge, ~1-2 min) ...")
    df = data_feed.get_candles(SYMBOL, count=BAR_FETCH)
    if df is None:
        print("Bar fetch failed.")
        return 1
    print(f"  got {len(df)} bars")

    # data_feed labels server timestamps as UTC. Key on that same convention so
    # both sides of the comparison use one clock.
    bar_spread = {}
    for t, s in zip(df["datetime"], df["spread"]):
        bar_spread[t.to_pydatetime().replace(tzinfo=timezone.utc)] = int(s)

    pairs = []
    unmatched = 0
    for c in cycles:
        try:
            ts = datetime.fromisoformat(c["timestamp"])
        except (ValueError, KeyError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        # Shift the real-UTC soak instant onto the server clock, then floor.
        server_instant = ts + timedelta(seconds=offset or 0)
        key = floor_15m(server_instant)
        if key in bar_spread:
            pairs.append({
                "soak_ts": ts.isoformat(),
                "bar_time": key.isoformat(),
                "session": c.get("session"),
                "live_spread": int(c["spread"]),
                "bar_spread": bar_spread[key],
            })
        else:
            unmatched += 1

    result["matched_pairs"] = len(pairs)
    result["unmatched_cycles"] = unmatched
    if not pairs:
        result["error"] = (
            "no soak cycle matched a bar. Either the soak window predates the "
            "fetched bars, or the server offset is wrong."
        )
        _write(result)
        print(result["error"])
        return 1

    live = [p["live_spread"] for p in pairs]
    bar = [p["bar_spread"] for p in pairs]
    ratios = [l / b for l, b in zip(live, bar) if b > 0]

    result["live_spread_p50"] = statistics.median(live)
    result["bar_spread_p50"] = statistics.median(bar)
    result["live_spread_mean"] = round(statistics.mean(live), 2)
    result["bar_spread_mean"] = round(statistics.mean(bar), 2)
    if ratios:
        result["ratio_live_over_bar_p50"] = round(statistics.median(ratios), 2)
        result["ratio_live_over_bar_mean"] = round(statistics.mean(ratios), 2)
    result["bar_ge_live_count"] = sum(1 for l, b in zip(live, bar) if b >= l)
    result["bar_understates_pct"] = round(
        100 * sum(1 for l, b in zip(live, bar) if b < l) / len(pairs), 1)

    by_session = {}
    for p in pairs:
        s = p["session"] or "UNKNOWN"
        by_session.setdefault(s, {"live": [], "bar": []})
        by_session[s]["live"].append(p["live_spread"])
        by_session[s]["bar"].append(p["bar_spread"])
    result["by_session"] = {
        s: {
            "n": len(v["live"]),
            "live_p50": statistics.median(v["live"]),
            "bar_p50": statistics.median(v["bar"]),
            "ratio_p50": round(statistics.median(v["live"]) / statistics.median(v["bar"]), 2)
            if statistics.median(v["bar"]) > 0 else None,
        }
        for s, v in sorted(by_session.items())
    }

    # Does the max_spread_points filter behave the same on both sources?
    threshold = 50
    result["filter_threshold"] = threshold
    result["would_block_on_live"] = sum(1 for x in live if x > threshold)
    result["would_block_on_bar"] = sum(1 for x in bar if x > threshold)

    r = result.get("ratio_live_over_bar_p50") or 0
    if r >= 1.5:
        result["verdict"] = (
            f"REJECT TIER 2 as-is. Bar spread understates live by {r}x at the "
            "median. Use Tier 3 with soak-anchored session distributions, or "
            "apply a calibration factor with the residual documented."
        )
    elif r <= 0.8:
        result["verdict"] = (
            f"Bar spread OVERSTATES live by {1/r:.2f}x. Conservative for "
            "costing, but reconcile before use."
        )
    else:
        result["verdict"] = (
            "Bar spread agrees with live within 20%. TIER 2 is defensible; "
            "record the residual in the preregistered cost model."
        )

    _write(result)
    _summarise(result)
    return 0


def _write(result):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"spread_reconciliation_{stamp}.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nWritten: {path}")


def _summarise(r):
    print("\n" + "=" * 66)
    print("SPREAD RECONCILIATION — bar-recorded vs live-measured")
    print("=" * 66)
    print(f"  matched pairs     {r.get('matched_pairs')}  "
          f"(unmatched {r.get('unmatched_cycles')})")
    print(f"  server offset     {r.get('server_offset_hours'):+.2f}h")
    print(f"\n  live  p50 {r.get('live_spread_p50')}   mean {r.get('live_spread_mean')}")
    print(f"  bar   p50 {r.get('bar_spread_p50')}   mean {r.get('bar_spread_mean')}")
    print(f"  ratio live/bar    p50 {r.get('ratio_live_over_bar_p50')}x   "
          f"mean {r.get('ratio_live_over_bar_mean')}x")
    print(f"  bar understates   {r.get('bar_understates_pct')}% of paired bars")
    print(f"\n  by session:")
    for s, v in (r.get("by_session") or {}).items():
        print(f"    {s:<8} n={v['n']:<5} live_p50={v['live_p50']:<5} "
              f"bar_p50={v['bar_p50']:<5} ratio={v['ratio_p50']}x")
    print(f"\n  filter at {r.get('filter_threshold')} points would block:")
    print(f"    on live spread  {r.get('would_block_on_live')} bars")
    print(f"    on bar spread   {r.get('would_block_on_bar')} bars")
    print(f"\n  VERDICT")
    print(f"  {r.get('verdict')}")
    print("=" * 66)


if __name__ == "__main__":
    sys.exit(main())