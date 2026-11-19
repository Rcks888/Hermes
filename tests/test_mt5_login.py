#!/usr/bin/env python3
"""Tests for the 29 Oct MT5 login defect.

The incident: at 12:52 UTC the soak run's mt5.login() with the settings.json
password was rejected ('Invalid account'). From then every run failed at
mt5.initialize() with (-6, 'Terminal: Authorization failed'): initialize() was
called without credentials, so the terminal kept retrying the account and
password it had saved, and the connector never reached its own login. 74 failed
slots; the CRITICAL alert advised restarting the rpyc bridge, which could not
help an authorisation failure.

Controls:
  1. initialize() hands the configured login, password and server to
     mt5.initialize(), so a terminal holding a stale saved login recovers,
  2. the connector still fails, with the -6 error kept, when the configured
     password itself is wrong,
  3. the terminal must end up on the configured account,
  4. a failed run carries its own reason (mt5_auth_failed, mt5_ipc_timeout,
     mt5_wrong_account), so CYCLE_ABORTED no longer says mt5_not_connected for
     everything,
  5. the alert for an authorisation failure says so and points at the
     password and the terminal log, not at the rpyc bridge,
  6. (Reviewer C1, 19 Nov) on -6 the connector reads the terminal journal;
     if its latest authorization line says 'Invalid account', the alert
     names the login and the account check (approved wording). --controls
     runs the broken variants of the C1 tests.
"""

import importlib
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if "rpyc" not in sys.modules:
    sys.modules["rpyc"] = types.ModuleType("rpyc")

from engine import mt5_connector, terminal_log
from alerts import telegram

GOOD = {"login": 10013386522, "password": "inv12345", "server": "MetaQuotes-Demo",
        "path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe"}
AUTH_FAILED = (-6, "Terminal: Authorization failed")
IPC_TIMEOUT = (-10003, "IPC initialize failed, Pipe server didn't answer in 60 sec")


class FakeTerminal:
    """MT5 terminal behind the bridge. It authorises only the valid account;
    without credentials initialize() retries whatever login it has saved."""

    def __init__(self, saved, valid):
        self.saved = dict(saved)
        self.valid = dict(valid)
        self.current = None
        self.error = (1, "Success")
        self.init_calls = []

    def _authorise(self, login, password, server):
        if (login, password, server) == (self.valid["login"], self.valid["password"],
                                          self.valid["server"]):
            self.current = login
            self.error = (1, "Success")
            return True
        self.saved = {"login": login, "password": password, "server": server}
        self.current = None
        self.error = AUTH_FAILED
        return False

    def initialize(self, path="", **kwargs):
        self.init_calls.append(dict(kwargs))
        if "login" in kwargs:
            return self._authorise(kwargs["login"], kwargs.get("password"), kwargs.get("server"))
        return self._authorise(self.saved["login"], self.saved["password"], self.saved["server"])

    def login(self, login, password="", server="", **kwargs):
        return self._authorise(login, password, server)

    def last_error(self):
        return self.error

    def terminal_info(self):
        return types.SimpleNamespace(connected=self.current is not None)

    def account_info(self):
        if self.current is None:
            return None
        return types.SimpleNamespace(login=self.current)

    def shutdown(self):
        return True


def _connector(tmp, terminal, config):
    mod = importlib.reload(mt5_connector)
    mod.FAILURE_STATE = Path(tmp) / "mt5_failure_state.json"
    mod.MT5_LOCK = Path(tmp) / "mt5.lock"
    mod.RETRY_BACKOFF = [0, 0, 0]
    mod._is_port_open = lambda host, port: True
    mod.start_rpyc_server = lambda: True
    mod._config = dict(config)
    root = types.SimpleNamespace(exposed_mt5=terminal)
    mod.rpyc = types.SimpleNamespace(connect=lambda *a, **k: types.SimpleNamespace(root=root))
    return mod


def test_initialize_passes_configured_credentials():
    term = FakeTerminal(saved=GOOD, valid=GOOD)
    with tempfile.TemporaryDirectory() as tmp:
        mod = _connector(tmp, term, GOOD)
        assert mod.initialize()
    first = term.init_calls[0]
    assert (first.get("login"), first.get("password"), first.get("server")) == (
        GOOD["login"], GOOD["password"], GOOD["server"]), (
        f"mt5.initialize() called with {sorted(first)}: no credentials, the "
        f"terminal retries its saved login")


