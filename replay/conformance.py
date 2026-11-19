"""Conformance, Level E and the verdict rule (EXECUTION_CONTRACT.md section 8).

Function parity (E0) is exact, on the live logged inputs. Model agreement
(E1-E4) compares the replay's proxy inputs with the live cycle's. A Level 1-4
mismatch, any E0 field mismatch, an E1 or E2 breach (E2 once TBD-4 is frozen),
E2 not assessed once TBD-4 is frozen, or any E3_UNATTRIBUTED makes the replay
NOT CONFORMANT, and historical performance reporting is then prohibited.

E2 [v2, proposed with TBD-4 in the ec-1.0 proposal v2, not yet frozen]: two
conditions on two populations. (a) Every E0-eligible signal cycle, with no
calibration exclusion applied: |diff| <= E2_SIGNAL_FACTOR x T. (b) The
forward population, cycles after the calibration cutoff selected as for
calibration: at least E2_MIN_FORWARD of them, and at most E2_RATE of them
with |diff| > T. Fewer is "E2 not assessed", never a pass. An eligible signal
cycle with no replay bid at E is a breach of (a); a forward cycle without one
is counted and left out of n. Differences and T are compared in integer
cents.

Built on synthetic inputs and the signal fixture only. The real report, on
the VPS events.jsonl against an export covering the live period, follows when
TBD-3 and TBD-4 are frozen.

Interpretations, to be confirmed in review (none changes a frozen clause):
- P1. Signal cycles are SIGNAL_APPROVED and SIGNAL_REJECTED events. The 0g-1
  execution fields are `exec_observation` with `quote` and `balance`.
- P2. Event bar_time is the server wall clock labelled +00:00 (7 Oct: bar
  17:30, quote_broker_time 17:53:05 server, received 14:53:05 UTC). E1 is
  taken in that clock: replay E at the baseline delay against
  bar_close + scheduling_delay_seconds.
- P3. E3 statuses come from propose_execution with the C4.4 calculator, the
  function live logs from, so only the swapped inputs differ. A disagreement
  removed by a single swap is attributed to that input. "bid|spread" means
  that EACH single swap removes it independently. One removed only when both
  inputs are swapped together is E3_UNATTRIBUTED (section 8 rejects joint
  attribution).
- P4. E4 compares integer points, live quote_spread_price / point rounded,
  with the model's per-session points; type-1 quantiles, no pass rule.

Module-level functions eligible, first_eligible and status_of are the seams
the tests patch with broken variants.
"""
import calendar
from datetime import datetime
from fractions import Fraction

from engine import entry_observation as eo
from replay import clock, entry, spread

SIGNAL_EVENTS = ("SIGNAL_APPROVED", "SIGNAL_REJECTED")
E1_LIMIT = 60
E3_UNATTRIBUTED = "E3_UNATTRIBUTED"
E2_SIGNAL_FACTOR = 2
E2_RATE = Fraction(2, 100)
E2_MIN_FORWARD = 400
LEVEL2_FIELDS = ("cannot_evaluate", "blocked_at", "direction", "entry", "sl", "tp", "reason")
QUANTILES = (0.10, 0.50, 0.90)


def bar_open_of(ev):
    """P2: the server epoch of the event's bar_time."""
    dt = datetime.fromisoformat(ev["bar_time"])
    return calendar.timegm(dt.timetuple())


def live_signal(ev):
    return {"direction": ev["direction"], "entry": ev["entry"], "sl": ev["sl"], "tp": ev["tp"],
            "symbol": "XAUUSD"}


def eligible(ev):
    """P1: the event carries the 0g-1 execution fields."""
    return bool(ev.get("exec_observation")) and bool(ev.get("quote")) and ev.get("balance") is not None


def first_eligible(sig):
    """Section 8: eligibility starts at the first event carrying the fields."""
    return next((i for i, e in enumerate(sig) if eligible(e)), len(sig))


def e0(events, params, spec):
    """E0: ineligible signal cycles before the first eligible one are listed;
    every later signal cycle must be eligible and match on every field."""
    sig = [e for e in events if e.get("event") in SIGNAL_EVENTS]
    first = first_eligible(sig)
    calc = spec.margin_calculator()
    rows = []
    for ev in sig[first:]:
        if not eligible(ev):
            rows.append({"bar_time": ev["bar_time"], "ok": False, "diff": "missing 0g-1 execution fields"})
            continue
        got = eo.propose_execution(live_signal(ev), ev["quote"], ev["balance"], params,
                                   spec.symbol_info, mt5=calc)
        want = ev["exec_observation"]
        diff = {k: (want[k], got.get(k)) for k in want if want[k] != got.get(k)}
        rows.append({"bar_time": ev["bar_time"], "ok": not diff, "diff": diff})
    return {"ineligible": [e["bar_time"] for e in sig[:first]], "rows": rows,
            "pass": all(r["ok"] for r in rows)}


def e1(events, delay=clock.BASELINE_DELAY):
    """E1: |replay E - (bar_close + scheduling_delay_seconds)| <= 60 s."""
    rows = []
    for ev in events:
        if ev.get("scheduling_delay_seconds") is None or not ev.get("bar_time"):
            continue
        b0 = bar_open_of(ev)
        e = clock.entry_instant(clock.decision_instant(b0, delay))
        diff = e - (clock.bar_close(b0) + float(ev["scheduling_delay_seconds"]))
        rows.append({"bar_time": ev["bar_time"], "diff": round(diff, 3), "ok": abs(diff) <= E1_LIMIT})
    return {"rows": rows, "pass": all(r["ok"] for r in rows)}


