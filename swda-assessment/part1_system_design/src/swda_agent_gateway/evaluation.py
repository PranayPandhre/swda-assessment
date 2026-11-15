"""Release gate: golden-set evaluation with thresholds from config. Non-zero exit blocks the pipeline."""
from __future__ import annotations

import json
import time

from .config import Settings, load_yaml
from .runtime import AgentRuntime
from .toolbox import ToolBox


def run_gate(settings: Settings, runtime: AgentRuntime) -> tuple[bool, dict]:
    gates = load_yaml(settings.path("release_gates"))
    la = runtime.agents[gates["agent_id"]]
    schema_keys = set(la.schema["properties"])
    rows = [json.loads(x) for x in settings.path("golden_set").read_text().splitlines() if x.strip()]
    correct = hits = scored = unclassified = 0
    latencies = []
    for row in rows:
        req = {k: v for k, v in row.items() if k in schema_keys}
        t0 = time.perf_counter()
        resp = la.agent.run(req, ToolBox(la.agent.tools, la.record.allowed_tools))
        latencies.append((time.perf_counter() - t0) * 1000)
        correct += resp.category == row["expected_category"]
        unclassified += resp.category == "unclassified"
        if row["expected_programmes"]:
            scored += 1
            topk = [r.programme_id for r in resp.recommendations[: gates["hit_rate_k"]]]
            hits += bool(set(topk) & set(row["expected_programmes"]))
    latencies.sort()
    metrics = {
        "category_accuracy": correct / len(rows),
        "hit_rate_at_k": hits / scored if scored else 1.0,
        "p95_latency_ms": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))],
        "unclassified_rate": unclassified / len(rows),
        "n": len(rows),
    }
    checks = {
        "category_accuracy": metrics["category_accuracy"] >= gates["min_category_accuracy"],
        "hit_rate_at_k": metrics["hit_rate_at_k"] >= gates["min_hit_rate_at_k"],
        "p95_latency_ms": metrics["p95_latency_ms"] <= gates["max_p95_latency_ms"],
        "unclassified_rate": metrics["unclassified_rate"] <= gates["max_unclassified_rate"],
    }
    return all(checks.values()), {"metrics": metrics, "checks": checks}
