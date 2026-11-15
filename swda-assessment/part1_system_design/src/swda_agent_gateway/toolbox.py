"""Least-privilege tool access. An agent can only call tools its registry record allows."""
from __future__ import annotations

import time
from collections.abc import Callable

from .models import ToolCallTrace


class ToolNotPermitted(Exception):
    pass


class ToolBox:
    def __init__(self, tools: dict[str, Callable], allowed: list[str]):
        self._tools = tools
        self._allowed = set(allowed)
        self.trace: list[ToolCallTrace] = []

    def call(self, name: str, **kwargs):
        if name not in self._allowed or name not in self._tools:
            self.trace.append(ToolCallTrace(tool=name, ok=False, duration_ms=0.0))
            raise ToolNotPermitted(f"tool '{name}' is not permitted for this agent")
        t0 = time.perf_counter()
        ok = False
        try:
            out = self._tools[name](**kwargs)
            ok = True
            return out
        finally:
            self.trace.append(ToolCallTrace(tool=name, ok=ok, duration_ms=round((time.perf_counter() - t0) * 1000, 3)))
