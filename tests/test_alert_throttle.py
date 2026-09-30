#!/usr/bin/env python3
"""Tests for cross-process alert throttling.

The bug being fixed: _last_disconnect_alert was a module-level global, but
run_hermes and run_soak are separate cron invocations a minute apart. Every
process started with the flag unset, so deduplication never happened and the
critical tier re-fired on every cycle from both processes -- roughly 2,700
messages over a two-week absence with a dead bridge. The practical consequence
is a muted channel and a missed real alert.

State therefore has to survive process exit, and the tests simulate separate
processes by reloading the module rather than by calling twice in one.
"""

import importlib
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alerts import telegram

_sent = []


def _setup(tmpdir):
    """Point state at a temp file and capture sends instead of posting."""
    telegram.ALERT_STATE = Path(tmpdir) / "alert_state.json"
    _sent.clear()
    telegram.send_message = lambda text: _sent.append(text)


def test_repeated_condition_sends_once_per_window():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        for _ in range(20):
            telegram.send_throttled("low_ram", "low ram", cooldown_minutes=60)
        assert len(_sent) == 1, f"expected 1 send, got {len(_sent)}"


def test_state_survives_process_restart():
    """The actual bug. A fresh process must not reset the throttle."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        telegram.send_throttled("mt5_disconnected_critical", "down")
        assert len(_sent) == 1

        # Simulate the next cron invocation: reload the module so every
        # module-level global is reinitialised, exactly as a new process would.
        state_path = telegram.ALERT_STATE
        importlib.reload(telegram)
        telegram.ALERT_STATE = state_path
        telegram.send_message = lambda text: _sent.append(text)

        telegram.send_throttled("mt5_disconnected_critical", "down")
        assert len(_sent) == 1, (
            "a new process re-sent a throttled alert -- state is not persisting")


def test_suppressed_count_is_reported_not_lost():
    """Throttling may stop repetition but must not hide persistence."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        telegram.send_throttled("low_ram", "low ram", cooldown_minutes=60)
        for _ in range(5):
            telegram.send_throttled("low_ram", "low ram", cooldown_minutes=60)

        # Move the last_sent timestamp back beyond the window.
        state = json.load(open(telegram.ALERT_STATE))
        state["low_ram"]["last_sent"] = (
            datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        json.dump(state, open(telegram.ALERT_STATE, "w"))

        telegram.send_throttled("low_ram", "low ram", cooldown_minutes=60)
        assert len(_sent) == 2
        assert "5 further occurrence" in _sent[1], _sent[1]


def test_clear_allows_immediate_realert():
    """A fault that resolves and returns inside one window must notify again."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        telegram.send_throttled("low_ram", "low ram")
        assert len(_sent) == 1
        telegram.send_throttled("low_ram", "low ram")
        assert len(_sent) == 1

        telegram.clear_alert("low_ram")
        telegram.send_throttled("low_ram", "low ram")
        assert len(_sent) == 2, "cleared key did not re-alert"


def test_distinct_keys_do_not_throttle_each_other():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        telegram.send_throttled("low_ram", "a")
        telegram.send_throttled("swap_pressure", "b")
        telegram.send_throttled("mt5_disconnected_first", "c")
        assert len(_sent) == 3


def test_corrupt_state_fails_toward_alerting():
    """A broken state file must not silence alerts."""
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        telegram.ALERT_STATE.write_text("{ this is not json")
        telegram.send_throttled("low_ram", "low ram")
        assert len(_sent) == 1, "corrupt state suppressed an alert"


def test_reconnect_clears_all_disconnect_tiers():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        telegram.alert_disconnected(1)
        telegram.alert_disconnected(2)
        telegram.alert_disconnected(9)
        assert len(_sent) == 3

        telegram.alert_reconnected(120.0)
        assert len(_sent) == 4

        # After recovery, a fresh outage must notify at every tier again.
        telegram.alert_disconnected(1)
        telegram.alert_disconnected(2)
        telegram.alert_disconnected(9)
        assert len(_sent) == 7, (
            f"tiers not cleared on reconnect, only {len(_sent) - 4} re-sent")


def test_sustained_outage_is_bounded():
    """Two weeks of 15-minute cycles from two processes must stay bounded.

    Before the fix this produced roughly 2,700 messages.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp)
        start = datetime.now(timezone.utc)
        cycles = 14 * 24 * 4 * 2  # two weeks, every 15 min, two processes
        for i in range(cycles):
            # Advance the clock by rewriting last_sent, since the real cooldown
            # is wall-clock based.
            if telegram.ALERT_STATE.exists():
                state = json.load(open(telegram.ALERT_STATE))
                for entry in state.values():
                    if entry.get("last_sent"):
                        entry["last_sent"] = (
                            datetime.fromisoformat(entry["last_sent"])
                            - timedelta(minutes=7.5)).isoformat()
                json.dump(state, open(telegram.ALERT_STATE, "w"), default=str)
            telegram.alert_disconnected(i + 4)

        # 6-hour cooldown over 14 days is at most 56 sends.
        assert len(_sent) <= 60, f"{len(_sent)} alerts over two weeks"
        assert len(_sent) >= 1


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