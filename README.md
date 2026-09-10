# 🔱 Hermes — Forex Day Trading Bot

Part of **Project Olympus**. Automated XAU/USD day trading via MT5 + Python on OANDA Global.

## Current Phase: Gate 0 — MT5 Stability Soak

Before any strategy code runs, we must prove MT5 connectivity is stable on Ubuntu + Wine.

## Quick Start (VPS)

```bash
# 1. Clone/copy to VPS
scp -r ~/Olympus/Hermes user@134.209.103.20:~/Olympus/Hermes

# 2. Run setup
ssh user@134.209.103.20
cd ~/Olympus/Hermes
chmod +x setup_vps.sh
./setup_vps.sh

# 3. Follow manual steps printed by setup script
# 4. Fill in config/settings.json with MT5 credentials
# 5. Test: python run_soak.py
# 6. Add cron for 15-min soak cycles
```

## Project Structure

```
config/          — Settings, strategy params, news calendar
engine/          — MT5 connector, data feed, (strategy modules later)
alerts/          — Telegram notifications
logs/            — Soak data, trade journal, runtime logs
run_soak.py      — Gate 0: stability soak script
setup_vps.sh     — One-time VPS setup (Wine + Xvfb + MT5 + Python)
```

## Gate 0 Pass Criteria

- MT5 uptime ≥ 98% over 7 days
- Candle pulls and account info consistently succeed
- Symbol specs and spread distribution confirmed
- Server time vs VPS time drift measured

## Docs

- [HERMES_BRIEF.md](HERMES_BRIEF.md) — Project brief and strategy overview
- [HERMES_PROPOSAL.md](HERMES_PROPOSAL.md) — Full technical proposal
