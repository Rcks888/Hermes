"""Attempt ledger (PREREGISTRATION.md pr-0.4 M1-M3, variant menu, section 9
test 6).

Attempt 0 is the frozen baseline, registered in pr-0.4 itself by its
config_hash. Every further attempt is exactly one menu variant, at most
N_FURTHER of them, each appended to the ledger (JSON lines) and committed
before its run. authorise() is the only way a run gets its G2 level.

Interpretations, to be confirmed in review (none changes a frozen clause):
- L1. A configuration is classified by its difference from the baseline
  parameters: no difference is attempt 0; exactly one key at exactly a menu
  value is that variant; anything else is off-menu and rejected.
- L2. V1 (D in 12:00-16:00 UTC only) has no baseline parameter, so it is
  represented by one added key, session_window_utc = [12, 16]. The engine
  must honour that key before V1 can run; adding that support is research
  infrastructure (M1), not an attempt.
- L3. "a prior commit of its config_hash": the hash appears in a ledger line
  added by a commit in the history of HEAD. committed_hashes() reads that
  from git; tests pass their own.
- L4. A variant is registered once. Rerunning it after an implementation
  defect (M1) supersedes its result and does not register it again.

Module-level functions classify, alpha_of and is_committed are the seams the
tests patch with broken variants.
"""
import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_PARAMS = ROOT / "config" / "strategy_params.json"
LEDGER = ROOT / "research" / "p1" / "attempt_ledger.jsonl"
REGISTERED_BASELINE = "265c3dd24d68"
N_FURTHER = 3
ALPHA_BASELINE, ALPHA_VARIANT = 0.025, 0.003125
MENU = {
    "V1": ("session_window_utc", [12, 16]),
    "V2": ("tp_rr_multiple", 1.5),
    "V3": ("tp_rr_multiple", 2.5),
    "V4": ("sl_atr_high", 2.0),
    "V5": ("max_spread_points", 40),
    "V6": ("volume_threshold_pullback", 1.0),
    "V7": ("sr_min_touches", 3),
    "V8": ("momentum_candle_multiplier", 1.5),
}


class OffMenu(Exception):
    """A configuration or registration outside the pr-0.4 menu."""


class NotCommitted(Exception):
    """An attempt whose config_hash was not committed before its run (M3)."""


def config_hash(params):
    """run_hermes.config_fingerprint, the live event's config_hash."""
    blob = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def base_params():
    return json.loads(BASE_PARAMS.read_text())


def variant_params(vid, base=None):
    if vid not in MENU:
        raise OffMenu(f"{vid} is not a menu variant")
    key, value = MENU[vid]
    return dict(base or base_params(), **{key: value})


def classify(params, base):
    """L1: None for the baseline, a menu id for a single menu change."""
    diff = sorted(k for k in set(params) | set(base) if params.get(k) != base.get(k))
    if not diff:
        return None
    for vid, (key, value) in MENU.items():
        if diff == [key] and params[key] == value:
            return vid
    raise OffMenu(f"changes {diff} are not exactly one menu variant")


def alpha_of(vid):
    """M2: the G2 level is fixed per configuration."""
    return ALPHA_BASELINE if vid is None else ALPHA_VARIANT


def entries(path=LEDGER):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def register(vid, rationale, path=LEDGER, base=None):
    """Append attempt k = 1..N_FURTHER for menu variant vid (M1, M3)."""
    if vid not in MENU:
        raise OffMenu(f"{vid!r} is not a menu variant")
    if not rationale or not str(rationale).strip():
        raise OffMenu("an attempt needs its rationale from the diagnosis (M3)")
    done = entries(path)
    if any(e["variant"] == vid for e in done):
        raise OffMenu(f"{vid} is already registered (L4)")
    if len(done) >= N_FURTHER:
        raise OffMenu(f"at most {N_FURTHER} further attempts (M1)")
    rec = {"attempt": len(done) + 1, "variant": vid,
           "config_hash": config_hash(variant_params(vid, base)), "rationale": str(rationale),
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def committed_hashes(path=LEDGER, repo=ROOT):
    """L3: config hashes in ledger lines added by commits reachable from HEAD."""
    rel = Path(path).resolve().relative_to(Path(repo).resolve())
    out = subprocess.run(["git", "log", "-p", "--format=", "--", str(rel)], cwd=repo,
                         capture_output=True, text=True, timeout=30)
    found = set()
    for line in out.stdout.splitlines():
        if line.startswith("+{"):
            try:
                found.add(json.loads(line[1:])["config_hash"])
            except (ValueError, KeyError):
                continue
    return found


def is_committed(h, committed):
    return h in committed


def authorise(params, path=LEDGER, committed=None, base=None):
    """The run's attempt, variant and G2 level, or an exception (M1-M3)."""
    base = base or base_params()
    vid = classify(params, base)
    h = config_hash(params)
    if vid is None:
        if h != REGISTERED_BASELINE:
            raise OffMenu(f"baseline hash {h} != registered {REGISTERED_BASELINE}")
        return {"attempt": 0, "variant": None, "config_hash": h, "alpha": alpha_of(None)}
    rec = next((e for e in entries(path) if e["config_hash"] == h), None)
    if rec is None:
        raise NotCommitted(f"{vid} ({h}) is not in the attempt ledger")
    committed = committed_hashes(path) if committed is None else committed
    if not is_committed(h, committed):
        raise NotCommitted(f"{vid} ({h}) has no prior commit (M3)")
    return {"attempt": rec["attempt"], "variant": vid, "config_hash": h, "alpha": alpha_of(vid)}
