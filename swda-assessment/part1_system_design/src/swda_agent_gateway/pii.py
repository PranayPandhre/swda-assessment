"""Regex PII redaction applied to inputs before processing/logging and to outputs before returning."""
from __future__ import annotations

import re


class Redactor:
    def __init__(self, patterns: dict[str, str], token: str):
        self._patterns = {k: re.compile(v) for k, v in patterns.items()}
        self._token = token

    def redact(self, text: str) -> tuple[str, dict[str, int]]:
        found: dict[str, int] = {}
        for kind, rx in self._patterns.items():
            text, n = rx.subn(self._token.format(kind=kind), text)
            if n:
                found[kind] = n
        return text, found

    def contains_pii(self, text: str) -> bool:
        return any(rx.search(text) for rx in self._patterns.values())
