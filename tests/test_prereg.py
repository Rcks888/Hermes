#!/usr/bin/env python3
"""PREREGISTRATION.md pr-0.4 section 9: preregistered protocol tests.

Each test is named test_N after its pr-0.4 section 9 number (test_4a-4c are
the three parts of test 4). VARIANTS maps a test to the broken variants it
must catch; `--controls` runs every test under each of them and requires a
failure (pr-0.4 section 9: "each shown once to fail on a deliberately broken
variant").

All inputs are constructed: synthetic trades, equity curves, bars and
ledgers, never fold data, so no historical SIMULATION result is produced.

Built: all seven. 1 seal and unseal (replay/holdout.py), 2 fold boundary
(replay/folds.py), 3 bootstrap golden vector, 4 gate evaluator (thresholds,
segments, paths), 5 random benchmark B2 (replay/benchmark.py; 5b is its
downstream part), 6 attempt ledger (replay/attempts.py), 7 holdout run family.
"""
import calendar
import dataclasses
import functools
import hashlib
import io
import json
import subprocess
import math
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import attempts, benchmark, clock, data, entry, exits, folds, gates, holdout, portfolio, spread  # noqa: E402,E501

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


# ------------------------------------------------- synthetic market fixtures

PARAMS = attempts.base_params()
SPEC = entry.BrokerSpec.load()
MODEL30 = spread.SpreadModel({s: [30] for s in spread.SESSIONS})
FLAT = (3000.0, 3000.5, 2999.5, 3000.2)
SPIKE = (3000.0, 3021.0, 2999.5, 3020.5)
BASE_HASH = attempts.REGISTERED_BASELINE
ENGINE = "a1b2c3d"
VERDICTS = {"5a5a5a5": {"verdict": "PASS", "config_hash": BASE_HASH, "attempt": 0},
            "fa11fa1": {"verdict": "FAIL", "config_hash": BASE_HASH, "attempt": 0},
            "b0b0b0b": {"verdict": "PASS", "config_hash": "0123456789ab", "attempt": 1}}
GO = f"{holdout.GO_AHEAD_PHRASE}: verdict 5a5a5a5, config {BASE_HASH}. Rickson Kang"
GO_AHEADS = {("60a4ead", "5a5a5a5"): GO,
             ("0th3r00", "5a5a5a5"): GO.replace("5a5a5a5", "b0b0b0b"),
             ("n0hash0", "5a5a5a5"): GO.replace(BASE_HASH, ""),
             ("n0phras", "5a5a5a5"): GO.replace(holdout.GO_AHEAD_PHRASE, "OK"),
             ("60a4ead", "fa11fa1"): GO.replace("5a5a5a5", "fa11fa1"),
             ("60a4ead", "b0b0b0b"): GO}


def go_lookup(commit, verdict_commit):
    return GO_AHEADS.get((commit, verdict_commit))


def typed(config_hash):
    return config_hash == BASE_HASH


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc as e:
        return e
    raise AssertionError(f"{getattr(fn, '__name__', fn)}{a} did not raise {exc.__name__}")


def build(minutes):
    m1 = data.Bars.from_rows(60, [(t,) + tuple(minutes[t]) for t in sorted(minutes)])
    slots = {}
    for t in sorted(minutes):
        slots.setdefault(t - t % 900, []).append((t,) + tuple(minutes[t]))
    m15 = data.Bars.from_rows(900, [(s,) + tuple(data.rebuild(ms)) for s, ms in sorted(slots.items())])
    return m1, m15


def market(minutes, first, last, params=PARAMS):
    m1, m15 = build(minutes)
    return portfolio.Context(m1, m15, clock.schedule(first, last), MODEL30, params, SPEC)


def long_signal():
    return {"direction": "LONG", "entry": 3000.0, "sl": 2990.0, "tp": 3020.0,
            "sl_distance": 10.0, "atr": 8.0, "symbol": "XAUUSD"}


def on_engine(sha):
    """The running engine as current_engine() would read it from git."""
    return mock.patch.object(holdout, "current_engine", lambda repo=None: sha)


def running(sha):
    def wrap(fn):
        @functools.wraps(fn)
        def test():
            with on_engine(sha):
                return fn()
        return test
    return wrap


