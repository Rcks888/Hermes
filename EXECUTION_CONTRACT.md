# Hermes execution contract (0g-6)

**Version `ec-0.1` — DRAFT, 2026-10-13.** Structure under review. Values marked
`TBD` are deliberately unset and each has a named resolution path (§11).

**Scope.** How the replay engine turns a historical M15 setup into an
executable, costed, resolved trade, and how that is checked against what live
Hermes recorded. It implements the frozen 0g-5 baseline unchanged. It is a
research contract, **not authorisation for live execution**.

**Identity.** Every replay run records `strategy_sha`, `config_hash`,
`data_snapshot_hash` (M15 and M1), `replay_engine_sha`, `contract_version`,
`spread_model_hash`, `offset_rule_version`, `broker_spec_version` and the delay
parameter. A result without the full identity is not a result.

---

## 1. Clocks and timestamps

| # | Clause |
|---|---|
| C1.1 | MT5 timestamps are **server clock, bar open**. An M15 bar stamped T spans `[T, T+900)` and closes at `T+900`; M1 likewise with 60. |
| C1.2 | The strategy pipeline runs on **server-clock bars, unconverted**, exactly as live. `indicators.calculate_vwap` resets on `datetime.dt.date`, i.e. at server midnight; converting bars to UTC first would move the reset and break parity. |
| C1.3 | UTC is derived only where a rule needs it (sessions, cron coverage): `utc = server - offset(t)`. **`offset` is not a constant** (hazard H1). Rule `TBD-1`. Until frozen, any replay span crossing a DST change is provisional. |
| C1.4 | **Decision instant** `D = bar_close + delay`. `delay` is a declared run parameter. Baseline **480 s**, the live schedule (observed live 484.978 s, the ~5 s being process overhead). Sensitivity runs at all five preregistered delays, 60 / 120 / 180 / 300 / 480 s, all reported. The live delay distribution is used for conformance (§8), never sampled. |
| C1.5 | No fixed R haircut is applied for delay. The "18% of nominal R" figure is retired; delay acts per trade through C1.4 and C4.1. |
| C1.6 | **Price at instant X** is the **open of the M1 bar stamped `floor_minute(X)`**. A missing minute is never forward-filled: the opportunity is recorded `quote_unavailable` with detail `m1_minute_unavailable`. |
| C1.7 | One evaluation per closed M15 bar whose decision instant falls **Mon-Fri UTC**, mirroring cron `dow=1-5`. Each bar is evaluated once, so live signal de-duplication is inert in replay. |

## 2. Strategy and risk gates

| # | Clause |
|---|---|
| C2.1 | The replay calls the **same pipeline**, imported from `engine/`, on the **200 closed M15 bars ending at the signal bar**: `market_structure.analyze` -> `calculate_vwap` -> `volume_average` -> `calculate_atr` -> `find_sr_zones` -> `strategy.evaluate` -> `risk_manager` filters. Prerequisite: lift that sequence out of `run_hermes.main()` into one shared function used by both. No backtest-only copy. |
| C2.2 | Session = `run_hermes.get_session_tag(utc_hour(D))`, imported. The probe-local `session_of()` is **not** used; it disagrees by one hour at three boundaries (table below). |
| C2.3 | The spread filter (`max_spread_points` 50) is applied to the **spread-model draw** (§3), in points. Never to bar spread. |
| C2.4 | Two modes, never mixed in one report. **CONFORMANCE** reproduces live alert-only state: no open positions and a daily loss count of zero, because live never records outcomes (`trades.json` `outcome: null`) and never holds positions (hazard H3). Used for §8. **SIMULATION** enforces `max_open_trades` 1 from simulated open trades and `daily_loss_limit` 3 stop-outs per **server-clock date**. Performance metrics come from SIMULATION only. |

Session boundaries, UTC:

