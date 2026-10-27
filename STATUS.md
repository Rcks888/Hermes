# Hermes: current status

Last updated: 2026-10-27. This page is the entry point. Detailed reasoning and
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
✔ 0b      preregistration PREREGISTRATION.md    pr-0.4 protocol frozen 23 Oct
            (pr-1.0 needs its §9 tests built with the replay engine)
✔ Data    M15 + M1 history CSV export, Wine side   exported 27 Oct, F3 met
            (TBD-2 resolved: MaxBars raised for the export only, then restored)
▶ Replay  purpose-built M15 delayed-entry engine   <- HERE (starts week of Nov 2)
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
| Oct 12 – Oct 16 | catch-up funnel; 0g-6 draft -> review -> freeze | 14 Oct: funnel for weeks Oct 5 and Oct 12 reconciled, frozen-tick and nightly-swap findings, `vm.swappiness=10` applied. 13 Oct: SHORT-side spread defect fixed (`3fdef54`), both signals resolved, first live conformance point, STATUS and weekly checkpoint added, 0g-6 drafted as `EXECUTION_CONTRACT.md` ec-0.1, reviews 1-4 HOLD (10 + 8 + 3 + 2 findings), all accepted in ec-0.1r2-r5. 14 Oct: review 5 APPROVE, **ec-0.2 structure frozen** (tag `ec-0.2`). 15 Oct: second broker outage, 13 Oct probe evidence committed. Sat 17 Oct: host reboot test with Ares; MT5 build 6271 blocked on Wine 9, moved to WineHQ Wine 11.0, PASS (see Incidents) | drafting found H1-H3, which add work to the weeks of Oct 19 and Oct 26 |
| Oct 19 – Oct 23 | 0b preregistration; M15 **and M1** CSV export and validation, including the M1 `MaxBars` test (contract TBD-2) | **Target partly met: 0b done 23 Oct, `pr-0.4` protocol frozen after three reviews (HOLD, HOLD, APPROVE); export not started.** 19 Oct: reboot validated on live cron (00:07 PASS), third broker outage. 20 Oct: swap timeline from host `sar`, broker-outage table, cross-process MT5 failure-state fix, `setup_vps.sh` to WineHQ; signal 3 (SHORT, NY), first live reconnect alert, fourth broker outage. 22 Oct: unattended-upgrade outage and host lock-up, MT5 pile-up controls, eMudhra/certifi fix, `requirements.lock` (see Incidents). 23 Oct: checkpoint, firewall confirmed harmless, signal 3 resolved SL_HIT, outcome-probe window defect found and fixed; signal 4 (LONG, NY), SL_HIT after 1 bar | Two unplanned host incidents (20 Oct failure state, 22 Oct lock-up) and the eMudhra check took the week. Export carried to Oct 26; later rows move one week. 0b was done on Fri 23 Oct, a free day |
| Oct 26 – Oct 30 | **carried:** M15 and M1 CSV export and validation, M1 `MaxBars` test (TBD-2). Export and data-quality checks only: no strategy evaluation on holdout bars (2025-09-01 to 2026-09-11, `PREREGISTRATION.md` F6). Watch the offset across the EU DST change on 25 Oct (TBD-1) | Mon 26 Oct: no work. 27 Oct: checkpoint a day late; EU DST change seen (offset 3.0 -> 2.0 h), the daily quote stop moved one hour later in UTC; signal 4 resolved. **Target met 27 Oct:** TBD-2 probe at `MaxBars` 100,000 = `M1_CAPPED_OR_ABSENT` (M1 absent from 1 Jun 2026 back, M15 absent in 2022); exporter `--maxbars` (`87b53be`, MaxBars-only `/config` file, `common.ini` never opened); at 2,000,000 = `M1_AVAILABLE`, all 9 windows covered to 2022-06-23. Export 2022-06-01 .. 2026-10-27 server: M15 103,511 rows, M1 1,549,410 rows, 0 unstable chunks, 0 structural defects, C6.3 0 mismatches / 0 envelope failures / 0 orphans, F3 met. Restored to 100,000 and verified. MT5 updated 6281 -> 6285 -> 6286 on the two restarts | moved from Oct 19; checkpoint on Tue (busy Mon) |
| Nov 2 – Nov 6 | contract amendment after `ec-0.2` (daily quote stop TBD-5, retry-budget decision, near-stop fills H6); replay engine build, controls first. Offset across the US DST change on 1 Nov | | moved one week |
| Nov 9 – Nov 13 | replay engine finish; Level 2 and Level E conformance; server-offset rule (TBD-1) | | moved one week |
| Nov 16 onward | rule-only baseline on development folds -> P1 gate | | no date for the P1 verdict until the baseline exists |