def unsealed(log, model=MODEL30):
    return holdout.unseal("5a5a5a5", "60a4ead", model, log, VERDICTS.get, go_lookup, typed)


# ------------------------------------------------------------------ test 1

@running(ENGINE)
def test_1_seal():
    h0 = data.HOLDOUT[0]
    raises(data.HoldoutSealed, data.guard, h0, h0 + 900)
    raises(data.HoldoutSealed, data.guard, h0 - 60, h0 + 60)
    with mock.patch.object(holdout.sys, "stdin", io.StringIO(BASE_HASH + "\n")):
        check(holdout.tty_confirm(BASE_HASH) is False, "a non-interactive stdin confirmed the unseal")
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "unseal.jsonl"
        for verdict, go, eng, conf in (("fa11fa1", "60a4ead", ENGINE, typed), ("0000000", "60a4ead", ENGINE, typed),
                                       ("b0b0b0b", "60a4ead", ENGINE, typed), ("5a5a5a5", "nonexst", ENGINE, typed),
                                       ("5a5a5a5", "0th3r00", ENGINE, typed), ("5a5a5a5", "n0hash0", ENGINE, typed),
                                       ("5a5a5a5", "n0phras", ENGINE, typed),
                                       ("5a5a5a5", "60a4ead", ENGINE + "-dirty", typed),
                                       ("5a5a5a5", "60a4ead", ENGINE, lambda h: False)):
            with on_engine(eng):
                raises(holdout.HoldoutRefused, holdout.unseal, verdict, go, MODEL30, log, VERDICTS.get,
                       go_lookup, conf)
        with mock.patch.object(holdout.sys, "stdin", io.StringIO(BASE_HASH + "\n")):
            raises(holdout.HoldoutRefused, holdout.unseal, "5a5a5a5", "60a4ead", MODEL30, log,
                   VERDICTS.get, go_lookup)
        check(not log.exists(), "a refused unseal wrote the log")
        u = unsealed(log)
        lines = log.read_text().splitlines()
        check(len(lines) == 1, f"{len(lines)} log entries after one unseal")
        e = json.loads(lines[0])
        check(e["verdict_commit"] == "5a5a5a5" and e["config_hash"] == BASE_HASH and e["engine_commit"] == ENGINE
              and e["go_ahead_commit"] == "60a4ead" and e["go_ahead"] == GO and e["ts"]
              and e["spread_hashes"]["draw"] == MODEL30.hash, f"unseal entry {e}")
        p = u.permit(holdout.RunSpec(BASE_HASH))
        data.guard(h0, h0 + 900, p)
        raises(data.HoldoutSealed, data.guard, h0, h0 + 900, object())
        for verdict in ("5a5a5a5", "b0b0b0b"):
            raises(holdout.SecondUnseal, holdout.unseal, verdict, "60a4ead", MODEL30, log,
                   VERDICTS.get, go_lookup, typed)
        check(len(log.read_text().splitlines()) == 1, "a refused second unseal wrote the log")
        s = holdout.session(log)
        check((s.verdict_commit, s.config_hash, s.engine_commit) == ("5a5a5a5", BASE_HASH, ENGINE),
              f"session {s}")


# ------------------------------------------------------------------ test 2

FRI, MON = date(2025, 8, 29), date(2025, 9, 1)


def boundary_case():
    """A LONG entered Fri 29 Aug 20:08 server (NY), flat to the development
    end, TP touched by the first holdout hour; a holdout signal Mon 10:08."""
    sched = clock.schedule(date(2025, 8, 25), date(2025, 9, 5))
    t0, t1 = srv(2025, 8, 29, 19, 45), srv(2025, 9, 1, 11, 0)
    mins = {t: FLAT for t in range(t0, t1, 60) if sched.scheduled(t)}
    spike = min(t for t in mins if t >= srv(2025, 9, 1, 1, 30))
    mins[spike] = SPIKE
    c = market(mins, date(2025, 8, 25), date(2025, 9, 5))
    opps = [{"bar_open": srv(2025, 8, 29, 19, 45), "signal": long_signal()},
            {"bar_open": srv(2025, 9, 1, 9, 45), "signal": long_signal()}]
    last_dev = max(t for t in mins if t < folds.DEVELOPMENT[1])
    return c, opps, last_dev


