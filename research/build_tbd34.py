#!/usr/bin/env python3
"""TBD-3 (spread model, C3.2) and TBD-4 (E2 price tolerance, section 8), built
by the Reviewer-approved rules of contract_amendments/
ec-1.0_TBD3_TBD4_PROPOSAL_v2.txt: S1-S6 v2 and T1-T2.

Reads logs/events.jsonl, the fixed 0a soak copy and a new history export. Writes
research/data_feasibility/spread_model_<stamp>.json and
e2_tolerance_<stamp>.json. Prints n by session, exclusions, alignment and T
only: it runs no strategy, no portfolio and no gate, and reads no signal
result, R, equity or benchmark. Every M1 bar read is after the holdout and
goes through replay.data.guard. The commit that adds both artefacts freezes
TBD-3 and TBD-4.

Usage (VPS, after Sun 22 Nov 2026 23:59:59 UTC, with an export covering it):
    venv/bin/python research/build_tbd34.py --export=latest \
        --soak=research/data_feasibility/soak_0a_20261009.json
(latest = the newest full export under data/history/, never a probe; or give
--export=data/history/<snapshot id>).

S5 fallback dataset, Reviewer ruling Q-S5 A (24 Nov 2026,
contract_amendments/ec-1.0_TBD3_S5_QUESTION_v1.txt): the 0a soak as it was on
30 Sep, not the rotating logs/soak.json. The file given by --soak must have
SOAK_SHA256 (the bytes of the VPS copy logs/soak.20261009.json); only cycles
with a spread and timestamp <= SOAK_CUTOFF are used, and the engine-session
counts must equal SOAK_N (1,341 cycles). File, sha256, cutoff and n are
recorded in the spread artefact. MIN_POINTS and the all-or-nothing rule are
unchanged.

Interpretations, disclosed (none changes an approved rule):
- I1. "The first 0g-1 cycle" (S2, S3(1)) is the first cycle event, in ts
  order, that carries a `quote` object; its ts is printed and recorded.
- I2. A cycle without a bar_time (every CYCLE_ABORTED) is no bar's cycle: it
  is excluded as "aborted" and never takes a bar's first occurrence.
- I3. A first cycle is excluded for the first failing test, in S3(4) order:
  aborted, no_quote, quote_error, stale, suspect_delay, no_delay, alignment,
  points_not_integer, points_below_1. Exclusions are counted by that reason
  and the cycle's logged session; stale ones also by stale flag.
- I4. Account (S6, information only): cycles with ts before 2026-11-16
  08:44:55 UTC, the first "'115681802': authorized" line of the terminal log
  20261116.log, are demo 10013386522; later ones demo 115681802.
- I5. Soak fallback (S5): a soak cycle's timestamp without a zone is UTC, as
  in probe_spread_reconciliation; a spread that is not a whole number of
  points is excluded and counted.
- I6. T2 is computed in exact decimals: |diff| = |Decimal(replay bid) -
  Decimal(live bid)|, the type-1 99th percentile at rank ceil(99 n / 100)
  in integer arithmetic, then rounded up to 0.01.

Module-level functions sequence, points_of, clock_of, exclusion, is_rerun,
in_window, covers, select, sufficient, load_soak, soak_points, soak_matches,
spread_model and quantile_rank are the seams tests/test_tbd34.py patches with
broken variants.
"""
import collections
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import weekly_checkpoint as wc  # noqa: E402
from engine.pipeline import get_session_tag  # noqa: E402
from replay import clock, conformance, data, entry, spread  # noqa: E402

