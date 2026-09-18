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

### ⚠️ Correction (logged 2026-09-18)
The Day 1 entry above recorded that reject reasons were being logged per signal.
**That was wrong.** `strategy.evaluate()` had 11 bare `return None` paths, none of
which logged anything, and `log_trade()` only ran after a signal survived all 11
gates. Since no signal ever survived, **nothing was logged at all** for the first
three days. This is why the "why no signals" question was unanswerable. Fixed below.

---

## 2026-09-18 — Signal Diagnostics, Fail-Closed Risk Gates, Memory Monitoring

### Root cause of the blind spot
No diagnostic existed for why a bar produced no signal. `evaluate()` returned bare
`None` from 11 different gates, so all failures looked identical from outside.

### Fix: `evaluate()` now returns `(signal, reason)` on every path
One consistent return shape, 16 return sites, every one a 2-tuple. Reason strings
are specific and include the numbers that caused the block, e.g.
`volume_too_low (1 < 1.2x avg 2742 = 3290)`.

### "Cannot evaluate" is now distinct from "rule violation"
Same bug family as the Ares queue incident, but Hermes was failing **open** rather
than closed — a transient data failure silently bypassed the gate meant to validate it.

| Location | Old behaviour | New behaviour |
|----------|---------------|---------------|
| VWAP gate | `if vwap is not None:` → missing VWAP skipped the check, signal passed | Rejects with `cannot_evaluate: vwap unavailable` |
| Volume gate | `if vol_avg and vol_avg > 0` → missing average skipped the check | Rejects with `cannot_evaluate: volume average unavailable` |
| Spread filter | `if max_spread and spread_points and ...` → `None` spread skipped the filter entirely | **Fails closed** — rejects with `cannot_evaluate: spread unknown` |
| Open positions | `get_positions()` returned `[]` on failure → `0 >= 1` False → **approved** | Returns `None`; risk_manager **fails closed** against double entry |
| Daily loss count | Unparseable `SL_HIT` timestamps silently dropped | Counted and warned — limit could have been understated |

The open-positions bug is the Ares bug verbatim. Harmless in alert-only; it would
have permitted double entry on the first day of Phase 3.

### Append-only event log — `logs/events.jsonl`
`trades.json` is rewritten wholesale each cycle (`open(..., "w")`), so a crash
mid-write truncates all history. The new event trail is append-only, one JSON object
per line, and covers cycles that abort before ever reaching a signal
(`mt5_not_connected`, `insufficient_candles`, `account_info_unavailable`).

Friday review command:
```bash
python3 -c "
import json,collections
c=collections.Counter()
for l in open('logs/events.jsonl'):
    e=json.loads(l)
    c[e.get('blocked_at','—').split(' (')[0]]+=1
for k,v in c.most_common(): print(f'{v:5d}  {k}')
"
```

### Inert config key fixed
`momentum_candle_lookback` had **zero** code references while the body-average window
was hardcoded to `range(len(df)-4, len(df)-1)`. Value happened to match, so harmless,
but silently ignored if changed. Now wired. Confirmed no other unread keys remain.

### First live diagnostic output
```
No signal | session=LONDON | blocked_at=trend_not_confirmed (trend=UP, bos_up=0, bos_down=1)
```
`trend=UP` but `bos_up=0, bos_down=1` — these disagree. `trend` is set by the latest
CHoCH; `trend_confirmed` requires ≥2 BOS in the trend direction. A CHoCH *is* the
flip, so immediately after one fires there are ~0 BOS in the new direction. In chop
the next CHoCH resets the count before two BOS accumulate. Consistent with observed
behaviour: long `confirmed=True` runs in trending sessions, near-permanent `False`
otherwise. Price also moved 4310 → 4398 (~2%) with `bos_up=0`, suggesting either the
50-bar window is too short to contain the move or `swing_lookback=3` is fragmenting
it. **Not acting on one sample** — deferred to Friday's histogram.

---

### Memory monitoring — two false-positive alerts corrected

**1. Cold-start reconnect (fixed earlier, confirmed live today)**
Each cron cycle is a fresh process, so `mt5` was always `None` at start →
`is_connected()` False → reconnect path → reported "reconnected after 0.0min" every
cycle. Now distinguishes cold start from real reconnection. Confirmed in today's run:
`MT5 connected (cold start)` with no Telegram.

**2. Swap occupancy misread as memory pressure**
Alert fired continuously at swap=284–308MB while **RAM free was 1486MB**. That is not
pressure. At the default `vm.swappiness=60` the kernel evicts idle anonymous pages
even with GBs free, and headless Wine/MT5 holds many such pages (GUI paths, chart
rendering) never faulted back in. Swap fills once and never self-releases.

