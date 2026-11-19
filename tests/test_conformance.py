#!/usr/bin/env python3
"""EXECUTION_CONTRACT.md section 8: Level E (E1-E4), Levels 1-3 event parity
and the NOT CONFORMANT rule, on the signal fixture and constructed inputs.

E0 is contract test 23 in test_replay_contract.py. VARIANTS maps each test to
the broken variants it must catch; `--controls` requires each to fail. No
replay result on historical data is produced.
"""
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import conformance, entry, spread  # noqa: E402

PARAMS = json.loads((ROOT / "config" / "strategy_params.json").read_text())
SPEC = entry.BrokerSpec.load()
EVENTS = [json.loads(x) for x in (ROOT / "tests" / "fixtures" / "live_signal_events.jsonl").read_text().splitlines()
          if x.strip()]
OCT7 = next(e for e in EVENTS if e["bar_time"] == "2026-10-07 17:30:00+00:00")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_e1_decision_instant():
    rep = conformance.e1(EVENTS)
    check(len(rep["rows"]) == len(EVENTS) - 1 and rep["pass"], f"fixture E1 {rep}")
    row = next(r for r in rep["rows"] if r["bar_time"] == OCT7["bar_time"])
    check(row["diff"] == -4.978, f"7 Oct E1 diff {row['diff']}")
    for delay, ok in ((540.0, True), (540.5, False), (420.0, True), (419.9, False)):
        r = conformance.e1([dict(OCT7, scheduling_delay_seconds=delay)])
        check(r["pass"] is ok, f"delay {delay}: diff {r['rows'][0]['diff']} pass {r['pass']}")


def test_e2_price():
    pairs = [("a", 3000.18, 3000.00), ("b", 2999.90, 3000.00), ("c", None, 3000.0)]
    r = conformance.e2(pairs)
    check(r["pass"] and r["tolerance"] is None and r["missing"] == ["c"], f"untoleranced E2 {r}")
    check(r["distribution"]["n"] == 2 and r["distribution"]["max_abs"] == 0.18, f"distribution {r}")
    check(conformance.e2(pairs, 0.18)["pass"], "0.18 at tolerance 0.18 breached")
    r = conformance.e2(pairs + [("d", 3000.19, 3000.0)], 0.18)
    check(not r["pass"] and r["breaches"] == [("d", 0.19)], f"0.19 at 0.18: {r['breaches']}")


def test_e3_status_attribution():
    lb, ls = OCT7["quote"]["quote_bid"], OCT7["quote"]["quote_spread_price"]
    cases = [(OCT7, lb, ls), (OCT7, 4131.0, ls), (OCT7, lb, 30.0), (OCT7, 4128.0, 3.0), (OCT7, 4131.0, 30.0)]
    r = conformance.e3(cases, PARAMS, SPEC)
    got = [x.get("attribution", "agree") for x in r["rows"]]
    want = ["agree", "bid", "spread", "bid+spread", conformance.E3_UNATTRIBUTED]
    check(got == want, f"attribution {got}, want {want}")
    check(r["agreement"] == 0.2 and r["unattributed"] == [OCT7["bar_time"]] and not r["pass"], f"E3 {r}")
    check(conformance.e3(cases[:4], PARAMS, SPEC)["pass"], "attributed disagreements failed E3")


def test_e4_spread_by_session():
    model = spread.SpreadModel({"LONDON": [20, 25, 30, 35], "NY": [18, 22]})
    evs = [dict(OCT7, session="NY", quote=dict(OCT7["quote"], quote_spread_price=p))
           for p in (0.26, 0.31, 0.22, 0.40)]
    r = conformance.e4(evs, model)
    check(r["NY"]["live"] == {"n": 4, "p10": 22, "p50": 26, "p90": 40, "max_abs": 40}, f"NY live {r['NY']['live']}")
    check(r["LONDON"]["live"]["n"] == 0 and r["LONDON"]["model"]["p50"] == 25, f"LONDON {r['LONDON']}")


