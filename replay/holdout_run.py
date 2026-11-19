"""The single holdout entry point (PREREGISTRATION.md pr-0.4 H2; holdout K5-K8).

Every holdout run is made here. Session takes the unseal and Inputs bound to
it (K6) and nothing else: it builds each run's Context itself, generates the
opportunities itself (the strategy's signal at every M15 bar of the holdout
fold; B2 drawn from the matching of its own primary S strategy run) and
issues one Permit per run carrying both. portfolio.check_permit refuses a run
whose context or opportunities differ.

    python -m replay.holdout_run unseal <verdict_commit> <go_ahead_commit> <spread_artefact>
    python -m replay.holdout_run run <spread_artefact>

Interpretations, to be confirmed in review:
- K7 [v4]. "H2 is run once": the run log research/p1/holdout_runs.jsonl is
  the family's lifecycle. run_family() opens the family only on an empty
  log (a family_start line), makes each run of the family once, and closes
  it (a family_complete line holding the sha256 of every run line) when all
  are logged. A log with any line, complete or interrupted, is never
  started again, and permits are issued only while the family is open. An
  interrupted family stays as logged; what follows it is a Reviewer
  decision, not a rerun by the command.
- K8 [v4]. The log is the evidence: each run line holds the run in full,
  losslessly encoded (encode/decode): for a strategy run every record with
  its resolved trade (outcome, R, AMBIGUOUS and other flags, hole decisions,
  marks and adverse marks), for B2 every portfolio's statistic and C count.
  read_family() rebuilds the family from the log alone, refusing an
  incomplete, reordered, altered or foreign log; the P1 record and the
  deferred K4 diagnostics are built only from what it returns, with the
  inputs bound by the unseal identity (K6).

Module-level functions bound, family, family_started, issuing, evidence,
encode, complete, in_order and intact, and Session._once, are the seams the
tests patch with broken variants.
"""
import dataclasses
import hashlib
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from replay import attempts, benchmark, clock, data, entry, exits, folds, holdout, portfolio, spread

FOLD = folds.HOLDOUT
RUN_LOG = holdout.ROOT / "research" / "p1" / "holdout_runs.jsonl"


def day_of(t):
    return datetime.fromtimestamp(int(t), timezone.utc).date()


def load_inputs(config_hash, model_path, data_dir=data.DEFAULT_DIR, spec_path=entry.BROKER_SPEC):
    """K6: the manifest-checked snapshot, the broker spec file, the frozen
    spread artefact and the committed configuration, with their identity."""
    snap = data.load_snapshot(data_dir)
    spec = entry.BrokerSpec.load(spec_path)
    model = spread.SpreadModel.load(model_path)
    params = attempts.params_of(config_hash)
    m1 = snap["m1"]
    sched = clock.schedule(day_of(m1.time[0]), day_of(m1.time[-1]))
    ident = holdout.identity_of(snap["identity"]["data_snapshot_hash"], spec, model, params)
    return holdout.Inputs(m1, snap["m15"], sched, spec, model, params, ident)


def family(config_hash):
    """K4: the strategy RunSpecs of H2, primary first; B2 is run per path."""
    specs = [holdout.RunSpec(config_hash, scenario=sc) for sc in holdout.SCENARIOS]
    specs += [holdout.RunSpec(config_hash, delay=d) for d in holdout.G7_DELAYS]
    specs.append(holdout.RunSpec(config_hash, spread="p90"))
    return specs


def bound(inputs, unseal):
    """K6: the inputs are the ones the unseal recorded."""
    return inputs.identity == unseal.identity


