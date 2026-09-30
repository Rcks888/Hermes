#!/usr/bin/env python3
"""Priority 0a — historical data feasibility probe.

Reports what M15 XAUUSD history the broker actually serves, plus the symbol
contract specification needed for broker-realistic simulation. No success
threshold, fold structure, trade-count requirement or sealed holdout may be
frozen until this has run, because all of them depend on available span.

Writes machine-readable output to
research/data_feasibility/mt5_m15_history_probe_<timestamp>.json
so the result is auditable and attributable to a code version.

Run on the VPS:
    cd /root/Hermes && source venv/bin/activate && export DISPLAY=:99
    python research/probe_data_feasibility.py
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import mt5_connector
from engine import version

SYMBOL = "XAUUSD"
M15 = 15
# copy_rates_from_pos rejects oversized counts outright with
# (-2, 'Terminal: Invalid params') rather than returning what it has, so a
# single large request cannot discover the ceiling. Step down until one
# succeeds; the first success is the usable depth.
# mt5 is a remote object across the rpyc bridge, so copy_rates_from_pos returns
# a netref. Iterating it pulls one row per network round trip, which makes bulk
# transfer unusable: 200k bars is 200k round trips. Depth is therefore found by
# binary search on bar position using single-row requests, and statistics come
# from a bounded sample.
SAMPLE_BARS = 3_000
POSITION_CEILING = 2 ** 22  # ~4.2M bars, far beyond any broker archive
OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"


def _bar_exists(mt5, pos):
    """Does a bar exist at this position? One row, one round trip."""
    r = mt5.copy_rates_from_pos(SYMBOL, M15, pos, 1)
    return r is not None and len(r) > 0


def find_depth(mt5):
    """Largest valid bar position, via exponential bracket then binary search.

    Roughly 2*log2(depth) round trips -- about 40 for a million bars, versus
    one per bar for a bulk transfer.
    """
    if not _bar_exists(mt5, 1):
        return 0, 0

    lo = 1
    hi = 2
    while hi < POSITION_CEILING and _bar_exists(mt5, hi):
        lo = hi
        hi *= 2
    probes = 0
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        probes += 1
        if _bar_exists(mt5, mid):
            lo = mid
        else:
            hi = mid
    return lo, probes


def classify_span(days):
    years = days / 365.25
    if years >= 5:
        return "TIER_A", "Multiple chronological segments; meaningful sealed holdout; several gold regimes"
    if years >= 2:
        return "TIER_B", "Fewer, broader segments; proportional holdout; reduced long-run robustness claims"
    if years >= 1:
        return "TIER_C", "MT5 history for conformance and recent validation only; acquire external M15 source"
    return "TIER_D", "Insufficient as primary research dataset; external ingestion mandatory"


def main():
    result = {
        "probe_ts": datetime.now(timezone.utc).isoformat(),
        "code_version": version.get_code_version(),
        "symbol": SYMBOL,
        "timeframe": "M15",
        "stats_sample_target": SAMPLE_BARS,
    }

    ok, _ = mt5_connector.ensure_connected()
    result["terminal_connected"] = bool(ok)
    if not ok:
        result["error"] = "MT5 not connected — probe inconclusive, not a negative result"
        _write(result)
        return 1

    mt5 = mt5_connector.mt5

    acct = mt5_connector.get_account_info()
    result["broker_server"] = acct.get("server") if acct else None

    # get_symbol_info() returns wrapper key names, not raw MT5 attribute names.
    info = mt5_connector.get_symbol_info(SYMBOL)
    if info:
        for k in ("digits", "point", "tick_size", "tick_value", "contract_size",
                  "volume_min", "volume_step", "volume_max", "spread", "trade_mode"):
            result[k] = info.get(k)

    print(f"Searching history depth for {SYMBOL} M15 ...")
    depth, probes = find_depth(mt5)
    result["total_bar_count"] = depth
    result["depth_search_probes"] = probes
    result["depth_hit_ceiling"] = depth >= POSITION_CEILING - 1

    if depth == 0:
        result["error"] = f"no bars at position 1: {mt5.last_error()}"
        _write(result)
        return 1
    print(f"  depth = {depth:,} bars ({probes} probes)")

    oldest = mt5.copy_rates_from_pos(SYMBOL, M15, depth, 1)
    newest = mt5.copy_rates_from_pos(SYMBOL, M15, 1, 1)
    first_ts = int(oldest[0][0])
    last_ts = int(newest[0][0])

    # Statistics from a bounded recent sample. Full-history stats would need one
    # round trip per bar, so they are deliberately not attempted here.
    n_sample = min(SAMPLE_BARS, depth)
    print(f"  sampling {n_sample:,} most recent bars for quality stats ...")
    sample = mt5.copy_rates_from_pos(SYMBOL, M15, 1, n_sample)
    rates = [tuple(row) for row in sample]  # single pass, then fully local
    result["sample_bar_count"] = len(rates)
    result["stats_basis"] = (
        f"quality statistics computed on the {len(rates)} most recent bars, "
        f"not the full {depth} bar archive"
    )

    times = [int(r[0]) for r in rates]
    vols = [int(r[5]) for r in rates]

    first = datetime.fromtimestamp(first_ts, tz=timezone.utc)
    last = datetime.fromtimestamp(last_ts, tz=timezone.utc)
    result["earliest_bar"] = first.isoformat()
    result["latest_completed_bar"] = last.isoformat()
    result["note_on_timestamps"] = (
        "Broker timestamps are server-local (MetaQuotes-Demo observed at UTC+3) "
        "but rendered here as UTC. Treat as server time, not true UTC, until "
        "the offset is confirmed and normalised."
    )

    span_days = (last - first).total_seconds() / 86400
    result["calendar_span_days"] = round(span_days, 1)
    result["calendar_span_years"] = round(span_days / 365.25, 2)

    # Forex trades ~5 of 7 days; 96 M15 bars per 24h. Compared against the full
    # archive count, not the sample, since span covers the whole archive.
    result["expected_m15_bars_estimate"] = int(span_days * (5 / 7) * 96)
    result["missing_bar_estimate"] = result["expected_m15_bars_estimate"] - depth

    # Sample-scoped, not archive-wide.
    result["sample_duplicate_timestamps"] = len(times) - len(set(times))
    result["sample_zero_volume_bars"] = sum(1 for v in vols if v == 0)

    gaps = [(times[i + 1] - times[i]) for i in range(len(times) - 1)]
    if gaps:
        largest = max(gaps)
        idx = gaps.index(largest)
        result["largest_gap_hours"] = round(largest / 3600, 1)
        result["largest_gap_starts"] = datetime.fromtimestamp(times[idx], tz=timezone.utc).isoformat()
        # A clean weekend break is ~48-50h; anything materially larger is a hole.
        result["sample_gaps_over_60h"] = sum(1 for g in gaps if g > 60 * 3600)

    tier, guidance = classify_span(span_days)
    result["data_tier"] = tier
    result["validation_guidance"] = guidance

    # Bar field 6 is the broker's recorded spread. If it varies across bars it
    # is a genuine historical spread series, which lifts the execution-data
    # tier from 3 (assumed distributions) to 2 (measured per bar). If it is
    # constant or zero it carries no information and must not be used.
    spreads = sorted(int(r[6]) for r in rates)
    n = len(spreads)
    uniq = len(set(spreads))
    result["bar_spread_distinct_values"] = uniq
    result["bar_spread_all_zero"] = all(s == 0 for s in spreads)
    if n:
        result["bar_spread_min"] = spreads[0]
        result["bar_spread_p50"] = spreads[n // 2]
        result["bar_spread_p95"] = spreads[int(n * 0.95)]
        result["bar_spread_max"] = spreads[-1]

    if uniq > 10 and not result["bar_spread_all_zero"]:
        result["execution_data_tier"] = (
            "TIER_2 — historical per-bar spread series available from the "
            "broker. Use measured spread rather than assumed distributions."
        )
    else:
        result["execution_data_tier"] = (
            "TIER_3 — bar spread field carries no usable variation. Use "
            "mid-price OHLC plus session-conditioned spread assumptions "
            "anchored to live soak percentiles."
        )

    _write(result)
    _summarise(result)
    return 0


def _write(result):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"mt5_m15_history_probe_{stamp}.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nWritten: {path}")


def _summarise(r):
    print("\n" + "=" * 62)
    print("PRIORITY 0a — HISTORICAL DATA FEASIBILITY")
    print("=" * 62)
    print(f"  broker            {r.get('broker_server')}")
    print(f"  code_version      {r.get('code_version')}")
    print(f"  total bars        {r.get('total_bar_count'):,} "
          f"(found in {r.get('depth_search_probes')} probes)")
    print(f"  stats sample      {r.get('sample_bar_count'):,} most recent bars")
    print(f"  earliest bar      {r.get('earliest_bar')}")
    print(f"  latest bar        {r.get('latest_completed_bar')}")
    print(f"  span              {r.get('calendar_span_days')} days "
          f"({r.get('calendar_span_years')} years)")
    print(f"  missing estimate  {r.get('missing_bar_estimate'):,} bars")
    print(f"  dupes (sample)    {r.get('sample_duplicate_timestamps')}")
    print(f"  zero-vol (sample) {r.get('sample_zero_volume_bars'):,}")
    print(f"  largest gap       {r.get('largest_gap_hours')}h "
          f"starting {r.get('largest_gap_starts')}")
    print(f"  gaps >60h (samp)  {r.get('sample_gaps_over_60h')}  (weekends are ~48-50h)")
    print(f"\n  min lot           {r.get('volume_min')}  step {r.get('volume_step')}")
    print(f"  contract size     {r.get('contract_size')}")
    print(f"  tick value        {r.get('tick_value')}  tick size {r.get('tick_size')}")
    print(f"\n  bar spread        p50 {r.get('bar_spread_p50')}  p95 {r.get('bar_spread_p95')}  "
          f"max {r.get('bar_spread_max')}  ({r.get('bar_spread_distinct_values')} distinct)")
    print(f"  {r.get('execution_data_tier')}")
    print(f"\n  DATA TIER         {r.get('data_tier')}")
    print(f"  {r.get('validation_guidance')}")
    print("=" * 62)


if __name__ == "__main__":
    sys.exit(main())