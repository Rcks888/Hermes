# Hermes execution contract (0g-6)

**Version `ec-0.1r2` — DRAFT, 2026-10-13.** Structure under review. Revision 2
answers the first review (verdict HOLD); every finding and the clause that
closes it is in §14. Values marked `TBD` are deliberately unset and each has a
named resolution path (§11).

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
| C1.4 | **Decision instant** `D = bar_close + delay`. `delay` is a declared run parameter. Baseline **480 s**, the live schedule (observed live 484.978 s, the ~5 s being process overhead). Sensitivity runs at all five preregistered delays, 60 / 120 / 180 / 300 / 480 s, all reported. The live delay distribution is never sampled. CONFORMANCE also replays at the baseline delay; the live delay is only E1's comparator (§8). |
| C1.5 | No fixed R haircut is applied for delay. The "18% of nominal R" figure is retired; delay acts per trade through C1.4 and C4.1. |
| C1.6 | **Entry instant** `E = ceil_minute(D)`, the first whole minute at or after D. Bar closes fall on whole minutes, so every preregistered delay gives `E = D`; E differs only for a fractional delay. **Price at E** is the **open of the M1 bar stamped E**. Nothing before E is post-entry and nothing at or after E is pre-entry, so no minute is on both sides. A missing minute is never forward-filled: the opportunity is recorded `quote_unavailable` with detail `m1_minute_unavailable`. |
| C1.8 | **Expected minute.** A minute is expected iff it falls in scheduled trading time per the trading calendar (`TBD-5`). An expected minute absent from the M1 data is a **hole** (C5.8). Time outside the calendar is a **closure**, across which C5.4 applies. |
| C1.7 | One evaluation per closed M15 bar whose decision instant falls **Mon-Fri UTC**, mirroring cron `dow=1-5`. Each bar is evaluated once, so live signal de-duplication is inert in replay. |

## 2. Strategy and risk gates

