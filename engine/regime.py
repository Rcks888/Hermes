#!/usr/bin/env python3
"""Deterministic regime classifier (Priority 0c).

No machine learning, no training data, no signals required. This exists so
replay results can be conditioned on market state from the first run, and so
the Guardian layer has a baseline that a model must later beat rather than
merely differ from.

TWO INDEPENDENT DIMENSIONS
    structure_regime   TREND / RANGE / TRANSITION / UNCERTAIN
    volatility_regime  HIGH / NORMAL / LOW / UNCERTAIN

Deliberately not one mutually exclusive label. TREND with HIGH volatility is a
real and important state for gold, and a single label cannot express it. Any
scheme that forces one label per bar destroys that distinction silently.

THRESHOLDS ARE FROZEN
Every parameter below is fixed as of PARAMS_VERSION and was chosen before any
outcome data existed -- Hermes has produced zero signals, so there is nothing
to have fitted to. ADX 25 and 20 are the conventional Wilder trend and range
levels, not values tuned here. Changing any of them creates a new
PARAMS_VERSION and invalidates comparisons across the boundary.

CAUSALITY
Every computation uses trailing data only, up to and including the decision
bar. classify() reads the frame it is given and nothing beyond it.
classify_series() is a vectorised equivalent for replay, and the two are tested
for exact agreement precisely because a vectorised implementation is where
lookahead normally creeps in.

CIRCULARITY SAFEGUARD
The trend gate in strategy.py and this classifier both read trend information.
A regime label is contextual evidence and never automatic proof that the gate
is right or wrong:
  - high rejection in TREND means investigate whether the gate is too strict,
    delayed, or measuring a different horizon
  - high rejection in RANGE is consistent with intended filtering, but is not
    on its own sufficient to validate the gate
Reporting must state both readings rather than choosing the flattering one.
"""

import numpy as np
import pandas as pd

PARAMS_VERSION = "regime-1.0"

# --- Structure ---------------------------------------------------------
ADX_PERIOD = 14
ADX_TREND_MIN = 25.0      # conventional Wilder trending threshold
ADX_RANGE_MAX = 20.0      # conventional Wilder non-trending threshold
EFFICIENCY_LOOKBACK = 20  # bars, 5 hours on M15
EFFICIENCY_TREND_MIN = 0.35
EFFICIENCY_RANGE_MAX = 0.25

# --- Volatility --------------------------------------------------------
ATR_PERIOD = 14
ATR_PERCENTILE_LOOKBACK = 100   # bars, ~25 hours on M15
VOL_HIGH_PERCENTILE = 70.0
VOL_LOW_PERCENTILE = 30.0

# Wilder smoothing needs roughly two periods to settle, and the ATR percentile
# needs a full lookback window before its rank means anything.
MIN_BARS = ATR_PERCENTILE_LOOKBACK + max(ATR_PERIOD, ADX_PERIOD) * 2

STRUCTURE_REGIMES = ("TREND", "RANGE", "TRANSITION", "UNCERTAIN")
VOLATILITY_REGIMES = ("HIGH", "NORMAL", "LOW", "UNCERTAIN")


def directional_efficiency(closes):
    """Net displacement divided by total path travelled, over the window.

    1.0 is a straight line, near 0.0 is pure chop. Returns None when the path
    length is zero, which means the market did not move at all -- a degenerate
    case that must not be reported as perfectly inefficient.
    """
    arr = np.asarray(closes, dtype=float)
    if len(arr) < 2 or np.isnan(arr).any():
        return None
    net = abs(arr[-1] - arr[0])
    path = np.abs(np.diff(arr)).sum()
    if path <= 0:
        return None
    return float(net / path)


def _wilder(series, period):
    """Wilder's smoothing, equivalent to an EMA with alpha = 1/period."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def adx_series(df, period=ADX_PERIOD):
    """Wilder ADX with +DI and -DI. Causal: bar i uses bars <= i only.

    Returns a DataFrame with columns adx, di_plus, di_minus.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = _wilder(tr, period)
    plus_di = 100.0 * _wilder(pd.Series(plus_dm, index=df.index), period) / atr
    minus_di = 100.0 * _wilder(pd.Series(minus_dm, index=df.index), period) / atr

    di_sum = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum.replace(0.0, np.nan)
    adx = _wilder(dx.fillna(0.0), period)

    return pd.DataFrame({"adx": adx, "di_plus": plus_di, "di_minus": minus_di})