def truncation_case():
    """Inside development, independent of the seal: a fold ending Fri 22:00
    server with the TP touched at 22:30, after the fold's end."""
    sched = clock.schedule(date(2025, 8, 25), date(2025, 9, 5))
    t0, t1 = srv(2025, 8, 29, 19, 45), srv(2025, 8, 29, 23, 0)
    mins = {t: FLAT for t in range(t0, t1, 60) if sched.scheduled(t)}
    mins[srv(2025, 8, 29, 22, 30)] = SPIKE
    fold = (folds.SPAN_START, srv(2025, 8, 29, 22, 0))
    return market(mins, date(2025, 8, 25), date(2025, 9, 5)), fold


@running(ENGINE)
def test_2_fold_boundary():
    c, fold = truncation_case()
    res = folds.run_fold([{"bar_open": srv(2025, 8, 29, 19, 45), "signal": long_signal()}], c, fold).records[0]["result"]
    check(res.outcome == "OPEN" and res.marks[-1][0] == fold[1],
          f"fold ending 22:00: {res.outcome} at {res.exit_time}, last mark {res.marks[-1][0]}; resolved past the fold end")
    c, opps, last_dev = boundary_case()
    check(folds.DEVELOPMENT[1] == folds.HOLDOUT[0] == data.HOLDOUT[0], "folds do not meet at the seal")
    dev = folds.run_fold(opps, c, folds.DEVELOPMENT)
    check([r["bar_open"] for r in dev.records] == [opps[0]["bar_open"]], "dev fold took a holdout opportunity")
    rec = dev.records[0]
    check(rec["population"] == "C", f"dev trade population {rec['population']}")
    res = rec["result"]
    check(res.outcome == "OPEN" and res.exit_time is None and res.r is None,
          f"dev trade {res.outcome} at {res.exit_time}: resolved past the development end")
    check(res.marks and res.marks[-1][0] == last_dev + 60 <= folds.DEVELOPMENT[1],
          f"last mark {res.marks[-1] if res.marks else None}, want the close of {last_dev}")
    want = (FLAT[3] - (3000.0 + 0.30)) / (3000.0 + 0.30 - 2990.0)
    check(close(res.mtm_r, want, 1e-9), f"mtm_r {res.mtm_r} != {want}")
    check(gates.closed_trades(dev) == [], "an OPEN trade entered the closed-trade statistics")
    raises(data.HoldoutSealed, folds.run_fold, opps, c, folds.HOLDOUT)
    with tempfile.TemporaryDirectory() as d:
        p = unsealed(Path(d) / "u.jsonl").permit(holdout.RunSpec(BASE_HASH))
    ho = folds.run_fold(opps, c, folds.HOLDOUT, permit=p)
    check([r["bar_open"] for r in ho.records] == [opps[1]["bar_open"]], "holdout fold took a dev opportunity")
    h = ho.records[0]
    check(h["population"] == "C" and h["open_trades"] == 0,
          f"holdout run did not start flat: {h.get('reason')}, open {h.get('open_trades')}")


# ------------------------------------------------------------------ test 5

def b2_inputs():
    """Two cells with signals and one evaluated cell without any (A_c = 0)."""
    bars, records = [], []
    for (mo, day), n_sig in (((3, 4), 6), ((4, 8), 0)):
        for k in range(16):
            b0 = srv(2025, mo, day, 9, 0) + 900 * k
            bars.append((b0, 3000.0 + k, 8.0))
            if k < n_sig:
                e = clock.entry_instant(clock.decision_instant(b0, clock.BASELINE_DELAY))
                sig = dict(long_signal(), direction="LONG" if k % 3 else "SHORT", sl_distance=4.003 + k)
                records.append({"bar_open": b0, "E": e, "population": "CAB"[k % 3], "signal": sig})
    records.append({"bar_open": srv(2025, 3, 4, 20, 0), "E": srv(2025, 3, 4, 20, 23), "population": None})
    return bars, records


def u_independent(i, t, part):
    k = int.from_bytes(hashlib.sha256(f"hermes-pr-random-v2|{i}|{t}|{part}".encode()).digest()[:8], "big")
    return (k + 0.5) / 2 ** 64


