# 🔱 Hermes — Forex Day Trading Bot

> Named after Hermes, the Greek god of speed and agility — fast intraday execution.

## Project Context

Hermes is part of **Project Olympus** — a multi-system automated trading platform:
- **Ares** (`~/Olympus/Ares/`) — US stock swing trading (live, V3.0, IBKR)
- **Athena** (`~/Olympus/Athena/`) — Backtesting engine & ML training
- **Hermes** (`~/Olympus/Hermes/`) — Forex day trading (NEW, OANDA)

Owner: Rickson Kang (beginner trader, Malaysia UTC+8, paper trading phase)
VPS: 134.209.103.20 (DigitalOcean Singapore, Ubuntu 24.04)
GitHub: github.com/Rcks888/Hermes (to be created, public)

## Why Hermes?

- Gain practical trading experience with higher trade frequency
- Forex = 24hr market = more opportunities than stocks
- Separate broker (OANDA) from stocks (IBKR) — no mixing
- Same VPS, same Telegram alerts, same discipline

## Broker: OANDA

- Demo account with virtual money (pending approval)
- Free REST API, pip install oandapyV20
- No gateway headaches like IBKR
- Supports MYR deposit for future live trading

## Trading Style

- **Type**: Day trading (open and close within same day)
- **Timeframe**: 15-minute candles (primary)
- **Pairs**: EUR/USD, GBP/USD (major pairs, tight spreads)
- **Sessions**: Asian session overlap (8-11 PM MYT) is prime time
- **Risk**: 1% per trade, max 2-3 losses/day then stop
- **R:R**: Minimum 2:1 risk-to-reward ratio

## Core Concepts (from MindMathMoney course)

### Market Structure (Big Picture)
- **Uptrend**: Higher highs + higher lows
- **Downtrend**: Lower lows + lower highs
- **Trading range**: Sideways movement
- **BOS (Break of Structure)**: Price breaks H/L in trend direction → trend continues
- **CHoCH (Change of Character)**: Price breaks against trend → first sign of reversal

### Price Action (Zoomed In)
- **Momentum candles**: Body ≥2x size of previous 3 candles = strong move
- **Engulfing candle**: Body completely covers previous candle = shift in control
- **Reaction candles (pin bars/hammers)**: Long wick at key level = rejection
- Patterns only matter at key levels, not in middle of nowhere

### Support & Resistance
- Draw ZONES, not lines — price reacts to areas
- Levels flip roles after break (resistance → support)
- 2-3 touches = significant zone
- Ascending triangle = bullish continuation (flat resistance + rising support)

### Volume
- High volume = real participation, move is significant
- Low volume = weak/fake move
- Volume spikes at S/R = big players stepping in
- Breakout on high volume = conviction; on low volume = trap risk

### VWAP (Volume Weighted Average Price)
- Day's average price weighted by volume
- Price above VWAP = buyers in control (bullish)
- Price below VWAP = sellers in control (bearish)
- Acts as magnet — smart money benchmark
- Resets daily — perfect for day trading

## Two Day Trading Strategies

### Strategy 1: Pullback Setup
```
Condition: Established trend (confirmed by BOS)
Setup:     Strong impulse move → pullback against trend
Entry A:   At support/key zone (with candlestick confirmation)
Entry B:   At breakout (when price breaks pullback high/low)
Stop Loss: Below/above pullback low/high
Target:    Previous swing high OR 2:1 R:R
Volume:    Prefer momentum candle at entry with high volume
```

### Strategy 2: Failure Test (False Breakout)
```
Condition: Clear S/R zone with 2-3 prior touches
Setup:     Price briefly breaks S/R then snaps back inside
Entry:     At candle close when price re-enters range
Stop Loss: Past the wick of the failed break
Target:    2:1 R:R or opposite side of range
Volume:    Traps breakout traders → creates momentum for reversal
```

## Filters for Both Strategies
- VWAP alignment (long only above VWAP, short only below)
- Volume confirmation (above average on signal candle)
- 15min timeframe on forex majors
- No trading during low-volatility dead zones
- Daily loss limit: 2-3 losses = done for the day

## Risk Management Rules
- Risk 1% of account per trade
- Position size calculated from SL distance (not feelings)
- 2:1 minimum R:R ratio
- Daily loss limit: stop after 2-3 consecutive losses
- No revenge trading, no FOMO
- Quality over quantity — 1 good trade > 10 average ones

## Technical Architecture (Planned)
```
~/Olympus/Hermes/
├── config/
│   └── strategy_params.json    # All parameters
├── engine/
│   ├── market_structure.py     # BOS, CHoCH, trend detection
│   ├── price_action.py         # Candle patterns, momentum
│   ├── indicators.py           # VWAP, S/R zones, volume
│   ├── strategy.py             # Pullback + Failure Test logic
│   ├── risk_manager.py         # Position sizing, daily limits
│   └── trader.py               # OANDA API execution
├── logs/
│   └── trades.json
├── dashboard.py                # Telegram alerts
├── run_hermes.sh               # Cron entry point
└── README.md
```

## Data Source
- OANDA API for 15min candles (real-time)
- VWAP calculated from intraday data
- Volume from OANDA tick volume

## Deployment
- Same VPS as Ares (134.209.103.20)
- Separate cron jobs, separate Telegram messages
- Same bot token, same chat ID (prefix messages with 🔱 HERMES)

## Phase Plan
1. **Phase 1**: Build engine + paper trade on OANDA demo
2. **Phase 2**: Collect 50+ trades, analyze win rate and R:R
3. **Phase 3**: Optimize based on data (adjust filters, not core strategy)
4. **Phase 4**: Go live with small real money (after Ares proves itself)

## Reference
- Course: MindMathMoney Full Day Trading Course (YouTube, ~1h45m)
- Key topics to deep-dive later: Liquidity, Chart Patterns, VWAP strategies, Trading Psychology
