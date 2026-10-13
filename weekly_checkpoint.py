#!/usr/bin/env python3
"""Weekly checkpoint for STATUS.md. Read-only: reads logs/events.jsonl only.

Usage:
    python weekly_checkpoint.py                    # current week, Mon 00:00 UTC
    python weekly_checkpoint.py --week=2026-10-05  # the week starting that Monday

Every cron run of run_hermes.py logs exactly one cycle event (NO_SIGNAL,
SIGNAL_APPROVED, SIGNAL_REJECTED or CYCLE_ABORTED). Cron fires four times an
hour on weekdays, so a full week is 480 cycles; fewer means missed runs.
Signal outcomes need MT5 and come from research/probe_signal_outcome.py.
"""
import collections
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

EVENTS = Path(__file__).resolve().parent / "logs" / "events.jsonl"
CYCLE_EVENTS = ("NO_SIGNAL", "SIGNAL_APPROVED", "SIGNAL_REJECTED", "CYCLE_ABORTED")
EXPECTED_CYCLES = 4 * 24 * 5
TRADEABLE = ("LONDON", "NY")


def week_bounds(argv):
    start = None
    for a in argv:
        if a.startswith("--week="):
            start = datetime.fromisoformat(a.split("=", 1)[1]).replace(tzinfo=timezone.utc)
    if start is None:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=today.weekday())
    if start.weekday() != 0:
        raise SystemExit(f"--week must be a Monday, got {start.date()} ({start:%A})")
    return start, start + timedelta(days=7)


def parse_ts(e):
    try:
        return datetime.fromisoformat(str(e.get("ts")))
    except ValueError:
        return None


def quantile(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(p * (len(sorted_vals) - 1) + 0.5))]


def main():
    start, end = week_bounds(sys.argv[1:])
    rows, bad = [], 0
    with open(EVENTS) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            t = parse_ts(e)
            if t is not None and start <= t < end:
                rows.append(e)
    cycles = [e for e in rows if e.get("event") in CYCLE_EVENTS]
    print(f"Week {start.date()} to {(end - timedelta(days=1)).date()}  (UTC, Mon-Sun)")
    print(f"code versions: {sorted({str(e.get('code_version')) for e in cycles})}")
    print(f"cycles {len(cycles)} of {EXPECTED_CYCLES} expected"
          f"  ({100 * len(cycles) / EXPECTED_CYCLES:.1f}%)  unparsable lines {bad}")
    print("by event:", dict(collections.Counter(e.get("event") for e in rows)))
    aborted = collections.Counter(str(e.get("reason")) for e in cycles
                                  if e.get("event") == "CYCLE_ABORTED")
    if aborted:
        print("aborted:", dict(aborted))
    print("\nSignals")
    sigs = [e for e in cycles if e.get("event") in ("SIGNAL_APPROVED", "SIGNAL_REJECTED")]
    if not sigs:
        print("  none")
    for e in sigs:
        ex = e.get("exec_observation") or {}
        print(f"  {e.get('event')}  {e.get('bar_time')}  {e.get('direction')}  {e.get('session')}"
              f"  entry {e.get('entry')}  sl {e.get('sl')}  tp {e.get('tp')}"
              f"  lots {e.get('position_size')}  delay {e.get('scheduling_delay_seconds')}s"
              f"  exec {ex.get('execution_validity_status')}"
              f"  reason {e.get('reason')}")
    print("\nFunnel, tradeable sessions (LONDON + NY)")
    tr = [e for e in cycles if e.get("session") in TRADEABLE and e.get("event") != "CYCLE_ABORTED"]
    funnel = collections.Counter(str(e.get("blocked_at", "-")).split(" (")[0]
                                 for e in tr if e.get("event") == "NO_SIGNAL")
    for gate, n in funnel.most_common():
        print(f"  {n:5d}  {gate}")
    n_sig = sum(1 for e in tr if e.get("event") != "NO_SIGNAL")
    print(f"  {n_sig:5d}  reached risk gate (signal)")
    print(f"  total {sum(funnel.values()) + n_sig} = tradeable evaluated cycles {len(tr)}")
    print("\n0g-1 accumulation")
    delays = sorted(e["scheduling_delay_seconds"] for e in cycles
                    if isinstance(e.get("scheduling_delay_seconds"), (int, float)))
    if delays:
        print(f"  scheduling delay n={len(delays)}  p50 {quantile(delays, .5):.1f}"
              f"  p95 {quantile(delays, .95):.1f}  max {delays[-1]:.1f} s")
    else:
        print("  scheduling delay: none recorded")
    notes = collections.Counter(e.get("scheduling_delay_note") for e in cycles
                                if e.get("scheduling_delay_note"))
    print(f"  suspect delay notes: {dict(notes) if notes else 'none'}")
    offsets = collections.Counter(round(e["server_offset_seconds"] / 3600.0, 1) for e in cycles
                                  if isinstance(e.get("server_offset_seconds"), (int, float)))
    print(f"  server offset, hours (EXECUTION_CONTRACT H1, watch for a DST change): "
          f"{dict(sorted(offsets.items())) if offsets else 'none recorded'}")
    quotes = [e["quote"] for e in cycles if isinstance(e.get("quote"), dict)]
    ok = [q for q in quotes if not q.get("quote_error")]
    lat = sorted(q["quote_latency_ms"] for q in ok
                 if isinstance(q.get("quote_latency_ms"), (int, float)))
    print(f"  quotes captured {len(ok)} ok / {len(quotes)} attempted / {len(cycles)} cycles"
          + (f"  latency p50 {quantile(lat, .5):.1f} ms  max {lat[-1]:.1f} ms" if lat else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
