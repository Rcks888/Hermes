"""Code version stamping for the event trail.

Every event must be attributable to the code that produced it. Commit
cf4e3d4 (evaluate closed bars, not the forming bar) changed what the
strategy sees, so events either side of it describe different systems.
Without a stamp the only way to separate them is a remembered deploy
time, which is not evidence.
"""

import subprocess
from pathlib import Path

_cached = None


def get_code_version():
    """Short git SHA of the running tree, with -dirty if uncommitted.

    Cached per process. Returns "unknown" rather than raising if git is
    unavailable, since a missing version must never stop a trading cycle.
    """
    global _cached
    if _cached is not None:
        return _cached

    repo = Path(__file__).parent.parent
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=5,
        )
        if sha.returncode != 0:
            _cached = "unknown"
            return _cached
        version = sha.stdout.strip()

        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, timeout=5,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            version += "-dirty"

        _cached = version
    except Exception:
        _cached = "unknown"
    return _cached