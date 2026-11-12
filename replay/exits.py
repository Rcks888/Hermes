"""Exit resolution on M1 bid bars (contract C5.1-C5.10, C2.6).

One trade, one scenario path (S, T or C of C5.9). The walk goes M15 slot by M15
slot from the entry minute E. Inside a slot, observed minutes at or after E are
resolved in order (C5.1, C5.3, C5.4). A scheduled minute absent from M1 at or
after E is a hole (C1.8). The first slot with a hole while the trade is still
open is B; nothing after its first hole is resolved minute by minute. B is
decided from its M15 range at K = B + 900 (C5.8), and every outcome decided
there is booked at K (C2.6). A hole is released at K under C5.8(c) or under
scenario C after PATH_UNKNOWN (C5.4); only then does the walk continue, from
the next slot. Every slot walked is checked against the holdout seal (F6).

Interpretations, to be confirmed in review (none changes a frozen clause):
- I1. A hole is a scheduled minute missing at or after E. A missing pre-entry
  minute of the entry bar is not a hole of the trade; it matters only through
  case (b), where it makes the entry-bar reach undetermined.
- I2. Case (b): a trigger reached by the entry bar's range counts as reached
  post-entry if an observed post-entry minute of B attains it, or if every
  pre-entry minute of B is observed and none attains B's extreme on that side.
- I3. Scenario C continues from the slot after B; observed minutes of B after
  its first hole are marked (C5.10) but not resolved, because C takes B to
  contain no touch.

Module-level functions is_hole, decision_time, releases and
path_unknown_count are the seams the contract tests patch with broken variants.
"""
from dataclasses import dataclass, field

from replay import data

M15, M1 = 900, 60
SCENARIOS = ("S", "T", "C")
UNRESOLVABLE = ("a", "b")


@dataclass
class Trade:
    direction: str
    fill: float
    sl: float
    tp: float
    spread: float
    entry_time: int

    @property
    def long(self):
        return self.direction == "LONG"

    @property
    def sl_trig(self):
        return self.sl if self.long else self.sl - self.spread

    @property
    def tp_trig(self):
        return self.tp if self.long else self.tp - self.spread

    def leg(self, bid):
        """Closing-leg price for a bid (C5.7): bid for LONG, bid + spread for SHORT."""
        return bid if self.long else bid + self.spread

    def r(self, leg):
        if self.long:
            return (leg - self.fill) / (self.fill - self.sl)
        return (self.fill - leg) / (self.sl - self.fill)

    def sl_hit(self, low, high):
        return low <= self.sl_trig if self.long else high >= self.sl_trig

    def tp_hit(self, low, high):
        return high >= self.tp_trig if self.long else low <= self.tp_trig

    def open_beyond_sl(self, o):
        return o <= self.sl_trig if self.long else o >= self.sl_trig

    def open_beyond_tp(self, o):
        return o >= self.tp_trig if self.long else o <= self.tp_trig

    def worse_sl_bid(self, o):
        return min(self.sl_trig, o) if self.long else max(self.sl_trig, o)

    def attains_extreme(self, m, bar, side):
        _, _, h, l, _ = m
        low_side = (side == "sl") == self.long
        return l <= bar["low"] if low_side else h >= bar["high"]


@dataclass
class Result:
    scenario: str
    outcome: str = "OPEN"
    exit_time: int = None
    exit_leg: float = None
    r: float = None
    mtm_r: float = None
    path_unknown: bool = False
    flags: set = field(default_factory=set)
    decisions: list = field(default_factory=list)
    hole_minutes: int = 0
    marks: list = field(default_factory=list)
    adverse_marks: list = field(default_factory=list)

    @property
    def is_loss(self):
        """A loss for the daily count (C2.5): SL_HIT, gap stops included, or a
        PATH_UNKNOWN stop under scenario S (C5.9)."""
        return self.outcome == "SL_HIT" or "S_STOP" in self.flags

    @property
    def closed_trade(self):
        """In closed-trade statistics (C5.6, C5.8): resolved, not PATH_UNKNOWN."""
        return self.outcome in ("TP_HIT", "SL_HIT") and not self.path_unknown


def is_hole(sched, t):
    """C1.8: an absent minute is a hole iff it is scheduled."""
    return sched.scheduled(t)


def decision_time(b0):
    """C5.8: B's range is known at its close K."""
    return b0 + M15


def releases(case, scenario):
    """C5.4: a hole is released by C5.8(c), or by scenario C after PATH_UNKNOWN."""
    return case == "c" or (case in UNRESOLVABLE and scenario == "C")


def path_unknown_count(results):
    """C5.4, C5.9: PATH_UNKNOWN is a property of the trade, counted once."""
    return sum(1 for r in results if r.path_unknown)


def minute(m1, t):
    i = m1.index(t)
    if i is None:
        return None
    return (t, float(m1.open[i]), float(m1.high[i]), float(m1.low[i]), float(m1.close[i]))


def _exit(res, trade, outcome, t, leg, flags=()):
    res.outcome = outcome
    res.exit_time = t
    res.exit_leg = leg
    res.r = trade.r(leg)
    res.flags |= set(flags)


