# Hermes Main Research and Deployment Roadmap

**Version:** v1.0-rc1
**Status:** Pending — Priority 0a follow-up probes (spread reconciliation, position sizing)
**Parity reference commit:** `cf4e3d4` — evaluate closed bars, not the forming bar

Events recorded before `cf4e3d4` describe a materially different system and must
be excluded from conformance testing. Commit SHAs are identifiers, not ordinals:
never filter with `>=`. Use an exact SHA or an explicit allowlist of audited
versions.

---

## Where we are

```
GATE 0  infrastructure soak        ████████████████████  COMPLETE
        multi-session observation  ████████████████████  COMPLETE
        live alert-only deploy     ████████████████████  COMPLETE
P0      prerequisites              ████░░░░░░░░░░░░░░░░  IN PROGRESS
P1      replay engine + baseline   ░░░░░░░░░░░░░░░░░░░░  BLOCKED by P0
P2      independent risk control   ░░░░░░░░░░░░░░░░░░░░  BLOCKED by P1
P2b     event-risk filter          ░░░░░░░░░░░░░░░░░░░░  can run parallel to P2
P3      exit-policy validation     ░░░░░░░░░░░░░░░░░░░░  BLOCKED by P1
P4      live research dataset      ░░░░░░░░░░░░░░░░░░░░  BLOCKED by robustness gate
P5      Guardian (AI layer)        ░░░░░░░░░░░░░░░░░░░░  PROHIBITED until P3 passes
```

**Signals generated to date: ZERO.** Two weeks of live observation produced none,
traced to `copy_rates_from_pos` reading the forming bar. Fixed in `cf4e3d4`.
No strategy edge has been demonstrated. Profit factor, drawdown, exit
effectiveness and regime robustness are all unknown.

### Priority 0 detail

| Item | Description | Status |
|---|---|---|
| 0a | Historical data feasibility | **Near complete** — 4.25y, both discrepancies resolved; depth still config-capped |
| 0b | Preregister success criteria | Blocked by 0a |
| 0c | Deterministic regime classifier | Not started |
| 0d | Research dataset schema | **DONE** — 69 evaluation + 42 setup fields, validators, 11 tests |
| 0e | Forward logging expansion | **DONE** `c6bfcc7` — 21 diagnostic fields, candle and config hash, block class |
| 0f | Return-path and dead-branch catalogue | Count reconciled, artefact not written |

### Open 0a questions

| Question | State |
|---|---|
| M15 archive depth | 99,999 bars = **config cap**, not archive. Raise "Max bars in chart" and re-probe |
| Span at current cap | 2022-06-29 → 2026-09-30, **4.25 years, TIER_B** |
| Data quality | Clean: 0 dupes, 0 zero-volume, no gap beyond a 50.2h weekend |
| Bar-recorded spread usable? | **NO — RESOLVED.** 1206 paired samples: bar understates live by 3.5x median, in 100% of pairs. **TIER 3** |
| Position sizing correct? | **NO — RESOLVED & FIXED.** Terminal confirmed 10x oversize. Fixed in `495e078` |
| Athena XAU/USD ingestion | **Unverified.** Must not use the rpyc bridge — one round trip per row |

---

## Cost model — decided (0a)

**Execution data tier: TIER 3.** Broker bar-recorded spread is rejected.

Paired against 1,206 soak measurements, bar spread understates live spread by
**3.5x at the median and in 100% of pairs** — systematic, not distributional.

| Session | n | live p50 | bar p50 | ratio |
|---|---|---|---|---|
| ASIA | 437 | 42 | 10 | 4.2x |
| LONDON | 278 | 28 | 8 | 3.5x |
| NY | 447 | 30 | 8 | 3.75x |
| OFF | 44 | 51 | 13 | 3.92x |

Decisive: at `max_spread_points: 50`, the filter blocks **101 bars on live
spread and 0 on bar spread**. Replay would under-cost every trade *and*
over-count eligible opportunities.

Athena uses the measured live session distributions above, not a calibration
factor applied to bar spread. Direct measurement beats a correction coefficient.

Note: LONDON p50 is **28**, superseding the earlier 22 figure from a smaller
window. The 1,206-pair sample is authoritative.

## Minimum viable capital — hard gate before Phase 4

