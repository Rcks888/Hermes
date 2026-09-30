import json
import logging
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger("hermes.telegram")

_config = None

# Cross-process alert state. run_hermes and run_soak are separate cron
# invocations a minute apart, so a module-level global cannot deduplicate
# anything: every process starts with it unset. Throttling has to survive
# process exit, which means it lives on disk.
ALERT_STATE = Path(__file__).parent.parent / "logs" / "alert_state.json"

# Alert on transitions and rates, not states. A condition that persists must
# not re-notify every cycle -- 96 cycles a day across two processes turns a
# single sustained fault into thousands of messages, and the practical result
# is a muted channel and a missed real alert.
COOLDOWNS_MINUTES = {
    "mt5_disconnected_first": 60,
    "mt5_disconnected_ongoing": 180,
    "mt5_disconnected_critical": 360,
    "low_ram": 360,
    "swap_pressure": 360,
    "swap_thrashing": 360,
    "swap_growth": 360,
}
DEFAULT_COOLDOWN_MINUTES = 60


def _load_config():
    global _config
    if _config is None:
        cfg_path = Path(__file__).parent.parent / "config" / "settings.json"
        with open(cfg_path) as f:
            _config = json.load(f)
    return _config


def send_message(text):
    cfg = _load_config()
    token = cfg["telegram"]["bot_token"]
    chat_id = cfg["telegram"]["chat_id"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Telegram send failed: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Telegram send error: {e}")


def _load_alert_state():
    if ALERT_STATE.exists():
        try:
            with open(ALERT_STATE) as f:
                return json.load(f)
        except Exception as exc:
            # A corrupt state file must not silence alerts. Fail toward
            # notifying rather than toward quiet.
            logger.warning(f"Alert state unreadable ({exc}); treating as empty")
    return {}


def _save_alert_state(state):
    """Atomic write. Two cron processes a minute apart can collide, and a
    half-written state file would be indistinguishable from a corrupt one."""
    try:
        ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(ALERT_STATE.parent), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, str(ALERT_STATE))
    except Exception as exc:
        logger.warning(f"Could not persist alert state: {exc}")


def send_throttled(key, text, cooldown_minutes=None):
    """Send at most once per cooldown window for this key.

    Returns True if sent. Suppressed occurrences are counted and reported in
    the next message that does go out, so throttling never hides how long a
    condition persisted -- it only stops the repetition.
    """
    if cooldown_minutes is None:
        cooldown_minutes = COOLDOWNS_MINUTES.get(key, DEFAULT_COOLDOWN_MINUTES)
    now = datetime.now(timezone.utc)
    state = _load_alert_state()
    entry = state.get(key, {})

    last_sent = entry.get("last_sent")
    if last_sent:
        try:
            last = datetime.fromisoformat(last_sent)
            if now - last < timedelta(minutes=cooldown_minutes):
                entry["suppressed"] = entry.get("suppressed", 0) + 1
                entry["last_suppressed"] = now.isoformat()
                state[key] = entry
                _save_alert_state(state)
                logger.info(
                    f"Alert '{key}' suppressed "
                    f"({entry['suppressed']} within {cooldown_minutes}min window)")
                return False
        except ValueError:
            pass  # unparseable timestamp: treat as never sent

    suppressed = entry.get("suppressed", 0)
    if suppressed:
        since = entry.get("first_seen") or last_sent
        text += (f"\n\n<i>{suppressed} further occurrence(s) suppressed since "
                 f"{since}.</i>")

    send_message(text)
    state[key] = {
        "last_sent": now.isoformat(),
        "suppressed": 0,
        "first_seen": entry.get("first_seen") or now.isoformat(),
    }
    _save_alert_state(state)
    return True


def clear_alert(key):
    """Forget a key so the next occurrence notifies immediately.

    Called on recovery. Without this, a fault that resolves and returns inside
    one cooldown window would be silent the second time -- which is precisely
    the case worth knowing about.
    """
    state = _load_alert_state()
    if key in state:
        del state[key]
        _save_alert_state(state)


def alert_disconnected(consecutive_failures=1):
    """Escalating notification, throttled per tier and across processes.

    The previous implementation used a module-level flag, which could not work:
    run_hermes and run_soak are separate cron invocations, so the flag was
    always unset at entry and the critical tier re-fired every cycle from both.
    """
    if consecutive_failures == 1:
        send_throttled(
            "mt5_disconnected_first",
            "⚠️ HERMES — MT5 connection lost, skipping cycle (1st failure)")
    elif 2 <= consecutive_failures < 4:
        send_throttled(
            "mt5_disconnected_ongoing",
            f"⚠️ HERMES — MT5 still down "
            f"({consecutive_failures} consecutive failures)")
    else:
        send_throttled(
            "mt5_disconnected_critical",
            f"🚨 HERMES CRITICAL HALT — MT5 down for {consecutive_failures} "
            f"consecutive cycles. rpyc bridge or MT5 may need manual restart.\n"
            f"Run on the VPS: 'pkill -f python.exe; sleep 2; "
            f"cd ~/Hermes && DISPLAY=:99 venv/bin/python run_soak.py'")


def alert_reconnected(downtime_seconds):
    for key in ("mt5_disconnected_first", "mt5_disconnected_ongoing",
                "mt5_disconnected_critical"):
        clear_alert(key)
    minutes = downtime_seconds / 60
    send_message(f"🔱 HERMES — MT5 reconnected after {minutes:.1f} min")


def alert_soak_status(stats):
    text = (
        f"🔱 HERMES — Soak Status\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Uptime: {stats['uptime_pct']:.1f}%\n"
        f"Cycles: {stats['total_cycles']} ({stats['success_cycles']} ok / {stats['fail_cycles']} fail)\n"
        f"Symbol: {stats.get('symbol', 'N/A')}\n"
        f"Spread now: {stats.get('spread', 'N/A')}\n"
        f"Server time offset: {stats.get('time_offset', 'N/A')}"
    )
    send_message(text)
