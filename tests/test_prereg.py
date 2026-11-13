#!/usr/bin/env python3
"""PREREGISTRATION.md pr-0.4 section 9: preregistered protocol tests.

Each test is named test_N after its pr-0.4 section 9 number (test_4a-4c are
the three parts of test 4). VARIANTS maps a test to the broken variants it
must catch; `--controls` runs every test under each of them and requires a
failure (pr-0.4 section 9: "each shown once to fail on a deliberately broken
variant").

All inputs are constructed: synthetic trades and equity curves, never fold
data, so no historical SIMULATION result is produced.

Built: 3 (bootstrap golden vector), 4 (gate evaluator: thresholds, segments,
paths). Tests 1, 2, 5, 6 and 7 follow.
"""
import calendar
import dataclasses
import json
import math
import sys
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import exits, gates, portfolio  # noqa: E402

GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "prereg_u3_golden.json").read_text())
SCENARIOS = exits.SCENARIOS


def srv(y, m, d, hh=0, mm=0):
    return calendar.timegm((y, m, d, hh, mm, 0))


DEV = (srv(2022, 6, 23), srv(2025, 9, 1))
TRADING_MONTHS = gates.fold_months(*DEV)[1:]


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def close(a, b, tol=1e-12):
    return a is not None and abs(a - b) <= tol


def trades_of(month_rs):
    """{(y, m): [R, ...]} -> [(E, R)], trade j of a month on day 4 + 3j."""
    out = []
    for (y, m), rs in sorted(month_rs.items()):
        out += [(srv(y, m, 4 + 3 * j, 10), r) for j, r in enumerate(rs)]
    return out


def curve_of(trades):
    """Equity marks at each exit, one hour after E."""
    cum, curve = 0.0, []
    for e, r in trades:
        cum += r
        curve.append((e + 3600, cum))
    return curve


def alternating(up, down):
    return {mo: list(up if k % 2 == 0 else down) for k, mo in enumerate(TRADING_MONTHS)}


def bench(per_path):
    return [{sc: per_path[sc](i) for sc in SCENARIOS} for i in range(1000)]


LOW = {sc: (lambda i: -0.5 + i / 2000) for sc in SCENARIOS}


def attempt(trades, curve=None, benchmark=None, paths=None, **kw):
    curve = curve if curve is not None else curve_of(trades)
    base = gates.Path(trades, curve, population_c=len(trades))
    return gates.Attempt(
        fold=DEV, paths=paths or {sc: base for sc in SCENARIOS},
        benchmark=benchmark or bench(LOW),
        delay_means={d: {sc: 0.2 for sc in SCENARIOS} for d in gates.SENSITIVITY_DELAYS},
        p90_means={sc: 0.1 for sc in SCENARIOS},
        preconditions={"ec_1_0": True, "conformant": True, "c6_3": True, "identity": True}, **kw)


def only_failing(ev, failing):
    bad = {k: v for k, v in ev["gates"].items() if v != gates.PASS}
    check(bad == failing, f"gates not PASS {bad}, expected {failing}")


def pass_case():
    return trades_of(alternating([2, 2, 2, 2], [-1, -1]))


def trimmed(n_losses_removed):
    """The PASS case with losses removed from the earliest down months."""
    months = alternating([2, 2, 2, 2], [-1, -1])
    left = n_losses_removed
    for mo in sorted(months):
        while left and -1 in months[mo]:
            months[mo].remove(-1)
            left -= 1
    return trades_of(months)


def g5_case():
    """4 of 6 segments positive, the last holding 60% of final equity."""
    months = {}
    segs = gates.segments(*DEV)
    for mo in TRADING_MONTHS:
        t = srv(mo[0], mo[1], 15)
        s = next(i for i, (a, b) in enumerate(segs) if a <= t < b)
        months[mo] = [-1, -1] if s in (2, 4) else [2, -1, 2, -1]
    months[(2025, 8)] = [2, 2, -1, 2, -1]
    return trades_of(months)


