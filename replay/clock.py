"""Clocks, instants and the entry price (contract C1.1-C1.9, C2.2).

Server epochs throughout, bar OPEN stamps (C1.1). UTC is derived only where a
rule needs it, through trading_calendar (offset-eu-1, C1.3); inside the
OFFSET_UNCERTAIN window no decision uses an offset (C1.3a).
"""
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

import trading_calendar as tc  # noqa: E402
from engine.pipeline import get_session_tag  # noqa: E402

M15, M1 = 900, 60
PREREGISTERED_DELAYS = (60, 120, 180, 300, 480)
BASELINE_DELAY = 480
OFFSET_RULE = tc.OFFSET_RULE
CALENDAR_RULE = tc.CALENDAR_RULE


def bar_close(bar_open):
    return bar_open + M15


def decision_instant(bar_open, delay):
    """D = bar close + delay (C1.4)."""
    return bar_open + M15 + delay


def entry_instant(d):
    """E = ceil_minute(D) (C1.6): the first whole minute at or after D."""
    return int(math.ceil(d / M1)) * M1


def pre_entry_minutes(bar_open, e):
    """M1 stamps in the pre-entry window [bar close, E) (C5.2)."""
    return list(range(bar_close(bar_open), e, M1))


def server_to_utc(t):
    """UTC epoch for server time t under offset-eu-1 (C1.3); fractions kept."""
    whole = int(math.floor(t))
    return tc.server_to_utc(whole) + (t - whole)


def offset_seconds(t):
    return int(math.floor(t)) - tc.server_to_utc(int(math.floor(t)))


def offset_uncertain(t, window=tc.OFFSET_UNCERTAIN):
    return tc.offset_uncertain(int(math.floor(t)), window)


def session_of(d):
    """C2.2: the engine's get_session_tag at the UTC hour of D."""
    return get_session_tag(time.gmtime(server_to_utc(d)).tm_hour)


def weekday_utc(d):
    """C1.7: evaluated only when D falls Mon-Fri UTC."""
    return time.gmtime(server_to_utc(d)).tm_wday < 5


def schedule(first, last, window=tc.OFFSET_UNCERTAIN):
    """calendar-2 scheduled time with the C1.3a union (trading_calendar.Schedule)."""
    return tc.Schedule(first, last, window=window, union=True)


def opportunity_clock(bar_open, delay, window=tc.OFFSET_UNCERTAIN):
    """Clock fields of one M15 bar (C1.3a, C1.4, C1.6, C1.7, C2.2).

    Inside the OFFSET_UNCERTAIN window nothing is compared: the bar is
    `offset_uncertain`, with UTC under both offsets recorded."""
    d = decision_instant(bar_open, delay)
    e = entry_instant(d)
    out = {"bar_open": bar_open, "bar_close": bar_close(bar_open), "D": d, "E": e,
           "delay": delay, "offset_rule_version": OFFSET_RULE}
    if offset_uncertain(d, window):
        out.update({"status": "quote_unavailable", "detail": "offset_uncertain",
                    "offset_seconds": None, "session": None, "evaluated": None,
                    "D_utc_if_utc2": d - 7200, "D_utc_if_utc3": d - 10800,
                    "E_utc_if_utc2": e - 7200, "E_utc_if_utc3": e - 10800})
        return out
    out.update({"status": None, "detail": None, "offset_seconds": offset_seconds(d),
                "D_utc": server_to_utc(d), "E_utc": server_to_utc(e),
                "session": session_of(d), "evaluated": weekday_utc(d)})
    return out


def entry_bid(m1, e, sched):
    """(bid, None) from the open of the M1 bar stamped E (C1.6), or
    (None, detail) per C1.9. Never deferred to a later minute."""
    bid = m1.open_at(e)
    if bid is not None:
        return bid, None
    return None, ("m1_minute_unavailable" if sched.scheduled(e) else "market_closed")
