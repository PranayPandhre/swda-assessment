"""The ONLY code path that can change a card's status. Agents have no tool that reaches it, and the reviewer
identity must match a human id pattern that agent identities ("agent:...") can never satisfy."""
from __future__ import annotations

import re
from pathlib import Path

from .config import Settings
from .patterns import card_digest
from .schemas import Approval, PatternCard
from .trace import TraceStore

STATUS = {"approve": "approved", "reject": "rejected", "request_changes": "changes_requested"}


class ApprovalRefused(Exception):
    pass


def load_card(path: Path) -> PatternCard:
    return PatternCard.model_validate_json(path.read_text())


def apply_decision(card_path: Path, *, reviewer: str, decision: str, notes: str, channel: str, settings: Settings,
                   clock, trace_path: Path | None = None) -> PatternCard:
    pol = settings.policy.approval
    card = load_card(card_path)
    if card.status != "pending_human_review":
        raise ApprovalRefused(f"card is already {card.status}")
    if decision not in pol.decisions:
        raise ApprovalRefused(f"decision must be one of {pol.decisions}")
    if not re.match(pol.reviewer_id_regex, reviewer):
        raise ApprovalRefused("reviewer must be an authenticated human identity")
    required = [k for k, vals in pol.notes_required_when.items()
                if (k == "source_support" and card.review.source_support in vals)
                or (k == "mo_class" and card.mo_classification.mo_class in vals)]
    if decision == "approve" and required and not notes.strip():
        raise ApprovalRefused(f"a written justification is required to approve ({', '.join(required)})")
    if decision == "approve" and pol.blocking_issues_prevent_approve and card.blocking_issues:
        raise ApprovalRefused(f"blocking issues: {', '.join(card.blocking_issues)}")
    card.approval = Approval(decision=decision, reviewer=reviewer, notes=notes, channel=channel, decided_at=clock(),
                             card_digest=card_digest(card))
    card.status = STATUS[decision]
    card_path.write_text(card.model_dump_json(by_alias=True, indent=2))
    if trace_path and trace_path.exists():
        TraceStore(trace_path, clock, resume=True).emit("approval_decision", card_id=card.card_id, decision=decision,
                                                         reviewer=reviewer, channel=channel)
    return card


def verify_card_integrity(card: PatternCard) -> bool:
    """A decided card must still match the content the human actually reviewed."""
    return card.approval is None or card.approval.card_digest == card_digest(card)
