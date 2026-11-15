"""Schemas for agent hand-offs (validated on every message) and for pattern cards."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Basis = Literal["source", "simulated", "assumption"]


class Benefit(_S):
    to: str
    kind: str


class Flow(_S):
    from_actor: str = Field(alias="from")
    to: str
    kind: str


class Contribution(_S):
    pattern_key: str
    pattern_title: str | None = None
    capability: str
    summary: str
    basis: Basis
    source_refs: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)
    essential: bool = True
    benefits: list[Benefit] = Field(default_factory=list)
    flows: list[Flow] = Field(default_factory=list)


class TurnMessage(_S):
    to: str
    content: str


class Assumption(_S):
    id: str
    text: str


class ToolRequest(_S):
    tool: str
    args: dict[str, str] = Field(default_factory=dict)


class ActorTurn(_S):
    actor: str
    round: int
    contributions: list[Contribution] = Field(default_factory=list)
    messages: list[TurnMessage] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    tool_calls: list[ToolRequest] = Field(default_factory=list)
    done: bool = False


class ReviewItem(_S):
    pattern_key: str
    reference_comment: str
    assumption_comment: str
    source_support_opinion: Literal["weak", "moderate", "strong"]
    closest_mo_opinion: str | None = None
    counter_evidence_refs: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    recommendation: Literal["recommend_human_approval", "needs_more_evidence", "recommend_reject"]


class ReviewReport(_S):
    # Deliberately NO approval/decision field: the Review Agent's schema cannot express an approval.
    reviews: list[ReviewItem] = Field(default_factory=list)
    tool_calls: list[ToolRequest] = Field(default_factory=list)


# ---------------------------- pattern card ----------------------------
class SourceItem(_S):
    id: str
    kind: Literal["annex_a_mo", "annex_b_excerpt"]
    basis: str
    text: str                                   # copied from the annex by CODE, never by an agent


class SimulatedItem(_S):
    actor: str
    round: int
    capability: str
    capability_label: str
    summary: str
    basis: Basis
    essential: bool
    source_refs: list[str]
    assumption_ids: list[str]
    label: Literal["SIMULATED - not a finding of fact"] = "SIMULATED - not a finding of fact"


class AssumptionItem(_S):
    id: str
    actor: str
    text: str


class ActorInfo(_S):
    actor: str
    title: str
    goals: list[str]
    contributed_capabilities: list[str]


class RemovalCheck(_S):
    removed_actor: str
    pattern_survives: bool
    missing_capabilities: list[str]


class MOClassification(_S):
    mo_class: Literal["new", "variation", "known"]
    closest_mo: str | None
    similarity: float
    matched_mos: list[str]
    novel_capabilities: list[str]


class DataNeed(_S):
    dataset: str
    test: str
    because: str


class ReviewSummary(_S):
    reviewed: bool
    references_valid: bool
    unsupported_contributions: int
    source_support: Literal["weak", "moderate", "strong"]
    source_support_score: float
    agent_source_support_opinion: str | None = None
    support_disagreement: bool = False
    agent_closest_mo_opinion: str | None = None
    mo_disagreement: bool = False
    counter_evidence_refs: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    agent_recommendation: str | None = None


class Safety(_S):
    quarantined_items_in_run: int
    pattern_items_quarantined: int
    run_halt_reason: str | None


class Approval(_S):
    decision: str
    reviewer: str
    notes: str
    channel: str
    decided_at: str
    card_digest: str


class PatternCard(_S):
    card_id: str
    run_id: str
    pattern_key: str
    title: str
    status: Literal["pending_human_review", "approved", "rejected", "changes_requested"] = "pending_human_review"
    disclaimer: str
    actors: list[ActorInfo]
    collusion_classification: Literal["collusion", "single_actor_misconduct", "parallel_misconduct", "redundant"]
    classification_basis: str
    benefits: list[Benefit]
    flows: list[Flow]
    simulated_behaviour: list[SimulatedItem]
    source_material: list[SourceItem]
    assumptions: list[AssumptionItem]
    removal_checks: list[RemovalCheck]
    sufficient_single_actors: list[str]
    mo_classification: MOClassification
    data_needed_for_testing: list[DataNeed]
    review: ReviewSummary
    safety: Safety
    blocking_issues: list[str]
    trace_seqs: list[int]
    approval: Approval | None = None
