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

Bar data is bid. A long is filled at ask, so the real entry sits one spread
above the quoted figure while the stop does not move. On this signal the spread
was 36 points against a 7.15 stop, so it consumes 5% of the risk and shrinks
the realised reward below the nominal 2.0R. Both raw and spread-adjusted
figures are reported, because the raw one flatters the strategy.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from engine import mt5_connector, data_feed  # noqa: E402
from engine.version import get_code_version  # noqa: E402

EVENTS = PROJECT_ROOT / "logs" / "events.jsonl"
OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"
SYMBOL = "XAUUSD"
FETCH_BARS = 700          # ~7 days of M15, enough for any signal this month
POINT = 0.01              # XAUUSD point size, confirmed by the sizing probe


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

    print(f"Fetching {FETCH_BARS} closed M15 bars ...")
    df = data_feed.get_candles(SYMBOL, count=FETCH_BARS)
    if df is None or df.empty:
        print("  No bars returned.")
        return 1
    print(f"  got {len(df)} bars, {df['datetime'].iloc[0]} .. "
          f"{df['datetime'].iloc[-1]}")

    rows = []
    for e in sigs:
        bar_time = str(e.get("bar_time"))
        entry, sl, tp = float(e["entry"]), float(e["sl"]), float(e["tp"])
        direction = e.get("direction", "LONG")

        after = df[df["datetime"].astype(str) > bar_time]
        bars = [(str(r.datetime), float(r.high), float(r.low))
                for r in after.itertuples()]

        raw = resolve(direction, entry, sl, tp, bars)

        # Spread-adjusted: long fills at ask, short at bid.
        spread_px = float(e.get("spread_points") or 0) * POINT
        entry_adj = entry + spread_px if direction == "LONG" else entry - spread_px
        adj = resolve(direction, entry_adj, sl, tp, bars)

        sl_dist_raw = abs(entry - sl)
        sl_dist_adj = abs(entry_adj - sl)
        tp_dist_adj = abs(tp - entry_adj)
        realised_r = round(tp_dist_adj / sl_dist_adj, 3) if sl_dist_adj else None

        row = {
            "ts": e.get("ts"), "bar_time": bar_time, "direction": direction,
            "entry": entry, "sl": sl, "tp": tp,
            "session": e.get("session"), "spread_points": e.get("spread_points"),
            "lots": (e.get("risk_details") or {}).get("position_size"),
            "code_version": e.get("code_version"),
            "candle_hash": e.get("candle_hash"),
            "regime": (e.get("regime") or {}).get("structure_regime"),
            "vol_regime": (e.get("regime") or {}).get("volatility_regime"),
            "bars_available_after": len(bars),
            "sl_distance": round(sl_dist_raw, 2),
            "nominal_rr": round(abs(tp - entry) / sl_dist_raw, 3) if sl_dist_raw else None,
            "spread_cost_pct_of_risk": (round(100 * spread_px / sl_dist_raw, 1)
                                        if sl_dist_raw else None),
            "realised_rr_after_spread": realised_r,
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
              f"  realised {realised_r}R vs nominal {row['nominal_rr']}R"
              f"  (spread = {row['spread_cost_pct_of_risk']}% of risk)")

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

    n = len(rows)
    print("\n" + "=" * 66)
    print(f"  {n} signal{'s' if n != 1 else ''} resolved. This is a sample of "
          f"{n}.")
    print("  It cannot support any claim about edge, win rate or expectancy,")
    print("  and no parameter may be changed on the strength of it.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
