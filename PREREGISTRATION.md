# Hermes 0b preregistration: P1 success criteria

**Version `pr-0.2` — DRAFT, 2026-10-23.** Nothing here is frozen. `pr-1.0` is
frozen and git-tagged **before any SIMULATION result exists on any historical
span** (§9). Decisions needing sign-off are in §8. Review 1 (HOLD, five findings)
and the clauses that close it are in §11.

**Scope.** What the P1 decision gate tests, on which data, with which
thresholds, and what happens on each outcome. It binds the replay engine (run
under the execution contract, `EXECUTION_CONTRACT.md`), the rule-only baseline
and the P1 verdict. It is a research protocol, **not authorisation for live
execution**; a P1 PASS opens Priority 2, nothing more.

---

## 0. What has been seen at registration

A preregistration is only as good as its record of what was already known.

| Item | Seen | Bearing |
|---|---|---|
| Strategy run over historical bars | **Never.** No replay engine exists | Development and holdout outcomes are unknown |
| Strategy parameters | Set at project init (Sep 2026, `HERMES_PROPOSAL.md`) and unchanged; `tp_rr_multiple` 2.0 was renamed, not changed (`17e05e8`). Registered identity: `config_hash` `265c3dd24d68` | No parameter was chosen on historical outcomes |
| Live alert-only period, from 14 Sep 2026 | Every cycle, the gate funnel, 3 signals and their outcomes (+1.856, -1, -1 R) | **Excluded from P1.** Conformance and forward tracking only |
| 0a data probes (30 Sep) | Bar existence, duplicates, gaps, volume over the M15 span; one sampled week per year for archive depth | Data quality only, no strategy evaluation |
| 0a spread reconciliation, 0g-4 drift studies | Soak period from 10 Sep 2026; drift windows 18 Sep - 13 Oct 2026 | Inside the excluded live period |
| Original live gates (`HERMES_PROPOSAL.md`) | PF >= 1.3, >= 100 trades, win >= 40% with realised RR >= 1.8, max DD < 10% | Thresholds below derive from these where they apply |

**Registration cutoff: 2026-09-11, the last trading day before live
operation.** Nothing after it enters P1.

## 1. Question and primary measure

| # | Clause |
|---|---|
| Q1 | **Question.** Does the frozen V1 rule system (Pullback Setup, XAU/USD M15, fixed SL, TP at 2R, sessions LONDON and NY, spread filter 50 points, 1% risk), executed per `ec-1.0` at the baseline delay, have positive expectancy after costs that is distinguishable from zero, robust across time and to the registered sensitivities, better than matched random entry, and confirmed on unseen data? |
| Q2 | **Primary configuration.** `ec-1.0`, SIMULATION mode, delay 480 s, TIER 3 hashed spread draw (C3.3), `PATH_UNKNOWN` scenario S (C5.9), fixed $100,000 non-compounding, 1% risk, `config_hash` `265c3dd24d68`. |
| Q3 | **Primary measure: expectancy in R per resolved trade**, population C, closed-trade statistics per C5.6 (`OPEN` and `PATH_UNKNOWN` excluded). Drawdown is from the C5.10 equity curve, which includes them. |
| Q4 | **Unit of sample size: resolved trades, not bars.** About 100,000 M15 bars give a few hundred trades at most. |

## 2. Data and folds

All boundaries are by the **server-clock date of the entry instant E**,
consistent with the contract's day boundary (D4).