def test_3_bootstrap_golden_vector():
    trades = [tuple(x) for x in GOLDEN["trades"]]
    months = gates.fold_months(*GOLDEN["fold"])
    check(len(months) == 24, f"{len(months)} fold months")
    empty = [list(m) for m in months if not any(gates.month_of(e) == m for e, _ in trades)]
    check(empty == GOLDEN["empty_months"], f"empty months {empty}")
    b = gates.bootstrap(trades, months, GOLDEN["alpha"])
    check(close(b["lower"], GOLDEN["lower_bound"]), f"lower {b['lower']!r} != {GOLDEN['lower_bound']!r}")
    check(b["dropped"] == GOLDEN["dropped"] and not b["hold"], f"dropped {b['dropped']}")
    b2 = gates.bootstrap(trades, months, gates.ALPHA_VARIANT)
    check(close(b2["lower"], GOLDEN["lower_bound_alpha_variant"]), f"variant level {b2['lower']!r}")
    r = np.zeros(24)
    n = np.zeros(24)
    for e, x in trades:
        i = months.index(gates.month_of(e))
        r[i] += x
        n[i] += 1

    def reference(rr, nn, alpha):
        idx = np.random.default_rng(20261023).integers(0, len(rr), size=(10000, len(rr)))
        stats = sorted(sum(rr[j] for j in row) / sum(nn[j] for j in row) for row in idx
                       if sum(nn[j] for j in row))
        return stats[math.ceil(alpha * len(stats)) - 1]

    check(close(reference(r, n, GOLDEN["alpha"]), GOLDEN["lower_bound"]), "independent loop disagrees")
    without = reference(r[n > 0], n[n > 0], GOLDEN["alpha"])
    check(abs(without - GOLDEN["lower_bound"]) > 1e-6, "vector does not pin the empty months")
    one = [(srv(2023, 5, 10), 1.0), (srv(2023, 5, 11), -1.0)]
    hb = gates.bootstrap(one, months, 0.025)
    check(hb["hold"] and hb["dropped"] > 100, f"drop rule: dropped {hb['dropped']}, hold {hb['hold']}")


def test_4a_gate_thresholds():
    ev = gates.evaluate(attempt(pass_case()), "S")
    check(ev["verdict"] == gates.PASS, f"PASS case: {ev['gates']}")
    check(ev["n"] == 114 and close(ev["mean_r"], 1.0), f"PASS case n {ev['n']} mean {ev['mean_r']}")
    hundred = gates.evaluate(attempt(trimmed(14)), "S")
    check(hundred["n"] == 100 and hundred["verdict"] == gates.PASS, f"100 trades: {hundred['gates']}")
    t99 = trimmed(15)
    ev = gates.evaluate(attempt(t99), "S")
    check(ev["n"] == 99 and ev["verdict"] == gates.HOLD, f"99 trades: {ev['verdict']}")
    only_failing(ev, {"G1": gates.HOLD})
    high = {sc: (lambda i: 1.5 + i / 1000) for sc in SCENARIOS}
    ev = gates.evaluate(attempt(t99, benchmark=bench(high)), "S")
    check(ev["verdict"] == gates.HOLD, f"99 trades with a failing gate: {ev['verdict']}, not HOLD (PD2)")
    only_failing(ev, {"G1": gates.HOLD, "G6": gates.FAIL})
    neg = trades_of(alternating([2, 2, 2, 2], [-1] * 6))
    ev = gates.evaluate(attempt(neg), "S")
    lb = ev["bootstrap"]["lower"]
    check(lb is not None and lb < 0 < ev["mean_r"], f"negative bound case: lower {lb}, mean {ev['mean_r']}")
    check(ev["verdict"] == gates.FAIL, f"negative lower bound: {ev['verdict']}")
    only_failing(ev, {"G2": gates.FAIL})
    ev = gates.evaluate(attempt(g5_case()), "S")
    rs, final = ev["segment_r"], ev["final_equity"]
    check(sum(r > 0 for r in rs) == 4 and len(rs) == 6, f"segments {rs}")
    check(close(max(rs), 0.6 * final), f"largest segment {max(rs)} of final {final}")
    check(ev["verdict"] == gates.FAIL, f"4 of 6 with 60%: {ev['verdict']}")
    only_failing(ev, {"G5": gates.FAIL})


def test_4b_segments():
    segs = gates.segments(*DEV)
    starts = [DEV[0], srv(2023, 1, 1), srv(2023, 7, 1), srv(2024, 1, 1), srv(2024, 7, 1), srv(2025, 1, 1)]
    check(segs == list(zip(starts, starts[1:] + [DEV[1]])), f"segments {segs}")
    early = exits.Result("S", outcome="SL_HIT", exit_time=srv(2022, 8, 10, 12), r=-1.0,
                         marks=[(srv(2022, 8, 10, 11), -0.5)])
    across = exits.Result("S", outcome="TP_HIT", exit_time=srv(2023, 7, 3, 2), r=2.0,
                          marks=[(srv(2023, 6, 30, 21), -0.4), (srv(2023, 7, 1), 0.3),
                                 (srv(2023, 7, 3, 1), 0.8)])
    still_open = exits.Result("S", marks=[(srv(2025, 8, 29, 21), -0.2), (srv(2025, 8, 30), -0.6)],
                              mtm_r=-0.6)
    curve = gates.equity_curve([early, across, still_open])
    rs = gates.segment_r(curve, segs)
    want = [-1.0, 0.3, 1.7, 0.0, 0.0, -0.6]
    check(all(close(a, b) for a, b in zip(rs, want)), f"segment R {rs}, want {want}")
    final = gates.value_at(curve, DEV[1])
    check(close(final, 0.4) and close(sum(rs), final), f"segments sum {sum(rs)}, final {final}")
    check(close(gates.max_drawdown(curve), 1.4), f"drawdown {gates.max_drawdown(curve)}")


