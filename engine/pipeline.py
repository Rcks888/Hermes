"""The strategy pipeline shared by live Hermes and the replay (execution
contract C2.1, C2.2).

Lifted unchanged out of run_hermes.main(). Both callers import it, so there is
no backtest-only copy. Nothing here touches MT5, files or the clock.
"""
from engine import indicators, market_structure, strategy


def get_session_tag(utc_hour):
    myt_hour = (utc_hour + 8) % 24
    if 7 <= myt_hour < 15:
        return "ASIA"
    elif 15 <= myt_hour < 20:
        return "LONDON"
    elif 20 <= myt_hour or myt_hour < 5:
        return "NY"
    else:
        return "OFF"


def prepare_bars(candles, params):
    """Structure, indicators and S/R zones on closed M15 bars, in live order."""
    structure = market_structure.analyze(candles, params.get("swing_lookback", 3))
    candles = indicators.calculate_vwap(candles)
    candles = indicators.volume_average(candles, params.get("volume_avg_period", 20))
    candles = indicators.calculate_atr(candles)
    zones = indicators.find_sr_zones(
        candles, structure["swing_highs"], structure["swing_lows"],
        params.get("sr_zone_cluster_pct", 0.5),
        params.get("sr_min_touches", 2),
    )
    return candles, structure, zones


def evaluate_closed_bars(candles, params, stopwatch=None):
    """prepare_bars then strategy.evaluate. Returns a dict with candles,
    structure, zones, signal, reason and diag. A stopwatch, if given, is marked
    around strategy.evaluate exactly as live did inline."""
    candles, structure, zones = prepare_bars(candles, params)
    if stopwatch is not None:
        stopwatch.mark("evaluation_start_time")
    diag = {}
    signal, reason = strategy.evaluate(candles, structure, zones, params, diag)
    if stopwatch is not None:
        stopwatch.mark("evaluation_end_time")
    return {"candles": candles, "structure": structure, "zones": zones,
            "signal": signal, "reason": reason, "diag": diag}
