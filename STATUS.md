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
✔ 0g      entry timing + execution contract     ec-0.2 structure frozen 14 Oct
            (ec-1.0 needs TBD-1..5, built §9 tests, conformance: Data/Replay/Conf)
▶ 0b      preregister success criteria, folds, sealed holdout   <- HERE
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
| Oct 12 – Oct 16 | catch-up funnel; 0g-6 draft -> review -> freeze | 13 Oct: SHORT-side spread defect fixed (`3fdef54`), both signals resolved, first live conformance point, STATUS and weekly checkpoint added, 0g-6 drafted as `EXECUTION_CONTRACT.md` ec-0.1, reviews 1-4 HOLD (10 + 8 + 3 + 2 findings), all accepted in ec-0.1r2-r5. 14 Oct: review 5 APPROVE, **ec-0.2 structure frozen** (tag `ec-0.2`) | drafting found H1-H3, which add work to the weeks of Oct 19 and Oct 26 |
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
python weekly_checkpoint.py --self-test                    # expect ALL PASS
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
- **Frozen tick after the daily quote stop (found 14 Oct).** On 12 and 13 Oct,
  XAUUSD ticks stop at server 22:59:58, about 20:00 UTC. From then on,
  `get_server_time_offset()`, which reads the last tick, falls by 900 s per
  cycle, and the scheduling delay reads about 899 s. Position 1 also stays on
  the 22:30 server bar, so that bar is re-evaluated (NY session, blocked by the
  signal-key dedup), and the 22:45 server bar (19:45–20:00 UTC) is very
  probably never evaluated on time. `weekly_checkpoint.py` now excludes these
  cycles. Still open: confirm on the VPS when ticks resume; decide whether
  `run_hermes.py` should log the tick age; make the live-vs-replay difference
  for the day's last bar explicit in the contract (a new version after
  `ec-0.2`) and in TBD-5, the trading calendar.
- **Memory (14 Oct).** `vm.swappiness` is still 60; the ROADMAP fix (10) was
  never applied. Every night Ares' 21:00 run causes a swap-out burst at the
  21:07 soak sample: 12 Oct 25,484 pages with a 382 ms stall, below the alert;
  13 Oct 43,045 pages with a 517 ms stall, which raised the alert. Free RAM
  stayed above 860 MB and Hermes was unaffected. Applying 10 is an owner
  decision, because the setting is system-wide.
- **MT5 authorization failure, 13 Oct 11:52 UTC.** `(-6, 'Terminal:
  Authorization failed')` on all three attempts. Memory was normal. The 11:53
  Hermes run recovered on its third attempt with delay 499 s, so no cycle was
  lost. Single occurrence; act only if it recurs.
- The live alert sizes from the signal-close stop, not the executable entry.
  The 0g-5 resize is logged as an observation only.
- Exit spread is approximated by the entry-time spread draw (contract C3.5, declared approximation).
- `ensure_connected()` spends an unconditional 2 s reconnect backoff every run.
- Cron `dow=1-5` misses the Sunday 22:00 UTC gold open (ASIA, untradeable; research gap only).

## Standing rules

- Hermes only; never modify Ares.
- Closed M15 bars only; Pullback Setup only; XAU/USD only.
- No parameter change on small samples. Displacement and minimum-RR filters stay
  preregistered candidates, not policy.
- Athena is partial reuse only (metrics, exit_policy, reconcile, indicators).
- Controls before numbers: every probe gates on tests whose answers are known.
