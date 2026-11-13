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

Built: 1-22, 24-28, 5(b) PATH_UNKNOWN part (test_05b) and the holdout seal of
pr-0.4 test 1 (strategy window only). Test 23 (E0 eligibility) is built with
conformance (section 8).
"""
import calendar
import dataclasses
import hashlib
import json
import logging
import math
import random
import sys
import types
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import trading_calendar as tc  # noqa: E402
from engine import entry_observation as eo  # noqa: E402
from engine.pipeline import get_session_tag  # noqa: E402
from replay import clock, data, entry, exits, portfolio, spread  # noqa: E402

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


def _vwap_fixture():
    t0 = srv(2025, 6, 2, 18, 0)
    rows = [(t0 + 900 * i, 3300 + i * 0.5, 3301 + i * 0.5, 3299 + i * 0.5, 3300.25 + i * 0.5,
             100 + 37 * (i % 7), 20) for i in range(48)]
    return t0, data.Bars.from_rows(900, rows)


def test_03_vwap_resets_at_server_midnight():
    from engine import indicators
    import pandas as pd
    t0, m15 = _vwap_fixture()
    last = t0 + 900 * 47
    replay_frame = data.strategy_window(m15, last)
    live_frame = pd.DataFrame({"datetime": pd.to_datetime(m15.time, unit="s", utc=True),
                               "open": m15.open, "high": m15.high, "low": m15.low,
                               "close": m15.close, "volume": m15.volume, "spread": m15.spread,
                               "real_volume": 0})
    v_replay = indicators.calculate_vwap(replay_frame)["vwap"].values
    v_live = indicators.calculate_vwap(live_frame)["vwap"].values
    assert np.allclose(v_replay, v_live), "replay VWAP must equal live VWAP on server-clock bars"
    utc_frame = live_frame.copy()
    utc_frame["datetime"] = pd.to_datetime([clock.server_to_utc(int(t)) for t in m15.time], unit="s", utc=True)
    v_utc = indicators.calculate_vwap(utc_frame)["vwap"].values
    assert not np.allclose(v_live, v_utc), "fixture must discriminate: UTC conversion moves the reset"
    i_mid = int((srv(2025, 6, 3) - t0) // 900)
    tp = (m15.high[i_mid] + m15.low[i_mid] + m15.close[i_mid]) / 3
    assert abs(v_replay[i_mid] - tp) < 1e-9, "VWAP resets at server midnight"


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


# ---------------------------------------------------------------- group D

FLAT = (3000.0, 3000.5, 2999.5, 3000.2)


def flat(t0, t1, px=FLAT):
    return {t: px for t in range(t0, t1, 60)}


def build(minutes, m15_set=None, m15_drop=()):
    """M1 from {t: (o, h, l, c)}; M15 rebuilt from M1 per slot, then overridden
    by m15_set {slot: (o, h, l, c)} and with m15_drop slots removed."""
    m1 = data.Bars.from_rows(60, [(t,) + tuple(minutes[t]) for t in sorted(minutes)])
    slots = {}
    for t in sorted(minutes):
        slots.setdefault(t - t % 900, []).append((t,) + tuple(minutes[t]))
    m15 = {s: data.rebuild(ms) for s, ms in slots.items()}
    m15.update(m15_set or {})
    for s in m15_drop:
        m15.pop(s, None)
    return m1, data.Bars.from_rows(900, [(s,) + tuple(m15[s]) for s in sorted(m15)])


def long_at(e):
    return exits.Trade("LONG", 3000.30, 2990.0, 3020.0, 0.30, e)


def short_at(e):
    return exits.Trade("SHORT", 3000.00, 3010.0, 2980.0, 0.30, e)


def sched_jun25():
    return clock.schedule(date(2025, 6, 1), date(2025, 6, 10))


def test_09_same_minute_sl_and_tp():
    e = srv(2025, 6, 3, 10, 8)
    mins = flat(e, e + 60)
    mins[e + 60] = (3000.0, 3021.0, 2989.0, 3000.0)
    m1, m15 = build(mins)
    res = exits.resolve(long_at(e), m1, m15, sched_jun25())
    assert res.outcome == "SL_HIT" and "AMBIGUOUS" in res.flags, (res.outcome, res.flags)
    assert res.exit_time == e + 120 and abs(res.r + 1) < 1e-9


def test_10_gaps_through_stop_and_target():
    fri = srv(2025, 6, 6, 23, 30)
    mon = srv(2025, 6, 9, 1, 0)
    sched = sched_jun25()
    assert not sched.scheduled(srv(2025, 6, 7, 12, 0)) and sched.scheduled(mon), "fixture: weekend closure"
    for trade, reopen, outcome, leg in (
            (long_at(fri), (2985.0, 2986.0, 2984.0, 2985.5), "SL_HIT", 2985.0),
            (long_at(fri), (3025.0, 3026.0, 3024.0, 3025.5), "TP_HIT", 3020.0),
            (short_at(fri), (3015.0, 3016.0, 3014.0, 3015.5), "SL_HIT", 3015.30)):
        mins = flat(fri, srv(2025, 6, 7))
        mins[mon] = reopen
        m1, m15 = build(mins)
        res = exits.resolve(trade, m1, m15, sched)
        assert (res.outcome, round(res.exit_leg, 2), res.exit_time) == (outcome, leg, mon + 60), \
            (trade.direction, res.outcome, res.exit_leg, res.exit_time)
        assert "gap" in res.flags and res.hole_minutes == 0
        if outcome == "SL_HIT":
            assert res.r < -1, res.r
        else:
            assert abs(res.r - (3020.0 - 3000.30) / 10.30) < 1e-9, "no positive slippage"


_SNAP = {}


def snapshot():
    if "s" not in _SNAP:
        if not (data.DEFAULT_DIR / "manifest.json").exists():
            raise Pending(f"snapshot {data.DEFAULT_DIR} not present")
        _SNAP["s"] = data.load_snapshot()
    return _SNAP["s"]


def test_13_m15_rebuilt_from_m1():
    b0 = srv(2025, 6, 3, 10, 0)
    mins = {b0 + 60 * i: (3000 + i, 3001 + i, 2999 + i, 3000.5 + i) for i in range(15)}
    hole_bar = b0 + 900
    mins.update({hole_bar + 60 * i: FLAT for i in (0, 1, 2, 5, 6, 14)})
    m1, m15 = build(mins, m15_set={hole_bar: (3000.0, 3000.4, 2999.5, 3000.2),
                                   b0 + 1800: (3000.0, 3001.0, 2999.0, 3000.5)})
    sched = sched_jun25()
    got = data.c63(m15, m1, sched, b0, b0 + 2700)
    assert (got["complete"], got["mismatch"]) == (1, []), got
    assert (got["with_holes"], got["envelope_fail"], got["orphan"]) == (1, [hole_bar], [b0 + 1800]), got
    _, m15_bad = build(mins, m15_set={b0: (3000.0, 3015.5, 2999.0, 3014.5)})
    assert data.c63(m15_bad, m1, sched, b0, b0 + 900)["mismatch"] == [b0]
    snap = snapshot()
    t0, t1 = srv(2026, 9, 14), srv(2026, 10, 27)
    real = data.c63(snap["m15"], snap["m1"], clock.schedule(date(2026, 9, 13), date(2026, 10, 27)), t0, t1)
    assert real["complete"] > 2500, real["complete"]
    assert (real["mismatch"], real["envelope_fail"], real["orphan"]) == ([], [], []), \
        {k: v[:5] for k, v in real.items() if isinstance(v, list)}


def _hole_bar_fixture(m15_hole_range, extra=None, drop=()):
    """LONG entered 10:08; bar 10:15 has holes 10:20-10:22; TP reachable at 10:30."""
    e = srv(2025, 6, 3, 10, 8)
    b = srv(2025, 6, 3, 10, 15)
    mins = flat(e, b + 300)
    mins.update(flat(b + 480, b + 900))
    mins.update(extra or {})
    mins[b + 900] = (3000.0, 3021.0, 2999.5, 3020.5)
    m1, m15 = build(mins, m15_set={b: m15_hole_range}, m15_drop=drop)
    return e, b, m1, m15


def test_16_hole_cases():
    sched = sched_jun25()
    e, b, m1, m15 = _hole_bar_fixture((3000.0, 3005.0, 2995.0, 3000.2))
    res = exits.resolve(long_at(e), m1, m15, sched)
    assert res.decisions == [(b, b + 900, "c")] and res.hole_minutes == 3, res.decisions
    assert (res.outcome, res.exit_time) == ("TP_HIT", b + 960), "(c): continues after B"
    e, b, m1, m15 = _hole_bar_fixture((3000.0, 3005.0, 2989.0, 3000.2))
    res = exits.resolve(long_at(e), m1, m15, sched)
    assert (res.outcome, res.exit_time, res.exit_leg) == ("SL_HIT", b + 900, 2990.0), "(d): SL at level at K"
    assert "m1_hole" in res.flags and abs(res.r + 1) < 1e-9
    e, b, m1, m15 = _hole_bar_fixture((3000.0, 3021.0, 2995.0, 3000.2))
    res = exits.resolve(long_at(e), m1, m15, sched)
    assert (res.outcome, res.exit_time, res.exit_leg) == ("TP_HIT", b + 900, 3020.0), "(d): TP at level at K"
    e, b, m1, m15 = _hole_bar_fixture((3000.0, 3005.0, 2987.0, 3000.2),
                                      extra={srv(2025, 6, 3, 10, 25): (2988.0, 2989.0, 2987.0, 2988.5)})
    res = exits.resolve(long_at(e), m1, m15, sched)
    assert (res.outcome, res.exit_time, res.exit_leg) == ("SL_HIT", b + 900, 2988.0), \
        ("(d): observed post-hole touch opening beyond the trigger exits at that open", res.exit_leg)
    e, b, m1, m15 = _hole_bar_fixture((3000.0, 3021.0, 2989.0, 3000.2))
    res = exits.resolve(long_at(e), m1, m15, sched)
    assert res.outcome == "SL_HIT" and {"AMBIGUOUS", "m1_hole"} <= res.flags and res.exit_time == b + 900
    e, b, m1, m15 = _hole_bar_fixture((3000.0, 3005.0, 2995.0, 3000.2), drop=(srv(2025, 6, 3, 10, 15),))
    for sc, outcome, leg, t in (("S", "PATH_UNKNOWN", 2990.0, b + 900), ("T", "PATH_UNKNOWN", 3020.0, b + 900),
                                ("C", "TP_HIT", 3020.0, b + 960)):
        res = exits.resolve(long_at(e), m1, m15, sched, scenario=sc)
        assert res.path_unknown and res.decisions[0][2] == "a", (sc, res.decisions)
        assert (res.outcome, res.exit_leg, res.exit_time) == (outcome, leg, t), (sc, res.outcome, res.exit_time)
        assert not res.closed_trade, "PATH_UNKNOWN is excluded from closed-trade statistics"
    b0 = srv(2025, 6, 3, 10, 0)
    e = b0 + 480
    for pre_low, case in ((2989.0, "b"), (2999.5, "d")):
        mins = flat(b0, b0 + 900)
        mins[b0 + 180] = (3000.0, 3000.5, pre_low, 3000.2)
        for t in (e + 120, e + 180):
            del mins[t]
        m1, m15 = build(mins, m15_set={b0: (3000.0, 3000.5, 2989.0, 3000.2)})
        res = exits.resolve(long_at(e), m1, m15, sched)
        assert res.decisions == [(b0, b0 + 900, case)], (pre_low, res.decisions)
        if case == "b":
            assert res.path_unknown and "m1_hole_entry_bar" in res.flags
        else:
            assert res.outcome == "SL_HIT" and res.exit_time == b0 + 900


def test_18_open_at_data_end_and_floating_loss():
    e = srv(2025, 6, 3, 10, 8)
    mins = flat(e, e + 1200)
    m1, m15 = build(mins)
    res = exits.resolve(long_at(e), m1, m15, sched_jun25())
    assert res.outcome == "OPEN" and res.exit_time is None and not res.closed_trade
    assert res.mtm_r is not None and abs(res.mtm_r - (3000.2 - 3000.30) / 10.30) < 1e-9, res.mtm_r
    mins = flat(e, e + 600)
    mins[e + 300] = (2999.0, 2999.5, 2992.0, 2992.06)
    mins[e + 600] = (3000.0, 3021.0, 2999.5, 3020.5)
    m1, m15 = build(mins)
    res = exits.resolve(long_at(e), m1, m15, sched_jun25())
    assert res.outcome == "TP_HIT" and res.closed_trade
    assert round(min(r for _, r in res.marks), 6) == -0.8, res.marks
    assert (e + 360, round(-0.8, 6)) in [(t, round(r, 6)) for t, r in res.marks]


def test_20_envelope_invalid_is_path_unknown():
    e, b, m1, m15 = _hole_bar_fixture((3000.0, 3000.4, 2995.0, 3000.2))
    res = exits.resolve(long_at(e), m1, m15, sched_jun25(), scenario="C")
    assert res.decisions[0] == (b, b + 900, "a") and res.path_unknown, res.decisions


def test_22_equity_marks():
    e = srv(2025, 6, 3, 10, 8)
    b = srv(2025, 6, 3, 10, 15)
    mins = flat(e, e + 1200)
    for t in (b + 120, b + 180):
        del mins[t]
    mins[e + 60] = (3000.0, 3001.0, 2999.0, 3000.9)
    m1, m15 = build(mins, m15_set={b: (3000.0, 3000.5, 2999.5, 3000.2)})
    res = exits.resolve(short_at(e), m1, m15, sched_jun25())
    marked = [t - 60 for t, _ in res.marks]
    assert marked == sorted(set(mins) & set(range(e, e + 1200, 60))), "a mark at every observed close, none in holes"
    assert res.hole_minutes == 2
    t_mark = dict(res.marks)[e + 120]
    assert abs(t_mark - (3000.0 - (3000.9 + 0.30)) / 10.0) < 1e-9, "SHORT marks on bid plus spread"
    adverse = dict(res.adverse_marks)[e + 120]
    assert abs(adverse - (3000.0 - (3001.0 + 0.30)) / 10.0) < 1e-9


def test_26_release_at_edges_and_under_c():
    sched = clock.schedule(date(2024, 12, 1), date(2024, 12, 6))
    e = srv(2024, 12, 3, 23, 38)
    b = srv(2024, 12, 3, 23, 45)
    reopen = srv(2024, 12, 4, 1, 0)
    assert sched.scheduled(b + 840) and not sched.scheduled(b + 900) and sched.scheduled(reopen)
    mins = flat(e, b + 840)
    mins[reopen] = (2985.0, 2986.0, 2984.0, 2985.5)
    m1, m15 = build(mins, m15_set={b: (3000.0, 3000.5, 2999.5, 3000.2)})
    res = exits.resolve(long_at(e), m1, m15, sched)
    assert res.decisions == [(b, b + 900, "c")], res.decisions
    assert (res.outcome, res.exit_leg, res.exit_time) == ("SL_HIT", 2985.0, reopen + 60) and "gap" in res.flags
    mins = flat(e, b + 900)
    mins.update(flat(reopen + 300, reopen + 900))
    mins[reopen + 300] = (2985.0, 2986.0, 2984.0, 2985.5)
    m1, m15 = build(mins, m15_set={reopen: (2985.0, 3000.5, 2984.0, 3000.2)})
    res = exits.resolve(long_at(e), m1, m15, sched)
    assert res.decisions == [(reopen, reopen + 900, "d")], res.decisions
    assert (res.outcome, res.exit_leg, res.exit_time) == ("SL_HIT", 2985.0, reopen + 900), (res.exit_leg, res.exit_time)
    sched = sched_jun25()
    e = srv(2025, 6, 3, 9, 38)
    b1, b2, b3 = srv(2025, 6, 3, 10, 0), srv(2025, 6, 3, 10, 15), srv(2025, 6, 3, 10, 30)
    for b3_open, want_c in ((2985.0, ("SL_HIT", 2985.0, b3 + 60)), (3005.0, ("OPEN", None, None))):
        mins = flat(e, b1)
        mins.update(flat(b3, b3 + 600, (b3_open, b3_open + 0.5, b3_open - 0.5, b3_open)))
        m1, m15 = build(mins, m15_drop=(b1, b2))
        results = []
        for sc, want in (("S", ("PATH_UNKNOWN", 2990.0, b2)), ("T", ("PATH_UNKNOWN", 3020.0, b2)), ("C", want_c)):
            res = exits.resolve(long_at(e), m1, m15, sched, scenario=sc)
            assert (res.outcome, res.exit_leg, res.exit_time) == want, (b3_open, sc, res.outcome, res.exit_leg, res.exit_time)
            assert res.path_unknown
            assert exits.path_unknown_count([res]) == 1, "PATH_UNKNOWN counted once per trade"
            results.append(res)
        c = results[-1]
        assert [d[:2] for d in c.decisions] == [(b1, b2), (b2, b3)], c.decisions
        if b3_open < 2990:
            assert c.r < -1


def test_05b_union_hole_is_path_unknown():
    window = (srv(2025, 3, 11), srv(2025, 3, 13))
    sched = clock.schedule(date(2025, 3, 9), date(2025, 3, 14), window=window)
    e = srv(2025, 3, 11, 22, 38)
    mins = flat(e, srv(2025, 3, 11, 23, 0))
    mins.update(flat(srv(2025, 3, 12, 0, 0), srv(2025, 3, 12, 0, 30)))
    m1, m15 = build(mins)
    res = exits.resolve(long_at(e), m1, m15, sched, scenario="S")
    assert res.path_unknown and res.decisions[0] == (srv(2025, 3, 11, 23, 0), srv(2025, 3, 11, 23, 15), "a"), \
        res.decisions


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


# ---------------------------------------------------------------- groups C and E

SNAPSHOT_SIGNALS = ("2026-10-02 09:45:00+00:00", "2026-10-07 17:30:00+00:00",
                    "2026-10-20 15:15:00+00:00", "2026-10-23 16:45:00+00:00")
NOT_COVERED = ("2026-11-04 15:15:00+00:00", "2026-11-05 16:00:00+00:00")


def _bar_epoch(bar_time):
    """Server epoch of a logged bar_time (server clock labelled +00:00)."""
    return int(datetime.fromisoformat(bar_time).timestamp())


def _candle_hash(candles):
    """run_hermes.main's candle hash, used as the comparator."""
    last = candles.iloc[-1]
    o = {k: round(float(last[k]), 2) for k in ("open", "high", "low", "close")}
    return hashlib.sha256(f"{last['datetime']}|{o['open']}|{o['high']}|{o['low']}|{o['close']}|"
                          f"{float(last.get('volume') or 0)}".encode()).hexdigest()[:16]


