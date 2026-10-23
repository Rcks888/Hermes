# Hermes 0b preregistration: P1 success criteria

**Version `pr-0.1` — DRAFT, 2026-10-23.** Nothing here is frozen. `pr-1.0` is
frozen and git-tagged **before any SIMULATION result exists on any historical
span** (§9). Decisions needing sign-off are in §8.

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
| F4 | **Each fold is one continuous SIMULATION starting flat:** no open trade, daily loss count zero. A trade still open at a fold's last bar is `OPEN` with `mtm_R` at that bar (C5.6). **Development trades are never resolved with holdout bars.** |
| F5 | **Development segments** for the stability gate: calendar half-years (Jan-Jun, Jul-Dec). The last is 2025-01-01 to 2025-08-31. A first segment shorter than 3 months merges into the next. On the full span that gives six: H2 2022, H1 2023, H2 2023, H1 2024, H2 2024, Jan-Aug 2025. |
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
| G2 | Expectancy, uncertainty | **One-sided lower confidence bound of mean R > 0** at level `alpha_k` (§6), by the bootstrap of §5 | The only gate that separates edge from noise |
| G3 | Profit factor | **>= 1.30**, point estimate | `HERMES_PROPOSAL.md` gate 2. At realised RR about 1.9 this implies a win rate of about 41% |
| G4 | Maximum drawdown | **<= 20 R** (20% at 1% risk) on the C5.10 equity curve, scenario S | Proposal gate 6 was 10% over a few weeks of demo. Over 100-250 trades drawdown grows with the sample; see the table below and §8 PD5 |
| G5 | Stability | Total R **> 0 in at least two thirds of segments**, rounded up (4 of 6), **and** no segment holds **more than 50%** of total R | The proposal's "monthly consistency", at a resolution where each unit holds enough trades to mean something |
| G6 | Random-entry benchmark | Mean R **above the 95th percentile** of 1,000 matched random portfolios (§4) | Separates entry value from exit structure and market drift. ROADMAP: "the decisive one" |
| G7 | Delay robustness | Mean R point estimate **> 0 at every registered delay**: 60, 120, 180, 300 s | The 480 s baseline is the live schedule; an edge that exists only at one delay is fragile |
| G8 | Cost robustness | Mean R point estimate **> 0 with constant session p90 spread** | TIER 3 draws come from about 6 weeks of 2026 spreads applied to 4 years (§10 L2) |
| G9 | Scenario agreement | Contract C5.9: `PATH_UNKNOWN` <= 1% of population C in each of S, T, C, and the same verdict on G1-G8 in all three. Otherwise **HOLD** | Inherited, not new |

Preconditions, any failing gives **HOLD**: `ec-1.0` frozen; REPLAY CONFORMANT
(contract §8); both C6.3 checks run on the span; full run identity recorded.

**What these thresholds mean in practice.** R per trade has a standard deviation
of about 1.43 at a 2R target with realised RR 1.9. With 150 trades, G2 at
`alpha_0` 0.025 needs mean R above about 0.23, a win rate of about 42%. **A 40%
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
| B2 | **Matched random entry.** For each strategy trade j (entry month m, session s, direction d, stop multiple r = `sl_atr_ratio`), a counterpart takes a uniformly random M15 decision instant in month m and session s, Mon-Fri, with usable data; the **same direction d**; stop r × ATR(14) at that bar; TP 2R; entry, costs and exits by `ec-1.0` through the same code path. Counterparts are resolved independently, with no slot rule. Portfolio i draws with SHA-256 of `hermes-pr-random-v1\|<i>\|<j>`, as in C3.3. 1,000 portfolios | **Gating (G6).** Matching direction controls for the bull-market drift that a random direction would hand the strategy for free |
| B3 | **Simple momentum**: 20-bar Donchian close breakout in LONDON and NY, breakout direction, stop 1.5 × ATR(14), TP 2R, slot rule, same contract | Reported only |
| B4 | **Cost-free diagnostic**: primary configuration with spread 0 | Reported only, labelled **non-deployable** |