def test_5_random_benchmark():
    bars, records = b2_inputs()
    m = benchmark.matching(records, bars, folds.DEVELOPMENT)
    cells = {benchmark.cell_of(b0, clock.BASELINE_DELAY) for b0, _, _ in bars}
    zero = [c for c in cells if m.q[c] == 0.0]
    check(len(zero) >= 1 and sum(m.q[c] > 0 for c in cells) >= 1, f"cells {m.q}")
    check(m.stops == sorted(r["signal"]["sl_distance"] / 8.0 for r in records if r["population"]),
          f"R {m.stops}")
    for i in (0, 7):
        for b0, _, _ in bars[:4]:
            for part in ("emit", "dir", "stop"):
                check(benchmark.u(i, b0 + 900, part) == u_independent(i, b0 + 900, part), "draw key")
    once = benchmark.draw_signals(7, m, bars, 2.0)
    check(once == benchmark.draw_signals(7, m, bars, 2.0), "portfolio 7 not reproducible")
    check(once != benchmark.draw_signals(8, m, bars, 2.0), "portfolios 7 and 8 identical")
    for o in once:
        b0 = o["bar_open"]
        c, t = benchmark.cell_of(b0, clock.BASELINE_DELAY), b0 + 900
        check(u_independent(7, t, "emit") < m.q[c], f"emitted at {b0} with u >= q_c")
        s, close_ = o["signal"], dict((b[0], b[1]) for b in bars)[b0]
        r = m.stops[spread.rank(u_independent(7, t, "stop"), len(m.stops)) - 1]
        sign = 1 if s["direction"] == "LONG" else -1
        check(s["direction"] == ("LONG" if u_independent(7, t, "dir") < m.p[c] else "SHORT"), "direction draw")
        check(s["entry"] == close_ and close(s["sl"], round(close_ - sign * r * 8.0, 2))
              and close(s["tp"], round(close_ + sign * 2.0 * r * 8.0, 2)), f"levels {s}")
        check(s["stop_multiple"] == r and s["sl_distance"] == r * 8.0
              and s["sl_price_distance"] == round(abs(close_ - s["sl"]), 2),
              f"sampled vs price stop distance {s}")
    emitted = [o["bar_open"] for i in range(200) for o in benchmark.draw_signals(i, m, bars, 2.0)]
    check(emitted and not [b for b in emitted if benchmark.cell_of(b, clock.BASELINE_DELAY) in zero],
          "a cell with A_c = 0 emitted")
    ho_bars = [(b0 + (srv(2025, 9, 2) - srv(2025, 3, 4)), c_, a) for b0, c_, a in bars]
    ho_rec = [dict(r, bar_open=r["bar_open"] + srv(2025, 9, 2) - srv(2025, 3, 4),
                   E=r["E"] + srv(2025, 9, 2) - srv(2025, 3, 4)) for r in records[:2]]
    mh = benchmark.matching(ho_rec, ho_bars, folds.HOLDOUT)
    check(sorted(mh.stops) == sorted(r["signal"]["sl_distance"] / 8.0 for r in ho_rec), "holdout R")
    raises(ValueError, benchmark.matching, records, ho_bars, folds.HOLDOUT)
    raises(ValueError, benchmark.matching, ho_rec, bars, folds.HOLDOUT)
    stats = [{sc: (None if i < 10 else -0.5 + i / 2000) for sc in SCENARIOS} for i in range(1000)]
    ev = gates.evaluate(attempt(pass_case(), benchmark=stats), "S")
    check(ev["benchmark_dropped"] == 10 and close(ev["benchmark_threshold"], -0.5 + 950 / 2000),
          f"G6 threshold {ev.get('benchmark_threshold')}, want rank 941 of 990")
    check(ev["gates"]["G6"] == gates.PASS, f"G6 {ev['gates']['G6']}")
    stats[10] = {sc: None for sc in SCENARIOS}
    check(gates.evaluate(attempt(pass_case(), benchmark=stats), "S")["gates"]["G6"] == gates.HOLD,
          "11 dropped is not HOLD")