| Session | Engine (`get_session_tag`) | Probe-local (`session_of`, fixed UTC+3) |
|---|---|---|
| ASIA | 23:00 - 07:00 | 00:00 - 08:00 |
| LONDON | 07:00 - 12:00 | 08:00 - 13:00 |
| NY | 12:00 - 21:00 | 13:00 - 21:00 |
| OFF | 21:00 - 23:00 | 21:00 - 24:00 |

Probe session splits in earlier evidence remain indicative only.

## 3. Prices and costs

| # | Clause |
|---|---|
| C3.1 | Price basis is **BID**, determined empirically in 0g-2. |
| C3.2 | Spread is **TIER 3**: measured live distributions, never broker bar spread (0a: bar spread understates live by 3.5x at the median, in 100% of 1,206 pairs). A per-session empirical distribution is frozen as a **versioned artefact with a hash before any replay result is viewed**. Source `TBD-3`. |
| C3.3 | Draw: deterministic, `u = hash(bar_close_epoch)` mapped to the session distribution's quantile `u`. The **same `u` is reused across all delay runs** (common random numbers), so delay comparisons are not confounded by spread noise. Sensitivity runs at constant session p50 and p90. |
| C3.4 | Direction accounting is `entry_observation.spread_accounting`, unchanged: LONG fills at `bid + spread` and triggers on bid at the levels; SHORT fills at bid and triggers when bid reaches `level - spread`. Spread is charged **once**, on the leg that transacts at the ask. |
| C3.5 | Exit spread equals the trade's entry-time draw. Declared approximation. Bar spread cannot refine it, because of the 3.5x understatement; this corrects an earlier note suggesting otherwise. |
| C3.6 | Commission: none modelled. The live account's commission must be verified before Phase 4. |
| C3.7 | Swap: not modelled in baseline. Each trade records its rollover crossings, and `swap_long` / `swap_short` from the broker spec are reported as a bound beside the headline. |
| C3.8 | Entry slippage beyond the quote: none. Exit slippage: only the gap rule C5.4. |

## 4. Entry and sizing (0g-5, unchanged)

| # | Clause |
|---|---|
| C4.1 | Build the quote at D: `bid` per C1.6, `ask = bid + draw`. Pass it to **`entry_observation.propose_execution`**, the same function live logs from. No reimplementation. |
| C4.2 | Original SL and TP fixed. Volume from executable entry to original SL, rounded **down** to the broker step, within broker min and max, taken from a versioned broker-spec snapshot (`contract_size` 100, `volume_min` 0.01, `volume_step` 0.01). |
| C4.3 | Balance is a fixed **$100,000, non-compounding**. **R is the unit of account**; cash is secondary and equals R times planned stop-loss cash. |
| C4.4 | Margin is not modelled: `margin_not_checked` is treated as executable in replay and counted. At 1% risk on $100,000 the one live margin reading was 1,642 for 0.40 lots, far from binding. |
| C4.5 | Every non-executable status is recorded in population B and excluded from C. It is never scored as a loss and never as a strategy rejection. |

## 5. Exit resolution

| # | Clause |
|---|---|
| C5.1 | Resolve on **M1 bid bars** from the entry minute `floor_minute(D)` onward, against the trigger levels of C3.4. Entry is at that minute's open, so its high and low are post-entry. |
| C5.2 | Pre-entry window `[bar_close, D)`: touches are recorded, never outcomes. A level already crossed at D is caught by `propose_execution` as execution-invalid. |
| C5.3 | SL and TP both inside one M1 bar resolves to **SL, flagged AMBIGUOUS**. Ambiguity count and an optimistic-ordering sensitivity are reported separately, never folded into the headline. |
| C5.4 | **Gaps.** An M1 bar opening beyond the SL trigger exits at that open, so a loss can exceed 1R. A bar opening beyond the TP trigger exits at the TP level; no positive slippage is credited. |
| C5.5 | No time stop and no management (V1 is fixed SL/TP). Trades may span sessions, days and weekends. |
| C5.6 | A trade still open at the end of the data is `OPEN`: counted, excluded from metrics. |
| C5.7 | **Realised R** = `(exit - fill) / (fill - SL)` for LONG and `(fill - exit) / (SL - fill)` for SHORT, where `exit` is the price on the closing leg: bid for LONG, `bid + spread` for SHORT. A clean stop is exactly -1R in both directions. |