class Session:
    """All H2 runs of one unseal session (K7): opened once on an empty run
    log, each run of the family made once and logged in full as it is made,
    closed when the family is complete."""

    def __init__(self, unseal, inputs, log_path=RUN_LOG):
        if not bound(inputs, unseal):
            raise holdout.HoldoutRefused(f"inputs {inputs.identity} != unseal {unseal.identity} (K6)")
        self.unseal, self.inputs, self.log_path = unseal, inputs, Path(log_path)
        self.models = {"draw": inputs.model, "p90": spread.constant_model(inputs.model, 0.90)}
        got = {k: m.hash for k, m in self.models.items()}
        if got != unseal.spread_hashes:
            raise holdout.HoldoutRefused(f"spread models {got} != unseal {unseal.spread_hashes}")
        self.cache, self._primary, self.done, self.is_open = {}, None, {}, False

    def open(self):
        """K7: the family starts once, on an empty run log; a started family,
        complete or interrupted, is never started again."""
        if family_started(self.log_path):
            raise holdout.HoldoutRefused(f"the H2 family was started before ({self.log_path})")
        self._write({"event": START, "unseal": self.unseal.entry,
                     "family": [sp.__dict__ for sp in full_family(self.unseal.config_hash)]})
        self.is_open = True

    def close(self):
        """K7: the family ends when every run of it is logged."""
        missing = [sp for sp in full_family(self.unseal.config_hash) if sp not in self.done]
        if missing:
            raise holdout.HoldoutRefused(f"{len(missing)} runs of the family not made: {missing[:2]}")
        runs = [x for x in self.log_path.read_text().splitlines() if x.strip() and json.loads(x)["event"] == RUN]
        self._write({"event": COMPLETE, "runs": len(runs), "run_sha256": line_hashes(runs)})
        self.is_open = False

    def _write(self, line):
        line = dict(line, ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    engine_commit=self.unseal.engine_commit, verdict_commit=self.unseal.verdict_commit)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")

    def _record(self, spec, result):
        """K7, K8: a run is made once, and logged in full before it is returned."""
        self.done[spec] = result
        self._write({"event": RUN, "spec": spec.__dict__, "evidence": evidence(spec, result)})
        return result

    def context(self, spec):
        """The only Context a holdout run may use: built here, never supplied."""
        i = self.inputs
        c = portfolio.Context(i.m1, i.m15, i.sched, self.models[spec.spread], i.params, i.spec, spec.delay)
        return replace(c, data_end=folds.fold_end(FOLD, c))

    def permit(self, spec, canonical=None):
        """K3, K4: a Permit for one run of the family on the session's engine."""
        if not issuing(self):
            raise holdout.HoldoutRefused("holdout runs are made only inside the open H2 family (K7)")
        holdout.check_engine(self.unseal.engine_commit)
        if not holdout.in_family(spec, self.unseal.config_hash):
            raise holdout.HoldoutRefused(f"{spec} is not in the H2 run family")
        return holdout.Permit(spec, self.unseal.verdict_commit, self.unseal.engine_commit,
                              self.context(spec), canonical)

    def scan(self, spec):
        """Every signal of the fold, and its evaluated bars, under a scan permit."""
        p = self.permit(spec)
        return folds.strategy_opportunities(replace(p.context, permit=p), FOLD, self.cache)

    def _once(self, spec):
        if spec in self.done:
            raise holdout.HoldoutRefused(f"{spec} was run before in this family (K7)")

    def strategy(self, spec):
        """One strategy run of the family, logged; the primary S run is the
        one primary() returns."""
        if spec == holdout.RunSpec(self.unseal.config_hash):
            return self.primary()
        return self._strategy(spec)

    def _strategy(self, spec):
        self._once(spec)
        opps, bars = self.scan(spec)
        canon = tuple(opps)
        p = self.permit(spec, lambda i: canon)
        return self._record(spec, folds.run_fold(opps, p.context, FOLD, spec.scenario, permit=p)), bars

    def primary(self):
        """The primary S strategy run and its evaluated bars, run once."""
        if self._primary is None:
            self._primary = self._strategy(holdout.RunSpec(self.unseal.config_hash))
        return self._primary

    def random(self, scenario, n=holdout.N_PORTFOLIOS):
        """B2 on one path, matched on this session's primary S strategy run, logged."""
        spec = holdout.RunSpec(self.unseal.config_hash, kind="random", scenario=scenario)
        self._once(spec)
        primary, bars = self.primary()
        m = benchmark.matching(primary.records, bars, FOLD, spec.delay)
        ctx = self.context(spec)
        tp, digits = ctx.params.get("tp_rr_multiple", 2.0), ctx.spec.digits
        p = self.permit(spec, lambda i: benchmark.draw_signals(i, m, bars, tp, digits))
        out = benchmark.benchmark(primary.records, bars, replace(p.context, permit=p), FOLD, n, (scenario,))
        return self._record(spec, out)

    def run_family(self, n=holdout.N_PORTFOLIOS):
        """H2, once: open, every strategy run, B2 on S, T and C, close. n
        below N_PORTFOLIOS only for tests; the command always runs N_PORTFOLIOS."""
        self.open()
        for spec in family(self.unseal.config_hash):
            self.strategy(spec)
        for sc in exits.SCENARIOS:
            self.random(sc, n)
        self.close()
        return dict(self.done)


START, RUN, COMPLETE = "family_start", "run", "family_complete"


def full_family(config_hash):
    """Every run of H2: the strategy RunSpecs, then B2 on each path."""
    return family(config_hash) + [holdout.RunSpec(config_hash, kind="random", scenario=sc)
                                  for sc in exits.SCENARIOS]


def log_lines(log_path):
    path = Path(log_path)
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def family_started(log_path):
    """K7: any line in the run log means the family was started."""
    return bool(log_lines(log_path))


# ---------------------------------------------------------------- evidence (K8)

