"""The MT5 terminal journal on the VPS (Reviewer C1 v4, approved 19 Nov).

On an authorisation failure (-6) the connector asks whether the terminal's
latest authorization entry is a rejection of the configured account, and
when it was written. The journal is one UTF-16 file per day under the
terminal's logs/ directory, each line
    <code> <level> HH:MM:SS.mmm <source> '<login>': authoriz...
Evidence from 16 Nov (VPS journal 20261116.log): the connector's -6 runs
from 00:07 UTC wrote no authorization line, so the rejection that explains
an outage can be hours old; the alert therefore states its time instead of
implying it is current. Rules:
- only the latest authorization line counts (a later success, for any
  account, means the terminal is not in the rejected state);
- it must say 'Invalid account' and name the configured login exactly;
- its time is the file's date and the line's time, in UTC: the VPS clock is
  UTC (date = date -u, 19 Nov) and the journal follows it. On a host that is
  not UTC nothing is reported;
- a line that does not parse, or is later than now, reports nothing.
Today's file is read first, then yesterday's. Nothing from the journal is
sent or logged beyond the login and the time.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_DIR = Path.home() / ".wine" / "drive_c" / "Program Files" / "MetaTrader 5" / "logs"
INVALID_ACCOUNT = "failed (Invalid account)"
LINE = re.compile(r"^\S+\s+\d+\s+(?P<t>\d{2}:\d{2}:\d{2})\.\d{3}\s+\S+\s+'(?P<login>\d+)': (?P<msg>.*)$")


def latest_authorization(text):
    lines = [x for x in text.splitlines() if "authoriz" in x.lower()]
    return lines[-1] if lines else None


def local_offset():
    return datetime.now().astimezone().utcoffset()


def host_is_utc():
    return local_offset() == timedelta(0)


def rejection_of(line, day, login):
    """The UTC time of a rejection of login on this line, else None."""
    m = LINE.match(line.strip())
    if not m or m["login"] != str(login) or INVALID_ACCOUNT not in m["msg"]:
        return None
    return datetime.strptime(f"{day:%Y%m%d} {m['t']}", "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc)


def account_rejected_at(login, now=None, log_dir=None):
    """When the terminal's latest authorization entry rejected login, or None."""
    now = now or datetime.now(timezone.utc)
    if login is None or not host_is_utc():
        return None
    log_dir = Path(log_dir or LOG_DIR)
    for day in (now, now - timedelta(days=1)):
        try:
            text = (log_dir / f"{day:%Y%m%d}.log").read_bytes().decode("utf-16", errors="replace")
        except OSError:
            continue
        line = latest_authorization(text)
        if line is not None:
            at = rejection_of(line, day, login)
            return at if at is not None and at <= now else None
    return None