| # | Clause |
|---|---|
| F1 | **Replay span** = M15 export ∩ M1 coverage (TBD-2), from no earlier than 2022-06-23 to 2026-09-11. Bars before a fold start may be used only as indicator warm-up (200 M15 bars, C2.1); no decision is taken in warm-up. |
| F2 | **Development span: from the span start to 2025-08-31.** **Sealed holdout: 2025-09-01 to 2026-09-11** (about 12.4 months). Both are fixed dates and do not move with the M1 span. |
| F3 | **Minimum development span 24 months.** If M1 coverage leaves less, or does not cover the whole holdout, P1 is **HOLD: undecidable on this data**. The remedy is a data-source decision, which needs a new registration. No padding with M15 (D7, `M15_APPROX` excluded). |
| F4 | **There are exactly two folds, development and holdout. Each is one continuous SIMULATION starting flat:** no open trade, daily loss count zero. A trade still open at a fold's last bar is `OPEN` with `mtm_R` at that bar (C5.6). **Development trades are never resolved with holdout bars.** Segments (F5) are not folds: they partition the development run and never restart it. |
| F5 | **Development segments** for the stability gate: calendar half-years (Jan-Jun, Jul-Dec). The last is 2025-01-01 to 2025-08-31. A first segment shorter than 3 months merges into the next. On the full span that gives six: H2 2022, H1 2023, H2 2023, H1 2024, H2 2024, Jan-Aug 2025. **Accounting:** segments are time partitions of the single development run, by server-clock instant. **Segment R** = the change in the C5.10 equity curve, scenario S, from the segment's start instant to its end instant, realised plus floating; the curve's value at an instant is its last mark at or before it. A trade crossing a boundary contributes its marked change to each side. Segment R values therefore sum exactly to the development run's final equity, its `OPEN` mark included. |
| F6 | **What may touch holdout bars before unsealing:** export, hashing and the data-quality checks of C6.3 and TBD-5 (counts, holes, envelopes, calendar). **Not** the strategy pipeline, signal counts, population sizes or any price statistic beyond those checks. |
| F7 | **Excluded period:** 14 Sep 2026 onward is the live period. It serves conformance (contract §8) and forward tracking only, and never enters P1. |
| F8 | **Coverage bound, stated with every result:** the span holds one gold bull run and one rate cycle. It excludes 2008, the 2013 crash and the 2020 COVID spike. No robustness claim reaches beyond the regimes it contains. |

## 3. Development gates (all required)

Evaluated once per attempt (§6), on the development span, primary configuration.
**Fold aggregation rule:** G1-G4 and G6-G8 are computed on the **pooled**
development span, one continuous run (F4); segments enter only through G5. No
per-segment calibration, no averaging of per-segment statistics.

| # | Gate | Threshold | Derivation |
|---|---|---|---|
| G1 | Sample | **>= 100 resolved trades** | `HERMES_PROPOSAL.md` gate 1. Fewer is **HOLD (insufficient sample)**, not FAIL |
| G2 | Expectancy, uncertainty | **One-sided lower confidence bound of mean R > 0** at the configuration's level `alpha_c` (§6 M2), by the bootstrap of §5 | The only gate that separates edge from noise |
| G3 | Profit factor | **>= 1.30**, point estimate | `HERMES_PROPOSAL.md` gate 2. At realised RR about 1.9 this implies a win rate of about 41% |
| G4 | Maximum drawdown | **<= 20 R** (20% at 1% risk) on the C5.10 equity curve, scenario S | Proposal gate 6 was 10% over a few weeks of demo. Over 100-250 trades drawdown grows with the sample; see the table below and §8 PD5 |
| G5 | Stability | **Segment R (F5) > 0 in at least two thirds of segments**, rounded up (4 of 6), **and** the development run's final equity > 0 with no segment's R above **50%** of it | The proposal's "monthly consistency", at a resolution where each unit holds enough trades to mean something |
| G6 | Random-entry benchmark | Closed-trade mean R, scenario S, **above the 95th percentile** (type 1, rank 950) of the same statistic over the 1,000 random portfolios of B2 | Separates entry value from exit structure and market drift. ROADMAP: "the decisive one" |
| G7 | Delay robustness | Mean R point estimate **> 0 at every registered delay**: 60, 120, 180, 300 s | The 480 s baseline is the live schedule; an edge that exists only at one delay is fragile |
| G8 | Cost robustness | Mean R point estimate **> 0 with constant session p90 spread** | TIER 3 draws come from about 6 weeks of 2026 spreads applied to 4 years (§10 L2) |
| G9 | Scenario agreement | Contract C5.9: `PATH_UNKNOWN` <= 1% of population C in each of S, T, C, and the same verdict on G1-G8 in all three. Otherwise **HOLD** | Inherited, not new |

Preconditions, any failing gives **HOLD**: `ec-1.0` frozen; REPLAY CONFORMANT
(contract §8); both C6.3 checks run on the span; full run identity recorded.