## Signals to date

| # | Bar (server) | Dir | Session | Stop | Outcome | R |
|---|---|---|---|---|---|---|
| 1 | 2026-10-02 09:45 | LONG | LONDON | 7.15 | TP_HIT, 5 bars | +1.856 |
| 2 | 2026-10-07 17:30 | SHORT | NY | 28.24 | SL_HIT, 34 bars | -1.000 |
| 3 | 2026-10-20 15:15 | SHORT | NY | 12.99 | SL_HIT, 5 bars | -1.000 |
| 4 | 2026-10-23 16:45 | LONG | NY | 6.51 | SL_HIT, 1 bar | -1.000 |

Signal 3 (ROADMAP "Third signal"): executable bid 2.78 below the signal close,
so the 0g-5 baseline resizes 0.76 -> 0.63 lots ($993.51); the alert's 0.76 lots
would have risked about $1,199 (1.20%). Outcome is the same from either entry.
Signal 4: entry 4022.85, SL 4016.34, TP 4035.87, 1.53 lots, regime
TRANSITION/NORMAL, delay 484.99 s, alert sent. MFE 0.00 and MAE 15.75 in the
first bar, 2.4 times the stop: the -1R assumes the stop fills at its level,
which a real stop in that bar may not have. Spread is 7.5% of risk (RR 1.79
with spread). **At the decision the ask was 5.81 below the signal close: the
executable stop was 0.70 (bid 0.16 above SL), so the 0g-5 baseline sized
14.28 lots, RR 26.9, margin $57,363**; the alert's 1.53 lots would have risked
$107 (0.11%). Proposed hazard H6 (ROADMAP "Fourth signal").

n = 4 resolved, net -1.144R (`signal_outcome_20261027T024014Z.json`; signals
1-3 reproduce the earlier figures exactly). Supports no claim about edge.
Throughput: 4 signals in the 4 weeks since the 30 Sep fix, about 1 per week,
which is why the replay, not forward collection, is the critical path.

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
| 2026-10-14 (wk Oct 5) | 483 of 480 (3 extra, probably manual reruns during the 7 Oct work: 3 repeated LONDON bars) | 1 (SHORT, SL_HIT) | 0 aborts. Funnel reconciles: 283 tradeable cycles, 260 distinct bars. Delay p50 484.0 s, p95 485.0 s (n=217 fresh). Offset UTC+3 on all 216 fresh cycles. 51 stale-tick cycles. First 0g-1 signal: delay 484.98 s, live bid 0.18 from the M1 proxy |
| 2026-10-14 (wk Oct 12, partial, to Wed 08:53 UTC) | 228 of 228 scheduled so far | 0 | 0 aborts; the 13 Oct 11:52 failure was soak-only. 120 tradeable cycles, 112 distinct bars. Delay p50 484.0 s, p95 485.0 s. UTC+3 on all 212 fresh cycles. 16 stale-tick cycles. 2 suspect delays of about 7,680 s: the 22:45 server bar evaluated at 22:08 UTC |
| 2026-10-23 (wk Oct 12, final) | 480 of 480 | 0 approved, 1 rejected | 1 abort (15 Oct 11:08 LONDON, broker outage). Rejection: 15 Oct 08:30 server bar, LONG, ASIA, session gate, as designed. 279 tradeable cycles, 259 distinct bars. Delay p50 484.0, p95 485.0, max 501 s (n=427). UTC+3 on all 431 fresh cycles. 48 stale-tick cycles; 4 suspect delays, all the daily-stop 22:45 bar. Quotes 479/479, latency p50 5.0 ms |
| 2026-10-23 (wk Oct 19, to Fri 02:53 UTC) | 395 of 396 scheduled so far (1 missing, most likely 22 Oct 08:23 while cron was paused for recovery; the pile-up runs logged their events up to 16 min late, so a per-slot check cannot place it) | 1 (SHORT, SL_HIT) | 11 aborts, all `mt5_not_connected`: 19 Oct 19:23, 20 Oct 16:38, and 9 in the 22 Oct lock-up. 216 tradeable cycles, 200 distinct bars. Delay p50 484.0, p95 485.0, max 487 s (n=348). UTC+3 on all 352 fresh cycles (EU DST ends 25 Oct). 32 stale-tick cycles; 4 suspect delays, all the daily-stop bar. Quotes 384/384. Signal 3 delay 483.99 s |
| 2026-10-27 (wk Oct 19, final) | 479 of 480 (the 1 missing as above) | 2 (SHORT SL_HIT; LONG SL_HIT) | 11 aborts as above. 272 tradeable cycles, 252 distinct bars; funnel 207 trend, 31 candle, 26 pullback, 4 volume, 1 SL too wide, 1 VWAP, 2 to the risk gate. Delay p50 484.0, p95 485.0, max 487 s (n=416). UTC+3 on all 420 fresh cycles. 48 stale-tick cycles (NY 20, OFF 24, ASIA 4); 4 suspect delays, all the daily-stop bar. Quotes 468/468, latency p50 4.5 ms. Signal 4 delay 484.99 s. Code versions include `aeed15d-dirty`: `code_version` counts untracked files as dirty (`git status --porcelain`), most likely the uncommitted 23 Oct probe JSONs |
| 2026-10-27 (wk Oct 26, to Tue 02:38 UTC) | 107 of 107 scheduled so far | 0 | 0 aborts. 56 tradeable cycles, 56 distinct bars. Delay p50 484.0, p95 485.0, max 486 s (n=102). **UTC+2 on all 103 fresh cycles: EU DST change seen.** 4 stale-tick cycles, all OFF (none in NY); 1 suspect delay, 4,084 s (the daily-stop 22:45 bar, 7,684 s less 3,600). Quotes 107/107, latency p50 4.8 ms. Code `dbfbe05` (VPS pulled `f436f8f`, docs only, on 27 Oct) |

