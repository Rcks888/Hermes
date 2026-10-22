#!/usr/bin/env python3
"""Tests for the 22 Oct MT5 pile-up fixes.

The incident: at 06:05 UTC needrestart restarted xvfb.service and MT5 lost its
display. Every relaunch then hit the 60 s IPC timeout (-10003), and each failed
attempt left another terminal64.exe behind. Runs took 564-968 s, so the :07
soak and :08 strategy runs overlapped, stacked terminals, and the 2 GB host
stopped answering SSH logins.

Three controls, each tested here:
  1. one MT5 run at a time (cross-process lock; a busy run skips, it does not
     start another terminal),
  2. a wall-clock retry budget, so one run cannot outlast its slot,
  3. after an IPC timeout the stuck terminal is killed before the next attempt.
Authorization failures (-6) are broker-side and must not kill the terminal.
"""

import importlib
import sys
import tempfile
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if "rpyc" not in sys.modules:
    sys.modules["rpyc"] = types.ModuleType("rpyc")

from engine import mt5_connector

IPC_TIMEOUT = (-10003, "IPC initialize failed, Pipe server didn't answer in 60 sec")
AUTH_FAILED = (-6, "Terminal: Authorization failed")


def _fresh(tmp):
    mod = importlib.reload(mt5_connector)
    mod.FAILURE_STATE = Path(tmp) / "mt5_failure_state.json"
    mod.MT5_LOCK = Path(tmp) / "mt5.lock"
    mod.RETRY_BACKOFF = [0, 0, 0]
    mod._is_port_open = lambda host, port: True
    return mod


def _failing_run(tmp, error, seconds_per_attempt):
    """One run in which every attempt fails with `error` and costs the given time."""
    mod = _fresh(tmp)
    clock = {"t": 0.0}
    calls = {"attempts": 0, "kills": 0}
    mod._monotonic = lambda: clock["t"]

    def fake_initialize():
        calls["attempts"] += 1
        clock["t"] += seconds_per_attempt
        mod._last_init_error = error
        return False

    def fake_kill():
        calls["kills"] += 1

    mod.initialize = fake_initialize
    mod.is_connected = lambda: False
    mod._kill_stuck_terminals = fake_kill
    ok, info = mod.ensure_connected()
    return ok, info, calls


def _hold_lock(lock_path, held, release):
    mod = importlib.reload(mt5_connector)
    mod.MT5_LOCK = Path(lock_path)
    assert mod.acquire_run_lock(wait_seconds=0)
    held.set()
    release.wait(timeout=20)


def test_second_run_cannot_take_a_held_lock():
    import multiprocessing as mp
    with tempfile.TemporaryDirectory() as tmp:
        ctx = mp.get_context("fork")
        held, release = ctx.Event(), ctx.Event()
        holder = ctx.Process(target=_hold_lock,
                             args=(str(Path(tmp) / "mt5.lock"), held, release))
        holder.start()
        assert held.wait(timeout=10), "holder never took the lock"
        mod = _fresh(tmp)
        try:
            assert mod.acquire_run_lock(wait_seconds=0.5) is False, (
                "a second run took the lock while the first still held it")
        finally:
            release.set()
            holder.join(timeout=10)
        mod = _fresh(tmp)
        assert mod.acquire_run_lock(wait_seconds=2) is True, (
            "lock not released when the holding process exited")


def test_retry_budget_caps_a_run():
    """Attempts that each cost 200 s must stop before a third one starts."""
    with tempfile.TemporaryDirectory() as tmp:
        ok, info, calls = _failing_run(tmp, IPC_TIMEOUT, seconds_per_attempt=200)
        assert not ok
        assert calls["attempts"] == 2, (
            f"{calls['attempts']} attempts in one run; budget "
            f"{mt5_connector.RETRY_BUDGET_SECONDS}s not enforced")


def test_fast_failures_still_get_three_attempts():
    """The budget must not cut the normal 17 s broker-blip retry sequence."""
    with tempfile.TemporaryDirectory() as tmp:
        _, _, calls = _failing_run(tmp, AUTH_FAILED, seconds_per_attempt=1)
        assert calls["attempts"] == 3, calls


def test_ipc_timeout_kills_stuck_terminal():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, calls = _failing_run(tmp, IPC_TIMEOUT, seconds_per_attempt=1)
        assert calls["kills"] == calls["attempts"] == 3, (
            f"{calls['kills']} kills for {calls['attempts']} IPC timeouts: "
            f"stuck terminals left to pile up")


def test_authorization_failure_does_not_kill_terminal():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, calls = _failing_run(tmp, AUTH_FAILED, seconds_per_attempt=1)
        assert calls["kills"] == 0, "terminal killed on a broker-side auth failure"


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