EVENTS = ROOT / "logs" / "events.jsonl"
OUT_DIR = ROOT / "research" / "data_feasibility"
SOAK_SHA256 = "84d11d3f8eb654d61b86acf0678937e93db45e33455067a537297f73e44be07d"
SOAK_CUTOFF = datetime(2026, 9, 30, 7, 6, 58, 683711, tzinfo=timezone.utc)
SOAK_N = {"ASIA": 449, "LONDON": 278, "NY": 502, "OFF": 112}
SOAK_RULING = "Q-S5 A, Reviewer 24 Nov 2026 (contract_amendments/ec-1.0_TBD3_S5_QUESTION_v1.txt)"
RULE3 = "TBD-3 S1-S6 v2"
RULE4 = "TBD-4 T1-T2"
PROPOSAL = "contract_amendments/ec-1.0_TBD3_TBD4_PROPOSAL_v2.txt"
UTC = timezone.utc
CAL_END = datetime(2026, 11, 22, 23, 59, 59, tzinfo=UTC)
FWD_START = datetime(2026, 11, 23, tzinfo=UTC)
MIN_POINTS = {"ASIA": 437, "LONDON": 278, "NY": 447, "OFF": 44}
ACCOUNT_SWITCH = datetime(2026, 11, 16, 8, 44, 55, tzinfo=UTC)
ACCOUNTS = ("10013386522", "115681802")
INCLUDED_EVENTS = ("NO_SIGNAL", "SIGNAL_APPROVED", "SIGNAL_REJECTED")
P99 = (99, 100)
CENT = Decimal("0.01")


def account_of(ts):
    return ACCOUNTS[0] if ts < ACCOUNT_SWITCH else ACCOUNTS[1]


def read_events(path=EVENTS):
    """Parsed lines in file order, and the count of unparsable ones."""
    out, bad = [], 0
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return out, bad


def sequence(events, end):
    """S3(1): cycle events from the first 0g-1 cycle (I1) to ts <= end, in ts
    order with ties in file order. Returns (cycles, first_ts, untimed)."""
    timed, untimed = [], 0
    for e in events:
        if e.get("event") not in wc.CYCLE_EVENTS:
            continue
        t = wc.parse_ts(e)
        if t is None:
            untimed += 1
            continue
        timed.append((t, e))
    timed.sort(key=lambda x: x[0])
    first = next((t for t, e in timed if isinstance(e.get("quote"), dict)), None)
    if first is None:
        return [], None, untimed
    return [e for t, e in timed if first <= t <= end], first, untimed


def points_of(quote, point):
    """S4: exact Decimal points, never rounded; None if not a whole number."""
    p = (Decimal(str(quote["quote_ask"])) - Decimal(str(quote["quote_bid"]))) / Decimal(str(point))
    return int(p) if p == p.to_integral_value() else None


def clock_of(ev):
    """(E, live D - E, session of E) at the baseline delay (S4, E1)."""
    b0 = conformance.bar_open_of(ev)
    e = clock.entry_instant(clock.decision_instant(b0, clock.BASELINE_DELAY))
    d = clock.bar_close(b0) + float(ev["scheduling_delay_seconds"])
    return e, round(d - e, 3), clock.session_of(e)


def exclusion(ev, flags, point):
    """I3: the first failing S3(4)/S4 test, or None with the point."""
    if ev.get("event") not in INCLUDED_EVENTS:
        return "aborted", None
    q = ev.get("quote")
    if not isinstance(q, dict):
        return "no_quote", None
    if q.get("quote_error") is not None:
        return "quote_error", None
    if flags:
        return "stale", None
    if ev.get("scheduling_delay_note"):
        return "suspect_delay", None
    if ev.get("scheduling_delay_seconds") is None:
        return "no_delay", None
    if abs(clock_of(ev)[1]) > conformance.E1_LIMIT:
        return "alignment", None
    p = points_of(q, point)
    if p is None:
        return "points_not_integer", None
    if p < 1:
        return "points_below_1", None
    return None, p


def is_rerun(bar_time, seen):
    """S3(3): a later cycle of a bar already met, whatever either holds."""
    return bar_time in seen


def in_window(ev, window):
    """S3(5): the cycle's ts (UTC log time) decides the window."""
    return window[0] <= wc.parse_ts(ev) <= window[1]


def covers(m1, last_e):
    """The export reaches the last calibration E."""
    return last_e is None or (len(m1) > 0 and int(m1.time[-1]) >= last_e)


