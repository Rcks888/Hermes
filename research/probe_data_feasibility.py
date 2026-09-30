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
REQUEST_LADDER = [200_000, 100_000, 50_000, 20_000, 10_000, 5_000, 1_000]
OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"


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
        "request_ladder_sizes": REQUEST_LADDER,
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

    rates = None
    attempts = []
    for want in REQUEST_LADDER:
        r = mt5.copy_rates_from_pos(SYMBOL, M15, 1, want)
        got = 0 if r is None else len(r)
        attempts.append({"requested": want, "returned": got,
                         "error": None if got else str(mt5.last_error())})
        if got:
            rates = r
            result["successful_request_size"] = want
            break
    result["request_ladder"] = attempts

    if rates is None:
        result["error"] = "no bars returned at any request size"
        _write(result)
        return 1

    times = [int(r[0]) for r in rates]
    vols = [int(r[5]) for r in rates]
    result["returned_bar_count"] = len(times)
    # Fewer bars than the accepted request means history ran out, so this is
    # the true depth. Exactly the request size means the ceiling is the request,
    # not the archive, and a larger ladder step might have returned more.
    result["depth_capped_by_history"] = len(times) < result["successful_request_size"]
    result["depth_may_exceed_probe"] = len(times) == result["successful_request_size"]

    first = datetime.fromtimestamp(times[0], tz=timezone.utc)
    last = datetime.fromtimestamp(times[-1], tz=timezone.utc)
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

    # Forex trades ~5 of 7 days; 96 M15 bars per 24h.
    result["expected_m15_bars_estimate"] = int(span_days * (5 / 7) * 96)
    result["missing_bar_estimate"] = result["expected_m15_bars_estimate"] - len(times)

    result["duplicate_timestamps"] = len(times) - len(set(times))
    result["zero_volume_bars"] = sum(1 for v in vols if v == 0)

    gaps = [(times[i + 1] - times[i]) for i in range(len(times) - 1)]
    if gaps:
        largest = max(gaps)
        idx = gaps.index(largest)
        result["largest_gap_hours"] = round(largest / 3600, 1)
        result["largest_gap_starts"] = datetime.fromtimestamp(times[idx], tz=timezone.utc).isoformat()
        # A clean weekend break is ~48-50h; anything materially larger is a hole.
        result["gaps_over_60h"] = sum(1 for g in gaps if g > 60 * 3600)

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
    print(f"  bars returned     {r.get('returned_bar_count'):,} "
          f"(request size {r.get('successful_request_size'):,})"
          f"{'  (history exhausted — true depth)' if r.get('depth_capped_by_history') else ''}"
          f"{'  (WARNING: hit request ceiling, more may exist)' if r.get('depth_may_exceed_probe') else ''}")
    print(f"  earliest bar      {r.get('earliest_bar')}")
    print(f"  latest bar        {r.get('latest_completed_bar')}")
    print(f"  span              {r.get('calendar_span_days')} days "
          f"({r.get('calendar_span_years')} years)")
    print(f"  missing estimate  {r.get('missing_bar_estimate'):,} bars")
    print(f"  duplicates        {r.get('duplicate_timestamps')}")
    print(f"  zero-volume bars  {r.get('zero_volume_bars'):,}")
    print(f"  largest gap       {r.get('largest_gap_hours')}h "
          f"starting {r.get('largest_gap_starts')}")
    print(f"  gaps over 60h     {r.get('gaps_over_60h')}  (weekends are ~48-50h)")
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