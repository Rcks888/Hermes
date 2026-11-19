"""The two folds (PREREGISTRATION.md pr-0.4 F1-F4; section 9 test 2).

Each fold is one continuous SIMULATION starting flat. An opportunity belongs
to the fold holding the server-clock instant of its entry E (F2, D4). Every
trade is resolved with data_end at the fold's end, so a development trade
still open at the last development bar is OPEN with mtm_R at that bar and
never sees a holdout bar (F4); the seal (data.guard) refuses one anyway.

Module-level functions start_book and fold_end are the seams the tests patch
with broken variants.
"""
import calendar
from dataclasses import replace

from replay import clock, data, portfolio

SPAN_START = calendar.timegm((2022, 6, 23, 0, 0, 0))
DEVELOPMENT = (SPAN_START, data.HOLDOUT[0])
HOLDOUT = data.HOLDOUT


def in_fold(e, fold):
    return fold[0] <= e < fold[1]


def start_book(fold):
    """F4: every fold starts flat."""
    return portfolio.Book()


def fold_end(fold, ctx):
    """F4: no trade of a fold is resolved past its end."""
    last = int(ctx.m1.time[-1]) + data.M1 if len(ctx.m1) else fold[1]
    return min(fold[1], last if ctx.data_end is None else ctx.data_end)


def strategy_opportunities(ctx, fold, cache=None):
    """C2.1 over the whole fold: every M15 bar whose E is in the fold and whose
    D is not off a UTC weekday is scanned, in time order, and every signal is
    kept. Also returns the evaluated bars (C1.7) as (bar_open, close, ATR(14))
    for B2. cache: bar_open -> pipeline result, shared across delays."""
    cache = {} if cache is None else cache
    opps, bars = [], []
    for b in (int(t) for t in ctx.m15.time):
        oc = clock.opportunity_clock(b, ctx.delay, ctx.window)
        if not in_fold(oc["E"], fold) or oc["evaluated"] is False:
            continue
        if b not in cache:
            cache[b] = portfolio.signal_at(ctx.m15, b, ctx.params, ctx.permit)
        res = cache[b]
        if oc["evaluated"]:
            last = res["candles"].iloc[-1]
            bars.append((b, float(last["close"]), float(last["atr"])))
        if res["signal"]:
            opps.append({"bar_open": b, "signal": res["signal"]})
    return opps, bars


def run_fold(opps, ctx, fold, scenario="S", permit=None):
    """One fold, one path: the opportunities with E in the fold, from flat."""
    chosen = [o for o in opps if in_fold(portfolio.entry_time_of(o, ctx), fold)]
    c = replace(ctx, data_end=fold_end(fold, ctx), permit=permit)
    return portfolio.run(chosen, c, portfolio.SIMULATION, scenario, book=start_book(fold))
