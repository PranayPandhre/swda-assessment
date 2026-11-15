"""Wires settings + scenario + mock LLM + orchestrator, and writes artefacts."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .approval import ApprovalRefused, apply_decision
from .config import Settings, load_settings
from .data import SourceLibrary
from .llm import LLMClient, ScriptedLLM
from .orchestrator import Orchestrator, RunResult
from .outputs import write_run
from .safeguards import Safeguards
from .scenarios import load_scenario
from .trace import TraceStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_scenario(name: str, out_dir: Path | None = None, settings: Settings | None = None, clock=None, run_id: str | None = None,
                 llm: LLMClient | None = None, annex_b: Path | None = None) -> RunResult:
    s = settings or load_settings()
    sc = load_scenario(s.root, name)
    if sc.overrides:
        sim = s.policy.simulation.model_copy(update=sc.overrides)
        s = s.model_copy(update={"policy": s.policy.model_copy(update={"simulation": sim})})
    clock = clock or _now
    sg = Safeguards(s.safeguards)
    lib = SourceLibrary(s.path("data/annex_a_mo_library.json"), annex_b or s.path("data/annex_b_social_media.json"), sg)
    trace = TraceStore(out_dir / "trace.jsonl" if out_dir else None, clock)
    rid = run_id or f"{name}-{re.sub(r'[^0-9]', '', clock())[:14]}"
    result = Orchestrator(s, llm or ScriptedLLM(sc.scripts), lib, sg, trace, rid, name, sc.disabled_actors).run()
    if out_dir:
        write_run(out_dir, result.cards, result.summary)
    return result


def apply_decisions_file(run_dir: Path, decisions_path: Path, settings: Settings | None = None, clock=None) -> list[str]:
    s, clock, report = settings or load_settings(), clock or _now, []
    for d in yaml.safe_load(decisions_path.read_text())["decisions"]:
        try:
            apply_decision(run_dir / "cards" / f"{d['card']}.json", reviewer=d["reviewer"], decision=d["decision"],
                           notes=d.get("notes", ""), channel="decisions_file", settings=s, clock=clock,
                           trace_path=run_dir / "trace.jsonl")
            report.append(f"{d['card']}: {d['decision']} by {d['reviewer']}")
        except ApprovalRefused as exc:
            report.append(f"{d['card']}: REFUSED ({exc})")
    _rewrite_markdown(run_dir)
    return report


def _rewrite_markdown(run_dir: Path) -> None:
    from .approval import load_card
    from .render import card_markdown
    for p in (run_dir / "cards").glob("*.json"):
        p.with_suffix(".md").write_text(card_markdown(load_card(p)))
