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
