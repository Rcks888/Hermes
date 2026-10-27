#!/usr/bin/env python3
"""Data step: export XAUUSD M15 and M1 history to CSV, hash it, check it.

Contract C6.1-C6.3, TBD-2 (M1 depth, hazard H2). The bars are fetched by
research/export_history_wine.py inside Wine Python, next to the terminal, so
bulk history never crosses the rpyc bridge. This driver holds the MT5 run lock,
connects, starts the worker, then hashes and checks what it wrote.

  --probe     TBD-2 only: one week of M15 and M1 per probe window, under a minute.
  (default)   probe, then the full export from 2022-06-01 to server midnight
              today, then hashing and the checks below.
  --qc DIR    rerun hashing and checks on an existing export directory, no MT5.
  --self-test controls only, no MT5. Every run starts with them and stops on a FAIL.

Checks report counts and timestamps only. The holdout, 2025-09-01 to
2026-09-11, is sealed (PREREGISTRATION.md F6): the export may hash it and run
the C6.3 and calendar checks, nothing else. No price, return or volume
statistic is computed or printed for any span.

CSVs go to data/history/<ts>/ (not committed, about 110 MB). The manifest with
both SHA-256 hashes goes to research/data_feasibility/ and is committed.

Run on the VPS just after a :08/:23/:38/:53 cycle has finished; cron runs that
find the lock held wait 50 s and then skip their cycle:
    cd /root/Hermes && source venv/bin/activate && export DISPLAY=:99
    python research/export_history.py --probe
"""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import export_history_wine as worker  # noqa: E402

OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"
DATA_DIR = PROJECT_ROOT / "data" / "history"
WINE_PYTHON = "C:\\Program Files\\Python312\\python.exe"
WORKER_TIMEOUT = 1200
LOCK_WAIT = 60

HOLDOUT_START = worker.epoch(2025, 9, 1)
HOLDOUT_END = worker.epoch(2026, 9, 12)
MIN_DEV_START = worker.epoch(2023, 9, 1)
COVERED_RATIO = 0.95
LONG_GAP_SECONDS = 1800
MISMATCH_LIST_CAP = 500
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def iso(t):
    return worker.iso(t)


def gap_bucket(missing):
    for limit, name in ((1, "1"), (4, "2-4"), (29, "5-29"), (179, "30-179")):
        if missing <= limit:
            return name
    return "180+"


def _stamp(t):
    g = time.gmtime(t)
    return "%s %02d:%02d" % (WEEKDAYS[g.tm_wday], g.tm_hour, g.tm_min)


class SeriesCheck:
    """Per-file checks on one streamed pass: order, alignment, OHLC sanity, gaps."""

    def __init__(self, step):
        self.step = step
        self.rows = 0
        self.first = None
        self.last = None
        self.parse_errors = 0
        self.duplicates = 0
        self.out_of_order = 0
        self.misaligned = 0
        self.bad_ohlc = 0
        self.nonpositive = 0
        self.gaps = Counter()
        self.long_gaps = Counter()
        self.monthly = Counter()

    def add(self, t, o, h, low, c):
        if self.last is not None:
            if t == self.last:
                self.duplicates += 1
            elif t < self.last:
                self.out_of_order += 1
            else:
                missing = (t - self.last) // self.step - 1
                if missing > 0:
                    self.gaps[gap_bucket(missing)] += 1
                if t - self.last - self.step >= LONG_GAP_SECONDS:
                    self.long_gaps[(_stamp(self.last), _stamp(t))] += 1
        if t % self.step:
            self.misaligned += 1
        if min(o, h, low, c) <= 0:
            self.nonpositive += 1
        if not (low <= min(o, c) and h >= max(o, c)):
            self.bad_ohlc += 1
        if self.first is None:
            self.first = t
        if self.last is None or t > self.last:
            self.last = t
        self.rows += 1
        self.monthly[iso(t)[:7]] += 1

    def ordered(self):
        return self.duplicates == 0 and self.out_of_order == 0

    def summary(self):
        patterns = self.long_gaps.most_common()
        return {
            "rows": self.rows,
            "first": iso(self.first) if self.first is not None else None,
            "last": iso(self.last) if self.last is not None else None,
            "parse_errors": self.parse_errors, "duplicates": self.duplicates,
            "out_of_order": self.out_of_order, "misaligned": self.misaligned,
            "bad_ohlc": self.bad_ohlc, "nonpositive": self.nonpositive,
            "gaps_by_missing_bars": dict(self.gaps),
            "long_gap_patterns_top": [[a, b, n] for (a, b), n in patterns[:25]],
            "long_gap_patterns_rare": [[a, b, n] for (a, b), n in patterns if n <= 2][:200],
            "long_gap_pattern_count": len(patterns),
            "rows_by_month": dict(self.monthly),
        }