def encode(x):
    """Lossless JSON form of a run: dataclasses, sets and tuples are tagged so
    that decode() returns objects equal to the originals. Anything else that
    JSON cannot hold is refused, never dropped."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        if type(x).__name__ not in DATACLASSES:
            raise TypeError(f"no evidence form for {type(x).__name__}")
        return {"__dataclass__": type(x).__name__,
                "fields": {f.name: encode(getattr(x, f.name)) for f in dataclasses.fields(x)}}
    if isinstance(x, (set, frozenset)):
        return {"__set__": sorted(encode(v) for v in x)}
    if isinstance(x, tuple):
        return {"__tuple__": [encode(v) for v in x]}
    if isinstance(x, list):
        return [encode(v) for v in x]
    if isinstance(x, dict):
        if not all(isinstance(k, str) for k in x) or any(k.startswith("__") for k in x):
            raise TypeError(f"no evidence form for dict keys {list(x)[:3]}")
        return {k: encode(v) for k, v in x.items()}
    if isinstance(x, np.generic):
        return x.item()
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    raise TypeError(f"no evidence form for {type(x).__name__}")


def decode(x):
    if isinstance(x, list):
        return [decode(v) for v in x]
    if not isinstance(x, dict):
        return x
    if "__dataclass__" in x:
        return DATACLASSES[x["__dataclass__"]](**{k: decode(v) for k, v in x["fields"].items()})
    if "__set__" in x:
        return {decode(v) for v in x["__set__"]}
    if "__tuple__" in x:
        return tuple(decode(v) for v in x["__tuple__"])
    return {k: decode(v) for k, v in x.items()}


DATACLASSES = {"Result": exits.Result}


def evidence(spec, result):
    """K8: everything the run produced. A strategy run: every record, with
    its resolved trade (outcome, R, flags including AMBIGUOUS, decisions,
    marks, adverse marks). B2: every portfolio's statistic and C count."""
    if spec.kind == "random":
        stats, counts = result
        return {"stats": encode(stats), "counts": encode(counts)}
    return {"mode": result.mode, "scenario": result.scenario, "records": encode(result.records)}


def from_evidence(spec, ev):
    if spec.kind == "random":
        return decode(ev["stats"]), decode(ev["counts"])
    return portfolio.Run(ev["mode"], ev["scenario"], decode(ev["records"]))


def read_family(log_path=RUN_LOG, unseal_log=holdout.UNSEAL_LOG):
    """K8: the H2 family as logged, the only source of the P1 record and the
    deferred diagnostics. Refused unless the log is one complete family of
    the logged unseal, each run once, in order, unaltered since it closed."""
    lines = log_lines(log_path)
    u = holdout.session(unseal_log)
    if not lines or lines[0].get("event") != START or lines[0].get("unseal") != u.entry:
        raise holdout.HoldoutRefused(f"{log_path} does not start the family of the logged unseal")
    if not complete(lines):
        raise holdout.HoldoutRefused(f"{log_path}: the H2 family is incomplete (interrupted session)")
    runs = lines[1:-1]
    specs = [holdout.RunSpec(**ln["spec"]) for ln in runs if ln.get("event") == RUN]
    if len(specs) != len(runs) or not in_order(specs, u.config_hash):
        raise holdout.HoldoutRefused(f"{log_path}: runs {len(specs)} are not the H2 family in order")
    raw = [x for x in Path(log_path).read_text().splitlines() if x.strip()][1:-1]
    if not intact(lines[-1], raw):
        raise holdout.HoldoutRefused(f"{log_path}: run lines changed after the family closed")
    if any((ln["engine_commit"], ln["verdict_commit"]) != (u.engine_commit, u.verdict_commit) for ln in lines):
        raise holdout.HoldoutRefused(f"{log_path}: a line from another engine or verdict")
    return {sp: from_evidence(sp, ln["evidence"]) for sp, ln in zip(specs, runs)}


def line_hashes(raw_lines):
    return [hashlib.sha256(x.encode()).hexdigest() for x in raw_lines]


def complete(lines):
    """K7: the log ends with the family_complete line."""
    return lines[-1].get("event") == COMPLETE


def in_order(specs, config_hash):
    """K7: each run of the family exactly once, in the family's order."""
    return specs == full_family(config_hash)


def intact(complete_line, raw_runs):
    """K8: the run lines are those the family_complete line hashed."""
    return complete_line.get("run_sha256") == line_hashes(raw_runs)


def issuing(session):
    """K7: permits only while the family is open."""
    return session.is_open


def main(argv):
    if len(argv) == 4 and argv[0] == "unseal":
        rec = holdout.verdict_at(argv[1])
        if not holdout.verdict_ok(rec):
            print(f"{argv[1]} holds no committed development PASS")
            return 1
        u = holdout.unseal(argv[1], argv[2], load_inputs(rec["config_hash"], argv[3]))
        print(f"unsealed: {u.entry}")
        return 0
    if len(argv) == 2 and argv[0] == "run":
        u = holdout.session()
        Session(u, load_inputs(u.config_hash, argv[1])).run_family()
        print(f"H2 family complete; log {RUN_LOG}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
