#!/usr/bin/env python3
"""Tests for cross-process MT5 failure state.

The bug (found 20 Oct): ensure_connected() kept its failure count and
disconnect time in module globals, but run_soak (:07) and run_hermes (:08) are
separate cron processes. Every run started at zero, so the count was always 1,
the "still down" and CRITICAL tiers never fired, no run ever reported a
reconnect, and the reconnect alert that clears the throttle never ran. The log
shows "Consecutive failures: 1" for the two failed Saturday checks of 17 Oct,
sixteen minutes apart.

Each simulated cron run reloads the module, so every global is reinitialised as
in a new process. Failures are counted per 15-minute slot: the soak/strategy
pair of one slot counts once.
"""

import importlib
import sys
import tempfile
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if "rpyc" not in sys.modules:
    sys.modules["rpyc"] = types.ModuleType("rpyc")

from engine import mt5_connector
from alerts import telegram

T0 = datetime(2026, 10, 19, 19, 22, 2, tzinfo=timezone.utc)


def _run(tmp, now, up):
    """One cron process: fresh module state, MT5 either reachable or not."""
    mod = importlib.reload(mt5_connector)
    mod.FAILURE_STATE = Path(tmp) / "mt5_failure_state.json"
    mod.RETRY_BACKOFF = [0, 0, 0]
    mod._utcnow = lambda: now
    mod._is_port_open = lambda host, port: True
    initialised = {"done": False}

    def fake_initialize():
        initialised["done"] = up
        return up

    mod.initialize = fake_initialize
    mod.is_connected = lambda: initialised["done"]
    return mod.ensure_connected()


def test_failure_count_survives_process_restart():
    with tempfile.TemporaryDirectory() as tmp:
        ok, info = _run(tmp, T0, up=False)
        assert not ok and info["consecutive_failures"] == 1
        ok, info = _run(tmp, T0 + timedelta(minutes=15), up=False)
        assert info["consecutive_failures"] == 2, (
            f"second slot reported {info['consecutive_failures']}: count not persisted")
        ok, info = _run(tmp, T0 + timedelta(minutes=45), up=False)
        assert info["consecutive_failures"] == 3


def test_soak_and_strategy_pair_counts_once():
    with tempfile.TemporaryDirectory() as tmp:
        _, soak = _run(tmp, T0, up=False)
        _, cycle = _run(tmp, T0 + timedelta(minutes=1), up=False)
        assert soak["consecutive_failures"] == 1
        assert cycle["consecutive_failures"] == 1, "the :08 run of the same slot escalated"
        _, nxt = _run(tmp, T0 + timedelta(minutes=15), up=False)
        assert nxt["consecutive_failures"] == 2


def test_new_process_reports_reconnect():
    with tempfile.TemporaryDirectory() as tmp:
        _run(tmp, T0, up=False)
        ok, info = _run(tmp, T0 + timedelta(minutes=15), up=True)
        assert ok
        assert info.get("reconnected") is True, "a new process did not see the outage end"
        assert abs(info["downtime_seconds"] - 900) < 1e-6
        assert not (Path(tmp) / "mt5_failure_state.json").exists(), "state not cleared"


def test_no_reconnect_without_prior_failure():
    with tempfile.TemporaryDirectory() as tmp:
        ok, info = _run(tmp, T0, up=True)
        assert ok and info.get("reconnected") is False


def test_fresh_outage_after_recovery_starts_at_one():
    with tempfile.TemporaryDirectory() as tmp:
        _run(tmp, T0, up=False)
        _run(tmp, T0 + timedelta(minutes=15), up=False)
        _run(tmp, T0 + timedelta(minutes=30), up=True)
        _, info = _run(tmp, T0 + timedelta(minutes=45), up=False)
        assert info["consecutive_failures"] == 1


def test_corrupt_state_still_counts_the_failure():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "mt5_failure_state.json").write_text("{ not json")
        ok, info = _run(tmp, T0, up=False)
        assert not ok and info["consecutive_failures"] == 1


def test_19_oct_broker_blip_sends_two_messages():
    """Soak 19:22 and cycle 19:23 fail, soak 19:37 connects: one disconnect
    alert, one reconnect alert, and the next blip alerts again immediately."""
    sent = []
    with tempfile.TemporaryDirectory() as tmp:
        telegram.ALERT_STATE = Path(tmp) / "alert_state.json"
        telegram.send_message = lambda text: sent.append(text)

        for minutes in (0, 1):
            _, info = _run(tmp, T0 + timedelta(minutes=minutes), up=False)
            telegram.alert_disconnected(info["consecutive_failures"])
        _, info = _run(tmp, T0 + timedelta(minutes=15), up=True)
        if info.get("reconnected"):
            telegram.alert_reconnected(info["downtime_seconds"])

        assert len(sent) == 2, f"expected 2 messages, got {len(sent)}: {sent}"
        assert "1st failure" in sent[0]
        assert "reconnected" in sent[1]

        _, info = _run(tmp, T0 + timedelta(hours=1), up=False)
        telegram.alert_disconnected(info["consecutive_failures"])
        assert len(sent) == 3, "a new outage after recovery was throttled"
        assert "suppressed" not in sent[2], sent[2]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as e:
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
                failures += 1
    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)
