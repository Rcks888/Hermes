"""The MT5 terminal journal on the VPS (Reviewer C1, approved 19 Nov).

On an authorisation failure (-6) the connector asks whether the terminal
itself rejected the account. The journal is one UTF-16 file per day under
the terminal's logs/ directory. Only the latest authorization line counts,
so an 'Invalid account' earlier in the day followed by a successful login
does not trigger. Today's file is read first, then yesterday's (a failure
just after midnight). Nothing from the journal is sent or logged; the caller
gets a boolean.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_DIR = Path.home() / ".wine" / "drive_c" / "Program Files" / "MetaTrader 5" / "logs"
INVALID_ACCOUNT = "Invalid account"


def latest_authorization(text):
    lines = [x for x in text.splitlines() if "authoriz" in x.lower()]
    return lines[-1] if lines else None


def invalid_account(now=None, log_dir=None):
    """True iff the latest authorization line of the journal says 'Invalid account'."""
    now = now or datetime.now(timezone.utc)
    log_dir = Path(log_dir or LOG_DIR)
    for day in (now, now - timedelta(days=1)):
        try:
            text = (log_dir / f"{day:%Y%m%d}.log").read_bytes().decode("utf-16", errors="replace")
        except OSError:
            continue
        line = latest_authorization(text)
        if line is not None:
            return INVALID_ACCOUNT in line
    return False