def test_5b_random_benchmark_downstream():
    """Every bar of a LONDON hour emits LONG at r = 1: the first signal's
    entry minute is missing, the second fills, the third meets the slot."""
    day = (2025, 2, 4)
    bars = [(srv(*day, 9, 45) + 900 * k, 3000.0, 8.0) for k in range(3)]
    m = benchmark.Matching({benchmark.cell_of(bars[0][0], clock.BASELINE_DELAY): 1.0},
                           {benchmark.cell_of(bars[0][0], clock.BASELINE_DELAY): 1.0},
                           [1.0], folds.DEVELOPMENT, clock.BASELINE_DELAY)
    mins = {t: FLAT for t in range(srv(*day, 9, 0), srv(*day, 12, 0), 60) if t != srv(*day, 10, 8)}
    c = market(mins, date(2025, 2, 2), date(2025, 2, 7))
    emitted = [o["bar_open"] for o in benchmark.draw_signals(0, m, bars, 2.0)]
    check(emitted == [b[0] for b in bars], f"emitted {emitted}")
    run_ = benchmark.run_portfolio(0, m, bars, c, "S")
    check([r["bar_open"] for r in run_.records] == emitted, "records differ from the emitted signals")
    first, second, third = run_.records
    status = first.get("exec", {}).get("execution_validity_status")
    check(first["population"] == "B" and status == "quote_unavailable",
          f"invalid signal {first.get('population')} {status}")
    check(second["population"] == "C", f"second {second.get('population')} {second.get('reason')}")
    check(third["population"] == "A" and "Max open trades" in str(third.get("reason")),
          f"third not blocked by the slot rule: {third.get('reason')}")


# ------------------------------------------------------------------ test 6

def test_6_attempt_ledger():
    base = attempts.base_params()
    a0 = attempts.authorise(base, committed=set())
    check(a0 == {"attempt": 0, "variant": None, "config_hash": BASE_HASH, "alpha": 0.025}, f"baseline {a0}")
    off = (dict(base, tp_rr_multiple=3.0), dict(base, tp_rr_multiple=1.5, max_spread_points=40),
           dict(base, swing_lookback=4))
    with tempfile.TemporaryDirectory() as d:
        forged = Path(d) / "forged.jsonl"
        forged.write_text("".join(json.dumps({"attempt": k + 1, "variant": "V2", "rationale": "forged",
                                              "config_hash": attempts.config_hash(p)}) + "\n"
                                  for k, p in enumerate(off)))
        for bad in off:
            raises(attempts.OffMenu, attempts.classify, bad, base)
            raises(attempts.OffMenu, attempts.authorise, bad, forged, {attempts.config_hash(p) for p in off}, base)
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "ledger.jsonl"
        raises(attempts.OffMenu, attempts.register, "V9", "why", led, base)
        raises(attempts.OffMenu, attempts.register, "V2", "", led, base)
        for bad in (dict(base, tp_rr_multiple=3.0), dict(base, tp_rr_multiple=1.5, max_spread_points=40),
                    dict(base, swing_lookback=4)):
            raises(attempts.OffMenu, attempts.authorise, bad, led, set(), base)
        v2 = attempts.variant_params("V2", base)
        raises(attempts.NotCommitted, attempts.authorise, v2, led, set(), base)
        rec = attempts.register("V2", "G3 near miss: wins too small", led, base)
        h = attempts.config_hash(v2)
        check(rec["attempt"] == 1 and rec["config_hash"] == h, f"ledger {rec}")
        raises(attempts.NotCommitted, attempts.authorise, v2, led, set(), base)
        a = attempts.authorise(v2, led, {h}, base)
        check(a == {"attempt": 1, "variant": "V2", "config_hash": h, "alpha": 0.003125}, f"variant {a}")
        raises(attempts.OffMenu, attempts.register, "V2", "again", led, base)
        attempts.register("V4", "r", led, base)
        attempts.register("V7", "r", led, base)
        raises(attempts.OffMenu, attempts.register, "V5", "a fourth", led, base)
        check(len(attempts.entries(led)) == 3, "a refused registration wrote the ledger")
    t = pass_case()
    lower = gates.evaluate(attempt(t, alpha=a["alpha"]), "S")["bootstrap"]["lower"]
    check(close(lower, gates.bootstrap(t, gates.fold_months(*DEV), 0.003125)["lower"]), "G2 not at the variant level")
    check(not close(lower, gates.bootstrap(t, gates.fold_months(*DEV), 0.025)["lower"]), "levels coincide")


