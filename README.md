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

## MT5 GUI on the VPS (VNC through an SSH tunnel)

The firewall allows inbound TCP 22 only, so VNC never listens on a public port. Start just after a :08/:23/:38/:53 cycle.

1. VPS, SSH session 1: `x11vnc -display :99 -localhost -once -nopw -quiet` (waits at `PORT=5900`; the DPMS warning is harmless; exits when the viewer disconnects).
2. Laptop PowerShell, keep open: `ssh -L 5900:localhost:5900 root@134.209.103.20`
3. RealVNC Viewer: `localhost:5900` (the unencrypted warning is fine inside the tunnel).

Do not restart the terminal from the GUI unless intended: a pending MT5 build installs on restart.

## Project Structure

```
config/          — Settings, strategy params, news calendar
engine/          — MT5 connector, data feed, strategy, risk, entry_observation (0g-1), pipeline (C2.1, shared with the replay)
replay/          — 0g-6 replay engine under ec-0.3, in progress: data (snapshot hashes, holdout seal, C6.3), clock, spread, entry, exits (C5.1-C5.10, S/T/C paths), portfolio (CONFORMANCE/SIMULATION, risk gate, C2.5 ordering, C5.9 scenario agreement), gates (pr-0.4 P1 gate evaluator G1-G9, month-cluster bootstrap U1-U3, F5 segments), folds (F1-F4, each fold flat), holdout (H1 unseal log with a committed go-ahead and TTY confirmation, engine identity read from git, inputs bound to the verdict's snapshot/spec/spread/config identity with every hash computed from content (broker spec verified against its recorded sha256), H2 run-family permits enforced on the run; nothing unsealed), holdout_run (the single holdout entry point: builds every run's context and opportunities itself, B2 matched on its own primary run; the unseal log and the run log are each created by one exclusive atomic create, so a second or concurrent unseal or family start is refused, each run made once and logged in full (lossless records, B2 statistics), and `read_family` rebuilds the P1 inputs from that log alone; `python -m replay.holdout_run unseal|run`, never run before P1), benchmark (B2 random portfolios), attempts (M1-M3 attempt ledger, V1-V8 menu, G2 level), conformance (§8 Level E E0-E4, E2 v2 signal and forward-rate conditions as proposed, and the NOT CONFORMANT rule); constructed inputs only until pr-1.0
alerts/          — Telegram notifications (`alert_soak_status` is unwired on purpose, ROADMAP known issue 5)
logs/            — Soak data, events.jsonl, runtime logs
contract_amendments/ — Contract amendment proposals for review (ec-0.3 approved; ec-1.0 TBD-3/TBD-4 proposal v1 and v2, 19 Nov; v2 answers the Reviewer's HOLD on v1)
research/        — Read-only probes: signal outcome, entry timing (0g-2..0g-4); history export (Data step); build_tbd34 (TBD-3 spread model and TBD-4 E2 tolerance artefacts, approved rules S1-S6 v2, T1-T2)
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
python tests/test_mt5_login.py                # credentials passed to mt5.initialize(), account check, own abort reason and alert text for -6 (29 Oct); C1 v4 (Reviewer approved 19 Nov): the terminal journal's latest authorization entry rejecting the configured login, with its UTC time (engine/terminal_log.py), and its alert (--controls)
python tests/test_pipeline_parity.py          # C2.1: engine/pipeline.py reproduces the old inline run_hermes sequence (synthetic + post-holdout windows; --full every window; --controls)
python tests/test_replay_contract.py          # ec-0.3 §9 contract tests 1-28 plus the holdout seal; 14, 23 (E0 eligibility) and 28 read tests/fixtures/live_signal_events.jsonl (the 6 live SIGNAL_APPROVED events, exported from the VPS 12 Nov)
python tests/test_replay_contract.py --controls  # each test under its broken variants; every variant must fail
python tests/test_prereg.py                   # pr-0.4 §9 protocol tests 1-7: seal/unseal, fold boundary, bootstrap golden vector (tests/fixtures/prereg_u3_golden.json), gate evaluator, random benchmark, attempt ledger, holdout run family; 7b engine identity (needs git); 7c holdout entry point (full-fold scan, B2, logged family)
python tests/test_prereg.py --controls        # same, under broken variants
python tests/test_conformance.py              # ec-0.3 §8 E1-E4 (E2 as proposed in v2), Levels 1-3 event parity and the verdict rule, on the signal fixture and constructed inputs
python tests/test_conformance.py --controls   # same, under broken variants
python tests/test_tbd34.py                    # research/build_tbd34.py: TBD-3 S1-S6 v2 selection (first occurrence, whole-sequence staleness, exact points, session of E, ts window, S5 fallback), the Q-S5 A soak dataset (sha256, cutoff, ruled n) and TBD-4 T1-T2 (type-1 p99, missing M1, holdout guard), constructed inputs (--controls)
python research/build_tbd34.py --export=latest --soak=research/data_feasibility/soak_0a_20261009.json  # VPS, after 22 Nov 2026 and a new full export; the soak must be the 0a copy (sha256 84d11d3f..., cycles to 30 Sep 07:06:58, n 1,341; Reviewer Q-S5 A), else refused: writes spread_model_<ts>.json and e2_tolerance_<ts>.json to research/data_feasibility/; prints n, exclusions, alignment and T only
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
