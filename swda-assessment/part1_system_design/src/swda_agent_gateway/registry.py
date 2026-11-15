"""Agent registry: integrity-checked records, an approval state machine and an "exposable" predicate.

An agent is exposed through MCP only if ALL hold (re-evaluated on every load, not just at approval time):
  status == approved, manifest digest matches, eval gate passed, approval policy still satisfied,
  entrypoint inside the allowed module prefix.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import load_yaml
from .models import AgentRecord, Approval, ApprovalPolicy

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"in_review"},
    "in_review": {"approved", "draft"},
    "approved": {"suspended", "retired"},
    "suspended": {"in_review", "retired"},
    "retired": set(),
}


class RegistryError(Exception):
    pass


def manifest_digest(rec: AgentRecord) -> str:
    body = rec.model_dump(mode="json", exclude={"manifest_sha256"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def policy_violations(rec: AgentRecord, policy: ApprovalPolicy) -> list[str]:
    problems = []
    roles = {a.role for a in rec.approvals}
    missing = set(policy.required_roles) - roles
    if missing:
        problems.append(f"missing approvals: {sorted(missing)}")
    approvers = {a.approver for a in rec.approvals}
    if len(approvers) < policy.min_distinct_approvers:
        problems.append(f"needs {policy.min_distinct_approvers} distinct approvers, has {len(approvers)}")
    if policy.owner_cannot_approve and rec.owner in approvers:
        problems.append("owner may not approve own agent (separation of duties)")
    if policy.require_eval_gate and not rec.eval_gate.passed:
        problems.append("evaluation gate has not passed")
    return problems


def transition(rec: AgentRecord, to: str, policy: ApprovalPolicy, approval: Approval | None = None) -> AgentRecord:
    """Pure state-machine step. Returns a new, re-sealed record or raises RegistryError."""
    if to not in TRANSITIONS[rec.status]:
        raise RegistryError(f"illegal transition {rec.status} -> {to}")
    new = rec.model_copy(deep=True)
    if approval:
        new.approvals = [a for a in new.approvals if a.role != approval.role] + [approval]
    if to == "approved":
        problems = policy_violations(new, policy)
        if problems:
            raise RegistryError("; ".join(problems))
    new.status = to  # type: ignore[assignment]
    new.manifest_sha256 = manifest_digest(new)
    return new


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AgentRegistry:
    def __init__(self, path: Path, entrypoint_prefix: str):
        raw = load_yaml(path)
        self.path = path
        self.policy = ApprovalPolicy(**raw["approval_policy"])
        self.records = [AgentRecord(**a) for a in raw["agents"]]
        self._prefix = entrypoint_prefix

    def get(self, agent_id: str) -> AgentRecord | None:
        return next((r for r in self.records if r.agent_id == agent_id), None)

    def exposure_problems(self, rec: AgentRecord) -> list[str]:
        problems = []
        if rec.status != "approved":
            problems.append(f"status is {rec.status}")
        if manifest_digest(rec) != rec.manifest_sha256:
            problems.append("manifest digest mismatch (record modified after sealing)")
        if not rec.entrypoint.startswith(self._prefix):
            problems.append("entrypoint outside allowed module prefix")
        problems += policy_violations(rec, self.policy)
        return problems

    def exposed(self) -> list[AgentRecord]:
        return [r for r in self.records if not self.exposure_problems(r)]

    def seal_file(self) -> None:
        """Re-compute digests and rewrite the YAML (what CI does after an approved registry PR merges)."""
        raw = load_yaml(self.path)
        for entry, rec in zip(raw["agents"], self.records):
            entry["manifest_sha256"] = manifest_digest(rec)
        with open(self.path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(raw, fh, sort_keys=False)
