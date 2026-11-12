#!/usr/bin/env python3
"""EXECUTION_CONTRACT.md ec-0.3 section 9: preregistered contract tests.

Each test is named test_NN_ after its section 9 number. VARIANTS maps a test to
the broken variants it must catch; `--controls` runs every test under each of
its variants and requires a failure (contract section 9: "must fail on a
deliberately broken variant, demonstrated once").

Fixtures are synthetic bars or logged live events, never fold data, so no
historical SIMULATION result is produced (PREREGISTRATION.md pr-0.4).
Tests needing tests/fixtures/live_signal_events.jsonl report PENDING until it
exists; PENDING is not a pass.

Built so far: 1, 2, 4, 5 (clock part), 6, 7, 8, 14, 15, 17, 27, 28, and the
holdout seal of pr-0.4 test 1 (strategy window only).
"""
import calendar
import json
import math
import sys
from datetime import date
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import trading_calendar as tc  # noqa: E402
from engine import entry_observation as eo  # noqa: E402
from engine.pipeline import get_session_tag  # noqa: E402
from replay import clock, data, entry, spread  # noqa: E402

PARAMS = json.loads((ROOT / "config" / "strategy_params.json").read_text())
FIXTURE = ROOT / "tests" / "fixtures" / "live_signal_events.jsonl"
SPEC = entry.BrokerSpec.load()


class Pending(Exception):
    """A required fixture is not present yet."""


def srv(y, m, d, hh=0, mm=0):
    """Server-clock epoch of a wall-clock server time."""
    return calendar.timegm((y, m, d, hh, mm, 0))


def live_events():
    if not FIXTURE.exists():
        raise Pending(f"{FIXTURE.relative_to(ROOT)} not present")
    return [json.loads(x) for x in FIXTURE.read_text().splitlines() if x.strip()]


def live_signal(ev):
    return {"direction": ev["direction"], "entry": ev["entry"], "sl": ev["sl"],
            "tp": ev["tp"], "symbol": "XAUUSD"}


def event_at(events, bar_time):
    hits = [e for e in events if e.get("bar_time") == bar_time]
    if not hits:
        raise Pending(f"no logged event for bar {bar_time}")
    return hits[-1]


# ---------------------------------------------------------------- group A

def test_01_decision_instant_and_entry_reads_open():
    bar = srv(2025, 6, 3, 10, 0)
    for delay in clock.PREREGISTERED_DELAYS:
        o = clock.opportunity_clock(bar, delay)
        assert o["D"] == bar + 900 + delay, (delay, o["D"])
        assert o["E"] == o["D"], "every preregistered delay gives E = D (C1.6)"
    e = bar + 900 + clock.BASELINE_DELAY
    m1 = data.Bars.from_rows(60, [(e, 10.0, 30.0, 5.0, 20.0)])
    sched = clock.schedule(date(2025, 6, 2), date(2025, 6, 4))
    assert clock.entry_bid(m1, e, sched) == (10.0, None), "price at E is the open of the M1 bar stamped E"


def test_02_missing_entry_minute_is_quote_unavailable():
    bar = srv(2025, 6, 3, 10, 0)
    e = clock.entry_instant(clock.decision_instant(bar, 480))
    m1 = data.Bars.from_rows(60, [(e - 60, 3300.0, 3301.0, 3299.0, 3300.5),
                                  (e + 60, 3302.0, 3303.0, 3301.0, 3302.5)])
    sched = clock.schedule(date(2025, 6, 2), date(2025, 6, 4))
    bid, detail = clock.entry_bid(m1, e, sched)
    assert bid is None and detail == "m1_minute_unavailable", (bid, detail)
    sig = {"direction": "LONG", "entry": 3300.0, "sl": 3290.0, "tp": 3320.0, "symbol": "XAUUSD"}
    quote = None if bid is None else entry.build_quote(bid, bid + 0.3)
    out = entry.execute(sig, quote, 100000.0, PARAMS, SPEC)
    assert out["execution_validity_status"] == "quote_unavailable"
    assert out["proposed_executable_entry"] is None, "no fabricated price"


