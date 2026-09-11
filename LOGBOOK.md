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
