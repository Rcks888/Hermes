import json
import logging
import requests
from pathlib import Path

logger = logging.getLogger("hermes.telegram")

_config = None
_last_disconnect_alert = False


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


def alert_disconnected(consecutive_failures=1):
    global _last_disconnect_alert
    if consecutive_failures == 1 and not _last_disconnect_alert:
        send_message("🔱 HERMES — MT5 connection lost, skipping cycle")
        _last_disconnect_alert = True
    elif consecutive_failures >= 3:
        send_message(
            f"🚨 HERMES CRITICAL — MT5 down for {consecutive_failures} "
            f"consecutive cycles. Manual intervention needed."
        )


def alert_reconnected(downtime_seconds):
    global _last_disconnect_alert
    _last_disconnect_alert = False
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
