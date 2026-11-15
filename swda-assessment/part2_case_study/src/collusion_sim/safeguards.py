"""Rule-based screens for hidden instructions and unsafe/over-asserting output (config-driven)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Safeguard


@dataclass(frozen=True)
class Finding:
    rule_id: str
    category: str


class Safeguards:
    def __init__(self, cfg: Safeguard):
        self._rules = [(cat, r.id, re.compile(r.pattern)) for cat, rules in cfg.rules.items() for r in rules]
        self._wrapper = cfg.untrusted_wrapper

    def scan(self, text: str) -> list[Finding]:
        return [Finding(rid, cat) for cat, rid, rx in self._rules if rx.search(text)]

    def wrap_untrusted(self, source_id: str, text: str) -> str:
        return self._wrapper.format(id=source_id, text=text)