def _mark(res, trade, m):
    t, _, h, l, c = m
    res.marks.append((t + M1, trade.r(trade.leg(c))))
    res.adverse_marks.append((t + M1, trade.r(trade.leg(l if trade.long else h))))


def _resolve_minute(res, trade, m):
    """C5.1, C5.3, C5.4 on one observed minute. True if the trade exits."""
    t, o, h, l, _ = m
    if t > trade.entry_time and trade.open_beyond_sl(o):
        _exit(res, trade, "SL_HIT", t + M1, trade.leg(o), ("gap",))
        return True
    if t > trade.entry_time and trade.open_beyond_tp(o):
        _exit(res, trade, "TP_HIT", t + M1, trade.tp, ("gap",))
        return True
    s, p = trade.sl_hit(l, h), trade.tp_hit(l, h)
    if s:
        _exit(res, trade, "SL_HIT", t + M1, trade.sl, ("AMBIGUOUS",) if p else ())
        return True
    if p:
        _exit(res, trade, "TP_HIT", t + M1, trade.tp)
        return True
    _mark(res, trade, m)
    return False


def _classify_b(trade, m1, m15, sched, b0, post):
    """C5.8 case of hole bar B: 'a' | 'b' | 'c' | 'd' | 'e'."""
    bar = m15.bar(b0)
    if bar is None:
        return "a", None
    observed = data.minutes_of(m1, b0)
    if not data.envelope_valid(bar, observed):
        return "a", bar
    reached = {"sl": trade.sl_hit(bar["low"], bar["high"]), "tp": trade.tp_hit(bar["low"], bar["high"])}
    if b0 <= trade.entry_time:
        pre = [m for m in observed if m[0] < trade.entry_time]
        pre_complete = len(pre) == sum(1 for t in range(b0, trade.entry_time, M1) if sched.scheduled(t))
        hit = {"sl": trade.sl_hit, "tp": trade.tp_hit}
        for side in ("sl", "tp"):
            if not reached[side]:
                continue
            by_observed = any(hit[side](m[3], m[2]) for m in post)
            by_envelope = pre_complete and not any(trade.attains_extreme(m, bar, side) for m in pre)
            if not (by_observed or by_envelope):
                return "b", bar
    n = reached["sl"] + reached["tp"]
    return "cde"[n], bar


def _decide_b(res, trade, m1, m15, sched, b0, post, first_hole, scenario):
    """C5.8 and C5.9 for hole bar B: 'exit' | 'continue' | 'held'."""
    k = decision_time(b0)
    case, bar = _classify_b(trade, m1, m15, sched, b0, post)
    res.decisions.append((b0, k, case))
    if case == "b":
        res.flags.add("m1_hole_entry_bar")
    if case in ("d", "e"):
        res.flags.add("m1_hole")
        if case == "e":
            res.flags.add("AMBIGUOUS")
        if trade.sl_hit(bar["low"], bar["high"]):
            touch = [m for m in post if m[0] > first_hole and trade.sl_hit(m[3], m[2])]
            bid = trade.worse_sl_bid(touch[0][1]) if touch else trade.sl_trig
            _exit(res, trade, "SL_HIT", k, trade.leg(bid))
        else:
            _exit(res, trade, "TP_HIT", k, trade.tp)
        return "exit"
    if case in UNRESOLVABLE:
        res.path_unknown = True
        if scenario == "S":
            _exit(res, trade, "PATH_UNKNOWN", k, trade.sl, ("S_STOP",))
            return "exit"
        if scenario == "T":
            _exit(res, trade, "PATH_UNKNOWN", k, trade.tp, ("T_TARGET",))
            return "exit"
    return "continue" if releases(case, scenario) else "held"


def resolve(trade, m1, m15, sched, scenario="S", data_end=None):
    """Resolve one trade on one scenario path. data_end: exclusive server time
    after the last M1 bar of the snapshot (C5.6); defaults to the last bar + 60."""
    if scenario not in SCENARIOS:
        raise ValueError(scenario)
    data_end = int(m1.time[-1]) + M1 if data_end is None else data_end
    res = Result(scenario)
    slot = trade.entry_time - trade.entry_time % M15
    while slot < data_end:
        data.guard(slot, slot + M15)
        post, first_hole = [], None
        for t in range(max(slot, trade.entry_time), min(slot + M15, data_end), M1):
            m = minute(m1, t)
            if m is None:
                if is_hole(sched, t):
                    res.hole_minutes += 1
                    first_hole = t if first_hole is None else first_hole
                continue
            if first_hole is None:
                if _resolve_minute(res, trade, m):
                    return res
            else:
                _mark(res, trade, m)
            post.append(m)
        if first_hole is not None:
            if slot + M15 > data_end:
                break
            step = _decide_b(res, trade, m1, m15, sched, slot, post, first_hole, scenario)
            if step == "exit":
                return res
            if step == "held":
                break
        slot += M15
    res.mtm_r = res.marks[-1][1] if res.marks else None
    return res
