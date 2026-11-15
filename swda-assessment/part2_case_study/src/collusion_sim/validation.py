"""Schema + semantic validation of every agent hand-off. Errors never echo submitted content (they are fed back
to the model on retry, and untrusted text must not be laundered through the error channel)."""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from .config import ActorSpec, Settings
from .schemas import ActorTurn, ReviewReport

BOUNDARY = "boundary:"


def _pyd_errors(exc: ValidationError) -> list[str]:
    return [f"schema:{'.'.join(str(x) for x in e['loc'])}:{e['type']}" for e in exc.errors()[:8]]


def _load_json(raw: str):
    try:
        return json.loads(raw), []
    except json.JSONDecodeError:
        return None, ["schema:invalid_json"]


def validate_turn(raw: str, *, actor_key: str, round_no: int, spec: ActorSpec, visible_ids: set[str], known_ids: set[str],
                  known_assumptions: set[str], settings: Settings) -> tuple[ActorTurn | None, list[str]]:
    data, errs = _load_json(raw)
    if errs:
        return None, errs
    try:
        turn = ActorTurn.model_validate(data)
    except ValidationError as exc:
        return None, _pyd_errors(exc)
    sim, tax = settings.policy.simulation, settings.taxonomy
    actors = set(settings.actors.actors)
    e: list[str] = []
    if turn.actor != actor_key:
        e.append(f"{BOUNDARY}actor_identity_mismatch")
    if turn.round != round_no:
        e.append("schema:round_mismatch")
    if len(turn.contributions) > sim.max_contributions_per_turn:
        e.append("limit:too_many_contributions")
    if len(turn.messages) > sim.max_messages_per_turn:
        e.append("limit:too_many_messages")
    if len(turn.tool_calls) > sim.max_tool_calls_per_turn:
        e.append("limit:too_many_tool_calls")
    declared = known_assumptions | {a.id for a in turn.assumptions}
    key_rx = re.compile(sim.pattern_key_regex)
    for i, c in enumerate(turn.contributions):
        p = f"contributions[{i}]"
        if not key_rx.match(c.pattern_key):
            e.append(f"schema:{p}.pattern_key_format")
        if c.capability not in tax.capabilities:
            e.append(f"schema:{p}.capability_not_in_taxonomy")
        elif c.capability not in spec.capabilities:
            e.append(f"{BOUNDARY}{p}.capability_outside_actor_role")
        if not c.summary.strip() or len(c.summary) > sim.max_summary_chars:
            e.append(f"limit:{p}.summary_length")
        if c.pattern_title is not None and len(c.pattern_title) > sim.max_title_chars:
            e.append(f"limit:{p}.title_length")
        for ref in c.source_refs:
            if ref not in known_ids:
                e.append(f"grounding:{p}.unknown_reference")
            elif ref not in visible_ids:
                e.append(f"{BOUNDARY}{p}.reference_not_visible_to_actor")
        if c.basis == "source" and not c.source_refs:
            e.append(f"grounding:{p}.basis_source_without_reference")
        if c.basis == "assumption" and not c.assumption_ids:
            e.append(f"grounding:{p}.basis_assumption_without_assumption_id")
        if any(a not in declared for a in c.assumption_ids):
            e.append(f"grounding:{p}.undeclared_assumption")
        for f in c.flows:
            if f.from_actor != actor_key or f.to not in actors or f.to == actor_key:
                e.append(f"{BOUNDARY}{p}.flow_endpoints")
            if f.kind not in tax.flow_kinds:
                e.append(f"schema:{p}.flow_kind")
        for b in c.benefits:
            if b.to not in actors or b.kind not in tax.benefit_kinds:
                e.append(f"schema:{p}.benefit")
    for i, m in enumerate(turn.messages):
        if m.to not in actors or m.to == actor_key:
            e.append(f"{BOUNDARY}messages[{i}].recipient")
        if not m.content.strip() or len(m.content) > sim.max_message_chars:
            e.append(f"limit:messages[{i}].length")
    return (None, e) if e else (turn, [])


def validate_review(raw: str, *, candidate_keys: set[str], known_ids: set[str], mo_ids: set[str],
                    settings: Settings) -> tuple[ReviewReport | None, list[str]]:
    data, errs = _load_json(raw)
    if errs:
        return None, errs
    try:
        rep = ReviewReport.model_validate(data)        # extra="forbid": an 'approved' field is a schema error
    except ValidationError as exc:
        return None, _pyd_errors(exc)
    e, seen = [], set()
    for i, it in enumerate(rep.reviews):
        if it.pattern_key not in candidate_keys or it.pattern_key in seen:
            e.append(f"grounding:reviews[{i}].pattern_key")
        seen.add(it.pattern_key)
        if it.closest_mo_opinion is not None and it.closest_mo_opinion not in mo_ids:
            e.append(f"grounding:reviews[{i}].closest_mo_unknown")
        if any(r not in known_ids for r in it.counter_evidence_refs):
            e.append(f"grounding:reviews[{i}].counter_evidence_unknown_reference")
        for text in (it.reference_comment, it.assumption_comment, *it.concerns):
            if len(text) > settings.policy.simulation.max_message_chars:
                e.append(f"limit:reviews[{i}].text_length")
    if len(rep.tool_calls) > settings.policy.simulation.max_tool_calls_per_turn:
        e.append("limit:too_many_tool_calls")
    return (None, e) if e else (rep, [])