**What these thresholds mean in practice.** R per trade has a standard deviation
of about 1.43 at a 2R target with realised RR 1.9. With 150 trades, G2 at
the baseline level 0.025 needs mean R above about 0.23, a win rate of about 42%. **A 40%
win-rate system fails G2, and even a genuine 43% system passes it only about
half the time. That is deliberate:** at this sample size a smaller edge cannot
be told apart from zero, and a false PASS costs more than a false FAIL.
Simulated by `research/prereg_gate_power.py`, 20,000 runs of independent trades
(drawdown in R; G2 approximated by a normal bound):

| Win rate | Mean R | Trades | DD p50 | DD p95 | P(DD > 15) | P(DD > 20) | P(G2 pass, 0.025) |
|---|---|---|---|---|---|---|---|
| 41% | 0.19 | 150 | 11.2 | 21.3 | 0.22 | 0.07 | 0.37 |
| 43% | 0.25 | 100 | 8.7 | 16.5 | 0.08 | 0.02 | 0.37 |
| 43% | 0.25 | 150 | 10.1 | 18.7 | 0.14 | 0.03 | 0.56 |
| 43% | 0.25 | 250 | 11.7 | 20.7 | 0.23 | 0.06 | 0.78 |
| 45% | 0.31 | 150 | 9.1 | 16.4 | 0.08 | 0.01 | 0.74 |

Independent trades understate clustering, so real drawdowns run deeper.

## 4. Benchmarks

| # | Benchmark | Role |
|---|---|---|
| B1 | **No trade**: 0 R | Implied by G2 |
| B2 | **Random-entry portfolio**, specified in B2.1-B2.5 below: the strategy's signal generator is replaced and **everything downstream is unchanged** | **Gating (G6).** Matching direction share by month and session controls for the bull-market drift that a random direction would hand the strategy for free |
| B3 | **Simple momentum**: 20-bar Donchian close breakout in LONDON and NY, breakout direction, stop 1.5 × ATR(14), TP 2R, slot rule, same contract | Reported only |
| B4 | **Cost-free diagnostic**: primary configuration with spread 0 | Reported only, labelled **non-deployable** |

**B2, random-entry portfolio.** Each portfolio is a complete SIMULATION of the
development fold, built from the strategy run of the same attempt.