## What runs (cron, weekdays, UTC)

| Minutes | Job |
|---|---|
| `:07 :22 :37 :52` | `run_soak.py`, connectivity and resources |
| `:08 :23 :38 :53` | `run_hermes.py`, strategy cycle, one event per run to `logs/events.jsonl` |

Ares shares the droplet and crontab. Edit only the two Hermes lines, back up
first, and diff before installing. The `:08` schedule does not collide with Ares
(measured 13 Oct); moving it is deferred until the replay prices the benefit.

## Known open items

- **Server offset across DST (contract H1).** EU DST change seen at the
  27 Oct checkpoint: UTC+3 through 23 Oct, UTC+2 on every fresh cycle from
  26 Oct. The daily quote stop follows server time: it is still server 23:00,
  now 21:00 UTC, so on 26 Oct the NY hour 20:00-21:00 UTC was quoted (0 NY
  stale cycles, against 20 in the week of Oct 19). One day only so far.
  Consequences, no code change now:
  - The history export needs the offset per date to convert bar times to UTC.
    Working assumption UTC+3 in EU summer time, UTC+2 otherwise; verify it in
    the exported bars from the position of the daily break (TBD-1).
  - Quoted NY hours in UTC differ between summer and winter, and the replay
    inherits that from the data.
  - Watch the offset and the daily stop again across the US DST change on 1 Nov.
- **M1 depth (contract H2, TBD-2) resolved 27 Oct.** The client cap, not the
  server, limited M1: at `MaxBars` 100,000 M1 stops between 3 Aug and 1 Jun 2026;
  at 2,000,000 M1 is complete back to 2022-06-23 (6,600-6,900 minutes a week,
  15 per M15 bar). The cap also limits M15: the 2022-06-23 "server floor" of
  30 Sep was the edge of the capped client history, which has since moved past
  July 2022; the server holds M15 and M1 from at least 2022-06-01. F1 still fixes
  the replay span at no earlier than 2022-06-23. Live runs at 100,000 (restored,
  verified from `terminal_info`); re-export uses `--maxbars 2000000` again.
  For the next contract amendment: record TBD-2 as resolved in C6.2/H2.
