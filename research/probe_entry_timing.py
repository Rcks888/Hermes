#!/usr/bin/env python3
"""0g -- entry timing and execution contract. Evidence probe.

PURPOSE
    Prevent the replay engine from filling at a historical M15 bar close that
    live Hermes cannot execute. Hermes evaluates a completed bar but acts some
    minutes later, so the bar close is an observation, never an available
    entry.

WHAT THIS MEASURES, AND WHAT IT DOES NOT
    This probe reads M1 OHLC. M1 bars are not executable quotes. The result is
    therefore labelled M1_PRICE_PROXY and is not measured slippage. Converting
    it into a slippage figure requires historical bid/ask, which this account
    may not retain.

TIMESTAMP CONVENTION, FROZEN HERE (0g-1 partial)
    MT5 bar timestamps identify the bar OPEN. An M15 bar stamped T spans
    [T, T+900) and closes at T+900. To observe the price at an instant X we
    therefore read the OPEN of the M1 bar stamped X, never its close: the close
    of the M1 bar stamped X is an observation of instant X+60.

    All times here are the server clock as integer unix seconds. The existing
    data_feed labels these values UTC via `utc=True`, which is wrong -- the
    MetaQuotes-Demo server runs UTC+3. This probe does not depend on the label
    because every comparison is server-clock to server-clock, but the mislabel
    is recorded because cross-source joins will trip on it.

PRICE BASIS
    Determined empirically from tick data where available, not assumed. A long
    fills at ask while bid-based bars understate that fill, so assuming mid
    would halve the cost silently.

POPULATIONS ARE NOT INTERCHANGEABLE
    All-bar results describe timing exposure: how much gold moves in d minutes.
    Qualified-setup results describe strategy exposure: how much it moves after
    the specific conditions Hermes requires. With one qualified setup on record
    only the former is computable, and it must not be presented as the latter.
"""
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "research" / "data_feasibility"
EVENTS = PROJECT_ROOT / "logs" / "events.jsonl"
SYMBOL = "XAUUSD"
TF_M1, TF_M15 = 1, 15
M15_SECONDS = 900

# Preregistered delays, seconds after the M15 bar closes. Fixed before looking
# at any result so that the comparison cannot be chosen to flatter an option.
DELAYS = {"d1_min": 60, "d8_min_current_schedule": 480}

# Diagnostic reference stop only. The 2 October signal's stop was 7.15; using
# it to normalise all bars would imply every setup has that stop, which is why
# this is reported separately from ATR-relative figures.
REFERENCE_STOP = 7.15

PERCENTILES = (50, 90, 95, 99)


def observe(m1_by_time, instant):
    """Price at `instant` = OPEN of the M1 bar stamped `instant`.

    Returns (price, None) or (None, reason). Never forward-fills: a missing
    minute is a market closure or a gap, and inventing a price there would
    manufacture low drift exactly where real execution is worst.
    """
    bar = m1_by_time.get(instant)
    if bar is None:
        return None, "m1_minute_unavailable"
    return bar["open"], None


def drift(signal_close, delayed_price, direction=None):
    """Signed, absolute and direction-adjusted adverse drift.

    Absolute drift measures displacement but not harm. A long is hurt by a
    higher delayed price, a short by a lower one, so the same displacement is
    adverse for one and favourable for the other.
    """
    signed = delayed_price - signal_close
    out = {"signed": round(signed, 4), "absolute": round(abs(signed), 4)}
    if direction == "LONG":
        out["adverse"] = round(signed, 4)
    elif direction == "SHORT":
        out["adverse"] = round(-signed, 4)
    else:
        # No direction: report both assumptions rather than picking one.
        out["adverse_if_long"] = round(signed, 4)
        out["adverse_if_short"] = round(-signed, 4)
    return out


