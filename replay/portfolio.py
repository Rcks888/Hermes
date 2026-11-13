"""Portfolio: modes, the risk gate, event ordering and the three scenario paths
(contract C2.1, C2.4-C2.6, C4.3-C4.5, C5.9).

An opportunity is one M15 signal bar with the strategy's signal dict, as
produced by signal_at (engine.pipeline on the sealed strategy window, C2.1).
Opportunities are decided in order of their entry instant E. At E the gate
sees only exits with X <= E (C2.5): a trade frees the slot iff X <= E, and the
daily loss count is the number of losses with X <= E on the server date of E.
CONFORMANCE sees no open trades and no losses (C2.4, live alert-only state);
SIMULATION sees its own book. The gate then runs risk_manager.approve's
checks in approve's order, on replay state instead of trades.json and MT5
positions; the filters and sizing are risk_manager's own functions.

A trade's whole path is resolved on entry (replay.exits), but the book only
ever reads it through X <= E, so no decision depends on an exit that is not
yet booked (C2.6).

Module-level functions frees_slot, counts_as_loss, gate_state, within_cap,
cap_ok and verdicts_agree are the seams the contract tests patch with broken
variants.
"""
import time
from dataclasses import dataclass, field

from engine import pipeline, risk_manager
from replay import clock, data, entry, exits, spread

CONFORMANCE, SIMULATION = "CONFORMANCE", "SIMULATION"
BALANCE = 100000.0
PU_CAP = 0.01
HOLD = "HOLD"


def server_date(t):
    return time.gmtime(int(t))[:3]


def frees_slot(x, e):
    """C2.5(a): a trade frees the slot iff X <= E."""
    return x is not None and x <= e


def counts_as_loss(position, e):
    """C2.5(b): a loss with X <= E counts on the server date of X, its exit."""
    res = position.result
    return res.is_loss and frees_slot(res.exit_time, e) and server_date(res.exit_time) == server_date(e)


@dataclass
class Position:
    entry_time: int
    result: exits.Result
    margin: float


@dataclass
class Book:
    positions: list = field(default_factory=list)

    def open_at(self, e):
        return [p for p in self.positions if not frees_slot(p.result.exit_time, e)]

    def losses_on(self, e):
        return sum(1 for p in self.positions if counts_as_loss(p, e))


def gate_state(book, e, mode):
    """(open positions, daily loss count) the gate sees at E (C2.4)."""
    if mode == CONFORMANCE:
        return [], 0
    return book.open_at(e), book.losses_on(e)


def risk_gate(signal, spread_points, session, params, symbol_info, open_count, daily_losses,
              balance=BALANCE):
    """risk_manager.approve on replay state: (approved, reason, details)."""
    if daily_losses >= params.get("daily_loss_limit", 3):
        return False, "Daily loss limit reached", {}
    if open_count >= params.get("max_open_trades", 1):
        return False, "Max open trades reached", {}
    reasons = risk_manager.check_no_trade_filters(signal, spread_points, params, session)
    if reasons:
        return False, "; ".join(reasons), {}
    lots, why = risk_manager.calculate_position_size(signal, balance, params, symbol_info)
    if why:
        return False, why, {}
    return True, "Approved", {"position_size": lots,
                              "risk_amount": round(balance * params.get("risk_pct", 0.01), 2),
                              "daily_losses": daily_losses}


def signal_at(m15, bar_open, params):
    """C2.1: the live pipeline on the 200 closed M15 bars ending at bar_open."""
    return pipeline.evaluate_closed_bars(data.strategy_window(m15, bar_open, params.get("candle_count", 200)),
                                         params)


@dataclass
class Context:
    m1: data.Bars
    m15: data.Bars
    sched: object
    model: spread.SpreadModel
    params: dict
    spec: entry.BrokerSpec
    delay: int = clock.BASELINE_DELAY
    window: tuple = clock.tc.OFFSET_UNCERTAIN
    data_end: int = None


def entry_time_of(opp, ctx):
    return clock.entry_instant(clock.decision_instant(opp["bar_open"], opp.get("delay", ctx.delay)))


