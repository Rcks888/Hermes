#!/usr/bin/env python3
"""Priority 0a — does the broker hold XAUUSD history before mid-2022?

The feasibility probe returned 99,999 M15 bars, earliest 2022-06-29. That is
100,000 - 1, and Config/common.ini carries [Charts] MaxBars=100000, so the
depth is a client-side chart cap rather than a measured archive boundary.

On-disk M1 history corroborates the cap rather than an archive edge:

    2022.hcc  11.2 MB   about half a year
    2023.hcc  21.2 MB   full
    2024.hcc  21.4 MB   full
    2025.hcc  21.2 MB   full
    2026.hcc  15.7 MB   partial, to September

2022 is half-sized and starts exactly where the probe's earliest bar sits. The
terminal downloaded enough M1 to build 100,000 M15 bars and stopped. It has
never been asked for more.

Raising MaxBars means editing a UTF-16 config that also holds account
credentials and that the terminal rewrites on exit, with cron able to relaunch
the terminal mid-edit. That is worth doing only if there is more history to
get. copy_rates_range with explicit dates makes MT5 fetch on demand and is
bounded by the archive, not by MaxBars, so it answers the question first and
changes nothing.

A zero-bar year is not proof of absence on its own -- it can also mean the
download did not complete within the request. Re-running is the cheap check:
MT5 caches what it fetched, so a genuine year fills in on a second pass while
a truly absent year stays empty.

Read-only. No configuration is modified.

Run on the VPS:
    cd /root/Hermes && source venv/bin/activate && export DISPLAY=:99
    python research/probe_archive_extent.py
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import mt5_connector
from engine import version

SYMBOL = "XAUUSD"
OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"

# One week per year is enough to establish presence or absence, and keeps the
# bridge traffic trivial. January is avoided: the first days of the year are
# thin and holiday-affected, which would make a real year look empty.
PROBE_YEARS = list(range(2026, 2003, -1))
SAMPLE_MONTH = 3
SAMPLE_DAY = 10
SAMPLE_DAYS = 7

# Established by mt5_m15_history_probe_20260930T065319Z.
KNOWN_EARLIEST = "2022-06-29"

# Control window for validating the call itself. Long enough to span a weekend
# and any single holiday, so an empty result can only mean a broken call.
CONTROL_DAYS = 10


# Argument-passing methods for copy_rates_range, most likely first.
#
# The first version of this probe passed timezone-aware datetime objects and
# every year failed in 0.01s with (-2, 'Invalid arguments') -- including years
# known to hold data. datetime objects do not cross the rpyc bridge as
# something the MT5 C extension will accept. Every call in data_feed.py that
# works passes integers only.
#
# MT5 documents date_from/date_to as accepting either a datetime or a number of
# seconds since 1970-01-01, so integers are both valid and unambiguous here.
METHODS = ("int", "naive_datetime", "aware_datetime")


def _as_args(start_dt, end_dt, method):
    if method == "int":
        return int(start_dt.timestamp()), int(end_dt.timestamp())
    if method == "naive_datetime":
        return start_dt.replace(tzinfo=None), end_dt.replace(tzinfo=None)
    return start_dt, end_dt


def _call_range(mt5, start_dt, end_dt, method):
    """Single copy_rates_range call. Returns (rates_or_None, error, elapsed)."""
    a, b = _as_args(start_dt, end_dt, method)
    t0 = datetime.now(timezone.utc)
    try:
        rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M15, a, b)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}", _since(t0)
    if rates is None:
        try:
            return None, f"none_returned: {mt5.last_error()}", _since(t0)
        except Exception:
            return None, "none_returned", _since(t0)
    return rates, None, _since(t0)


def _since(t0):
    return round((datetime.now(timezone.utc) - t0).total_seconds(), 2)


def detect_method(mt5):
    """Find an argument form that returns bars for a window known to have them.

    Sweeping 23 years with an unvalidated call is how the first run of this
    probe produced 23 identical failures and no information. The control
    window is recent and long enough to span a weekend, so zero bars from it
    means the call is wrong, never that the data is missing.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=CONTROL_DAYS)
    attempts = []
    for method in METHODS:
        rates, error, elapsed = _call_range(mt5, start, end, method)
        n = len(rates) if rates is not None else 0
        attempts.append({"method": method, "bars": n,
                         "error": error, "elapsed_s": elapsed})
        print(f"  control {method:16} {n:>6} bars  {elapsed:>6.2f}s"
              f"{'  ' + error if error else ''}")
        if n > 0:
            return method, attempts
    return None, attempts


def probe_year(mt5, year, method):
    """Request one week of M15 bars in the given year.

    Returns a dict rather than raising. A failed year must not abort the
    sweep, since the interesting result is the boundary between years that
    return data and years that do not.
    """
    row = {"year": year, "bars": 0, "first": None, "last": None,
           "elapsed_s": None, "error": None, "method": method}

    start = datetime(year, SAMPLE_MONTH, SAMPLE_DAY, tzinfo=timezone.utc)
    end = start + timedelta(days=SAMPLE_DAYS)
    row["window"] = [start.date().isoformat(), end.date().isoformat()]

    rates, error, elapsed = _call_range(mt5, start, end, method)
    row["elapsed_s"] = elapsed
    if rates is None:
        row["error"] = error
        return row

    # len() is a single bridge round trip. Iterating rows would be one trip
    # per row, which is what made the first feasibility probe appear to hang.
    n = len(rates)
    row["bars"] = n
    if n:
        row["first"] = datetime.fromtimestamp(int(rates[0][0]), timezone.utc).isoformat()
        row["last"] = datetime.fromtimestamp(int(rates[n - 1][0]), timezone.utc).isoformat()
    return row