- **History snapshot 20261027T070105Z** (`data/history/`, not in git; evidence
  `research/data_feasibility/history_export_20261027T070105Z.json`):
  - M15 `4af57ead...d377`, M1 `6348a40a...2969`, `data_snapshot_hash`
    `520a4850231da2d0c9ad8c7b6653f739c20d99f5956246395a571bcd87804fac`.
  - Server time, 2022-06-01 01:00 .. 2026-10-26 22:59. Holdout rows only
    counted and hashed (F6).
  - 1,402 of 103,511 M15 bars (1.4%) have 1-14 of their 15 minutes. The replay
    handles them by C1.6 at entry and C5.8 after it; where they fall is a
    replay-build question, not checked here.
  - Still to do on these bars: UTC conversion per date from the daily-break
    position (TBD-1, week of Nov 9).
- **`-dirty` code versions on 27 Oct live cycles** (06:38, 06:53) come from
  probe JSONs that were untracked between being written and committed on the
  VPS. Harmless; commit evidence promptly to keep `code_version` clean.
- **Frozen tick in the daily quote stop (found 14 Oct, confirmed by both checkpoints).**
  XAUUSD stops quoting at server 23:00 (20:00 UTC at UTC+3, 21:00 UTC at UTC+2)
  and resumes between 21:53 and 22:08 UTC. Figures below are for UTC+3. In between:
  - The tick-derived offset and delay are artefacts.
  - Position 1 stays on the 22:30 server bar, which is re-evaluated 4 times in
    NY (20:08–20:53) and 4 times in OFF (21:08–21:53). The pattern is exact
    across 7 weekdays.
  - The day's last bar, 22:45 server (19:45–20:00 UTC), is evaluated at 22:08
    UTC, 7,680 s late and in OFF, so it can never alert. On Fridays it is never
    evaluated.

  **No trading impact:** there are no quotes until about 22:00 UTC, so that bar
  is untradeable at any delay, and the signal-key dedup blocks repeat alerts.
  `weekly_checkpoint.py` excludes these cycles and reports distinct bars.
  **Pending contract amendment** for the version after `ec-0.2`: the replay
  records such a bar as `quote_unavailable` at D (C1.6, C1.8), while live
  evaluates it at reopen in OFF. It must be excluded from E1 and E3 with an
  explicit label, and the daily stop goes into TBD-5. No `run_hermes.py`
  change proposed.
- **Memory: swap eviction in Ares windows (updated 20 Oct).** Before 14 Oct
  the Ares 21:00 run caused a swap-out burst at the 21:07 soak: 12 Oct 25,484
  pages with a 382 ms stall; 13 Oct 43,045 pages with a 517 ms stall (alert).
  **`vm.swappiness=10` applied 14 Oct about 09:00 UTC**, persisted in
  `/etc/sysctl.d/99-swappiness.conf` (it was 60). Host `sar` for the
  21:00–21:10 UTC slot:

  | Night | Swappiness | Swap growth, 20:50 → 21:20 | Hermes 21:07 |
  |---|---|---|---|
  | 13 Oct | 60 | +163 MB (302 → 469 MB) | THRASHING, 517 ms |
  | 14 Oct | 10 | +36 MB | quiet, 402 ms |
  | 19 Oct | 10 | 0 (flat at 316 MB) | quiet, 113 ms |

  Mon 19 Oct swap grew 38 → 316 MB in two other windows: 13:30–13:40 (+105 MB,
  Ares 13:30 run, gateway up) and 16:00–17:30 (+167 MB, gateway up after the
  16:00 restart). Largest stall 309 ms per 15 min; no alert, no impact.
  **Confounded:** 19 Oct Ares screened only 3 symbols (Finviz broken), so it
  cannot separate swappiness from workload. Needs a night with normal Ares
  screening before concluding. The Ares dashboard's 171 MB / 1386 ms for
  19 Oct are window totals, not a 21:07 burst. The MT5 server-midnight
  hypothesis is withdrawn: the rollover happened on 19 Oct with no burst.
