"""Holdout unseal and run family (PREREGISTRATION.md pr-0.4 F6, H1, H2;
section 9 tests 1 and 7).

data.guard refuses every span overlapping the holdout unless it is handed a
Permit. Permits are issued by replay/holdout_run.Session, the single holdout
entry point, from an Unseal; an Unseal comes only from unseal(): a committed
development PASS, a committed go-ahead bound to it, a confirmation typed at a
terminal, the clean engine read from git, the frozen inputs the verdict was
reached on, and an unseal log with no earlier entry. The log is append-only
JSON lines and its first entry spends the unseal for good (H1, H4).

Interpretations, to be confirmed in review (none changes a frozen clause):
- K1. "The commit of that development verdict": a commit reachable from HEAD
  whose tree holds VERDICT_PATH with verdict PASS and the configuration's
  config_hash. verdict_at() reads it from git; tests pass their own lookup.
- K2 [v2]. The user's explicit written go-ahead is GO_AHEAD_PATH, committed in
  a descendant of the verdict commit, whose text holds GO_AHEAD_PHRASE, the
  verdict commit and the config_hash. unseal() additionally requires the
  config_hash typed at an interactive terminal (stdin is a TTY). Procedure:
  only the owner writes and commits the go-ahead and runs the unseal, on the
  VPS. No check on a single-user machine proves who typed; the bindings make
  a go-ahead unusable for any other verdict or configuration, and the TTY
  requirement keeps a non-interactive tool run from unsealing.
- K3 [v3]. "One unseal session from one engine commit": the engine is read
  from the repository, never supplied. current_engine() is the full HEAD sha,
  with "-dirty" if any tracked file differs or any untracked file is not a
  holdout output (a data file, OUTPUT_SUFFIXES, under research/p1/; code is
  never exempt). unseal() records it; every permit issued and every
  portfolio.run() under a permit require it unchanged and clean. Outputs are committed after the session:
  a commit during it changes HEAD and ends the session.
- K4 [v2]. The H2 family is a closed set of RunSpec values for the committed
  config_hash. Strategy runs: the primary settings (delay 480, the frozen
  spread draw) on path S, T or C, or exactly one sensitivity on path S: a G7
  delay or the G8 constant session p90. Random runs (B2): portfolios 0-999 at
  the primary settings. Optimistic ordering (C5.3) and intraminute-adverse
  marks (C5.10) are not separate executions and have no RunSpec: they are
  derived from the primary S run's report, which records the AMBIGUOUS flags
  and the adverse marks. Optimistic ordering re-prices each AMBIGUOUS trade at
  its TP with the run's decisions held fixed, so a changed daily-loss gate
  state is not modelled; the AMBIGUOUS trades on days where that gate blocked
  a later signal are counted beside it. Everything else is refused, B3, B4
  (spread zero) and combined sensitivities included.
- K5 [v3]. portfolio.check_permit enforces the permit on the run itself:
  SIMULATION mode, the running engine (K3), the path, a flat starting book,
  every Context field (data, schedule, spread model, configuration, broker
  spec, delay, offset window, data end) identical to the context the entry
  point built, and the opportunities equal to the canonical ones the permit
  carries: for a strategy run every signal of the fold as the entry point's
  scan produced it, for B2 the draw of exactly one portfolio index below
  N_PORTFOLIOS. A per-opportunity delay, a dropped or added signal, another
  portfolio's draw or a second portfolio in one book all differ from it.
- K6 [v4]. Inputs are bound, not supplied: unseal() requires the inputs'
  identity (data_snapshot_hash, broker_spec_version, broker_spec_sha256,
  spread_model_hash, config_hash) to equal the identity recorded in the
  development verdict, and records it. holdout_run.load_inputs builds them
  from the manifest-checked snapshot, the broker spec file and the frozen
  spread artefact. Every hash in the identity is computed from content:
  broker_spec_sha256 is the SHA-256 of the spec as loaded (entry.spec_hash),
  and entry.BrokerSpec refuses a file whose recorded sha256 or version label
  differs from it, so no field read by the replay (contract size, margin
  rate, volume limits, point, digits) can change under the same label. Disclosed limit: these are in-process Python checks. They stop a
  holdout run that deviates by mistake through the public path; they cannot
  stop code that deliberately builds a Permit or Inputs by hand. The
  procedure closes that: the holdout is run only by the committed command
  python -m replay.holdout_run on the clean session engine (K3), and the P1
  record is built only from the run log it writes.
- K9 [v5]. "Once" holds across processes: the unseal log and the run log
  are each created by claim(), an exclusive create (O_CREAT | O_EXCL, then
  the first line written and fsynced). It is the only test of "unsealed
  before" and "family started before", so there is no check-then-append
  window: of two concurrent unseal (or run) commands exactly one creates
  the file, the other is refused before it reads a holdout bar. A file that
  exists counts, whatever it holds: a crash between create and write
  leaves an empty file, which is consumed, not reusable. The logs live on
  the VPS's local disk, where O_EXCL is atomic (not guaranteed on NFS).

Module-level functions claim, previous_unseals, verdict_ok, go_ahead_ok, identity_ok,
in_family, same_engine, clean, is_dirty and current_engine are the seams the
tests patch with broken variants.
"""
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from replay import attempts, clock, spread