def classify(years):
    """Decide whether raising MaxBars can yield more history."""
    with_data = [r for r in years if r["bars"] > 0]
    errored = [r for r in years if r["error"]]

    if not with_data:
        return ("INCONCLUSIVE",
                "No year returned bars, including years known to exist. The "
                "bridge or symbol selection is at fault, not the archive. Do "
                "not touch the config on this result.")

    oldest = min(r["year"] for r in with_data)
    known_year = int(KNOWN_EARLIEST[:4])

    if oldest < known_year:
        return ("MORE_HISTORY_AVAILABLE",
                f"Year {oldest} returned bars, older than the {KNOWN_EARLIEST} "
                f"floor the capped probe reported. The archive extends past "
                f"what the terminal has downloaded, so raising MaxBars will "
                f"deepen it. The config edit is justified.")

    empty_older = [r["year"] for r in years
                   if r["year"] < known_year and r["bars"] == 0 and not r["error"]]
    if empty_older:
        return ("ARCHIVE_STARTS_AT_KNOWN_FLOOR",
                f"Every year before {known_year} returned zero bars with no "
                f"error across {len(empty_older)} probes. The demo server "
                f"appears not to hold XAUUSD before {KNOWN_EARLIEST}, so the "
                f"MaxBars cap and the archive edge coincide. Raising MaxBars "
                f"would gain nothing -- skip the config edit and accept 4.25 "
                f"years as the real depth. Re-run once to confirm, since a "
                f"year that failed to download also reads as zero.")

    return ("UNDETERMINED",
            f"Oldest year with data is {oldest}; {len(errored)} years errored. "
            f"Not enough clean evidence either way.")


def main():
    result = {
        "probe_ts": datetime.now(timezone.utc).isoformat(),
        "code_version": version.get_code_version(),
        "symbol": SYMBOL,
        "timeframe": "M15",
        "known_earliest_from_capped_probe": KNOWN_EARLIEST,
        "max_bars_setting": 100000,
        "sample_window_days": SAMPLE_DAYS,
    }

    ok, _ = mt5_connector.ensure_connected()
    if not ok:
        print("MT5 not connected — inconclusive, not a negative result.")
        return 1
    mt5 = mt5_connector.mt5

    print(f"Validating the call against the last {CONTROL_DAYS} days, "
          f"which must contain bars:")
    method, attempts = detect_method(mt5)
    result["control_attempts"] = attempts
    result["method"] = method

    if method is None:
        result["verdict"] = "PROBE_DEFECT"
        result["verdict_detail"] = (
            "No argument form returned bars for a window known to contain "
            "them, so copy_rates_range could not be called successfully at "
            "all. This says nothing about archive depth. Fix the call before "
            "drawing any conclusion, and do not touch the config.")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = OUT_DIR / f"archive_extent_{stamp}.json"
        out.write_text(json.dumps(result, indent=2))
        print(f"\nWritten: {out}")
        print(f"\n  VERDICT  PROBE_DEFECT\n  {result['verdict_detail']}")
        return 1

    print(f"\n  using method: {method}\n")
    print(f"Sweeping {PROBE_YEARS[-1]}..{PROBE_YEARS[0]}, "
          f"{SAMPLE_DAYS}d sample each. Older years may pause while the "
          f"terminal attempts a download.\n")

    years = []
    for y in PROBE_YEARS:
        row = probe_year(mt5, y, method)
        years.append(row)
        mark = f"{row['bars']:>5} bars" if row["bars"] else "    0 bars"
        note = f"  {row['error']}" if row["error"] else ""
        first = f"  first={row['first'][:10]}" if row["first"] else ""
        print(f"  {y}  {mark}  {row['elapsed_s']:>6.2f}s{first}{note}")
    result["years"] = years

    verdict, detail = classify(years)
    result["verdict"] = verdict
    result["verdict_detail"] = detail

    with_data = [r["year"] for r in years if r["bars"] > 0]
    result["oldest_year_with_data"] = min(with_data) if with_data else None
    result["years_with_data"] = sorted(with_data)
    result["total_bridge_seconds"] = round(
        sum(r["elapsed_s"] or 0 for r in years), 1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"archive_extent_{stamp}.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nWritten: {out}")

    print("\n" + "=" * 66)
    print("ARCHIVE EXTENT")
    print("=" * 66)
    print(f"  years with data    {result['years_with_data']}")
    print(f"  oldest with data   {result['oldest_year_with_data']}")
    print(f"  capped probe floor {KNOWN_EARLIEST}")
    print(f"  bridge time        {result['total_bridge_seconds']}s")
    print(f"\n  VERDICT  {verdict}")
    print(f"  {detail}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
