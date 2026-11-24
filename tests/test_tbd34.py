#!/usr/bin/env python3
"""research/build_tbd34.py: TBD-3 S1-S6 v2 and TBD-4 T1-T2, on constructed
events, soak cycles and M1 bars. No live data is read.

VARIANTS maps each test to the broken variants it must catch; `--controls`
requires each to fail on its own assertion (an AssertionError).
"""
import calendar
import json
import math
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import build_tbd34 as b  # noqa: E402
from engine.pipeline import get_session_tag  # noqa: E402
from replay import conformance, data, entry, spread  # noqa: E402

UTC = timezone.utc
SPEC = entry.BrokerSpec.load()
OFFSET = 7200
NOW = datetime(2026, 11, 24, tzinfo=UTC)
WIDE = (datetime(2026, 10, 1, tzinfo=UTC), b.CAL_END)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return
    raise AssertionError(f"{getattr(fn, '__name__', fn)}{a} did not raise {exc.__name__}")


def server(bar):
    return calendar.timegm(datetime.fromisoformat(bar).timetuple())


def e_of(bar):
    return server(bar) + 900 + 480


def cyc(bar, bid=3000.10, ask=3000.25, delay=484.0, event="NO_SIGNAL", session=None, ts=None,
        tick=None, offset=7199.0, note=None, quote=True, **kw):
    """A cycle on the server bar `bar` ('2026-11-17 13:45'), UTC+2, logged at D + 1 s."""
    d_utc = datetime.fromtimestamp(server(bar) - OFFSET + 900 + delay, UTC)
    t = ts or d_utc + timedelta(seconds=1)
    ev = {"ts": t.isoformat(), "event": event, "session": session or get_session_tag(d_utc.hour),
          "bar_time": f"{bar}:00+00:00", "scheduling_delay_seconds": delay, "scheduling_delay_note": note,
          "server_offset_seconds": offset}
    if quote:
        ev["quote"] = {"quote_bid": bid, "quote_ask": ask, "quote_spread_price": round(ask - bid, 5),
                       "quote_time_msc": tick or int(t.timestamp() * 1000), "quote_error": None}
    ev.update(kw)
    return ev


def aborted(ts, session="NY"):
    return {"ts": ts.isoformat(), "event": "CYCLE_ABORTED", "session": session, "reason": "mt5_auth_failed"}


def ts_of(ev, seconds=0):
    return datetime.fromisoformat(ev["ts"]) + timedelta(seconds=seconds)


def bars(sel):
    return [p["bar_time"] for p in sel["points"]]


def test_1_first_occurrence():
    """S3(3): the first cycle of a bar is its cycle, never replaced by a
    later rerun, even a fresh one, when the first fails."""
    a = cyc("2026-11-17 13:45", offset=7613.0)
    c = cyc("2026-11-17 14:00")
    rerun = cyc("2026-11-17 13:45", ts=ts_of(c, 60))
    sel = b.select([a, c, rerun], WIDE, SPEC.point)
    check(bars(sel) == ["2026-11-17 14:00:00+00:00"], f"points {bars(sel)}")
    check(dict(sel["exclusions"]) == {("stale", "NY"): 1} and dict(sel["reruns"]) == {"NY": 1},
          f"exclusions {dict(sel['exclusions'])} reruns {dict(sel['reruns'])}")
    check(dict(sel["stale_by_flag"]) == {"offset_off_hour": 1}, f"flags {dict(sel['stale_by_flag'])}")


def test_2_sequence_and_staleness():
    """S3(1)-(2): the sequence starts at the first cycle with a quote, is in
    ts order with ties in file order, and staleness is that of the whole
    sequence, not of the window."""
    early = cyc("2026-11-16 13:00", quote=False)
    p = cyc("2026-11-17 13:45", tick=111)
    q = cyc("2026-11-17 14:00", tick=111)
    t1 = cyc("2026-11-17 14:15")
    t2 = dict(cyc("2026-11-17 14:30"), ts=t1["ts"])
    late = cyc("2026-11-25 10:00")
    seq, first, untimed = b.sequence([t2, early, dict(p, ts="bad"), p, q, t1, late], b.CAL_END)
    check(first == ts_of(p) and seq == [p, q, t2, t1] and untimed == 1,
          f"first {first}, sequence {[e['bar_time'] for e in seq]}, untimed {untimed}")
    sel = b.select([p, q], (ts_of(q), b.CAL_END), SPEC.point)
    check(bars(sel) == [] and dict(sel["exclusions"]) == {("stale", "NY"): 1}
          and dict(sel["stale_by_flag"]) == {"tick_unchanged": 1},
          f"q repeats p's tick, p before the window: {bars(sel)} {dict(sel['exclusions'])}")


