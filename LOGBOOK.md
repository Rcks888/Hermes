# 🔱 Hermes Logbook

## 2026-09-10 — Project Init & Gate 0 Start

### What was done
- Created Hermes project: proposal, brief, architecture
- Chose MT5 + Python (Wine + rpyc bridge) after confirming OANDA Global has no REST API
- Set up VPS (134.209.103.20, Ubuntu 24.04):
  - Installed Wine 9.0, Xvfb, Python 3.12 (Wine + native)
  - Installed MT5 via Wine, logged into MetaQuotes-Demo ($100k demo)
  - Built rpyc bridge: Wine Python (MT5 + rpyc server) ↔ Linux Python (Hermes engine)
  - Added 2GB swap file (VPS only has 1GB RAM, shared with Ares)
- Created Telegram bot for Hermes alerts (separate from Ares)
- Started Gate 0 stability soak (cron every 15 min)

### First soak cycle results
- MT5 connected successfully via rpyc bridge
- Candle pull: 10 bars fetched (XAUUSD M15)
- Symbol specs: XAUUSD, point=0.01, digits=2, tick_value=0.1, lot_min=0.01
- Spread: 23 points (0.23 USD)
- Server time offset: ~3 hours (MetaQuotes-Demo vs VPS)
- Account: $100,000 USD balance confirmed

### Decisions made
- V1: Pullback strategy only (Failure Test deferred to V2)
- XAU/USD only (no multi-pair scanning)
- Wine + rpyc on existing Ubuntu VPS (fallback: Windows VPS if unstable)
- Alert-only mode for Phase 1 (4-6 weeks minimum)
- Separate Telegram bot from Ares

### Known issues
- Wine toolbar spam (harmless, cosmetic)
- ALSA audio warnings (no sound card on VPS, harmless)
- MT5 initialize takes ~30-60s on first connection each cycle
- numpy <2 required in Wine Python (Wine 9.0 incompatible with numpy 2.x)

### Next steps
- Monitor soak for 3-7 days
- Gate 0 pass criteria: ≥98% uptime, stable candle pulls, spread data collected
- After Gate 0: build strategy engine (market structure, indicators, pullback detection)

---

## 2026-09-10 — Reviewer Feedback & Fixes

### Reviewer findings addressed
1. **rpyc bridge fragility** — Now tracking: rpyc server restarts, init latency per cycle, consecutive failure streaks. Soak metrics expanded.
2. **requirements.txt** — Split into Linux (rpyc, pandas, numpy, requests) vs Wine (MetaTrader5, rpyc, numpy<2) sections.
3. **settings.json** — Confirmed gitignored, stays only on VPS.
4. **Time offset ~3 hours** — Classified as broker server timezone (MetaQuotes-Demo uses EET). Strategy will use MT5 bar timestamps, not VPS local time.
5. **Shutdown every cycle removed** — rpyc server + MT5 now stay alive between cron cycles. Reduces init latency from ~30-60s to near-zero on warm cycles.
6. **Escalation thresholds** — Telegram now distinguishes: 1st failure (warning), 2-3 failures (still down), 4+ failures (CRITICAL HALT with recovery command).

### New soak metrics tracked
- `init_latency_s`: time to connect/reconnect per cycle
- `rpyc_restarted`: whether rpyc server had to be restarted
- `consecutive_failures`: failure streak count

### Gate 0 daily review checklist
- Uptime % (target: ≥98%)
- Candle-pull success rate
- Account-info success rate
- Median + p95 spread by session (Asia/London/NY)
- Init/reconnect latency (median + max)
- Consecutive failure streaks
- Symbol + contract specs consistency

---

## 2026-09-10 — Resource Monitoring & VPS Safety

### Problem
VPS has only 1GB RAM, shared with Ares. Reviewer flagged risk of OOM kills, Ares slowdowns, and cron overlaps.

### VPS baseline (with Hermes running)
| Component | RAM | Notes |
|-----------|-----|-------|
| MT5 terminal (Wine) | ~185MB | Stays running between cycles |
| Wine Python + rpyc | ~49MB | Stays running between cycles |
| Available RAM | 462MB | After Hermes loaded |
| Swap used | 305MB | Some pressure already |
| Load avg (1m) | 0.88 | Fine for 1 CPU |
| Total Hermes footprint | ~235MB | |

### Changes made
1. **Staggered cron** — Hermes runs at :07,:22,:37,:52 (Ares runs at :00,:30). No overlap.
2. **Resource logging** — Every cycle now records: free RAM, swap used, load average.
3. **Low RAM alert** — Telegram warning if free RAM drops below 150MB.
4. **Log rotation** — hermes.log rotates at 5MB, soak.json archives at 2000 cycles (keeps last 500).
5. **Health check script** — `./check_health.sh` for manual VPS status.
6. **2GB swap file added** — Prevents OOM kills if RAM fills up.

