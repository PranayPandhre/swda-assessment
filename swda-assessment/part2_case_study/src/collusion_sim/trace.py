"""Append-only, hash-chained run trace (JSONL). Every LLM call, validation failure, tool call, safeguard hit,
delivery, review and human decision is an event. Cards reference events by seq."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class TraceStore:
    def __init__(self, path: Path | None, clock, resume: bool = False):
        self.path, self._clock, self.events, self._prev = path, clock, [], "0" * 64
        if path and resume and path.exists():
            self.events = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
            self._prev = self.events[-1]["hash"] if self.events else self._prev
        elif path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def emit(self, type_: str, **fields) -> int:
        seq = len(self.events) + 1
        ev = {"seq": seq, "ts": self._clock(), "type": type_, **fields, "prev_hash": self._prev}
        ev["hash"] = hashlib.sha256(_canon(ev).encode()).hexdigest()
        self._prev = ev["hash"]
        self.events.append(ev)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(_canon(ev) + "\n")
        return seq

    def of_type(self, *types: str) -> list[dict]:
        return [e for e in self.events if e["type"] in types]


def verify_trace(path: Path) -> tuple[bool, str]:
    prev, n = "0" * 64, 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        n += 1
        claimed = ev.pop("hash")
        if ev["prev_hash"] != prev or ev["seq"] != n:
            return False, f"chain broken at event {n}"
        if hashlib.sha256(_canon(ev).encode()).hexdigest() != claimed:
            return False, f"event {n} modified"
        prev = claimed
    return True, f"{n} events verified"