def test_3_inclusion():
    """S3(4), I2, I3: the first failing test excludes, counted by reason and
    logged session; |live D - E| <= 60 s is inclusive."""
    evs = [cyc("2026-11-17 10:00"),
           cyc("2026-11-17 10:15", note="suspect: 7683 s"),
           cyc("2026-11-17 10:30"),
           cyc("2026-11-17 10:45", quote=False),
           cyc("2026-11-17 11:00", delay=540.0),
           cyc("2026-11-17 11:15", delay=540.5),
           cyc("2026-11-17 11:30", bid=3000.10, ask=3000.10),
           cyc("2026-11-17 11:45", delay=420.0),
           cyc("2026-11-17 12:00")]
    evs[2]["quote"]["quote_error"] = "no tick"
    del evs[-1]["scheduling_delay_seconds"]
    evs.insert(1, aborted(ts_of(evs[0], 300), "LONDON"))
    sel = b.select(evs, WIDE, SPEC.point)
    check(bars(sel) == ["2026-11-17 10:00:00+00:00", "2026-11-17 11:00:00+00:00", "2026-11-17 11:45:00+00:00"],
          f"points {bars(sel)}")
    want = {("aborted", "LONDON"): 1, ("suspect_delay", "LONDON"): 1, ("quote_error", "LONDON"): 1,
            ("no_quote", "LONDON"): 1, ("alignment", "LONDON"): 1, ("points_below_1", "LONDON"): 1,
            ("no_delay", "LONDON"): 1}
    check(dict(sel["exclusions"]) == want, f"exclusions {dict(sel['exclusions'])}")
    check([p["d_minus_e"] for p in sel["points"]] == [4.0, 60.0, -60.0], f"D - E {sel['points']}")


def test_4_points_exact():
    """S4: points from exact decimals, never rounded; a non-integer is
    excluded; a spread price that disagrees is counted."""
    evs = [cyc("2026-11-17 10:00", bid=2650.00, ask=2650.29),
           cyc("2026-11-17 10:15", bid=3572.16, ask=3572.31),
           cyc("2026-11-17 10:30", bid=3000.10, ask=3000.255),
           cyc("2026-11-17 10:45", bid=3000.10, ask=3000.25)]
    evs[3]["quote"]["quote_spread_price"] = 0.16
    sel = b.select(evs, WIDE, SPEC.point)
    check([p["points"] for p in sel["points"]] == [29, 15, 15], f"points {[p['points'] for p in sel['points']]}")
    check(dict(sel["exclusions"]) == {("points_not_integer", "LONDON"): 1}, f"exclusions {dict(sel['exclusions'])}")
    check(sel["spread_mismatch"] == 1, f"spread-price mismatch {sel['spread_mismatch']}")


def test_5_session_and_account():
    """S4, S6, I4: a point's session is that of E, whatever was logged; the
    mismatch is counted; the account follows the ts boundary."""
    evs = [cyc("2026-11-17 03:00"), cyc("2026-11-17 10:00"), cyc("2026-11-17 13:45", session="LONDON"),
           cyc("2026-11-17 23:15"), cyc("2026-11-16 10:30")]
    sel = b.select(evs, WIDE, SPEC.point)
    got = {p["bar_time"][:16]: p["session"] for p in sel["points"]}
    check(got == {"2026-11-17 03:00": "ASIA", "2026-11-17 10:00": "LONDON", "2026-11-17 13:45": "NY",
                  "2026-11-17 23:15": "OFF", "2026-11-16 10:30": "LONDON"}, f"sessions {got}")
    check(b.alignment(sel["points"])["session_mismatch"] == 1, f"alignment {b.alignment(sel['points'])}")
    acc = {p["bar_time"][:16]: p["account"] for p in sel["points"]}
    check(acc["2026-11-16 10:30"] == "115681802" and acc["2026-11-17 03:00"] == "115681802"
          and b.account_of(datetime(2026, 11, 16, 8, 44, 54, tzinfo=UTC)) == "10013386522", f"accounts {acc}")