| # | Clause |
|---|---|
| C2.1 | The replay calls the **same pipeline**, imported from `engine/`, on the **200 closed M15 bars ending at the signal bar**: `market_structure.analyze` -> `calculate_vwap` -> `volume_average` -> `calculate_atr` -> `find_sr_zones` -> `strategy.evaluate` -> `risk_manager` filters. Prerequisite: lift that sequence out of `run_hermes.main()` into one shared function used by both. No backtest-only copy. |
| C2.2 | Session = `run_hermes.get_session_tag(utc_hour(D))`, imported. The probe-local `session_of()` is **not** used; it disagrees by one hour at three boundaries (table below). |
| C2.3 | The spread filter (`max_spread_points` 50) is applied to the **spread-model draw** (§3), in points. Never to bar spread. |
| C2.4 | Two modes, never mixed in one report. **CONFORMANCE** reproduces live alert-only state: no open positions and a daily loss count of zero, because live never records outcomes (`trades.json` `outcome: null`) and never holds positions (hazard H3). Used for §8. **SIMULATION** enforces `max_open_trades` 1 from simulated open trades and `daily_loss_limit` 3 losses per **server-clock date**. Performance metrics come from SIMULATION only. |
| C2.5 | **Event ordering in SIMULATION.** A trade's exit time `X` is the close (`open + 60`) of the M1 bar it resolves in; for a gap exit, the close of the gap bar. At a new setup's entry instant E: (a) a trade **frees the slot** iff `X <= E`; (b) the daily loss count is the number of losses with `X <= E` whose `X` falls on the **server date of E**. A loss belongs to its **exit** date, the day it is realised. A loss is `SL_HIT`, or `PATH_UNKNOWN` (C5.8), which is counted conservatively and frees the slot at the close of the M15 bar where the path became unknown. Exits are therefore always applied before a decision at the same instant. Live `_daily_loss_count` keys on the signal bar date instead; that deviation is recorded and stays moot while the live gate is inert (H3). |

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
| C3.3 | **Draw**, fully specified: (1) `bar_close_epoch` = integer seconds of the signal M15 bar's close **as stamped by the server**, i.e. bar open + 900, no offset applied. (2) `key` = ASCII `hermes-ec-spread-v1|<bar_close_epoch>`. (3) `k` = first 8 bytes of SHA-256(`key`) read as an unsigned big-endian integer; `u = (k + 0.5) / 2^64`, so `0 < u < 1`. (4) The artefact holds, per session, the sorted observed spreads in **integer points** `x(1) <= ... <= x(n)`; the draw is `x(ceil(u * n))`, the inverse empirical CDF with no interpolation (Hyndman-Fan type 1). (5) The session is that of E. (6) `spread_price = points * point`, with `point` 0.01 from the broker spec; `ask = round(bid + spread_price, digits)`. The filter (C2.3) compares integer points. `u` depends only on the bar, so it is **identical across delay runs** (common random numbers); a delay that moves E across a session boundary changes the distribution, not `u`, and is recorded. Sensitivity: constant session p50 and p90, by the same type-1 quantile. Golden vectors: §9 test 6. |
| C3.4 | Direction accounting is `entry_observation.spread_accounting`, unchanged: LONG fills at `bid + spread` and triggers on bid at the levels; SHORT fills at bid and triggers when bid reaches `level - spread`. Spread is charged **once**, on the leg that transacts at the ask. |
| C3.5 | Exit spread equals the trade's entry-time draw. Declared approximation. Bar spread cannot refine it, because of the 3.5x understatement; this corrects an earlier note suggesting otherwise. |
| C3.6 | Commission: none modelled. The live account's commission must be verified before Phase 4. |
| C3.7 | Swap: not modelled in baseline. Each trade records its rollover crossings, at the rollover instant from `TBD-5`, with the broker's triple-swap weekday counted three times. Beside the headline, a **current-rate swap estimate** is reported: per trade, `sum(crossing weight) * swap rate per lot in account currency (converted per the snapshot's swap_mode) * volume / planned stop-loss cash`, in R, from the versioned broker-spec snapshot. It applies today's rates to every year, is labelled as such, and is **not** a historical bound. |
| C3.8 | Entry slippage beyond the quote: none. Exit slippage: only the gap rule C5.4. |

## 4. Entry and sizing (0g-5, unchanged)

| # | Clause |
|---|---|
| C4.1 | Build the quote at E: `bid` per C1.6, `ask` per C3.3. Pass it to **`entry_observation.propose_execution`**, the same function live logs from, with the margin calculator of C4.4 in the `mt5` argument. No reimplementation. |
| C4.2 | Original SL and TP fixed. Volume from executable entry to original SL, rounded **down** to the broker step, within broker min and max, taken from a versioned broker-spec snapshot (`contract_size` 100, `volume_min` 0.01, `volume_step` 0.01). |
| C4.3 | Balance is a fixed **$100,000, non-compounding**. **R is the unit of account**; cash is secondary and equals R times planned stop-loss cash. |
| C4.4 | **Margin is modelled and checked.** Required margin = `volume * contract_size * entry * margin_rate`, with `margin_rate` from the broker-spec snapshot, supplied to `propose_execution` as an `order_calc_margin` stand-in. A rate of 1% (1:100) reproduces the one live reading, 1,642.26 for 0.40 lots at 4105.66, to the cent (§8 E0). After `propose_execution`, the replay applies `required_margin <= balance - margin of open simulated trades`; failure is status **`margin_insufficient`**, a versioned addition to the status set, recorded in population B. Live never makes this comparison; it only logs the requirement. `margin_not_checked` cannot occur in replay; if it does, the run is invalid. |
| C4.5 | Every non-executable status is recorded in population B and excluded from C. It is never scored as a loss and never as a strategy rejection. |

## 5. Exit resolution

| # | Clause |
|---|---|
| C5.1 | Resolve on **M1 bid bars** from the entry minute E onward, against the trigger levels of C3.4. Entry is at E's open, so E's high and low are post-entry. |
| C5.2 | Pre-entry window `[bar_close, E)`: touches are recorded, never outcomes. A level already crossed at E is caught by `propose_execution` as execution-invalid. |
| C5.3 | SL and TP both inside one M1 bar resolves to **SL, flagged AMBIGUOUS**. Ambiguity count and an optimistic-ordering sensitivity are reported separately, never folded into the headline. |
| C5.4 | **Gaps.** Applies between consecutive observed M1 bars with no hole between them (C1.8), including across a closure. A bar opening beyond the SL trigger exits at that open, so a loss can exceed 1R. A bar opening beyond the TP trigger exits at the TP level; no positive slippage is credited. |
| C5.5 | No time stop and no management (V1 is fixed SL/TP). Trades may span sessions, days and weekends. |
| C5.6 | **Data end.** A trade unresolved at the last M1 bar of the snapshot is `OPEN`, marked to market at that bar's close on its closing leg (C5.7), giving `mtm_R`. **Closed-trade statistics** (expectancy, win rate, profit factor, R distribution) use resolved trades only. **Portfolio statistics** (total R, equity curve, maximum drawdown) include `OPEN` at `mtm_R`. Both are reported, with the `OPEN` count; with `max_open_trades` 1 there is at most one. |
| C5.8 | **Holes after entry.** For each M15 bar during the trade that contains a hole: if the trade is not already resolved by an observed minute **before** the bar's first hole, and the M15 bid range reaches either trigger, the outcome is **`PATH_UNKNOWN`**, flag `m1_hole`, and resolution stops. If the M15 range reaches neither trigger, nothing in that bar touched one, and resolution continues with the hole counted. A whole missing M15 bar in scheduled time leaves no range, so an unresolved trade becomes `PATH_UNKNOWN`. `PATH_UNKNOWN` is excluded from closed-trade statistics and reported with two bounds: **adverse**, SL with the gap rule applied at the first observed open after the hole; **favourable**, TP. Portfolio statistics use the adverse bound. |
| C5.7 | **Realised R** = `(exit - fill) / (fill - SL)` for LONG and `(fill - exit) / (SL - fill)` for SHORT, where `exit` is the price on the closing leg: bid for LONG, `bid + spread` for SHORT. A clean stop is exactly -1R in both directions. |

## 6. Data dependencies

| # | Clause |
|---|---|
| C6.1 | M15: 2022-06-23 to present, about 100,000 bars, exported Wine-side to CSV, snapshot-hashed. |
| C6.2 | **M1 over the same span is required** by C1.6 and §5. Whether the server holds M1 that far back is **unknown**, and `MaxBars=100000` very probably caps the client at about 72 trading days in any case (hazard H2). Resolution `TBD-2`. If M1 is available for a shorter span only, the replay span is the M1 span and the P1 sample shrinks accordingly; that is reported, never padded. **No silent M15 fallback**: any run without M1 is labelled `M15_APPROX` and excluded from the P1 gate. Missing M1 inside an otherwise covered span is handled by C1.6 at entry and C5.8 after it. |
| C6.3 | Control: M15 OHLC rebuilt from M1 must equal the exported M15 bars over the full span. Mismatches are listed, not tolerated. |

## 7. Populations and per-opportunity record

Populations per ROADMAP: **A** strategy signal, **B** risk-eligible, **C**
simulated execution. Performance metrics use C only.

Each opportunity in B writes one record: identity (bar open and close, server;
D and E in server and UTC; offset applied), mode, delay, session, spread draw
`u` and points, quote bid and ask, `propose_execution` output in full, margin
check, trigger levels, pre-entry touches, outcome, exit time X and price,
AMBIGUOUS, gap and `m1_hole` flags, hole count, realised R (or `mtm_R`, or both
`PATH_UNKNOWN` bounds), cash, rollover crossings, and M1 availability.

Records conform to the 0d schema in `engine/research_schema.py`. Population C
rows carry `outcome_type` **`ACTUAL_SIMULATED`** and an exit reason from
`EXIT_REASONS`; V1 uses only `TP_HIT`, `SL_HIT`, `PATH_UNKNOWN` and `OPEN`, with
ambiguity, gap fills and holes as flags, not new reasons. Rejected setups tracked for research are
`COUNTERFACTUAL_DIAGNOSTIC` and never enter a performance metric. The fields
above that 0d lacks are added as a versioned schema change, not ad hoc keys.

## 8. Conformance

Levels 1-4 as defined in ROADMAP, run in CONFORMANCE mode. 0g-6 adds
**Level E, execution parity**, against the 0g-1 fields of every live cycle. It
separates two kinds of check that must not be confused: **function parity** on
identical inputs, which is exact, and **model agreement** on proxy inputs,
which is not, because the replay's bid is an M1 open and its spread is a draw.

| # | Kind | Check | Pass rule |
|---|---|---|---|
| E0 | function parity | The live logged quote, logged signal and the balance are passed to `propose_execution` with the C4.4 margin calculator | **every** logged execution field equal, including status and `required_margin`. First point (7 Oct): all fields equal |
| E1 | model agreement | Decision instant: replay E at baseline delay vs live `bar_close + scheduling_delay_seconds` | `abs(diff) <= 60 s` |
| E2 | model agreement | Price: replay bid at E vs live `quote_bid` | difference distribution reported; tolerance `TBD-4`, frozen before any replay result. First point: 0.18 |
| E3 | model agreement | `execution_validity_status` on signal cycles, replay inputs vs live | agreement rate reported. Each disagreement is **attributed** by re-running with one input swapped at a time: live bid with model spread, then proxy bid with live spread. A disagreement that one swap removes is explained by that input |
| E4 | model agreement | Spread: model distribution vs live decision-time `quote_spread_price` by session | distributional only, because the draw is synthetic |

**REPLAY NOT CONFORMANT, historical performance reporting prohibited**, on any
of: a Level 1-4 mismatch; any E0 field mismatch; an E1 or E2 breach (E2 once
`TBD-4` is frozen); an E3 disagreement that no single-input swap explains.

## 9. Contract tests (preregistered)

Each test is written before the engine and must **fail on a deliberately broken
variant**, demonstrated once, as 0g-3 did.

1. Decision instant equals bar close plus delay; the M1 open at E is read, never the close (boundary values 10 / 20 / 30).
2. A missing entry minute gives `quote_unavailable`, never a fabricated price.
3. VWAP reset parity: the same bars converted to UTC give a different VWAP, and the replay matches live.
4. Session parity: the replay's session equals `get_session_tag` at boundary minutes 06:59 / 07:00 / 11:59 / 12:00 / 20:59 / 21:00 UTC.
5. The offset rule maps a known pre-DST and a known post-DST bar correctly (after `TBD-1`).
6. Spread draw is deterministic and identical across delay runs for the same bar, and reproduces the golden vectors: `bar_close_epoch` 1790935200 (2 Oct) gives `u` = 0.032532492723; 1791395100 (7 Oct) gives `u` = 0.127935830013; with n = 1000 these select ranks 33 and 128.
7. LONG and SHORT accounting reproduce 1.856R (2 Oct) and the SHORT trigger levels 4130.19 / 4045.48 (7 Oct).
8. A SHORT stop is crossed on the ask in `propose_execution`.
9. A same-minute SL and TP gives SL plus AMBIGUOUS.
10. A gap through the stop exits at the bar open with R < -1; a gap through the target exits at TP.
11. In CONFORMANCE mode, a second signal while a simulated trade is open is not blocked; in SIMULATION mode it is blocked `Max open trades reached`.
12. Strategy parity: replaying both live signals in CONFORMANCE mode reproduces their logged `entry`, `sl`, `tp`, `blocked_at` and `candle_hash`. Here `entry` is the **signal-close price** the strategy emits, not the executable fill, which depends on replay inputs and is covered by E1-E3, not by this test.
13. M15 rebuilt from M1 matches the M15 export (C6.3).
14. Function parity E0: the 7 Oct logged quote reproduces every logged execution field, including `required_margin` 1642.26. (The 2 Oct signal predates 0g-1 and has no fields to compare.)
15. A fractional delay (484.978 s) gives E at the next whole minute; the pre-entry window ends at E and no minute is both pre- and post-entry.
16. A hole inside a trade: harmless when the M15 range reaches neither trigger, `PATH_UNKNOWN` with both bounds when it does, and `PATH_UNKNOWN` for a whole missing M15 bar.
17. A constructed stop tight enough that `required_margin` exceeds the balance gives `margin_insufficient`, recorded in B, never C.
18. A trade unresolved at data end is `OPEN` with `mtm_R`, excluded from closed-trade statistics and included in drawdown.
19. Ordering: an exit with `X = E` frees the slot for the decision at E, and a loss is counted on the server date of X, not of entry.

## 10. Decisions for review

| # | Decision | Recommendation | Alternative rejected, and why |
|---|---|---|---|
| D1 | Delay model | Fixed 480 s baseline, sensitivity at 60-480 s; entry instant `E = ceil_minute(D)` (C1.6) | Sampling the live distribution extrapolates a few hundred observations over about 100,000 bars and ties the replay to a schedule that is still open. `floor_minute(D)` reads a price before D |
| D2 | Spread draw | Deterministic SHA-256 draw, type-1 inverse ECDF in integer points, fully specified in C3.3; p50 and p90 as sensitivity | A constant p50 never trips the 50-point filter in LONDON or NY (p50 28 / 30), so it over-counts eligible trades, the exact error 0a rejected bar spread for |
| D3 | Gap fills | Stop at gap open; target at level; only between observed bars with no hole (C5.4) | Filling stops at the level understates weekend risk; crediting target gaps is optimistic |
| D4 | SIMULATION day boundary | Server-clock date; a loss belongs to its exit date; exits before decisions at the same instant (C2.5) | UTC date; the live code compares a server-clock bar date to a UTC date, so neither matches it, and server date is consistent with VWAP |
| D5 | Swap | Not modelled; rollover count and a current-rate estimate reported, labelled as not a bound (C3.7) | Modelling it now needs per-date swap history the broker does not supply |
| D6 | Capital | Fixed $100,000, non-compounding, R as unit; margin modelled and checked (C4.4); `OPEN` marked to market in portfolio statistics (C5.6) | Compounding makes results path-dependent and hides per-trade expectancy |
| D7 | Missing M1 | No silent fallback; `M15_APPROX` label, excluded from P1; holes after entry give `PATH_UNKNOWN` with bounds (C5.8) | Silently approximating entry on M15 would reintroduce exactly what 0g exists to prevent |

## 11. Open values

| # | Value | Resolution | When |
|---|---|---|---|
| TBD-1 | Server offset rule across DST | Live `server_offset_seconds` is logged every cycle; watch it across 25 Oct (EU) and 1 Nov (US). Then confirm historically from week-open bar timestamps | by 6 Nov |
| TBD-2 | M1 coverage of the replay span | Wine-side test request for an M1 range in 2023. If capped, the user raises `MaxBars` manually (terminal stopped, outside cron, UTF-16 credential-bearing file) or history is read Wine-side | with the CSV export, week of 19 Oct |
| TBD-3 | Spread-model source and n | Prefer 0g-1 decision-time spreads once each tradeable session has enough cycles; otherwise the 1,206 soak measurements. Frozen with a hash | before the first replay result |
| TBD-4 | E2 price tolerance | From accumulated live-vs-proxy differences | before the first replay result |
| TBD-5 | Trading calendar and rollover instant (C1.8, C3.7) | Broker session schedule if the terminal exposes it; otherwise inferred from the M15 export, every inferred closure listed for review. Triple-swap weekday and swap rates from the broker-spec snapshot | with the CSV export, week of 19 Oct |

## 12. Hazards found while drafting

| # | Hazard | Consequence | Action |
|---|---|---|---|
| H1 | Server offset assumed fixed at UTC+3. MetaQuotes servers commonly move to UTC+2 in northern winter; the EU change is 25 Oct 2026 | Replay sessions and cron coverage shift by an hour across half of every year. Live is unaffected because it measures the offset each cycle | TBD-1; weekly checkpoint watches the offset |
| H2 | `MaxBars` 100,000 was judged not worth raising because it gained M15 only six days. The same cap very probably limits M1 to about 72 trading days, at about 1,380 bars per day. Inferred from M15 returning exactly the cap; not yet tested on M1 | A 4-year delayed-entry replay is impossible without raising it. Whether the **server** holds M1 back to 2022-06-23 is a separate, untested question: M15 depth does not establish M1 depth. The available span may be shorter even with the cap raised | TBD-2; that earlier decision is reopened for M1 only |
| H3 | `daily_loss_limit` and `max_open_trades` are **inert live**: alert-only never writes outcomes or holds positions. The day boundary also compares a server-clock bar date to a UTC date | Conformance must reproduce inert gates; SIMULATION must model what live does not | C2.4, D4; the live code is unchanged while alert-only |
| H4 | Probe-local session classifier is one hour off the engine's at three boundaries | Session splits in 0g-4 evidence are indicative only | C2.2 |
| H5 | An earlier note suggested MT5 per-bar spread might refine the exit-spread approximation | It contradicts 0a, where bar spread understates by 3.5x | C3.5; ROADMAP note corrected |

## 13. Freeze rule

- **`ec-0.2`, structure frozen**, when a review returns no HOLD on D1-D7, F or
  any §14 finding. Any later change to a clause is a new version, with the
  reason recorded.
- **`ec-1.0`** requires all of: TBD-1 to TBD-5 resolved and frozen; every §9
  test passing and shown once to fail on its broken variant; E0 passing on
  every live signal cycle to date; the C6.3 control run on the replay span
  with its mismatch list recorded; and the TBD-3 spread artefact hashed.
- Replay results produced under any `ec-0.x` are labelled **provisional** and
  cannot enter the P1 gate.

## 14. Revision history

**r2, 2026-10-13**, answering review 1 (HOLD). Each finding, whether it was
accepted, and where it is closed:

| # | Finding | Response | Closed in |
|---|---|---|---|
| R1 | E3 demanded exact status equality on proxy inputs | Accepted. Level E split into exact function parity (E0) and model agreement with single-input attribution (E3) | §8, test 14 |
| R2 | `floor_minute(D)` makes part of a minute both pre- and post-entry for fractional D | Accepted. Entry instant `E = ceil_minute(D)`; equal to D at every preregistered delay; CONFORMANCE uses the baseline delay | C1.4, C1.6, C5.1, C5.2, test 15 |
| R3 | No rule for M1 missing during a trade | Accepted. Expected-minute calendar; M15-range test; `PATH_UNKNOWN` with adverse and favourable bounds | C1.8, C5.8, TBD-5, test 16 |
| R4 | `OPEN` and portfolio accounting underdefined; unchecked margin counted executable | Accepted. Mark-to-market at data end in portfolio statistics only; margin modelled, checked, `margin_insufficient` | C5.6, C4.4, tests 17-18 |
| R5 | H2 asserted M1 history exists without evidence | Accepted. Claim withdrawn; server depth stated unknown; shorter span handled | H2, C6.2 |
| R6 | Test 12 ambiguous between signal price and fill | Accepted. Test 12 is strategy parity on signal close; fill parity is test 14 | §9 |
| R7 | D2 hash, epoch, quantile and units unfixed | Accepted. Fully specified, with golden vectors | C3.3, test 6 |
| R8 | D4 loss date and same-instant ordering unspecified | Accepted. Exit date; exits before decisions; exit time is the M1 bar close | C2.5, test 19 |
| R9 | D5 "bound" undefined | Accepted. Renamed a current-rate estimate, formula given, explicitly not a bound | C3.7 |
| R10 | Freeze rule insufficient | Accepted. ec-0.2 needs no HOLD; ec-1.0 lists every gate | §13 |

Not verifiable from the two reviewed files, and not claimed to be: the bundle's
rerunnable evidence. The new E0 claim (all fields equal, margin to the cent) is
in the bundle's `results/`.
