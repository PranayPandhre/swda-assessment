"""LLM interface + scripted mock. A real model plugs in by implementing `complete`; nothing else changes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMRequest:
    role: str            # trainee | training_provider | employer | review
    round: int           # 0 for the review pass
    attempt: int         # 0 = first try; >0 = retry after a validation failure
    system: str
    prompt: str


class LLMClient(Protocol):
    def complete(self, req: LLMRequest) -> str: ...


class ScriptedLLM:
    """Deterministic: returns the scripted response for (role, round, attempt). Each attempt may be a dict
    (serialised to JSON) or a raw string (to simulate malformed output). Missing script => a valid no-op turn.
    It IGNORES the prompt content, which is exactly why it cannot test pattern quality (see design note)."""

    def __init__(self, scripts: dict[str, list[dict]]):
        self._scripts = scripts

    def complete(self, req: LLMRequest) -> str:
        entry = next((e for e in self._scripts.get(req.role, []) if e["round"] == req.round), None)
        if entry is None:
            if req.role == "review":
                return json.dumps({"reviews": [], "tool_calls": []})
            return json.dumps({"actor": req.role, "round": req.round, "done": True})
        attempts = entry["attempts"]
        out = attempts[min(req.attempt, len(attempts) - 1)]
        return out if isinstance(out, str) else json.dumps(out)
