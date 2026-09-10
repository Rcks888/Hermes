import logging
from datetime import datetime, timezone

import pandas as pd

from engine import mt5_connector

logger = logging.getLogger("hermes.data")


def get_candles(symbol="XAUUSD", timeframe=None, count=200):
    mt5 = mt5_connector.mt5
    if timeframe is None:
        timeframe = 15  # M15
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        logger.error(f"Failed to get candles: {mt5.last_error()}")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.rename(columns={
        "time": "datetime",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "tick_volume": "volume",
        "spread": "spread",
        "real_volume": "real_volume",
    }, inplace=True)
    return df


def get_current_spread(symbol="XAUUSD"):
    mt5 = mt5_connector.mt5
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Failed to get tick: {mt5.last_error()}")
        return None
    return tick.ask - tick.bid


def get_positions(symbol="XAUUSD"):
    mt5 = mt5_connector.mt5
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return []
    result = []
    for p in positions:
        result.append({
            "ticket": p.ticket,
            "type": "BUY" if p.type == 0 else "SELL",
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "time": datetime.fromtimestamp(p.time, tz=timezone.utc),
        })
    return result