ROOT = Path(__file__).resolve().parents[1]
UNSEAL_LOG = ROOT / "research" / "p1" / "holdout_unseal_log.jsonl"
VERDICT_PATH = "research/p1/development_verdict.json"
GO_AHEAD_PATH = "research/p1/holdout_go_ahead.txt"
GO_AHEAD_PHRASE = "I authorise unsealing the Hermes holdout"
UNTRACKED_OK = ("research/p1/",)
OUTPUT_SUFFIXES = (".json", ".jsonl", ".txt", ".csv")
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


@dataclass(frozen=True, eq=False)
class Inputs:
    """K6: what every holdout run reads, with the identity that binds it."""
    m1: object
    m15: object
    sched: object
    spec: object
    model: object
    params: dict
    identity: dict


@dataclass(frozen=True, eq=False)
class Permit:
    """One H2 run. context: the portfolio.Context the entry point built
    (permit None). canonical: index -> the run's opportunities; None for the
    entry point's own signal scan, which may read but not run."""
    spec: RunSpec
    verdict_commit: str
    engine_commit: str
    context: object
    canonical: object = None


@dataclass(frozen=True)
class Unseal:
    verdict_commit: str
    config_hash: str
    engine_commit: str
    spread_hashes: dict
    identity: dict
    entry: dict


def identity_of(snapshot_hash, spec, model, params):
    """K6: the identity of a set of inputs."""
    return {"data_snapshot_hash": snapshot_hash, "broker_spec_version": spec.version,
            "broker_spec_sha256": spec.sha256, "spread_model_hash": model.hash,
            "config_hash": attempts.config_hash(params)}


def identity_ok(rec, identity):
    """K6: the inputs are those the development verdict was reached on."""
    return bool(identity) and rec.get("identity") == identity and identity["config_hash"] == rec["config_hash"]


def spread_hashes(model):
    """The draw model's hash and that of its constant session p90 (G8)."""
    return {"draw": model.hash, "p90": spread.constant_model(model, 0.90).hash}


