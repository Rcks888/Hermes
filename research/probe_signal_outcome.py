#!/usr/bin/env python3
"""Counterfactual outcome of signals Hermes alerted on but never traded.

Read-only. Places no orders and touches no config. Hermes is alert-only, so a
signal has no broker record; the only way to learn what would have happened is
to replay the bars that followed.

Two honesty constraints shape the whole file.

M15 OHLC does not order events inside a bar. If one bar's range covers both
target and stop, which came first is unknowable from this data, and guessing
would manufacture a win roughly half the time. Such bars are reported as
AMBIGUOUS and, where a single outcome is needed, resolved as the stop. That is
the conservative direction and it must stay conservative: a replay engine that
breaks ties optimistically will show a profitable strategy that does not exist.

Bar data is bid, and the spread falls on whichever side transacts at the ask.
A long buys at ask on entry, so its fill sits one spread above the quoted
figure while SL and TP trigger on the bid at their levels; on 2 October that
was 36 points against a 7.15 stop, 5% of risk, giving 1.856R. A short sells at
bid on entry and buys at ask on exit, so its realised distances are nominal but
both levels trigger when the bid is one spread below them: the stop comes
sooner and the target needs a deeper move. The rule lives in
entry_observation.spread_accounting; an earlier revision here mirrored the long
rule onto shorts, which moved the stop away and the target closer.

Both raw and spread-adjusted figures are reported, because the raw one flatters
the strategy.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine import mt5_connector, data_feed  # noqa: E402
from engine.entry_observation import spread_accounting  # noqa: E402
from engine.version import get_code_version  # noqa: E402

EVENTS = PROJECT_ROOT / "logs" / "events.jsonl"
OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"
SYMBOL = "XAUUSD"
FETCH_BARS = 700          # minimum; widened to reach the oldest signal
FETCH_BARS_CAP = 20000    # well inside the terminal's MaxBars
POINT = 0.01              # XAUUSD point size, confirmed by the sizing probe


def bars_after_signal(rows, bar_time):
    """Bars strictly after the signal bar, and whether the window covers it.

    rows is a list of (iso_time, high, low) in time order. A fixed-count fetch
    can start after an old signal; filtering on time > bar_time then hands the
    resolver bars from days later and it reports a confident wrong outcome. On
    23 Oct the 2 Oct and 7 Oct signals were resolved on bars from 13 Oct
    onwards this way. The signal bar itself must be in the window.
    """
    covered = any(t == bar_time for t, _, _ in rows)
    if not covered:
        return [], False
    return [r for r in rows if r[0] > bar_time], True


def outcome_r(outcome, realised_rr):
    """Result in R of the realised risk: TP pays realised_rr, SL loses 1."""
    if outcome == "TP_HIT":
        return realised_rr
    if outcome == "SL_HIT":
        return -1.0
    return None


def fetch_count(bar_times, now):
    """Upper bound on M15 bars back to the oldest signal (weekends included)."""
    oldest = min(datetime.fromisoformat(t) for t in bar_times)
    need = int((now - oldest).total_seconds() // 900) + 200
    return min(max(FETCH_BARS, need), FETCH_BARS_CAP)


def resolve(direction, entry, sl, tp, bars):
    """Walk bars forward and decide the outcome.

    bars is a list of (iso_time, high, low) strictly after the signal bar.
    Returns a dict. Never raises on ordinary data.
    """
    out = {"outcome": "OPEN", "bars_to_resolve": None, "resolved_at": None,
           "ambiguous": False, "mfe": 0.0, "mae": 0.0}
    long = direction == "LONG"
    best = worst = entry

    for i, (t, high, low) in enumerate(bars, start=1):
        best = max(best, high) if long else min(best, low)
        worst = min(worst, low) if long else max(worst, high)

        hit_tp = high >= tp if long else low <= tp
        hit_sl = low <= sl if long else high >= sl

        if hit_tp and hit_sl:
            # Both inside one bar. Order is unknowable at M15.
            out.update(outcome="SL_HIT", ambiguous=True,
                       bars_to_resolve=i, resolved_at=t)
            break
        if hit_tp:
            out.update(outcome="TP_HIT", bars_to_resolve=i, resolved_at=t)
            break
        if hit_sl:
            out.update(outcome="SL_HIT", bars_to_resolve=i, resolved_at=t)
            break

    out["mfe"] = round(abs(best - entry), 2)
    out["mae"] = round(abs(entry - worst), 2)
    return out


def resolve_with_spread(direction, entry, sl, tp, bars, spread_px):
    """Resolve on bid bars with the spread on the correct side of the trade."""
    lv = spread_accounting(direction, entry, sl, tp, spread_px)
    adj = resolve(direction, lv["fill"], lv["sl_trigger_bid"],
                  lv["tp_trigger_bid"], bars)
    return adj, lv


def selftest():
    """Prove the resolver detects each outcome before trusting it on real bars.

    Four probes this session returned confident wrong answers because their own
    mechanism was never checked against a case with a known result. A resolver
    that silently reports OPEN for everything would look like a quiet month.
    """
    cases = [
        ("LONG tp", "LONG", 100.0, 95.0, 110.0,
         [("t1", 102, 99), ("t2", 111, 101)], "TP_HIT", False),
        ("LONG sl", "LONG", 100.0, 95.0, 110.0,
         [("t1", 102, 99), ("t2", 103, 94)], "SL_HIT", False),
        ("LONG both", "LONG", 100.0, 95.0, 110.0,
         [("t1", 111, 94)], "SL_HIT", True),
        ("LONG open", "LONG", 100.0, 95.0, 110.0,
         [("t1", 102, 99)], "OPEN", False),
        ("SHORT tp", "SHORT", 100.0, 105.0, 90.0,
         [("t1", 101, 98), ("t2", 99, 89)], "TP_HIT", False),
        ("SHORT sl", "SHORT", 100.0, 105.0, 90.0,
         [("t1", 106, 99)], "SL_HIT", False),
    ]
    ok = True
    for name, d, e, sl, tp, bars, want, want_amb in cases:
        got = resolve(d, e, sl, tp, bars)
        good = got["outcome"] == want and got["ambiguous"] == want_amb
        ok &= good
        flag = "PASS" if good else "FAIL"
        print(f"  {flag}  {name:12} -> {got['outcome']}"
              f"{' AMBIGUOUS' if got['ambiguous'] else ''}")

    # Spread composition. Every case here discriminates: the mirrored-long
    # rule an earlier revision used gives a different answer on each SHORT.
    spread_cases = [
        # SHORT: bid high 104.6 means ask 105.1, so the 105 stop has filled.
        ("SHORT sl on ask", "SHORT", 100.0, 105.0, 90.0, 0.5,
         [("t1", 104.6, 99.0)], "SL_HIT", 2.0),
        # SHORT: bid low 89.8 means ask 90.3, so the 90 target has NOT filled.
        ("SHORT tp needs ask", "SHORT", 100.0, 105.0, 90.0, 0.5,
         [("t1", 101.0, 89.8)], "OPEN", 2.0),
        ("SHORT tp on ask", "SHORT", 100.0, 105.0, 90.0, 0.5,
         [("t1", 101.0, 89.4)], "TP_HIT", 2.0),
        # LONG regression: fill at ask, triggers on bid, RR below nominal.
        ("LONG sl on bid", "LONG", 100.0, 95.0, 110.0, 0.5,
         [("t1", 101.0, 95.0)], "SL_HIT", 9.5 / 5.5),
        ("LONG tp on bid", "LONG", 100.0, 95.0, 110.0, 0.5,
         [("t1", 110.0, 99.0)], "TP_HIT", 9.5 / 5.5),
    ]
    for name, d, e, sl, tp, s, bars, want, want_rr in spread_cases:
        got, lv = resolve_with_spread(d, e, sl, tp, bars, s)
        good = (got["outcome"] == want
                and abs(lv["realised_rr"] - want_rr) < 1e-9)
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  {name:18} -> {got['outcome']}"
              f"  rr {lv['realised_rr']:.3f}")

    # Window coverage. The fetched rows start after the signal bar, which is
    # what a fixed 700-bar fetch did to the 2 and 7 Oct signals on 23 Oct.
    rows = [("2026-10-13 07:00:00+00:00", 4190.0, 4180.0),
            ("2026-10-13 07:15:00+00:00", 4191.0, 4185.0)]
    bars, covered = bars_after_signal(rows, "2026-10-07 17:30:00+00:00")
    good = (not covered) and bars == []
    ok &= good
    print(f"  {'PASS' if good else 'FAIL'}  window misses signal -> "
          f"covered={covered} bars={len(bars)}")
    rows = [("2026-10-07 17:30:00+00:00", 4105.0, 4100.0)] + rows
    bars, covered = bars_after_signal(rows, "2026-10-07 17:30:00+00:00")
    good = covered and len(bars) == 2
    ok &= good
    print(f"  {'PASS' if good else 'FAIL'}  window holds signal  -> "
          f"covered={covered} bars={len(bars)}")

    # A stop-out is -1R, never the R:R ratio the trade was aiming for.
    for o, rr, want in (("SL_HIT", 2.0, -1.0), ("TP_HIT", 1.856, 1.856),
                        ("OPEN", 2.0, None)):
        got = outcome_r(o, rr)
        good = got == want
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'}  outcome_r {o:7} -> {got}")
    return ok


def load_signals():
    if not EVENTS.exists():
        return []
    sigs = []
    with open(EVENTS) as f:
        for line in f:
            line = line.strip()
            if not line or "SIGNAL_APPROVED" not in line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") != "SIGNAL_APPROVED":
                continue
            if not all(e.get(k) is not None for k in ("entry", "sl", "tp")):
                continue
            sigs.append(e)
    return sigs


def main():
    print("Validating the resolver against known outcomes:")
    if not selftest():
        print("\n  ABORT  resolver self-test failed; results would be "
              "meaningless. Fix the resolver before rerunning.")
        return 1

    sigs = load_signals()
    print(f"\nSIGNAL_APPROVED events in log: {len(sigs)}")
    if not sigs:
        print("  Nothing to resolve.")
        return 0

    ok, _ = mt5_connector.ensure_connected()
    if not ok:
        print("  MT5 unavailable; cannot fetch bars.")
        return 1

    count = fetch_count([str(e.get("bar_time")) for e in sigs],
                        datetime.now(timezone.utc))
    print(f"Fetching {count} closed M15 bars ...")
    df = data_feed.get_candles(SYMBOL, count=count)
    if df is None or df.empty:
        print("  No bars returned.")
        return 1
    print(f"  got {len(df)} bars, {df['datetime'].iloc[0]} .. "
          f"{df['datetime'].iloc[-1]}")
    all_rows = [(str(r.datetime), float(r.high), float(r.low))
                for r in df.itertuples()]

    rows = []
    uncovered = 0
    for e in sigs:
        bar_time = str(e.get("bar_time"))
        entry, sl, tp = float(e["entry"]), float(e["sl"]), float(e["tp"])
        direction = e.get("direction", "LONG")

        bars, covered = bars_after_signal(all_rows, bar_time)
        if not covered:
            uncovered += 1
            rows.append({"ts": e.get("ts"), "bar_time": bar_time,
                         "direction": direction, "entry": entry, "sl": sl,
                         "tp": tp, "window_covers_signal": False})
            print(f"\n  {direction} @ {entry}  bar {bar_time}  "
                  f"{e.get('session')}")
            print("    NOT RESOLVED  signal bar is outside the fetched window")
            continue

        raw = resolve(direction, entry, sl, tp, bars)

        spread_px = float(e.get("spread_points") or 0) * POINT
        adj, lv = resolve_with_spread(direction, entry, sl, tp, bars, spread_px)

        sl_dist_raw = abs(entry - sl)
        realised_r = (round(lv["realised_rr"], 3)
                      if lv["realised_rr"] is not None else None)

        row = {
            "ts": e.get("ts"), "bar_time": bar_time, "direction": direction,
            "entry": entry, "sl": sl, "tp": tp,
            "session": e.get("session"), "spread_points": e.get("spread_points"),
            "lots": (e.get("position_size")
                     or (e.get("risk_details") or {}).get("position_size")),
            "code_version": e.get("code_version"),
            "candle_hash": e.get("candle_hash"),
            "regime": (e.get("regime") or {}).get("structure_regime"),
            "vol_regime": (e.get("regime") or {}).get("volatility_regime"),
            "bars_available_after": len(bars),
            "sl_distance": round(sl_dist_raw, 2),
            "nominal_rr": round(abs(tp - entry) / sl_dist_raw, 3) if sl_dist_raw else None,
            "spread_cost_pct_of_risk": (round(100 * spread_px / sl_dist_raw, 1)
                                        if sl_dist_raw else None),
            "window_covers_signal": True,
            "realised_rr_after_spread": realised_r,
            "outcome_r_after_spread": outcome_r(adj["outcome"], realised_r),
            "spread_side": lv["spread_side"],
            "fill": round(lv["fill"], 2),
            "sl_trigger_bid": round(lv["sl_trigger_bid"], 2),
            "tp_trigger_bid": round(lv["tp_trigger_bid"], 2),
            "raw": raw, "spread_adjusted": adj,
        }
        rows.append(row)

        print(f"\n  {direction} @ {entry}  bar {bar_time}  {e.get('session')}")
        print(f"    sl {sl}  tp {tp}  sl_dist {sl_dist_raw:.2f}  "
              f"lots {row['lots']}")
        print(f"    regime {row['regime']}/{row['vol_regime']}  "
              f"bars after {len(bars)}")
        print(f"    raw        {raw['outcome']:7}"
              f"{' AMBIGUOUS' if raw['ambiguous'] else ''}"
              f"  after {raw['bars_to_resolve']} bars"
              f"  MFE {raw['mfe']}  MAE {raw['mae']}")
        print(f"    w/ spread  {adj['outcome']:7}"
              f"{' AMBIGUOUS' if adj['ambiguous'] else ''}"
              f"  result {row['outcome_r_after_spread']}R"
              f"  (rr {realised_r} vs nominal {row['nominal_rr']},"
              f" spread = {row['spread_cost_pct_of_risk']}% of risk)")

    result = {
        "probe": "signal_outcome",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "code_version": get_code_version(),
        "note": ("Counterfactual. Hermes placed no orders. Same-bar "
                 "target/stop collisions resolve to the stop because M15 OHLC "
                 "cannot order intrabar events."),
        "signals": rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"signal_outcome_{stamp}.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nWritten: {out}")

    n = len(rows) - uncovered
    print("\n" + "=" * 66)
    if uncovered:
        print(f"  {uncovered} signal(s) NOT resolved: outside the bar window.")
    print(f"  {n} signal{'s' if n != 1 else ''} resolved. This is a sample of "
          f"{n}.")
    print("  It cannot support any claim about edge, win rate or expectancy,")
    print("  and no parameter may be changed on the strength of it.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
