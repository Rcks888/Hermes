# 🔱 Hermes — Forex Day Trading Bot

Part of **Project Olympus**. Automated XAU/USD day trading via MT5 + Python on OANDA Global.

## Current Phase: Priority 0g — Entry Timing and Execution Contract

Alert-only, no capital at risk. Gate 0 soak passed; the Pullback Setup strategy
runs every 15 minutes on closed M15 bars. Two signals to date (2 Oct LONG,
7 Oct SHORT). 0g fixes how the replay engine prices entry: delayed decision
time, executable quote, and spread charged on the correct leg per direction
(`engine/entry_observation.spread_accounting`). See [ROADMAP.md](ROADMAP.md),
section "Resume here", for detail; [STATUS.md](STATUS.md) is the entry point.

## Quick Start (VPS)

```bash
# 1. Clone/copy to VPS
scp -r ~/Olympus/Hermes user@<VPS_HOST>:~/Olympus/Hermes

# 2. Run setup
ssh user@<VPS_HOST>
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
engine/          — MT5 connector, data feed, strategy, risk, entry_observation (0g-1)
alerts/          — Telegram notifications
logs/            — Soak data, events.jsonl, runtime logs
research/        — Read-only probes: signal outcome, entry timing (0g-2..0g-4)
tests/           — Unit tests
run_hermes.py    — Strategy cycle (alert-only)
run_soak.py      — Gate 0: stability soak script
setup_vps.sh     — One-time VPS setup (Wine + Xvfb + MT5 + Python)
```

## Gate 0 Pass Criteria

- MT5 uptime ≥ 98% over 7 days
- Candle pulls and account info consistently succeed
- Symbol specs and spread distribution confirmed
- Server time vs VPS time drift measured

## Tests and controls

```bash
python tests/test_entry_observation.py        # 27 unit tests, no MT5 needed
python research/probe_entry_timing.py --days=10 --anchor=<epoch>   # 14 controls gate the run
python research/probe_signal_outcome.py       # self-test gates resolution of logged signals
```

Weekly: `python weekly_checkpoint.py --week=<Monday>` feeds the checkpoint log in [STATUS.md](STATUS.md). It keeps stale-tick cycles (market not quoting) out of the delay and offset statistics; `--self-test` runs its controls.

## Docs

- [STATUS.md](STATUS.md) — entry point: current state, weekly timeline, checkpoints
- [ROADMAP.md](ROADMAP.md) — priorities, evidence and decisions
- [EXECUTION_CONTRACT.md](EXECUTION_CONTRACT.md) — 0g-6 replay execution contract (`ec-0.2`, structure frozen; `ec-1.0` pending its TBDs)
- [HERMES_BRIEF.md](HERMES_BRIEF.md) — Project brief and strategy overview
- [HERMES_PROPOSAL.md](HERMES_PROPOSAL.md) — Full technical proposal