def test_levels_and_verdict():
    live = {"bar_time": "t", "blocked_at": "no_pullback", "cannot_evaluate": False}
    check(conformance.level([(live, dict(live))])["pass"], "identical events mismatched")
    check(not conformance.level([(live, {"bar_time": "t", "blocked_at": "no_pullback"})])["pass"],
          "a field missing on the replay side passed")
    check(not conformance.level([(live, dict(live, blocked_at="no_bos"))])["pass"], "blocked_at mismatch passed")
    ok = {"pass": True}
    v = conformance.verdict({1: ok, 2: ok}, ok, ok, ok, ok)
    check(v["verdict"] == "CONFORMANT" and v["reasons"] == [], f"all pass: {v}")
    lb = OCT7["quote"]["quote_bid"]
    e3 = conformance.e3([(OCT7, 4131.0, 30.0)], PARAMS, SPEC)
    for args, reason in ((({1: ok, 2: {"pass": False}}, ok, ok, ok, ok), "Level 2 mismatch"),
                         (({1: ok}, {"pass": False}, ok, ok, ok), "E0 field mismatch"),
                         (({1: ok}, ok, conformance.e1([dict(OCT7, scheduling_delay_seconds=600.0)]), ok, ok),
                          "E1 breach"),
                         (({1: ok}, ok, ok, conformance.e2([("x", lb + 0.5, lb)], 0.18), ok), "E2 breach"),
                         (({1: ok}, ok, ok, ok, e3), conformance.E3_UNATTRIBUTED)):
        v = conformance.verdict(*args)
        check(v["verdict"] == "NOT CONFORMANT" and v["reasons"] == [reason]
              and v["historical_reporting"] == "prohibited", f"{reason}: {v}")
    v = conformance.verdict({1: ok}, ok, ok, conformance.e2([("x", lb + 0.5, lb)]), ok)
    check(v["verdict"] == "CONFORMANT", "E2 breached before TBD-4 is frozen")


_real_status_of = conformance.status_of
_real_verdict = conformance.verdict


def _status_ignoring_spread(ev, bid, spread_price, params, spec):
    return _real_status_of(ev, bid, OCT7["quote"]["quote_spread_price"], params, spec)


def _verdict_without_e3(levels, e0r, e1r, e2r, e3r):
    return _real_verdict(levels, e0r, e1r, e2r, dict(e3r, **{"pass": True}))


def _type7(xs, p):
    if not xs:
        return None
    k = p * (len(xs) - 1)
    i = int(k)
    return xs[i] + (xs[min(i + 1, len(xs) - 1)] - xs[i]) * (k - i)


def _level_both_sides_only(pairs, fields=conformance.LEVEL2_FIELDS):
    rows = [{"diff": f} for live, rep in pairs for f in fields
            if f in live and f in rep and live[f] != rep[f]]
    return {"mismatches": rows, "pass": not rows}


VARIANTS = {
    "test_e1_decision_instant": [
        ("E1 limit 61 s", conformance, "E1_LIMIT", 61)],
    "test_e2_price": [
        ("tolerance inclusive", conformance, "e2",
         lambda pairs, tolerance=None, _f=conformance.e2: _f(pairs, None if tolerance is None else tolerance - 0.001))],
    "test_e3_status_attribution": [
        ("spread not passed through", conformance, "status_of", _status_ignoring_spread),
        ("unattributed accepted", conformance, "E3_UNATTRIBUTED", "bid+spread")],
    "test_e4_spread_by_session": [
        ("type-7 quantiles", conformance, "_type1", _type7)],
    "test_levels_and_verdict": [
        ("E3 ignored by the verdict", conformance, "verdict", _verdict_without_e3),
        ("one-sided fields ignored", conformance, "level", _level_both_sides_only)],
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
            if verdict == "FAIL":
                print(f"PASS    {name} / {label}: fails as required ({why[:90]})")
            else:
                bad += 1
                print(f"FAIL    {name} / {label}: broken variant PASSED")
    missing = [n for n, _ in tests() if n not in VARIANTS]
    for n in missing:
        print(f"FAIL    {n}: no broken variant registered")
    bad += len(missing)
    print("\nCONTROLS ALL PASS" if bad == 0 else f"\nCONTROLS: {bad} FAILED")
    return bad == 0


if __name__ == "__main__":
    ok = main_controls() if "--controls" in sys.argv else main_tests()
    sys.exit(0 if ok else 1)