def test_6_window_by_ts():
    """S3(5): the ts decides the window, not the server bar_time."""
    sun = cyc("2026-11-23 00:15")
    mon = cyc("2026-11-23 02:00")
    sel = b.select([sun, mon], WIDE, SPEC.point)
    check(bars(sel) == ["2026-11-23 00:15:00+00:00"] and ts_of(sun) <= b.CAL_END < ts_of(mon),
          f"calibration points {bars(sel)} (ts {sun['ts']}, {mon['ts']})")
    fwd = b.select([sun, mon], (b.FWD_START, datetime(2026, 12, 1, tzinfo=UTC)), SPEC.point)
    check(bars(fwd) == ["2026-11-23 02:00:00+00:00"], f"forward points {bars(fwd)}")


def _sel(n):
    return {"points": [{"session": s, "points": 15} for s in spread.SESSIONS for _ in range(n[s])]}


SOAK = {"cycles": [{"timestamp": "2026-09-20T12:10:00", "spread": 16, "session": "LONDON"},
                   {"timestamp": "2026-09-20T02:10:00+00:00", "spread": 17.0, "session": "ASIA"},
                   {"timestamp": "2026-09-20T09:10:00", "spread": 15.5, "session": "LONDON"},
                   {"timestamp": "2026-09-20T22:10:00", "spread": None, "session": "OFF"}]}
SOAK_FIX_N = {"ASIA": 1, "LONDON": 0, "NY": 1, "OFF": 0}


def test_7_sufficiency_and_fallback():
    """S5: the live points iff every session reaches its minimum; otherwise
    the soak for all four sessions, reclassified by get_session_tag."""
    model, n_live, info = b.spread_model(_sel(b.MIN_POINTS), SOAK)
    check(model.source == "0g-1 live quotes" and info is None
          and {s: len(v) for s, v in model.by_session.items()} == b.MIN_POINTS, f"at the minimum: {model.source}")
    short = dict(b.MIN_POINTS, OFF=43)
    model, n_live, info = b.spread_model(_sel(short), SOAK)
    check(model.source == "soak" and model.by_session == {"ASIA": [17], "LONDON": [], "NY": [16], "OFF": []},
          f"OFF 43: {model.source} {model.by_session}")
    check(n_live == short and info["n_by_session_probe_local"] == {"LONDON": 1, "ASIA": 1}
          and info["skipped"] == {"spread_not_whole_points": 1}, f"info {info}")


def _m1(rows):
    return data.Bars.from_rows(60, sorted(rows))


def _points(diffs, bar0="2026-11-17 10:00"):
    out, rows = [], []
    for i, d in enumerate(diffs):
        bar = datetime.fromisoformat(bar0) + timedelta(minutes=15 * i)
        e = calendar.timegm(bar.timetuple()) + 1380
        out.append({"bar_time": f"{bar:%Y-%m-%d %H:%M}:00+00:00", "E": e, "bid": 3000.00,
                    "session": "NY", "account": "115681802"})
        if d is not None:
            rows.append((e, round(3000.00 + d, 2), 3001.0, 2999.0, 3000.0))
        rows.append((e - 60, 2999.0, 3001.0, 2999.0, 3000.0))
    return out, rows


def test_8_tolerance():
    """T1-T2, I6: T is the type-1 99th percentile of |diff| at rank
    ceil(99 n / 100), rounded up to 0.01; a cycle with no M1 bar at E is
    counted, not used, never forward-filled; no holdout minute is read."""
    pts, rows = _points([0.05] * 74 + [-0.05] * 74 + [0.30, -0.30] + [None])
    r = b.calibrate(pts, _m1(rows))
    check(r["n"] == 150 and r["missing_m1"] == {"NY": 1}, f"n {r['n']} missing {r['missing_m1']}")
    check(r["T"] == 0.30 and r["T_cents"] == 30 and r["calibration_exceedance"]["count"] == 0,
          f"T {r['T']} ({r['T_cents']}), exceed {r['calibration_exceedance']}")
    pts2, rows2 = _points([0.05] * 74 + [-0.05] * 74 + [0.30, -0.31] + [None])
    r2 = b.calibrate(pts2, _m1(rows2))
    check(r2["T"] == 0.30 and r2["calibration_exceedance"]["count"] == 1 and r2["rows_sha256"] != r["rows_sha256"],
          f"one row changed: T {r2['T']}, exceed {r2['calibration_exceedance']}")
    check(r["signed_diff"]["NY"]["n"] == 150 and r["signed_diff"]["account 115681802"]["n"] == 150,
          f"signed {r['signed_diff']}")
    e = data.HOLDOUT[0] + 3600
    raises(data.HoldoutSealed, b.calibrate, [dict(pts[0], E=e)], _m1([(e, 3000.0, 3000.0, 3000.0, 3000.0)]))