def test_12_strategy_parity_live_signals():
    """Every live signal the snapshot covers. An approved event logs no
    blocked_at key; its gate outcome is reason 'Approved' with blocked_class
    None, compared here. The gate runs in CONFORMANCE state on the logged
    spread_points: the spread artefact is TBD-3, and this test is strategy
    parity, not spread."""
    events = live_events()
    snap = snapshot()
    covered = []
    for ev in events:
        b = _bar_epoch(ev["bar_time"])
        if snap["m15"].index(b) is None:
            assert ev["bar_time"] in NOT_COVERED, ev["bar_time"]
            continue
        covered.append(ev["bar_time"])
        res = portfolio.signal_at(snap["m15"], b, PARAMS)
        sig = res["signal"]
        assert sig is not None, (ev["bar_time"], res["reason"])
        got = (sig["direction"], sig["entry"], sig["sl"], sig["tp"], _candle_hash(res["candles"]))
        want = (ev["direction"], ev["entry"], ev["sl"], ev["tp"], ev["candle_hash"])
        assert got == want, (ev["bar_time"], got, want)
        assert res["diag"] == ev["diag"], ev["bar_time"]
        d = clock.decision_instant(b, clock.BASELINE_DELAY)
        held, losses = portfolio.gate_state(portfolio.Book(), clock.entry_instant(d), portfolio.CONFORMANCE)
        session = clock.session_of(d)
        ok, reason, details = portfolio.risk_gate(sig, ev["spread_points"], session, PARAMS,
                                                  SPEC.symbol_info, len(held), losses)
        assert (session, ok, reason, ev["blocked_class"]) == (ev["session"], True, ev["reason"], None), \
            (ev["bar_time"], session, reason)
        assert details["position_size"] == ev["position_size"], (ev["bar_time"], details)
    assert tuple(covered) == SNAPSHOT_SIGNALS, covered