def test_04_session_parity_at_boundaries():
    expect = {(6, 59): "ASIA", (7, 0): "LONDON", (11, 59): "LONDON",
              (12, 0): "NY", (20, 59): "NY", (21, 0): "OFF"}
    for day, off in ((date(2025, 6, 3), 3), (date(2025, 12, 2), 2)):
        for (h, m), want in expect.items():
            utc = calendar.timegm((day.year, day.month, day.day, h, m, 0))
            d = utc + 3600 * off
            got = clock.session_of(d)
            assert got == want == get_session_tag(h), (day, h, m, got, want)


def test_05_offset_rule_and_uncertain_window():
    cases = [((2025, 3, 28), 2), ((2025, 3, 31), 3), ((2025, 10, 24), 3), ((2025, 10, 27), 2),
             ((2026, 3, 27), 2), ((2026, 3, 30), 3), ((2026, 10, 23), 3), ((2026, 10, 26), 2)]
    for (y, m, d), off in cases:
        t = srv(y, m, d, 12, 0)
        assert clock.server_to_utc(t) == t - 3600 * off, ((y, m, d), off)
    for bar in (srv(2026, 3, 10, 14, 0), srv(2026, 3, 10, 16, 0)):
        o = clock.opportunity_clock(bar, 480)
        assert o["detail"] == "offset_uncertain" and o["status"] == "quote_unavailable", o
        assert o["session"] is None and o["D_utc_if_utc2"] - o["D_utc_if_utc3"] == 3600
    same = clock.opportunity_clock(srv(2026, 3, 10, 16, 0), 480)
    assert get_session_tag(time_hour(same["D_utc_if_utc2"])) == get_session_tag(time_hour(same["D_utc_if_utc3"])), \
        "fixture must include a bar where both offsets give the same session"
    assert clock.opportunity_clock(srv(2026, 3, 12, 9, 0), 480)["detail"] is None
    syn = clock.schedule(date(2025, 3, 10), date(2025, 3, 14), window=(srv(2025, 3, 11), srv(2025, 3, 13)))
    assert syn.scheduled(srv(2025, 3, 11, 23, 30)), "23:30 is scheduled under UTC+3 only"
    assert syn.scheduled(srv(2025, 3, 12, 0, 30)), "00:30 is scheduled under UTC+2 only"
    real = clock.schedule(date(2026, 3, 8), date(2026, 3, 13))
    assert real.scheduled(srv(2026, 3, 10, 0, 5)) and real.scheduled(srv(2026, 3, 10, 0, 30))
    assert not real.scheduled(srv(2026, 3, 9, 23, 30)), "closed under both offsets (2026 server-23:00 close)"


