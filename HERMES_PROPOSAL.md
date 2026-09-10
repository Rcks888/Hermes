# 🔱 Hermes — Forex Day Trading Bot Proposal

> Automated XAU/USD day trading bot using MT5 + Python with Telegram alerts.
> Broker: OANDA Global (via MetaTrader 5)

## Why MT5 + Python (Not OANDA REST API)

OANDA Global (Asia-Pacific region, including Malaysia) does **not** offer the fxTrade REST API.
The REST API (`oandapyV20`) is only available for OANDA's US/EU fxTrade platform.

OANDA Global provides two platforms: **MetaTrader 5** and **TradingView** (view-only).
For programmatic trading, MT5 is the only option.

| Considered | Verdict | Reason |
|------------|---------|--------|
| OANDA REST API (`oandapyV20`) | ❌ Not available | OANDA Global doesn't offer REST API for MY region |
| MetaTrader 5 + Python (`MetaTrader5` package) | ✅ Selected | Full trading API — data, orders, position management. Confirmed by OANDA Global support |
| Switch broker | ❌ Rejected | Already have OANDA account; avoid unnecessary complexity |
| TradingView | ❌ View only | No programmatic execution available |

**VPS consideration:** The `MetaTrader5` Python package is Windows-native.
On our Ubuntu VPS (134.209.103.20), we run MT5 headless via **Wine + Xvfb**.
If this proves unstable, fallback is a small Windows VPS (~$10-15/month).

## Gate 0: MT5 Infrastructure Stability

**Before any strategy code is written, MT5 connectivity must be proven stable.**

### Stability Soak (3-7 Days)

Run the MT5 connectivity module only — no strategy, no signals. Log everything:

| Metric | What to Log | Why |
|--------|-------------|-----|
| Connection up/down | Timestamps of every connect/disconnect | Measure Wine reliability |
| Candle pulls | Success/fail for each `copy_rates_from_pos` call | Confirm data feed works |
| Account info | Success/fail for each `account_info()` call | Confirm account access |
| Symbol specs | Exact symbol string, tick size, point value, lot step, min lot | Inputs for position sizing |
| Spread samples | Spread at each 15min cycle, tagged by session (Asia/London/NY) | Calibrate spread filter |
| Server vs VPS time | MT5 broker server time vs VPS system time (logged once at startup) | Detect clock drift; avoid candle alignment issues |

### What We Learn After 3-7 Days

- Whether Wine + MT5 is acceptable (< 2% downtime) or we need Windows VPS
- Exact symbol string (e.g. `XAUUSD` vs `XAUUSDm` vs broker-specific suffix)
- Realistic spread distribution by session → calibrate the spread filter
- Whether tick volume is usable for volume-based filters
- Whether position-sizing inputs (tick value, contract size) are trustworthy

### Pass/Fail Criteria

| Result | Action |
|--------|--------|
| Uptime ≥ 98% over 7 days | ✅ Proceed to strategy build |
| Uptime 90-98% | ⚠️ Investigate, fix, re-soak |
| Uptime < 90% or chronic reconnect failures | ❌ Switch to Windows VPS immediately |

### MT5 Auto-Reconnect

```
On every cycle:
  1. Check mt5.terminal_info() — is terminal connected?
  2. If disconnected:
     → Retry mt5.initialize() + mt5.login() up to 3 times
     → Backoff: 2s → 5s → 10s
  3. If reconnected:
     → Log recovery timestamp
     → Telegram: "🔱 HERMES — MT5 reconnected after [duration]"
     → Resume normal cycle
  4. If all 3 retries fail:
     → Skip this cycle entirely
     → Log error with timestamp
     → Telegram alert ONCE: "🔱 HERMES — MT5 connection lost, skipping cycle"
     → Do NOT alert again until recovered (no spam)
  5. If reconnect fails for 3-4 consecutive cycles (45-60 min):
     → Escalate as CRITICAL infrastructure failure
     → Telegram: "🚨 HERMES CRITICAL — MT5 down for [N] cycles. Manual intervention needed."
     → Halt all trading until manually restarted
```

## V1 Scope