def read_csv(path, check):
    """Yield (time, open, high, low, close), feeding each row to check."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            try:
                bar = (int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]))
            except (ValueError, IndexError):
                check.parse_errors += 1
                continue
            check.add(*bar)
            yield bar


class CrossCheck:
    """C6.3 on a merge walk: each M15 bar against its minutes [T, T+900)."""

    def __init__(self):
        self.minutes = Counter()
        self.complete_mismatch = []
        self.complete_mismatch_n = 0
        self.envelope_fail = []
        self.envelope_fail_n = 0
        self.orphan_m1 = 0
        self.orphan_days = Counter()
        self.monthly = {}

    def _month(self, t):
        return self.monthly.setdefault(iso(t)[:7], Counter())

    def orphan(self, t):
        self.orphan_m1 += 1
        self.orphan_days[iso(t)[:10]] += 1

    def bar(self, m15, mins):
        t, o, h, low, c = m15
        n = len(mins)
        mon = self._month(t)
        mon["m15"] += 1
        mon["m1_in_bars"] += n
        self.minutes["15" if n == 15 else ("0" if n == 0 else "1-14")] += 1
        if n == 0:
            mon["no_m1"] += 1
            return
        if n == 15:
            mon["complete"] += 1
            rebuilt = (mins[0][1], max(m[2] for m in mins), min(m[3] for m in mins), mins[-1][4])
            fields = [name for name, a, b in zip(("open", "high", "low", "close"), (o, h, low, c), rebuilt)
                      if a != b]
            if fields:
                self.complete_mismatch_n += 1
                if len(self.complete_mismatch) < MISMATCH_LIST_CAP:
                    self.complete_mismatch.append([iso(t), fields])
            return
        mon["holes"] += 1
        fields = []
        if any(m[2] > h for m in mins):
            fields.append("high")
        if any(m[3] < low for m in mins):
            fields.append("low")
        if mins[0][0] == t and mins[0][1] != o:
            fields.append("open")
        if mins[-1][0] == t + 840 and mins[-1][4] != c:
            fields.append("close")
        if fields:
            self.envelope_fail_n += 1
            if len(self.envelope_fail) < MISMATCH_LIST_CAP:
                self.envelope_fail.append([iso(t), n, fields])

    def summary(self):
        return {
            "m15_bars_by_minutes_present": dict(self.minutes),
            "c63a_complete_mismatches": self.complete_mismatch_n,
            "c63a_list": self.complete_mismatch,
            "c63b_envelope_failures": self.envelope_fail_n,
            "c63b_list": self.envelope_fail,
            "orphan_m1_minutes": self.orphan_m1,
            "orphan_m1_by_day": dict(self.orphan_days),
            "by_month": {k: dict(v) for k, v in sorted(self.monthly.items())},
        }


def cross_walk(m15_iter, m1_iter, cross):
    m1 = next(m1_iter, None)
    for bar in m15_iter:
        end = bar[0] + 900
        mins = []
        while m1 is not None and m1[0] < end:
            if m1[0] < bar[0]:
                cross.orphan(m1[0])
            else:
                mins.append(m1)
            m1 = next(m1_iter, None)
        cross.bar(bar, mins)
    while m1 is not None:
        cross.orphan(m1[0])
        m1 = next(m1_iter, None)


def coverage(m1_first, m1_last, by_month):
    """TBD-2 span and the F3 data-sufficiency test, from counts only."""
    if m1_first is None:
        return {"m1_covered_from": None, "f3": "NOT MET: no M1", "months_below_ratio": []}
    holdout_months = sorted({iso(t)[:7] for t in range(HOLDOUT_START, HOLDOUT_END, 86400)})
    holdout_ok = m1_first <= HOLDOUT_START and m1_last >= HOLDOUT_END - 86400 and all(
        by_month.get(m, {}).get("m1_in_bars", 0) > 0 for m in holdout_months)
    dev_ok = m1_first <= MIN_DEV_START
    low = [m for m, r in sorted(by_month.items())
           if r.get("m15") and r.get("m1_in_bars", 0) < COVERED_RATIO * 15 * r["m15"]]
    ok = dev_ok and holdout_ok
    return {
        "m1_covered_from": iso(m1_first), "m1_last": iso(m1_last),
        "development_24_months": dev_ok, "holdout_covered": holdout_ok,
        "f3": "MET" if ok else "NOT MET: P1 HOLD, undecidable on this data",
        "months_below_ratio": low,
    }


def classify_window(w):
    if not w.get("m15"):
        return "NO_M15"
    if not w.get("m1"):
        return "ABSENT"
    return "COVERED" if w["m1"] >= COVERED_RATIO * 15 * w["m15"] else "PARTIAL"


def probe_verdict(windows):
    classes = [classify_window(w) for w in windows]
    if not classes or classes[0] != "COVERED":
        return "PROBE_INVALID", classes
    informative = [c for c in classes[1:] if c != "NO_M15"]
    if all(c == "COVERED" for c in informative):
        return "M1_AVAILABLE", classes
    return "M1_CAPPED_OR_ABSENT", classes


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run_qc(data_dir):
    m15_path = data_dir / "XAUUSD_M15.csv"
    m1_path = data_dir / "XAUUSD_M1.csv"
    files = {}
    for name, p in (("M15", m15_path), ("M1", m1_path)):
        files[name] = {"file": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)}
    snapshot = hashlib.sha256("".join(
        "%s %s\n" % (files[k]["file"], files[k]["sha256"]) for k in ("M15", "M1")).encode()).hexdigest()
    s15, s1, cross = SeriesCheck(900), SeriesCheck(60), CrossCheck()
    cross_walk(read_csv(m15_path, s15), read_csv(m1_path, s1), cross)
    cs = cross.summary()
    return {
        "files": files, "data_snapshot_hash": snapshot,
        "m15": s15.summary(), "m1": s1.summary(), "cross": cs,
        "cross_valid": s15.ordered() and s1.ordered(),
        "coverage": coverage(s1.first, s1.last, cs["by_month"]),
    }


def print_qc(qc):
    for k in ("M15", "M1"):
        f = qc["files"][k]
        print(f"  {f['file']:16} {f['bytes']:>11} bytes  sha256 {f['sha256']}")
    print(f"  data_snapshot_hash {qc['data_snapshot_hash']}")
    for k in ("m15", "m1"):
        s = qc[k]
        print(f"  {k.upper():4} rows {s['rows']}  {s['first']} .. {s['last']} (server)  "
              f"dup {s['duplicates']}  order {s['out_of_order']}  misaligned {s['misaligned']}  "
              f"bad_ohlc {s['bad_ohlc']}  nonpositive {s['nonpositive']}  parse {s['parse_errors']}")
        print(f"       gaps by missing bars {s['gaps_by_missing_bars']}  "
              f"long-gap patterns {s['long_gap_pattern_count']}")
    c = qc["cross"]
    print(f"  M15 bars by M1 minutes present {c['m15_bars_by_minutes_present']}")
    print(f"  C6.3(a) complete-bar mismatches {c['c63a_complete_mismatches']}  "
          f"C6.3(b) envelope failures {c['c63b_envelope_failures']}  "
          f"orphan M1 minutes {c['orphan_m1_minutes']}  cross-check valid {qc['cross_valid']}")
    v = qc["coverage"]
    print(f"  M1 covered from {v['m1_covered_from']}  F3 {v['f3']}")
    if v["months_below_ratio"]:
        print(f"  months with M1 below {COVERED_RATIO:.0%} of 15 x M15, for review: {v['months_below_ratio']}")


def print_probe(windows):
    verdict, classes = probe_verdict(windows)
    for w, c in zip(windows, classes):
        print(f"  {w['window'][0]} .. {w['window'][1]}  M15 {w.get('m15', 0):>5}  "
              f"M1 {w.get('m1', 0):>6}  first M1 {w.get('m1_first')}  {c}")
    print(f"  TBD-2 probe verdict: {verdict}")
    return verdict


def winpath(p):
    return "Z:" + str(p).replace("/", "\\")


def run_worker(mode, out_dir, terminal_path):
    cmd = ["wine", WINE_PYTHON, winpath(Path(__file__).parent / "export_history_wine.py"),
           "--out-dir", winpath(out_dir), "--mode", mode]
    if terminal_path:
        cmd += ["--path", terminal_path]
    env = os.environ.copy()
    env["DISPLAY"] = ":99"
    with open(out_dir / "worker.log", "w") as log:
        subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=WORKER_TIMEOUT)
    path = out_dir / "worker_result.json"
    if not path.exists():
        return {"ok": False, "error": f"worker wrote no result; see {out_dir / 'worker.log'}"}
    with open(path) as f:
        return json.load(f)


def selftest():
    """Each check is shown to catch a planted defect before it is trusted."""
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    T = worker.epoch(2024, 3, 11) + 3600

    def minutes(t0, n=15, skip=()):
        return [(t0 + 60 * i, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i) for i in range(n) if i not in skip]

    def m15_of(mins, t0):
        return (t0, mins[0][1], max(m[2] for m in mins), min(m[3] for m in mins), mins[-1][4])

    def walk(m15, m1):
        cr = CrossCheck()
        cross_walk(iter(m15), iter(m1), cr)
        return cr

    a, b = minutes(T), minutes(T + 900)
    cr = walk([m15_of(a, T), m15_of(b, T + 900)], a + b)
    check("minute T+900 belongs to the next bar (C1.1)",
          cr.minutes["15"] == 2 and cr.complete_mismatch_n == 0, str(dict(cr.minutes)))
    bad = list(a)
    bad[7] = (bad[7][0], bad[7][1], 200.0, bad[7][3], bad[7][4])
    cr = walk([m15_of(a, T)], bad)
    check("C6.3(a) catches a minute high above the bar", cr.complete_mismatch_n == 1
          and cr.complete_mismatch[0][1] == ["high"], str(cr.complete_mismatch))
    hole = minutes(T, skip=(5,))
    cr = walk([m15_of(a, T)], hole)
    check("C6.3(b) passes a consistent bar with a hole", cr.minutes["1-14"] == 1 and cr.envelope_fail_n == 0)
    low_out = list(hole)
    low_out[2] = (low_out[2][0], low_out[2][1], low_out[2][2], 1.0, low_out[2][4])
    cr = walk([m15_of(a, T)], low_out)
    check("C6.3(b) catches a minute low below the bar", cr.envelope_fail_n == 1
          and cr.envelope_fail[0][2] == ["low"], str(cr.envelope_fail))
    first_missing = minutes(T, skip=(0,))
    cr = walk([m15_of(a, T)], first_missing)
    check("C6.3(b) does not judge the open when minute 0 is missing", cr.envelope_fail_n == 0)
    wrong_open = list(hole)
    wrong_open[0] = (T, 100.7, 101.0, 99.0, 100.5)
    cr = walk([m15_of(a, T)], wrong_open)
    check("C6.3(b) catches a wrong open when minute 0 is present", cr.envelope_fail_n == 1
          and cr.envelope_fail[0][2] == ["open"], str(cr.envelope_fail))
    cr = walk([m15_of(a, T), m15_of(b, T + 900)], b)
    check("M15 bar without minutes counted", cr.minutes["0"] == 1 and cr.minutes["15"] == 1)
    cr = walk([m15_of(b, T + 900)], a + b)
    check("minutes without an M15 bar are orphans", cr.orphan_m1 == 15 and cr.minutes["15"] == 1)

    s = SeriesCheck(60)
    for bar in [(T, 1, 2, 0.5, 1.5), (T, 1, 2, 0.5, 1.5), (T - 60, 1, 2, 0.5, 1.5),
                (T + 90, 1, 2, 0.5, 1.5), (T + 180, 3, 2, 0.5, 1.5), (T + 300, 1, 2, 0.5, 1.5)]:
        s.add(*bar)
    check("series: duplicate, out of order, misaligned, bad OHLC",
          (s.duplicates, s.out_of_order, s.misaligned, s.bad_ohlc) == (1, 1, 1, 1),
          str((s.duplicates, s.out_of_order, s.misaligned, s.bad_ohlc)))
    check("series: one missing minute is a gap of 1", s.gaps.get("1") == 1, str(dict(s.gaps)))
    s = SeriesCheck(60)
    s.add(T, 1, 2, 0.5, 1.5)
    s.add(T + 3600 + 60, 1, 2, 0.5, 1.5)
    check("series: an hour closure is a long-gap pattern", sum(s.long_gaps.values()) == 1
          and s.gaps.get("30-179") == 1, str(dict(s.long_gaps)))

    check("probe: COVERED / PARTIAL / ABSENT / NO_M15",
          [classify_window(w) for w in ({"m15": 10, "m1": 150}, {"m15": 10, "m1": 100},
                                        {"m15": 10, "m1": 0}, {"m15": 0, "m1": 0})]
          == ["COVERED", "PARTIAL", "ABSENT", "NO_M15"])
    cov, absent, none = {"m15": 10, "m1": 150}, {"m15": 10, "m1": 0}, {"m15": 0, "m1": 0}
    check("probe verdicts", (probe_verdict([cov, cov, none])[0], probe_verdict([cov, cov, absent])[0],
                             probe_verdict([absent, cov])[0])
          == ("M1_AVAILABLE", "M1_CAPPED_OR_ABSENT", "PROBE_INVALID"))

    months = {m: {"m15": 1, "m1_in_bars": 15} for m in
              sorted({iso(t)[:7] for t in range(worker.epoch(2022, 6, 1), HOLDOUT_END, 86400 * 20)})}
    check("F3 met from 2022-06-23", coverage(worker.epoch(2022, 6, 23), HOLDOUT_END, months)["f3"] == "MET")
    check("F3 not met from 2024-06-01",
          coverage(worker.epoch(2024, 6, 1), HOLDOUT_END, months)["f3"].startswith("NOT MET"))
    check("F3 not met when M1 ends inside the holdout",
          coverage(worker.epoch(2022, 6, 23), worker.epoch(2026, 7, 1), months)["f3"].startswith("NOT MET"))

    r = worker.month_ranges(worker.epoch(2022, 6, 1), worker.epoch(2022, 8, 15))
    check("month chunks contiguous, end exclusive",
          r == [(worker.epoch(2022, 6, 1), worker.epoch(2022, 7, 1)),
                (worker.epoch(2022, 7, 1), worker.epoch(2022, 8, 1)),
                (worker.epoch(2022, 8, 1), worker.epoch(2022, 8, 15))], str(r))
    line = worker.format_row((T, 4180.89, 4181.5, 4179.0, 4180.1, 12, 30, 0), 2)
    check("CSV row round trip", line == f"{T},4180.89,4181.50,4179.00,4180.10,12,30,0\n", line.strip())
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name, rows in (("M15", [m15_of(a, T), m15_of(b, T + 900)]), ("M1", a + b)):
            with open(d / f"XAUUSD_{name}.csv", "w") as f:
                f.write(worker.CSV_HEADER)
                for r in rows:
                    f.write(worker.format_row(tuple(r) + (1, 30, 0), 2))
        qc = run_qc(d)
        check("end to end: files hashed, read back, both bars complete and equal",
              qc["m15"]["rows"] == 2 and qc["m1"]["rows"] == 30 and qc["cross_valid"]
              and qc["cross"]["m15_bars_by_minutes_present"] == {"15": 2}
              and qc["cross"]["c63a_complete_mismatches"] == 0 and len(qc["data_snapshot_hash"]) == 64)
    return ok


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--probe", action="store_true")
    g.add_argument("--qc", metavar="DIR")
    g.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    print("Self-test:")
    if not selftest():
        print("SELF-TEST FAILED: nothing exported")
        return 1
    if args.self_test:
        print("ALL PASS")
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.qc:
        data_dir = Path(args.qc).resolve()
        print(f"QC of {data_dir}")
        qc = run_qc(data_dir)
        print_qc(qc)
        out = OUT_DIR / f"history_qc_{ts}.json"
        out.write_text(json.dumps({"ts": ts, "data_dir": str(data_dir), "qc": qc}, indent=1))
        print(f"Written: {out}")
        return 0

    from engine import mt5_connector, version
    mode = "probe" if args.probe else "export"
    if not mt5_connector.acquire_run_lock(wait_seconds=LOCK_WAIT):
        print("MT5 lock held by a cron run; start again after it finishes")
        return 1
    ok, _ = mt5_connector.ensure_connected()
    if not ok:
        print("MT5 not connected")
        return 1
    data_dir = DATA_DIR / ts
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Wine worker, mode {mode}, into {data_dir} ...")
    t0 = time.monotonic()
    result = run_worker(mode, data_dir, mt5_connector._load_config().get("path", ""))
    print(f"  worker {'ok' if result.get('ok') else 'FAILED: ' + str(result.get('error'))}"
          f"  {time.monotonic() - t0:.0f} s  terminal {result.get('terminal')}")
    manifest = {"ts": ts, "code_version": version.get_code_version(), "mode": mode,
                "data_dir": str(data_dir), "worker": result}
    if result.get("probe"):
        verdict = print_probe(result["probe"])
        manifest["probe_verdict"] = verdict
        manifest["probe_classes"] = probe_verdict(result["probe"])[1]
    if mode == "export" and result.get("ok"):
        unstable = [c for c in result.get("chunks", []) if not c.get("stable")]
        print(f"  export {result.get('export_start')} .. {result.get('export_end_exclusive')} (server, end exclusive)"
              f"  files {result.get('files')}  unstable chunks {len(unstable)}")
        qc = run_qc(data_dir)
        print_qc(qc)
        manifest["qc"] = qc
    name = "m1_depth_probe" if mode == "probe" else "history_export"
    out = OUT_DIR / f"{name}_{ts}.json"
    out.write_text(json.dumps(manifest, indent=1, default=str))
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    print(f"Written: {out}")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
