"""Spread draw (contract C3.2, C3.3).

u depends only on the signal bar's server-stamped close, so it is identical
across delay runs (common random numbers). The draw is the type-1 inverse
empirical CDF over sorted integer points, with no interpolation. The artefact
itself is TBD-3; tests supply synthetic ones.
"""
import hashlib
import json
import math
from pathlib import Path

KEY_PREFIX = "hermes-ec-spread-v1|"
SESSIONS = ("ASIA", "LONDON", "NY", "OFF")


def u_of(bar_close_epoch):
    key = (KEY_PREFIX + str(int(bar_close_epoch))).encode("ascii")
    k = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
    return (k + 0.5) / 2 ** 64


def rank(u, n):
    """1-based rank ceil(u * n), Hyndman-Fan type 1."""
    return max(1, min(n, math.ceil(u * n)))


def quantile_type1(sorted_points, u):
    return sorted_points[rank(u, len(sorted_points)) - 1]


class SpreadModel:
    """Per-session sorted spreads in integer points, hashed canonically."""

    def __init__(self, by_session, source="synthetic"):
        self.by_session = {s: sorted(int(x) for x in v) for s, v in by_session.items()}
        self.source = source
        blob = json.dumps({"source": source, "by_session": self.by_session},
                          sort_keys=True, separators=(",", ":")).encode()
        self.hash = hashlib.sha256(blob).hexdigest()

    @classmethod
    def load(cls, path):
        d = json.loads(Path(path).read_text())
        m = cls(d["by_session"], d.get("source", str(path)))
        if d.get("sha256") and d["sha256"] != m.hash:
            raise ValueError(f"spread artefact hash {m.hash} != recorded {d['sha256']}")
        return m

    def draw(self, bar_close_epoch, session):
        """(u, points). The session is that of E (C3.3 step 5)."""
        u = u_of(bar_close_epoch)
        return u, quantile_type1(self.by_session[session], u)


def draw_for_bar(model, bar_open, e):
    """C3.3 for one opportunity: u from the signal bar's close, distribution of
    the session of E. Returns (u, points, session)."""
    from replay import clock
    session = clock.session_of(e)
    u, points = model.draw(bar_open + 900, session)
    return u, points, session


def ask_from(bid, points, point=0.01, digits=2):
    """C3.3 step 6: ask = round(bid + points * point, digits)."""
    return round(bid + points * point, digits)
