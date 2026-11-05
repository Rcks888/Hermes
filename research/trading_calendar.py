#!/usr/bin/env python3
"""Declared trading calendar `calendar-2` and server offset `offset-eu-1`
(proposed ec-0.3, C1.3 and C1.8). The replay must import this module, never copy it.

Every function here is a function of the clock only. No price, no bar and no
M1/M15 presence is read, so whether minute t is scheduled is known at t
(contract C2.6). The export is used only to verify the schedule
(calendar_check.py), never to define it.

Times: "server" epochs are server wall-clock seconds, read with time.gmtime, as
in the export. New York time follows the US DST rule, server time follows
offset-eu-1.

Schedule, New York time (the CME metals session):
- Trading day X (Mon-Fri) opens at 18:00 on the calendar day before X and
  closes at 17:00 on X. A Monday opens on Sunday at 18:00.
- Full closures, where the trading day X does not exist: Good Friday;
  25 December and 1 January, moved to Monday when they fall on a Sunday.
- Early close at 14:30 (13:30 Chicago) on MLK Day, Presidents Day, Memorial
  Day, Juneteenth, Independence Day (observed), Labor Day and Thanksgiving.
- Early close at 13:45 (12:45 Chicago) on the Friday after Thanksgiving and on
  24 December when that is a weekday.
- From server date BREAK_2026_FROM, the close is the earlier of the above and
  server 23:00. This is the one element dated from the broker's data, not from
  exchange rules. Every date from 7 to 30 March 2026 gives the same schedule,
  because in those gap weeks server 23:00 is 17:00 New York.
EDGE_SECONDS after an open and before a close is a tolerance band: a missing
minute there is not a hole, and an observed one is used.
"""

import calendar as _cal
import sys
from datetime import date, datetime, timedelta, timezone

CALENDAR_RULE = "calendar-2"
OFFSET_RULE = "offset-eu-1"
EDGE_SECONDS = 300
BREAK_2026_FROM = date(2026, 3, 9)
OFFSET_UNCERTAIN = (_cal.timegm((2026, 3, 9, 0, 0, 0)), _cal.timegm((2026, 3, 12, 0, 0, 0)))
OPEN_NY_MIN = 18 * 60
CLOSE_NY_MIN = 17 * 60
EARLY_1430 = 14 * 60 + 30
EARLY_1345 = 13 * 60 + 45


def epoch(d):
    return _cal.timegm((d.year, d.month, d.day, 0, 0, 0))


def utc_date(t):
    return datetime.fromtimestamp(t, timezone.utc).date()


def last_sunday(y, m):
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def nth_sunday(y, m, n):
    d = date(y, m, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))


def nth_weekday(y, m, wd, n):
    d = date(y, m, 1)
    return d + timedelta(days=(wd - d.weekday()) % 7 + 7 * (n - 1))