MODEL30 = spread.SpreadModel({s: [30] for s in spread.SESSIONS})
DAY = (2025, 2, 4)
WED = (2025, 2, 5)
DIP = (3000.0, 3000.5, 2989.0, 2999.0)
SPIKE = (3000.0, 3021.0, 2999.5, 3020.5)


def at(hh, mm, day=DAY):
    """Server time on a winter Tuesday: server 10:00 is 08:00 UTC, LONDON."""
    return srv(*day, hh, mm)


def long_signal(**kw):
    return dict({"direction": "LONG", "entry": 3000.0, "sl": 2990.0, "tp": 3020.0,
                 "sl_distance": 10.0, "atr": 8.0, "symbol": "XAUUSD"}, **kw)


def opp(bar_open, delay=None):
    o = {"bar_open": bar_open, "signal": long_signal()}
    if delay is not None:
        o["delay"] = delay
    return o


def ctx(mins, m15_set=None, m15_drop=()):
    m1, m15 = build(mins, m15_set, m15_drop)
    return portfolio.Context(m1, m15, clock.schedule(date(2025, 2, 2), date(2025, 2, 7)),
                             MODEL30, PARAMS, SPEC)


def by_e(run):
    return {r["E"]: r for r in run.records}


def _approve_live(signal, spread_points, session, open_count, daily_losses):
    """risk_manager.approve itself, with trades.json and MT5 positions stubbed."""
    import engine
    from engine import risk_manager
    now = datetime.now(timezone.utc).isoformat()
    trades = [{"outcome": "SL_HIT", "signal_bar_time": now}] * daily_losses
    stub = types.SimpleNamespace(get_positions=lambda instrument: [object()] * open_count)
    with mock.patch.object(risk_manager, "_load_trades", lambda: trades), \
            mock.patch.object(engine, "data_feed", stub, create=True), \
            mock.patch.dict(sys.modules, {"engine.data_feed": stub}), \
            mock.patch.object(logging.getLogger("hermes.risk"), "disabled", True):
        return risk_manager.approve(signal, {"balance": portfolio.BALANCE}, spread_points, session,
                                    PARAMS, SPEC.symbol_info)


