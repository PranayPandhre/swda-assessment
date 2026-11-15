"""All behaviour-shaping values come from YAML under config/. Typed access, validated once at start-up."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

ROOT = Path(os.environ.get("COLLUSION_SIM_ROOT", Path(__file__).resolve().parents[2]))


def _load(p: Path) -> dict:
    with open(p, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SimulationPolicy(_S):
    max_rounds: int
    round_order: list[str]
    max_contributions_per_turn: int
    max_messages_per_turn: int
    max_tool_calls_per_turn: int
    max_message_chars: int
    max_summary_chars: int
    max_title_chars: int
    pattern_key_regex: str
    max_retries: int
    max_failed_turns: int
    max_quarantined_items: int
    max_cost_units: float


class CostPolicy(_S):
    chars_per_token: float
    units_per_1k_tokens: float


class TracePolicy(_S):
    store_prompt_text: bool


class ApprovalPolicy(_S):
    decisions: list[str]
    reviewer_id_regex: str
    notes_required_when: dict[str, list[str]]
    blocking_issues_prevent_approve: bool


class Policy(_S):
    simulation: SimulationPolicy
    cost: CostPolicy
    trace: TracePolicy
    approval: ApprovalPolicy
    disclaimer: str


class ActorSpec(_S):
    agent_id: str
    title: str
    goals: list[str]
    incentives: str
    capabilities: list[str]
    visible_sources: list[str]
    visible_mos: list[str]
    tools: list[str]


class ReviewSpec(_S):
    agent_id: str
    title: str
    visible_sources: str
    visible_mos: str
    tools: list[str]


class Actors(_S):
    actors: dict[str, ActorSpec]
    review_agent: ReviewSpec


class Taxonomy(_S):
    capabilities: dict[str, dict]
    flow_kinds: list[str]
    benefit_kinds: list[str]
    mo_features: dict[str, list[str]]
    mo_classification: dict
    evidence: dict


class Rule(_S):
    id: str
    pattern: str


class Safeguard(_S):
    rules: dict[str, list[Rule]]
    untrusted_wrapper: str


class Settings(_S):
    policy: Policy
    actors: Actors
    taxonomy: Taxonomy
    safeguards: Safeguard
    evaluation: dict
    root: Path = ROOT

    def path(self, rel: str) -> Path:
        return self.root / rel


def load_settings(root: Path | None = None) -> Settings:
    root = root or ROOT
    c = root / "config"
    return Settings(policy=_load(c / "policy.yaml"), actors=_load(c / "actors.yaml"), taxonomy=_load(c / "taxonomy.yaml"),
                    safeguards=_load(c / "safeguards.yaml"), evaluation=_load(c / "evaluation.yaml"), root=root)