def time_hour(u):
    return (int(u) // 3600) % 24


def test_15_fractional_delay_entry_minute():
    bar = srv(2025, 6, 3, 10, 0)
    o = clock.opportunity_clock(bar, 484.978)
    assert o["E"] >= o["D"] and o["E"] - o["D"] < 60 and o["E"] % 60 == 0, o
    assert o["E"] == srv(2025, 6, 3, 10, 24)
    pre = clock.pre_entry_minutes(bar, o["E"])
    assert pre == list(range(bar + 900, o["E"], 60)) and o["E"] not in pre
    assert not set(pre) & set(range(o["E"], o["E"] + 600, 60)), "no minute both pre- and post-entry"


def test_27_entry_with_no_m1_bar():
    sched = clock.schedule(date(2026, 6, 1), date(2026, 6, 4))
    e_a = clock.entry_instant(clock.decision_instant(srv(2026, 6, 2, 22, 45), 480))
    assert e_a == srv(2026, 6, 2, 23, 8)
    m1 = data.Bars.from_rows(60, [(srv(2026, 6, 2, 22, 59), 4300.0, 4300.5, 4299.5, 4300.2),
                                  (srv(2026, 6, 3, 1, 0), 4305.0, 4306.0, 4304.0, 4305.5)])
    assert clock.entry_bid(m1, e_a, sched) == (None, "market_closed")
    sched_b = clock.schedule(date(2025, 6, 2), date(2025, 6, 4))
    e_b = srv(2025, 6, 3, 10, 0)
    m1_b = data.Bars.from_rows(60, [(e_b + 60, 3300.0, 3301.0, 3299.0, 3300.5)])
    assert clock.entry_bid(m1_b, e_b, sched_b) == (None, "m1_minute_unavailable")
    sched_c = clock.schedule(date(2024, 11, 27), date(2024, 12, 2))
    e_c = srv(2024, 11, 29, 21, 0)
    assert not sched_c.scheduled(e_c), "fixture: 21:00 on Black Friday 2024 is outside the schedule"
    m1_c = data.Bars.from_rows(60, [(e_c, 2650.10, 2650.4, 2649.9, 2650.2)])
    assert clock.entry_bid(m1_c, e_c, sched_c) == (2650.10, None), "observed minute fills at its open"


# ---------------------------------------------------------------- group B

def test_06_spread_draw_golden_vectors():
    model = spread.SpreadModel({s: list(range(1, 1001)) for s in spread.SESSIONS})
    for epoch, u_want, rank_want in ((1790935200, 0.032532492723, 33), (1791395100, 0.127935830013, 128)):
        u, pts = model.draw(epoch, "NY")
        assert f"{u:.12f}" == f"{u_want:.12f}", (epoch, u)
        assert pts == rank_want and isinstance(pts, int), (epoch, pts)
    bar = 1790935200 - 900
    draws = {d: spread.draw_for_bar(model, bar, clock.entry_instant(clock.decision_instant(bar, d)))
             for d in clock.PREREGISTERED_DELAYS}
    assert len({u for u, _, _ in draws.values()}) == 1, "u identical across delay runs"
    assert {p for _, p, _ in draws.values()} == {33}
    assert spread.ask_from(4181.17, 36) == 4181.53


def test_07_long_short_accounting():
    lv = entry.levels("LONG", 4180.89, 4173.74, 4195.19, 0.36)
    assert round(lv["fill"], 2) == 4181.25
    assert round(entry.realised_r("LONG", lv["fill"], 4173.74, lv["tp_trigger_bid"]), 3) == 1.856
    assert entry.realised_r("LONG", lv["fill"], 4173.74, 4173.74) == -1.0
    sv = entry.levels("SHORT", 4102.21, 4130.45, 4045.74, 0.26)
    assert (round(sv["sl_trigger_bid"], 2), round(sv["tp_trigger_bid"], 2)) == (4130.19, 4045.48), sv
    exit_leg = sv["sl_trigger_bid"] + 0.26
    assert abs(entry.realised_r("SHORT", sv["fill"], 4130.45, exit_leg) + 1.0) < 1e-9, "clean SHORT stop is -1R"


def test_08_short_stop_crossed_on_ask():
    sig = {"direction": "SHORT", "entry": 4102.21, "sl": 4130.45, "tp": 4045.74, "symbol": "XAUUSD"}
    out = entry.execute(sig, entry.build_quote(4130.30, 4130.56), 100000.0, PARAMS, SPEC)
    assert out["execution_validity_status"] == "original_sl_already_crossed", out["execution_validity_status"]
    assert entry.population(out) == "B"


def test_14_function_parity_e0():
    events = live_events()
    eligible = [e for e in events if e.get("exec_observation")]
    assert eligible, "no E0-eligible event in the fixture"
    calc = SPEC.margin_calculator()
    for ev in eligible:
        got = eo.propose_execution(live_signal(ev), ev["quote"], ev["balance"], PARAMS,
                                   SPEC.symbol_info, mt5=calc)
        want = ev["exec_observation"]
        diff = {k: (want[k], got.get(k)) for k in want if want[k] != got.get(k)}
        assert not diff, (ev["bar_time"], diff)
    oct7 = event_at(events, "2026-10-07 17:30:00+00:00")
    assert oct7["exec_observation"]["required_margin"] == 1642.26


def test_17_margin_insufficient():
    calc = SPEC.margin_calculator()
    assert round(calc.order_calc_margin(calc.ORDER_TYPE_BUY, "XAUUSD", 0.40, 4105.66), 2) == 1642.26
    sig = {"direction": "LONG", "entry": 4000.00, "sl": 3999.90, "tp": 4000.20, "symbol": "XAUUSD"}
    out = entry.execute(sig, entry.build_quote(4000.00, 4000.05), 100000.0, PARAMS, SPEC)
    assert out["proposed_volume"] == 66.66 and out["required_margin"] > 100000.0, out
    assert out["execution_validity_status"] == "margin_insufficient", out["execution_validity_status"]
    assert entry.population(out) == "B"
    roomy = {"direction": "LONG", "entry": 4000.00, "sl": 3990.00, "tp": 4020.00, "symbol": "XAUUSD"}
    r = entry.execute(roomy, entry.build_quote(4000.00, 4000.30), 100000.0, PARAMS, SPEC)
    assert r["execution_validity_status"] == "executable" and entry.population(r) == "C"
    r2 = entry.execute(roomy, entry.build_quote(4000.00, 4000.30), 100000.0, PARAMS, SPEC,
                       open_margin=99000.0)
    assert r2["execution_validity_status"] == "margin_insufficient", "margin of open trades counts"


def test_28_r1_report_only():
    events = live_events()
    for bar_time, q_want, lots in (("2026-10-23 16:45:00+00:00", 0.11, 14.28),
                                   ("2026-11-04 15:15:00+00:00", 1.27, 0.68)):
        ev = event_at(events, bar_time)
        out = entry.execute(live_signal(ev), ev["quote"], ev["balance"], PARAMS, SPEC)
        assert out["execution_validity_status"] == "executable", (bar_time, out["execution_validity_status"])
        assert out["proposed_volume"] == lots, (bar_time, out["proposed_volume"])
        assert round(entry.r1_q(out), 2) == q_want, (bar_time, entry.r1_q(out))
        assert entry.population(out) == "C", bar_time


def test_seal_strategy_window():
    m15 = data.Bars.from_rows(900, [(srv(2025, 8, 31, 0, 0) + 900 * i, 1, 1, 1, 1) for i in range(400)])
    ok = data.strategy_window(m15, srv(2025, 8, 31, 0, 0) + 900 * 50)
    assert len(ok) == 51
    for bar in (srv(2025, 9, 1, 0, 0), srv(2025, 9, 2, 0, 0)):
        try:
            data.strategy_window(m15, bar)
        except data.HoldoutSealed:
            continue
        raise AssertionError(f"strategy window ending {bar} touched the holdout")


# ---------------------------------------------------------------- broken variants

def _decision_without_close(bar_open, delay):
    return bar_open + delay


def _entry_reads_close(m1, e, sched):
    i = m1.index(e)
    return (float(m1.close[i]), None) if i is not None else (None, "m1_minute_unavailable")


def _entry_forward_fill(m1, e, sched):
    i = int(np.searchsorted(m1.time, e, side="right")) - 1
    return (float(m1.close[i]), None) if i >= 0 else (None, "m1_minute_unavailable")


def _entry_next_available(m1, e, sched):
    i = int(np.searchsorted(m1.time, e))
    return (float(m1.open[i]), None) if i < len(m1.time) else (None, "m1_minute_unavailable")


def _entry_single_detail(m1, e, sched):
    bid = m1.open_at(e)
    return (bid, None) if bid is not None else (None, "m1_minute_unavailable")


def _probe_session(d):
    import probe_entry_timing
    return probe_entry_timing.session_of(int(d))


def _fixed_utc3(t):
    return t - 10800


def _uncertain_sessions_only(bar_open, delay, window=tc.OFFSET_UNCERTAIN):
    d = clock.decision_instant(bar_open, delay)
    e = clock.entry_instant(d)
    if clock.offset_uncertain(d, window) and get_session_tag(time_hour(d - 7200)) != get_session_tag(time_hour(d - 10800)):
        return {"bar_open": bar_open, "D": d, "E": e, "status": "quote_unavailable",
                "detail": "offset_uncertain", "session": None,
                "D_utc_if_utc2": d - 7200, "D_utc_if_utc3": d - 10800}
    return {"bar_open": bar_open, "D": d, "E": e, "status": None, "detail": None,
            "session": get_session_tag(time_hour(d - 7200)), "D_utc_if_utc2": d - 7200,
            "D_utc_if_utc3": d - 10800}


def _schedule_eu1_alone(first, last, window=tc.OFFSET_UNCERTAIN):
    return tc.Schedule(first, last, window=window, union=False)


class _SingleOffsetInWindow:
    def __init__(self, first, last, window, hours):
        lo, hi = window
        base = tc.sessions(first, last)
        self.intervals = tc.merge(tc.clip(base, -2 ** 62, lo) + tc.clip(base, hi, 2 ** 62)
                                  + tc.clip(tc.sessions(first, last, hours), lo, hi))

    def scheduled(self, t):
        return any(a <= t < b for a, b in self.intervals)


def _schedule_utc3_alone(first, last, window=tc.OFFSET_UNCERTAIN):
    return _SingleOffsetInWindow(first, last, window, 3)


def _floor_minute(d):
    return int(d // 60) * 60


def _quantile_type7(sorted_points, u):
    h = (len(sorted_points) - 1) * u
    lo = int(math.floor(h))
    hi = min(lo + 1, len(sorted_points) - 1)
    return sorted_points[lo] + (h - lo) * (sorted_points[hi] - sorted_points[lo])


def _draw_keyed_on_entry(model, bar_open, e):
    session = clock.session_of(e)
    u, points = model.draw(e, session)
    return u, points, session


def _levels_mirrored(direction, bid, sl, tp, s):
    if direction == "SHORT":
        return {"fill": bid - s, "sl_trigger_bid": sl, "tp_trigger_bid": tp}
    return eo.spread_accounting(direction, bid, sl, tp, s)


def _propose_bid_trigger(signal, quote, balance, params, symbol_info, mt5):
    q = dict(quote, quote_ask=quote["quote_bid"]) if quote else quote
    return eo.propose_execution(signal, q, balance, params, symbol_info, mt5=mt5)


def _execute_no_margin_check(signal, quote, balance, params, spec, open_margin=0.0):
    return entry.propose(signal, quote, balance, params, spec.symbol_info, spec.margin_calculator())


def _propose_without_calculator(signal, quote, balance, params, symbol_info, mt5):
    return eo.propose_execution(signal, quote, balance, params, symbol_info, mt5=None)


def _population_q_filter(out):
    q = entry.r1_q(out) if out.get("executable_sl_distance") else None
    if out["execution_validity_status"] == eo.EXEC_OK and (q is None or q >= 0.25):
        return "C"
    return "B"


def _guard_off(t0, t1):
    return None


VARIANTS = {
    "test_01_decision_instant_and_entry_reads_open": [
        ("D without the bar close", clock, "decision_instant", _decision_without_close),
        ("entry reads the M1 close", clock, "entry_bid", _entry_reads_close)],
    "test_02_missing_entry_minute_is_quote_unavailable": [
        ("forward-filled entry minute", clock, "entry_bid", _entry_forward_fill)],
    "test_04_session_parity_at_boundaries": [
        ("probe-local session_of", clock, "session_of", _probe_session)],
    "test_05_offset_rule_and_uncertain_window": [
        ("fixed UTC+3 offset", clock, "server_to_utc", _fixed_utc3),
        ("uncertain only where sessions differ", clock, "opportunity_clock", _uncertain_sessions_only),
        ("offset-eu-1 alone in the window", clock, "schedule", _schedule_eu1_alone),
        ("UTC+3 alone in the window", clock, "schedule", _schedule_utc3_alone)],
    "test_15_fractional_delay_entry_minute": [
        ("floor_minute(D)", clock, "entry_instant", _floor_minute)],
    "test_27_entry_with_no_m1_bar": [
        ("first M1 open at or after E", clock, "entry_bid", _entry_next_available),
        ("no market_closed detail", clock, "entry_bid", _entry_single_detail)],
    "test_06_spread_draw_golden_vectors": [
        ("interpolated quantile (type 7)", spread, "quantile_type1", _quantile_type7),
        ("draw keyed on E, not the bar close", spread, "draw_for_bar", _draw_keyed_on_entry)],
    "test_07_long_short_accounting": [
        ("SHORT mirrored from LONG", entry, "levels", _levels_mirrored)],
    "test_08_short_stop_crossed_on_ask": [
        ("SHORT stop checked on the bid", entry, "propose", _propose_bid_trigger)],
    "test_14_function_parity_e0": [
        ("no margin calculator", eo, "propose_execution",
         lambda s, q, b, p, si, mt5=None, _f=eo.propose_execution: _f(s, q, b, p, si, mt5=None))],
    "test_17_margin_insufficient": [
        ("margin not compared with the balance", entry, "execute", _execute_no_margin_check)],
    "test_28_r1_report_only": [
        ("q filter at 0.25", entry, "population", _population_q_filter)],
    "test_seal_strategy_window": [
        ("seal disabled", data, "guard", _guard_off)],
}


def run(fn):
    try:
        fn()
        return "PASS", ""
    except Pending as e:
        return "PENDING", str(e)
    except Exception as e:  # noqa: BLE001
        return "FAIL", f"{type(e).__name__}: {e}"


def tests():
    return sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))