GATE_CASES = (
    (long_signal(), 30, "LONDON", 0, 0), (long_signal(), 30, "LONDON", 1, 3),
    (long_signal(), 30, "LONDON", 1, 0), (long_signal(), 60, "NY", 0, 0),
    (long_signal(), 30, "ASIA", 0, 2), (long_signal(atr=2.0), 30, "LONDON", 0, 0),
    (long_signal(sl_distance=2000.0, atr=1000.0), 30, "LONDON", 0, 0))


def test_11_slot_gate_by_mode():
    c = ctx(flat(at(10, 8), at(11, 0)))
    opps = [opp(at(9, 45)), opp(at(10, 0))]
    first, second = at(10, 8), at(10, 23)
    conf = by_e(portfolio.run(opps, c, portfolio.CONFORMANCE))
    assert conf[first]["result"].exit_time is None, "fixture: the first trade is still open"
    assert [conf[e]["population"] for e in (first, second)] == ["C", "C"], "CONFORMANCE does not block"
    sim = by_e(portfolio.run(opps, c, portfolio.SIMULATION))
    assert sim[first]["population"] == "C"
    r = sim[second]
    assert (r["approved"], r["reason"], r["population"], r["open_trades"]) == \
        (False, "Max open trades reached", "A", 1), (r["reason"], r["population"])
    for sig, points, session, n_open, n_loss in GATE_CASES:
        got = portfolio.risk_gate(sig, points, session, PARAMS, SPEC.symbol_info, n_open, n_loss)
        want = _approve_live(sig, points, session, n_open, n_loss)
        assert got == want, (points, session, n_open, n_loss, got, want)


