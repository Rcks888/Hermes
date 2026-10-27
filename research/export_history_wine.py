"""Wine-side history worker for research/export_history.py. Not run directly.

Runs inside Wine Python, next to the terminal, so bulk history never crosses
the rpyc bridge (ROADMAP: one round trip per row). Started by the Linux-side
driver, which holds the MT5 run lock and has already connected the terminal.

    wine "C:\\Program Files\\Python312\\python.exe" export_history_wine.py \
        --out-dir Z:\\root\\Hermes\\data\\history\\<ts> --mode probe|export [--path <terminal64.exe>]

Writes worker_result.json, and in export mode XAUUSD_M15.csv and XAUUSD_M1.csv,
into --out-dir. Times are server clock, bar open, unconverted (contract C1.1).
Requests are explicit server-clock epoch ranges: integers, as every working
call in this repo passes. Read-only: no order, no configuration change.
"""

import argparse
import calendar
import json
import os
import sys
import time
from datetime import datetime, timezone

SYMBOL = "XAUUSD"
EXPORT_START = (2022, 6, 1)
CSV_HEADER = "time,open,high,low,close,tick_volume,spread,real_volume\n"
MAX_TRIES = 5
DAY = 86400

# Probe windows, server-clock dates, a week each. Recent windows sit inside a
# 100,000-bar M1 cap (about 72 trading days), older ones far outside it, so a
# capped client shows an edge between them (contract H2, TBD-2).
PROBE_WINDOWS = [
    ("2026-08-03", 7), ("2026-06-01", 7), ("2026-03-09", 7), ("2025-03-10", 7),
    ("2024-03-11", 7), ("2023-03-13", 7), ("2022-07-04", 7), ("2022-06-23", 7),
]


def epoch(y, m, d):
    return calendar.timegm((y, m, d, 0, 0, 0))


def iso(t):
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d %H:%M")


def month_ranges(start, end):
    """[(a, b)] month chunks from start to end, b exclusive, contiguous."""
    y, m = time.gmtime(start)[:2]
    out = []
    a = start
    while a < end:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        b = min(epoch(y, m, 1), end)
        out.append((a, b))
        a = b
    return out


def format_row(r, digits):
    f = "%." + str(digits) + "f"
    return ("%d," + f + "," + f + "," + f + "," + f + ",%d,%d,%d\n") % (
        int(r[0]), r[1], r[2], r[3], r[4], int(r[5]), int(r[6]), int(r[7]))


def fetch(mt5, tf, a, b):
    """Rows with a <= time < b, re-requested until two calls agree.

    MT5 downloads missing history on demand, so a first call can return part
    of a window. Rows outside [a, b) are dropped and counted: before the
    archive starts MT5 clamps to its oldest bar instead of returning nothing.
    """
    tries, prev, rates = [], None, None
    for i in range(MAX_TRIES):
        rates = mt5.copy_rates_range(SYMBOL, tf, a, b - 1)
        n = -1 if rates is None else len(rates)
        tries.append(n if n >= 0 else "none %s" % (mt5.last_error(),))
        if prev is not None and n == prev:
            break
        prev = n
        time.sleep(0.5 if i == 0 else 3)
    if rates is None:
        return [], {"tries": tries, "dropped": 0, "stable": False}
    rows = [r for r in rates if a <= int(r[0]) < b]
    stable = len(tries) >= 2 and tries[-1] == tries[-2]
    return rows, {"tries": tries, "dropped": len(rates) - len(rows), "stable": stable}


def probe_ranges(end):
    """Control week (the last full week before end) first, then the fixed weeks."""
    out = [(end - 7 * DAY, end)]
    for day, ndays in PROBE_WINDOWS:
        y, m, d = (int(x) for x in day.split("-"))
        out.append((epoch(y, m, d), epoch(y, m, d) + ndays * DAY))
    return out


def probe(mt5, end):
    out = []
    for a, b in probe_ranges(end):
        row = {"window": [iso(a), iso(b)]}
        for name, tf in (("m15", mt5.TIMEFRAME_M15), ("m1", mt5.TIMEFRAME_M1)):
            rows, meta = fetch(mt5, tf, a, b)
            row[name] = len(rows)
            row[name + "_first"] = iso(rows[0][0]) if rows else None
            row[name + "_last"] = iso(rows[-1][0]) if rows else None
            row[name + "_meta"] = meta
        out.append(row)
    return out


def export(mt5, out_dir, start, end, digits):
    files, chunks = {}, []
    for name, tf in (("M15", mt5.TIMEFRAME_M15), ("M1", mt5.TIMEFRAME_M1)):
        fname = "%s_%s.csv" % (SYMBOL, name)
        n = 0
        with open(os.path.join(out_dir, fname), "w", newline="\n") as f:
            f.write(CSV_HEADER)
            for a, b in month_ranges(start, end):
                rows, meta = fetch(mt5, tf, a, b)
                for r in rows:
                    f.write(format_row(r, digits))
                n += len(rows)
                meta.update({"tf": name, "month": iso(a)[:7], "rows": len(rows)})
                chunks.append(meta)
        files[name] = {"file": fname, "rows": n}
    return files, chunks


def plain(d):
    return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v))
            for k, v in d.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--mode", choices=("probe", "export"), required=True)
    ap.add_argument("--path", default="")
    args = ap.parse_args()
    result = {"mode": args.mode, "started": datetime.now(timezone.utc).isoformat()}
    result_path = os.path.join(args.out_dir, "worker_result.json")
    try:
        import MetaTrader5 as mt5
        result["mt5_package"] = getattr(mt5, "__version__", None)
        ok = mt5.initialize(path=args.path) if args.path else mt5.initialize()
        if not ok:
            raise RuntimeError("initialize failed: %s" % (mt5.last_error(),))
        term = mt5.terminal_info()
        result["terminal"] = {k: getattr(term, k, None)
                              for k in ("build", "maxbars", "company", "name", "connected")}
        if not term.connected:
            raise RuntimeError("terminal not connected to the trade server")
        mt5.symbol_select(SYMBOL, True)
        info = mt5.symbol_info(SYMBOL)
        result["symbol_info"] = plain(info._asdict())
        tick = mt5.symbol_info_tick(SYMBOL)
        server_now = int(tick.time)
        end = server_now // DAY * DAY
        result["server_now"] = iso(server_now)
        result["export_end_exclusive"] = iso(end)
        result["probe"] = probe(mt5, end)
        if args.mode == "export":
            start = epoch(*EXPORT_START)
            result["export_start"] = iso(start)
            files, chunks = export(mt5, args.out_dir, start, end, int(info.digits))
            result["files"] = files
            result["chunks"] = chunks
        mt5.shutdown()
        result["ok"] = True
    except Exception as exc:
        result["ok"] = False
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    result["finished"] = datetime.now(timezone.utc).isoformat()
    with open(result_path, "w") as f:
        json.dump(result, f, indent=1, default=str)
    sys.exit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
