import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("hermes.structure")


def find_swing_points(df, lookback=3):
    highs = []
    lows = []
    h = df["high"].values
    l = df["low"].values

    for i in range(lookback, len(df) - lookback):
        if all(h[i] >= h[i - j] for j in range(1, lookback + 1)) and \
           all(h[i] >= h[i + j] for j in range(1, lookback + 1)):
            highs.append({"index": i, "price": h[i], "datetime": df.iloc[i]["datetime"]})

        if all(l[i] <= l[i - j] for j in range(1, lookback + 1)) and \
           all(l[i] <= l[i + j] for j in range(1, lookback + 1)):
            lows.append({"index": i, "price": l[i], "datetime": df.iloc[i]["datetime"]})

    return highs, lows


def detect_structure(swing_highs, swing_lows):
    events = []

    for sh in swing_highs:
        events.append({"type": "SH", "index": sh["index"], "price": sh["price"], "datetime": sh["datetime"]})
    for sl in swing_lows:
        events.append({"type": "SL", "index": sl["index"], "price": sl["price"], "datetime": sl["datetime"]})

    events.sort(key=lambda x: x["index"])

    bos_list = []
    choch_list = []

    prev_sh = None
    prev_sl = None
    trend = None

    for e in events:
        if e["type"] == "SH":
            if prev_sh is not None:
                if e["price"] > prev_sh["price"]:
                    if trend == "UP":
                        bos_list.append({"direction": "UP", "index": e["index"], "price": e["price"], "datetime": e["datetime"]})
                    elif trend == "DOWN" or trend is None:
                        choch_list.append({"direction": "UP", "index": e["index"], "price": e["price"], "datetime": e["datetime"]})
                        trend = "UP"
                    else:
                        bos_list.append({"direction": "UP", "index": e["index"], "price": e["price"], "datetime": e["datetime"]})
                        trend = "UP"
            prev_sh = e

        elif e["type"] == "SL":
            if prev_sl is not None:
                if e["price"] < prev_sl["price"]:
                    if trend == "DOWN":
                        bos_list.append({"direction": "DOWN", "index": e["index"], "price": e["price"], "datetime": e["datetime"]})
                    elif trend == "UP" or trend is None:
                        choch_list.append({"direction": "DOWN", "index": e["index"], "price": e["price"], "datetime": e["datetime"]})
                        trend = "DOWN"
                    else:
                        bos_list.append({"direction": "DOWN", "index": e["index"], "price": e["price"], "datetime": e["datetime"]})
                        trend = "DOWN"
            prev_sl = e

    return bos_list, choch_list, trend


def analyze(df, lookback=3):
    swing_highs, swing_lows = find_swing_points(df, lookback)
    bos_list, choch_list, trend = detect_structure(swing_highs, swing_lows)

    recent_bos_up = sum(1 for b in bos_list if b["direction"] == "UP" and b["index"] > len(df) - 50)
    recent_bos_down = sum(1 for b in bos_list if b["direction"] == "DOWN" and b["index"] > len(df) - 50)

    trend_confirmed = False
    if trend == "UP" and recent_bos_up >= 2:
        trend_confirmed = True
    elif trend == "DOWN" and recent_bos_down >= 2:
        trend_confirmed = True

    return {
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "bos": bos_list,
        "choch": choch_list,
        "trend": trend,
        "trend_confirmed": trend_confirmed,
        "recent_bos_up": recent_bos_up,
        "recent_bos_down": recent_bos_down,
    }
