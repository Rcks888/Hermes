import logging
import numpy as np

logger = logging.getLogger("hermes.strategy")


def _is_pullback(df, trend, lookback=5):
    if len(df) < lookback + 1:
        return None

    recent = df.iloc[-lookback:]
    close = recent["close"].values

    if trend == "UP":
        return close[-1] < max(close[:-1])
    elif trend == "DOWN":
        return close[-1] > min(close[:-1])
    return False


def evaluate(df, structure, zones, params, diag=None):
    """Returns (signal_or_None, reason). Reason is always a string describing
    which gate blocked, or "OK" when a signal was produced.

    If a dict is passed as diag it is filled with every intermediate value as
    that value is computed, whether or not a later gate blocks. This exists so
    replay conformance can be checked numerically rather than by comparing
    reason strings, and so a rejected setup can be reconstructed without
    rerunning the strategy. Each value is recorded BEFORE the gate consuming
    it, so a blocked gate still reports the input that blocked it. Fields not
    yet reached are absent rather than null.
    """
    d = diag if diag is not None else {}
    trend = structure["trend"]
    trend_confirmed = structure["trend_confirmed"]

    d["trend"] = trend
    d["trend_confirmed"] = bool(trend_confirmed)
    d["bos_up"] = structure.get("recent_bos_up")
    d["bos_down"] = structure.get("recent_bos_down")
    d["swing_high_count"] = len(structure.get("swing_highs") or [])
    d["swing_low_count"] = len(structure.get("swing_lows") or [])
    d["sr_zone_count"] = len(zones or [])

    if not trend_confirmed:
        return None, f"trend_not_confirmed (trend={trend}, bos_up={structure['recent_bos_up']}, bos_down={structure['recent_bos_down']})"

    pullback = _is_pullback(df, trend)
    d["pullback"] = None if pullback is None else bool(pullback)
    if pullback is None:
        return None, "cannot_evaluate: insufficient bars for pullback check"
    if not pullback:
        return None, f"no_pullback (trend={trend}, last_close={df.iloc[-1]['close']:.2f})"

    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]
    price = last_candle["close"]

    from engine.indicators import price_near_zone
    touched_zone = price_near_zone(price, zones)

    from engine.price_action import is_momentum_candle, is_engulfing, is_reaction_candle

    body_lookback = params.get("momentum_candle_lookback", 3)
    body_start = max(0, len(df) - 1 - body_lookback)
    recent_bodies = [abs(df.iloc[j]["close"] - df.iloc[j]["open"]) for j in range(body_start, len(df) - 1)]
    avg_body = sum(recent_bodies) / len(recent_bodies) if recent_bodies else 0.01

    has_momentum = is_momentum_candle(last_candle, avg_body, params.get("momentum_candle_multiplier", 2.0))
    engulf = is_engulfing(last_candle, prev_candle)
    reaction_dir, reaction_zone = is_reaction_candle(last_candle, zones)

    d["avg_body"] = round(float(avg_body), 4)
    d["has_momentum"] = bool(has_momentum)
    d["engulfing"] = engulf
    d["reaction"] = reaction_dir
    d["near_sr_zone"] = touched_zone is not None

    candle_confirmed = False
    if trend == "UP":
        candle_confirmed = has_momentum and last_candle["close"] > last_candle["open"]
        candle_confirmed = candle_confirmed or engulf == "BULLISH"
        candle_confirmed = candle_confirmed or reaction_dir == "BULLISH"
    elif trend == "DOWN":
        candle_confirmed = has_momentum and last_candle["close"] < last_candle["open"]
        candle_confirmed = candle_confirmed or engulf == "BEARISH"
        candle_confirmed = candle_confirmed or reaction_dir == "BEARISH"

    d["candle_confirmed"] = bool(candle_confirmed)
    if not candle_confirmed:
        return None, f"no_candle_confirmation (momentum={has_momentum}, engulf={engulf}, reaction={reaction_dir})"

    vwap = last_candle.get("vwap")
    _vwap_ok = not (vwap is None or (isinstance(vwap, float) and np.isnan(vwap)))
    d["price"] = round(float(price), 2)
    d["vwap"] = round(float(vwap), 2) if _vwap_ok else None
    if vwap is None or (isinstance(vwap, float) and np.isnan(vwap)):
        return None, "cannot_evaluate: vwap unavailable"
    if trend == "UP" and price < vwap:
        return None, f"vwap_misaligned (LONG but price {price:.2f} < vwap {vwap:.2f})"
    if trend == "DOWN" and price > vwap:
        return None, f"vwap_misaligned (SHORT but price {price:.2f} > vwap {vwap:.2f})"

    d["vwap_aligned"] = True

    vol = last_candle.get("volume", 0)
    vol_avg = last_candle.get("vol_avg")
    vol_threshold = params.get("volume_threshold_pullback", 1.2)
    _va_ok = not (vol_avg is None
                  or (isinstance(vol_avg, float) and np.isnan(vol_avg))
                  or vol_avg <= 0)
    d["volume"] = float(vol) if vol is not None else None
    d["vol_avg"] = round(float(vol_avg), 1) if _va_ok else None
    d["vol_threshold"] = vol_threshold
    d["vol_ratio"] = (round(float(vol) / float(vol_avg), 3)
                      if _va_ok and vol else None)
    if vol_avg is None or (isinstance(vol_avg, float) and np.isnan(vol_avg)) or vol_avg <= 0:
        return None, "cannot_evaluate: volume average unavailable"
    if vol < vol_avg * vol_threshold:
        return None, f"volume_too_low ({vol} < {vol_threshold}x avg {vol_avg:.0f} = {vol_avg * vol_threshold:.0f})"

    at_zone = touched_zone is not None or reaction_zone is not None

    atr = last_candle.get("atr")
    _atr_ok = not (atr is None or (isinstance(atr, float) and np.isnan(atr)))
    d["atr"] = round(float(atr), 3) if _atr_ok else None
    d["at_zone"] = at_zone
    if atr is None or (isinstance(atr, float) and np.isnan(atr)):
        return None, "cannot_evaluate: atr unavailable"
    if atr <= 0:
        return None, f"atr_invalid ({atr})"

    sl_buffer = atr * params.get("sl_buffer_atr_multiplier", 0.5)

    if trend == "UP":
        direction = "LONG"
        swing_lows = structure["swing_lows"]
        recent_lows = [sl for sl in swing_lows if sl["index"] > len(df) - 20]
        if recent_lows:
            sl_price = min(sl["price"] for sl in recent_lows) - sl_buffer
        else:
            sl_price = last_candle["low"] - sl_buffer
        entry = price
        sl_distance = entry - sl_price
    else:
        direction = "SHORT"
        swing_highs = structure["swing_highs"]
        recent_highs = [sh for sh in swing_highs if sh["index"] > len(df) - 20]
        if recent_highs:
            sl_price = max(sh["price"] for sh in recent_highs) + sl_buffer
        else:
            sl_price = last_candle["high"] + sl_buffer
        entry = price
        sl_distance = sl_price - entry

    d["direction"] = direction
    d["entry"] = round(float(entry), 2)
    d["sl_raw"] = round(float(sl_price), 2)
    d["sl_buffer"] = round(float(sl_buffer), 3)
    d["sl_distance"] = round(float(sl_distance), 3)
    d["sl_atr_ratio"] = round(float(sl_distance) / float(atr), 3) if atr else None
    if sl_distance <= 0:
        return None, f"sl_distance_invalid ({sl_distance:.2f} — SL on wrong side of entry)"

    # Fixed-R exit policy: the target is placed mechanically at a multiple of
    # the initial risk distance. This is a target-placement parameter, not an
    # eligibility threshold — Hermes does not measure available reward and
    # reject setups below it. An S/R-derived target, which would make a genuine
    # minimum-RR filter meaningful, is a Priority 3 candidate policy.
    tp_rr_multiple = params.get("tp_rr_multiple", 2.0)
    tp_distance = sl_distance * tp_rr_multiple

    if direction == "LONG":
        tp_price = entry + tp_distance
    else:
        tp_price = entry - tp_distance

    d["tp_rr_multiple"] = tp_rr_multiple
    d["tp_distance"] = round(float(tp_distance), 3)
    d["tp"] = round(float(tp_price), 2)

    min_atr = params.get("min_atr", 3.0)
    d["min_atr"] = min_atr
    if atr < min_atr:
        return None, f"atr_too_low ({atr:.2f} < {min_atr})"

    sl_atr_low = params.get("sl_atr_low", 0.5)
    sl_atr_high = params.get("sl_atr_high", 2.5)
    if sl_distance < atr * sl_atr_low:
        return None, f"sl_too_tight ({sl_distance:.2f} < {sl_atr_low}x atr {atr:.2f} = {atr * sl_atr_low:.2f})"
    if sl_distance > atr * sl_atr_high:
        return None, f"sl_too_wide ({sl_distance:.2f} > {sl_atr_high}x atr {atr:.2f} = {atr * sl_atr_high:.2f})"

    signal = {
        "strategy": "PULLBACK",
        "direction": direction,
        "entry": round(entry, 2),
        "sl": round(sl_price, 2),
        "tp": round(tp_price, 2),
        "sl_distance": round(sl_distance, 2),
        "tp_distance": round(tp_distance, 2),
        "tp_rr_multiple": tp_rr_multiple,
        "atr": round(atr, 2),
        "trend": trend,
        "bos_count": structure["recent_bos_up"] if trend == "UP" else structure["recent_bos_down"],
        "vwap": round(vwap, 2) if vwap else None,
        "volume": vol,
        "vol_avg": round(vol_avg, 2) if vol_avg else None,
        "at_sr_zone": at_zone,
        "zone": touched_zone or reaction_zone,
        "candle_pattern": [],
        "signal_bar_time": str(last_candle["datetime"]),
    }

    if has_momentum:
        signal["candle_pattern"].append("MOMENTUM")
    if engulf:
        signal["candle_pattern"].append(f"ENGULFING_{engulf}")
    if reaction_dir:
        signal["candle_pattern"].append(f"REACTION_{reaction_dir}")

    logger.info(f"SIGNAL: {direction} @ {entry} SL={sl_price} TP={tp_price} target={tp_rr_multiple:.1f}R")
    return signal, "OK"
