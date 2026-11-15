"""Least-privilege tools bound to ONE actor identity. Visibility and permissions are enforced here, in code,
not by asking the model nicely. The only tools that exist are read-only lookups; there is no approval,
write, network or file tool for any agent."""
from __future__ import annotations

from .config import ActorSpec, ReviewSpec
from .data import SourceLibrary
from .safeguards import Safeguards


class ToolDenied(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ActorToolBox:
    def __init__(self, spec: ActorSpec | ReviewSpec, library: SourceLibrary, safeguards: Safeguards):
        self._spec, self._lib, self._sg = spec, library, safeguards
        self._tools = {"lookup_source": ("annex_b_excerpt", spec.visible_sources), "lookup_mo": ("annex_a_mo", spec.visible_mos)}

    def visible_ids(self) -> set[str]:
        out: set[str] = set()
        for kind, vis in self._tools.values():
            out |= set(self._lib.ids(kind)) if vis == "all" else set(vis)
        return out

    def call(self, tool: str, args: dict[str, str]) -> str:
        if tool not in self._spec.tools or tool not in self._tools:
            raise ToolDenied(f"tool '{tool}' not granted")
        if set(args) != {"id"}:
            raise ToolDenied("arguments must be exactly {id}")
        kind, vis = self._tools[tool]
        rec = self._lib.get(args["id"])
        if rec is None or rec.kind != kind:
            raise ToolDenied("unknown id")
        if vis != "all" and rec.id not in vis:
            raise ToolDenied(f"'{rec.id}' is outside this actor's visible information")
        return self._sg.wrap_untrusted(rec.id, rec.text)
