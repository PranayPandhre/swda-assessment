"""Minimal metrics (Prometheus text format) and structured logging. Prod: OpenTelemetry -> CloudWatch."""
from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self._latency: dict[str, list[float]] = defaultdict(list)

    def inc(self, name: str, **labels) -> None:
        with self._lock:
            self._counters[(name, tuple(sorted(labels.items())))] += 1

    def observe_latency(self, agent_id: str, ms: float) -> None:
        with self._lock:
            self._latency[agent_id].append(ms)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": {f"{n}{dict(lab)}": v for (n, lab), v in self._counters.items()},
                "latency_ms": {a: sorted(v) for a, v in self._latency.items()},
            }

    def prometheus(self) -> str:
        lines = []
        with self._lock:
            for (name, labels), v in sorted(self._counters.items()):
                lab = ",".join(f'{k}="{val}"' for k, val in labels)
                lines.append(f"{name}{{{lab}}} {v}")
            for agent, vals in self._latency.items():
                if vals:
                    s = sorted(vals)
                    lines.append(f'agent_latency_ms_p95{{agent="{agent}"}} {s[min(len(s) - 1, int(0.95 * len(s)))]:.3f}')
        return "\n".join(lines) + "\n"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {"level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        base.update(getattr(record, "ctx", {}))
        return json.dumps(base, default=str)


def get_logger(name: str = "swda.gateway") -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler()          # stderr: stdout is reserved for the MCP stdio protocol
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log
