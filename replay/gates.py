"""P1 development gates and the uncertainty rule (PREREGISTRATION.md pr-0.4
F5, G1-G9, B2.5, U1-U3).

Nothing here reads bars. The evaluator takes, per C5.9 path, the closed
trades and the C5.10 equity curve of the strategy run, plus the random
portfolios' statistic and the sensitivity means read on that same path (G9),
and returns gate results and the P1 development verdict. It is exercised on
constructed inputs only (pr-0.4 section 9 tests 3 and 4) until pr-1.0.

Interpretations, to be confirmed in review (none changes a frozen clause):
- J1. U2 "resamples drawn in one call in month order": one
  rng.integers(0, M, size=(10000, M)) call; row i is resample i, its entries
  index the fold's months in calendar order.
- J2. A path's verdict is HOLD if any gate gives HOLD (G1 below 100 trades,
  the U2 or B2.5 drop rule), else FAIL if any gate fails, else PASS. PD2: a
  thin sample is never FAIL.
- J3. Profit factor with no losing trade is infinite and passes G3.
- J4. The equity curve is 0 at the fold start (F4, flat); drawdown is
  measured from a running peak that starts at 0.
- J5. G5 concentration: a segment R strictly above 50% of final equity fails;
  exactly 50% passes. G6: strictly above the rank value.
- J6. F5 names the last segment, 2025-01-01 to 2025-08-31, but states a merge
  rule only for a short first segment. The fold ends inside H2 2025, so a
  last segment shorter than 3 months (Jul-Aug 2025) merges into the previous
  one; this reproduces F5's six segments.

Module-level functions month_totals, lower_quantile, bootstrap, equity_curve,
value_at, concentration_ok, combine and portfolio_stat, and the constant
MIN_TRADES, are the seams the tests patch with broken variants.
"""
import bisect
import calendar
import math
import time
from dataclasses import dataclass, field

import numpy as np

from replay import clock, exits, portfolio

PASS, FAIL, HOLD = "PASS", "FAIL", portfolio.HOLD
N_RESAMPLES = 10000
SEED = 20261023
DROP_CAP = 0.01
ALPHA_BASELINE, ALPHA_VARIANT = 0.025, 0.003125
MIN_TRADES = 100
MIN_PF = 1.30
MAX_DD = 20.0
CONCENTRATION = 0.5
SENSITIVITY_DELAYS = tuple(d for d in clock.PREREGISTERED_DELAYS if d != clock.BASELINE_DELAY)
GATES = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")


def month_of(t):
    g = time.gmtime(int(t))
    return g.tm_year, g.tm_mon


def _next_month(y, m):
    return (y + 1, 1) if m == 12 else (y, m + 1)


def fold_months(start, end):
    """Calendar months of the fold [start, end), by server date, in order (U1)."""
    out, ym, last = [], month_of(start), month_of(end - 1)
    while ym <= last:
        out.append(ym)
        ym = _next_month(*ym)
    return out


def month_totals(trades, months):
    """(total R, trade count) per fold month; months with no trade stay (U1)."""
    pos = {mo: i for i, mo in enumerate(months)}
    r, n = np.zeros(len(months)), np.zeros(len(months))
    for e, x in trades:
        i = pos.get(month_of(e))
        if i is None:
            raise ValueError(f"trade at {e} outside the fold")
        r[i] += x
        n[i] += 1
    return r, n


def draw_indices(rng, m):
    return rng.integers(0, m, size=(N_RESAMPLES, m))


def lower_quantile(stats, alpha):
    return float(np.quantile(stats, alpha, method="inverted_cdf"))


def bootstrap(trades, months, alpha):
    """U1, U2: month-cluster bootstrap of total R / total trades."""
    r, n = month_totals(trades, months)
    idx = draw_indices(np.random.default_rng(SEED), len(r))
    tr, tn = r[idx].sum(axis=1), n[idx].sum(axis=1)
    keep = tn > 0
    dropped = int((~keep).sum())
    lower = lower_quantile(tr[keep] / tn[keep], alpha) if keep.any() else None
    return {"lower": lower, "dropped": dropped, "retained": int(keep.sum()),
            "hold": dropped > DROP_CAP * N_RESAMPLES or lower is None}


def equity_curve(results):
    """C5.10 marks [(t, R)]: realised R plus the floating R of open trades, at
    every mark and every exit. Results are replay.exits.Result objects."""
    events = []
    for k, res in enumerate(results):
        events += [(t, 0, k, v) for t, v in res.marks]
        if res.exit_time is not None and res.r is not None:
            events.append((res.exit_time, 1, k, res.r))
    events.sort(key=lambda ev: (ev[0], ev[1]))
    realised, floating, curve = 0.0, {}, []
    for t, is_exit, k, v in events:
        if is_exit:
            realised += v
            floating.pop(k, None)
        else:
            floating[k] = v
        curve.append((t, realised + sum(floating.values())))
    return curve


def closed_trades(run):
    """[(E, R)] of a portfolio.Run's closed trades (C5.6)."""
    return [(r["E"], r["result"].r) for r in run.population("C") if r["result"].closed_trade]


def value_at(curve, t):
    """F5: the curve's last mark at or before t; 0 before the first (J4)."""
    i = bisect.bisect_right([c[0] for c in curve], t)
    return curve[i - 1][1] if i else 0.0


def max_drawdown(curve):
    peak = dd = 0.0
    for _, v in curve:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd


