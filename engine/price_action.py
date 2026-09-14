import logging
import numpy as np

logger = logging.getLogger("hermes.price_action")


def is_momentum_candle(candle, avg_body_size, multiplier=2.0):
    body = abs(candle["close"] - candle["open"])
    return body >= avg_body_size * multiplier


def is_engulfing(current, previous):
    curr_body_top = max(current["open"], current["close"])
    curr_body_bot = min(current["open"], current["close"])
    prev_body_top = max(previous["open"], previous["close"])
    prev_body_bot = min(previous["open"], previous["close"])

    if curr_body_top > prev_body_top and curr_body_bot < prev_body_bot:
        if current["close"] > current["open"] and previous["close"] < previous["open"]:
            return "BULLISH"
        elif current["close"] < current["open"] and previous["close"] > previous["open"]:
            return "BEARISH"
    return None


def is_reaction_candle(candle, zones, wick_body_ratio=2.0):
    body = abs(candle["close"] - candle["open"])
    if body == 0:
        body = 0.01

    upper_wick = candle["high"] - max(candle["open"], candle["close"])
    lower_wick = min(candle["open"], candle["close"]) - candle["low"]

    from engine.indicators import price_near_zone

    if lower_wick >= body * wick_body_ratio:
        zone = price_near_zone(candle["low"], zones)
        if zone is not None:
            return "BULLISH", zone

    if upper_wick >= body * wick_body_ratio:
        zone = price_near_zone(candle["high"], zones)
        if zone is not None:
            return "BEARISH", zone

    return None, None


def scan(df, zones, momentum_multiplier=2.0, momentum_lookback=3):
    patterns = []
    n = len(df)
    if n < momentum_lookback + 2:
        return patterns

    for i in range(momentum_lookback + 1, n):
        candle = df.iloc[i]
        prev = df.iloc[i - 1]

        recent_bodies = [abs(df.iloc[j]["close"] - df.iloc[j]["open"]) for j in range(i - momentum_lookback, i)]
        avg_body = sum(recent_bodies) / len(recent_bodies) if recent_bodies else 0.01

        if is_momentum_candle(candle, avg_body, momentum_multiplier):
            direction = "BULLISH" if candle["close"] > candle["open"] else "BEARISH"
            patterns.append({
                "type": "MOMENTUM",
                "direction": direction,
                "index": i,
                "datetime": candle["datetime"],
                "price": candle["close"],
            })

        engulf = is_engulfing(candle, prev)
        if engulf:
            patterns.append({
                "type": "ENGULFING",
                "direction": engulf,
                "index": i,
                "datetime": candle["datetime"],
                "price": candle["close"],
            })

        reaction_dir, reaction_zone = is_reaction_candle(candle, zones)
        if reaction_dir:
            patterns.append({
                "type": "REACTION",
                "direction": reaction_dir,
                "index": i,
                "datetime": candle["datetime"],
                "price": candle["close"],
                "zone": reaction_zone,
            })

    return patterns
