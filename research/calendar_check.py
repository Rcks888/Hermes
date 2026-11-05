#!/usr/bin/env python3
"""TBD-1 and TBD-5 (calendar part) from an exported history snapshot.

Reads only the timestamp column of XAUUSD_M15.csv and XAUUSD_M1.csv. No price,
return or volume is read, so the holdout (PREREGISTRATION.md F6) is touched
only by the calendar check its rule permits.

Calendar rule `calendar-1` (proposed ec-0.3, C1.8). Between two consecutive
observed M1 minutes a < b, the minutes [a + 60, b) are missing. They are a
CLOSURE iff they contain at least CLOSURE_MIN_SLOTS consecutive whole M15
slots that are also absent from the M15 export; otherwise every one of them
is an expected minute that is missing, a HOLE. A missing M15 bar alone is a
hole, not a closure, so contract C5.8(a) keeps its meaning.

Offset rule `offset-eu-1` (proposed ec-0.3, TBD-1): server = UTC+3 while EU
summer time is in force, UTC+2 otherwise. It is tested against the reopen
after each closure, on the assumption seen live in Oct-Nov 2026 that the
market reopens at 18:00 New York: predicted reopen hour, server clock, is 00
in weeks with the US on summer time and the EU not, and 01 otherwise. Under
the alternative (server follows US DST) it would be 01 in every week, so only
those weeks discriminate. Mismatches are listed, never corrected.

  python research/calendar_check.py data/history/<ts>   # writes calendar_check_<ts>.json
  python research/calendar_check.py --self-test
"""

import argparse
import csv
import json
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import export_history as eh  # noqa: E402

CALENDAR_RULE = "calendar-1"
OFFSET_RULE = "offset-eu-1"
CLOSURE_MIN_SLOTS = 2
DAILY_MAX_SECONDS = 6 * 3600
DAILY_START_HOURS = (21, 22, 23, 0)
LIST_CAP = 300
WEEKDAYS = eh.WEEKDAYS


def last_sunday(y, m):
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def nth_sunday(y, m, n):
    d = date(y, m, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))


def eu_summer(d):
    return last_sunday(d.year, 3) <= d < last_sunday(d.year, 10)


def us_summer(d):
    return nth_sunday(d.year, 3, 2) <= d < nth_sunday(d.year, 11, 1)


def regime(d):
    eu, us = eu_summer(d), us_summer(d)
    if eu and us:
        return "both_summer"
    if not eu and not us:
        return "both_winter"
    return "gap_us_summer_eu_winter" if us else "gap_eu_summer_us_winter"


def offset_hours(d):
    """offset-eu-1: hours to subtract from server time to get UTC."""
    return 3 if eu_summer(d) else 2


def predicted_reopen_hour(d):
    return ((22 if us_summer(d) else 23) + offset_hours(d)) % 24


def server_date(t):
    return datetime.fromtimestamp(t, timezone.utc).date()


def hhmm(t):
    g = time.gmtime(t)
    return "%02d:%02d" % (g.tm_hour, g.tm_min)


def stamp(t):
    g = time.gmtime(t)
    return "%s %s %s" % (eh.iso(t)[:10], WEEKDAYS[g.tm_wday], hhmm(t))


