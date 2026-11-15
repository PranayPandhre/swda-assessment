"""Deterministic pattern analysis and card assembly. Cards are built by CODE from schema-validated fields,
never from free-form model prose; source text is copied from the annexes by ID."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .config import Settings
from .data import SourceLibrary
from .schemas import (
    ActorInfo,
    AssumptionItem,
    Benefit,
    Contribution,
    DataNeed,
    Flow,
    MOClassification,
    PatternCard,
    RemovalCheck,
    ReviewItem,
    ReviewSummary,
    Safety,
    SimulatedItem,
    SourceItem,
)

LEVELS = ["weak", "moderate", "strong"]


@dataclass(frozen=True)
class Accepted:
    actor: str
    round: int
    seq: int                     # trace event that accepted it
    c: Contribution


def group(accepted: list[Accepted]) -> dict[str, list[Accepted]]:
    out: dict[str, list[Accepted]] = {}
    for a in accepted:
        out.setdefault(a.c.pattern_key, []).append(a)
    return out


def _caps_by_actor(items: list[Accepted], essential_only: bool) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for a in items:
        if a.c.essential or not essential_only:
            out.setdefault(a.actor, set()).add(a.c.capability)
    return out


def essential_caps(items: list[Accepted]) -> set[str]:
    return {a.c.capability for a in items if a.c.essential}


def removal_checks(items: list[Accepted]) -> list[RemovalCheck]:
    need, by_actor = essential_caps(items), _caps_by_actor(items, True)
    out = []
    for actor in sorted({a.actor for a in items}):
        remaining = set().union(*(caps for k, caps in by_actor.items() if k != actor)) if len(by_actor) > 1 else set()
        missing = sorted(need - remaining)
        out.append(RemovalCheck(removed_actor=actor, pattern_survives=not missing, missing_capabilities=missing))
    return out


def classify_collusion(items: list[Accepted]) -> tuple[str, str, list[str]]:
    """Returns (label, plain-language basis, single actors sufficient on their own)."""
    actors = sorted({a.actor for a in items})
    need, by_actor = essential_caps(items), _caps_by_actor(items, True)
    sufficient = [x for x in actors if need and need <= by_actor.get(x, set())]
    if len(actors) == 1:
        return "single_actor_misconduct", f"only {actors[0]} contributes", sufficient
    if sufficient:
        return ("single_actor_misconduct",
                f"{', '.join(sufficient)} alone covers every essential element; other contributions are not needed", sufficient)
    necessary = [r.removed_actor for r in removal_checks(items) if not r.pattern_survives]
    cross_flow = any(f.from_actor in actors and f.to in actors for a in items for f in a.c.flows)   # between contributing actors only
    if len(necessary) >= 2 and cross_flow:
        return "collusion", f"{len(necessary)} actors are each necessary and value/information flows between actors", []
    if len(necessary) >= 2:
        return "parallel_misconduct", "several actors are necessary but no flow links them (no evidence of coordination)", []
    return "redundant", "no actor is individually necessary", []


def classify_mo(items: list[Accepted], settings: Settings) -> MOClassification:
    tax = settings.taxonomy
    sig = {a.c.capability for a in items}
    sims = []
    for mo, feats in sorted(tax.mo_features.items()):
        fs = set(feats)
        union = sig | fs
        sims.append((len(sig & fs) / len(union) if union else 0.0, mo, fs))
    best_sim, best_mo, _ = max(sims, key=lambda t: (t[0], -int(t[1][3:])))
    matched = [mo for s, mo, fs in sorted(sims, key=lambda t: (-t[0], t[1])) if sig & fs]
    covered = set().union(*(set(tax.mo_features[m]) for m in matched)) if matched else set()
    cfg = tax.mo_classification
    cls = "known" if best_sim >= cfg["known_threshold"] else "variation" if best_sim >= cfg["variation_threshold"] else "new"
    return MOClassification(mo_class=cls, closest_mo=best_mo if best_sim > 0 else None, similarity=round(best_sim, 4),
                            matched_mos=matched, novel_capabilities=sorted(sig - covered))


def source_support(items: list[Accepted], assumption_count: int, lib: SourceLibrary, settings: Settings) -> tuple[str, float]:
    ev = settings.taxonomy.evidence
    refs = sorted({r for a in items for r in a.c.source_refs})
    score = float(sum(ev["source_weights"].get(lib.get(r).basis, 0) for r in refs if lib.get(r))) - ev["assumption_penalty"] * assumption_count
    th = ev["thresholds"]
    level = "strong" if score >= th["strong"] else "moderate" if score >= th["moderate"] else "weak"
    unverified = set(ev["unverified_bases"])
    if not refs or all((lib.get(r) is None) or lib.get(r).basis in unverified for r in refs):
        level = min(level, ev["unverified_only_cap"], key=LEVELS.index)
    return level, score


def data_needs(items: list[Accepted], mo_class: str, settings: Settings) -> list[DataNeed]:
    tax, seen, out = settings.taxonomy, set(), []
    for cap in sorted({a.c.capability for a in items}):
        for n in tax.capabilities[cap]["data_needed"]:
            if (n["dataset"], n["test"]) not in seen:
                seen.add((n["dataset"], n["test"]))
                out.append(DataNeed(dataset=n["dataset"], test=n["test"], because=f"capability:{cap}"))
    for n in tax.mo_classification["data_needed_by_class"].get(mo_class, []):
        out.append(DataNeed(dataset=n["dataset"], test=n["test"], because=f"mo_class:{mo_class}"))
    return out


def card_digest(card: PatternCard) -> str:
    body = card.model_dump(mode="json", by_alias=True, exclude={"approval", "status"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_card(*, key: str, items: list[Accepted], assumptions: dict[str, AssumptionItem], review: ReviewItem | None,
               reviewed: bool, run_id: str, lib: SourceLibrary, settings: Settings, quarantined_total: int,
               quarantined_in_pattern: int, halt_reason: str | None) -> PatternCard:
    tax, actors_cfg = settings.taxonomy, settings.actors.actors
    label, basis, sufficient = classify_collusion(items)
    mo = classify_mo(items, settings)
    used_assumptions = sorted({i for a in items for i in a.c.assumption_ids})
    level, score = source_support(items, len(used_assumptions), lib, settings)
    refs = sorted({r for a in items for r in a.c.source_refs})
    title = next((a.c.pattern_title for a in items if a.c.pattern_title), key)
    caps_by_actor = _caps_by_actor(items, False)

    unsupported = sum(1 for a in items if not a.c.source_refs)
    refs_valid = all(lib.get(r) is not None for r in refs)
    summary = ReviewSummary(
        reviewed=reviewed and review is not None, references_valid=refs_valid, unsupported_contributions=unsupported,
        source_support=level, source_support_score=score,
        agent_source_support_opinion=review.source_support_opinion if review else None,
        support_disagreement=bool(review and review.source_support_opinion != level),
        agent_closest_mo_opinion=review.closest_mo_opinion if review else None,
        mo_disagreement=bool(review and review.closest_mo_opinion != mo.closest_mo),
        counter_evidence_refs=review.counter_evidence_refs if review else [],
        concerns=review.concerns if review else [], agent_recommendation=review.recommendation if review else None)

    blocking = []
    if not refs_valid:
        blocking.append("invalid_references")
    if not summary.reviewed:
        blocking.append("not_reviewed")
    if halt_reason:
        blocking.append(f"run_halted:{halt_reason}")

    return PatternCard(
        card_id=f"{run_id}:{key}", run_id=run_id, pattern_key=key, title=title, disclaimer=settings.policy.disclaimer,
        actors=[ActorInfo(actor=a, title=actors_cfg[a].title, goals=actors_cfg[a].goals,
                          contributed_capabilities=sorted(caps_by_actor[a])) for a in sorted(caps_by_actor)],
        collusion_classification=label, classification_basis=basis,
        benefits=[Benefit(to=b.to, kind=b.kind) for a in items for b in a.c.benefits],
        flows=[Flow(**{"from": f.from_actor, "to": f.to, "kind": f.kind}) for a in items for f in a.c.flows],
        simulated_behaviour=[SimulatedItem(actor=a.actor, round=a.round, capability=a.c.capability,
                                           capability_label=tax.capabilities[a.c.capability]["label"], summary=a.c.summary,
                                           basis=a.c.basis, essential=a.c.essential, source_refs=a.c.source_refs,
                                           assumption_ids=a.c.assumption_ids) for a in items],
        source_material=[SourceItem(id=r, kind=lib.get(r).kind, basis=lib.get(r).basis, text=lib.get(r).text)
                         for r in refs if lib.get(r)],
        assumptions=[assumptions[i] for i in used_assumptions if i in assumptions],
        removal_checks=removal_checks(items), sufficient_single_actors=sufficient, mo_classification=mo,
        data_needed_for_testing=data_needs(items, mo.mo_class, settings), review=summary,
        safety=Safety(quarantined_items_in_run=quarantined_total, pattern_items_quarantined=quarantined_in_pattern,
                      run_halt_reason=halt_reason),
        blocking_issues=blocking, trace_seqs=sorted({a.seq for a in items}),
    )