## 6. Data dependencies

| # | Clause |
|---|---|
| C6.1 | M15: 2022-06-23 to present, about 100,000 bars, exported Wine-side to CSV, snapshot-hashed. |
| C6.2 | **M1 over the same span is required** by C1.6 and §5. `MaxBars=100000` very probably caps M1 at about 72 trading days (hazard H2). Resolution `TBD-2`. **No silent M15 fallback**: any run without M1 is labelled `M15_APPROX` and excluded from the P1 gate. |
| C6.3 | Control: M15 OHLC rebuilt from M1 must equal the exported M15 bars over the full span. Mismatches are listed, not tolerated. |

## 7. Populations and per-opportunity record

Populations per ROADMAP: **A** strategy signal, **B** risk-eligible, **C**
simulated execution. Performance metrics use C only.

Each opportunity in B writes one record: identity (bar open and close, server;
D in server and UTC; offset applied), mode, delay, session, spread draw `u` and
points, quote bid and ask, `propose_execution` output in full, trigger levels,
pre-entry touches, outcome, exit time and price, AMBIGUOUS flag, gap flag,
realised R, cash, rollover crossings, and M1 availability.

## 8. Conformance

Levels 1-4 as defined in ROADMAP, run in CONFORMANCE mode. 0g-6 adds
**Level E, execution parity**, against the 0g-1 fields of every live cycle:

| # | Check | Pass rule |
|---|---|---|
| E1 | Decision instant: replay D at baseline delay vs live `bar_close + scheduling_delay_seconds` | `abs(diff)` <= 60 s |
| E2 | Price: replay bid at D vs live `quote_bid` | difference distribution reported; tolerance `TBD-4`, frozen before any replay result. First point: 0.18 |
| E3 | `execution_validity_status` on signal cycles | equal, except margin |
| E4 | Spread: model distribution vs live decision-time `quote_spread_price` by session | distributional only, because the draw is synthetic |

Any unexplained mismatch at any level: **REPLAY NOT CONFORMANT, historical
performance reporting prohibited.**

## 9. Contract tests (preregistered)

Each test is written before the engine and must **fail on a deliberately broken
variant**, demonstrated once, as 0g-3 did.

1. Decision instant equals bar close plus delay; the M1 open is read, never the close (boundary values 10 / 20 / 30).
2. A missing decision minute gives `quote_unavailable`, never a fabricated price.
3. VWAP reset parity: the same bars converted to UTC give a different VWAP, and the replay matches live.
4. Session parity: the replay's session equals `get_session_tag` at boundary minutes 06:59 / 07:00 / 11:59 / 12:00 / 20:59 / 21:00 UTC.
5. The offset rule maps a known pre-DST and a known post-DST bar correctly (after `TBD-1`).
6. Spread draw is deterministic and identical across delay runs for the same bar.
7. LONG and SHORT accounting reproduce 1.856R (2 Oct) and the SHORT trigger levels 4130.19 / 4045.48 (7 Oct).
8. A SHORT stop is crossed on the ask in `propose_execution`.
9. A same-minute SL and TP gives SL plus AMBIGUOUS.
10. A gap through the stop exits at the bar open with R < -1; a gap through the target exits at TP.
11. In CONFORMANCE mode, a second signal while a simulated trade is open is not blocked; in SIMULATION mode it is blocked `Max open trades reached`.
12. Replaying both live signals in CONFORMANCE mode reproduces their logged `entry`, `sl`, `tp`, `blocked_at` and `candle_hash`.
13. M15 rebuilt from M1 matches the M15 export (C6.3).

## 10. Decisions for review