A consequence of `contract_size` 100 with `volume_min` 0.01: the smallest
tradeable position is 1 oz, so **min-lot risk in dollars equals the SL distance
numerically**.

```
min-lot risk = sl_distance x 100 x 0.01 = sl_distance x $1.00
balance required for 1% risk = sl_distance x 100
```

With `min_atr` 3.0, stops between 0.5x and 2.5x ATR, and observed ATR ~6.5:

| SL distance | Min-lot risk | Balance for 1% |
|---|---|---|
| 1.5 (absolute floor) | $1.50 | $150 |
| 3.3 (0.5x observed ATR) | $3.30 | $330 |
| 6.5 (1x observed ATR) | $6.50 | **$650** |
| 16.3 (2.5x observed ATR) | $16.30 | **$1,630** |

**A $100 account cannot express 1% risk on any realistic stop** and will reject
every signal with `position_size_below_minimum`. That is correct behaviour, not a
fault — expect zero executed trades and do not debug it as one.

Thresholds: **~$650** to trade typical signals, **~$1,650** to trade all valid
signals including wide stops.

Permitted responses: fund adequately, or record an explicit higher-risk decision
for small capital. Rounding up to the broker minimum is **prohibited** — that is
the defect fixed in `495e078`, and it converts a stated 1% into an actual 6.5%.

## Replay engine — Athena is not reusable (0a)

Surveyed at `/home/ricksonkang/Olympus/Athena`. **Verdict: PARTIAL_REUSE_ONLY.**

The blocker is architectural, not configurable. Athena destroys intraday
timestamps at ingestion — `engine/data_feed.py:87` calls `.normalize()`,
collapsing every bar to a midnight date, then `data_feed.py:89` drops
duplicates keeping the last. **96 M15 bars per day reduce to one.**

Day-granularity is then assumed throughout: `trading_days` as a date set
(`portfolio_sim_v6.py:250`), ageing by `.days` (315, 470), date strings as
position identity (272-273), `holding_days` (611), Sharpe annualised at
`sqrt(252)` (711).

Other couplings: yfinance-only source with no generic CSV loader; share-based
fractional sizing `shares = stake / buy` (`portfolio_sim_v6.py:358`) with no
`contract_size` or `point`; **no spread field at all** — only
`slippage_pct` plus flat commission, and no hook for a session-conditioned
spread distribution, which is exactly what TIER 3 requires. The strategy is not
pluggable: `evaluate_entry` is called inline at `portfolio_sim_v6.py:443`.
No walk-forward machinery exists.

**Decision: build a purpose-built M15 replay engine.** Estimated 3-5 days
against 1-2 weeks adapting Athena, and adaptation would mean inheriting equity
scaffolding that must be continually disabled.

Reuse these instrument-agnostic parts rather than rewriting them:

| Component | Source |
|---|---|
| `exit_policy.py` | price-source-agnostic by design |
| `max_drawdown()` | `portfolio_sim_v6.py:663` |
| `summarise()`, `yearly_table()`, `exit_reason_table()` | `portfolio_sim_v6.py:679-853` |
| `reconcile()` P&L identity check | `portfolio_sim_v6.py:639-661` |
| `indicators.py` | pure OHLCV |
| Snapshot-manifest reproducibility pattern | `snapshot_data.py` |

Athena stays untouched and keeps running Ares work. The new engine lives in
the Hermes repo.

## Known defects and hazards

| # | Item | Severity | State |
|---|---|---|---|
| 2 | Soak spread samples are 4/hour instantaneous reads, not time-weighted. Cost model inherits any intra-bar bias | Medium | Accepted limitation, documented in 0b |
| 3 | Duplicate disconnect alerts — soak and strategy call `ensure_connected()` in separate processes | Low | Flagged |
| 4 | `ram_free < 300` is a state check, re-fires every cycle during a dip | Low | Flagged |
| 5 | `alerts.telegram.alert_soak_status()` orphaned after periodic summary removal | Cosmetic | Deliberate — re-wire hook |
| 6 | `price_action.scan()` never called | Cosmetic | Deliberate — V2 candidate |
| 7 | Structure indices computed pre-indicator, used against post-indicator frame | Latent | Same length today; fragile to any reindex |
| 8 | No equity drawdown kill switch; only a 3-losing-trade daily count | **Blocks Phase 4** | P2 |
| 9 | No exit management beyond fixed SL/TP | **Blocks Phase 3** | P3 |
| 10 | News/event filter absent | Blocks Phase 2 | P2b |
| 11 | VPS IP remains in two historical commits | Informational | Not a credential |

