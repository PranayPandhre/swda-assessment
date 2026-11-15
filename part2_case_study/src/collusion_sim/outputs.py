from __future__ import annotations

import json
from pathlib import Path

from .render import card_markdown
from .schemas import PatternCard


def write_run(out: Path, cards: list[PatternCard], summary: dict) -> None:
    (out / "cards").mkdir(parents=True, exist_ok=True)
    for c in cards:
        (out / "cards" / f"{c.pattern_key}.json").write_text(c.model_dump_json(by_alias=True, indent=2))
        (out / "cards" / f"{c.pattern_key}.md").write_text(card_markdown(c))
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