def main_tests():
    counts = {"PASS": 0, "FAIL": 0, "PENDING": 0}
    for name, fn in tests():
        verdict, why = run(fn)
        counts[verdict] += 1
        print(f"{verdict:7s} {name}" + (f": {why}" if why else ""))
    if counts["FAIL"] == 0 and counts["PENDING"] == 0:
        print("\nALL PASS")
    else:
        print(f"\n{counts['PASS']} PASS, {counts['FAIL']} FAILED, {counts['PENDING']} PENDING")
    return counts["FAIL"] == 0 and counts["PENDING"] == 0


def main_controls():
    bad = pending = 0
    names = dict(tests())
    for name in sorted(VARIANTS):
        for label, module, attr, broken in VARIANTS[name]:
            with mock.patch.object(module, attr, broken):
                verdict, why = run(names[name])
            if verdict == "PENDING":
                pending += 1
                print(f"PENDING {name} / {label}: {why}")
            elif verdict == "FAIL":
                print(f"PASS    {name} / {label}: fails as required ({why[:90]})")
            else:
                bad += 1
                print(f"FAIL    {name} / {label}: broken variant PASSED")
    missing = [n for n, _ in tests() if n not in VARIANTS]
    for n in missing:
        print(f"FAIL    {n}: no broken variant registered")
    bad += len(missing)
    print("\nCONTROLS ALL PASS" if bad == 0 and pending == 0 else
          f"\nCONTROLS: {bad} FAILED, {pending} PENDING")
    return bad == 0 and pending == 0


if __name__ == "__main__":
    ok = main_controls() if "--controls" in sys.argv else main_tests()
    sys.exit(0 if ok else 1)
