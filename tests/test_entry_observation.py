"""0g-1 tests. The central requirement is that observation changes nothing."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import entry_observation as eo  # noqa: E402

SIGNAL = {"direction": "LONG", "entry": 4180.89, "sl": 4173.74, "tp": 4195.19,
          "sl_distance": 7.15, "symbol": "XAUUSD"}
SYM = {"contract_size": 100.0, "volume_min": 0.01, "volume_step": 0.01,
       "volume_max": 100.0}
PARAMS = {"risk_pct": 0.01}
BAL = 100000.0


def quote(bid, ask):
    return {"quote_bid": bid, "quote_ask": ask, "quote_error": None}


def test_signal_is_never_mutated():
    """Observation must not touch the live decision's inputs."""
    before = copy.deepcopy(SIGNAL)
    eo.propose_execution(SIGNAL, quote(4181.17, 4181.53), BAL, PARAMS, SYM)
    assert SIGNAL == before


def test_sl_and_tp_are_carried_through_unchanged():
    """Fixed TP is the control. If it moves, delay and exits are confounded."""
    r = eo.propose_execution(SIGNAL, quote(4181.17, 4181.53), BAL, PARAMS, SYM)
    assert r["original_sl"] == SIGNAL["sl"]
    assert r["original_tp"] == SIGNAL["tp"]
    assert r["baseline_policy"] == "sl_fixed_tp_fixed_volume_from_executable_entry"


def test_volume_recomputed_from_executable_entry():
    r = eo.propose_execution(SIGNAL, quote(4181.17, 4181.53), BAL, PARAMS, SYM)
    assert r["proposed_executable_entry"] == 4181.53
    assert r["executable_sl_distance"] == 7.79
    # 1000 / (7.79 * 100) = 1.2837 -> rounds DOWN to 1.28
    assert r["proposed_volume"] == 1.28
    assert r["planned_stop_loss_cash"] <= 1000.0


def test_rr_degrades_rather_than_risk_widening():
    """A delayed entry must cost reward, not silently increase risk."""
    r = eo.propose_execution(SIGNAL, quote(4181.17, 4181.53), BAL, PARAMS, SYM)
    assert r["rr_at_executable_entry"] < r["rr_at_signal_close"]
    assert r["planned_stop_loss_pct_of_balance"] <= 1.0


def test_adverse_displacement_is_direction_aware():
    long_r = eo.propose_execution(SIGNAL, quote(4181.17, 4181.53), BAL, PARAMS, SYM)
    assert long_r["adverse_displacement"] > 0
    short = {"direction": "SHORT", "entry": 4180.89, "sl": 4188.04,
             "tp": 4166.59, "sl_distance": 7.15, "symbol": "XAUUSD"}
    short_r = eo.propose_execution(short, quote(4181.17, 4181.53), BAL, PARAMS, SYM)
    assert short_r["adverse_displacement"] < 0


def test_never_rounds_up_to_reach_broker_minimum():
    """Rounding up to force a trade would breach the risk budget."""
    r = eo.propose_execution(SIGNAL, quote(4181.17, 4181.53), 50.0, PARAMS, SYM)
    assert r["execution_validity_status"] == "volume_below_broker_minimum"
    assert r["proposed_volume"] is None
    assert "Not rounded up" in r["execution_validity_detail"]


def test_crossed_stop_is_execution_invalid_not_a_strategy_rejection():
    r = eo.propose_execution(SIGNAL, quote(4173.00, 4173.36), BAL, PARAMS, SYM)
    assert r["execution_validity_status"] == "original_sl_already_crossed"
    assert r["execution_validity_status"] in eo.EXEC_STATUSES


def test_crossed_target_is_execution_invalid():
    r = eo.propose_execution(SIGNAL, quote(4196.00, 4196.36), BAL, PARAMS, SYM)
    assert r["execution_validity_status"] == "original_tp_already_crossed"


