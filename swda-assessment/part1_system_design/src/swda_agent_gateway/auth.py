"""Caller authentication (token digest lookup), scope checks and token-bucket rate limiting."""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import load_yaml


@dataclass(frozen=True)
class Client:
    client_id: str
    scopes: frozenset[str]
    rate_limit_per_minute: int | None = None

    def has_scopes(self, required: list[str]) -> bool:
        return set(required) <= self.scopes


class Authenticator:
    def __init__(self, clients_path: Path):
        raw = load_yaml(clients_path)["clients"]
        self._by_digest = {
            c["token_sha256"]: Client(c["client_id"], frozenset(c.get("scopes", [])), c.get("rate_limit_per_minute"))
            for c in raw
        }

    def authenticate(self, token: str | None) -> Client | None:
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        match = None
        for known, client in self._by_digest.items():   # compare against all: no early exit on first match
            if hmac.compare_digest(known, digest):
                match = client
        return match


@dataclass
class _Bucket:
    tokens: float
    updated: float = field(default_factory=time.monotonic)


class RateLimiter:
    def __init__(self, default_per_minute: int, burst: int, clock=time.monotonic):
        self._default, self._burst, self._clock = default_per_minute, burst, clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, client: Client) -> bool:
        rate = (client.rate_limit_per_minute or self._default) / 60.0
        now = self._clock()
        with self._lock:
            b = self._buckets.setdefault(client.client_id, _Bucket(self._burst, now))
            b.tokens = min(self._burst, b.tokens + (now - b.updated) * rate)
            b.updated = now
            if b.tokens >= 1:
                b.tokens -= 1
                return True
            return False