**V1 launches with Pullback Strategy only.** Failure Test will be added in V2 after 100+ logged signals from Pullback. This reduces complexity and lets us validate one strategy cleanly before stacking.

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    HERMES ENGINE (VPS)                       │
│                                                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐  │
│  │  Market   │──▶│ Strategy │──▶│   Risk   │──▶│ Trader │  │
│  │ Analyzer  │   │  Engine  │   │ Manager  │   │ (MT5)  │  │
│  └──────────┘   └──────────┘   └──────────┘   └────────┘  │
│       │              │              │              │         │
│       └──────────────┴──────────────┴──────────────┘         │
│                          │                                   │
│                    ┌─────▼─────┐                             │
│                    │ Telegram  │                             │
│                    │  Alerts   │                             │
│                    └───────────┘                             │
└─────────────────────────────────────────────────────────────┘
```

## Execution Flow (Every 15-Minute Candle Close)

### Step 0: Connection Health Check

```
Every cycle starts here:
  → Check mt5.terminal_info() connected status
  → If disconnected → auto-reconnect sequence (see Gate 0)
  → If still down after retries → skip entire cycle, alert, done
  → If connected → proceed to Step 1
```

### Step 1: Data Collection

```
MT5 API  → mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 0, 200)
         → mt5.positions_get(symbol="XAUUSD")
         → mt5.account_info() → balance, equity, margin
         → mt5.symbol_info("XAUUSD") → spread, tick_size, tick_value, lot step
```

### Step 2: Market Structure Analysis

```
Candle Data → Identify Swing Highs/Lows (pivot points, lookback=3)
           → Classify Trend: Uptrend / Downtrend / Range
           → Detect BOS (Break of Structure)
           → Detect CHoCH (Change of Character)
           → Map S/R Zones (cluster swing points within 0.5% range)
```

### Step 3: Indicator Calculation

```
Candle Data → Calculate VWAP (daily reset, from MT5 candles)
           → Calculate Volume Moving Average (20-period)
           → Flag Momentum Candles (body ≥ 2x avg of last 3)
           → Flag Engulfing Candles
           → Flag Reaction Candles (wick ≥ 2x body at S/R zone)
```

### Step 4: Strategy Engine — Signal Detection

**Strategy 1: Pullback Setup**

```
IF   trend = confirmed (≥2 BOS in same direction)
AND  current price is in pullback (retracing against trend)
AND  price touches S/R zone OR breaks pullback high/low
AND  candlestick confirmation (momentum/engulfing/reaction)
AND  VWAP alignment (long above VWAP, short below VWAP)
AND  volume on signal candle > 1.2x average volume
THEN → SIGNAL: Pullback Entry

     Entry A: At S/R zone touch + candle confirmation
     Entry B: At breakout of pullback high/low
     SL: Below/above pullback extreme + buffer (ATR-based)
     TP: 2:1 R:R from entry
```

**Strategy 2: Failure Test (False Breakout) — V2, after 100+ Pullback signals**

```
IF   clear S/R zone exists (≥2 prior touches)
AND  price breaks beyond S/R zone briefly (wick pierces zone)
AND  candle CLOSES back inside the range
AND  reaction candle pattern detected (long wick rejection)
AND  VWAP alignment (long above VWAP, short below VWAP)
AND  volume spike on rejection candle > 1.5x average
THEN → SIGNAL: Failure Test Entry

     Entry: At candle close (back inside range)
     SL: Past the wick of the failed break + buffer
     TP: 2:1 R:R or opposite side of range
```

### Step 5: Risk Manager — Gate Check

```
Signal received → Check: Daily loss count < 3?
               → Check: No open position on XAU/USD?
               → Check: Signal R:R ≥ 2.0?
               → Check: SL distance is reasonable (not too tight/wide)?
               → Calculate position size:
                    risk_amount = account_balance × 0.01
                    position_size = risk_amount / SL_distance_in_price
               → ALL PASS? → Approve trade
               → ANY FAIL? → Reject + log reason
