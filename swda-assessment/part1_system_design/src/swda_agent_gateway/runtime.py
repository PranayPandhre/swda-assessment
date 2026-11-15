"""Loads exposable agents from the registry and invokes them with least-privilege tools + a timeout."""
from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from .config import Settings
from .models import AgentRecord, RecommendationResponse
from .registry import AgentRegistry
from .toolbox import ToolBox, ToolNotPermitted


class AgentTimeout(Exception):
    pass


@dataclass
class LoadedAgent:
    record: AgentRecord
    agent: object
    schema: dict
    validator: Draft202012Validator


class AgentRuntime:
    def __init__(self, settings: Settings, registry: AgentRegistry):
        self._settings = settings
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")
        self.agents: dict[str, LoadedAgent] = {}
        for rec in registry.exposed():
            mod_name, func_name = rec.entrypoint.split(":")
            build = getattr(importlib.import_module(mod_name), func_name)
            agent = build(rec, settings.root)
            schema = dict(agent.request_schema)
            # Size limit is operational config, injected into the advertised schema so clients see it.
            props = dict(schema["properties"])
            props["text"] = {**props["text"], "maxLength": settings.limits.max_text_chars}
            schema["properties"] = props
            self.agents[rec.agent_id] = LoadedAgent(rec, agent, schema, Draft202012Validator(schema))

    def invoke(self, agent_id: str, arguments: dict) -> RecommendationResponse:
        la = self.agents[agent_id]
        toolbox = ToolBox(la.agent.tools, la.record.allowed_tools)
        fut = self._pool.submit(la.agent.run, arguments, toolbox)
        try:
            return fut.result(timeout=self._settings.limits.agent_timeout_seconds)
        except FutureTimeout as exc:
            fut.cancel()
            raise AgentTimeout(agent_id) from exc


__all__ = ["AgentRuntime", "AgentTimeout", "ToolNotPermitted", "LoadedAgent"]
