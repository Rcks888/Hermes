"""Holdout unseal and run family (PREREGISTRATION.md pr-0.4 F6, H1, H2;
section 9 tests 1 and 7).

data.guard refuses every span overlapping the holdout unless it is handed a
Permit. A Permit comes only from Unseal.permit, an Unseal only from unseal():
the commit of a committed development PASS, the user's written go-ahead, and
an unseal log with no earlier entry. The log is append-only JSON lines and its
first entry spends the unseal for good (H1, H4).

Interpretations, to be confirmed in review (none changes a frozen clause):
- K1. "The commit of that development verdict": a commit reachable from HEAD
  whose tree holds VERDICT_PATH with verdict PASS and the configuration's
  config_hash. verdict_at() reads it from git; tests pass their own lookup.
- K2. The go-ahead is recorded verbatim. The engine cannot verify who wrote
  it; it refuses an empty one.
- K3. "One unseal session from one engine commit": the commit is recorded at
  unsealing and every permit requires the same clean commit.
- K4. The H2 family is a closed set of RunSpec values for the committed
  config_hash. Strategy runs: the primary settings (delay 480, spread draw,
  pessimistic ordering, closing-leg marks) on path S, T or C, or exactly one
  reported deviation from them on path S: a G7 delay, the G8 p90 spread,
  optimistic ordering, or intraminute-adverse marks. Random runs (B2): 1,000
  portfolios at the primary settings. Everything else is refused, B3, B4
  (spread zero) and combined sensitivities included.
- K5. portfolio.run checks a permit against its context: config_hash, delay
  and scenario. Spread model, ordering and marks are carried in the permit
  and recorded in the run identity.

Module-level functions previous_unseals, verdict_ok, in_family and
same_engine are the seams the tests patch with broken variants.
"""
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from replay import clock

ROOT = Path(__file__).resolve().parents[1]
UNSEAL_LOG = ROOT / "research" / "p1" / "holdout_unseal_log.jsonl"
VERDICT_PATH = "research/p1/development_verdict.json"
PRIMARY_DELAY = clock.BASELINE_DELAY
G7_DELAYS = tuple(d for d in clock.PREREGISTERED_DELAYS if d != PRIMARY_DELAY)
N_PORTFOLIOS = 1000
SCENARIOS = ("S", "T", "C")


class HoldoutRefused(Exception):
    """An unseal or a holdout run that H1/H2 does not permit."""


class SecondUnseal(HoldoutRefused):
    """The holdout has been unsealed before (H1)."""


@dataclass(frozen=True)
class RunSpec:
    config_hash: str
    kind: str = "strategy"
    delay: int = PRIMARY_DELAY
    spread: str = "draw"
    scenario: str = "S"
    ordering: str = "pessimistic"
    marks: str = "close"
    n_portfolios: int = None


PRIMARY = {"delay": PRIMARY_DELAY, "spread": "draw", "ordering": "pessimistic", "marks": "close"}


@dataclass(frozen=True)
class Permit:
    spec: RunSpec
    verdict_commit: str
    engine_commit: str


@dataclass(frozen=True)
class Unseal:
    verdict_commit: str
    config_hash: str
    engine_commit: str
    entry: dict

    def permit(self, spec, engine_commit):
        """A Permit for one H2 run, or HoldoutRefused (K3, K4)."""
        if not same_engine(engine_commit, self.engine_commit):
            raise HoldoutRefused(f"engine {engine_commit} != unseal session {self.engine_commit} (H2)")
        if not in_family(spec, self.config_hash):
            raise HoldoutRefused(f"{spec} is not in the H2 run family")
        return Permit(spec, self.verdict_commit, self.engine_commit)


def previous_unseals(log_path):
    path = Path(log_path)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def verdict_at(commit, repo=ROOT):
    """K1: the development verdict record at commit, or None."""
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=repo,
                         capture_output=True, timeout=10)
    if anc.returncode != 0:
        return None
    out = subprocess.run(["git", "show", f"{commit}:{VERDICT_PATH}"], cwd=repo,
                         capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def verdict_ok(rec):
    return bool(rec) and rec.get("verdict") == "PASS" and bool(rec.get("config_hash"))


def clean(commit):
    return bool(commit) and not commit.endswith("-dirty") and commit != "unknown"


def same_engine(a, b):
    return a == b and clean(a)


def in_family(spec, config_hash):
    """K4: the closed H2 family for the committed configuration."""
    if spec.config_hash != config_hash or spec.scenario not in SCENARIOS:
        return False
    settings = {"delay": spec.delay, "spread": spec.spread, "ordering": spec.ordering,
                "marks": spec.marks}
    off = {k for k, v in settings.items() if v != PRIMARY[k]}
    if spec.kind == "random":
        return not off and spec.n_portfolios == N_PORTFOLIOS
    if spec.kind != "strategy" or spec.n_portfolios is not None:
        return False
    if not off:
        return True
    if len(off) != 1 or spec.scenario != "S":
        return False
    (k,) = off
    return {"delay": spec.delay in G7_DELAYS, "spread": spec.spread == "p90",
            "ordering": spec.ordering == "optimistic", "marks": spec.marks == "adverse"}[k]


def unseal(verdict_commit, go_ahead, engine_commit, log_path=UNSEAL_LOG, lookup=verdict_at):
    """H1: log one valid unseal and return it; refuse anything else."""
    if previous_unseals(log_path):
        raise SecondUnseal(f"the holdout was unsealed before ({log_path})")
    rec = lookup(verdict_commit)
    if not verdict_ok(rec):
        raise HoldoutRefused(f"{verdict_commit} holds no committed development PASS")
    if not go_ahead or not str(go_ahead).strip():
        raise HoldoutRefused("the user's written go-ahead is required (H1)")
    if not clean(engine_commit):
        raise HoldoutRefused(f"engine commit {engine_commit!r} is not a clean commit")
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "verdict_commit": verdict_commit,
             "config_hash": rec["config_hash"], "engine_commit": engine_commit, "go_ahead": str(go_ahead)}
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    return Unseal(verdict_commit, rec["config_hash"], engine_commit, entry)


def session(log_path=UNSEAL_LOG):
    """The logged unseal, to continue its session in another process."""
    done = previous_unseals(log_path)
    if len(done) != 1:
        raise HoldoutRefused(f"{len(done)} unseal entries in {log_path}")
    e = done[0]
    return Unseal(e["verdict_commit"], e["config_hash"], e["engine_commit"], e)