def atr_series(df, period=ATR_PERIOD):
    """True-range rolling mean, matching indicators.calculate_atr."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = df["close"].astype(float).shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def _percentile_of_last(window):
    """Percentile rank of the final value within the window, 0-100."""
    arr = np.asarray(window, dtype=float)
    if np.isnan(arr).any():
        return np.nan
    return 100.0 * float((arr <= arr[-1]).mean())


def _label_structure(adx, efficiency):
    if adx is None or efficiency is None or np.isnan(adx):
        return "UNCERTAIN"
    trending = adx >= ADX_TREND_MIN and efficiency >= EFFICIENCY_TREND_MIN
    ranging = adx < ADX_RANGE_MAX and efficiency < EFFICIENCY_RANGE_MAX
    # The two conditions are disjoint by construction, since the ADX tests
    # cannot both hold. The explicit check documents the tie-break rather than
    # relying on the reader to verify disjointness.
    if trending and ranging:
        return "UNCERTAIN"
    if trending:
        return "TREND"
    if ranging:
        return "RANGE"
    return "TRANSITION"


def _label_volatility(atr_pct):
    if atr_pct is None or np.isnan(atr_pct):
        return "UNCERTAIN"
    if atr_pct >= VOL_HIGH_PERCENTILE:
        return "HIGH"
    if atr_pct <= VOL_LOW_PERCENTILE:
        return "LOW"
    return "NORMAL"


def classify(df):
    """Classify the regime as at the LAST bar of the supplied frame.

    Uses only the frame given, so passing a trailing window is sufficient and
    no future bar can influence the result. Returns the labels together with
    every metric behind them -- a label alone is not auditable.
    """
    out = {
        "params_version": PARAMS_VERSION,
        "structure_regime": "UNCERTAIN",
        "volatility_regime": "UNCERTAIN",
        "adx": None,
        "di_plus": None,
        "di_minus": None,
        "directional_efficiency": None,
        "atr_regime": None,
        "atr_percentile": None,
        "bars_available": int(len(df)),
        "bars_required": MIN_BARS,
        "insufficient_history": bool(len(df) < MIN_BARS),
    }
    if len(df) < MIN_BARS:
        return out

    a = adx_series(df)
    last_adx = a["adx"].iloc[-1]
    out["adx"] = None if pd.isna(last_adx) else round(float(last_adx), 3)
    out["di_plus"] = (None if pd.isna(a["di_plus"].iloc[-1])
                      else round(float(a["di_plus"].iloc[-1]), 3))
    out["di_minus"] = (None if pd.isna(a["di_minus"].iloc[-1])
                       else round(float(a["di_minus"].iloc[-1]), 3))

    eff = directional_efficiency(
        df["close"].iloc[-(EFFICIENCY_LOOKBACK + 1):].values)
    out["directional_efficiency"] = None if eff is None else round(eff, 4)

    atr = atr_series(df)
    out["atr_regime"] = (None if pd.isna(atr.iloc[-1])
                         else round(float(atr.iloc[-1]), 4))
    window = atr.iloc[-ATR_PERCENTILE_LOOKBACK:]
    atr_pct = _percentile_of_last(window.values)
    out["atr_percentile"] = None if np.isnan(atr_pct) else round(atr_pct, 2)

    out["structure_regime"] = _label_structure(out["adx"], eff)
    out["volatility_regime"] = _label_volatility(atr_pct)
    return out


def classify_series(df):
    """Vectorised per-bar classification for replay over long histories.

    Must agree exactly with classify() applied to each trailing slice. A
    vectorised implementation is the usual place lookahead enters a backtest,
    so that equivalence is asserted in the tests rather than assumed.
    """
    n = len(df)
    a = adx_series(df)
    atr = atr_series(df)

    close = df["close"].astype(float)
    net = (close - close.shift(EFFICIENCY_LOOKBACK)).abs()
    path = close.diff().abs().rolling(window=EFFICIENCY_LOOKBACK).sum()
    eff = net / path.replace(0.0, np.nan)

    pct = atr.rolling(window=ATR_PERCENTILE_LOOKBACK).apply(
        _percentile_of_last, raw=True)

    rows = []
    for i in range(n):
        if i + 1 < MIN_BARS:
            rows.append({
                "params_version": PARAMS_VERSION,
                "structure_regime": "UNCERTAIN",
                "volatility_regime": "UNCERTAIN",
                "adx": None, "di_plus": None, "di_minus": None,
                "directional_efficiency": None,
                "atr_regime": None, "atr_percentile": None,
                "bars_available": i + 1, "bars_required": MIN_BARS,
                "insufficient_history": True,
            })
            continue
        adx_v = a["adx"].iloc[i]
        eff_v = eff.iloc[i]
        pct_v = pct.iloc[i]
        adx_r = None if pd.isna(adx_v) else round(float(adx_v), 3)
        eff_r = None if pd.isna(eff_v) else round(float(eff_v), 4)
        rows.append({
            "params_version": PARAMS_VERSION,
            "structure_regime": _label_structure(adx_r, eff_r),
            "volatility_regime": _label_volatility(
                np.nan if pd.isna(pct_v) else float(pct_v)),
            "adx": adx_r,
            "di_plus": (None if pd.isna(a["di_plus"].iloc[i])
                        else round(float(a["di_plus"].iloc[i]), 3)),
            "di_minus": (None if pd.isna(a["di_minus"].iloc[i])
                         else round(float(a["di_minus"].iloc[i]), 3)),
            "directional_efficiency": eff_r,
            "atr_regime": (None if pd.isna(atr.iloc[i])
                           else round(float(atr.iloc[i]), 4)),
            "atr_percentile": None if pd.isna(pct_v) else round(float(pct_v), 2),
            "bars_available": i + 1,
            "bars_required": MIN_BARS,
            "insufficient_history": False,
        })
    return pd.DataFrame(rows, index=df.index)


def frozen_parameters():
    """The complete frozen parameter set, for preregistration and logging."""
    return {
        "params_version": PARAMS_VERSION,
        "adx_period": ADX_PERIOD,
        "adx_trend_min": ADX_TREND_MIN,
        "adx_range_max": ADX_RANGE_MAX,
        "efficiency_lookback": EFFICIENCY_LOOKBACK,
        "efficiency_trend_min": EFFICIENCY_TREND_MIN,
        "efficiency_range_max": EFFICIENCY_RANGE_MAX,
        "atr_period": ATR_PERIOD,
        "atr_percentile_lookback": ATR_PERCENTILE_LOOKBACK,
        "vol_high_percentile": VOL_HIGH_PERCENTILE,
        "vol_low_percentile": VOL_LOW_PERCENTILE,
        "min_bars": MIN_BARS,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(frozen_parameters(), indent=2))