def test_4c_paths():
    t = pass_case()
    s = gates.Path(t, curve_of(t), population_c=len(t))
    dip = sorted(curve_of(t) + [(srv(2024, 3, 20), gates.value_at(curve_of(t), srv(2024, 3, 20)) - 25.0)])
    c = gates.Path(t, dip, population_c=len(t))
    final, rows = gates.p1_development(attempt(t, paths={"S": s, "T": s, "C": c}))
    check(rows["S"]["verdict"] == gates.PASS, f"S: {rows['S']['gates']}")
    only_failing(rows["C"], {"G4": gates.FAIL})
    check(final == gates.HOLD, f"G4 fails on C only: {final}, not HOLD by G9")
    mixed = {"S": LOW["S"], "T": LOW["T"], "C": (lambda i: 0.5 + i / 1000)}
    final, rows = gates.p1_development(attempt(t, benchmark=bench(mixed)))
    check(close(rows["C"]["benchmark_threshold"], 1.449), f"C threshold {rows['C'].get('benchmark_threshold')}")
    only_failing(rows["C"], {"G6": gates.FAIL})
    check(rows["S"]["verdict"] == gates.PASS and final == gates.HOLD, f"G6 on C: {final}")
    final, _ = gates.p1_development(attempt(t))
    check(final == gates.PASS, f"all paths PASS: {final}")
    a = attempt(t)
    a.preconditions["conformant"] = False
    check(gates.p1_development(a)[0] == gates.HOLD, "failed precondition is not HOLD")


_real_month_totals = gates.month_totals
_real_equity_curve = gates.equity_curve


def _totals_without_empty(trades, months):
    r, n = _real_month_totals(trades, months)
    return r[n > 0], n[n > 0]


def _totals_per_trade(trades, months):
    return np.array([x for _, x in trades]), np.ones(len(trades))


def _bootstrap_on_mean(trades, months, alpha):
    return {"lower": gates.mean_r(trades), "dropped": 0, "retained": gates.N_RESAMPLES, "hold": False}


def _combine_fail_first(results):
    vals = set(results.values())
    return gates.FAIL if gates.FAIL in vals else gates.HOLD if gates.HOLD in vals else gates.PASS


def _curve_realised_only(results):
    return _real_equity_curve([dataclasses.replace(r, marks=[]) for r in results])


def _value_strictly_before(curve, t):
    prior = [v for s, v in curve if s < t]
    return prior[-1] if prior else 0.0


VARIANTS = {
    "test_3_bootstrap_golden_vector": [
        ("empty months dropped", gates, "month_totals", _totals_without_empty),
        ("trade-level bootstrap", gates, "month_totals", _totals_per_trade),
        ("type-7 quantile", gates, "lower_quantile",
         lambda stats, alpha: float(np.quantile(stats, alpha, method="linear")))],
    "test_4a_gate_thresholds": [
        ("G1 at 99 trades", gates, "MIN_TRADES", 99),
        ("thin sample FAIL first", gates, "combine", _combine_fail_first),
        ("G2 on the mean", gates, "bootstrap", _bootstrap_on_mean),
        ("no concentration cap", gates, "concentration_ok", lambda rs, final: True),
        ("cap against positive segments", gates, "concentration_ok",
         lambda rs, final: max(rs) <= gates.CONCENTRATION * sum(r for r in rs if r > 0))],
    "test_4b_segments": [
        ("segments from realised R only", gates, "equity_curve", _curve_realised_only),
        ("value strictly before the instant", gates, "value_at", _value_strictly_before)],
    "test_4c_paths": [
        ("verdict of S only", portfolio, "verdicts_agree", lambda rows: True),
        ("benchmark read on S", gates, "portfolio_stat", lambda p, sc: p["S"])],
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