def test_19_exit_at_e_frees_slot_and_loss_on_exit_date():
    mins = flat(at(10, 8), at(11, 0))
    mins[at(10, 37)] = DIP
    r = by_e(portfolio.run([opp(at(9, 45)), opp(at(10, 0)), opp(at(10, 15))], ctx(mins)))
    assert r[at(10, 8)]["result"].exit_time == at(10, 38)
    assert r[at(10, 23)]["reason"] == "Max open trades reached"
    assert (r[at(10, 38)]["approved"], r[at(10, 38)]["population"]) == (True, "C"), "X = E frees the slot"
    reopen = at(1, 0, WED)
    mins = flat(at(22, 38), at(23, 59) + 60)
    mins[reopen] = (2985.0, 2986.0, 2984.0, 2985.5)
    mins.update(flat(at(10, 8, WED), at(10, 30, WED)))
    r = by_e(portfolio.run([opp(at(22, 15)), opp(at(9, 45, WED))], ctx(mins)))
    loss = r[at(22, 38)]["result"]
    assert (loss.outcome, loss.exit_time, loss.is_loss) == ("SL_HIT", reopen + 60, True), loss.outcome
    assert r[at(10, 8, WED)]["daily_losses"] == 1, "a loss counts on the server date of X, not of entry"


def _k_fixture(drop=False, prior_losses=False):
    """Trade entered 09:38; bar 10:00 has a hole at 10:02 and an SL touch at
    10:12; second setups at E = 10:08, 10:13 and 10:15; TP reachable at 10:40."""
    mins = flat(at(9, 0), at(10, 50))
    del mins[at(10, 2)]
    mins[at(10, 12)] = DIP
    mins[at(10, 40)] = SPIKE
    opps = [opp(at(9, 15)), opp(at(9, 45)), opp(at(9, 45), delay=780), opp(at(10, 0), delay=0)]
    if prior_losses:
        mins[at(9, 14)] = DIP
        mins[at(9, 29)] = DIP
        opps += [opp(at(8, 45)), opp(at(9, 0))]
    return ctx(mins, m15_drop=(at(10, 0),) if drop else ()), opps


