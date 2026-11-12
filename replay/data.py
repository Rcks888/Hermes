"""History snapshot for the replay (contract C6.1, C6.2) and the holdout seal
(PREREGISTRATION.md F2, F6, H1).

Bars carry server-clock epochs of the bar OPEN (C1.1), exactly as exported.
A snapshot is only usable if both CSVs hash to the values in its manifest.

The seal: no strategy window and no exit path may touch a bar inside the
holdout, server 2025-09-01 00:00 to 2026-09-12 00:00. Data-quality checks
(calendar_check.py, export QC) read the CSVs directly and are not affected.
Unsealing is pr-0.4 H1 and is not implemented: the engine refuses.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

import export_history as eh  # noqa: E402

SNAPSHOT_ID = "20261027T070105Z"
DEFAULT_DIR = ROOT / "data" / "history" / SNAPSHOT_ID
HOLDOUT = (eh.HOLDOUT_START, eh.HOLDOUT_END)
M15, M1 = 900, 60


class HoldoutSealed(Exception):
    """A strategy window or an exit path reached into the sealed holdout."""


class SnapshotMismatch(Exception):
    """A CSV does not hash to its manifest value."""


def in_holdout(t):
    return HOLDOUT[0] <= t < HOLDOUT[1]


def guard(t0, t1):
    """Refuse any server span [t0, t1) that overlaps the holdout."""
    if t0 < HOLDOUT[1] and t1 > HOLDOUT[0]:
        raise HoldoutSealed(f"span [{t0}, {t1}) overlaps the sealed holdout {HOLDOUT}")


class Bars:
    """One timeframe, as sorted numpy arrays indexed by bar-open epoch."""

    def __init__(self, period, time, open_, high, low, close, volume=None, spread=None):
        self.period = period
        self.time = np.asarray(time, dtype=np.int64)
        self.open = np.asarray(open_, dtype=float)
        self.high = np.asarray(high, dtype=float)
        self.low = np.asarray(low, dtype=float)
        self.close = np.asarray(close, dtype=float)
        n = len(self.time)
        self.volume = np.zeros(n) if volume is None else np.asarray(volume, dtype=float)
        self.spread = np.zeros(n, dtype=np.int64) if spread is None else np.asarray(spread, dtype=np.int64)
        if n and (np.any(np.diff(self.time) <= 0) or np.any(self.time % period)):
            raise ValueError("bar times must be strictly increasing multiples of the period")

    @classmethod
    def from_rows(cls, period, rows):
        """rows: (t, o, h, l, c) or (t, o, h, l, c, volume, spread)."""
        cols = list(zip(*rows)) if rows else [[]] * 5
        return cls(period, *cols)

    @classmethod
    def from_csv(cls, period, path):
        df = pd.read_csv(path)
        return cls(period, df["time"].values, df["open"].values, df["high"].values,
                   df["low"].values, df["close"].values, df["tick_volume"].values,
                   df["spread"].values)

    def __len__(self):
        return len(self.time)

    def index(self, t):
        i = int(np.searchsorted(self.time, t))
        return i if i < len(self.time) and self.time[i] == t else None

    def bar(self, t):
        i = self.index(t)
        if i is None:
            return None
        return {"time": int(self.time[i]), "open": float(self.open[i]), "high": float(self.high[i]),
                "low": float(self.low[i]), "close": float(self.close[i])}

    def open_at(self, t):
        """Open of the bar stamped exactly t, or None. Never forward-filled (C1.6)."""
        i = self.index(t)
        return None if i is None else float(self.open[i])

    def span(self, t0, t1):
        """Index range of bars with open in [t0, t1)."""
        return int(np.searchsorted(self.time, t0)), int(np.searchsorted(self.time, t1))

    def frame(self, i0, i1):
        """Bars i0..i1-1 in the live data_feed format."""
        return pd.DataFrame({
            "datetime": pd.to_datetime(self.time[i0:i1], unit="s", utc=True),
            "open": self.open[i0:i1], "high": self.high[i0:i1], "low": self.low[i0:i1],
            "close": self.close[i0:i1], "volume": self.volume[i0:i1],
            "spread": self.spread[i0:i1], "real_volume": 0,
        }).reset_index(drop=True)


def strategy_window(m15, signal_bar_open, count=200):
    """The `count` closed M15 bars ending at the signal bar (C2.1), sealed."""
    i = m15.index(signal_bar_open)
    if i is None:
        raise KeyError(f"no M15 bar at {signal_bar_open}")
    i0 = max(0, i + 1 - count)
    guard(int(m15.time[i0]), int(m15.time[i]) + m15.period)
    return m15.frame(i0, i + 1)


def minutes_of(m1, b0):
    """Observed M1 bars of the M15 slot b0 as (t, o, h, l, c)."""
    i0, i1 = m1.span(b0, b0 + M15)
    return [(int(m1.time[i]), float(m1.open[i]), float(m1.high[i]), float(m1.low[i]), float(m1.close[i]))
            for i in range(i0, i1)]


def rebuild(minutes):
    """M15 OHLC from its observed minutes (C6.3(a))."""
    return (minutes[0][1], max(m[2] for m in minutes), min(m[3] for m in minutes), minutes[-1][4])


def envelope_valid(bar, minutes):
    """C6.3(b) on an M15 bar dict and its observed minutes."""
    if any(h > bar["high"] or l < bar["low"] for _, _, h, l, _ in minutes):
        return False
    if minutes and minutes[0][0] == bar["time"] and minutes[0][1] != bar["open"]:
        return False
    if minutes and minutes[-1][0] == bar["time"] + M15 - M1 and minutes[-1][4] != bar["close"]:
        return False
    return True


def c63(m15, m1, sched, t0, t1):
    """Both C6.3 checks on the M15 bars with open in [t0, t1), sealed.
    Returns {"complete", "mismatch", "with_holes", "envelope_fail", "orphan"},
    the last three as lists of bar opens; orphan = M15 bar with no M1 minute."""
    guard(t0, t1)
    out = {"complete": 0, "mismatch": [], "with_holes": 0, "envelope_fail": [], "orphan": []}
    i0, i1 = m15.span(t0, t1)
    for i in range(i0, i1):
        b0 = int(m15.time[i])
        bar = m15.bar(b0)
        minutes = minutes_of(m1, b0)
        if not minutes:
            out["orphan"].append(b0)
            continue
        seen = {m[0] for m in minutes}
        holes = any(sched.scheduled(t) and t not in seen for t in range(b0, b0 + M15, M1))
        if holes:
            out["with_holes"] += 1
            if not envelope_valid(bar, minutes):
                out["envelope_fail"].append(b0)
        else:
            out["complete"] += 1
            if rebuild(minutes) != (bar["open"], bar["high"], bar["low"], bar["close"]):
                out["mismatch"].append(b0)
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_snapshot(data_dir=DEFAULT_DIR):
    """Both timeframes, after checking every hash against the manifest."""
    data_dir = Path(data_dir)
    manifest = json.loads((data_dir / "manifest.json").read_text())
    files = manifest["qc"]["files"]
    for name in ("M15", "M1"):
        got = sha256(data_dir / files[name]["file"])
        if got != files[name]["sha256"]:
            raise SnapshotMismatch(f"{name}: {got} != manifest {files[name]['sha256']}")
    snap = hashlib.sha256("".join(
        "%s %s\n" % (files[k]["file"], files[k]["sha256"]) for k in ("M15", "M1")).encode()).hexdigest()
    if snap != manifest["qc"]["data_snapshot_hash"]:
        raise SnapshotMismatch(f"data_snapshot_hash {snap} != manifest")
    return {
        "m15": Bars.from_csv(M15, data_dir / files["M15"]["file"]),
        "m1": Bars.from_csv(M1, data_dir / files["M1"]["file"]),
        "identity": {"snapshot_id": data_dir.name, "data_snapshot_hash": snap,
                     "m15_sha256": files["M15"]["sha256"], "m1_sha256": files["M1"]["sha256"]},
    }