# ------------------------------------------------------------------ test 7

@running(ENGINE)
def test_7_holdout_run_family():
    with tempfile.TemporaryDirectory() as d:
        u = unsealed(Path(d) / "u.jsonl")
    R = holdout.RunSpec
    ok = ([R(BASE_HASH, scenario=sc) for sc in SCENARIOS] + [R(BASE_HASH, delay=dl) for dl in (60, 120, 180, 300)]
          + [R(BASE_HASH, spread="p90")] + [R(BASE_HASH, kind="random", scenario=sc) for sc in SCENARIOS])
    for s in ok:
        check(isinstance(u.permit(s), holdout.Permit), f"{s} refused")
    v2 = attempts.config_hash(attempts.variant_params("V2"))
    bad = [R(v2), R(BASE_HASH, delay=240), R(BASE_HASH, delay=600), R(BASE_HASH, delay=60, spread="p90"),
           R(BASE_HASH, spread="zero"), R(BASE_HASH, delay=60, scenario="C"), R(BASE_HASH, spread="p90", scenario="T"),
           R(BASE_HASH, kind="momentum"), R(BASE_HASH, kind="random", spread="p90"),
           R(BASE_HASH, kind="random", delay=60), R(BASE_HASH, scenario="X")]
    for s in bad:
        raises(holdout.HoldoutRefused, u.permit, s)
    for other in ("ffff000", ENGINE + "-dirty", "unknown"):
        with on_engine(other):
            raises(holdout.HoldoutRefused, u.permit, R(BASE_HASH))
    h0 = data.HOLDOUT[0] + 86400
    base_ctx = market({h0: FLAT}, date(2025, 9, 1), date(2025, 9, 5))
    p90 = spread.constant_model(MODEL30, 0.90)
    strat = [{"bar_open": h0 - 900, "signal": long_signal()}]

    def rand(i):
        return [{"bar_open": h0 - 900, "signal": dict(long_signal(), benchmark_portfolio=i)}]

    c = dataclasses.replace(base_ctx, permit=u.permit(R(BASE_HASH)))
    portfolio.run(strat, c, portfolio.SIMULATION, "S")
    for cc, sc, opps in ((dataclasses.replace(c, params=attempts.variant_params("V2")), "S", strat),
                         (dataclasses.replace(c, delay=300), "S", strat), (c, "T", strat),
                         (dataclasses.replace(c, model=p90), "S", strat), (c, "S", rand(0))):
        raises(holdout.HoldoutRefused, portfolio.run, opps, cc, portfolio.SIMULATION, sc)
    rec = portfolio.run([dict(strat[0], delay=480)], c, portfolio.SIMULATION, "S").records[0]
    check(rec["D"] == h0 + 480, f"explicit primary delay: D {rec['D']}")
    for d in (240, 60):
        raises(holdout.HoldoutRefused, portfolio.run, [dict(strat[0], delay=d)], c, portfolio.SIMULATION, "S")
    raises(holdout.HoldoutRefused, portfolio.run, strat, c, portfolio.CONFORMANCE, "S")
    for other in ("ffff000", ENGINE + "-dirty"):
        with on_engine(other):
            raises(holdout.HoldoutRefused, portfolio.run, strat, c, portfolio.SIMULATION, "S")
    c = dataclasses.replace(base_ctx, model=p90, permit=u.permit(R(BASE_HASH, spread="p90")))
    portfolio.run(strat, c, portfolio.SIMULATION, "S")
    raises(holdout.HoldoutRefused, portfolio.run, strat, dataclasses.replace(c, model=MODEL30),
           portfolio.SIMULATION, "S")
    c = dataclasses.replace(base_ctx, permit=u.permit(R(BASE_HASH, kind="random", scenario="T")))
    portfolio.run(rand(999), c, portfolio.SIMULATION, "T")
    for opps in (rand(1000), strat, rand(3) + [dict(rand(4)[0], bar_open=h0)], [dict(rand(5)[0], delay=240)]):
        raises(holdout.HoldoutRefused, portfolio.run, opps, c, portfolio.SIMULATION, "T")