def test_21_chronology_at_k():
    main, e08, e13, e15 = at(9, 38), at(10, 8), at(10, 13), at(10, 15)
    slot, limit = "Max open trades reached", "Daily loss limit reached"
    c, opps = _k_fixture()
    for sc in exits.SCENARIOS:
        r = by_e(portfolio.run(opps, c, scenario=sc))
        res = r[main]["result"]
        assert (res.outcome, res.exit_time) == ("SL_HIT", e15), ("(a) booked at K, not 10:13", sc, res.exit_time)
        assert [r[e]["reason"] for e in (e08, e13, e15)] == [slot, slot, "Approved"], (sc, r[e13]["reason"])
    c, opps = _k_fixture(drop=True)
    for sc, want in (("S", "Approved"), ("T", "Approved"), ("C", slot)):
        r = by_e(portfolio.run(opps, c, scenario=sc))
        assert r[main]["result"].path_unknown
        assert r[e15]["reason"] == want, ("(b)", sc, r[e15]["reason"])
    assert by_e(portfolio.run(opps, c, scenario="C"))[main]["result"].exit_time == at(10, 41)
    c, opps = _k_fixture(prior_losses=True)
    r = by_e(portfolio.run(opps, c))
    assert [r[e]["reason"] for e in (at(9, 8), at(9, 23), main)] == ["Approved"] * 3
    assert (r[e08]["reason"], r[e08]["daily_losses"]) == (slot, 2)
    assert (r[e15]["open_trades"], r[e15]["reason"]) == (0, limit), ("(c)", r[e15]["reason"])
    c, opps = _k_fixture(drop=True, prior_losses=True)
    for sc, want in (("S", limit), ("T", "Approved"), ("C", slot)):
        r = by_e(portfolio.run(opps, c, scenario=sc))
        assert r[e15]["reason"] == want, ("(c) with (b)", sc, r[e15]["reason"])


def _path(sc, rows):
    return portfolio.Run(portfolio.SIMULATION, sc, [
        {"population": "C", "result": exits.Result(sc, outcome=o, r=r, path_unknown=pu)} for o, r, pu in rows])


def _sign_verdict(run):
    return "PASS" if sum(res.r for res in run.results()) > 0 else "FAIL"


def test_24_scenario_agreement_hold():
    def base(top):
        return [("TP_HIT", 1.0, False)] * 49 + [("SL_HIT", -1.0, False)] * 49 + [("TP_HIT", top, False)]

    def paths(rows, s, t, c, rows_c=None):
        return {"S": _path("S", rows + [("PATH_UNKNOWN", s, True)]),
                "T": _path("T", rows + [("PATH_UNKNOWN", t, True)]),
                "C": _path("C", (rows_c or rows) + [("TP_HIT", c, True)])}
    v, rows = portfolio.scenario_agreement(paths(base(0.5), -1.0, 2.0, 1.0), _sign_verdict)
    assert (v, rows["S"]["verdict"], rows["T"]["verdict"]) == (portfolio.HOLD, "FAIL", "PASS"), (v, rows)
    v, rows = portfolio.scenario_agreement(paths(base(5.0), -1.0, 2.0, 1.0), _sign_verdict)
    assert v == "PASS" and all(r["share"] == 0.01 for r in rows.values()), ("1% is allowed", v, rows)
    two = base(5.0)
    two[49] = ("SL_HIT", -1.0, True)
    v, rows = portfolio.scenario_agreement(paths(base(5.0), -1.0, 2.0, 1.0, rows_c=two), _sign_verdict)
    assert (v, rows["C"]["share"], rows["S"]["share"]) == (portfolio.HOLD, 0.02, 0.01), (v, rows)
    assert {r["verdict"] for r in rows.values()} == {"PASS"}


def _arbitrary(rng):
    o, c = 3000 + rng.uniform(-30, 30), 3000 + rng.uniform(-30, 30)
    return (o, max(o, c) + rng.uniform(0, 10), min(o, c) - rng.uniform(0, 10), c)


def _scramble(c, t, seed):
    """C2.6 metamorphic input: M1 after t arbitrary (values, and presence
    except for the last bar); M1 stamped t keeps only its open; every M15 bar
    not closed by t arbitrary or absent. 'Not closed by t' (stamp > t - 900)
    contains the bar stamped t that section 9 test 25 names."""
    rng = random.Random(seed)
    m1, m15 = c.m1, c.m15
    end = int(m1.time[-1])
    mins = {}
    for i in range(len(m1)):
        tt = int(m1.time[i])
        row = (float(m1.open[i]), float(m1.high[i]), float(m1.low[i]), float(m1.close[i]))
        if tt < t:
            mins[tt] = row
        elif tt == t:
            o, cl = row[0], row[0] + rng.uniform(-25, 25)
            mins[tt] = (o, max(o, cl) + rng.uniform(0, 10), min(o, cl) - rng.uniform(0, 10), cl)
        elif tt == end or rng.random() > 0.15:
            mins[tt] = _arbitrary(rng)
    for tt in range(t + 60, end, 60):
        if tt not in mins and c.sched.scheduled(tt) and rng.random() < 0.3:
            mins[tt] = _arbitrary(rng)
    bars = {int(m15.time[i]): (float(m15.open[i]), float(m15.high[i]), float(m15.low[i]), float(m15.close[i]))
            for i in range(len(m15)) if int(m15.time[i]) <= t - 900}
    for s in range(int(m15.time[0]) - int(m15.time[0]) % 900, end + 60, 900):
        if s > t - 900 and rng.random() > 0.2:
            bars[s] = _arbitrary(rng)
    m1s = data.Bars.from_rows(60, [(k,) + mins[k] for k in sorted(mins)])
    m15s = data.Bars.from_rows(900, [(k,) + bars[k] for k in sorted(bars)])
    return dataclasses.replace(c, m1=m1s, m15=m15s)