## 5. Uncertainty rule

| # | Clause |
|---|---|
| U1 | **Cluster bootstrap over calendar months** of E. Months in the fold with no trade are included and contribute none. Resample months with replacement, as many as the fold has; statistic = total R / total trades of the resample. Months, not trades, are resampled because outcomes cluster by regime. |
| U2 | 10,000 resamples, `numpy.random.default_rng(20261023)` (PCG64), resamples drawn in one call in month order. Lower bound = `numpy.quantile(stats, alpha_k, method="inverted_cdf")`. A resample with zero trades is dropped and counted; more than 1% dropped gives HOLD. |
| U3 | Golden vector: a fixed synthetic R series, committed with the implementation, reproduces a recorded lower bound to 1e-12 (§9 test 3). |

## 6. Attempts and multiple testing

| # | Clause |
|---|---|
| M1 | **Attempt 0 is the frozen baseline.** At most **N = 3** further attempts, each a **parameter change** as classified in ROADMAP (diagnose the rule system). Implementation defects and research-infrastructure changes do not consume an attempt, but every prior attempt is rerun under them for comparability. |
| M2 | **`alpha_k` = 0.025, 0.0125, 0.00625, 0.003125** for attempts 0-3, so the family-wise rate over all four is below 0.05. |
| M3 | Every attempt is **committed in full before its run**: the change, its rationale from the diagnosis, and its new `config_hash`. Running a variant before its commit consumes the attempt and voids its result. |
| M4 | Every attempt is evaluated on the **development span only**, against all of G1-G9. Each gets its own report; none is discarded. |
| M5 | Only the **first attempt to pass all development gates** proceeds to the holdout. After attempt N fails: **STOP. Strategy not validated; Hermes remains research-only.** |
| M6 | **Reported, never gating, never selected from:** the London-NY overlap-only session policy (D in 12:00-16:00 UTC), results by `structure_regime`, `volatility_regime` and session, win rate, mean realised R of TP exits (the proposal's RR >= 1.8, as a diagnostic), optimistic-ambiguity ordering, intraminute-adverse drawdown, the swap estimate (C3.7). Adopting the overlap policy later is a parameter change and consumes an attempt. |

## 7. Holdout

| # | Clause |
|---|---|
| H1 | **Unsealed once**, only after: `pr-1.0` and `ec-1.0` frozen; a development PASS committed with full identity and report; and the user's explicit written go-ahead, recorded in the unseal log. The engine refuses holdout timestamps unless given the commit of that development verdict, and refuses a second unseal (§9 test 1). |
| H2 | One run: the passing attempt's configuration, unchanged, primary settings only. Sensitivities are reported beside it, not gated. |
| H3 | **PASS** requires all of: **>= 30 resolved trades**; **total R > 0**; **maximum drawdown <= 20 R**; G9 scenario agreement. Fewer than 30 trades is **UNDECIDED**. |
| H4 | **Holdout FAIL or UNDECIDED: P1 is not passed and the holdout is spent.** No attempt may be evaluated on it again. A further confirmation needs new unseen data, meaning a forward demo sample, with its own registration written before it starts. |

Holdout gates are weaker than development gates by design: about 12 months
yields perhaps 40-60 trades, too few for a confidence bound. Its job is to
refute, not to prove.

## 8. Decisions for review

| # | Decision | Recommendation | Alternative rejected, and why |
|---|---|---|---|
| PD1 | Holdout | Fixed dates, 2025-09-01 to 2026-09-11, the year before live | A relative "final 25% of the span" lets the M1 span move the holdout after data are seen |
| PD2 | Minimums | Development >= 24 months and >= 100 trades; holdout >= 30 trades; below them HOLD or UNDECIDED, never FAIL | Calling a thin sample FAIL would invite a "rerun with more data", which is a forking path |
| PD3 | Uncertainty | Month-cluster bootstrap, one-sided, `alpha_k` per M2 | Trade-level bootstrap ignores regime clustering and overstates precision |
| PD4 | Profit factor | 1.30 point estimate, from the proposal | A PF bound duplicates G2 |
| PD5 | Drawdown | 20 R on the equity curve, scenario S. **This is your risk appetite**, not a statistic: it is the deepest peak-to-trough you would sit through at 1% risk | 10 R, the proposal's demo gate, is exceeded by a 43% system over 150 trades about half the time, and 15 R about 14%; either would mostly test luck |
| PD6 | Stability | Two thirds of half-year segments positive, plus a 50% concentration cap | Monthly: about 4 trades a month makes the sign noise |
| PD7 | Benchmark | Direction-matched random entry, 95th percentile, gating | Random direction: in a bull run any long-heavy system beats it without timing skill |
| PD8 | Sensitivity gates | Point estimate > 0 at every delay and at p90 spread; scenario agreement per C5.9 | Requiring G2 at every sensitivity multiplies the tests without a correction |
| PD9 | Attempts | N = 3, `alpha` halving from 0.025, each committed before it runs. Cost: the baseline's G2 power drops from about 0.5 to about 0.37 at a 41% win rate and 150 trades | Unlimited attempts with a fixed `alpha` guarantees a false pass eventually; `alpha` 0.05 on the baseline alone leaves no budget for attempts |
| PD10 | Holdout use | Once, after a development PASS, user go-ahead logged; failure spends it | Peeking at the holdout to pick between attempts turns it into a second development set |
| PD11 | Session policy | Primary = live configuration, LONDON and NY. Overlap-only is reported (M6), and adopting it consumes attempt 1 | ROADMAP calls overlap the "primary candidate from soak". Making it primary changes the strategy from what live runs, so conformance (contract §8) would no longer cover it; registering both as attempt 0 halves `alpha_0` to 0.0125 for each. The soak evidence is about spreads, which TIER 3 already prices |

## 9. Freeze rule and preregistered tests

- **`pr-1.0` is frozen and tagged before the first SIMULATION result on any
  historical span.** Conformance runs on the live period (contract §8) and
  engine tests on synthetic data may precede it.
- A change after `pr-1.0` is a new version with its reason recorded. A change
  made after any development result has been seen **consumes an attempt** if it
  touches a gate, threshold or fold.
- `pr-1.0` also requires the tests below, written with the engine and each shown
  once to fail on a deliberately broken variant, as for the contract.

1. **Seal:** the engine refuses any holdout timestamp without the development verdict commit, logs a valid unseal, and refuses a second one.
2. **Fold boundary:** a development trade open at the last development bar is `OPEN` with `mtm_R` at that bar and is never resolved with a holdout bar; the holdout run starts flat.
3. **Bootstrap:** the golden vector of U3 is reproduced; months with no trade are included.
4. **Gate evaluator:** constructed inputs give HOLD at 99 trades, FAIL at a negative lower bound, FAIL at 4 of 6 positive segments with one holding 60% of total R, and PASS on a case meeting every gate.
5. **Random benchmark:** reproducible from its keys, direction-matched, and identical between two runs.
6. **Attempt ledger:** an attempt without a prior commit of its `config_hash` is rejected; `alpha_k` follows M2.

## 10. Limitations declared at registration

| # | Limitation |
|---|---|
| L1 | Regime coverage, F8. |
| L2 | TIER 3 spread comes from live measurements in Sep-Oct 2026, applied to 2022-2026 on the assumption that session spread distributions are stable. G8 tests p90; nothing tests earlier regimes. Soak spreads are instantaneous reads 4 times an hour, not time-weighted (ROADMAP defect 2). |
| L3 | Commission is not modelled (C3.6); swap is an estimate at today's rates (C3.7). |
| L4 | Development and holdout share one broker's data. P1 says nothing about another feed. |
| L5 | A P1 PASS establishes the rule-only baseline against these criteria. It does not establish exit-policy value (P3), risk control (P2) or live readiness. |