def test_recovers_from_stale_saved_login():
    """29 Oct: the terminal holds a rejected password; settings.json is right."""
    stale = dict(GOOD, password="old-rejected")
    term = FakeTerminal(saved=stale, valid=GOOD)
    with tempfile.TemporaryDirectory() as tmp:
        mod = _connector(tmp, term, GOOD)
        ok, info = mod.ensure_connected()
    assert ok, f"stuck on the stale saved login: {info}"
    assert term.current == GOOD["login"]


def test_wrong_configured_password_fails_with_auth_reason():
    term = FakeTerminal(saved=GOOD, valid=GOOD)
    with tempfile.TemporaryDirectory() as tmp:
        mod = _connector(tmp, term, dict(GOOD, password="wrong"))
        ok, info = mod.ensure_connected()
    assert not ok
    assert info.get("reason") == "mt5_auth_failed", info
    assert tuple(info.get("last_error", ())) == AUTH_FAILED, info


def test_terminal_on_another_account_is_a_failure():
    other = {"login": 5055745820, "password": "x", "server": "MetaQuotes-Demo"}
    term = FakeTerminal(saved=other, valid=other)
    term.initialize = lambda path="", **kw: term._authorise(
        other["login"], other["password"], other["server"])
    with tempfile.TemporaryDirectory() as tmp:
        mod = _connector(tmp, term, GOOD)
        ok, info = mod.ensure_connected()
    assert not ok, "connected while the terminal is logged into another account"
    assert info.get("reason") == "mt5_wrong_account", info


def test_failure_reason_by_error():
    mod = importlib.reload(mt5_connector)
    assert mod.failure_reason(AUTH_FAILED) == "mt5_auth_failed"
    assert mod.failure_reason(IPC_TIMEOUT) == "mt5_ipc_timeout"
    assert mod.failure_reason((-10005, "IPC timeout")) == "mt5_not_connected"
    assert mod.failure_reason(None) == "mt5_not_connected"


def test_ipc_timeout_reason_from_ensure_connected():
    with tempfile.TemporaryDirectory() as tmp:
        mod = _connector(tmp, FakeTerminal(saved=GOOD, valid=GOOD), GOOD)

        def fake_initialize():
            mod._last_init_error = IPC_TIMEOUT
            return False

        mod.initialize = fake_initialize
        mod.is_connected = lambda: False
        mod._kill_stuck_terminals = lambda: None
        ok, info = mod.ensure_connected()
    assert not ok and info.get("reason") == "mt5_ipc_timeout", info


def _alerts(tmp):
    sent = []
    telegram.ALERT_STATE = Path(tmp) / "alert_state.json"
    telegram.send_message = lambda text: sent.append(text)
    return sent


def test_auth_alert_names_the_cause():
    with tempfile.TemporaryDirectory() as tmp:
        sent = _alerts(tmp)
        telegram.alert_disconnected(1, reason="mt5_auth_failed")
        telegram.alert_disconnected(4, reason="mt5_auth_failed")
    assert len(sent) == 2, sent
    for text in sent:
        assert "authoris" in text.lower(), text
        assert "settings.json" in text, text
    assert "pkill" not in sent[1], f"critical auth alert still advises a bridge restart: {sent[1]}"


def test_default_alert_text_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        sent = _alerts(tmp)
        telegram.alert_disconnected(1)
        telegram.alert_disconnected(4)
    assert sent[0] == "⚠️ HERMES — MT5 connection lost, skipping cycle (1st failure)", sent[0]
    assert "pkill -f python.exe" in sent[1], sent[1]


NOW = datetime(2026, 11, 16, 8, 30, tzinfo=timezone.utc)
REJECTED = "\t'10013386522': authorization on MetaQuotes-Demo failed (Invalid account)"
AUTHORISED = "\t'10013386522': authorized on MetaQuotes-Demo through Access Server"


def _journal(log_dir, day, lines):
    """One terminal journal file, UTF-16 as MT5 writes it."""
    (Path(log_dir) / f"{day:%Y%m%d}.log").write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-16"))


