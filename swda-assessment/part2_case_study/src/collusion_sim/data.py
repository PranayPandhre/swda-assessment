"""Annex loader. Annex text is UNTRUSTED: it is scanned on load and only ever shown to agents wrapped as data."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .safeguards import Safeguards

WITHHELD = "[withheld: flagged by safeguards as containing instruction-like text]"


@dataclass(frozen=True)
class SourceRecord:
    id: str
    kind: str          # annex_a_mo | annex_b_excerpt
    basis: str
    text: str
    flagged_rules: tuple[str, ...] = ()


class SourceLibrary:
    def __init__(self, annex_a: Path, annex_b: Path, safeguards: Safeguards):
        self._items: dict[str, SourceRecord] = {}
        for mo in json.loads(annex_a.read_text())["modus_operandi"]:
            self._add(mo["id"], "annex_a_mo", mo["basis"], f"{mo['modus_operandi']}: {mo['pattern']}", safeguards)
        for ex in json.loads(annex_b.read_text())["excerpts"]:
            self._add(ex["id"], "annex_b_excerpt", ex["basis"], ex["text"], safeguards)

    def _add(self, id_, kind, basis, text, sg: Safeguards) -> None:
        hits = tuple(f.rule_id for f in sg.scan(text) if f.category in ("injection", "pii"))
        self._items[id_] = SourceRecord(id_, kind, basis, WITHHELD if hits else text, hits)

    def get(self, id_: str) -> SourceRecord | None:
        return self._items.get(id_)

    def ids(self, kind: str | None = None) -> list[str]:
        return [i for i, r in self._items.items() if kind in (None, r.kind)]

    def flagged(self) -> list[SourceRecord]:
        return [r for r in self._items.values() if r.flagged_rules]