def last_weekday(y, m, wd):
    d = date(y, m + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def eu_summer(d):
    return last_sunday(d.year, 3) <= d < last_sunday(d.year, 10)


def us_summer(d):
    return nth_sunday(d.year, 3, 2) <= d < nth_sunday(d.year, 11, 1)


def offset_hours(d):
    """offset-eu-1: server = UTC + this, by UTC date (the EU change is at 01:00 UTC on a closed Sunday)."""
    return 3 if eu_summer(d) else 2


def server_to_utc(t):
    return t - 3600 * offset_hours(utc_date(t - 3 * 3600))


def utc_to_server(u):
    return u + 3600 * offset_hours(utc_date(u))


def ny_to_server(d, minutes):
    u = epoch(d) + 60 * minutes + 3600 * (4 if us_summer(d) else 5)
    return utc_to_server(u)


def server_to_ny_minutes(d, t):
    """Minutes after New York midnight of date d at server epoch t."""
    u = server_to_utc(t)
    return (u - 3600 * (4 if us_summer(d) else 5) - epoch(d)) // 60


def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    return date(y, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)


def observed(d):
    return d + timedelta(days=1) if d.weekday() == 6 else d - timedelta(days=1) if d.weekday() == 5 else d


def holidays(y):
    """{New York date: 'full' | minutes of the early close}."""
    h = {}
    h[easter(y) - timedelta(days=2)] = "full"
    for d in (date(y, 12, 25), date(y, 1, 1)):
        if d.weekday() == 6:
            h[d + timedelta(days=1)] = "full"
        elif d.weekday() < 5:
            h[d] = "full"
    early = [nth_weekday(y, 1, 0, 3), nth_weekday(y, 2, 0, 3), last_weekday(y, 5, 0),
             observed(date(y, 6, 19)), observed(date(y, 7, 4)), nth_weekday(y, 9, 0, 1)]
    thanks = nth_weekday(y, 11, 3, 4)
    for d in early + [thanks]:
        h.setdefault(d, EARLY_1430)
    h.setdefault(thanks + timedelta(days=1), EARLY_1345)
    if date(y, 12, 24).weekday() < 5:
        h.setdefault(date(y, 12, 24), EARLY_1345)
    return h


def session(d, hol=None):
    """(open, close) in server epochs for New York trading day d, or None."""
    hol = holidays(d.year) if hol is None else hol
    if d.weekday() > 4 or hol.get(d) == "full":
        return None
    close = hol.get(d, CLOSE_NY_MIN)
    t_close = ny_to_server(d, close)
    if d >= BREAK_2026_FROM:
        t_close = min(t_close, epoch(d) + 23 * 3600)
    return ny_to_server(d - timedelta(days=1), OPEN_NY_MIN), t_close


def sessions(first, last):
    """All sessions whose trading day lies in [first, last] (New York dates)."""
    out, d, cache = [], first, {}
    while d <= last:
        hol = cache.setdefault(d.year, holidays(d.year))
        s = session(d, hol)
        if s:
            out.append(s)
        d += timedelta(days=1)
    return out


def classify(t, sess):
    """'open' | 'edge' | 'closed' for the server minute t; sess from sessions()."""
    for a, b in sess:
        if a <= t < b:
            return "edge" if t < a + EDGE_SECONDS or t >= b - EDGE_SECONDS else "open"
    return "closed"


def offset_uncertain(t):
    return OFFSET_UNCERTAIN[0] <= t < OFFSET_UNCERTAIN[1]


def selftest():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    def hm(t):
        g = datetime.fromtimestamp(t, timezone.utc)
        return g.strftime("%m-%d %H:%M")

    def s(y, m, d):
        r = session(date(y, m, d))
        return (hm(r[0]), hm(r[1])) if r else None

    live = {date(2026, 10, 23): 3, date(2026, 10, 27): 2, date(2026, 11, 5): 2}
    check("offset-eu-1 reproduces the live offsets (23 Oct +3, 27 Oct +2, 5 Nov +2)",
          all(offset_hours(d) == h for d, h in live.items()))
    check("server/UTC round trip across both EU changes",
          all(server_to_utc(utc_to_server(u)) == u for u in
              (epoch(date(2026, 3, 28)) + 12 * 3600, epoch(date(2026, 10, 26)) + 12 * 3600)))
    check("Easter 2023, 2024, 2025, 2026", [easter(y) for y in (2023, 2024, 2025, 2026)]
          == [date(2023, 4, 9), date(2024, 3, 31), date(2025, 4, 20), date(2026, 4, 5)])
    check("both summer 2025: Tue 3 Jun opens Mon 01:00, closes Wed 00:00 server",
          s(2025, 6, 3) == ("06-03 01:00", "06-04 00:00"), str(s(2025, 6, 3)))
    check("gap week 2024: Tue 12 Mar opens 00:00, closes 23:00 server",
          s(2024, 3, 12) == ("03-12 00:00", "03-12 23:00"), str(s(2024, 3, 12)))
    check("Independence Day 2024 closes 21:30 server, 5 Jul reopens 01:00",
          s(2024, 7, 4)[1] == "07-04 21:30" and s(2024, 7, 5)[0] == "07-05 01:00")
    check("24 Dec 2024 closes 20:45 server, 25 Dec has no session, 26 Dec opens 01:00",
          s(2024, 12, 24)[1] == "12-24 20:45" and s(2024, 12, 25) is None and s(2024, 12, 26)[0] == "12-26 01:00")
    check("Good Friday 2024 has no session; Mon 1 Apr opens 01:00 server after the EU change",
          s(2024, 3, 29) is None and s(2024, 4, 1)[0] == "04-01 01:00")
    check("Christmas 2022 on a Sunday closes Mon 26 Dec", s(2022, 12, 26) is None and s(2022, 12, 27)[0] == "12-27 01:00")
    check("2026 summer: Tue 2 Jun closes 23:00 server (16:00 New York); broken variant (17:00) would close 00:00",
          s(2026, 6, 2) == ("06-02 01:00", "06-02 23:00"), str(s(2026, 6, 2)))
    check("2026 gap week (live 27 Oct): opens 00:00, closes 23:00 server",
          s(2026, 10, 27) == ("10-27 00:00", "10-27 23:00"), str(s(2026, 10, 27)))
    check("2026 winter (live 3 Nov): opens 01:00, closes 23:00 server",
          s(2026, 11, 3) == ("11-03 01:00", "11-03 23:00"), str(s(2026, 11, 3)))
    check("Feb 2026, before the change: closes 00:00 server", s(2026, 2, 3)[1] == "02-04 00:00")
    sess = sessions(date(2024, 7, 1), date(2024, 7, 8))
    check("classify: open, edge after an open and before a close, closed in the break and on Saturday",
          classify(_cal.timegm((2024, 7, 2, 10, 0, 0)), sess) == "open"
          and classify(_cal.timegm((2024, 7, 2, 1, 3, 0)), sess) == "edge"
          and classify(_cal.timegm((2024, 7, 2, 23, 57, 0)), sess) == "edge"
          and classify(_cal.timegm((2024, 7, 3, 0, 30, 0)), sess) == "closed"
          and classify(_cal.timegm((2024, 7, 6, 12, 0, 0)), sess) == "closed")
    check("OFFSET_UNCERTAIN covers server 9 Mar 00:00 to 12 Mar 00:00 2026 only",
          offset_uncertain(_cal.timegm((2026, 3, 9, 0, 0, 0))) and offset_uncertain(_cal.timegm((2026, 3, 11, 23, 59, 0)))
          and not offset_uncertain(_cal.timegm((2026, 3, 12, 0, 0, 0))))
    return ok


if __name__ == "__main__":
    print("Self-test:")
    sys.exit(0 if selftest() and print("ALL PASS") is None else 1)