def _view(paths, t):
    """Everything decided or booked at or before t, per path."""
    out = {}
    for sc, run in paths.items():
        dec, booked, cases, held, marks = [], [], [], [], []
        for r in run.records:
            if r["E"] > t:
                continue
            ex = r.get("exec") or {}
            dec.append((r["bar_open"], r["E"], r.get("reason"), r.get("population"),
                        ex.get("execution_validity_status"), ex.get("proposed_volume"), ex.get("required_margin")))
            res = r.get("result")
            if res is None:
                continue
            if portfolio.frees_slot(res.exit_time, t):
                booked.append((r["E"], res.outcome, res.exit_time, round(res.exit_leg, 9), res.is_loss))
            else:
                held.append(r["E"])
            cases += [(r["E"],) + d for d in res.decisions if d[1] <= t]
            marks += [(r["E"], mt, round(v, 9)) for mt, v in res.marks if mt <= t]
        out[sc] = (dec, booked, cases, held, marks)
    return out


def test_25_causality_metamorphic():
    for n, (c, opps) in enumerate((_k_fixture(prior_losses=True), _k_fixture(drop=True, prior_losses=True))):
        base = portfolio.run_paths(opps, c)
        assert any(r["result"].decisions for r in base["S"].population("C")), "fixture: a C5.8 decision"
        instants = range(at(9, 0), at(10, 50), 60)
        changed = 0
        for t in instants:
            alt = portfolio.run_paths(opps, _scramble(c, t, 100000 * n + t // 60))
            assert _view(base, t) == _view(alt, t), (n, t)
            changed += _view(base, at(10, 50)) != _view(alt, at(10, 50))
        assert changed > len(instants) // 2, ("fixture: the scramble must change what follows t", changed)
    snap = snapshot()
    b = _bar_epoch(SNAPSHOT_SIGNALS[1])
    d = clock.decision_instant(b, clock.BASELINE_DELAY)
    m15 = snap["m15"]
    rng = np.random.default_rng(25)
    later = m15.time > d - 900
    cols = [a.copy() for a in (m15.open, m15.high, m15.low, m15.close, m15.volume)]
    for a in cols:
        a[later] = rng.uniform(1000, 5000, int(later.sum()))
    m15s = data.Bars(900, m15.time, *cols[:4], volume=cols[4], spread=m15.spread)
    a, z = portfolio.signal_at(m15, b, PARAMS), portfolio.signal_at(m15s, b, PARAMS)
    assert (a["signal"], a["reason"], a["diag"]) == (z["signal"], z["reason"], z["diag"]), \
        "the strategy decision at D reads only M15 bars closed by D"


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


def _frame_in_utc(self, i0, i1):
    import pandas as pd
    return pd.DataFrame({
        "datetime": pd.to_datetime([clock.server_to_utc(int(t)) for t in self.time[i0:i1]], unit="s", utc=True),
        "open": self.open[i0:i1], "high": self.high[i0:i1], "low": self.low[i0:i1],
        "close": self.close[i0:i1], "volume": self.volume[i0:i1],
        "spread": self.spread[i0:i1], "real_volume": 0}).reset_index(drop=True)


def _resolver(tp_first=False, sl_gap_at_level=False, tp_gap_at_open=False):
    def resolve_minute(res, trade, m):
        t, o, h, l, _ = m
        if t > trade.entry_time and trade.open_beyond_sl(o):
            exits._exit(res, trade, "SL_HIT", t + 60, trade.sl if sl_gap_at_level else trade.leg(o), ("gap",))
            return True
        if t > trade.entry_time and trade.open_beyond_tp(o):
            exits._exit(res, trade, "TP_HIT", t + 60, trade.leg(o) if tp_gap_at_open else trade.tp, ("gap",))
            return True
        s, p = trade.sl_hit(l, h), trade.tp_hit(l, h)
        if p and (tp_first or not s):
            exits._exit(res, trade, "TP_HIT", t + 60, trade.tp)
            return True
        if s:
            exits._exit(res, trade, "SL_HIT", t + 60, trade.sl, ("AMBIGUOUS",) if p else ())
            return True
        exits._mark(res, trade, m)
        return False
    return resolve_minute


def _rebuild_last_open(minutes):
    return (minutes[-1][1], max(m[2] for m in minutes), min(m[3] for m in minutes), minutes[-1][4])


def _mark_on_bid(res, trade, m):
    t, _, h, l, c = m
    res.marks.append((t + 60, trade.r(c)))
    res.adverse_marks.append((t + 60, trade.r(l if trade.long else h)))


def _hole_at_edge_ignored(sched, t):
    edge = not sched.scheduled(t - 900) or not sched.scheduled(t + 900)
    return sched.scheduled(t) and not edge


def _path_unknown_per_bar(results):
    return sum(1 for r in results for d in r.decisions if d[2] in exits.UNRESOLVABLE)


def _risk_gate_slot_first(signal, spread_points, session, params, symbol_info, open_count, daily_losses,
                          balance=portfolio.BALANCE, _f=portfolio.risk_gate):
    if open_count >= params.get("max_open_trades", 1):
        return False, "Max open trades reached", {}
    return _f(signal, spread_points, session, params, symbol_info, open_count, daily_losses, balance)


def _decide_b_at_touch(res, trade, m1, m15, sched, b0, post, first_hole, scenario, _f=exits._decide_b):
    step = _f(res, trade, m1, m15, sched, b0, post, first_hole, scenario)
    touch = [m for m in post if m[0] > first_hole and trade.sl_hit(m[3], m[2])]
    if step == "exit" and res.outcome == "SL_HIT" and touch:
        res.exit_time = touch[0][0] + 60
    return step


def _window_shifted(by):
    def window(m15, signal_bar_open, count=200, _f=data.strategy_window):
        return _f(m15, signal_bar_open + by, count)
    return window


def _guard_off(t0, t1):
    return None


VARIANTS = {
    "test_01_decision_instant_and_entry_reads_open": [
        ("D without the bar close", clock, "decision_instant", _decision_without_close),
        ("entry reads the M1 close", clock, "entry_bid", _entry_reads_close)],
    "test_02_missing_entry_minute_is_quote_unavailable": [
        ("forward-filled entry minute", clock, "entry_bid", _entry_forward_fill)],
    "test_03_vwap_resets_at_server_midnight": [
        ("bars converted to UTC before the pipeline", data.Bars, "frame", _frame_in_utc)],
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
    "test_09_same_minute_sl_and_tp": [
        ("TP checked first", exits, "_resolve_minute", _resolver(tp_first=True))],
    "test_10_gaps_through_stop_and_target": [
        ("gapped stop filled at the level", exits, "_resolve_minute", _resolver(sl_gap_at_level=True)),
        ("gapped target credited at the open", exits, "_resolve_minute", _resolver(tp_gap_at_open=True))],
    "test_13_m15_rebuilt_from_m1": [
        ("rebuilt open from the last minute", data, "rebuild", _rebuild_last_open)],
    "test_16_hole_cases": [
        ("(d) SL always at the level", exits.Trade, "worse_sl_bid", lambda self, o: self.sl_trig),
        ("(b) pre-entry extreme ignored", exits.Trade, "attains_extreme", lambda self, m, bar, side: False)],
    "test_18_open_at_data_end_and_floating_loss": [
        ("marks only at exits", exits, "_mark", lambda res, trade, m: None)],
    "test_20_envelope_invalid_is_path_unknown": [
        ("no envelope check", data, "envelope_valid", lambda bar, minutes: True)],
    "test_22_equity_marks": [
        ("SHORT marked on the bid", exits, "_mark", _mark_on_bid)],
    "test_26_release_at_edges_and_under_c": [
        ("C5.4 bridges an unreleased hole", exits, "is_hole", lambda sched, t: False),
        ("release by C5.8(c) only", exits, "releases", lambda case, scenario: case == "c"),
        ("release at the bar open, not K", exits, "decision_time", lambda b0: b0),
        ("hole at a session edge ignored", exits, "is_hole", _hole_at_edge_ignored),
        ("PATH_UNKNOWN counted per bar", exits, "path_unknown_count", _path_unknown_per_bar)],
    "test_05b_union_hole_is_path_unknown": [
        ("offset-eu-1 alone in the window", clock, "schedule", _schedule_eu1_alone)],
    "test_12_strategy_parity_live_signals": [
        ("strategy window ending one bar early", data, "strategy_window", _window_shifted(-900)),
        ("probe-local session_of", clock, "session_of", _probe_session)],
    "test_11_slot_gate_by_mode": [
        ("CONFORMANCE reads the book", portfolio, "gate_state",
         lambda book, e, mode: (book.open_at(e), book.losses_on(e))),
        ("SIMULATION ignores the book", portfolio, "gate_state", lambda book, e, mode: ([], 0)),
        ("slot checked before the loss limit", portfolio, "risk_gate", _risk_gate_slot_first)],
    "test_19_exit_at_e_frees_slot_and_loss_on_exit_date": [
        ("slot freed only if X < E", portfolio, "frees_slot", lambda x, e: x is not None and x < e),
        ("loss counted on the entry date", portfolio, "counts_as_loss",
         lambda p, e: p.result.is_loss and portfolio.frees_slot(p.result.exit_time, e)
         and portfolio.server_date(p.entry_time) == portfolio.server_date(e))],
    "test_21_chronology_at_k": [
        ("C5.8 exit booked at the observed touch", exits, "_decide_b", _decide_b_at_touch),
        ("S stop not counted as a loss", exits.Result, "is_loss",
         property(lambda self: self.outcome == "SL_HIT"))],
    "test_24_scenario_agreement_hold": [
        ("verdict of S only", portfolio, "verdicts_agree", lambda rows: True),
        ("1% cap exclusive", portfolio, "within_cap", lambda s: s is not None and s < portfolio.PU_CAP),
        ("cap checked on S only", portfolio, "cap_ok", lambda rows: portfolio.within_cap(rows["S"]["share"]))],
    "test_25_causality_metamorphic": [
        ("C5.8 exit booked before K", exits, "decision_time", lambda b0: b0 + 600),
        ("strategy window ending one bar late", data, "strategy_window", _window_shifted(900))],
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
