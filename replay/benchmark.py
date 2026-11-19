"""Random-entry benchmark B2 (PREREGISTRATION.md pr-0.4 B2.1-B2.5, G6;
section 9 test 5).

Each portfolio replaces the strategy's signal generator and keeps everything
downstream: portfolio.run in SIMULATION mode, with the same gate, spread draw,
delay, propose_execution, margin, exits and statuses. Matching statistics
come from the strategy run of the same fold and never cross folds.

Interpretations, to be confirmed in review (none changes a frozen clause):
- N1. Population A signals of a strategy run are its records with a
  population (A, B or C). A record with population None was not evaluated
  (C1.7) and is not a signal.
- N2. A cell's session is the session of D at the run's delay, by
  clock.session_of, the classifier the gate uses.
- N3. A signal's sl_atr_ratio is sl_distance / atr, unrounded, as
  risk_manager computes it.
- N4. Random SL and TP are rounded to the symbol's digits, as the strategy's
  levels are; sl_distance stays r x ATR(14).
- N5. `bars` are (bar_open, close, atr) for every evaluated bar of the fold,
  taken from the strategy run's own pipeline, so ATR(14) is the strategy's.

Module-level functions u, matching, draw_signals, run_portfolio and the
constant MODE are the seams the tests patch with broken variants.
"""
import time
from dataclasses import dataclass, replace

from replay import clock, exits, folds, portfolio, spread

KEY = "hermes-pr-random-v2"
MODE = portfolio.SIMULATION


def u(i, t, part):
    """B2.2: uniform for portfolio i, bar close t, part emit | dir | stop."""
    return spread.u_key(f"{KEY}|{i}|{int(t)}|{part}")


def cell_of(bar_open, delay):
    """B2.1: (calendar month of the server date, session of D) (N2)."""
    g = time.gmtime(int(bar_open))
    return (g.tm_year, g.tm_mon), clock.session_of(clock.decision_instant(bar_open, delay))


@dataclass
class Matching:
    q: dict
    p: dict
    stops: list
    fold: tuple
    delay: int


def _require_fold(times, fold):
    out = [t for t in times if not folds.in_fold(t, fold)]
    if out:
        raise ValueError(f"{len(out)} matching inputs outside the fold {fold}: matching never crosses folds")


def matching(records, bars, fold, delay=clock.BASELINE_DELAY):
    """B2.1 from the strategy run of this fold: q_c, p_c and the sorted R."""
    signals = [r for r in records if r.get("population") is not None]
    _require_fold([r["E"] for r in signals], fold)
    _require_fold([clock.entry_instant(clock.decision_instant(b[0], delay)) for b in bars], fold)
    n, a, longs = {}, {}, {}
    for b0, _, _ in bars:
        c = cell_of(b0, delay)
        n[c] = n.get(c, 0) + 1
    for r in signals:
        c = cell_of(r["bar_open"], delay)
        a[c] = a.get(c, 0) + 1
        longs[c] = longs.get(c, 0) + (r["signal"]["direction"] == "LONG")
    q = {c: a.get(c, 0) / n[c] for c in n}
    p = {c: longs[c] / a[c] for c in a}
    stops = sorted(r["signal"]["sl_distance"] / r["signal"]["atr"] for r in signals)
    return Matching(q, p, stops, fold, delay)


def draw_signals(i, m, bars, tp_rr, digits=2):
    """B2.2, B2.3: portfolio i's signals over the fold's evaluated bars, in
    time order. A cell with A_c = 0 has q_c = 0 and emits nothing."""
    opps = []
    for b0, close, atr in sorted(bars):
        c, t = cell_of(b0, m.delay), b0 + 900
        if not u(i, t, "emit") < m.q.get(c, 0.0):
            continue
        is_long = u(i, t, "dir") < m.p[c]
        r = m.stops[spread.rank(u(i, t, "stop"), len(m.stops)) - 1]
        dist = r * atr
        sl = round(close - dist if is_long else close + dist, digits)
        tp = round(close + tp_rr * dist if is_long else close - tp_rr * dist, digits)
        opps.append({"bar_open": b0, "signal": {
            "direction": "LONG" if is_long else "SHORT", "entry": close, "sl": sl, "tp": tp,
            "sl_distance": dist, "atr": atr, "symbol": "XAUUSD", "benchmark_portfolio": i}})
    return opps


def run_portfolio(i, m, bars, ctx, scenario="S"):
    """B2.4: everything downstream unchanged. Invalid signals stay in B."""
    opps = draw_signals(i, m, bars, ctx.params.get("tp_rr_multiple", 2.0), ctx.spec.digits)
    return portfolio.run(opps, ctx, MODE, scenario)


def statistic(run):
    """B2.5: closed-trade mean R on the run's path, None with no resolved trade."""
    rs = [r["result"].r for r in run.population("C") if r["result"].closed_trade]
    return sum(rs) / len(rs) if rs else None


def benchmark(records, bars, ctx, fold, n=1000, scenarios=exits.SCENARIOS):
    """B2 for one fold: per portfolio {path: statistic}, and its realised C counts."""
    m = matching(records, bars, fold, ctx.delay)
    c = replace(ctx, data_end=folds.fold_end(fold, ctx))
    stats, counts = [], []
    for i in range(n):
        runs = {sc: run_portfolio(i, m, bars, c, sc) for sc in scenarios}
        stats.append({sc: statistic(runs[sc]) for sc in scenarios})
        counts.append({sc: len(runs[sc].population("C")) for sc in scenarios})
    return stats, counts
