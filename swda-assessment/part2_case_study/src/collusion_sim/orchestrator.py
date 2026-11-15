"""Orchestrator: deterministic CODE (not an LLM) that owns rounds, message routing, limits, retries, cost and the
hand-off to a human. Agents propose; this module validates, screens, quarantines and records."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import patterns, prompts
from .config import Settings
from .data import SourceLibrary
from .llm import LLMClient, LLMRequest
from .safeguards import Safeguards
from .schemas import ActorTurn, AssumptionItem, PatternCard, ReviewItem
from .tools import ActorToolBox, ToolDenied
from .trace import TraceStore
from .validation import BOUNDARY, validate_review, validate_turn


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@dataclass
class RunResult:
    run_id: str
    cards: list[PatternCard]
    summary: dict
    trace: TraceStore


@dataclass
class _RoleStats:
    calls: int = 0
    turns: int = 0
    first_attempt_valid: int = 0
    final_valid: int = 0


class Orchestrator:
    def __init__(self, settings: Settings, llm: LLMClient, library: SourceLibrary, safeguards: Safeguards,
                 trace: TraceStore, run_id: str, scenario_name: str = "", disabled_actors: list[str] | None = None):
        self.s, self.llm, self.lib, self.sg, self.trace = settings, llm, library, safeguards, trace
        self.run_id, self.scenario_name = run_id, scenario_name
        self.sim, self.cost_cfg = settings.policy.simulation, settings.policy.cost
        self.disabled = set(disabled_actors or [])
        self.cost = 0.0
        self.halt_reason: str | None = None
        self.accepted: list[patterns.Accepted] = []
        self.assumptions: dict[str, AssumptionItem] = {}          # namespaced "actor:A1"
        self.local_assumptions: dict[str, set[str]] = {}
        self.inbox: dict[str, list[tuple[str, str]]] = {a: [] for a in settings.actors.actors}
        self.pending: dict[str, list[tuple[str, str]]] = {a: [] for a in settings.actors.actors}
        self.notes: dict[str, list[str]] = {a: [] for a in settings.actors.actors}
        self.stats: dict[str, _RoleStats] = {}
        self.counters = dict(quarantined=0, boundary_violations=0, tool_allowed=0, tool_denied=0, validation_failures=0,
                             failed_turns=0, messages_delivered=0, messages_undelivered=0, llm_calls=0, retries=0)
        self.quarantined_by_pattern: dict[str, int] = {}

    # ---------------------------------------------------------------- run
    def run(self) -> RunResult:
        flagged = self.lib.flagged()
        self.trace.emit("run_started", run_id=self.run_id, scenario=self.scenario_name, disabled_actors=sorted(self.disabled),
                        limits=self.sim.model_dump(), flagged_sources=[{"id": r.id, "rules": list(r.flagged_rules)} for r in flagged])
        rounds = 0
        for rnd in range(1, self.sim.max_rounds + 1):
            if self.halt_reason:
                break
            rounds = rnd
            self.trace.emit("round_started", round=rnd)
            self._deliver()
            before, done_flags = len(self.accepted), []
            for actor in self.sim.round_order:
                if actor in self.disabled or self.halt_reason:
                    continue
                done_flags.append(self._actor_turn(actor, rnd))
            if not self.halt_reason and (len(self.accepted) == before or all(done_flags)):
                self.trace.emit("converged", round=rnd, new_contributions=len(self.accepted) - before)
                break
        else:
            self.trace.emit("limit_hit", limit="max_rounds", value=self.sim.max_rounds)
        for actor, msgs in self.pending.items():
            for _ in msgs:
                self.counters["messages_undelivered"] += 1
        review = self._review() if not self.halt_reason else {}
        cards = self._cards(review)
        status = f"halted:{self.halt_reason}" if self.halt_reason else "completed"
        summary = self._summary(status, rounds, cards)
        self.trace.emit("run_finished", status=status, cards=[c.card_id for c in cards], cost_units=round(self.cost, 4))
        return RunResult(self.run_id, cards, summary, self.trace)

    # ---------------------------------------------------------------- guarded LLM call
    def _guarded_call(self, role: str, rnd: int, system: str, build_prompt, validator):
        stats = self.stats.setdefault(role, _RoleStats())
        stats.turns += 1
        feedback: list[str] = []
        for attempt in range(self.sim.max_retries + 1):
            if self.cost >= self.sim.max_cost_units:
                self._halt("cost_limit")
                return None
            prompt = build_prompt(feedback)
            raw = self.llm.complete(LLMRequest(role, rnd, attempt, system, prompt))
            tokens = (len(system) + len(prompt) + len(raw)) / self.cost_cfg.chars_per_token
            self.cost += tokens / 1000 * self.cost_cfg.units_per_1k_tokens
            stats.calls += 1
            self.counters["llm_calls"] += 1
            self.counters["retries"] += attempt > 0
            result, errors = validator(raw)
            self.trace.emit("llm_call", role=role, round=rnd, attempt=attempt, prompt_sha=_sha(prompt), response_sha=_sha(raw),
                            est_tokens=round(tokens), cumulative_cost=round(self.cost, 4), valid=not errors,
                            **({"prompt": prompt} if self.s.policy.trace.store_prompt_text else {}))
            if not errors:
                stats.first_attempt_valid += attempt == 0
                stats.final_valid += 1
                return result
            self.counters["validation_failures"] += 1
            self.trace.emit("validation_failed", role=role, round=rnd, attempt=attempt, errors=errors)
            for e in errors:
                if e.startswith(BOUNDARY):
                    self.counters["boundary_violations"] += 1
                    self.trace.emit("boundary_violation", role=role, round=rnd, detail=e)
            feedback = errors
        return None

    def _halt(self, reason: str) -> None:
        if not self.halt_reason:
            self.halt_reason = reason
            self.trace.emit("limit_hit", limit=reason)

    # ---------------------------------------------------------------- actor turn
    def _actor_turn(self, actor: str, rnd: int) -> bool:
        spec = self.s.actors.actors[actor]
        tb = ActorToolBox(spec, self.lib, self.sg)
        visible = sorted(tb.visible_ids())
        sources = [(i, self.lib.get(i).text) for i in visible]            # visibility decided by the tool box, not the model
        inbox = list(self.inbox[actor])
        system = prompts.actor_system(actor, self.s)
        turn: ActorTurn | None = self._guarded_call(
            actor, rnd, system,
            lambda fb: prompts.actor_prompt(actor, rnd, self.s, self.sg, sources, inbox, self.notes[actor], fb),
            lambda raw: validate_turn(raw, actor_key=actor, round_no=rnd, spec=spec, visible_ids=set(visible),
                                      known_ids=set(self.lib.ids()), known_assumptions=self.local_assumptions.get(actor, set()),
                                      settings=self.s))
        if turn is None:
            if not self.halt_reason:
                self.counters["failed_turns"] += 1
                self.trace.emit("turn_failed", role=actor, round=rnd)
                if self.counters["failed_turns"] >= self.sim.max_failed_turns:
                    self._halt("max_failed_turns")
            return False
        # ---- safeguards on every free-text field
        bad_assumptions = set()
        for a in turn.assumptions:
            if self._screen(actor, rnd, "assumption", a.text):
                bad_assumptions.add(a.id)
            else:
                self.local_assumptions.setdefault(actor, set()).add(a.id)
                self.assumptions[f"{actor}:{a.id}"] = AssumptionItem(id=f"{actor}:{a.id}", actor=actor, text=a.text)
        kept = []
        for c in turn.contributions:
            texts = [c.summary] + ([c.pattern_title] if c.pattern_title else [])
            hit = self._screen(actor, rnd, "contribution", *texts) or bool(set(c.assumption_ids) & bad_assumptions)
            if hit:
                self.quarantined_by_pattern[c.pattern_key] = self.quarantined_by_pattern.get(c.pattern_key, 0) + 1
            else:
                kept.append(c)
        seq = self.trace.emit("turn_accepted", role=actor, round=rnd, contributions=len(kept),
                              dropped=len(turn.contributions) - len(kept), done=turn.done)
        for c in kept:
            ns = c.model_copy(update={"assumption_ids": [f"{actor}:{i}" for i in c.assumption_ids]})
            self.accepted.append(patterns.Accepted(actor, rnd, seq, ns))
        for m in turn.messages:
            if not self._screen(actor, rnd, "message", m.content):
                self.pending[m.to].append((self.s.actors.actors[actor].agent_id, m.content))
                self.trace.emit("message_queued", sender=actor, to=m.to, content_sha=_sha(m.content))
        for t in turn.tool_calls:
            try:
                out = tb.call(t.tool, t.args)
                self.counters["tool_allowed"] += 1
                self.notes[actor].append(out)
                self.trace.emit("tool_call", role=actor, tool=t.tool, args=t.args, allowed=True)
            except ToolDenied as exc:
                self.counters["tool_denied"] += 1
                self.counters["boundary_violations"] += 1
                self.trace.emit("tool_call", role=actor, tool=t.tool, args=t.args, allowed=False, reason=exc.reason)
        return turn.done

    def _screen(self, actor: str, rnd: int, kind: str, *texts: str) -> bool:
        hits = [f for t in texts for f in self.sg.scan(t)]
        if not hits:
            return False
        self.counters["quarantined"] += 1
        self.trace.emit("item_quarantined", role=actor, round=rnd, kind=kind, rules=sorted({f.rule_id for f in hits}),
                        categories=sorted({f.category for f in hits}), content="\n".join(texts))
        if self.counters["quarantined"] >= self.sim.max_quarantined_items:
            self._halt("max_quarantined_items")
        return True

    def _deliver(self) -> None:
        for actor in self.inbox:
            self.inbox[actor] = self.pending[actor]
            self.pending[actor] = []
            for frm, _ in self.inbox[actor]:
                self.counters["messages_delivered"] += 1
                self.trace.emit("message_delivered", to=actor, sender=frm)

    # ---------------------------------------------------------------- review + cards
    def _review(self) -> dict[str, ReviewItem]:
        groups = patterns.group(self.accepted)
        if not groups:
            return {}
        spec = self.s.actors.review_agent
        tb = ActorToolBox(spec, self.lib, self.sg)
        candidates = [{"pattern_key": k, "title": next((a.c.pattern_title for a in v if a.c.pattern_title), k),
                       "contributions": [{"actor": a.actor, "capability": a.c.capability, "basis": a.c.basis,
                                          "source_refs": a.c.source_refs, "assumption_ids": a.c.assumption_ids} for a in v]}
                      for k, v in groups.items()]
        sources = [(i, self.lib.get(i).text) for i in sorted(tb.visible_ids())]
        mo_ids = set(self.lib.ids("annex_a_mo"))
        rep = self._guarded_call(
            "review", 0, prompts.review_system(), lambda fb: prompts.review_prompt(candidates, sources, self.sg, fb),
            lambda raw: validate_review(raw, candidate_keys=set(groups), known_ids=set(self.lib.ids()), mo_ids=mo_ids, settings=self.s))
        if rep is None:
            return {}
        for t in rep.tool_calls:
            try:
                tb.call(t.tool, t.args)
                self.counters["tool_allowed"] += 1
                self.trace.emit("tool_call", role="review", tool=t.tool, args=t.args, allowed=True)
            except ToolDenied as exc:
                self.counters["tool_denied"] += 1
                self.counters["boundary_violations"] += 1
                self.trace.emit("tool_call", role="review", tool=t.tool, args=t.args, allowed=False, reason=exc.reason)
        out = {}
        for it in rep.reviews:
            if self._screen("review", 0, "review_item", it.reference_comment, it.assumption_comment, *it.concerns):
                continue                                              # flagged review text => pattern stays "not reviewed"
            out[it.pattern_key] = it
        self.trace.emit("review_completed", patterns=sorted(out))
        return out

    def _cards(self, review: dict[str, ReviewItem]) -> list[PatternCard]:
        cards = []
        for key, items in patterns.group(self.accepted).items():
            card = patterns.build_card(
                key=key, items=items, assumptions=self.assumptions, review=review.get(key), reviewed=not self.halt_reason,
                run_id=self.run_id, lib=self.lib, settings=self.s, quarantined_total=self.counters["quarantined"],
                quarantined_in_pattern=self.quarantined_by_pattern.get(key, 0), halt_reason=self.halt_reason)
            self.trace.emit("card_built", card_id=card.card_id, classification=card.collusion_classification,
                            mo_class=card.mo_classification.mo_class)
            cards.append(card)
        return cards

    def _summary(self, status: str, rounds: int, cards: list[PatternCard]) -> dict:
        return {
            "run_id": self.run_id, "scenario": self.scenario_name, "status": status, "rounds_executed": rounds,
            "max_rounds": self.sim.max_rounds, "cost_units": round(self.cost, 4), "max_cost_units": self.sim.max_cost_units,
            **self.counters,
            "roles": {r: {"llm_calls": s.calls, "turns": s.turns,
                          "first_attempt_validity": round(s.first_attempt_valid / s.turns, 4) if s.turns else 1.0,
                          "final_validity": round(s.final_valid / s.turns, 4) if s.turns else 1.0}
                      for r, s in sorted(self.stats.items())},
            "cards": [{"card_id": c.card_id, "classification": c.collusion_classification, "mo_class": c.mo_classification.mo_class,
                       "source_support": c.review.source_support, "blocking_issues": c.blocking_issues} for c in cards],
        }