def test_unusable_quote_fails_closed():
    for bad in (quote(0, 0), quote(4181.5, 4180.0), None):
        r = eo.propose_execution(SIGNAL, bad, BAL, PARAMS, SYM)
        assert r["proposed_volume"] is None
        assert r["execution_validity_status"] in ("quote_unavailable",
                                                  "quote_unusable")


def test_missing_contract_size_fails_closed():
    r = eo.propose_execution(SIGNAL, quote(4181.17, 4181.53), BAL, PARAMS, {})
    assert r["execution_validity_status"] == "contract_size_unavailable"
    assert r["proposed_volume"] is None


def test_margin_unchecked_is_stated_not_implied():
    r = eo.propose_execution(SIGNAL, quote(4181.17, 4181.53), BAL, PARAMS, SYM)
    assert r["execution_validity_status"] == "margin_not_checked"
    assert r["proposed_volume"] == 1.28, "unchecked margin must not void sizing"


def test_execution_statuses_disjoint_from_strategy_vocabulary():
    """An unexecutable opportunity is not a strategy rejection."""
    from engine import research_schema as rs
    strategy_terms = set()
    for attr in ("RULE_GATES", "CANNOT_EVALUATE_REASONS", "RISK_BLOCKS"):
        strategy_terms |= set(getattr(rs, attr, ()) or ())
    overlap = set(eo.EXEC_STATUSES) & strategy_terms
    assert not overlap, f"vocabularies must not overlap: {overlap}"


def test_stopwatch_uses_monotonic_for_durations():
    sw = eo.Stopwatch()
    sw.mark("a")
    sw.mark("b")
    assert sw.elapsed_ms("a", "b") >= 0
    assert sw.elapsed_ms("a", "missing") is None
    assert sw.stamp("a").endswith("+00:00")


def test_scheduling_delay_applies_the_server_offset():
    """Bar close 10:00 server (= 07:00 UTC), evaluated 07:01 UTC -> 60s."""
    d, note = eo.scheduling_delay_seconds("2026-10-07 10:00:00+00:00",
                                          "2026-10-07T07:01:00+00:00", 10800)
    assert d == 60.0 and note is None, (d, note)


def test_unapplied_offset_is_caught_as_negative():
    """The trap, with the sign established by test rather than assumed.

    Treating a server-clock bar time as UTC makes the bar appear to close three
    hours AFTER evaluation began, so the artefact is a large negative delay,
    not a large positive one. It is caught, but by the negative guard.
    """
    d, note = eo.scheduling_delay_seconds("2026-10-07 10:00:00+00:00",
                                          "2026-10-07T07:01:00+00:00", 0)
    assert d < -10000, d
    assert note is not None and "negative" in note


def test_double_corrected_offset_is_caught_as_implausible():
    """The other failure mode: subtracting an offset that was never present.

    If a bar timestamp is already true UTC and the offset is applied anyway,
    the bar appears to have closed three hours early and the delay inflates
    past an hour. This is what the implausible guard exists for.
    """
    d, note = eo.scheduling_delay_seconds("2026-10-07 07:00:00+00:00",
                                          "2026-10-07T07:08:00+00:00", 10800)
    assert d > 10000, d
    assert note is not None and "implausible" in note


def test_scheduling_delay_negative_is_surfaced_not_clamped():
    d, note = eo.scheduling_delay_seconds("2026-10-07 10:00:00+00:00",
                                          "2026-10-07T06:00:00+00:00", 10800)
    assert d < 0 and "negative" in note


def test_scheduling_delay_matches_the_current_cron_slot():
    """Cron :08 against a bar closing on the hour should read ~480s."""
    d, note = eo.scheduling_delay_seconds("2026-10-07 10:00:00+00:00",
                                          "2026-10-07T07:08:00+00:00", 10800)
    assert d == 480.0 and note is None


def test_scheduling_delay_bad_input_does_not_raise():
    d, note = eo.scheduling_delay_seconds("not a time", "also not", 10800)
    assert d is None and "uncomputable" in note