def resolve_from_delayed_entry(direction, entry, sl, tp, bars, entry_time):
    """Outcome of a trade entered at `entry_time`, not at the signal close.

    Three rules that a naive replay gets wrong:

    1. Only bars at or after entry_time can resolve the trade. A target touched
       while Hermes was still deciding was never available to it.
    2. A pre-entry touch of either level is reported, because it tells us the
       delay changed the trade rather than merely repriced it.
    3. A bar whose range spans both levels cannot be ordered at this
       granularity; it resolves to the stop and is flagged AMBIGUOUS.

    bars: list of (time_int, high, low) in ascending time.
    """
    long = direction == "LONG"
    pre = {"sl_touched": False, "tp_touched": False}
    for t, high, low in bars:
        if t >= entry_time:
            break
        if (low <= sl) if long else (high >= sl):
            pre["sl_touched"] = True
        if (high >= tp) if long else (low <= tp):
            pre["tp_touched"] = True

    out = {"outcome": "OPEN", "bars_to_resolve": None, "resolved_at": None,
           "ambiguous": False, "mfe": 0.0, "mae": 0.0,
           "pre_entry_touch": pre,
           "pre_entry_tp_discarded": pre["tp_touched"],
           "pre_entry_sl_discarded": pre["sl_touched"]}

    live = [b for b in bars if b[0] >= entry_time]
    best = worst = entry
    for i, (t, high, low) in enumerate(live, start=1):
        best = max(best, high) if long else min(best, low)
        worst = min(worst, low) if long else max(worst, high)
        hit_tp = (high >= tp) if long else (low <= tp)
        hit_sl = (low <= sl) if long else (high >= sl)
        if hit_tp and hit_sl:
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


def pct(values):
    """Percentile summary. Returns None fields rather than raising on n<2."""
    if not values:
        return {"n": 0}
    vs = sorted(values)
    out = {"n": len(vs), "min": round(vs[0], 4), "max": round(vs[-1], 4),
           "mean": round(statistics.fmean(vs), 4)}
    for p in PERCENTILES:
        idx = min(len(vs) - 1, int(round((p / 100.0) * (len(vs) - 1))))
        out[f"p{p}"] = round(vs[idx], 4)
    return out


def session_of(server_epoch):
    """Probe-local session label on the server clock (UTC+3).

    NOT the engine's classifier. Reconciling the two is a prerequisite of the
    0g-6 freeze; until then session splits here are indicative.
    """
    h = datetime.fromtimestamp(server_epoch, tz=timezone.utc).hour
    if 3 <= h < 11:
        return "ASIA"
    if 11 <= h < 16:
        return "LONDON"
    if 16 <= h < 24:
        return "NY"
    return "OFF"


# ---------------------------------------------------------------- 0g-3 controls

def controls():
    """Control tests. These gate everything below them.

    Four probes this session returned confident wrong answers because their
    mechanism was never checked against a case whose answer was known in
    advance. A drift probe is especially prone to this: a systematic
    one-minute indexing error produces a plausible, publishable, wrong number
    with no symptom.
    """
    results = []

    def check(name, got, want):
        ok = got == want
        results.append((name, ok, got, want))
        return ok

    # Constant series must produce exactly zero drift.
    flat = {t: {"open": 100.0} for t in range(1000, 2000, 60)}
    p, err = observe(flat, 1060)
    check("constant_series_zero_drift",
          (p, err, drift(100.0, p)["absolute"]), (100.0, None, 0.0))

    # Known offset must be reproduced exactly, not approximately.
    ramp = {t: {"open": 100.0 + (t - 1000) / 60.0} for t in range(1000, 2000, 60)}
    p, _ = observe(ramp, 1000 + 480)
    check("known_offset_exact", round(p - 100.0, 6), 8.0)

    # Adverse sign must invert with direction.
    up = drift(100.0, 103.0, "LONG")
    dn = drift(100.0, 103.0, "SHORT")
    check("adverse_sign_long", up["adverse"], 3.0)
    check("adverse_sign_short", dn["adverse"], -3.0)

    # A missing minute must be reported unavailable, never fabricated.
    gap = {1000: {"open": 100.0}}
    check("missing_minute_unavailable", observe(gap, 1060), (None, "m1_minute_unavailable"))

    # Timestamp boundary: the price at instant X is the OPEN of the bar stamped
    # X. Reading the close of that bar, or the open of the next, is the classic
    # one-minute error. Both wrong answers are distinguishable here.
    boundary = {1000: {"open": 10.0, "close": 20.0}, 1060: {"open": 30.0}}
    p, _ = observe(boundary, 1000)
    check("timestamp_boundary_uses_open_of_stamped_bar", p, 10.0)

    # A target touched before the delayed entry is not a win.
    bars = [(0, 111.0, 99.0), (60, 102.0, 94.0)]
    r = resolve_from_delayed_entry("LONG", 100.0, 95.0, 110.0, bars, entry_time=60)
    check("pre_entry_tp_not_a_win", (r["outcome"], r["pre_entry_tp_discarded"]),
          ("SL_HIT", True))

    # And the same trade entered at the signal bar would have won, which is
    # precisely the overstatement 0g exists to prevent.
    r0 = resolve_from_delayed_entry("LONG", 100.0, 95.0, 110.0, bars, entry_time=0)
    check("same_trade_wins_without_delay", r0["outcome"], "TP_HIT")

    # Ambiguity must survive the delayed-entry path too.
    r2 = resolve_from_delayed_entry("LONG", 100.0, 95.0, 110.0,
                                    [(60, 111.0, 94.0)], entry_time=60)
    check("ambiguous_resolves_to_stop", (r2["outcome"], r2["ambiguous"]),
          ("SL_HIT", True))

    all_ok = True
    for name, ok, got, want in results:
        all_ok &= ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            print(f"        got  {got}\n        want {want}")
    return all_ok