def decide(opp, ctx, book, mode, scenario):
    """One opportunity at its E: clock, spread draw, gate, quote, execution,
    and for population C the resolved trade, added to the book."""
    sig = opp["signal"]
    oc = clock.opportunity_clock(opp["bar_open"], opp.get("delay", ctx.delay), ctx.window)
    rec = dict(oc, mode=mode, scenario=scenario, signal=sig)
    if oc["status"]:
        rec["population"] = "B"
        return rec
    if not oc["evaluated"]:
        rec["population"] = None
        return rec
    e = oc["E"]
    u, points, sess_e = spread.draw_for_bar(ctx.model, opp["bar_open"], e)
    held, losses = gate_state(book, e, mode)
    ok, reason, details = risk_gate(sig, points, oc["session"], ctx.params, ctx.spec.symbol_info,
                                    len(held), losses)
    rec.update(u=u, spread_points=points, spread_session=sess_e, open_trades=len(held),
               daily_losses=losses, approved=ok, reason=reason, risk_details=details)
    if not ok:
        rec["population"] = "A"
        return rec
    bid, detail = clock.entry_bid(ctx.m1, e, ctx.sched)
    sp = round(points * ctx.spec.point, ctx.spec.digits)
    quote = None if bid is None else entry.build_quote(bid, spread.ask_from(bid, points, ctx.spec.point,
                                                                           ctx.spec.digits))
    out = entry.execute(sig, quote, BALANCE, ctx.params, ctx.spec, open_margin=sum(p.margin for p in held))
    if bid is None:
        out["execution_validity_detail"] = detail
    rec.update(quote=quote, exec=out, population=entry.population(out))
    if rec["population"] == "C":
        lv = entry.levels(sig["direction"], bid, sig["sl"], sig["tp"], sp)
        trade = exits.Trade(sig["direction"], lv["fill"], float(sig["sl"]), float(sig["tp"]), sp, e)
        res = exits.resolve(trade, ctx.m1, ctx.m15, ctx.sched, scenario, ctx.data_end)
        rec.update(levels=lv, result=res)
        book.positions.append(Position(e, res, out["required_margin"]))
    return rec


@dataclass
class Run:
    mode: str
    scenario: str
    records: list

    def population(self, p):
        return [r for r in self.records if r.get("population") == p]

    def results(self):
        return [r["result"] for r in self.population("C")]


def run(opps, ctx, mode=SIMULATION, scenario="S"):
    """One complete path. Modes are never mixed in one run (C2.4)."""
    if mode not in (CONFORMANCE, SIMULATION):
        raise ValueError(mode)
    book = Book()
    order = sorted(opps, key=lambda o: (entry_time_of(o, ctx), o["bar_open"]))
    return Run(mode, scenario, [decide(o, ctx, book, mode, scenario) for o in order])


def run_paths(opps, ctx, mode=SIMULATION):
    """C5.9: the three complete paths S, T and C."""
    return {sc: run(opps, ctx, mode, sc) for sc in exits.SCENARIOS}


def within_cap(share):
    """C5.9: PATH_UNKNOWN at most 1% of the path's population C."""
    return share is not None and share <= PU_CAP


def cap_ok(rows):
    return all(within_cap(r["share"]) for r in rows.values())


def verdicts_agree(rows):
    return len({r["verdict"] for r in rows.values()}) == 1


def scenario_agreement(paths, verdict):
    """C5.9 P1 scenario agreement: (verdict or HOLD, per-path rows).
    `verdict(run)` is the P1 rule, defined by the registration, not here."""
    rows = {}
    for sc in exits.SCENARIOS:
        results = paths[sc].results()
        n, pu = len(results), exits.path_unknown_count(results)
        rows[sc] = {"n": n, "path_unknown": pu, "share": pu / n if n else None,
                    "verdict": verdict(paths[sc])}
    final = rows["S"]["verdict"] if cap_ok(rows) and verdicts_agree(rows) else HOLD
    return final, rows