```

### Step 5b: No-Trade Filters

**All thresholds are numeric and enforced programmatically. No discretion.**

| Filter | Rule | Value | Rationale |
|--------|------|-------|-----------|
| **Spread** | Reject if spread > threshold | TBD — calibrated from stability soak spread data | "Points" meaning verified against MT5 symbol_info; value set after observing normal range |
| **Session** | Only trade during London + NY | 3:00 PM - 5:00 AM MYT (UTC+8) | Covers London open through NY close; skip Asia dead zone |
| **News blackout** | No new trades around high-impact events | ±30 min before, ±60 min after | NFP, FOMC, CPI, ECB — sourced from Forex Factory (daily scrape, cached locally) |
| **Weekend** | No new trades before weekend close | After Friday 4:00 PM EST | Avoid gap risk over weekend |
| **ATR gate** | Reject if SL distance is abnormal vs ATR(14) | SL < 0.5× ATR or SL > 2.5× ATR | Too tight = noise stop-out; too wide = outsized risk |
| **Min ATR** | Skip if market is dead | ATR(14) < 3.0 USD | Below this, not enough movement to hit 2:1 R:R |

### Step 6: Trade Execution

```
Phase 1 (Paper Trading - ALERT ONLY):
  → Send Telegram alert with setup details
  → Log signal to trades.json
  → YOU decide to execute manually on MT5

Phase 2 (Semi-Auto):
  → Send Telegram alert + wait 60 seconds
  → If you reply "GO" → execute
  → If no reply → skip

Phase 3 (Full Auto):
  → Place market order via mt5.order_send()
  → SL and TP attached in the order request
  → Send Telegram confirmation
  → Log trade to trades.json
```

### Step 7: Trade Management (While Position Open)

```
Every 15min cycle:
  → Check if SL or TP hit (MT5 server handles this)
  → Optional: Trail SL to breakeven after 1R profit
  → If trade closes → log result, update daily P&L
  → If daily loss limit hit → disable trading for the day
```

### Step 8: Telegram Alerts

```
🔱 HERMES — SIGNAL DETECTED
━━━━━━━━━━━━━━━━━━━━━
Strategy: Pullback Setup (Long)
Pair: XAU/USD
Entry: 2,345.50
Stop Loss: 2,338.20 (-7.30)
Take Profit: 2,360.10 (+14.60)
R:R: 2.0:1
Risk: 1.0% ($100.00)
Position Size: 0.14 lots
Volume: ✅ 1.8x average
VWAP: ✅ Price above
Trend: ✅ Uptrend (2 BOS)
━━━━━━━━━━━━━━━━━━━━━

🔱 HERMES — TRADE CLOSED
━━━━━━━━━━━━━━━━━━━━━
Result: ✅ WIN +$200.00
Duration: 3h 45m
Daily P&L: +$150.00 (2W / 1L)
━━━━━━━━━━━━━━━━━━━━━
```

## Architecture

```
~/Olympus/Hermes/
├── config/
│   ├── settings.json          # MT5 credentials, Telegram config, allow_live flag
│   ├── strategy_params.json   # All tunable parameters
│   └── news_calendar.json     # Cached high-impact events (daily scrape from Forex Factory)
├── engine/
│   ├── mt5_connector.py       # MT5 init, login, reconnect, health check
│   ├── data_feed.py           # Candle data, account info, symbol specs
│   ├── market_structure.py    # Swing points, BOS, CHoCH, trend
│   ├── indicators.py          # VWAP, volume avg, S/R zones
│   ├── price_action.py        # Momentum/engulfing/reaction candles
│   ├── strategy.py            # Pullback only (V1) — no Failure Test code paths until V2
│   ├── risk_manager.py        # Position sizing, daily limits, no-trade filters
│   └── trader.py              # MT5 order execution + management (Phase 2+)
├── alerts/
│   └── telegram.py            # Signal alerts, connection alerts, daily summary
├── logs/
│   ├── trades.json            # Trade journal with full signal metadata
│   ├── soak.json              # Stability soak data (uptime, spreads, symbol specs)
│   └── hermes.log             # Runtime logs
├── run_hermes.py              # Main loop entry point (idempotency, health check)
├── run_hermes.sh              # Cron wrapper (sleep 10 + exec)
├── requirements.txt
└── README.md
```

## Safety Mechanisms

### Demo-Only Hard Lock

```json
// in settings.json
"allow_live": false    // MUST remain false until ALL Phase 4 gates pass
                       // Manually flipped — no automated override