def _snapshot(pts_rows):
    return {"m1": _m1(pts_rows), "identity": {"snapshot_id": "test", "data_snapshot_hash": "0" * 64}}


def test_9_build():
    """The two artefacts: the spread artefact loads as a SpreadModel with its
    recorded sha256; the build refuses before the S2 window ends and on an
    export that ends before the last E."""
    evs = [cyc("2026-11-17 10:00"), cyc("2026-11-17 10:15", bid=3000.00, ask=3000.20),
           aborted(datetime(2026, 11, 17, 9, 0, tzinfo=UTC))]
    rows = [(e_of("2026-11-17 10:00"), 3000.12, 3001.0, 2999.0, 3000.0),
            (e_of("2026-11-17 10:15"), 3000.00, 3001.0, 2999.0, 3000.0)]
    sa, ta = b.build(evs, SOAK, _snapshot(rows), SPEC, now=NOW, soak_n=SOAK_FIX_N)
    check(sa["source"] == "soak" and sa["live_n_by_session"]["LONDON"] == 2
          and sa["exclusions"] == {"aborted": {"NY": 1}}, f"spread artefact {sa['source']} {sa['exclusions']}")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "spread_model_test.json"
        path.write_text(json.dumps(sa))
        m = spread.SpreadModel.load(path)
    check(m.hash == sa["sha256"] and ta["spread_model_sha256"] == sa["sha256"], f"model hash {m.hash}")
    check(ta["T"] == 0.02 and ta["n"] == 2 and ta["export"]["snapshot_id"] == "test", f"tolerance {ta}")
    raises(SystemExit, b.build, evs, SOAK, _snapshot(rows), SPEC, now=datetime(2026, 11, 22, 12, tzinfo=UTC),
           soak_n=SOAK_FIX_N)
    raises(SystemExit, b.build, evs, SOAK, _snapshot(rows[:1]), SPEC, now=NOW, soak_n=SOAK_FIX_N)


def test_10_latest_export():
    """--export=latest is the newest full export with its QC, never a later
    probe or a failed export."""
    with tempfile.TemporaryDirectory() as d:
        for name, m in (("20261027T070105Z", {"mode": "export", "qc": {"ok": 1}}),
                        ("20261123T090000Z", {"mode": "export", "qc": {"ok": 1}}),
                        ("20261123T091500Z", {"mode": "export"}),
                        ("20261123T093000Z", {"mode": "probe", "qc": {"ok": 1}})):
            (Path(d) / name).mkdir()
            (Path(d) / name / "manifest.json").write_text(json.dumps(m))
        got = b.latest_export(Path(d))
        check(got.name == "20261123T090000Z", f"latest export {got.name}")
        raises(SystemExit, b.latest_export, Path(d) / "none")


