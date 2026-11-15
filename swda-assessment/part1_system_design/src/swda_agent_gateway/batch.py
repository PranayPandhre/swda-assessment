"""Hourly batch path: same agent code as the online path; idempotent on request_id; bad records go to a DLQ file."""
from __future__ import annotations

import json
from pathlib import Path

from .pii import Redactor
from .runtime import AgentRuntime


def run_batch(runtime: AgentRuntime, agent_id: str, redactor: Redactor, src: Path, out: Path, dlq: Path) -> dict:
    la = runtime.agents[agent_id]
    done = set()
    if out.exists():
        done = {json.loads(line)["request_id"] for line in out.read_text().splitlines() if line.strip()}
    stats = {"processed": 0, "skipped_already_done": 0, "dead_lettered": 0}
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(src, encoding="utf-8") as fin, open(out, "a", encoding="utf-8") as fout, open(dlq, "a", encoding="utf-8") as fdlq:
        for lineno, line in enumerate(fin, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                errs = list(la.validator.iter_errors(rec))
                if errs:
                    raise ValueError("; ".join(f"{'/'.join(map(str, e.path))}: {e.validator}" for e in errs[:3]))
                if rec["request_id"] in done:
                    stats["skipped_already_done"] += 1
                    continue
                rec = {k: (redactor.redact(v)[0] if isinstance(v, str) else v) for k, v in rec.items()}
                resp = runtime.invoke(agent_id, rec)
                fout.write(resp.model_dump_json() + "\n")
                stats["processed"] += 1
            except Exception as exc:  # noqa: BLE001 - a poison record must not stop the batch
                fdlq.write(json.dumps({"line": lineno, "reason": f"{type(exc).__name__}: {exc}"}) + "\n")
                stats["dead_lettered"] += 1
    return stats
