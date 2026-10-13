# Hermes: current status

Last updated: 2026-10-13. This page is the entry point. Detailed reasoning and
evidence are in `ROADMAP.md`, early history in `LOGBOOK.md`, probe outputs in
`research/data_feasibility/`.

## State: ALERT-ONLY, research phase 0g

XAU/USD M15 Pullback Setup on MetaQuotes-Demo, alert-only, no orders and no
capital at risk. Strategy parameters are frozen: nothing is tuned on live
signals at this sample size. Research-policy results are not authorisation for
live execution.

## Plan and timeline

```
✔ Gate 0  MT5 stability soak            passed 14 Sep (99.47% uptime)
✔ Gate 1  strategy engine, alert-only   live 14 Sep
✔ P0      0a 0c 0d 0e 0f prerequisites  30 Sep
✔ Fix     closed-bar evaluation cf4e3d4 30 Sep -> first signal 2 Oct
▶ 0g      entry timing + execution contract
            0g-1..0g-5 done; 0g-6 ec-0.1r4, review 4 pending   <- HERE
◻ 0b      preregister success criteria, folds, sealed holdout
◻ Data    M15 history CSV export, Wine side (hard gate on replay)
◻ Replay  purpose-built M15 delayed-entry engine
◻ Conf    Level 2/3 conformance against logged events
◻ P1      rule-only baseline -> P1 decision gate
```

By week (Mon–Fri). Update the "Actual" column at each weekly checkpoint, and
say why if it differs from the plan. Future rows are a plan, not a commitment.

| Week | Planned | Actual | Change vs plan |
|---|---|---|---|
| Sep 7 – Sep 11 | project init, Gate 0 soak | init 10 Sep, MT5 via Wine + rpyc, VPS to 2 GB, soak running | — |
| Sep 14 – Sep 18 | Gate 0 decision, strategy build | Gate 0 passed 14 Sep, Gate 1 engine deployed, cannot-evaluate vs rule-violation split, PSI memory alerts | — |
| Sep 21 – Sep 25 | observe | observation only, zero signals | — |
| Sep 28 – Oct 2 | two-week review; vacation from Oct 1 | 30 Sep: forming-bar root cause fixed (`cf4e3d4`), 10x oversizing fixed (`495e078`), ROADMAP, P0 0c/0d/0e/0f done. 2 Oct: first signal, LONG, TP_HIT | — |
| Oct 5 – Oct 9 | vacation; resume 7 Oct | 7 Oct: 0g opened, 0g-1 to 0g-5 done, three own bugs caught by guards and controls, cron change deferred. Second signal 7 Oct, SHORT, SL_HIT | unplanned: 0g promoted ahead of the replay |
| Oct 12 – Oct 16 | catch-up funnel; 0g-6 draft -> review -> freeze | 13 Oct: SHORT-side spread defect fixed (`3fdef54`), both signals resolved, first live conformance point, STATUS and weekly checkpoint added, 0g-6 drafted as `EXECUTION_CONTRACT.md` ec-0.1, reviews 1-3 HOLD (10 + 8 + 3 findings), all accepted in ec-0.1r2-r4, bundle v4 at `Olympus/review_bundle_execution_contract_v4.tgz` | drafting found H1-H3, which add work to the weeks of Oct 19 and Oct 26 |
| Oct 19 – Oct 23 | 0b preregistration; M15 **and M1** CSV export and validation, including the M1 `MaxBars` test (contract TBD-2) | | M1 added by the contract |
| Oct 26 – Oct 30 | replay engine build, controls first | | |
| Nov 2 – Nov 6 | replay engine finish; Level 2 and Level E conformance; server-offset rule from the DST change (TBD-1) | | |
| Nov 9 onward | rule-only baseline on development folds -> P1 gate | | no date for the P1 verdict until the baseline exists |

## Signals to date

| # | Bar (server) | Dir | Session | Stop | Outcome | R |
|---|---|---|---|---|---|---|
| 1 | 2026-10-02 09:45 | LONG | LONDON | 7.15 | TP_HIT, 5 bars | +1.856 |
| 2 | 2026-10-07 17:30 | SHORT | NY | 28.24 | SL_HIT, 34 bars | -1.000 |

n = 2, net +0.856R. Supports no claim about edge. Throughput about 1.6 signals
per week, which is why the replay, not forward collection, is the critical path.

## Weekly checkpoint

At the end of each week, on the VPS:

```bash
cd /root/Hermes && git pull && source venv/bin/activate && export DISPLAY=:99
python weekly_checkpoint.py --week=<Monday, YYYY-MM-DD>   # cycles, signals, funnel, 0g-1
python research/probe_signal_outcome.py                     # resolve any new signals
```

Then update this page: the "Actual" column, the signals table, the `<- HERE`
marker, and one line in the log below.

| Checkpoint | Cycles | Signals | Notes |
|---|---|---|---|
| 2026-10-13 (wk Oct 5) | pending | 1 (SHORT) | first 0g-1 signal: delay 484.98 s, live bid 0.18 from M1 proxy |

## What runs (cron, weekdays, UTC)

| Minutes | Job |
|---|---|
| `:07 :22 :37 :52` | `run_soak.py`, connectivity and resources |
| `:08 :23 :38 :53` | `run_hermes.py`, strategy cycle, one event per run to `logs/events.jsonl` |

Ares shares the droplet and crontab. Edit only the two Hermes lines, back up
first, and diff before installing. The `:08` schedule does not collide with Ares
(measured 13 Oct); moving it is deferred until the replay prices the benefit.

## Known open items

- **Server offset across DST (contract H1).** Watch `server_offset_seconds` in
  the weekly checkpoint around 25 Oct and 1 Nov.
- **M1 depth (contract H2).** `MaxBars` very probably caps M1 at about 72
  trading days; a 4-year replay needs it raised or read Wine-side.
- 7–13 October funnel reconciliation: `weekly_checkpoint.py` not yet run on the VPS.
- The live alert sizes from the signal-close stop, not the executable entry.
  The 0g-5 resize is logged as an observation only.
- Exit spread is approximated by the signal-time spread (0g-6 `TBD`).
- `ensure_connected()` spends an unconditional 2 s reconnect backoff every run.
- Cron `dow=1-5` misses the Sunday 22:00 UTC gold open (ASIA, untradeable; research gap only).

## Standing rules

- Hermes only; never modify Ares.
- Closed M15 bars only; Pullback Setup only; XAU/USD only.
- No parameter change on small samples. Displacement and minimum-RR filters stay
  preregistered candidates, not policy.
- Athena is partial reuse only (metrics, exit_policy, reconcile, indicators).
- Controls before numbers: every probe gates on tests whose answers are known.