- **Broker-side MT5 outages (13, 15, 19, 20 Oct).** `(-6, 'Terminal:
  Authorization failed')` is what `initialize()` returns while the terminal
  itself has lost MetaQuotes-Demo. The MT5 terminal log shows each one:

  | Date (UTC) | Terminal lost | Re-authorised | Outage | Hermes cycle lost |
  |---|---|---|---|---|
  | 13 Oct | 11:49:08 | 11:53:16 | 4.1 min | none (11:53 succeeded on retry 3, delay 499 s) |
  | 15 Oct | 11:05:18 | 11:09:55 | 4.6 min | 11:08 LONDON |
  | 19 Oct | 19:19:12 | 19:25:01 | 5.8 min | 19:23 NY |
  | 20 Oct | 16:36:14 | 16:42:18 | 6.1 min | 16:38 NY |

  Measured from the start of the lost run, the terminal re-authorised after
  1.9 min (15 Oct), 2.0 min (19 Oct) and 4.3 min (20 Oct). Input for the
  retry-budget decision, not a decision.

  The retry budget is 17 s (2 + 5 + 10), so an outage that covers a :07/:08
  slot costs that cycle. **Open decision, retry budget:** about 6 min of retries
  would ride these out, but it moves decision time D to about 14 min after bar
  close, which touches contract §1 and E1. Decide with the next contract
  amendment, not as a patch. Separately, MT5 drops for about 1 s on every
  access-point switch (2–3 min after each authorisation); harmless so far.
- **MT5 build 6281 installed 22 Oct 08:26** on the manual restart. Each later
  build installs on the next terminal restart, which 17 and 22 Oct showed is the
  risky moment; the 22 Oct controls now bound it (see Incidents). 27 Oct: the
  `--maxbars` restarts installed 6285 (06:47) and 6286 (07:01), both connected
  first time; confirm the first live cycles after 07:04 at the next checkpoint.
- **needrestart override for `xvfb.service` (22 Oct).** Verify after the next
  library upgrade: the unattended-upgrades dpkg log should list `xvfb.service`
  under "Service restarts being deferred". Xvfb then runs old libraries until a
  planned reboot; schedule one with Ares when `/var/run/reboot-required` appears.
- The 240 s per-run retry budget (22 Oct) caps runaway runs only. It is not the
  open retry-budget decision above, which concerns riding out broker outages.
- The live alert sizes from the signal-close stop, not the executable entry.
  The 0g-5 resize is logged as an observation only. At the executable fill the
  alert's size would have risked 0.87% (signal 2), 1.20% (signal 3) and
  0.11% (signal 4).
- **Proposed H6, near-stop executable fills (signal 4, 27 Oct).** When the
  delay moves price towards the stop, the 0g-5 baseline keeps $1,000 risk by
  sizing up: signal 4 gave 14.28 lots, RR 26.9, bid 0.16 above the stop. ec-0.2
  does not bound the executable stop against the spread or the original stop,
  and stops fill at their level, so such trades can dominate replay R. Decide
  in the next contract amendment (week of Nov 2), before any SIMULATION; no
  live change.
- Exit spread is approximated by the entry-time spread draw (contract C3.5, declared approximation).
- `ensure_connected()` spends an unconditional 2 s reconnect backoff every run.
- Cron `dow=1-5` misses the Sunday 22:00 UTC gold open (ASIA, untradeable; research gap only).

## Incidents

