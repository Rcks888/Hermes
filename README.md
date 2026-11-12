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
engine/          — MT5 connector, data feed, strategy, risk, entry_observation (0g-1), pipeline (C2.1, shared with the replay)
replay/          — 0g-6 replay engine under ec-0.3, in progress: data (snapshot hashes, holdout seal, C6.3), clock, spread, entry, exits (C5.1-C5.10, S/T/C paths)
alerts/          — Telegram notifications
logs/            — Soak data, events.jsonl, runtime logs
research/        — Read-only probes: signal outcome, entry timing (0g-2..0g-4); history export (Data step)
data/history/    — Exported M15/M1 CSV snapshots, not committed; manifests with hashes are in research/data_feasibility/
tests/           — Unit tests
run_hermes.py    — Strategy cycle (alert-only)
run_soak.py      — Gate 0: stability soak script
setup_vps.sh     — One-time VPS setup (Wine + Xvfb + MT5 + Python)
requirements.lock — Exact venv versions; install with `pip install -r requirements.lock`
requirements.txt — Lower bounds only; never `pip install -U -r` it on the VPS
```

## Gate 0 Pass Criteria

- MT5 uptime ≥ 98% over 7 days
- Candle pulls and account info consistently succeed
- Symbol specs and spread distribution confirmed
- Server time vs VPS time drift measured

## Tests and controls

```bash
python tests/test_entry_observation.py        # 27 unit tests, no MT5 needed
python tests/test_mt5_failure_state.py        # cross-process outage count and reconnect, no MT5 needed
python tests/test_alert_throttle.py           # Telegram throttle state across cron processes, incl. concurrent runs
python tests/test_mt5_run_lock.py             # one MT5 run at a time, retry budget, stuck-terminal kill (22 Oct)
python tests/test_mt5_login.py                # credentials passed to mt5.initialize(), account check, own abort reason and alert text for -6 (29 Oct)
python tests/test_pipeline_parity.py          # C2.1: engine/pipeline.py reproduces the old inline run_hermes sequence (synthetic + post-holdout windows; --full every window; --controls)
python tests/test_replay_contract.py          # ec-0.3 §9 contract tests built so far (1-10, 13-18, 20, 22, 26(v)-(vi), 27, 28, holdout seal); PENDING until tests/fixtures/live_signal_events.jsonl exists
python tests/test_replay_contract.py --controls  # each test under its broken variants; every variant must fail
python research/probe_entry_timing.py --days=10 --anchor=<epoch>   # 14 controls gate the run
python research/probe_signal_outcome.py       # self-test gates resolution of logged signals; a signal outside the bar window is NOT RESOLVED
python research/prereg_gate_power.py          # local, no MT5: reproduces the 0b drawdown/power table (PREREGISTRATION.md section 3)
python research/export_history.py --self-test # local, no MT5: 20 controls for the export checks (C6.3, coverage, F3)
python research/export_history.py --probe     # VPS: TBD-2 M1 depth probe via the Wine-side worker, holds the MT5 lock
python research/export_history.py             # VPS: probe, M15+M1 CSV export to data/history/<ts>/, SHA-256, count-only checks (holdout sealed, F6)
python research/export_history.py --maxbars N --probe  # VPS: restart the terminal with a /config file holding only [Charts] MaxBars=N (common.ini never opened), then probe
python research/export_history.py --qc DIR    # rerun hashing and checks on an existing export, no MT5
python research/trading_calendar.py           # declared calendar-2 + offset-eu-1 (clock only, no data); self-test; the replay imports it
python research/calendar_check.py DIR         # local, no MT5: verifies calendar-2 against the export (observed-outside runs, holes, absent M15 slots), reopen hour vs offset-eu-1, timestamps only; checks hashes against the manifest; calendar-1 kept as the broken variant
python research/broker_spec_snapshot.py       # VPS: versioned XAUUSD/account spec, swap fields and a 1-lot margin probe (C4.2, C4.4, C3.7), holds the MT5 lock
```

Weekly: `python weekly_checkpoint.py --week=<Monday>` feeds the checkpoint log in [STATUS.md](STATUS.md). It keeps stale-tick cycles (market not quoting) out of the delay and offset statistics; `--self-test` runs its controls.

`CYCLE_ABORTED` reasons for MT5: `mt5_busy` (lock held), `mt5_auth_failed` (-6), `mt5_ipc_timeout` (-10003), `mt5_wrong_account` (terminal on another account), `mt5_not_connected` (anything else); the event also carries `last_error`. Before 5 Nov every MT5 failure was logged as `mt5_not_connected`.

## Docs

- [STATUS.md](STATUS.md) — entry point: current state, weekly timeline, checkpoints
- [ROADMAP.md](ROADMAP.md) — priorities, evidence and decisions
- [EXECUTION_CONTRACT.md](EXECUTION_CONTRACT.md) — 0g-6 replay execution contract (`ec-0.3`, structure frozen 12 Nov; `ec-1.0` pending TBD-3, TBD-4, the §9 tests and conformance)
- [contract_amendments/ec-0.3_PROPOSAL_v4_APPROVED.txt](contract_amendments/ec-0.3_PROPOSAL_v4_APPROVED.txt) — amendment `ec-0.3` as approved in review 4 (6 Nov); applied to EXECUTION_CONTRACT.md on 12 Nov with the A4 values from broker-spec snapshot `bs-39eef15e09e9`
- [PREREGISTRATION.md](PREREGISTRATION.md) — 0b P1 success criteria, folds, sealed holdout and attempt budget (`pr-0.4` protocol frozen 23 Oct; `pr-1.0` when its tests pass; no historical SIMULATION result before `pr-1.0`)
- [HERMES_BRIEF.md](HERMES_BRIEF.md) — Project brief and strategy overview
- [HERMES_PROPOSAL.md](HERMES_PROPOSAL.md) — Full technical proposal
