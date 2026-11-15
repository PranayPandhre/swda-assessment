"""Pydantic models for registry records and agent responses."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["draft", "in_review", "approved", "suspended", "retired"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Approval(_Strict):
    role: str
    approver: str
    at: str
    evidence: str


class EvalGate(_Strict):
    passed: bool
    run_id: str | None = None


class AgentRecord(_Strict):
    agent_id: str
    version: str
    description: str
    owner: str
    status: Status
    risk_tier: str
    data_classification: str
    entrypoint: str
    agent_config: str
    allowed_tools: list[str]
    required_scopes: list[str]
    approvals: list[Approval] = Field(default_factory=list)
    eval_gate: EvalGate
    manifest_sha256: str = ""


class ApprovalPolicy(_Strict):
    required_roles: list[str]
    min_distinct_approvers: int
    owner_cannot_approve: bool
    require_eval_gate: bool


class Recommendation(_Strict):
    programme_id: str
    title: str
    score: float
    reasons: list[str]


class ToolCallTrace(_Strict):
    tool: str
    ok: bool
    duration_ms: float


class RecommendationResponse(_Strict):
    request_id: str
    category: str
    category_scores: dict[str, float]
    recommendations: list[Recommendation]
    flags: list[str]
    needs_human_review: bool
    agent_id: str
    agent_version: str
    tool_trace: list[ToolCallTrace]