## Resolved

| Item | Commit |
|---|---|
| Evaluated the forming bar instead of the last closed bar | `cf4e3d4` |
| `cannot_evaluate` conflated with rule violation; spread and open-position checks failed open | earlier |
| No code version on events — `cf4e3d4` created an unattributable epoch boundary | `ecc3eff` |
| VPS IP embedded in Telegram CRITICAL alert body | `ecc3eff` |
| `min_rr_ratio` structurally unreachable; renamed `tp_rr_multiple`, dead check removed | `17e05e8` |
| Sizing 10x oversized — hardcoded `tick_value` 0.1 vs terminal-verified $1.00/point | `495e078` |
| Sub-minimum lots rounded UP, silently over-risking small accounts | `495e078` |
| Swap occupancy misread as memory pressure; replaced with PSI | earlier |
| Cold-start reconnect reported as a real reconnection | earlier |

---

## Strategy taxonomy

`strategy.evaluate()` has **16 return sites carrying 15 reason codes** —
`vwap_misaligned` is emitted from two direction-specific sites (LONG below VWAP,
SHORT above VWAP) under one code.

```
STRATEGY EVALUATION
        │
        ├── CANNOT EVALUATE          (4 conditions — missing evidence)
        │     1. insufficient_bars
        │     2. vwap_unavailable
        │     3. volume_average_unavailable
        │     4. atr_unavailable
        │
        ├── RULE VIOLATION           (10 gates — condition genuinely failed)
        │     1. trend_not_confirmed        >= 2 BOS in trend dir, 50-bar window
        │     2. no_pullback
        │     3. no_candle_confirmation     momentum OR engulfing OR reaction
        │     4. vwap_misaligned
        │     5. volume_too_low             > 1.2x 20-bar average
        │     6. atr_invalid
        │     7. atr_too_low                min_atr 3.0
        │     8. sl_too_tight               0.5x ATR floor
        │     9. sl_too_wide                2.5x ATR ceiling
        │    10. sl_distance_invalid
        │
        └── OK                       (1 outcome)
              Strategy signal generated
                    │
                    ▼
        POST-SIGNAL RISK EVALUATION  (risk_manager, not strategy)
                    │
                    ├── session blocked
                    ├── spread blocked
                    ├── daily loss count reached      3 losing trades
                    ├── open position exists          max_open_trades 1
                    ├── data or connection state      fails closed
                    └── executable opportunity
```

There is **no higher-timeframe bias gate** — Hermes is M15 only. There is **no
R:R eligibility gate**; `tp_rr_multiple` places the target mechanically and never
filters. Athena must not implement either.

### Population separation — never collapse these

```
A. STRATEGY SIGNAL          all 10 gates passed
B. RISK-ELIGIBLE            session, spread, account, safety passed
C. SIMULATED EXECUTION      position, volume, margin, fill passed
```

Performance metrics use **population C**. Gate diagnostics retain A and B.

---

## The flow

