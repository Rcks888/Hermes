import logging
import numpy as np

logger = logging.getLogger("hermes.strategy")


def _is_pullback(df, trend, lookback=5):
    if len(df) < lookback + 1:
        return False

    recent = df.iloc[-lookback:]
    close = recent["close"].values

    if trend == "UP":
        return close[-1] < max(close[:-1])
    elif trend == "DOWN":
        return close[-1] > min(close[:-1])
    return False


def evaluate(df, structure, zones, params):
    trend = structure["trend"]
    trend_confirmed = structure["trend_confirmed"]

    if not trend_confirmed:
        return None

    if not _is_pullback(df, trend):
        return None

    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]
    price = last_candle["close"]

    from engine.indicators import price_near_zone
    touched_zone = price_near_zone(price, zones)

    from engine.price_action import is_momentum_candle, is_engulfing, is_reaction_candle

    recent_bodies = [abs(df.iloc[j]["close"] - df.iloc[j]["open"]) for j in range(len(df) - 4, len(df) - 1)]
    avg_body = sum(recent_bodies) / len(recent_bodies) if recent_bodies else 0.01

    has_momentum = is_momentum_candle(last_candle, avg_body, params.get("momentum_candle_multiplier", 2.0))
    engulf = is_engulfing(last_candle, prev_candle)
    reaction_dir, reaction_zone = is_reaction_candle(last_candle, zones)

    candle_confirmed = False
    if trend == "UP":
        candle_confirmed = has_momentum and last_candle["close"] > last_candle["open"]
        candle_confirmed = candle_confirmed or engulf == "BULLISH"
        candle_confirmed = candle_confirmed or reaction_dir == "BULLISH"
    elif trend == "DOWN":
        candle_confirmed = has_momentum and last_candle["close"] < last_candle["open"]
        candle_confirmed = candle_confirmed or engulf == "BEARISH"
        candle_confirmed = candle_confirmed or reaction_dir == "BEARISH"

    if not candle_confirmed:
        return None

    vwap = last_candle.get("vwap")
    if vwap is not None:
        if trend == "UP" and price < vwap:
            return None
        if trend == "DOWN" and price > vwap:
            return None

    vol = last_candle.get("volume", 0)
    vol_avg = last_candle.get("vol_avg", 0)
    vol_threshold = params.get("volume_threshold_pullback", 1.2)
    if vol_avg and vol_avg > 0 and vol < vol_avg * vol_threshold:
        return None

    at_zone = touched_zone is not None or reaction_zone is not None

    atr = last_candle.get("atr")
    if atr is None or atr <= 0:
        return None

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

    if sl_distance <= 0:
        return None

    min_rr = params.get("min_rr_ratio", 2.0)
    tp_distance = sl_distance * min_rr

    if direction == "LONG":
        tp_price = entry + tp_distance
    else:
        tp_price = entry - tp_distance

    min_atr = params.get("min_atr", 3.0)
    if atr < min_atr:
        return None

    sl_atr_low = params.get("sl_atr_low", 0.5)
    sl_atr_high = params.get("sl_atr_high", 2.5)
    if sl_distance < atr * sl_atr_low or sl_distance > atr * sl_atr_high:
        return None

    rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0

    signal = {
        "strategy": "PULLBACK",
        "direction": direction,
        "entry": round(entry, 2),
        "sl": round(sl_price, 2),
        "tp": round(tp_price, 2),
        "sl_distance": round(sl_distance, 2),
        "tp_distance": round(tp_distance, 2),
        "rr_ratio": round(rr_ratio, 2),
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

    logger.info(f"SIGNAL: {direction} @ {entry} SL={sl_price} TP={tp_price} RR={rr_ratio:.1f}")
    return signal