### Escalation plan if Hermes causes instability
1. Reduce soak frequency to every 30 min
2. Move Hermes to separate $6/month VPS
3. Switch to Windows VPS for MT5 (eliminates Wine overhead)

### Ongoing watchpoints (from reviewer)
- Monitor free RAM during Ares scan windows (9:30 PM, 11:30 PM, 1:30 AM, 5:00 AM MYT)
- If free RAM often <200MB or swap keeps climbing → switch soak to every 30 min immediately
- Persistent MT5 trades speed for idle RAM — acceptable, just monitor

---

## 2026-09-11 — VPS Upgraded to 2GB RAM ($12/month)

### Reason
1GB RAM was tight — swap usage at 305MB, free RAM at 462MB with Hermes running. Ares was also affected (see Ares LOGBOOK for details).

### Before vs After
| Metric | 1GB VPS | 2GB VPS |
|--------|---------|---------|
| RAM free | 462MB | 791MB |
| Swap used | 305MB | 18MB |
| Risk to Ares | Medium | None |

### Soak status at upgrade
- 74 cycles completed, 98.6% uptime (73/74)
- 1 failure (likely during VPS resize/reboot)
- Init latency: ~2s (warm cycles)
- RAM concern eliminated

---

## 2026-09-14 — Gate 0 PASSED ✅

### Soak results (187 cycles, ~4 days)
| Metric | Result | Target |
|--------|--------|--------|
| Uptime | 99.47% (186/187) | ≥98% ✅ |
| Init latency P50 | 2.03s | — |
| Init latency P95 | 2.07s | — |
| Init latency max | 24.48s (cold start) | — |
| rpyc restarts | 1 (VPS resize) | — |
| Ares impact | None after 2GB upgrade | — |

### Spread analysis
| Session | P50 | P95 | Best | Worst | Samples |
|---------|-----|-----|------|-------|---------|
| London | 22 | 35 | 16 | 37 | 38 |
| NY | 28 | 40 | 13 | 62 | 71 |
| Asia | 41 | 51 | 19 | 53 | 61 |
| OFF | 36 | 66 | 35 | 66 | 16 |

### Key takeaways for strategy
- London has tightest spreads — prefer for entries
- NY also good, wider range
- Asia widest — avoid or use wider SL
- OFF hours (weekends/gaps) — do not trade

### RAM after 2GB upgrade
- Free RAM avg: 789MB, min: 218MB (during Ares scans)
- Swap avg: 366MB (includes pre-upgrade data)
- Load avg: 0.71 — healthy

### Decision
Gate 0 passed. Proceeding to Gate 1: Pullback strategy engine build.

---

## 2026-09-14 — Gate 1: Strategy Engine Built & Deployed

### New modules
| Module | Purpose |
|--------|---------|
| `engine/market_structure.py` | Swing points, BOS/CHoCH detection, trend classification |
| `engine/indicators.py` | VWAP (daily reset), volume avg, ATR(14), S/R zone clustering |
| `engine/price_action.py` | Momentum, engulfing, reaction candle detection |
| `engine/strategy.py` | Pullback signal evaluation — all 6 conditions checked |
| `engine/risk_manager.py` | Position sizing (1% risk), daily loss limit, no-trade filters |
| `run_hermes.py` | Main loop: data → structure → indicators → strategy → risk → alert |

### Pullback signal conditions (all must pass)
1. Trend confirmed (≥2 BOS in same direction)
2. Price in pullback (retracing against trend)
3. Candlestick confirmation (momentum/engulfing/reaction)
4. VWAP alignment (long above, short below)
5. Volume on signal candle > 1.2x average
6. ATR gate (min 3.0 USD, SL within 0.5-2.5x ATR)

### No-trade filters
- Spread > 50 points → reject
- Session: only LONDON and NY
- Daily loss limit: 3
- Max open trades: 1

### Calibrated from soak data
- Max spread: 50 points (P95 across all sessions was 40-66)
- Trading sessions: London (tightest spreads P50=22) and NY (P50=28)
- Asia excluded (P50=41, too wide)

### Cron schedule
- Soak: :07, :22, :37, :52
- Strategy: :08, :23, :38, :53 (1 min after soak)
- Both Mon-Fri only (XAU/USD market hours)

### First test run
- Trend detected: DOWN confirmed
- No signal: ASIA session (correctly filtered)
- Mode: ALERT_ONLY (no auto-execution)

### Next steps
- Monitor signals during London/NY sessions this week
- Review signal quality in logs/trades.json
- Target: 100+ logged signals before considering Phase 2 (semi-auto)

---

## 2026-09-15 — First Day Strategy Monitoring

### Observations
- Strategy engine running correctly every 15 min via cron
- Trend detection working: saw DOWN confirmed=True during London/NY on Sep 14
- Trend flipped to UP via CHoCH at idx=168, but not yet confirmed (needs ≥2 BOS UP)
- No signals triggered — correct behavior, market hasn't given a valid pullback setup yet

