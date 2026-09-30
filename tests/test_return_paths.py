#!/usr/bin/env python3
"""Tests for the return-path catalogue (0f).

The point of these is to make the claim "every decision path is catalogued"
falsifiable. A markdown table asserting the same thing would drift from the
source on the first edit and nobody would notice.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine import return_paths as rp
from engine import research_schema as rs


def test_catalogue_count_matches_source():
    """Add a return without cataloguing it and this fails."""
    for (path, func), entries in rp.CATALOGUES.items():
        actual = rp.count_returns(path, func)
        assert actual == len(entries), (
            f"{func}: {actual} returns in source but {len(entries)} catalogued. "
            f"Current lines: {rp.refresh_lines(path, func)}")


def test_strategy_reconciles_16_sites_to_15_codes():
    """The discrepancy must be fully explained, not approximately."""
    sites = len(rp.STRATEGY_PATHS)
    codes = rp.reason_codes(rp.STRATEGY_PATHS)
    assert sites == 16, sites
    assert len(codes) == 15, codes
    duplicated = [c for c in codes
                  if sum(1 for p in rp.STRATEGY_PATHS if p["code"] == c) > 1]
    assert duplicated == ["vwap_misaligned"], duplicated
    assert sum(1 for p in rp.STRATEGY_PATHS
               if p["code"] == "vwap_misaligned") == 2


def test_strategy_class_distribution():
    """Ten rule gates, four cannot_evaluate conditions, one success."""
    by_class = {}
    for p in rp.STRATEGY_PATHS:
        by_class[p["cls"]] = by_class.get(p["cls"], 0) + 1
    assert by_class[rp.CANNOT] == 4, by_class
    assert by_class[rp.OK] == 1, by_class
    # Ten distinct rule codes across eleven sites, since vwap_misaligned
    # occupies two.
    rule_codes = {p["code"] for p in rp.STRATEGY_PATHS if p["cls"] == rp.RULE}
    assert len(rule_codes) == 10, sorted(rule_codes)
    assert by_class[rp.RULE] == 11, by_class


def test_rule_gates_agree_with_research_schema():
    """Two modules listing the same gates is how a rename gets miscounted.

    research_schema.RULE_GATES drives dataset classification; this catalogue
    drives the audit. They must be the same set or the funnel and the audit
    will disagree about what happened.
    """
    catalogued = {p["code"] for p in rp.STRATEGY_PATHS if p["cls"] == rp.RULE}
    declared = set(rs.RULE_GATES)
    assert catalogued == declared, {
        "in catalogue only": sorted(catalogued - declared),
        "in research_schema only": sorted(declared - catalogued),
    }


def test_cannot_evaluate_reasons_agree_with_research_schema():
    catalogued = {p["code"] for p in rp.STRATEGY_PATHS if p["cls"] == rp.CANNOT}
    declared = set(rs.CANNOT_EVALUATE_REASONS)
    assert catalogued == declared, {
        "in catalogue only": sorted(catalogued - declared),
        "in research_schema only": sorted(declared - catalogued),
    }


def test_every_path_declares_reachability():
    for (path, func), entries in rp.CATALOGUES.items():
        for e in entries:
            assert isinstance(e["reachable"], bool), (func, e["code"])
            # An unreachable path must say why, or the claim is unverifiable.
            if not e["reachable"]:
                assert "UNREACHABLE" in e["note"], (func, e["code"])


def test_unreachable_paths_are_only_in_sizing():
    """Live decision paths should all be reachable.

    The two unreachable branches are defensive guards in
    calculate_position_size, kept because the function is directly callable.
    A new unreachable path anywhere else is a design smell worth surfacing.
    """
    for (path, func), entries in rp.CATALOGUES.items():
        unreachable = [e["code"] for e in entries if not e["reachable"]]
        if func == "calculate_position_size":
            assert set(unreachable) == {"sl_distance_not_positive",
                                        "loss_per_lot_not_positive"}, unreachable
        else:
            assert unreachable == [], (func, unreachable)


def test_helper_returns_excluded_and_counted():
    """_is_pullback's returns must not inflate the decision-path count."""
    assert rp.count_returns("engine/strategy.py", "_is_pullback") == 4
    helper = {h["func"]: h["count"] for h in rp.HELPER_RETURNS}
    assert helper["strategy._is_pullback"] == 4
    assert helper["risk_manager.check_no_trade_filters"] == 1
    # The decision catalogue must not contain them.
    assert rp.count_returns("engine/strategy.py", "evaluate") == 16


def test_every_diag_field_named_exists_in_schema():
    """A catalogue promising a logged field that cannot be stored is a lie."""
    for (path, func), entries in rp.CATALOGUES.items():
        for e in entries:
            for field in e["diag"]:
                assert field in rs.EVALUATION_FIELD_NAMES, (
                    f"{func}:{e['code']} names diag field {field!r} "
                    "which the schema cannot store")


def test_removed_dead_branch_recorded():
    """The removal must stay discoverable so it cannot be reintroduced."""
    codes = {d["code"] for d in rp.REMOVED_DEAD_BRANCHES}
    assert "min_rr_ratio_check" in codes
    entry = next(d for d in rp.REMOVED_DEAD_BRANCHES
                 if d["code"] == "min_rr_ratio_check")
    assert entry["removed_in"] == "17e05e8"


def test_coverage_gaps_are_reported_not_hidden():
    """An honest gap list is more useful than a coverage percentage."""
    gaps = rp.coverage_gaps()
    assert isinstance(gaps, list)
    # Pinned so a new uncovered path forces a deliberate decision rather than
    # sliding in unnoticed. Lower this number as tests are written; never raise
    # it without saying why.
    #
    # Highest priority of the eleven are the four cannot_evaluate paths in
    # evaluate. Those are precisely where a bug would convert missing evidence
    # into an apparent rule violation, and the whole fail-closed design rests
    # on that distinction holding. They are untested because reaching them
    # requires a frame that first passes trend, pullback and candle
    # confirmation, which no synthetic frame has yet produced -- the same
    # reason Hermes has zero live signals. Deferred until replay supplies real
    # qualifying bars rather than faked with monkeypatching, which would test
    # the patch instead of the code.
    assert len(gaps) == 11, (len(gaps), gaps)


def test_summary_renders():
    text = rp.summary()
    assert "evaluate" in text
    assert "approve" in text
    assert "calculate_position_size" in text


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as e:
                print(f"FAIL  {name}: {e}")
                failures += 1
    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)