def test_7b_engine_identity():
    """K3: current_engine reads HEAD and the work tree; holdout outputs in
    research/p1/ do not make it dirty, anything else does."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        def git(*a):
            return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo,
                                  capture_output=True, text=True, check=True).stdout.strip()
        git("init", "-q")
        (repo / "engine.py").write_text("x = 1\n")
        git("add", "engine.py")
        git("commit", "-q", "-m", "c1")
        sha = git("rev-parse", "HEAD")
        check(holdout.current_engine(repo) == sha, f"clean tree: {holdout.current_engine(repo)} != {sha}")
        (repo / "research" / "p1").mkdir(parents=True)
        (repo / "research" / "p1" / "holdout_unseal_log.jsonl").write_text("{}\n")
        check(holdout.current_engine(repo) == sha, "holdout output in research/p1/ made the tree dirty")
        (repo / "replay_patch.py").write_text("y = 2\n")
        check(holdout.current_engine(repo) == sha + "-dirty", "an untracked module left the tree clean")
        (repo / "replay_patch.py").unlink()
        (repo / "engine.py").write_text("x = 2\n")
        check(holdout.current_engine(repo) == sha + "-dirty", "a modified tracked file left the tree clean")
        git("commit", "-q", "-am", "c2")
        check(holdout.current_engine(repo) not in (sha, sha + "-dirty"), "a new commit kept the engine id")


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


_real_matching = benchmark.matching
_real_run_portfolio = benchmark.run_portfolio


def _guard_any_permit(t0, t1, permit=None):
    if t0 < data.HOLDOUT[1] and t1 > data.HOLDOUT[0] and permit is None:
        raise data.HoldoutSealed("sealed")


def _shared_book():
    book = portfolio.Book()
    return lambda fold: book


def _matching_pooled(records, bars, fold, delay=clock.BASELINE_DELAY):
    m = _real_matching(records, bars, fold, delay)
    sig = [r for r in records if r.get("population")]
    q = len(sig) / len(bars)
    p = sum(r["signal"]["direction"] == "LONG" for r in sig) / len(sig)
    return dataclasses.replace(m, q={c: q for c in m.q}, p={c: p for c in m.q})


def _run_dropping_invalid(i, m, bars, ctx, scenario="S"):
    r = _real_run_portfolio(i, m, bars, ctx, scenario)
    r.records = [x for x in r.records if x.get("population") != "B"]
    return r


def _diff(params, base):
    return sorted(k for k in set(params) | set(base) if params.get(k) != base.get(k))


def _classify_any_single(params, base):
    d = _diff(params, base)
    if not d:
        return None
    if len(d) == 1:
        return next((v for v, (k, x) in attempts.MENU.items() if k == d[0] and x == params[k]), "X-" + d[0])
    raise attempts.OffMenu(d)


def _classify_first_menu_change(params, base):
    d = _diff(params, base)
    if not d:
        return None
    for vid, (k, x) in attempts.MENU.items():
        if k in d and params[k] == x:
            return vid
    raise attempts.OffMenu(d)


def _family_combined(spec, h):
    if spec.config_hash != h or spec.scenario not in SCENARIOS:
        return False
    if spec.kind == "random":
        return spec.delay == holdout.PRIMARY_DELAY and spec.spread == "draw"
    return spec.kind == "strategy" and (spec.delay in clock.PREREGISTERED_DELAYS
                                        and spec.spread in ("draw", "p90"))


def _permit_without(part):
    """check_permit with one of its K5 checks removed."""
    def check(ctx, scenario, opps, mode):
        p = ctx.permit
        if part != "mode" and mode != portfolio.SIMULATION:
            raise holdout.HoldoutRefused("mode")
        if part != "engine":
            holdout.check_engine(p.engine_commit)
        got = [attempts.config_hash(ctx.params), ctx.delay, scenario, ctx.model.hash]
        want = [p.spec.config_hash, p.spec.delay, p.spec.scenario, p.spread_hash]
        if part == "model":
            got, want = got[:3], want[:3]
        if got != want:
            raise holdout.HoldoutRefused("run")
        if part != "opp delay" and any(o.get("delay", ctx.delay) != p.spec.delay for o in opps):
            raise holdout.HoldoutRefused("delay")
        kind = portfolio.run_kind(opps)
        if part != "kind" and kind not in (None, p.spec.kind):
            raise holdout.HoldoutRefused("kind")
        if kind == "random":
            idx = {o["signal"]["benchmark_portfolio"] for o in opps}
            if part != "one portfolio" and len(idx) != 1:
                raise holdout.HoldoutRefused("portfolios")
            if part != "index" and max(idx) >= holdout.N_PORTFOLIOS:
                raise holdout.HoldoutRefused("index")
    return check


_real_draw_signals = benchmark.draw_signals


def _draw_price_distance(i, m, bars, tp_rr, digits=2):
    out = _real_draw_signals(i, m, bars, tp_rr, digits)
    for o in out:
        o["signal"]["sl_distance"] = o["signal"]["sl_price_distance"]
    return out


VARIANTS = {
    "test_1_seal": [
        ("second unseal accepted", holdout, "previous_unseals", lambda p: []),
        ("verdict not checked", holdout, "verdict_ok", lambda rec: True),
        ("dirty engine accepted", holdout, "clean", lambda c: bool(c)),
        ("go-ahead not bound to verdict and config", holdout, "go_ahead_ok", lambda t, v, h: bool(t)),
        ("no interactive confirmation", holdout, "tty_confirm", lambda h: True),
        ("seal disabled", data, "guard", lambda t0, t1, permit=None: None),
        ("any object opens the seal", data, "guard", _guard_any_permit)],
    "test_2_fold_boundary": [
        ("fold end ignored", folds, "fold_end", lambda fold, ctx: int(ctx.m1.time[-1]) + 60),
        ("holdout inherits the dev book", folds, "start_book", _shared_book())],
    "test_5_random_benchmark": [
        ("key without portfolio index", benchmark, "u",
         lambda i, t, part: spread.u_key(f"{benchmark.KEY}|{int(t)}|{part}")),
        ("pooled emission rate", benchmark, "matching", _matching_pooled),
        ("G6 rank fixed at 950", gates, "benchmark_rank", lambda m: 950),
        ("matching crosses folds", benchmark, "_require_fold", lambda ts, fold: None),
        ("stop distance from rounded prices", benchmark, "draw_signals", _draw_price_distance)],
    "test_5b_random_benchmark_downstream": [
        ("invalid signals dropped", benchmark, "run_portfolio", _run_dropping_invalid),
        ("no slot rule", benchmark, "MODE", portfolio.CONFORMANCE)],
    "test_6_attempt_ledger": [
        ("any single change accepted", attempts, "classify", _classify_any_single),
        ("combined variants accepted", attempts, "classify", _classify_first_menu_change),
        ("commit not checked", attempts, "is_committed", lambda h, c: True),
        ("baseline level for variants", attempts, "alpha_of", lambda vid: 0.025),
        ("no attempt cap", attempts, "N_FURTHER", 8)],
    "test_7_holdout_run_family": [
        ("any run permitted", holdout, "in_family", lambda spec, h: True),
        ("combined sensitivities permitted", holdout, "in_family", _family_combined),
        ("engine not checked", holdout, "same_engine", lambda a, b: True),
        ("permit not checked on the run", portfolio, "check_permit", lambda c, sc, o, m: None),
        ("mode not checked", portfolio, "check_permit", _permit_without("mode")),
        ("engine not rechecked at run time", portfolio, "check_permit", _permit_without("engine")),
        ("opportunity delay not checked", portfolio, "check_permit", _permit_without("opp delay")),
        ("several portfolios in one run", portfolio, "check_permit", _permit_without("one portfolio")),
        ("spread model not checked", portfolio, "check_permit", _permit_without("model")),
        ("signal kind not checked", portfolio, "check_permit", _permit_without("kind")),
        ("portfolio count not checked", portfolio, "check_permit", _permit_without("index"))],
    "test_7b_engine_identity": [
        ("untracked files ignored", holdout, "is_dirty",
         lambda lines: any(not x.startswith("?? ") for x in lines if x)),
        ("no untracked exemption", holdout, "is_dirty", lambda lines: any(lines)),
        ("work tree not checked", holdout, "is_dirty", lambda lines: False)],
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