```

If `allow_live` is `false`, the bot **refuses** to send any order to a live account, even if accidentally connected to one. Hard check on every cycle.

### Idempotency (No Duplicate Signals)

Each signal is keyed by the M15 candle open timestamp + strategy name.
If the same key was already signaled → skip. Prevents:
- Duplicate Telegram alerts if cron fires twice
- Re-signaling the same bar after a reconnect

### Paper Trade Journal Fields

Every signal logged to `trades.json` includes:

| Field | Purpose |
|-------|---------|
| `signal_bar_time` | M15 candle timestamp that triggered the signal |
| `spread_at_signal` | Spread in points at detection time |
| `session_tag` | Asia / London / NY overlap |
| `filters_passed` | Which no-trade filters passed/failed and values |
| `strategy` | Pullback (V1) or Failure Test (V2) |
| `direction` | Long / Short |
| `entry`, `sl`, `tp` | Planned prices |
| `rr_ratio` | Planned R:R |
| `position_size` | Calculated lot size |
| `outcome` | Manually tagged: taken / skipped / SL hit / TP hit |
| `actual_entry`, `actual_exit` | Filled prices (Phase 2+) |
| `realized_rr` | Actual R:R achieved |
| `notes` | Free text for observations |

## Main Loop (`run_hermes.py`)

```python
# Simplified flow — runs every 15 minutes via cron
def main():
    # 0. Health check + reconnect
    if not mt5_connector.ensure_connected():
        telegram.alert_disconnected()
        return  # Skip entire cycle

    # 1. Fetch data
    candles = data_feed.get_candles("XAUUSD", mt5.TIMEFRAME_M15, count=200)
    account = data_feed.get_account()

    # 2. Analyze market
    structure = market_structure.analyze(candles)
    zones = indicators.find_sr_zones(candles)
    vwap = indicators.calculate_vwap(candles)
    vol_avg = indicators.volume_average(candles, period=20)

    # 3. Detect patterns
    patterns = price_action.scan(candles, zones)

    # 4. Check strategies
    signal = strategy.evaluate(structure, zones, vwap, vol_avg, patterns)

    # 5. Risk gate
    if signal and risk_manager.approve(signal, account):
        # 6. Execute or alert
        if config.mode == "ALERT_ONLY":
            telegram.send_signal(signal)
        else:
            trade = trader.execute(signal)
            telegram.send_execution(trade)

    # 7. Manage open trades
    trader.manage_open_positions(account)

    # 8. Daily summary at session end
    if is_session_end():
        telegram.send_daily_summary()