def test_11_soak_dataset():
    """Q-S5 A: the soak is taken by path and refused unless its bytes have the
    ruled sha256; only cycles to the cutoff (inclusive) count; the build
    refuses a fallback whose engine-session counts differ from the ruled n,
    and records file, sha256, cutoff and n."""
    cut = b.SOAK_CUTOFF
    cycles = SOAK["cycles"] + [{"timestamp": cut.isoformat(), "spread": 30, "session": "LONDON"},
                               {"timestamp": (cut + timedelta(microseconds=1)).isoformat(), "spread": 31,
                                "session": "LONDON"}]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "soak_fix.json"
        path.write_text(json.dumps({"cycles": cycles}))
        digest = b.hashlib.sha256(path.read_bytes()).hexdigest()
        soak = b.load_soak(path, digest=digest)
        raises(b.SoakInvalid, b.load_soak, path)
        path.write_text(json.dumps({"cycles": cycles[:-1]}))
        raises(b.SoakInvalid, b.load_soak, path, digest=digest)
    check(soak["source"]["sha256"] == digest and soak["source"]["file"] == "soak_fix.json",
          f"source {soak['source']}")
    pts, _, skipped = b.soak_points(soak)
    check(pts["LONDON"] == [30] and skipped == {"after_cutoff": 1, "spread_not_whole_points": 1},
          f"cutoff: LONDON {pts['LONDON']} skipped {skipped}")
    evs = [cyc("2026-11-17 10:00")]
    rows = [(e_of("2026-11-17 10:00"), 3000.12, 3001.0, 2999.0, 3000.0)]
    n = dict(SOAK_FIX_N, LONDON=1)
    sa, _ = b.build(evs, soak, _snapshot(rows), SPEC, now=NOW, soak_n=n)
    check(sa["n_by_session"] == n and sa["soak_source"]["sha256"] == digest
          and sa["soak_source"]["cutoff"] == "2026-09-30T07:06:58.683711+00:00"
          and sa["soak_source"]["n_by_session"] == n, f"soak source {sa['soak_source']}")
    raises(SystemExit, b.build, evs, soak, _snapshot(rows), SPEC, now=NOW, soak_n=SOAK_FIX_N)
    check(b.SOAK_N == {"ASIA": 449, "LONDON": 278, "NY": 502, "OFF": 112} and sum(b.SOAK_N.values()) == 1341
          and b.SOAK_SHA256.startswith("84d11d3f8eb6"), "ruled constants")


def _latest_any(history=None):
    return sorted(p.parent for p in history.glob("*/manifest.json"))[-1]


_SELECT, _SEQUENCE, _CLOCK_OF, _EXCLUSION = b.select, b.sequence, b.clock_of, b.exclusion
_SOAK_POINTS, _BARS_OPEN_AT = b.soak_points, data.Bars.open_at


def _select_per_window(cycles, window, point):
    return _SELECT([e for e in cycles if window[0] <= b.wc.parse_ts(e) <= window[1]], window, point)


def _sequence_from_first_event(events, end):
    seq, first, untimed = _SEQUENCE([dict(e, quote=e.get("quote", {})) for e in events], end)
    return [{k: v for k, v in e.items() if not (k == "quote" and v == {})} for e in seq], first, untimed


def _sequence_unstable(events, end):
    seq, first, untimed = _SEQUENCE(events, end)
    return sorted(seq, key=lambda e: (e["ts"], e.get("bar_time") or "")), first, untimed


def _in_window_by_bar(ev, window):
    t = datetime.fromisoformat(ev["bar_time"]) if ev.get("bar_time") else b.wc.parse_ts(ev)
    return window[0] <= t <= window[1]


def _logged_session(ev):
    e, d_minus_e, _ = _CLOCK_OF(ev)
    return e, d_minus_e, ev["session"]


def _soak_probe_local(soak):
    pts = {s: [] for s in spread.SESSIONS}
    for c in soak.get("cycles", []):
        if c.get("spread") is not None and float(c["spread"]).is_integer():
            pts[c["session"]].append(int(c["spread"]))
    return pts, {}, {}


def _spread_model_mixed(selected, soak):
    live = b.by_session(selected["points"])
    soak_pts = _SOAK_POINTS(soak)[0]
    mixed = {s: (live[s] if len(live[s]) >= m else soak_pts[s]) for s, m in b.MIN_POINTS.items()}
    return spread.SpreadModel(mixed, source="mixed"), {s: len(v) for s, v in live.items()}, None


def _load_soak_unchecked(path, digest=None):
    return {"cycles": json.loads(Path(path).read_text()).get("cycles", []),
            "source": {"file": Path(path).name, "sha256": digest}}


def _soak_points_no_cutoff(soak, cutoff=None):
    return _SOAK_POINTS(soak, cutoff=datetime.max.replace(tzinfo=UTC))


def _soak_points_cutoff_exclusive(soak, cutoff=None):
    return _SOAK_POINTS(soak, cutoff=b.SOAK_CUTOFF - timedelta(microseconds=1))


def _open_at_forward_filled(self, t):
    i = int(__import__("numpy").searchsorted(self.time, t, side="right")) - 1
    return None if i < 0 else float(self.open[i])