def test_no_pre_entry_touch_counts_as_an_outcome():
    """Guard the 0g-5 clause at the observation layer too."""
    r = eo.propose_execution(SIGNAL, quote(4173.00, 4173.36), BAL, PARAMS, SYM)
    assert "outcome" not in r
    assert r["execution_validity_status"] == "original_sl_already_crossed"


SHORT = {"direction": "SHORT", "entry": 4180.89, "sl": 4188.04,
         "tp": 4166.59, "sl_distance": 7.15, "symbol": "XAUUSD"}


def test_short_stop_crossed_is_judged_on_ask():
    """A SHORT closes by buying, so its stop fills on the ask.

    Bid 4187.80 is below the 4188.04 stop but ask 4188.16 is above it: the
    position would be stopped on arrival. Judging on the bid, as an earlier
    revision did, passed this as executable.
    """
    r = eo.propose_execution(SHORT, quote(4187.80, 4188.16), BAL, PARAMS, SYM)
    assert r["execution_validity_status"] == "original_sl_already_crossed", r
    assert "ask=" in r["execution_validity_detail"]


def test_long_stop_crossed_still_judged_on_bid():
    """Regression: the LONG rule must not move with the SHORT fix."""
    r = eo.propose_execution(SIGNAL, quote(4173.70, 4174.06), BAL, PARAMS, SYM)
    assert r["execution_validity_status"] == "original_sl_already_crossed", r
    assert "bid=" in r["execution_validity_detail"]


def test_short_sizes_from_bid_to_stop():
    """A SHORT fills at bid and its stop fills at ask = SL, so risk = SL - bid."""
    r = eo.propose_execution(SHORT, quote(4181.17, 4181.53), BAL, PARAMS, SYM)
    assert r["proposed_executable_entry"] == 4181.17
    assert r["executable_sl_distance"] == 6.87
    # 1000 / (6.87 * 100) = 1.4556 -> rounds DOWN to 1.45
    assert r["proposed_volume"] == 1.45
    assert r["planned_stop_loss_cash"] <= 1000.0


def test_spread_accounting_short_pays_on_exit():
    """13 October signal: realised RR is nominal, triggers sit one spread low."""
    lv = eo.spread_accounting("SHORT", 4102.21, 4130.45, 4045.74, 0.26)
    assert lv["spread_side"] == "exit"
    assert lv["fill"] == 4102.21
    assert abs(lv["sl_trigger_bid"] - 4130.19) < 1e-9
    assert abs(lv["tp_trigger_bid"] - 4045.48) < 1e-9
    assert abs(lv["realised_risk"] - 28.24) < 1e-9
    assert abs(lv["realised_rr"] - 56.47 / 28.24) < 1e-9


def test_spread_accounting_long_pays_on_entry():
    """2 October signal: the recorded 1.856R must be reproduced exactly."""
    lv = eo.spread_accounting("LONG", 4180.89, 4173.74, 4195.19, 0.36)
    assert lv["spread_side"] == "entry"
    assert abs(lv["fill"] - 4181.25) < 1e-9
    assert (lv["sl_trigger_bid"], lv["tp_trigger_bid"]) == (4173.74, 4195.19)
    assert round(lv["realised_rr"], 3) == 1.856


def test_spread_accounting_is_not_the_mirrored_long_rule():
    """The defect being fixed: SHORT as entry - spread with bid triggers."""
    lv = eo.spread_accounting("SHORT", 100.0, 105.0, 90.0, 0.5)
    mirrored_rr = (99.5 - 90.0) / (105.0 - 99.5)
    assert lv["realised_rr"] == 2.0
    assert abs(lv["realised_rr"] - mirrored_rr) > 0.2


def test_spread_accounting_rejects_bad_input():
    for args in (("FLAT", 100, 95, 110, 0.5), ("LONG", 100, 95, 110, -0.1)):
        try:
            eo.spread_accounting(*args)
        except ValueError:
            continue
        raise AssertionError(f"accepted {args}")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