def absent_run(a, b, m15):
    """Longest run of consecutive whole M15 slots inside [a, b) absent from m15."""
    best = run = 0
    t = -(-a // 900) * 900
    while t + 900 <= b:
        run = run + 1 if t not in m15 else 0
        best = max(best, run)
        t += 900
    return best


def is_closure(a, b, m15, m15_aware=True):
    """Missing minutes [a, b): closure or hole. m15_aware=False is the broken
    variant that ignores the M15 export and calls any 30-minute run a closure."""
    if not m15_aware:
        return b - a >= CLOSURE_MIN_SLOTS * 900
    return absent_run(a, b, m15) >= CLOSURE_MIN_SLOTS


def spans_saturday(a, b):
    d, end = server_date(a), server_date(b - 1)
    while d <= end:
        if d.weekday() == 5:
            return True
        d += timedelta(days=1)
    return False


class Calendar:
    def __init__(self, m15, m15_aware=True):
        self.m15 = m15
        self.m15_aware = m15_aware
        self.last = None
        self.first = None
        self.minutes_by_slot = Counter()
        self.hole_minutes_by_slot = Counter()
        self.closures = []
        self.hole_runs = Counter()
        self.hole_minutes = 0
        self.hole_minutes_holdout = 0

    def add(self, t):
        self.minutes_by_slot[t - t % 900] += 1
        if self.first is None:
            self.first = t
        if self.last is not None and t - self.last > 60:
            a, b = self.last + 60, t
            if is_closure(a, b, self.m15, self.m15_aware):
                self.closures.append((a, b))
            else:
                n = (b - a) // 60
                self.hole_minutes += n
                self.hole_runs[eh.gap_bucket(n)] += 1
                for m in range(a, b, 60):
                    self.hole_minutes_by_slot[m - m % 900] += 1
                    if eh.HOLDOUT_START <= m < eh.HOLDOUT_END:
                        self.hole_minutes_holdout += 1
        self.last = t

    def classify(self, a, b):
        if spans_saturday(a, b):
            return "weekend"
        if b - a > DAILY_MAX_SECONDS:
            return "other"
        return "daily" if time.gmtime(a).tm_hour in DAILY_START_HOURS else "intraday"

    def reopens(self):
        """[(a, b, class, regime, predicted, observed, match)] for every closure."""
        out = []
        for a, b in self.closures:
            d = server_date(b)
            pred = predicted_reopen_hour(d)
            obs = time.gmtime(b).tm_hour
            out.append((a, b, self.classify(a, b), regime(d), pred, obs, obs == pred))
        return out

    def summary(self):
        rows = self.reopens()
        by_class = Counter(r[2] for r in rows)
        tally = {}
        for _, _, cls, reg, _, _, ok in rows:
            if cls in ("other", "intraday"):
                continue
            k = tally.setdefault(reg, {"match": 0, "mismatch": 0})
            k["match" if ok else "mismatch"] += 1
        mismatches = [[stamp(a), stamp(b), cls, reg, "%02d" % p, "%02d" % o]
                      for a, b, cls, reg, p, o, ok in rows if not ok and cls in ("daily", "weekend")]
        gap_weeks = {}
        for a, b, cls, reg, p, o, ok in rows:
            if reg.startswith("gap") and cls in ("daily", "weekend"):
                wk = (server_date(b) - timedelta(days=server_date(b).weekday())).isoformat()
                w = gap_weeks.setdefault(wk, {"match": 0, "mismatch": 0, "reopens": []})
                w["match" if ok else "mismatch"] += 1
                w["reopens"].append(stamp(b))
        starts = {}
        for a, b, cls, reg, _, _, _ in rows:
            if cls == "daily":
                starts.setdefault(reg, Counter())[hhmm(a)] += 1
        others = [[stamp(a), stamp(b), cls, round((b - a) / 3600, 2)] for a, b, cls, *_ in rows if cls in ("other", "intraday")]
        partial = [s for s in self.m15 if self.minutes_by_slot.get(s, 0) < 15]
        partial_hole = sum(1 for s in partial if self.hole_minutes_by_slot.get(s))
        return {
            "calendar_rule": CALENDAR_RULE, "offset_rule": OFFSET_RULE,
            "closure_min_slots": CLOSURE_MIN_SLOTS, "daily_max_hours": DAILY_MAX_SECONDS / 3600,
            "m1_first": eh.iso(self.first) if self.first else None,
            "m1_last": eh.iso(self.last) if self.last else None,
            "closures_by_class": dict(by_class),
            "reopen_vs_offset_rule_by_regime": tally,
            "reopen_mismatches": mismatches[:LIST_CAP], "reopen_mismatch_count": len(mismatches),
            "gap_weeks": gap_weeks,
            "daily_closure_start_server_by_regime": {k: dict(v.most_common()) for k, v in starts.items()},
            "other_closures": others[:LIST_CAP], "other_closure_count": len(others),
            "holes": {
                "hole_minutes": self.hole_minutes,
                "hole_minutes_in_holdout_count_only": self.hole_minutes_holdout,
                "hole_runs_by_length": dict(self.hole_runs),
                "m15_bars_with_hole_minutes": len(self.hole_minutes_by_slot),
                "m15_bars_missing_inside_scheduled_time": sum(
                    1 for s in self.hole_minutes_by_slot if s not in self.m15),
            },
            "partial_m15_bars": len(partial),
            "partial_m15_bars_with_hole": partial_hole,
            "partial_m15_bars_closure_only": len(partial) - partial_hole,
        }


def read_times(path):
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            yield int(row[0])


def run(data_dir):
    m15 = set(read_times(data_dir / "XAUUSD_M15.csv"))
    cal = Calendar(m15)
    for t in read_times(data_dir / "XAUUSD_M1.csv"):
        cal.add(t)
    return cal.summary()


def verify_hashes(data_dir):
    """SHA-256 of both CSVs against the snapshot manifest, if one is present."""
    man = data_dir / "manifest.json"
    if not man.exists():
        return {"manifest": None}
    files = json.loads(man.read_text()).get("qc", {}).get("files", {})
    out = {}
    for k, v in files.items():
        got = eh.sha256(data_dir / v["file"])
        out[k] = {"file": v["file"], "expected": v["sha256"], "match": got == v["sha256"]}
    return out


def selftest():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    check("EU 2026: summer to 25 Oct, winter from 26 Oct",
          last_sunday(2026, 3) == date(2026, 3, 29) and last_sunday(2026, 10) == date(2026, 10, 25)
          and eu_summer(date(2026, 10, 23)) and not eu_summer(date(2026, 10, 26)))
    check("US 2026: summer 8 Mar to 1 Nov",
          nth_sunday(2026, 3, 2) == date(2026, 3, 8) and nth_sunday(2026, 11, 1) == date(2026, 11, 1)
          and us_summer(date(2026, 10, 30)) and not us_summer(date(2026, 11, 2)))
    live = {date(2026, 10, 23): 3, date(2026, 10, 27): 2, date(2026, 11, 5): 2}
    check("offset-eu-1 reproduces the live offsets (23 Oct +3, 27 Oct +2, 5 Nov +2)",
          all(offset_hours(d) == h for d, h in live.items()))
    check("reopen hour live: 22:00 UTC in the gap week is server 00, 23:00 UTC after 1 Nov is server 01",
          predicted_reopen_hour(date(2026, 10, 27)) == 0 and predicted_reopen_hour(date(2026, 11, 3)) == 1
          and predicted_reopen_hour(date(2026, 8, 4)) == 1)

    day = eh.worker.epoch(2024, 3, 12)
    m15 = {day + s for s in range(0, 23 * 3600, 900) if not (9 * 3600 <= s < 9 * 3600 + 900)}
    m15 |= {day + 86400 + s for s in range(3600, 6 * 3600, 900)}

    def series(skip, m15_aware=True):
        cal = Calendar(m15, m15_aware)
        for t in range(day, day + 86400 + 6 * 3600, 60):
            if not any(a <= t < b for a, b in skip):
                cal.add(t)
        return cal

    daily = (day + 23 * 3600, day + 86400 + 3600 + 300)
    cal = series([daily])
    check("daily break 23:00-01:05 with M15 absent is one closure, class daily",
          [c[2] for c in cal.reopens()] == ["daily"] and cal.hole_minutes == 0)
    check("reopen minutes 01:00-01:04 belong to the closure, so the 01:00 bar is partial by closure only",
          cal.summary()["partial_m15_bars_closure_only"] == 1 and cal.summary()["partial_m15_bars_with_hole"] == 0)
    hole30 = (day + 3 * 3600, day + 3 * 3600 + 1800)
    good, bad = series([daily, hole30]), series([daily, hole30], m15_aware=False)
    check("30 missing minutes with M15 present are a hole; broken variant (ignores M15) calls it a closure",
          good.hole_minutes == 30 and len(good.closures) == 1 and len(bad.closures) == 2)
    one = (day + 9 * 3600, day + 9 * 3600 + 900)
    cal = series([daily, one])
    check("one missing M15 bar with its minutes is a hole (C5.8(a)), not a closure",
          cal.hole_minutes == 15 and cal.summary()["holes"]["m15_bars_missing_inside_scheduled_time"] == 1)
    r = cal.reopens()[0]
    check("2024-03-13 is a gap week: predicted 00, observed 01 is listed as a mismatch",
          r[3] == "gap_us_summer_eu_winter" and r[4] == 0 and r[5] == 1 and not r[6])
    fri = eh.worker.epoch(2024, 3, 15)
    check("a closure spanning Saturday is weekend; 30 h on a weekday is other",
          Calendar(set()).classify(fri + 23 * 3600, fri + 3 * 86400 + 3600) == "weekend"
          and Calendar(set()).classify(day, day + 30 * 3600) == "other")
    check("a 3 h weekday closure from 04:03 server is intraday, not the daily break; 23:59 is daily",
          Calendar(set()).classify(day + 4 * 3600 + 180, day + 7 * 3600 + 180) == "intraday"
          and Calendar(set()).classify(day + 23 * 3600 + 3540, day + 25 * 3600) == "daily")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", nargs="?")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    print("Self-test:")
    if not selftest():
        print("SELF-TEST FAILED")
        return 1
    if args.self_test or not args.data_dir:
        print("ALL PASS")
        return 0
    data_dir = Path(args.data_dir).resolve()
    hashes = verify_hashes(data_dir)
    print(f"hashes vs manifest: {hashes}")
    if any(not v.get("match", True) for v in hashes.values() if isinstance(v, dict)):
        print("HASH MISMATCH: snapshot is not the one in the manifest; nothing checked")
        return 2
    s = run(data_dir)
    print(f"M1 {s['m1_first']} .. {s['m1_last']} (server)  closures {s['closures_by_class']}")
    print(f"reopen vs {OFFSET_RULE}, daily and weekend closures: {s['reopen_vs_offset_rule_by_regime']}")
    for wk, w in sorted(s["gap_weeks"].items()):
        print(f"  gap week {wk}: match {w['match']} mismatch {w['mismatch']}  {w['reopens'][:3]}")
    print(f"mismatches {s['reopen_mismatch_count']}, first: {s['reopen_mismatches'][:8]}")
    print(f"daily closure start, server: {s['daily_closure_start_server_by_regime']}")
    print(f"other closures {s['other_closure_count']}: {s['other_closures'][:12]}")
    print(f"holes {s['holes']}")
    print(f"partial M15 bars {s['partial_m15_bars']}: with hole {s['partial_m15_bars_with_hole']}, "
          f"closure only {s['partial_m15_bars_closure_only']}")
    ts = data_dir.name
    out = eh.OUT_DIR / f"calendar_check_{ts}.json"
    out.write_text(json.dumps({"snapshot": ts, "hashes": hashes, "summary": s}, indent=1))
    print(f"Written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