VARIANTS = {
    "test_1_first_occurrence": [
        ("a rerun counted as its bar's cycle", b, "is_rerun", lambda bt, seen: False)],
    "test_2_sequence_and_staleness": [
        ("staleness of the window only", b, "select", _select_per_window),
        ("sequence from the first event, quote or not", b, "sequence", _sequence_from_first_event),
        ("ties not kept in file order", b, "sequence", _sequence_unstable)],
    "test_3_inclusion": [
        ("suspect delay kept", b, "exclusion", lambda ev, f, p: _EXCLUSION(dict(ev, scheduling_delay_note=None), f, p)),
        ("no alignment limit", conformance, "E1_LIMIT", 10 ** 6),
        ("alignment limit exclusive", conformance, "E1_LIMIT", 59.999)],
    "test_4_points_exact": [
        ("float points truncated", b, "points_of",
         lambda q, point: int((q["quote_ask"] - q["quote_bid"]) / point)),
        ("float points rounded", b, "points_of",
         lambda q, point: round((q["quote_ask"] - q["quote_bid"]) / point))],
    "test_5_session_and_account": [
        ("logged session kept", b, "clock_of", _logged_session)],
    "test_6_window_by_ts": [
        ("window by server bar_time", b, "in_window", _in_window_by_bar)],
    "test_7_sufficiency_and_fallback": [
        ("minimum exclusive", b, "sufficient",
         lambda n: all(n.get(s, 0) > m for s, m in b.MIN_POINTS.items())),
        ("sources mixed per session", b, "spread_model", _spread_model_mixed),
        ("probe-local soak sessions", b, "soak_points", _soak_probe_local)],
    "test_8_tolerance": [
        ("rank rounded, not ceiling", b, "quantile_rank", lambda n, p=b.P99: max(1, round(0.99 * n))),
        ("type-7 style rank (floor)", b, "quantile_rank", lambda n, p=b.P99: max(1, math.floor(0.99 * n))),
        ("missing M1 forward-filled", data.Bars, "open_at", _open_at_forward_filled),
        ("holdout not guarded", data, "guard", lambda *a, **k: None)],
    "test_9_build": [
        ("short export accepted", b, "covers", lambda m1, last_e: True),
        ("run before the window ends", b, "CAL_END", datetime(2026, 11, 1, tzinfo=UTC))],
    "test_10_latest_export": [
        ("newest manifest of any kind", b, "latest_export", _latest_any)],
    "test_11_soak_dataset": [
        ("soak hash not checked", b, "load_soak", _load_soak_unchecked),
        ("soak cutoff not applied", b, "soak_points", _soak_points_no_cutoff),
        ("soak cutoff exclusive", b, "soak_points", _soak_points_cutoff_exclusive),
        ("ruled soak n not checked", b, "soak_matches", lambda info, expected: True),
        ("ruled soak n changed", b, "SOAK_N", {"ASIA": 437, "LONDON": 278, "NY": 447, "OFF": 44})],
}


def run(fn):
    try:
        fn()
        return "PASS", ""
    except Exception as e:  # noqa: BLE001
        return "FAIL", f"{type(e).__name__}: {e}"


def tests():
    return sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))


def main_tests():
    failed = 0
    for name, fn in tests():
        verdict, why = run(fn)
        failed += verdict == "FAIL"
        print(f"{verdict:7s} {name}" + (f": {why}" if why else ""))
    print("\nALL PASS" if failed == 0 else f"\n{failed} FAILED")
    return failed == 0


def main_controls():
    bad = 0
    names = dict(tests())
    for name in sorted(VARIANTS):
        for label, module, attr, broken in VARIANTS[name]:
            with mock.patch.object(module, attr, broken):
                verdict, why = run(names[name])
            if verdict == "FAIL" and why.startswith("AssertionError"):
                print(f"PASS    {name} / {label}: fails as required ({why[:90]})")
            else:
                bad += 1
                print(f"FAIL    {name} / {label}: " + ("broken variant PASSED" if verdict == "PASS"
                                                       else f"failed on {why[:90]}, not its assertion"))
    missing = [n for n, _ in tests() if n not in VARIANTS]
    for n in missing:
        print(f"FAIL    {n}: no broken variant registered")
    bad += len(missing)
    print("\nCONTROLS ALL PASS" if bad == 0 else f"\nCONTROLS: {bad} FAILED")
    return bad == 0


if __name__ == "__main__":
    ok = main_controls() if "--controls" in sys.argv else main_tests()
    sys.exit(0 if ok else 1)