| # | Decision | Recommendation | Alternative rejected, and why |
|---|---|---|---|
| D1 | Delay model | Fixed 480 s baseline, sensitivity at 60-480 s | Sampling the live distribution extrapolates a few hundred observations over about 100,000 bars and ties the replay to a schedule that is still open |
| D2 | Spread draw | Deterministic hashed draw from the session distribution; p50 and p90 as sensitivity | A constant p50 never trips the 50-point filter in LONDON or NY (p50 28 / 30), so it over-counts eligible trades, the exact error 0a rejected bar spread for |
| D3 | Gap fills | Stop at gap open; target at level | Filling stops at the level understates weekend risk; crediting target gaps is optimistic |
| D4 | SIMULATION day boundary | Server-clock date | UTC date; the live code compares a server-clock bar date to a UTC date, so neither matches it, and server date is consistent with VWAP |
| D5 | Swap | Not modelled; rollover count and bound reported | Modelling it now needs per-date swap history the broker does not supply |
| D6 | Capital | Fixed $100,000, non-compounding, R as unit | Compounding makes results path-dependent and hides per-trade expectancy |
| D7 | Missing M1 | No silent fallback; `M15_APPROX` label, excluded from P1 | Silently approximating entry on M15 would reintroduce exactly what 0g exists to prevent |

## 11. Open values

| # | Value | Resolution | When |
|---|---|---|---|
| TBD-1 | Server offset rule across DST | Live `server_offset_seconds` is logged every cycle; watch it across 25 Oct (EU) and 1 Nov (US). Then confirm historically from week-open bar timestamps | by 6 Nov |
| TBD-2 | M1 coverage of the replay span | Wine-side test request for an M1 range in 2023. If capped, the user raises `MaxBars` manually (terminal stopped, outside cron, UTF-16 credential-bearing file) or history is read Wine-side | with the CSV export, week of 19 Oct |
| TBD-3 | Spread-model source and n | Prefer 0g-1 decision-time spreads once each tradeable session has enough cycles; otherwise the 1,206 soak measurements. Frozen with a hash | before the first replay result |
| TBD-4 | E2 price tolerance | From accumulated live-vs-proxy differences | before the first replay result |

## 12. Hazards found while drafting

| # | Hazard | Consequence | Action |
|---|---|---|---|
| H1 | Server offset assumed fixed at UTC+3. MetaQuotes servers commonly move to UTC+2 in northern winter; the EU change is 25 Oct 2026 | Replay sessions and cron coverage shift by an hour across half of every year. Live is unaffected because it measures the offset each cycle | TBD-1; weekly checkpoint watches the offset |
| H2 | `MaxBars` 100,000 was judged not worth raising because it gained M15 only six days. The same cap very probably limits M1 to about 72 trading days, at about 1,380 bars per day. Inferred from M15 returning exactly the cap; not yet tested on M1 | A 4-year delayed-entry replay is impossible without raising it. Higher timeframes are built from M1, so the M1 history exists back to 2022-06-23; only the client cap restricts it | TBD-2; that earlier decision is reopened for M1 only |
| H3 | `daily_loss_limit` and `max_open_trades` are **inert live**: alert-only never writes outcomes or holds positions. The day boundary also compares a server-clock bar date to a UTC date | Conformance must reproduce inert gates; SIMULATION must model what live does not | C2.4, D4; the live code is unchanged while alert-only |
| H4 | Probe-local session classifier is one hour off the engine's at three boundaries | Session splits in 0g-4 evidence are indicative only | C2.2 |
| H5 | An earlier note suggested MT5 per-bar spread might refine the exit-spread approximation | It contradicts 0a, where bar spread understates by 3.5x | C3.5; ROADMAP note corrected |

## 13. Freeze rule

- On approval of §1-§10, this becomes **`ec-0.2`, structure frozen**. Any
  later change to a clause is a new version, with the reason recorded.
- **`ec-1.0`** when TBD-1 to TBD-4 are resolved and the §9 tests pass, each
  shown to fail on its broken variant.
- Replay results produced under any `ec-0.x` are labelled **provisional** and
  cannot enter the P1 gate.