def _type1(sorted_xs, p):
    return sorted_xs[spread.rank(p, len(sorted_xs)) - 1] if sorted_xs else None


def summary(xs):
    xs = sorted(xs)
    return {"n": len(xs), **{f"p{int(p * 100)}": _type1(xs, p) for p in QUANTILES},
            "max_abs": max((abs(x) for x in xs), default=None)}


def cents(x):
    return int(round(abs(x) * 100))


def _diffs(pairs):
    return ([(t, round(r - lv, 2)) for t, r, lv in pairs if r is not None and lv is not None],
            [t for t, r, lv in pairs if r is None or lv is None])


def e2(signal_pairs, forward_pairs, tolerance=None):
    """E2 [v2]: replay bid minus live quote_bid, pairs (bar_time, replay_bid,
    live_bid). signal_pairs: every E0-eligible signal cycle; forward_pairs:
    the forward population. Before TBD-4 (tolerance None) it is reported only:
    never a breach and never assessed."""
    sig, sig_missing = _diffs(signal_pairs)
    fwd, fwd_missing = _diffs(forward_pairs)
    out = {"signal": summary([d for _, d in sig]), "forward": summary([d for _, d in fwd]),
           "missing": {"signal": sig_missing, "forward": fwd_missing}, "tolerance": tolerance}
    if tolerance is None:
        return dict(out, signal_breaches=[], forward_n=len(fwd), forward_exceed=None,
                    assessed=False, **{"pass": True})
    t = cents(tolerance)
    sig_breaches = [(b, d) for b, d in sig if cents(d) > E2_SIGNAL_FACTOR * t] + [(b, None) for b in sig_missing]
    exceed = sum(cents(d) > t for _, d in fwd)
    assessed = len(fwd) >= E2_MIN_FORWARD
    rate_breach = assessed and Fraction(exceed, len(fwd)) > E2_RATE
    return dict(out, signal_breaches=sig_breaches, forward_n=len(fwd), forward_exceed=exceed,
                assessed=assessed, **{"pass": not sig_breaches and not rate_breach})


def status_of(ev, bid, spread_price, params, spec):
    """P3: propose_execution's status for the event's signal at (bid, spread)."""
    quote = entry.build_quote(bid, round(bid + spread_price, spec.digits))
    out = eo.propose_execution(live_signal(ev), quote, ev["balance"], params, spec.symbol_info,
                               mt5=spec.margin_calculator())
    return out["execution_validity_status"]


def e3(cases, params, spec):
    """E3: cases are (event, proxy_bid, model_spread_price) per eligible
    signal cycle. Each disagreement is attributed by one swap at a time."""
    rows = []
    for ev, proxy_bid, model_sp in cases:
        live = ev["exec_observation"]["execution_validity_status"]
        lb, ls = ev["quote"]["quote_bid"], ev["quote"]["quote_spread_price"]
        rep = status_of(ev, proxy_bid, model_sp, params, spec)
        row = {"bar_time": ev["bar_time"], "live": live, "replay": rep, "agree": rep == live}
        if not row["agree"]:
            by = [name for name, (b, s) in (("bid", (lb, model_sp)), ("spread", (proxy_bid, ls)))
                  if status_of(ev, b, s, params, spec) == live]
            row["attribution"] = "|".join(by) if by else E3_UNATTRIBUTED
        rows.append(row)
    n = len(rows)
    return {"rows": rows, "agreement": (sum(r["agree"] for r in rows) / n) if n else None,
            "unattributed": [r["bar_time"] for r in rows if r.get("attribution") == E3_UNATTRIBUTED],
            "pass": not any(r.get("attribution") == E3_UNATTRIBUTED for r in rows)}


def e4(events, model, point=0.01):
    """E4: live decision-time spread points vs the model, by session."""
    live = {}
    for ev in events:
        q = ev.get("quote") or {}
        if q.get("quote_spread_price") is not None and ev.get("session"):
            live.setdefault(ev["session"], []).append(int(round(q["quote_spread_price"] / point)))
    return {s: {"live": summary(live.get(s, [])), "model": summary(model.by_session.get(s, []))}
            for s in sorted(set(live) | set(model.by_session))}


def level(pairs, fields=LEVEL2_FIELDS):
    """Levels 1-3, event by event: pairs of (live, replay) field dicts; a
    field present on either side must be equal."""
    rows = []
    for live, rep in pairs:
        diff = {f: (live.get(f), rep.get(f)) for f in fields
                if (f in live or f in rep) and live.get(f) != rep.get(f)}
        if diff:
            rows.append({"bar_time": live.get("bar_time"), "diff": diff})
    return {"mismatches": rows, "pass": not rows}


def verdict(levels, e0r, e1r, e2r, e3r):
    """Section 8: the reasons the replay is NOT CONFORMANT, or none."""
    why = [f"Level {k} mismatch" for k, r in sorted(levels.items()) if not r["pass"]]
    why += ["E0 field mismatch"] * (not e0r["pass"])
    why += ["E1 breach"] * (not e1r["pass"])
    why += ["E2 breach"] * (not e2r["pass"])
    why += ["E2 not assessed"] * (e2r.get("tolerance") is not None and not e2r.get("assessed"))
    why += [E3_UNATTRIBUTED] * (not e3r["pass"])
    return {"verdict": "NOT CONFORMANT" if why else "CONFORMANT", "reasons": why,
            "historical_reporting": "prohibited" if why else "permitted by section 8"}