# ------------------------------------------------- 0g-2 feasibility and basis

def determine_price_basis(mt5, m1_df):
    """Compare an M1 bar's extremes against the bid and ask series inside it.

    If the bar high equals the maximum bid, the bars are bid-based. Returns a
    verdict plus the evidence, or UNDETERMINED -- which is a valid and
    important answer, because assuming mid would understate a long's fill by
    half the spread on every trade in the replay.
    """
    out = {"verdict": "UNDETERMINED", "reason": None, "evidence": {}}
    try:
        import pandas as pd
        row = m1_df.iloc[-1]
        t0 = int(row["epoch"])
        ticks = mt5.copy_ticks_range(SYMBOL, t0, t0 + 60, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            out["reason"] = f"no_ticks: {mt5.last_error()}"
            return out
        td = pd.DataFrame(ticks)
        if "bid" not in td or "ask" not in td or td.empty:
            out["reason"] = "tick_frame_missing_bid_ask"
            return out
        bid_hi, bid_lo = float(td["bid"].max()), float(td["bid"].min())
        ask_hi, ask_lo = float(td["ask"].max()), float(td["ask"].min())
        bh, bl = float(row["high"]), float(row["low"])
        out["evidence"] = {"bar_high": bh, "bar_low": bl, "tick_count": len(td),
                           "bid_high": bid_hi, "bid_low": bid_lo,
                           "ask_high": ask_hi, "ask_low": ask_lo}
        d_bid = abs(bh - bid_hi) + abs(bl - bid_lo)
        d_ask = abs(bh - ask_hi) + abs(bl - ask_lo)
        out["evidence"]["residual_vs_bid"] = round(d_bid, 5)
        out["evidence"]["residual_vs_ask"] = round(d_ask, 5)
        tol = 0.02
        if d_bid <= tol and d_bid < d_ask:
            out["verdict"] = "BID"
        elif d_ask <= tol and d_ask < d_bid:
            out["verdict"] = "ASK"
        elif abs(d_bid - d_ask) <= tol:
            out["verdict"] = "AMBIGUOUS_BID_ASK"
            out["reason"] = "bar extremes equidistant from bid and ask series"
        else:
            out["reason"] = "bar extremes match neither series within tolerance"
    except Exception as e:  # noqa: BLE001 - any bridge failure is UNDETERMINED
        out["reason"] = f"tick_query_failed: {type(e).__name__}: {e}"
    return out


def m1_quality(m1_df):
    """Span, gaps, duplicates. M1 coverage must not be assumed to match M15."""
    epochs = [int(v) for v in m1_df["epoch"].tolist()]
    uniq = sorted(set(epochs))
    diffs = [b - a for a, b in zip(uniq, uniq[1:])]
    gaps = [(a, b - a) for a, b in zip(uniq, uniq[1:]) if b - a > 60]
    gaps.sort(key=lambda x: -x[1])
    return {
        "bars": len(epochs),
        "duplicates": len(epochs) - len(uniq),
        "first": datetime.fromtimestamp(uniq[0], tz=timezone.utc).isoformat(),
        "last": datetime.fromtimestamp(uniq[-1], tz=timezone.utc).isoformat(),
        "span_days": round((uniq[-1] - uniq[0]) / 86400.0, 2),
        "expected_if_continuous": ((uniq[-1] - uniq[0]) // 60) + 1,
        "gap_count": len(gaps),
        "missing_minutes": sum(g[1] // 60 - 1 for g in gaps),
        "largest_gaps_hours": [
            {"from": datetime.fromtimestamp(a, tz=timezone.utc).isoformat(),
             "hours": round(s / 3600.0, 2)} for a, s in gaps[:5]],
        "non_60s_steps": len([d for d in diffs if d != 60]),
    }


# ------------------------------------------------------- fetch and 0g-4 probe

def fetch(mt5, timeframe, count):
    """Bulk fetch keeping the raw server-clock epoch.

    data_feed converts time with utc=True, which mislabels server time. This
    probe needs the integer epoch for exact minute indexing, so it converts
    nothing. One bulk call; iterating netref rows individually is what made an
    earlier probe appear to hang.
    """
    import pandas as pd
    rates = mt5.copy_rates_from_pos(SYMBOL, timeframe, 1, count)
    if rates is None or len(rates) == 0:
        return None, f"no_bars: {mt5.last_error()}"
    df = pd.DataFrame(rates)
    df = df.rename(columns={"time": "epoch", "tick_volume": "volume"})
    return df, None


def wilder_atr(highs, lows, closes, period=14):
    """Probe-local Wilder ATR, period matching regime-1.0's atr_period."""
    if len(closes) <= period:
        return [None] * len(closes)
    trs = [None]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    out = [None] * len(closes)
    seed = statistics.fmean(trs[1:period + 1])
    out[period] = seed
    prev = seed
    for i in range(period + 1, len(closes)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def timing_probe(m15_df, m1_by_time):
    """Drift between each M15 close and the price available d seconds later."""
    e = [int(v) for v in m15_df["epoch"].tolist()]
    h = [float(v) for v in m15_df["high"].tolist()]
    lo = [float(v) for v in m15_df["low"].tolist()]
    c = [float(v) for v in m15_df["close"].tolist()]
    atr = wilder_atr(h, lo, c)

    rows, excluded = [], {}
    for i in range(len(e)):
        close_time = e[i] + M15_SECONDS
        rec = {"bar_epoch": e[i], "close_time": close_time,
               "signal_close": c[i], "atr": atr[i],
               "session": session_of(close_time), "delays": {}}
        for name, d in DELAYS.items():
            price, err = observe(m1_by_time, close_time + d)
            if err:
                rec["delays"][name] = {"available": False, "reason": err}
                excluded[err] = excluded.get(err, 0) + 1
                continue
            dr = drift(c[i], price)
            dr["available"] = True
            dr["delayed_price"] = price
            dr["vs_reference_stop_pct"] = round(
                100.0 * dr["absolute"] / REFERENCE_STOP, 2)
            dr["vs_atr_pct"] = (round(100.0 * dr["absolute"] / atr[i], 2)
                                if atr[i] else None)
            rec["delays"][name] = dr
        rows.append(rec)
    return rows, excluded


def summarise(rows):
    """Aggregate by delay, then by session, then by chronological half."""
    out = {}
    mid = rows[len(rows) // 2]["close_time"] if rows else 0
    for name in DELAYS:
        got = [(r, r["delays"][name]) for r in rows
               if r["delays"].get(name, {}).get("available")]
        absd = [d["absolute"] for _, d in got]
        sign = [d["signed"] for _, d in got]
        blk = {
            "available": len(got),
            "unavailable": len(rows) - len(got),
            "absolute": pct(absd),
            "signed": pct(sign),
            "adverse_if_long": pct(sign),
            "adverse_if_short": pct([-s for s in sign]),
            "vs_reference_stop_pct": pct([d["vs_reference_stop_pct"] for _, d in got]),
            "vs_atr_pct": pct([d["vs_atr_pct"] for _, d in got
                               if d["vs_atr_pct"] is not None]),
            "by_session": {}, "by_period": {},
        }
        for s in ("ASIA", "LONDON", "NY", "OFF"):
            vals = [d["absolute"] for r, d in got if r["session"] == s]
            if vals:
                blk["by_session"][s] = pct(vals)
        for label, keep in (("first_half", lambda t: t < mid),
                            ("second_half", lambda t: t >= mid)):
            vals = [d["absolute"] for r, d in got if keep(r["close_time"])]
            if vals:
                blk["by_period"][label] = pct(vals)
        out[name] = blk
    return out


def qualified_setups(m1_by_time, m1_rows):
    """Re-resolve logged signals from a delayed entry rather than the close.

    This is the strategy-exposure population. It is tiny and is reported
    separately from the all-bar timing figures for that reason.
    """
    if not EVENTS.exists():
        return []
    out = []
    with open(EVENTS) as f:
        for line in f:
            if "SIGNAL_APPROVED" not in line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") != "SIGNAL_APPROVED":
                continue
            if not all(ev.get(k) is not None for k in ("entry", "sl", "tp")):
                continue
            entry_q = float(ev["entry"])
            sl, tp = float(ev["sl"]), float(ev["tp"])
            direction = ev.get("direction", "LONG")

            # Locate the signal bar's close in server-clock seconds by matching
            # the quoted entry against M1 opens is unreliable; instead use the
            # logged bar_time if it parses, else skip rather than guess.
            bt = ev.get("bar_time")
            try:
                close_time = int(datetime.fromisoformat(str(bt)).timestamp()) + M15_SECONDS
            except Exception:  # noqa: BLE001
                out.append({"ts": ev.get("ts"), "error": f"unparsable bar_time: {bt!r}"})
                continue

            rec = {"ts": ev.get("ts"), "bar_time": str(bt), "direction": direction,
                   "quoted_entry": entry_q, "sl": sl, "tp": tp,
                   "quoted_sl_distance": round(abs(entry_q - sl), 2),
                   "session": ev.get("session"),
                   "spread_points": ev.get("spread_points"), "delays": {}}
            for name, d in DELAYS.items():
                price, err = observe(m1_by_time, close_time + d)
                if err:
                    rec["delays"][name] = {"available": False, "reason": err}
                    continue
                dr = drift(entry_q, price, direction)
                sl_dist = abs(price - sl)
                res = resolve_from_delayed_entry(direction, price, sl, tp,
                                                 m1_rows, close_time + d)
                dr.update({
                    "available": True, "delayed_entry": price,
                    "delayed_sl_distance": round(sl_dist, 2),
                    "delayed_rr": (round(abs(tp - price) / sl_dist, 3)
                                   if sl_dist else None),
                    "outcome": res,
                })
                rec["delays"][name] = dr
            out.append(rec)
    return out


# ------------------------------------------------------------------------ main

def main():
    days = 10
    for a in sys.argv[1:]:
        if a.startswith("--days="):
            days = int(a.split("=", 1)[1])

    print("0g-3  Control tests (these gate every number below):")
    if not controls():
        print("\n  ABORT  controls failed. A drift probe with a broken "
              "mechanism yields a plausible wrong number with no symptom.")
        return 1

    from engine import mt5_connector  # noqa: E402
    from engine.version import get_code_version  # noqa: E402

    ok, _ = mt5_connector.ensure_connected()
    if not ok:
        print("\n  MT5 unavailable.")
        return 1
    mt5 = mt5_connector.mt5
    offset_h = mt5_connector.get_server_time_offset()

    m1_count, m15_count = days * 1440, days * 96 + 64
    print(f"\n0g-2  Fetching {m1_count} M1 and {m15_count} M15 bars "
          f"({days}d). M1 is the slow leg over the bridge.")
    m1_df, err = fetch(mt5, TF_M1, m1_count)
    if err:
        print(f"  M1 fetch failed: {err}")
        return 1
    m15_df, err = fetch(mt5, TF_M15, m15_count)
    if err:
        print(f"  M15 fetch failed: {err}")
        return 1
    print(f"  M1 {len(m1_df)} bars, M15 {len(m15_df)} bars")

    quality = m1_quality(m1_df)
    basis = determine_price_basis(mt5, m1_df)
    print(f"  M1 span {quality['span_days']}d  gaps {quality['gap_count']}  "
          f"missing minutes {quality['missing_minutes']}  "
          f"dupes {quality['duplicates']}")
    print(f"  price basis: {basis['verdict']}"
          f"{'  (' + str(basis['reason']) + ')' if basis['reason'] else ''}")

    m1_by_time = {int(r.epoch): {"open": float(r.open), "high": float(r.high),
                                 "low": float(r.low), "close": float(r.close)}
                  for r in m1_df.itertuples()}
    m1_rows = [(int(r.epoch), float(r.high), float(r.low))
               for r in m1_df.itertuples()]

    print("\n0g-4  Timing probe over all bars (timing exposure):")
    rows, excluded = timing_probe(m15_df, m1_by_time)
    summary = summarise(rows)

    for name, d in DELAYS.items():
        b = summary[name]
        a_, s_ = b["absolute"], b["vs_reference_stop_pct"]
        print(f"\n  {name}  (+{d}s after close)   n={a_['n']}  "
              f"unavailable={b['unavailable']}")
        if a_["n"]:
            print(f"    |drift| pts   p50 {a_['p50']}  p90 {a_['p90']}  "
                  f"p95 {a_['p95']}  p99 {a_['p99']}  max {a_['max']}")
            print(f"    % of {REFERENCE_STOP} ref stop  p50 {s_['p50']}%  "
                  f"p90 {s_['p90']}%  p95 {s_['p95']}%  p99 {s_['p99']}%")
            if b["vs_atr_pct"]["n"]:
                v = b["vs_atr_pct"]
                print(f"    % of ATR      p50 {v['p50']}%  p90 {v['p90']}%  "
                      f"p95 {v['p95']}%")
            for s, v in b["by_session"].items():
                print(f"      {s:7} n={v['n']:5} p50={v['p50']:<7} p95={v['p95']}")

    print("\n0g-4  Qualified setups (strategy exposure, reported separately):")
    setups = qualified_setups(m1_by_time, m1_rows)
    if not setups:
        print("  none on record")
    for s in setups:
        if s.get("error"):
            print(f"  {s['ts']}  {s['error']}")
            continue
        print(f"  {s['direction']} quoted {s['quoted_entry']} "
              f"sl_dist {s['quoted_sl_distance']} {s['session']}")
        for name, d in s["delays"].items():
            if not d.get("available"):
                print(f"    {name:28} unavailable ({d.get('reason')})")
                continue
            o = d["outcome"]
            print(f"    {name:28} entry {d['delayed_entry']}  "
                  f"adverse {d['adverse']:+.2f}  "
                  f"sl_dist {d['delayed_sl_distance']}  rr {d['delayed_rr']}")
            print(f"    {'':28} -> {o['outcome']}"
                  f"{' AMBIGUOUS' if o['ambiguous'] else ''}"
                  f"  pre-entry tp {o['pre_entry_tp_discarded']}"
                  f"  sl {o['pre_entry_sl_discarded']}")

    result = {
        "probe": "entry_timing_0g",
        "label": "M1_PRICE_PROXY",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "code_version": get_code_version(),
        "conventions": {
            "bar_timestamp_means": "bar open",
            "observation_at_instant_X": "open of M1 bar stamped X",
            "clock": "server clock, integer unix seconds",
            "server_offset_hours": offset_h,
            "known_defect": ("data_feed labels server time as UTC via "
                             "utc=True; comparisons here are server-to-server "
                             "so unaffected, but cross-source joins are not"),
            "forward_fill": "never; missing minutes are excluded",
            "session_classifier": "probe-local, not the engine's",
        },
        "preregistered_delays_seconds": DELAYS,
        "reference_stop": REFERENCE_STOP,
        "m1_quality": quality,
        "price_basis": basis,
        "excluded_observations": excluded,
        "all_bar_summary": summary,
        "qualified_setups": setups,
        "caveats": [
            "M1 OHLC is not an executable quote. This is a price proxy, not "
            "measured slippage.",
            "All-bar figures describe timing exposure; qualified-setup figures "
            "describe strategy exposure. The two populations are not "
            "interchangeable.",
            "Delays are preregistered, not selected after inspecting results.",
        ],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"entry_timing_{stamp}.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nWritten: {out}")
    print("\n" + "=" * 68)
    print("  Label: M1_PRICE_PROXY. Not measured slippage. Bar-close outcomes")
    print("  remain diagnostic only until 0g-5 and 0g-6 are frozen.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