def select(cycles, window, point):
    """S3 (2)-(5) on a sequence from sequence(): staleness over the whole
    sequence, the first cycle of each bar_time only, inclusion on it, then
    the window [lo, hi] by ts. Returns points, exclusion counts and reruns."""
    seen, pts = set(), []
    excl, stale_by_flag, reruns = collections.Counter(), collections.Counter(), collections.Counter()
    spread_mismatch = 0
    for ev, flags in wc.stale_flags(cycles):
        bt = ev.get("bar_time")
        if bt is None:
            if in_window(ev, window):
                excl[("aborted", str(ev.get("session")))] += 1
            continue
        if is_rerun(bt, seen):
            if in_window(ev, window):
                reruns[str(ev.get("session"))] += 1
            continue
        seen.add(bt)
        if not in_window(ev, window):
            continue
        ts = wc.parse_ts(ev)
        why, p = exclusion(ev, flags, point)
        if why:
            excl[(why, str(ev.get("session")))] += 1
            if why == "stale":
                stale_by_flag.update(flags)
            continue
        q = ev["quote"]
        sp = q.get("quote_spread_price")
        if sp is None or Decimal(str(sp)) / Decimal(str(point)) != p:
            spread_mismatch += 1
        e, d_minus_e, session = clock_of(ev)
        pts.append({"ts": ev["ts"], "bar_time": bt, "E": e, "bid": q["quote_bid"], "ask": q["quote_ask"],
                    "points": p, "session": session, "logged_session": ev.get("session"),
                    "d_minus_e": d_minus_e, "account": account_of(ts)})
    return {"points": pts, "exclusions": excl, "stale_by_flag": stale_by_flag, "reruns": reruns,
            "spread_mismatch": spread_mismatch}


def by_session(rows, key="points"):
    out = {s: [] for s in spread.SESSIONS}
    for r in rows:
        out[r["session"]].append(r[key])
    return {s: sorted(v) for s, v in out.items()}


def sufficient(n_by_session):
    """S5: every session reaches its fixed minimum."""
    return all(n_by_session.get(s, 0) >= m for s, m in MIN_POINTS.items())


class SoakInvalid(ValueError):
    """The soak file is not the Q-S5 A dataset."""


def load_soak(path, digest=SOAK_SHA256):
    """Q-S5 A: the soak file by path, refused unless its bytes hash to digest."""
    raw = Path(path).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != digest:
        raise SoakInvalid(f"{path}: sha256 {got}, expected {digest}")
    return {"cycles": json.loads(raw).get("cycles", []),
            "source": {"file": Path(path).name, "sha256": got, "ruling": SOAK_RULING}}


def soak_points(soak, cutoff=None):
    """S5 fallback: every soak cycle with a spread and timestamp <= cutoff
    (Q-S5 A), integer points as logged, session = get_session_tag(UTC hour of
    its timestamp) (I5)."""
    cutoff = SOAK_CUTOFF if cutoff is None else cutoff
    pts = {s: [] for s in spread.SESSIONS}
    probe_local, skipped = collections.Counter(), collections.Counter()
    for c in soak.get("cycles", []):
        if c.get("spread") is None:
            continue
        try:
            t = datetime.fromisoformat(str(c["timestamp"]))
        except (KeyError, ValueError):
            skipped["timestamp_unparsable"] += 1
            continue
        t = t if t.tzinfo else t.replace(tzinfo=UTC)
        if t > cutoff:
            skipped["after_cutoff"] += 1
            continue
        p = Decimal(str(c["spread"]))
        if p != p.to_integral_value() or p < 1:
            skipped["spread_not_whole_points"] += 1
            continue
        pts[get_session_tag(t.astimezone(UTC).hour)].append(int(p))
        probe_local[str(c.get("session"))] += 1
    return {s: sorted(v) for s, v in pts.items()}, probe_local, skipped


def spread_model(selected, soak):
    """S5: the 0g-1 points iff sufficient, else the soak for all four sessions."""
    live = by_session(selected["points"])
    n_live = {s: len(v) for s, v in live.items()}
    if sufficient(n_live):
        return spread.SpreadModel(live, source="0g-1 live quotes"), n_live, None
    soak_pts, probe_local, skipped = soak_points(soak)
    info = {"n_by_session_engine": {s: len(v) for s, v in soak_pts.items()},
            "n_by_session_probe_local": dict(probe_local), "skipped": dict(skipped)}
    return spread.SpreadModel(soak_pts, source="soak"), n_live, info


