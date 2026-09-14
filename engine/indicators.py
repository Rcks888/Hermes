import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("hermes.indicators")


def calculate_vwap(df):
    d = df.copy()
    d["date"] = d["datetime"].dt.date
    d["typical_price"] = (d["high"] + d["low"] + d["close"]) / 3
    d["tp_vol"] = d["typical_price"] * d["volume"]

    vwap_values = []
    for date, group in d.groupby("date"):
        cum_tp_vol = group["tp_vol"].cumsum()
        cum_vol = group["volume"].cumsum()
        vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
        vwap_values.extend(vwap.values)

    df = df.copy()
    df["vwap"] = vwap_values
    return df


def volume_average(df, period=20):
    df = df.copy()
    df["vol_avg"] = df["volume"].rolling(window=period).mean()
    return df


def calculate_atr(df, period=14):
    d = df.copy()
    d["prev_close"] = d["close"].shift(1)
    d["tr"] = np.maximum(
        d["high"] - d["low"],
        np.maximum(
            abs(d["high"] - d["prev_close"]),
            abs(d["low"] - d["prev_close"])
        )
    )
    d["atr"] = d["tr"].rolling(window=period).mean()
    df = df.copy()
    df["atr"] = d["atr"].values
    return df


def find_sr_zones(df, swing_highs, swing_lows, cluster_pct=0.5, min_touches=2):
    all_levels = []
    for sh in swing_highs:
        all_levels.append(sh["price"])
    for sl in swing_lows:
        all_levels.append(sl["price"])

    if not all_levels:
        return []

    all_levels.sort()
    zones = []
    used = set()

    for i, level in enumerate(all_levels):
        if i in used:
            continue
        cluster = [level]
        threshold = level * (cluster_pct / 100)
        for j in range(i + 1, len(all_levels)):
            if j in used:
                continue
            if abs(all_levels[j] - level) <= threshold:
                cluster.append(all_levels[j])
                used.add(j)
        used.add(i)

        if len(cluster) >= min_touches:
            zone_low = min(cluster)
            zone_high = max(cluster)
            zone_mid = sum(cluster) / len(cluster)
            zones.append({
                "low": round(zone_low, 2),
                "high": round(zone_high, 2),
                "mid": round(zone_mid, 2),
                "touches": len(cluster),
            })

    return zones


def price_near_zone(price, zones, tolerance_pct=0.15):
    for zone in zones:
        zone_range = zone["high"] - zone["low"]
        buffer = max(zone_range * 0.5, price * tolerance_pct / 100)
        if zone["low"] - buffer <= price <= zone["high"] + buffer:
            return zone
    return None