```
              ORIGINAL HERMES                                          [DONE]
    Rule-based SMC Pullback strategy
    Ten rule-violation gates + four cannot_evaluate conditions
    Fixed SL, TP mechanically placed at 2R
    1% risk per trade + daily limit of 3 losing trades
    M15 only — no higher-timeframe bias exists
    Alert-only operation
            │
            ▼
    GATE 0: MT5 INFRASTRUCTURE SOAK                                    [DONE]
    Live broker-feed monitoring on VPS via Wine/rpyc
    Uptime, candle retrieval, account access, symbol spec,
    time drift and spread measured
    Result: 3 failures in 14 days, all self-recovered
            │
            ▼
    MULTI-SESSION SOAK TEST                                            [DONE]
    Asia, London, London-NY overlap and New York monitored
            │
            ▼
    SESSION RESULT                                                     [DONE]
    London-NY overlap produced the best observed conditions
    London P50 spread 22 vs Asia P50 41
            │
            ▼
    LIVE ALERT-ONLY DEPLOYMENT                                         [DONE]
            │
            ▼
    OPERATIONAL STRENGTHS CONFIRMED                                    [DONE]
    Fail-closed decision flow
    cannot_evaluate distinguished from rule violation
    blocked_at reason on every return path
    Append-only audit trail (events.jsonl)
    Code version stamped per event
    Broker, data-health and PSI memory monitoring
            │
            ▼
    IMPLEMENTATION ISSUE FOUND                                         [DONE]
    copy_rates_from_pos read position 0 — the forming bar
    Momentum and volume tests compared a half-built bar
    against averages of completed bars
    Two weeks produced zero setups.  Fixed in cf4e3d4
            │
            ▼
    LESSON
    Infrastructure stability was demonstrated,
    but strategy behaviour was never historically validated.
    A historical replay would have exposed the zero-signal
    condition in minutes rather than weeks.
            │
            ▼
    ╔═══════════════════════════════════════════════════════╗
    ║  PRIORITY 0: PREREQUISITES                      [NOW]  ║
    ║  Nothing downstream may begin until these exist        ║
    ╚═══════════════════════════════════════════════════════╝
            │
            ├──────────────► 0a. HISTORICAL DATA FEASIBILITY
            │                Earliest and latest available M15 bar
            │                Total bar count and true archive depth
            │                Missing, duplicate and zero-volume rates
            │                Server-time offset and interpretation
            │                Whether history survives terminal restart
            │                Whether Athena supports XAUUSD ingestion
            │                Whether historical bid/ask or spread exist
            │                Whether external acquisition is required
            │
            │                EXECUTION-DATA TIERS
            │                  T1  broker historical bid/ask ticks
            │                  T2  broker historical spread series
            │                  T3  mid-price bars + session-conditioned
            │                      spread anchored to live soak
            │                  T4  fixed spread — diagnostic only
            │
            │                No success threshold, fold structure,
            │                trade-count minimum or sealed holdout may
            │                be frozen until this closes.
            │
            ├──────────────► 0b. PREREGISTER SUCCESS CRITERIA
            │                Git-committed BEFORE any replay runs
            │                Minimum Profit Factor
            │                Minimum trade count
            │                Maximum acceptable drawdown
            │                Required monthly consistency
            │                Cost model and spread scenarios
            │                Benchmark set, including random-entry
            │                Confidence interval / uncertainty rule
            │                Fold aggregation rule
            │                Multiple-testing policy
            │                Maximum parameter-change attempts (N)
            │                Final holdout period, named and sealed
            │
            │                BENCHMARK SET
            │                  no-trade / cash
            │                  random entry, same session and exits
            │                  simple momentum or trend
            │                  cost-free diagnostic, marked non-deployable
            │
            │                Random entry is the decisive one: it
            │                separates entry value from exit value.
            │
            ├──────────────► 0c. DETERMINISTIC REGIME CLASSIFIER
            │                No ML, no training data, no signals needed
            │                Directional efficiency, ATR percentile, ADX
            │
            │                TWO INDEPENDENT DIMENSIONS
            │                  structure_regime  TREND/RANGE/TRANSITION/UNCERTAIN
            │                  volatility_regime HIGH/NORMAL/LOW/UNCERTAIN
            │                A single mutually exclusive label cannot
            │                express TREND + HIGH_VOL, which is a real
            │                and important state for gold.
            │
            │                Freeze before running: efficiency lookback,
            │                ADX period and threshold, ATR-percentile
            │                lookback, minimum history, tie-breaking,
            │                transition and uncertain definitions.
            │                Trailing data only, as at the decision bar.
            │
            │                CIRCULARITY SAFEGUARD
            │                The trend gate and the classifier may both
            │                read trend information. A regime label is
            │                contextual evidence, never automatic proof
            │                that the gate is right or wrong.
            │                  high rejection in TREND -> investigate
            │                    whether the gate is too strict, delayed,
            │                    or measuring a different horizon
            │                  high rejection in RANGE -> consistent with
            │                    intended filtering, not sufficient alone
            │                    to validate the gate
            │
            ├──────────────► 0d. RESEARCH DATASET SCHEMA
            │                Two linked tables, not one
            │
            │                evaluations   one row per completed-bar
            │                              evaluation, every gate value,
            │                              blocked_at, cannot_evaluate
            │                setups        one row per unique qualified or
            │                              counterfactually tracked
            │                              opportunity
            │
            │                Avoids labelling one developing pullback as
            │                many separate setups.
            │
            │                MFE / MAE DEFINITION
            │                  start  executable entry timestamp
            │                  end    first of SL, TP, time exit,
            │                         session exit, max holding horizon
            │
            │                outcome_type must be recorded:
            │                  ACTUAL_SIMULATED
            │                  COUNTERFACTUAL_DIAGNOSTIC
            │                Never mix them in a deployable Profit Factor.
            │                Rejected setups need a frozen hypothetical
            │                entry and horizon, or they carry unlimited
            │                hindsight.
            │
            ├──────────────► 0e. FORWARD LOGGING EXPANSION
            │                Every live cycle before this lands forfeits
            │                Level 3 conformance permanently. Bar data is
            │                reconstructible; decision context is not.
            │
            │                Add to events.jsonl: candle_hash, OHLC,
            │                tick_volume, bos counts as fields, pullback
            │                state, confirmation type, vwap and alignment,
            │                volume and average and ratio, atr, raw and
            │                effective SL, tp, config_hash, risk state and
            │                block reason, open-position state.
            │
            │                Unavailable fields explicit as null with a
            │                reason. Never silently omitted.
            │
            └──────────────► 0f. RETURN-PATH AND DEAD-BRANCH CATALOGUE
                             Per return: source line, class, reason code,
                             reachable or unreachable, required inputs,
                             logging fields, Athena equivalent, unit test.
                             Source return count must equal categorised
                             path count before claiming exhaustive coverage.
                             Unreachable branches catalogued separately.
            │
            ▼
    ╔═══════════════════════════════════════════════════════╗
    ║  PRIORITY 1: HISTORICAL REPLAY                         ║
    ║  Purpose-built M15 engine — Athena is not reusable      ║
    ╚═══════════════════════════════════════════════════════╝
            │
            ▼
    PRODUCTION-PARITY REQUIREMENT
    Declare the identity being reproduced, not just the code:
      strategy_sha · config_hash · data_schema_version
      replay_engine_sha · broker_spec_version
    Same code can behave differently under altered configuration.
    Same closed-bar handling (position 1, never 0)
    Same SMC structure detection and Pullback rules
    Same ten gates and four cannot_evaluate conditions, same order
    Same VWAP, volume, ATR and SL-distance checks
    Same session filter placement (post-evaluate, in risk_manager)
    No simplified backtest-only strategy copy
            │
            ▼
    ┌───────────────────────────────────────────────────────┐
    │  REPLAY CONFORMANCE CHECK                             │
    │  Validate Athena before trusting any historical result │
    │                                                       │
    │  LEVEL 1  event identity                              │
    │    symbol · bar_time · code_version · config_version  │
    │    candle values or hash · evaluation eligibility      │
    │                                                       │
    │  LEVEL 2  decision parity, event by event             │
    │    cannot_evaluate · blocked_at · direction           │
    │    entry · SL · TP · risk-state result                │
    │                                                       │
    │  LEVEL 3  numeric parity, frozen tolerances           │
    │    trend state · BOS counts · pullback state          │
    │    confirmation · vwap · volume ratio · atr           │
    │    raw and final SL distance · spread · session       │
    │    Exact equality for discrete states.                │
    │                                                       │
    │  LEVEL 4  aggregate distributions                     │
    │    blocked_at · cannot_evaluate · session · spread    │
    │                                                       │
    │  A HISTOGRAM MATCH ALONE IS INSUFFICIENT. Two wrong   │
    │  implementations can produce similar distributions    │
    │  while disagreeing on individual bars.                │
    │                                                       │
    │  Existing 749 events support Levels 1 (partial), 2    │
    │  and 4 only. Level 3 requires 0e, then forward data.  │
    │                                                       │
    │  Any unexplained mismatch:                            │
    │    REPLAY NOT CONFORMANT                              │
    │    Historical performance reporting prohibited.       │
    └───────────────────────────────────────────────────────┘
            │
            ▼
    BROKER-REALISTIC SIMULATION
    Bid and ask execution
    Spread per the tier established in 0a
    Slippage scenarios
    Minimum-lot enforcement (0.01 = 1 oz)
    Tick value and contract size verified against the terminal
    Margin validation
    Position-overlap handling (max_open_trades 1)
            │
            ▼
    INTRABAR AMBIGUITY RULE
    If tick path is unavailable and both SL and TP fall inside
    one bar: apply conservative ordering, or classify as
    path_unknown. Never silently assume TP came first.
            │
            ▼
    GATE-FUNNEL AUDIT
    Report populations A, B and C separately at every stage
            │
            ▼
    RULE-ONLY BASELINE
    Profit Factor · expectancy in R · maximum drawdown
    Win rate · trade count · MFE and MAE
    Average holding time · monthly consistency
    Performance by regime (requires 0c) · by session
            │
            ▼
    CHRONOLOGICAL VALIDATION
    For the frozen baseline:
      predefined chronological test segments
      no calibration inside segments
      report stability by segment and by regime
    For any calibrated variant:
      train -> validation -> test walk-forward
      freeze changes before each test window
      never reuse the final sealed holdout
    No random train/test shuffling, ever.
            │
            ▼
    SESSION-POLICY COMPARISON
    1. London-NY overlap only    (primary candidate from soak)
    2. Broader London and New York windows
            │
            ▼
    DECISION GATE
    Does rule-based Hermes meet the criteria preregistered in 0b,
    under the aggregation rule declared there?
            │
            ├── NO
            │    │
            │    ▼
            │  ┌─────────────────────────────────────────────────┐
            │  │  DIAGNOSE THE RULE SYSTEM                       │
            │  │                                                 │
            │  │  Classify every change before making it:        │
            │  │                                                 │
            │  │  IMPLEMENTATION DEFECT                          │
            │  │    Code did not do what was specified           │
            │  │    Requires: written expected behaviour, a       │
            │  │    failing test proving the mismatch, the fix,  │
            │  │    a passing regression test, and a statement   │
            │  │    of whether prior results are invalidated     │
            │  │    -> may rerun freely                          │
            │  │                                                 │
            │  │  PARAMETER CHANGE                               │
            │  │    The specification itself is being altered     │
            │  │    because results disappointed                  │
            │  │    -> consumes one of N preregistered attempts   │
            │  │    -> this is fitting to the test set           │
            │  │                                                 │
            │  │  RESEARCH INFRASTRUCTURE CHANGE                 │
            │  │    Better spread data, corrected tick value,     │
            │  │    improved intrabar handling, timestamp fixes   │
            │  │    -> may require rerunning all variants for     │
            │  │       comparability                             │
            │  │    -> does not consume a strategy attempt        │
            │  │    -> must be applied symmetrically             │
            │  │                                                 │
            │  │  The sealed holdout stays untouched through ALL  │
            │  │  diagnosis.                                     │
            │  │                                                 │
            │  │  Candidates: dead or unreachable gates;          │
            │  │  contradictory conditions (trend=DOWN with       │
            │  │  bos_up=2, bos_down=1); closed-bar and state     │
            │  │  handling; entry and exit assumptions.           │
            │  │                                                 │
            │  │  On attempt N+1 without a pass:                  │
            │  │    STOP. Strategy not validated.                │
            │  │    Hermes remains research-only.                │
            │  └─────────────────────────────────────────────────┘
            │    │
            │    └──────► back to REPLAY CONFORMANCE CHECK
            │
            └── YES
                 │
                 ▼
    ╔═══════════════════════════════════════════════════════╗
    ║  PRIORITY 2: INDEPENDENT RISK CONTROLLER               ║
    ║  Separate portfolio safety from strategy logic         ║
    ╚═══════════════════════════════════════════════════════╝
            │
            ├──────────────► RISK PROTECTION
            │                Daily realized-loss limit    exists: 3 losses
            │                Rolling multi-day loss monitor      missing
            │                Peak-equity drawdown warning        missing
            │                Reduced-risk state                  missing
            │                Hard strategy halt                  missing
            │                Consecutive-loss control            missing
            │                Manual reactivation requirement     missing
            │
            └──────────────► SAFETY STATES — most restrictive wins
                             CONNECTION_HALT   (highest precedence)
                             DATA_HALT
                             STRATEGY_HALT
                             DAILY_LOCK
                             CAUTION
                             NORMAL             (lowest)
            │
            ▼
    OPEN-POSITION TREATMENT
    A halt blocks new entries. Existing positions continue under
    the preregistered exit policy unless a separately defined and
    tested emergency-liquidation condition applies. A data or
    connection halt must never arbitrarily close open positions.
            │
            ▼
    ╔═══════════════════════════════════════════════════════╗
    ║  PRIORITY 2b: EVENT-RISK FILTER                        ║
    ║  Parallel to P2 — no AI dependency whatsoever          ║
    ╚═══════════════════════════════════════════════════════╝
    Deterministic calendar lookup. No model, no shadow period.
    CPI · NFP · FOMC · PCE · major scheduled US events
    Blackout window before and after each release.
    Implemented in the RISK CONTROLLER layer, not as strategy
    eligibility, so the SMC setup is still recorded for research
    while live entry is blocked. That enables analysis of valid
    setups blocked by a CPI window, their hypothetical outcome,
    observed spread expansion and slippage risk.
    This is the outstanding V1 news filter.
            │
            ▼
    ╔═══════════════════════════════════════════════════════╗
    ║  PRIORITY 3: EXIT-POLICY VALIDATION                    ║
    ║  Consumes MFE/MAE from the 0d schema                   ║
    ╚═══════════════════════════════════════════════════════╝
            │
            ▼
    TRUE PRODUCTION BASELINE
    Fixed SL, TP mechanically at tp_rr_multiple x SL distance.
    Nothing else. Maximum holding time and session-end handling
    do NOT exist in Hermes today, so they are new policy, not
    implementation gaps, and must not silently enter the baseline.
            │
            ▼
    CONTROLLED EXIT EXPERIMENTS — one modification at a time
      1. Break-even rule
      2. Time-based exit
      3. Structure-based trailing stop
      4. ATR-based trailing stop
      5. Partial profit with remaining runner
      6. S/R-derived take profit
         introduces tp_mode: sr_zone with a separately named
         minimum_available_rr, at which point a genuine RR
         eligibility gate becomes meaningful
    Each experiment consumes a preregistered attempt.
            │
            ▼
    EXECUTION STRESS TESTING
    Higher spread · slippage · missed trades · delayed entries
    Stop-execution variation · randomized trade sequence
    Minimum-lot and margin restrictions
            │
            ▼
    ROBUSTNESS GATE
    Does Hermes remain stable after realistic costs and stress?
            │
            ├── NO
            │    │
            │    ▼
            │  RETURN TO RULE AND EXIT REVIEW
            │  Hermes remains research-only
            │  Guardian activation PROHIBITED
            │
            └── YES
                 │
                 ▼
    ╔═══════════════════════════════════════════════════════╗
    ║  PRIORITY 4: LIVE RESEARCH DATASET                     ║
    ║  Schema already defined in 0d — add the live feed       ║
    ╚═══════════════════════════════════════════════════════╝
            │
            ▼
    ADD AI SUPPORT — GUARDIAN
    Do not replace the rule-based SMC engine.
    Do not use AI to generate independent direction.
    Hermes remains the setup authority.
            │
            ├──────────────► REGIME CLASSIFIER
            │                Already deterministic from 0c.
            │                ML only if 0c proves inadequate.
            │
            ├──────────────► SETUP QUALITY META-MODEL
            │                Scores only setups that passed all
            │                ten strategy gates.
            │                Target: probability TP is reached
            │                before SL, after costs.
            │
            ├──────────────► DRIFT MONITOR
            │                Zero-signal detection — would have
            │                caught cf4e3d4 on day one rather
            │                than day fourteen
            │                Signal-explosion detection
            │                Gate-frequency changes
            │                Feature-distribution shifts
            │
            └──────────────► EXECUTION MONITOR
                             Spread percentile
                             MT5 latency — the repeated 17.06s /
                             17.08s init latency is exactly this
                             class of signal
                             Candle freshness · slippage
                             Order rejection · margin constraints
            │
            ▼
    ┌───────────────────────────────────────────────────────┐
    │  MODEL-READINESS GATE                                 │
    │  Preregistered BEFORE inspecting any outcome:          │
    │    minimum independent qualified setups                │
    │    minimum positive and negative outcomes              │
    │    minimum chronological test segments                 │
    │                                                       │
    │  Sample size is QUALIFIED SETUPS, not candles. A       │
    │  hundred thousand M15 bars are not a hundred thousand  │
    │  training examples.                                    │
    │                                                       │
    │  If unmet: no supervised model is trained. Guardian    │
    │  remains deterministic and monitoring-only.            │
    └───────────────────────────────────────────────────────┘
            │
            ▼
    CONTAMINATION SEPARATION
    Strategy-development period · model-training period
    model-validation period · final model holdout
    The period used to adjust Hermes gates can never later be
    presented as untouched evidence for Guardian.
            │
            ▼
    GUARDIAN MODEL SELECTION — simplest valid benchmark first
      1. Deterministic regime baseline (from 0c)
      2. Logistic regression setup-quality baseline
      3. CatBoost or LightGBM challenger
      4. Drift and anomaly detection support
            │
            ▼
    MODEL RULE
    If the gradient-boosted challenger does not clearly outperform
    the simpler baseline under EVERY preregistered condition, the
    simpler model remains in use.
            │
            ▼
    SHADOW MODE
    Guardian records recommendations but cannot alter production
    decisions. Hermes continues acting on rules alone.
            │
            ▼
    SHADOW EVALUATION — against the rule-only baseline
    Losses potentially avoided · winners incorrectly rejected
    Profit Factor change · drawdown change · expectancy change
    Calibration by probability bucket
    Stability across walk-forward windows
    Evaluated only on data the model never trained on.
            │
            ▼
    GUARDIAN VALUE GATE
    Does Guardian add stable out-of-sample value?
            │
            ├── NO
            │    │
            │    ▼
            │  GUARDIAN CONTROL PLANE — monitoring only
            │  Data health · drift detection
            │  Execution anomaly detection
            │  Signal suppression alerts · audit support
            │
            │  THIS IS A LEGITIMATE ENDPOINT, NOT A
            │  CONSOLATION OUTCOME. A drift monitor that
            │  flags a zero-signal condition on day one
            │  instead of day fourteen would have paid for
            │  the entire Guardian effort by itself.
            │
            └── YES
                 │
                 ▼
    CONTROLLED GUARDIAN ACTIVATION
            │
            ├──────────────► ALLOWED
            │                ALLOW normal Hermes risk
            │                CAUTION and reduce exposure
            │                BLOCK an otherwise qualified setup
            │                Trigger investigation alerts
            │
            └──────────────► PROHIBITED
                             No independent BUY or SELL signals
                             No overriding failed Hermes gates
                             No risk increase above baseline
                             No automatic threshold changes
                             No autonomous retraining or deployment
                             No automatic restart after a kill switch
            │
            ▼
    FINAL ARCHITECTURE
    Rule-based SMC core
      + Athena validation
      + Deterministic regime and event filters
      + Independent risk controller
      + Hermes Guardian
            │
            ▼
    FINAL DECISION FLOW
    1. Data and broker health valid?
    2. Hermes strategy gates passed?            all ten
    3. Event-risk window clear?                 deterministic, P2b
    4. Guardian environment acceptable?         advisory unless activated
    5. Risk controller permits exposure?        state NORMAL or CAUTION
    6. Generate alert or execute approved order
    7. Journal the complete decision and outcome, code_version stamped
```

---

## Standing principles

**Absent evidence is never a pass.** A check that could not run has not
succeeded.

**Alert on transitions and rates, not states.** Two false-positive alerts —
cold-start reconnects and swap occupancy — shared this single root cause.

**Reconstructible versus perishable.** Anything derivable from market data can be
backfilled. Anything about the running process — code version, latency,
connection state, decision context — vanishes if unrecorded. This is why `0e`
has a cost clock and `0c` does not.

**Fail-closed loses opportunities; fail-open loses money.** Ares failed closed
and destroyed valid signals. Hermes failed open and would have permitted double
entry on day one of auto-execution. Know which way each system errs.

**A believed-in protection that does not exist is worse than none**, because it
changes behaviour. Two instances found: Ares `risk_rules.json`, and the Hermes
`min_rr_ratio` check that compared a value against itself.

**Do not clear swap.** `swapoff -a && swapon -a` force-faults every evicted page
back at once, manufacturing the spike it appears to diagnose, and the kernel
re-evicts the same idle pages over the following hours. Measured: 0 to 308 MB
refill in two phases. Reduce the cause instead — `vm.swappiness=10`.

**Bulk history must not cross the rpyc bridge.** Each row costs a network round
trip. Export from the terminal to CSV on the Wine side and read locally.