### Market snapshot (10:19 AM MYT, Asia session)
| Metric | Value |
|--------|-------|
| Price | 4310.18 |
| VWAP | 4295.85 |
| ATR(14) | 6.53 |
| Trend | UP (unconfirmed, 0 BOS UP) |
| Swing highs | 16 |
| Swing lows | 21 |
| S/R zones | 5 |
| Volume | Very low (Asia/MetaQuotes-Demo) |

### Key S/R zones
- 4300.64 - 4321.87 (7 touches)
- 4323.22 - 4343.99 (10 touches)
- 4347.44 - 4361.01 (8 touches)

### Fixes applied today
- Disabled routine soak Telegram summary (was firing every 24 cycles)
- Fixed cold start false "reconnected" alerts — only alerts on real disconnections now
- Telegram is now silent unless: signal found, MT5 failure, or low RAM

### Status
- Waiting for market to confirm trend with ≥2 BOS before pullback signals can trigger
- All conditions checked correctly: trend → pullback → candle → VWAP → volume → ATR
- Strategy is selective by design — patience required

---

## 2026-09-15 — Security Audit & VPS Hardening (post-Ares handoff)

Triggered by an Ares incident: its Telegram bot token was committed in plaintext to
a public repo and the bot was hijacked within days. Audited Hermes for the same
exposure and four other failure modes carried over from the Ares thread.

### Audit results — Hermes clean on all five
| Check | Result |
|-------|--------|
| Secrets in tracked files | ✅ None — only key names (`cfg["bot_token"]`), never values |
| Secrets in git history | ✅ None — `config/settings.json` never committed |
| Telegram bot | ✅ Separate bot from Ares, never leaked, no rotation needed |
| tzdata dependency | ✅ None — stdlib `timezone.utc` + `utc=True` only, no `ZoneInfo`/`pytz` |
| Cache staleness | ✅ No cache exists — every cycle pulls fresh candles from MT5 |

Session tags are computed by integer hour arithmetic, not timezone conversion, so
Hermes is structurally immune to the `ZoneInfoNotFoundError` that silently broke
all Ares IBKR data.

### Changes applied
- `.gitignore` hardened: blocks `.env*`, `*.pem`, `*.key`, `*.p12`, `config.ini`,
  `*token*`, `*secret*`, `*credential*`, `*password*`. Verified via `git check-ignore`.
- `run_soak.py`: added `cron.log` rotation at 10 MB — previously unrotated, and at
  ~100 KB/day (96 soak runs) it is the largest log source on the box.
- `run_soak.py`: archive pruning, keeps last 3.
- `run_soak.py`: low-RAM threshold raised 150 → 300 MB to align with the Ares dashboard.
- `run_soak.py`: new swap alert at >200 MB — Hermes filled the swap, so Hermes reports it.

### Measured RAM footprint — higher than assumed
| Process | RSS |
|---------|-----|
| `main` (wine64 = MT5 terminal) | 245 MB |
| `python.exe` (Wine MT5 bridge) | 82 MB |
| `winedevice.exe` ×2 | ~32 MB |
| `wineserver` | 17 MB |
| **Total** | **~376 MB** |

The proposal assumed 200–300 MB. Actual is ~50% higher, and ~130 MB had been paged
to swap. Hermes is the single largest consumer on the shared 2 GB box
(vs IB Gateway ~440 MB, Ares Python ~100 MB). ~790 MB free after both systems.

**Action item:** revisit the proposal's resource section before Phase 3. Full-auto
holds the MT5 connection continuously rather than per-cycle, so the footprint will
not drop and may rise.

### Shared-VPS context from Ares thread
- `multipathd`, `ModemManager` disabled; `fwupd` masked (~61 MB reclaimed).
  Not yet reboot-tested — nothing in the Wine stack should depend on them, but Xvfb
  is worth verifying after the next boot.
- journald capped to 50 MB. Prefer Hermes' own log files over `journalctl` for history.
- Swap reclaimed 313 MB → 0. Watching whether it re-accumulates.
- Ares monitor moved :05 → :10 to avoid Hermes' :07/:08. Hermes cron unchanged and
  clear of Ares' :00/:10/:25/:30 windows.

### Still pending
- News filter (Forex Factory blackout ±30 min on high-impact USD/gold events) — last
  spec'd V1 item outstanding. Lower risk in alert-only, matters before Phase 2.
- Reboot test of the Wine stack (do on a weekend, market closed).

### Decision
Run unchanged through Friday. If no signals by then, the question to investigate is
**not** threshold tuning — it is whether `trend_confirmed` requiring ≥2 BOS inside a
50-bar window is too strict for M15 gold. Review against actual bar data, not guesswork.