```

## Cron Schedule

```
# Run every 15 minutes, offset by 10 seconds to ensure candle is fully closed
# XAU/USD trades ~23hrs/day (Sun 6PM - Fri 5PM EST)
*/15 * * * 1-5  sleep 10 && cd ~/Olympus/Hermes && ./run_hermes.sh
```

> **Why +10 seconds?** Running exactly on `:00/:15/:30/:45` risks grabbing an incomplete bar. The 10s delay ensures the M15 candle is fully closed before we read it.

## Tunable Parameters (`strategy_params.json`)

```json
{
    "instrument": "XAUUSD",
    "timeframe": "M15",
    "candle_count": 200,

    "swing_lookback": 3,
    "sr_zone_cluster_pct": 0.5,
    "sr_min_touches": 2,

    "momentum_candle_multiplier": 2.0,
    "momentum_candle_lookback": 3,
    "volume_avg_period": 20,
    "volume_threshold_pullback": 1.2,
    "volume_threshold_failure": 1.5,

    "risk_pct": 0.01,
    "min_rr_ratio": 2.0,
    "daily_loss_limit": 3,
    "sl_buffer_atr_multiplier": 0.5,

    "mode": "ALERT_ONLY",
    "max_open_trades": 1
}
```

## Phase Rollout

| Phase | Mode | Duration | Exit Criteria |
|-------|------|----------|---------------|
| **1** | Alert Only | 4-6 weeks | ≥100 logged signals, strategy logic validated |
| **2** | Semi-Auto (demo) | 4-6 weeks | ≥50 executed paper trades, no order management bugs |
| **3** | Full Auto (demo) | 4+ weeks | ≥100 closed trades, profit factor > 1.3, daily loss rule never violated |
| **4** | Live Money | When ready | All Phase 3 gates passed, small capital only |

## Success Criteria Before Going Live (Phase 4 Gates)

**Non-negotiable. If ANY gate fails → stay in demo. No exceptions.**

| # | Gate | Threshold | Notes |
|---|------|-----------|-------|
| 1 | Closed demo trades | ≥ 100 | Must be real strategy signals, not manual overrides |
| 2 | Profit factor | ≥ 1.3 | After spread + slippage assumptions |
| 3 | Win rate × R:R | Win ≥ 40% AND avg realized R:R ≥ 1.8 | If realized R:R collapses vs planned 2.0, investigate before proceeding |
| 4 | Daily loss rule | Never violated | Bot must have self-stopped every time 3 losses hit in a day |
| 5 | Order management | Zero critical bugs for ≥ 30 consecutive trading days | No orphaned SL/TP, no duplicate orders, no phantom positions |
| 6 | Max drawdown | < 10% of demo account | Peak-to-trough, measured daily |
| 7 | Uptime stability | ≥ 30 consecutive trading days without manual intervention | No crashes, no missed cycles, no stale data |

## Dependencies

```
MetaTrader5         # MT5 Python API (data, orders, positions)
pandas              # Candle data manipulation
numpy               # Calculations
requests            # Telegram API
```

### VPS Setup (Ubuntu + MT5)

```
Wine                # Windows compatibility layer for MT5
Xvfb                # Virtual framebuffer (headless display for MT5)
MetaTrader 5        # Installed via Wine, runs headless
```

## XAU/USD Risk Notes

- Gold moves fast — $20+ swings in minutes during news events
- Stay on demo **much longer** than you think is necessary
- The no-trade filters (spread, session, news) are critical safety nets
- V1 cron-based execution is fine; V2 may move to an always-on event loop for tighter execution

## V1 Build Order

Build in this exact sequence. Each step must be stable before the next begins.

| Step | Module | What | Duration |
|------|--------|------|----------|
| **0** | VPS Setup | Install Wine + Xvfb + MT5 on Ubuntu VPS | 1 day |
| **1** | `engine/mt5_connector.py` | MT5 login, candle pull, account info, positions, auto-reconnect | 1-2 days |
| **2** | Stability Soak | Run connector only, log uptime + symbol specs + spreads | **3-7 days** |
| **3** | `engine/data_feed.py` | Clean candle data, volume, symbol info into DataFrames | 1 day |
| **4** | `engine/indicators.py` + `market_structure.py` + `price_action.py` | VWAP, S/R zones, swing points, BOS, candle patterns | 2-3 days |
| **5** | `engine/strategy.py` | Pullback signal detection (alert-only) | 1-2 days |
| **6** | `engine/risk_manager.py` | Position sizing, daily limits, no-trade filters | 1 day |
| **7** | `alerts/telegram.py` | Signal alerts + connection alerts + daily summary | 1 day |
| **8** | `run_hermes.py` + cron | Main loop, idempotency, journal logging | 1 day |
| **9** | Phase 1 soak | Alert-only mode, collect 100+ signals | **4-6 weeks** |
| **10** | `engine/trader.py` | MT5 order execution (only after Phase 1 validated) | 1-2 days |

**Key principle:** Do NOT build strategy logic on top of an unstable terminal bridge. Steps 0-2 must pass before anything else.

## V2 Roadmap (After Pullback Strategy Proven)

1. Add Failure Test strategy
2. Event-driven loop (replace cron)
3. Trailing stop-loss options (breakeven at 1R, trail at 1.5R)
4. Multi-instrument scanning (EUR/USD, GBP/USD)
5. Performance dashboard (web or Telegram inline)

## Next Steps

1. Download MT5 from OANDA Global
2. Create a **demo account** in MT5 (File → Open an Account → choose demo)
3. Provide MT5 credentials: **login ID**, **password**, **server name** (e.g. `OandaGlobal-Demo`)
4. Build and deploy Hermes on VPS (134.209.103.20)
5. Start Phase 1: Alert-only mode on XAU/USD
