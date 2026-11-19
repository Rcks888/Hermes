"""The single holdout entry point (PREREGISTRATION.md pr-0.4 H2; holdout K5, K6).

Every holdout run is made here. Session takes the unseal and Inputs bound to
it (K6) and nothing else: it builds each run's Context itself, generates the
opportunities itself (the strategy's signal at every M15 bar of the holdout
fold; B2 drawn from the matching of its own primary S strategy run) and
issues one Permit per run carrying both. portfolio.check_permit refuses a run
whose context or opportunities differ.

    python -m replay.holdout_run unseal <verdict_commit> <go_ahead_commit> <spread_artefact>
    python -m replay.holdout_run run <spread_artefact>

run executes the whole H2 family once and appends one line per run to
research/p1/holdout_runs.jsonl; the P1 record is built only from that log.
"""
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

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
    """All H2 runs of one unseal session."""

    def __init__(self, unseal, inputs):
        if not bound(inputs, unseal):
            raise holdout.HoldoutRefused(f"inputs {inputs.identity} != unseal {unseal.identity} (K6)")
        self.unseal, self.inputs = unseal, inputs
        self.models = {"draw": inputs.model, "p90": spread.constant_model(inputs.model, 0.90)}
        got = {k: m.hash for k, m in self.models.items()}
        if got != unseal.spread_hashes:
            raise holdout.HoldoutRefused(f"spread models {got} != unseal {unseal.spread_hashes}")
        self.cache, self._primary = {}, None

    def context(self, spec):
        """The only Context a holdout run may use: built here, never supplied."""
        i = self.inputs
        c = portfolio.Context(i.m1, i.m15, i.sched, self.models[spec.spread], i.params, i.spec, spec.delay)
        return replace(c, data_end=folds.fold_end(FOLD, c))

    def permit(self, spec, canonical=None):
        """K3, K4: a Permit for one run of the family on the session's engine."""
        holdout.check_engine(self.unseal.engine_commit)
        if not holdout.in_family(spec, self.unseal.config_hash):
            raise holdout.HoldoutRefused(f"{spec} is not in the H2 run family")
        return holdout.Permit(spec, self.unseal.verdict_commit, self.unseal.engine_commit,
                              self.context(spec), canonical)

    def scan(self, spec):
        """Every signal of the fold, and its evaluated bars, under a scan permit."""
        p = self.permit(spec)
        return folds.strategy_opportunities(replace(p.context, permit=p), FOLD, self.cache)

    def strategy(self, spec):
        opps, bars = self.scan(spec)
        canon = tuple(opps)
        p = self.permit(spec, lambda i: canon)
        return folds.run_fold(opps, p.context, FOLD, spec.scenario, permit=p), bars

    def primary(self):
        """The primary S strategy run and its evaluated bars, run once."""
        if self._primary is None:
            self._primary = self.strategy(holdout.RunSpec(self.unseal.config_hash))
        return self._primary

    def random(self, scenario, n=holdout.N_PORTFOLIOS):
        """B2 on one path, matched on this session's primary S strategy run."""
        primary, bars = self.primary()
        spec = holdout.RunSpec(self.unseal.config_hash, kind="random", scenario=scenario)
        m = benchmark.matching(primary.records, bars, FOLD, spec.delay)
        ctx = self.context(spec)
        tp, digits = ctx.params.get("tp_rr_multiple", 2.0), ctx.spec.digits
        p = self.permit(spec, lambda i: benchmark.draw_signals(i, m, bars, tp, digits))
        return benchmark.benchmark(primary.records, bars, replace(p.context, permit=p), FOLD, n, (scenario,))

    def run_family(self, log_path=RUN_LOG, n=holdout.N_PORTFOLIOS):
        """H2, once: every strategy run, then B2 on S, T and C. n below
        N_PORTFOLIOS only for tests; the command always runs N_PORTFOLIOS."""
        out = {}
        for spec in family(self.unseal.config_hash):
            run = self.primary()[0] if spec == holdout.RunSpec(self.unseal.config_hash) else self.strategy(spec)[0]
            out[spec] = run
            log(log_path, self.unseal, spec, {"records": len(run.records),
                                             "population_C": len(run.population("C"))})
        for sc in exits.SCENARIOS:
            stats, counts = self.random(sc, n)
            spec = holdout.RunSpec(self.unseal.config_hash, kind="random", scenario=sc)
            out[spec] = (stats, counts)
            log(log_path, self.unseal, spec, {"portfolios": len(stats)})
        return out


def log(log_path, unseal, spec, summary):
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine_commit": unseal.engine_commit,
            "verdict_commit": unseal.verdict_commit, "spec": spec.__dict__, "summary": summary}
    with path.open("a") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")


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
        print(f"H2 family run; log {RUN_LOG}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