def _add_months(t, k):
    g = time.gmtime(int(t))
    y, m = g.tm_year, g.tm_mon
    for _ in range(k):
        y, m = _next_month(y, m)
    d = min(g.tm_mday, calendar.monthrange(y, m)[1])
    return calendar.timegm((y, m, d, g.tm_hour, g.tm_min, g.tm_sec))


def segments(start, end):
    """F5: calendar half-years of [start, end); a first segment shorter than
    3 months merges into the next, a last one into the previous (J6)."""
    cuts, (y, m) = [start], month_of(start)
    while True:
        y, m = (y, 7) if m < 7 else (y + 1, 1)
        b = calendar.timegm((y, m, 1, 0, 0, 0))
        if b >= end:
            break
        cuts.append(b)
    cuts.append(end)
    segs = list(zip(cuts[:-1], cuts[1:]))
    if len(segs) > 1 and segs[0][1] < _add_months(start, 3):
        segs[:2] = [(segs[0][0], segs[1][1])]
    if len(segs) > 1 and _add_months(segs[-1][0], 3) > end:
        segs[-2:] = [(segs[-2][0], segs[-1][1])]
    return segs


def segment_r(curve, segs):
    return [value_at(curve, b) - value_at(curve, a) for a, b in segs]


def mean_r(trades):
    return sum(r for _, r in trades) / len(trades) if trades else None


def profit_factor(trades):
    won = sum(r for _, r in trades if r > 0)
    lost = -sum(r for _, r in trades if r < 0)
    if lost == 0:
        return math.inf if won > 0 else None
    return won / lost


def concentration_ok(rs, final):
    return max(rs) <= CONCENTRATION * final


def benchmark_rank(m):
    """G6: type-1 rank ceil(0.95 * M), in integers."""
    return -(-95 * m // 100)


def portfolio_stat(p, sc):
    """G9: a random portfolio's B2.5 statistic read on path sc."""
    return p[sc]


def combine(results):
    """J2."""
    vals = set(results.values())
    return HOLD if HOLD in vals else FAIL if FAIL in vals else PASS


@dataclass
class Path:
    """One C5.9 path of the strategy run."""
    trades: list
    curve: list
    population_c: int
    path_unknown: int = 0


@dataclass
class Attempt:
    """One attempt on the development fold (M4). benchmark: one dict per
    random portfolio, {path: statistic or None if dropped}; delay_means:
    {delay: {path: mean R}}; p90_means: {path: mean R}."""
    fold: tuple
    paths: dict
    benchmark: list
    delay_means: dict
    p90_means: dict
    alpha: float = ALPHA_BASELINE
    preconditions: dict = field(default_factory=dict)


def evaluate(attempt, sc):
    """G1-G8 on path sc (G9: every quantity read on that path)."""
    p = attempt.paths[sc]
    tr, curve, (start, end) = p.trades, p.curve, attempt.fold
    g, info = {}, {"n": len(tr), "mean_r": mean_r(tr)}
    g["G1"] = PASS if len(tr) >= MIN_TRADES else HOLD
    boot = bootstrap(tr, fold_months(start, end), attempt.alpha) if tr else None
    info["bootstrap"] = boot
    g["G2"] = HOLD if boot is None or boot["hold"] else PASS if boot["lower"] > 0 else FAIL
    pf = info["profit_factor"] = profit_factor(tr)
    g["G3"] = PASS if pf is not None and pf >= MIN_PF else FAIL
    dd = info["max_drawdown"] = max_drawdown(curve)
    g["G4"] = PASS if dd <= MAX_DD else FAIL
    rs = info["segment_r"] = segment_r(curve, segments(start, end))
    final = info["final_equity"] = value_at(curve, end)
    need = -(-2 * len(rs) // 3)
    g["G5"] = PASS if sum(r > 0 for r in rs) >= need and final > 0 and concentration_ok(rs, final) else FAIL
    stats = [portfolio_stat(b, sc) for b in attempt.benchmark]
    kept = sorted(s for s in stats if s is not None)
    info["benchmark_dropped"] = len(stats) - len(kept)
    if not kept or len(stats) - len(kept) > DROP_CAP * len(stats):
        g["G6"] = HOLD
    else:
        thr = info["benchmark_threshold"] = kept[benchmark_rank(len(kept)) - 1]
        g["G6"] = PASS if info["mean_r"] is not None and info["mean_r"] > thr else FAIL
    missing = [d for d in SENSITIVITY_DELAYS if d not in attempt.delay_means]
    if missing:
        raise ValueError(f"G7 needs every registered delay, missing {missing}")
    g["G7"] = PASS if all(attempt.delay_means[d][sc] > 0 for d in SENSITIVITY_DELAYS) else FAIL
    g["G8"] = PASS if attempt.p90_means[sc] > 0 else FAIL
    return {"verdict": combine(g), "gates": g, **info}


def p1_development(attempt):
    """G9 and the preconditions: (verdict, per-path rows). The headline is S."""
    rows = {}
    for sc in exits.SCENARIOS:
        p, ev = attempt.paths[sc], evaluate(attempt, sc)
        share = p.path_unknown / p.population_c if p.population_c else None
        rows[sc] = dict(ev, share=share)
    ok = all(attempt.preconditions.get(k) for k in ("ec_1_0", "conformant", "c6_3", "identity"))
    final = (rows["S"]["verdict"] if ok and portfolio.cap_ok(rows) and portfolio.verdicts_agree(rows)
             else HOLD)
    return final, rows