def soak_matches(info, expected):
    """Q-S5 A: the fallback holds exactly the ruled engine-session counts."""
    return info["n_by_session_engine"] == expected


def quantile_rank(n, p=P99):
    """Type-1 rank ceil(p n), 1-based, in integer arithmetic (I6)."""
    return max(1, min(n, -(-p[0] * n // p[1])))


def calibrate(points, m1):
    """T1-T2: |replay bid at E - live quote_bid| over the calibration set; T
    is its type-1 99th percentile rounded up to 0.01."""
    rows, missing = [], collections.Counter()
    for r in points:
        data.guard(r["E"], r["E"] + clock.M1)
        bid = m1.open_at(r["E"])
        if bid is None:
            missing[r["session"]] += 1
            continue
        rows.append((r["bar_time"], bid, r["bid"], r["session"], r["account"]))
    absd = sorted(abs(Decimal(str(rb)) - Decimal(str(lb))) for _, rb, lb, _, _ in rows)
    if not absd:
        return {"T": None, "n": 0, "missing_m1": dict(missing)}
    t = absd[quantile_rank(len(absd)) - 1].quantize(CENT, rounding=ROUND_CEILING)
    t_cents = int(t / CENT)
    blob = json.dumps([[b, rb, lb] for b, rb, lb, _, _ in rows], separators=(",", ":")).encode()
    signed = collections.defaultdict(list)
    for _, rb, lb, s, a in rows:
        d = float(Decimal(str(rb)) - Decimal(str(lb)))
        signed[s].append(d)
        signed[f"account {a}"].append(d)
    exceed = sum(x > t for x in absd)
    return {"T": float(t), "T_cents": t_cents, "n": len(rows), "rows_sha256": hashlib.sha256(blob).hexdigest(),
            "missing_m1": dict(missing),
            "calibration_exceedance": {"count": exceed, "rate": round(exceed / len(rows), 6),
                                       "note": "description only (Q4)"},
            "signed_diff": {k: conformance.summary(v) for k, v in sorted(signed.items())}}


def alignment(points):
    d = [r["d_minus_e"] for r in points]
    return {"d_minus_e": conformance.summary(d),
            "session_mismatch": sum(r["session"] != r["logged_session"] for r in points)}


def account_split(points):
    out = {}
    for a in ACCOUNTS:
        bs = by_session([r for r in points if r["account"] == a])
        out[a] = {s: {"n": len(v), "median": v[(len(v) - 1) // 2] if v else None} for s, v in bs.items()}
    return out


def counts(c):
    out = collections.defaultdict(dict)
    for (why, s), n in sorted(c.items()):
        out[why][s] = n
    return dict(out)


def build(events, soak, snapshot, spec, now=None, soak_n=SOAK_N):
    """Both artefacts as dicts, from already-loaded inputs; soak from
    load_soak, soak_n the counts its fallback must hold."""
    now = now or datetime.now(UTC)
    if now <= CAL_END:
        raise SystemExit(f"the S2 window ends {CAL_END.isoformat()}; run after it")
    cycles, first, untimed = sequence(events, CAL_END)
    if first is None:
        raise SystemExit("no 0g-1 cycle (no event with a quote)")
    sel = select(cycles, (first, CAL_END), spec.point)
    model, n_live, soak_info = spread_model(sel, soak)
    if soak_info is not None and not soak_matches(soak_info, soak_n):
        raise SystemExit(f"soak fallback n {soak_info['n_by_session_engine']}, ruled {soak_n} (Q-S5 A)")
    if not covers(snapshot["m1"], max((r["E"] for r in sel["points"]), default=None)):
        raise SystemExit("the export ends before the last calibration E; export again")
    window = {"first_0g1_cycle_ts": first.isoformat(), "end": CAL_END.isoformat()}
    spread_art = {
        "source": model.source, "window": window, "inclusion_rule": RULE3, "proposal": PROPOSAL,
        "n_by_session": {s: len(v) for s, v in model.by_session.items()},
        "live_n_by_session": n_live, "min_points": MIN_POINTS, "live_sufficient": soak_info is None,
        "soak": soak_info,
        "soak_source": dict(soak.get("source") or {}, cutoff=SOAK_CUTOFF.isoformat(), n_by_session=soak_n),
        "exclusions": counts(sel["exclusions"]), "stale_by_flag": dict(sel["stale_by_flag"]),
        "reruns_discarded": dict(sel["reruns"]), "untimed_cycles": untimed,
        "spread_price_mismatch": sel["spread_mismatch"],
        "alignment": alignment(sel["points"]), "accounts": account_split(sel["points"]),
        "broker_spec_version": spec.version, "by_session": model.by_session, "sha256": model.hash,
    }
    tol = calibrate(sel["points"], snapshot["m1"])
    tol_art = dict({"rule": RULE4, "proposal": PROPOSAL, "window": window, "export": snapshot["identity"],
                    "spread_model_sha256": model.hash}, **tol)
    return spread_art, tol_art


def report(sa, ta):
    print(f"TBD-3 {sa['inclusion_rule']}  window {sa['window']['first_0g1_cycle_ts']} .. {sa['window']['end']}")
    print(f"  live points by session {sa['live_n_by_session']}  minimum {sa['min_points']}"
          f"  -> source: {sa['source']}")
    src = sa["soak_source"]
    print(f"  soak {src.get('file')}  sha256 {src.get('sha256')}  cutoff {src['cutoff']}")
    if sa["soak"]:
        print(f"  soak, engine sessions {sa['soak']['n_by_session_engine']}"
              f"  probe-local {sa['soak']['n_by_session_probe_local']}  skipped {sa['soak']['skipped']}")
    print(f"  model n by session {sa['n_by_session']}  sha256 {sa['sha256']}")
    print(f"  exclusions {sa['exclusions']}")
    print(f"  stale by flag {sa['stale_by_flag']}  reruns discarded {sa['reruns_discarded']}"
          f"  untimed {sa['untimed_cycles']}  spread-price mismatch {sa['spread_price_mismatch']}")
    print(f"  alignment D-E {sa['alignment']['d_minus_e']}  session mismatch {sa['alignment']['session_mismatch']}")
    print(f"TBD-4 {ta['rule']}  export {ta['export']['snapshot_id']}")
    print(f"  T = {ta['T']}  n {ta['n']}  missing M1 {ta['missing_m1']}  rows sha256 {ta.get('rows_sha256')}")


def latest_export(history=ROOT / "data" / "history"):
    """The newest full export (mode "export", with its QC), not a probe."""
    full = [p.parent for p in sorted(history.glob("*/manifest.json"))
            if (m := json.loads(p.read_text())).get("mode") == "export" and m.get("qc")]
    if not full:
        raise SystemExit(f"no full export under {history}")
    return full[-1]


def main(argv):
    exp = next((a.split("=", 1)[1] for a in argv if a.startswith("--export=")), None)
    soak_path = next((a.split("=", 1)[1] for a in argv if a.startswith("--soak=")), None)
    if not exp or not soak_path:
        raise SystemExit("usage: build_tbd34.py --export=latest | --export=data/history/<snapshot id>"
                         " --soak=research/data_feasibility/soak_0a_20261009.json")
    export_dir = latest_export() if exp == "latest" else (Path(exp) if Path(exp).is_absolute() else ROOT / exp)
    print(f"export {export_dir.name}")
    events, bad = read_events()
    if bad:
        raise SystemExit(f"{bad} unparsable lines in {EVENTS}")
    soak = load_soak(Path(soak_path) if Path(soak_path).is_absolute() else ROOT / soak_path)
    snapshot = data.load_snapshot(export_dir)
    sa, ta = build(events, soak, snapshot, entry.BrokerSpec.load())
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for name, art in (("spread_model", sa), ("e2_tolerance", ta)):
        path = OUT_DIR / f"{name}_{stamp}.json"
        path.write_text(json.dumps(art, indent=1, sort_keys=True) + "\n")
        print(f"wrote {path.relative_to(ROOT)}")
    report(sa, ta)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