| # | Clause |
|---|---|
| B2.1 | **Matching cells.** A cell is (calendar month of the bar's server date, session of D). From the strategy run: `A_c` = population A signals in cell c; `N_c` = evaluated bars in c (C1.7); `L_c` = LONG signals in c; `R` = the sorted `sl_atr_ratio` values of all population A signals. Rate `q_c = A_c / N_c`; LONG share `p_c = L_c / A_c` (cells with `A_c = 0` emit nothing). |
| B2.2 | **Draws.** For portfolio i and an evaluated bar with server-stamped close epoch t, three uniforms are taken exactly as `u` in C3.3, with keys `hermes-pr-random-v2\|<i>\|<t>\|emit`, `...\|dir` and `...\|stop`. The bar emits a signal iff `u_emit < q_c`; it is LONG iff `u_dir < p_c`; its stop multiple is `R` at type-1 rank `ceil(u_stop * n)`. Every evaluated bar of the fold is eligible, in time order; there is no other ordering. |
| B2.3 | **Signal.** Entry = the signal bar's close; SL = entry ∓ r × ATR(14) at that bar, TP = entry ± `tp_rr_multiple` × r × ATR(14), both anchored at the signal close before delay, as the strategy's are. |
| B2.4 | **Downstream unchanged:** the same `risk_manager` filters (session, spread draw, ATR, stop-ratio bounds), SIMULATION slot and daily-loss rules (C2.4, C2.5), delay, `propose_execution`, margin, exits and statuses. An execution-invalid or `margin_insufficient` signal goes to population B and is **not replaced**; `OPEN` and `PATH_UNKNOWN` follow C5.6 and C5.9 exactly as for the strategy. Frequency is matched in expectation at population A; the realised C count of each portfolio is reported. |
| B2.5 | **Statistic:** closed-trade mean R, scenario S. A portfolio with zero resolved trades is dropped and counted; more than 1% dropped gives HOLD. |

## 5. Uncertainty rule

| # | Clause |
|---|---|
| U1 | **Cluster bootstrap over calendar months** of E. Months in the fold with no trade are included and contribute none. Resample months with replacement, as many as the fold has; statistic = total R / total trades of the resample. Months, not trades, are resampled because outcomes cluster by regime. |
| U2 | 10,000 resamples, `numpy.random.default_rng(20261023)` (PCG64), resamples drawn in one call in month order. Lower bound = `numpy.quantile(stats, alpha_k, method="inverted_cdf")`. A resample with zero trades is dropped and counted; more than 1% dropped gives HOLD. |
| U3 | Golden vector: a fixed synthetic R series, committed with the implementation, reproduces a recorded lower bound to 1e-12 (§9 test 3). |

## 6. Attempts and multiple testing

| # | Clause |
|---|---|
| M1 | **Attempt 0 is the frozen baseline.** At most **N = 3** further attempts, each **exactly one variant from the menu below**, which is fixed in this registration before any result. Variants are not combined. A change outside the menu cannot be tested under `pr-1.x` (§9). Implementation defects and research-infrastructure changes, as classified in ROADMAP, do not consume an attempt; every prior attempt is rerun under them and the rerun **supersedes** the earlier result, which is kept in the report without a verdict. |
| M2 | **G2 levels are fixed per configuration, not per attempt:** `alpha` = **0.025 for the baseline** and **0.003125 for each of the 8 menu variants**. By the union (weighted Bonferroni) bound, the probability that any configuration whose true mean R is <= 0 passes G2 is at most 0.025 + 8 × 0.003125 = **0.05, however the attempts are chosen**, because every configuration that could ever be tested and its level are fixed here. The bound is **nominal**: it inherits the bootstrap's approximate coverage (PD3), and it assumes reruns happen only under M1's documented defect or infrastructure classes, a procedural protection, not a statistical one. |
| M3 | Every attempt is **committed before its run**: the menu variant, its rationale from the diagnosis, and its new `config_hash`. Choosing which variant to try after seeing results is allowed; that choice is what M2 covers. Running a variant before its commit consumes the attempt and voids its result. |
| M4 | Every attempt is evaluated on the **development span only**, against all of G1-G9. Each gets its own report; none is discarded. |
| M5 | Only the **first attempt to pass all development gates** proceeds to the holdout. After attempt N fails: **STOP. Strategy not validated; Hermes remains research-only.** |
| M6 | **Reported, never gating, never selected from:** the overlap-only session policy (menu V1) as a baseline breakdown, results by `structure_regime`, `volatility_regime` and session, win rate, mean realised R of TP exits (the proposal's RR >= 1.8, as a diagnostic), optimistic-ambiguity ordering, intraminute-adverse drawdown, the swap estimate (C3.7). |

**Variant menu.** Each is one change to the baseline `config/strategy_params.json`;
chosen from the configuration alone, with no outcome known.

| # | Change | Baseline |
|---|---|---|
| V1 | Session policy: D in **12:00-16:00 UTC** only (London-NY overlap) | LONDON and NY, 07:00-21:00 |
| V2 | `tp_rr_multiple` **1.5** | 2.0 |
| V3 | `tp_rr_multiple` **2.5** | 2.0 |
| V4 | `sl_atr_high` **2.0** | 2.5 |
| V5 | `max_spread_points` **40** | 50 |
| V6 | `volume_threshold_pullback` **1.0** | 1.2 |
| V7 | `sr_min_touches` **3** | 2 |
| V8 | `momentum_candle_multiplier` **1.5** | 2.0 |

## 7. Holdout

| # | Clause |
|---|---|
| H1 | **Unsealed once**, only after: `pr-1.0` and `ec-1.0` frozen; a development PASS committed with full identity and report; and the user's explicit written go-ahead, recorded in the unseal log. The engine refuses holdout timestamps unless given the commit of that development verdict, and refuses a second unseal (§9 test 1). |
| H2 | **Permitted holdout executions, all in one unseal session from one engine commit:** the passing configuration, unchanged, at the primary settings, with its three C5.9 paths S, T and C; and, reported only, the sensitivities of G7 and G8, optimistic ordering, intraminute-adverse marks and B2 with 1,000 portfolios. Nothing else is ever run on holdout bars. A rerun is allowed only for an implementation defect whose failing test is committed before it; both reports are kept and the P1 record is flagged. |
| H3 | **Holdout gates**, on the primary settings: **(a) >= 30 resolved trades**, fewer is **UNDECIDED**; **(b) total R > 0**, from the equity curve's final value; **(c) maximum drawdown <= 20 R**. G1-G9 do not apply to the holdout. |
| H4 | **Holdout FAIL or UNDECIDED: P1 is not passed and the holdout is spent.** No attempt may be evaluated on it again. A further confirmation needs new unseen data, meaning a forward demo sample, with its own registration written before it starts. |
| H5 | **Holdout scenario agreement**, replacing G9 here: `PATH_UNKNOWN` <= 1% of population C in each of S, T and C, and the H3 verdict the same in all three. Otherwise **HOLD**. H3 is evaluated on S; the T and C paths serve only this check. |

Holdout gates are weaker than development gates by design: about 12 months
yields perhaps 40-60 trades, too few for a confidence bound. Its job is to
refute, not to prove.

## 8. Decisions for review

| # | Decision | Recommendation | Alternative rejected, and why |
|---|---|---|---|
| PD1 | Holdout | Fixed dates, 2025-09-01 to 2026-09-11, the year before live | A relative "final 25% of the span" lets the M1 span move the holdout after data are seen |
| PD2 | Minimums | Development >= 24 months and >= 100 trades; holdout >= 30 trades; below them HOLD or UNDECIDED, never FAIL | Calling a thin sample FAIL would invite a "rerun with more data", which is a forking path |
| PD3 | Uncertainty | Month-cluster bootstrap, one-sided, `alpha_c` per M2 | Trade-level bootstrap ignores regime clustering and overstates precision |
| PD4 | Profit factor | 1.30 point estimate, from the proposal | A PF bound duplicates G2 |
| PD5 | Drawdown | 20 R on the equity curve, scenario S. **This is your risk appetite**, not a statistic: it is the deepest peak-to-trough you would sit through at 1% risk | 10 R, the proposal's demo gate, is exceeded by a 43% system over 150 trades about half the time, and 15 R about 14%; either would mostly test luck |
| PD6 | Stability | Two thirds of half-year segments positive, plus a 50% concentration cap; segments partition one run and are scored by equity change (F5) | Monthly: about 4 trades a month makes the sign noise |
| PD7 | Benchmark | Random-entry **portfolio** under the same execution constraints (B2), matched by month and session on frequency, direction share and stop-multiple distribution; 95th percentile, gating. Invalid signals are not replaced, so the portfolio faces the same attrition as the strategy | Random direction: in a bull run any long-heavy system beats it without timing skill |
| PD8 | Sensitivity gates | Point estimate > 0 at every delay and at p90 spread; scenario agreement per C5.9 | Requiring G2 at every sensitivity multiplies the tests without a correction |
| PD9 | Attempts | N = 3 runs from a fixed menu of 8; `alpha` 0.025 for the baseline, 0.003125 per variant, union-bounded at 0.05 (M2). Cost: at 150 trades a variant needs mean R above about 0.32, a win rate of about 46%; the baseline's power is unchanged | `alpha` halving per attempt (`pr-0.1`): its sum does not bound the error when each variant is chosen after seeing earlier results on the same data (review 1). Unlimited or off-menu variants: no bound exists |
| PD10 | Holdout use | Once, after a development PASS, user go-ahead logged; the run family is fixed (H2), its gates and scenario check are its own (H3, H5); failure spends it | Peeking at the holdout to pick between attempts turns it into a second development set |
| PD11 | Session policy | Primary = live configuration, LONDON and NY. Overlap-only is reported (M6) and is menu variant V1 | ROADMAP calls overlap the "primary candidate from soak". Making it primary changes the strategy from what live runs, so conformance (contract §8) would no longer cover it; registering both as attempt 0 splits the baseline level 0.025 into 0.0125 for each. The soak evidence is about spreads, which TIER 3 already prices |
| PD12 | Variant menu | The eight single-parameter variants of §6, fixed now. **Your call**: these are the only rule changes P1 can ever test under `pr-1.x`, so anything you would want to try belongs here before the freeze | Leaving attempts open-ended (`pr-0.1`) keeps diagnosis flexible but leaves no error bound |

## 9. Freeze rule and preregistered tests

- **`pr-1.0` is frozen and tagged before the first SIMULATION result on any
  historical span.** Conformance runs on the live period (contract §8) and
  engine tests on synthetic data may precede it.
- A change after `pr-1.0` is a new version with its reason recorded. **After any
  development result has been seen, the success test is closed:** gates,
  thresholds, levels, folds, benchmarks and the variant menu cannot change
  under `pr-1.x`. A change to any of them is a new registration, `pr-2.0`; it
  ends P1 under `pr-1.x` with its current verdict, and the development span
  cannot serve as confirmatory evidence for it.
- `pr-1.0` also requires the tests below, written with the engine and each shown
  once to fail on a deliberately broken variant, as for the contract.

1. **Seal:** the engine refuses any holdout timestamp without the development verdict commit, logs a valid unseal, and refuses a second one.
2. **Fold boundary:** a development trade open at the last development bar is `OPEN` with `mtm_R` at that bar and is never resolved with a holdout bar; the holdout run starts flat.
3. **Bootstrap:** the golden vector of U3 is reproduced; months with no trade are included.
4. **Gate evaluator:** constructed inputs give HOLD at 99 trades, FAIL at a negative lower bound, FAIL at 4 of 6 positive segments with one holding 60% of final equity, and PASS on a case meeting every gate. **Segments:** a trade open across a boundary splits its marked change between the two segments, and the segment R values sum to the final equity.
5. **Random benchmark:** reproducible from its keys and identical between two runs; a cell with `A_c = 0` emits nothing; an execution-invalid signal is recorded in B and not replaced; a second signal while one is open is blocked by the slot rule.
6. **Attempt ledger:** an attempt that is not a menu variant, or lacks a prior commit of its `config_hash`, is rejected; G2 uses 0.025 for the baseline and 0.003125 for a variant.
7. **Holdout run family:** after unsealing, the engine runs only the H2 family for the committed configuration, and refuses any other configuration or setting on holdout bars.

## 10. Limitations declared at registration

| # | Limitation |
|---|---|
| L1 | Regime coverage, F8. |
| L2 | TIER 3 spread comes from live measurements in Sep-Oct 2026, applied to 2022-2026 on the assumption that session spread distributions are stable. G8 tests p90; nothing tests earlier regimes. Soak spreads are instantaneous reads 4 times an hour, not time-weighted (ROADMAP defect 2). |
| L3 | Commission is not modelled (C3.6); swap is an estimate at today's rates (C3.7). |
| L4 | Development and holdout share one broker's data. P1 says nothing about another feed. |
| L5 | A P1 PASS establishes the rule-only baseline against these criteria. It does not establish exit-policy value (P3), risk control (P2) or live readiness. |
| L6 | The holdout's positive-total-R rule refutes; it is not a significance test. Statistical protection rests on G2 under M2, and is nominal. |

## 11. Revisions

**Review 1 of `pr-0.1`: HOLD.** Approved PD1-PD5, PD8 and PD11; PD5 as a
research threshold, not a relaxation of the original live-money drawdown gate.
The power table was independently reproduced. Five findings, all accepted:

| # | Finding | `pr-0.2` |
|---|---|---|
| R1 | Alpha halving per attempt sums to 0.047, but attempts chosen after seeing earlier results on the same data are not covered by that sum; the claimed family-wise rate did not follow | Attempts restricted to a menu of 8 single-parameter variants fixed now; per-configuration levels, 0.025 baseline and 0.003125 per variant, union-bounded at 0.05 for any choice order; stated as nominal (M1-M3, menu, PD9, PD12, L6) |
| R2 | The random benchmark resolved counterparts without the slot or loss rules, and left invalid, `OPEN` and `PATH_UNKNOWN` counterparts, target anchoring and timestamp-to-hash mapping undefined | B2 is now a full SIMULATION portfolio with the strategy's generator replaced and everything downstream unchanged: matching cells, draw keys, anchoring at the signal close, no replacement of invalid signals, C5.6 and C5.9 applied (B2.1-B2.5, G6, test 5) |
| R3 | F4 restarted folds flat while §3 required one pooled run; segment attribution of boundary-crossing trades was undefined | Exactly two folds; segments partition the development run and are scored by equity-curve change, summing to final equity (F4, F5, G5, test 4) |
| R4 | H3 required G9, which compares verdicts on G1-G8 and so imported development gates into the holdout; the post-unseal run family was not fixed | H3 holds its own gates; H5 replaces G9 for the holdout; H2 fixes the permitted executions (test 7) |
| R5 | §9 allowed a post-result change to a gate, threshold or fold at the cost of an attempt, a route to change what counts as success | After any development result the success test is closed; such a change is a new registration that ends P1 under `pr-1.x` (§9) |