Observed two-phase pattern after a manual `swapoff -a && swapon -a`:
- 0 → 284 MB rapidly
- 284 → 308 MB over 21 hours (~1 MB/hr)

**Swap occupancy cannot distinguish** a genuine RAM peak from equilibrium restoration
after the swap clear — both produce that shape. This invalidates the earlier
assumption (carried over from the Ares thread) that swap is a sufficient proxy for
peak RAM. Peak tracking had been deferred on the strength of that proxy.

### Correct detector: PSI, not occupancy
| Signal | Measures | Catches |
|--------|----------|---------|
| `MemAvailable` | State, sampled | Only pressure present *at* the sample |
| `swap_used` | Cumulative occupancy | That eviction happened *sometime*, cause unknown |
| `pswpout` delta | Rate | Active churn between cycles |
| **PSI `full` delta** | **Cumulative stall time** | **Any real event, incl. between samples** |

`/proc/pressure/memory` `full` measures time in which *every* runnable task was
blocked on memory reclaim. Totals are cumulative since boot, so a 15-min sampling
interval still detects spikes that begin and end between samples — which point-in-time
`MemAvailable` reads structurally cannot.

New alert conditions:
- swap >200 MB **AND** RAM free <400 MB → real pressure
- `pswpout` delta >25k pages/cycle (~100 MB) → active thrashing
- PSI `full` delta >1s/cycle → pressure event occurred
- Benign occupancy → INFO log only, no Telegram

Both false positives shared a root cause: **alerting on a state rather than a
transition or rate.**

### Do not clear swap again
`swapoff -a && swapon -a` force-faults every evicted page back into RAM at once — a
real spike with Ares' JVM also resident — and the kernel then re-evicts the same idle
pages over the following hours. That is exactly the 0 → 308 MB refill observed. Churn
for no benefit. If the occupancy is unwanted, reduce the cause instead:
`sysctl vm.swappiness=10`.

### Current Telegram surface (7 message types)
Signal detected · MT5 disconnected · MT5 still down (2–3) · CRITICAL HALT (4+) ·
MT5 reconnected · Low RAM (<300 MB) · Real memory pressure · Swap thrashing ·
Memory stall (PSI)

Silenced: periodic soak summary, cold-start reconnects, swap-occupancy-only warnings.
Expected steady state is **silence**.

---

### 🚩 Flagged, not fixed
1. **`alert_soak_status()` is orphaned** — its only caller was removed when the
   periodic summary was disabled. ~20 lines unreachable in `alerts/telegram.py`.
   Left deliberately: it is the hook to re-wire if a weekly digest is ever wanted.
   Same dead-code pattern as the 53 lines found in Ares.
2. **`price_action.scan()` is never called** — `strategy.py` imports the individual
   pattern functions directly. Left in place as it appears intended for the V2
   Failure Test strategy. Unused `price_action` import removed from `run_hermes.py`.
3. **Duplicate disconnect alerts** — `run_soak.py` and `run_hermes.py` run one minute
   apart and each call `ensure_connected()` in separate processes. A genuine outage
   produces **two** disconnect alerts per 15-min window. Fix would be a shared
   timestamped state file with a cooldown. Low priority while connectivity is stable,
   but it will be noticeable during a real broker outage.
4. **`ram_free < 300` is still a state check** — same shape as the two corrected false
   positives. It will re-fire every cycle during any sustained dip rather than once on
   crossing. Not urgent at 1486 MB free.
5. **News filter still outstanding** — last spec'd V1 item. Lower risk in alert-only,
   matters before Phase 2.
6. **Structure indices vs indicator dataframe** — `market_structure.analyze()` runs
   before the indicator columns are added, and `find_sr_zones()` uses swing indices
   from the pre-indicator frame against the post-indicator frame. Same length today so
   correct, but fragile: any future reindex or row-drop in the indicator path would
   silently misalign zones. Latent, matches the "order of operations was silent"
   pattern from the Ares handoff.

### Commits
- `5e4b608` — distinguish cannot-evaluate from rule violation, event log, config key
- `18f17b3` — alert on real memory pressure, not swap occupancy
- `7006d4e` — detect pressure events via PSI instead of inferring from swap

### Open question for the user
`cat /proc/pressure/memory` on the VPS answers retroactively whether a genuine RAM
peak ever occurred. `full total=0` plus a clean `dmesg` means phase 1 was housekeeping
after the swap clear. A substantial `full total` means a real event happened and the
next question is which process caused it.
