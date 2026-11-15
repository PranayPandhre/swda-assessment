"""Evaluation harness: golden expectations (end-to-end + agent-level), cross-cutting invariants, and regression
checks against a committed baseline. All thresholds/expectations are data (config/evaluation.yaml, evals/)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import yaml

from .config import Settings
from .data import SourceLibrary
from .orchestrator import RunResult
from .runner import run_scenario
from .safeguards import Safeguards
from .schemas import PatternCard
from .trace import verify_trace


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class EvalReport:
    checks: list[Check] = field(default_factory=list)
    metrics: dict[str, dict] = field(default_factory=dict)
    fingerprints: dict[str, dict] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, bool(passed), detail))


def fingerprint(card: PatternCard) -> str:
    body = card.model_dump(mode="json", by_alias=True, exclude={"run_id", "card_id", "approval", "status"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def metrics_of(result: RunResult) -> dict:
    s = result.summary
    roles = s["roles"].values()
    return {"first_attempt_validity_min": min((r["first_attempt_validity"] for r in roles), default=1.0),
            "final_validity_min": min((r["final_validity"] for r in roles), default=1.0), "cards": len(result.cards),
            "quarantined": s["quarantined"], "boundary_violations": s["boundary_violations"], "tool_denied": s["tool_denied"],
            "cost_units": s["cost_units"]}


def _cmp(value, spec: dict) -> bool:
    return all([("eq" not in spec or value == spec["eq"]), ("min" not in spec or value >= spec["min"]),
                ("max" not in spec or value <= spec["max"])])


# ------------------------------------------------------------------ golden (end-to-end + agent-level)
def check_golden(rep: EvalReport, name: str, result: RunResult, spec: dict) -> None:
    s = result.summary
    rep.add(f"{name}/status", s["status"] == spec["status"], f"{s['status']} (expected {spec['status']})")
    cards = {c.pattern_key: c for c in result.cards}
    rep.add(f"{name}/pattern_set", set(cards) == set(spec["cards"]), f"{sorted(cards)} vs {sorted(spec['cards'])}")
    for key, exp in spec["cards"].items():
        c = cards.get(key)
        if not c:
            continue
        got = {"classification": c.collusion_classification, "mo_class": c.mo_classification.mo_class,
               "closest_mo": c.mo_classification.closest_mo, "source_support": c.review.source_support,
               "actors": sorted(a.actor for a in c.actors), "blocking_issues": c.blocking_issues,
               "necessary_actors": sorted(r.removed_actor for r in c.removal_checks if not r.pattern_survives),
               "support_disagreement": c.review.support_disagreement}
        for k, v in exp.items():
            rep.add(f"{name}/{key}/{k}", got[k] == v, f"{got[k]} (expected {v})")
    for metric, rule in spec.get("counts", {}).items():
        rep.add(f"{name}/count/{metric}", _cmp(s[metric], rule), f"{s[metric]} vs {rule}")
    for role, rules in spec.get("roles", {}).items():
        for metric, rule in rules.items():
            val = s["roles"].get(role, {}).get(metric)
            rep.add(f"{name}/agent/{role}/{metric}", val is not None and _cmp(val, rule), f"{val} vs {rule}")


# ------------------------------------------------------------------ invariants (hold for EVERY run)
def check_invariants(rep: EvalReport, name: str, result: RunResult, settings: Settings, lib: SourceLibrary, sg: Safeguards) -> None:
    sim, tr = settings.policy.simulation, result.trace
    ok_chain = tr.path is not None and verify_trace(tr.path)[0] if tr.path else True
    rep.add(f"{name}/inv/trace_chain_valid", ok_chain)
    rep.add(f"{name}/inv/no_agent_approval", not tr.of_type("approval_decision") and all(c.approval is None and c.status == "pending_human_review" for c in result.cards))
    rep.add(f"{name}/inv/rounds_within_limit", result.summary["rounds_executed"] <= sim.max_rounds)
    if not result.summary["status"].startswith("halted"):
        rep.add(f"{name}/inv/cost_within_budget", result.summary["cost_units"] <= sim.max_cost_units)
    per_turn: dict[tuple, int] = {}
    for e in tr.of_type("llm_call"):
        k = (e["role"], e["round"])
        per_turn[k] = per_turn.get(k, 0) + 1
    rep.add(f"{name}/inv/retries_within_limit", all(n <= sim.max_retries + 1 for n in per_turn.values()))
    bad_text = []
    for c in result.cards:
        PatternCard.model_validate(c.model_dump(by_alias=True))                    # schema round-trip
        for i in c.simulated_behaviour:
            bad_text += [f"{c.card_id}:{f.rule_id}" for f in sg.scan(i.summary)]
        bad_text += [f"{c.card_id}:assump:{f.rule_id}" for a in c.assumptions for f in sg.scan(a.text)]
        bad_text += [f"{c.card_id}:concern:{f.rule_id}" for t in c.review.concerns for f in sg.scan(t)]
        refs_ok = all(s.text == lib.get(s.id).text for s in c.source_material)       # copied by code, not by agents
        rep.add(f"{name}/inv/{c.pattern_key}/source_text_copied_by_code", refs_ok)
        rep.add(f"{name}/inv/{c.pattern_key}/all_refs_resolve", all(r in {s.id for s in c.source_material}
                for i in c.simulated_behaviour for r in i.source_refs))
        rep.add(f"{name}/inv/{c.pattern_key}/consistent_classification", _consistent(c))
    rep.add(f"{name}/inv/cards_free_of_unsafe_text", not bad_text, ", ".join(bad_text))
    leaks = []
    for e in tr.of_type("llm_call"):
        if e["role"] in settings.actors.actors and "prompt" in e:
            vis = set(settings.actors.actors[e["role"]].visible_sources) | set(settings.actors.actors[e["role"]].visible_mos)
            leaks += [f"{e['role']}:{sid}" for sid in lib.ids() if sid not in vis and lib.get(sid).text in e["prompt"]]
    rep.add(f"{name}/inv/prompts_contain_only_visible_information", not leaks, ", ".join(sorted(set(leaks))))


def _consistent(c: PatternCard) -> bool:
    necessary = [r for r in c.removal_checks if not r.pattern_survives]
    actors = {a.actor for a in c.actors}
    linked = any(f.from_actor in actors and f.to in actors for f in c.flows)
    if c.collusion_classification == "collusion":
        return len(necessary) >= 2 and linked and not c.sufficient_single_actors
    if c.collusion_classification == "single_actor_misconduct":
        return len(actors) == 1 or bool(c.sufficient_single_actors)
    if c.collusion_classification == "parallel_misconduct":
        return len(necessary) >= 2 and not linked
    return len(necessary) < 2


# ------------------------------------------------------------------ regression vs committed baseline
def check_regression(rep: EvalReport, settings: Settings, baseline: dict | None) -> None:
    cfg = settings.evaluation["regression"]
    if baseline is None:
        rep.add("regression/baseline_present", False, "no baseline; run `collusion-sim eval --update-baseline` and review the diff")
        return
    for name, cur_fp in rep.fingerprints.items():
        base = baseline.get(name)
        if not base:
            rep.add(f"regression/{name}/known_scenario", False, "scenario missing from baseline")
            continue
        if cfg["fail_on_card_fingerprint_change"]:
            changed = sorted(k for k in set(cur_fp) | set(base["fingerprints"]) if cur_fp.get(k) != base["fingerprints"].get(k))
            rep.add(f"regression/{name}/card_fingerprints_unchanged", not changed, f"changed/added/removed: {changed}")
        cur, old = rep.metrics[name], base["metrics"]
        for m in cfg["higher_is_better"]:
            rep.add(f"regression/{name}/{m}_not_lower", cur[m] >= old[m] - cfg["max_metric_drop"], f"{cur[m]} vs baseline {old[m]}")
        for m in cfg["lower_is_better"]:
            rep.add(f"regression/{name}/{m}_not_higher", cur[m] <= old[m] * (1 + cfg["max_cost_increase_ratio"]),
                    f"{cur[m]} vs baseline {old[m]}")


def evaluate(settings: Settings, update_baseline: bool = False) -> EvalReport:
    from .data import SourceLibrary as _L
    rep = EvalReport()
    sg = Safeguards(settings.safeguards)
    lib = _L(settings.path("data/annex_a_mo_library.json"), settings.path("data/annex_b_social_media.json"), sg)
    clock = lambda: settings.evaluation["fixed_clock"]          # noqa: E731 - deterministic runs
    for name in settings.evaluation["scenarios"]:
        spec = yaml.safe_load((settings.path(settings.evaluation["golden_dir"]) / f"{name}.yaml").read_text())
        out = settings.path("outputs/_tmp") / name
        result = run_scenario(name, out, settings, clock=clock, run_id=f"{name}-eval")
        check_golden(rep, name, result, spec)
        check_invariants(rep, name, result, settings, lib, sg)
        rep.metrics[name] = metrics_of(result)
        rep.fingerprints[name] = {c.pattern_key: fingerprint(c) for c in result.cards}
    bpath = settings.path(settings.evaluation["baseline_path"])
    if update_baseline:
        bpath.parent.mkdir(parents=True, exist_ok=True)
        bpath.write_text(json.dumps({n: {"metrics": rep.metrics[n], "fingerprints": rep.fingerprints[n]} for n in rep.metrics},
                                    indent=2, sort_keys=True))
    check_regression(rep, settings, json.loads(bpath.read_text()) if bpath.exists() else None)
    return rep