def test_terminal_log_invalid_account():
    """C1: only the latest authorization line counts; yesterday's journal is
    read when today's has none."""
    cases = [({NOW: ["build 5430 started", REJECTED]}, True),
             ({NOW: [REJECTED, "connection lost", AUTHORISED]}, False),
             ({NOW: [AUTHORISED, REJECTED]}, True),
             ({NOW: ["build 5430 started"], NOW - timedelta(days=1): [AUTHORISED, REJECTED]}, True),
             ({NOW - timedelta(days=1): [REJECTED], NOW: [AUTHORISED]}, False),
             ({}, False)]
    for files, want in cases:
        with tempfile.TemporaryDirectory() as d:
            for day, lines in files.items():
                _journal(d, day, lines)
            got = terminal_log.invalid_account(NOW, d)
        assert got is want, f"journal {files}: invalid_account {got}, want {want}"


def test_auth_failure_reports_invalid_account():
    term = FakeTerminal(saved=GOOD, valid=GOOD)
    with tempfile.TemporaryDirectory() as tmp:
        _journal(tmp, datetime.now(timezone.utc), [REJECTED])
        mod = _connector(tmp, term, dict(GOOD, password="wrong"))
        with mock.patch.object(terminal_log, "LOG_DIR", Path(tmp)):
            ok, info = mod.ensure_connected()
    assert not ok and info.get("reason") == "mt5_auth_failed", info
    assert info.get("invalid_account") is True and info.get("login") == GOOD["login"], (
        f"-6 with 'Invalid account' in the journal: {info}")


def test_invalid_account_alert_text():
    want = ("MT5 authorisation failed: terminal log reports 'Invalid account' for 10013386522. "
            "Check in the MT5 web terminal whether the demo account still exists; if it does not, "
            "open a new demo and update the config.")
    with tempfile.TemporaryDirectory() as tmp:
        sent = _alerts(tmp)
        telegram.alert_disconnected(1, reason="mt5_auth_failed", invalid_account=True, login=GOOD["login"])
        telegram.alert_disconnected(4, reason="mt5_auth_failed", invalid_account=False, login=GOOD["login"])
    assert len(sent) == 2, sent
    assert sent[0].splitlines()[-1] == want, f"C1 alert text: {sent[0]!r}"
    assert sent[1].splitlines()[-1] == telegram.AUTH_FAILED_HINT, f"no rejection in the journal: {sent[1]!r}"


def _any_rejection(now=None, log_dir=None):
    d = Path(log_dir)
    return any(terminal_log.INVALID_ACCOUNT in p.read_bytes().decode("utf-16") for p in d.glob("*.log"))


def _today_only(now=None, log_dir=None):
    try:
        text = (Path(log_dir) / f"{now:%Y%m%d}.log").read_bytes().decode("utf-16")
    except OSError:
        return False
    line = terminal_log.latest_authorization(text)
    return line is not None and terminal_log.INVALID_ACCOUNT in line


VARIANTS = {
    "test_terminal_log_invalid_account": [
        ("any 'Invalid account' line counts", terminal_log, "invalid_account", _any_rejection),
        ("today's journal only", terminal_log, "invalid_account", _today_only)],
    "test_auth_failure_reports_invalid_account": [
        ("journal not consulted on -6", terminal_log, "invalid_account", lambda now=None, log_dir=None: False)],
    "test_invalid_account_alert_text": [
        ("generic hint kept", telegram, "auth_hint", lambda invalid, login: telegram.AUTH_FAILED_HINT),
        ("hint whatever the journal says", telegram, "auth_hint",
         lambda invalid, login: telegram.INVALID_ACCOUNT_TEXT.format(login=login))],
}


def _run(fn):
    try:
        fn()
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def main_controls():
    """Broken variants for the C1 tests (the earlier tests in this file
    predate the controls rule)."""
    bad = 0
    for name in sorted(VARIANTS):
        for label, module, attr, broken in VARIANTS[name]:
            with mock.patch.object(module, attr, broken):
                why = _run(globals()[name])
            if why and why.startswith("AssertionError"):
                print(f"PASS  {name} / {label}: fails as required ({why[:90]})")
            else:
                bad += 1
                print(f"FAIL  {name} / {label}: {'passed' if why is None else why}")
    print("\nCONTROLS ALL PASS" if bad == 0 else f"\nCONTROLS: {bad} FAILED")
    return bad == 0


if __name__ == "__main__":
    if "--controls" in sys.argv:
        sys.exit(0 if main_controls() else 1)
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
