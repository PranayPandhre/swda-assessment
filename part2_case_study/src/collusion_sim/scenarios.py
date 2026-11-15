from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Scenario:
    name: str
    description: str
    scripts: dict
    overrides: dict = field(default_factory=dict)         # policy.simulation overrides for this scenario
    disabled_actors: list[str] = field(default_factory=list)


def load_scenario(root: Path, name: str) -> Scenario:
    raw = yaml.safe_load((root / "scenarios" / f"{name}.yaml").read_text())
    scripts = raw.get("scripts")
    if "extends" in raw:                                   # reuse another scenario's scripts, change only limits/actors
        scripts = {**load_scenario(root, raw["extends"]).scripts, **raw.get("script_overrides", {})}
    return Scenario(raw["name"], raw["description"], scripts, raw.get("overrides", {}), raw.get("disabled_actors", []))