def claim(path, first_line):
    """K9: create path holding first_line, atomically and exclusively
    (O_CREAT | O_EXCL); False if path exists, whatever it holds. Of any
    number of processes claiming one path, exactly one succeeds."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(first_line)
        f.flush()
        os.fsync(f.fileno())
    return True


def previous_unseals(log_path):
    path = Path(log_path)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _git(args, repo=ROOT):
    return subprocess.run(["git"] + args, cwd=repo, capture_output=True, text=True, timeout=10)


def verdict_at(commit, repo=ROOT):
    """K1: the development verdict record at commit, or None."""
    if _git(["merge-base", "--is-ancestor", commit, "HEAD"], repo).returncode != 0:
        return None
    out = _git(["show", f"{commit}:{VERDICT_PATH}"], repo)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def go_ahead_at(commit, verdict_commit, repo=ROOT):
    """K2: the go-ahead text at commit, if commit descends from the verdict
    commit and is reachable from HEAD; else None."""
    for a, b in ((verdict_commit, commit), (commit, "HEAD")):
        if _git(["merge-base", "--is-ancestor", a, b], repo).returncode != 0:
            return None
    out = _git(["show", f"{commit}:{GO_AHEAD_PATH}"], repo)
    return out.stdout if out.returncode == 0 else None


def verdict_ok(rec):
    return bool(rec) and rec.get("verdict") == "PASS" and bool(rec.get("config_hash"))


def go_ahead_ok(text, verdict_commit, config_hash):
    """K2: the go-ahead names this verdict and this configuration."""
    return bool(text) and all(s in text for s in (GO_AHEAD_PHRASE, verdict_commit, config_hash))


def tty_confirm(config_hash):
    """K2: the config_hash typed at an interactive terminal."""
    if not sys.stdin.isatty():
        return False
    return input(f"Type the config_hash {config_hash} to unseal the holdout: ").strip() == config_hash


def output_file(path):
    """K3: an untracked holdout output, never code."""
    return path.startswith(UNTRACKED_OK) and path.endswith(OUTPUT_SUFFIXES)


def is_dirty(porcelain_lines):
    """K3: a tracked change, or an untracked file that is not a holdout output."""
    return any(not (x.startswith("?? ") and output_file(x[3:])) for x in porcelain_lines if x)


def current_engine(repo=ROOT):
    """K3: the running engine, read from the repository."""
    head = _git(["rev-parse", "HEAD"], repo)
    st = _git(["status", "--porcelain", "--untracked-files=all"], repo)
    if head.returncode != 0 or st.returncode != 0:
        return "unknown"
    return head.stdout.strip() + ("-dirty" if is_dirty(st.stdout.splitlines()) else "")


def clean(commit):
    return bool(commit) and not commit.endswith("-dirty") and commit != "unknown"


def same_engine(a, b):
    return a == b and clean(a)


def check_engine(session_commit):
    """K3: the engine running now is the session's clean commit."""
    running = current_engine()
    if not same_engine(running, session_commit):
        raise HoldoutRefused(f"running engine {running} != unseal session {session_commit} (H2)")


def in_family(spec, config_hash):
    """K4: the closed H2 family for the committed configuration."""
    if spec.config_hash != config_hash or spec.scenario not in SCENARIOS:
        return False
    primary = spec.delay == PRIMARY_DELAY and spec.spread == "draw"
    if spec.kind == "random":
        return primary
    if spec.kind != "strategy":
        return False
    if primary:
        return True
    one = ((spec.delay in G7_DELAYS and spec.spread == "draw")
           or (spec.delay == PRIMARY_DELAY and spec.spread == "p90"))
    return one and spec.scenario == "S"


def unseal(verdict_commit, go_ahead_commit, inputs, log_path=UNSEAL_LOG,
           lookup=verdict_at, go_ahead_lookup=go_ahead_at, confirm=None):
    """H1: log one valid unseal and return it; refuse anything else.
    confirm defaults to tty_confirm, resolved at call time."""
    confirm = confirm or tty_confirm
    rec = lookup(verdict_commit)
    if not verdict_ok(rec):
        raise HoldoutRefused(f"{verdict_commit} holds no committed development PASS")
    text = go_ahead_lookup(go_ahead_commit, verdict_commit)
    if not go_ahead_ok(text, verdict_commit, rec["config_hash"]):
        raise HoldoutRefused(f"{go_ahead_commit} holds no committed go-ahead for {verdict_commit} (H1, K2)")
    engine_commit = current_engine()
    if not clean(engine_commit):
        raise HoldoutRefused(f"running engine {engine_commit!r} is not a clean commit (K3)")
    if not identity_ok(rec, inputs.identity):
        raise HoldoutRefused(f"inputs {inputs.identity} != the verdict's {rec.get('identity')} (K6)")
    if not confirm(rec["config_hash"]):
        raise HoldoutRefused("the unseal was not confirmed at an interactive terminal (K2)")
    hashes = spread_hashes(inputs.model)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "verdict_commit": verdict_commit,
             "config_hash": rec["config_hash"], "engine_commit": engine_commit,
             "go_ahead_commit": go_ahead_commit, "go_ahead": text, "spread_hashes": hashes,
             "identity": inputs.identity}
    if not claim(log_path, json.dumps(entry, sort_keys=True) + "\n"):
        raise SecondUnseal(f"the holdout was unsealed before ({log_path})")
    return Unseal(verdict_commit, rec["config_hash"], engine_commit, hashes, inputs.identity, entry)


def session(log_path=UNSEAL_LOG):
    """The logged unseal, to continue its session in another process."""
    done = previous_unseals(log_path)
    if len(done) != 1:
        raise HoldoutRefused(f"{len(done)} unseal entries in {log_path}")
    e = done[0]
    return Unseal(e["verdict_commit"], e["config_hash"], e["engine_commit"], e["spread_hashes"], e["identity"], e)
