"""Population Stability Index for categorical / binned distributions (input and prediction drift)."""
from __future__ import annotations

import math


def psi(reference: dict[str, float], current: dict[str, float], floor: float) -> float:
    keys = set(reference) | set(current)
    rt, ct = sum(reference.values()) or 1.0, sum(current.values()) or 1.0
    total = 0.0
    for k in keys:
        r = max(reference.get(k, 0.0) / rt, floor)
        c = max(current.get(k, 0.0) / ct, floor)
        total += (c - r) * math.log(c / r)
    return total


def drift_status(value: float, warn: float, alert: float) -> str:
    return "alert" if value >= alert else "warn" if value >= warn else "ok"