| Date (UTC) | What happened | Resolution |
|---|---|---|
| 14 Oct 06:22 | Unattended upgrade (libx11) restarted `xvfb.service`; MT5 relaunched with LiveUpdate, init 32 s | Self-recovered. Explains the 14 Oct wine/Xvfb pair Ares asked about |
| 17 Oct 01:10–01:50 | Planned host reboot (kernel 6.8.0-147). MT5 updated to build 6271 on relaunch and stopped answering the IPC pipe on Wine 9.0 ("debugger found" dialog; MT5 asks for Wine 10+) | Wine prefix backed up, Ubuntu Wine 9.0 replaced by WineHQ stable 11.0, avahi disabled, `wineboot -u` with Mono/Gecko overrides. PASS 01:50 UTC, init 21 s. Mon 19 Oct 00:07 soak PASS, init 2.06 s. `setup_vps.sh` now installs WineHQ |
| 20 Oct 16:37–16:52 | MT5 disconnect at the 16:37 soak; first live "reconnected" alert, 14.7 min after the first failed check | Self-recovered. **Broker-side**: the terminal lost MetaQuotes-Demo 16:36:14 and re-authorised 16:42:18 (Access Point HA, 6.1 min), then a 1 s access-point switch at 16:45. Lost the 16:38 NY cycle. Added to the outage table |
| 22 Oct 06:05–08:26 | Unattended upgrades (libnss3, liblzma5, ca-certificates, ...) at 06:04–06:06; needrestart restarted `xvfb.service` and `winbind`. MT5 lost its display; build 6281 was pending, every relaunch hit the 60 s IPC timeout (-10003) and left a terminal behind. Runs took 564–968 s, overlapped, and the host stopped answering SSH logins (load 8). Every alert tier went out twice. Last good check 05:53, 9 failed slots, 16 failed runs, about 2 h 20 min lost (ASIA and LONDON) | Hermes cron paused, `wineserver -k`, manual soak: build 6281 installed, connected 08:26, cron restored. Host: `/etc/needrestart/conf.d/50-hermes-xvfb.conf` stops needrestart restarting `xvfb.service` (Xvfb keeps old libraries until a planned reboot). Code: one MT5 run at a time (`logs/mt5.lock`, busy run logs `CYCLE_ABORTED mt5_busy`), 240 s retry budget per run, stuck terminal killed after an IPC timeout, alert state locked across processes. 5 + 1 controls, 4 + 1 fail on the old code |
| 22 Oct, check | eMudhra CA key compromise and PHP RCE advisories. No PHP or web server on the host; only `sshd` listens publicly. System trust store (`ca-certificates` 20260816ubuntu0.24.04.1, upgraded 06:05) makes curl and git reject both diagnostic chains. Hermes and Ares venvs had certifi 2026.07.22, and Python `requests` **accepted** the `dv-g3b-emudhra` test chain | Hermes: `certifi>=2026.10.19` pinned in `requirements.txt`, venv upgraded, test chain rejected. Ares venv reported to the Ares thread (Ares frozen). The upgrade (`pip install -U -r`, 08:49 UTC) also moved **numpy to 2.5.4 and pandas to 3.0.6**; previous versions unrecoverable, most likely pandas 3.0.x and numpy 2.x (Ares venv: 3.0.5, 2.2.6). All 9 suites pass; first strategy cycle on the new libraries 08:53. Versions now frozen in `requirements.lock`; deploys install from it. Any funnel change from 22 Oct 08:49 has this as a candidate cause. Ares side (22 Oct, Ares logbook): Ares venv certifi 2026.10.19 (`--no-deps`), emSign roots removed from the gateway JRE, and a **DigitalOcean cloud firewall on the host: inbound TCP 22 only, outbound unrestricted**. Hermes needs outbound only (MetaQuotes, Telegram, GitHub) and rpyc is loopback. **Confirmed 23 Oct**: soak init 2.03 s, strategy cycles normal, alerts and `git pull` working |
| 23 Oct, found | `probe_signal_outcome.py` fetched a fixed 700 bars (start 13 Oct by then) and filtered `time > bar_time` without checking the signal bar was in the window, so signals 1 and 2 were resolved on bars from 13 Oct onwards (signal 2: SL_HIT after 1 bar, MAE 88.17). It also printed the R:R ratio as "realised" for stop-outs | Fixed: signal bar must be in the window, else NOT RESOLVED; fetch widened to the oldest signal; signed result R. 3 controls; the window control fails on the old filter. The 23 Oct 03:02 JSON is invalid for signals 1 and 2 and is not committed; signals 1 and 2 stand on the 13 Oct evidence |
| 20 Oct, found | Cross-process failure state: `ensure_connected()` kept its failure count in module globals, so every cron run started at 0. The count was always 1, the "still down" and CRITICAL tiers never fired, no reconnect was ever reported, and the throttle footer cited a never-cleared 13 Oct `first_seen` | Fixed: failure state persisted in `logs/mt5_failure_state.json`, counted per 15-min slot; reconnect alert now fires; footer cites the previous alert. 7 + 1 controls, 5 fail on the old code |

## Standing rules

- Python packages: install from `requirements.lock` only (`pip install -r requirements.lock`).
  Never `pip install -U -r requirements.txt` on the VPS; change one pinned
  version at a time and record it under Incidents.
- Hermes only; never modify Ares.
- Closed M15 bars only; Pullback Setup only; XAU/USD only.
- No parameter change on small samples. Displacement and minimum-RR filters stay
  preregistered candidates, not policy.
- Athena is partial reuse only (metrics, exit_policy, reconcile, indicators).
- Controls before numbers: every probe gates on tests whose answers are known.
