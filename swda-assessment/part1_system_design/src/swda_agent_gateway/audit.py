"""Tamper-evident audit log: JSONL records chained by SHA-256 (prev_hash -> hash).

Production: ship to S3 with Object Lock (WORM) + CloudWatch; the chain makes silent edits detectable.
Raw request text is never stored; only an HMAC pseudonym of it (keyed, so low-entropy inputs
cannot be confirmed by hashing guesses).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _canon(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class AuditLog:
    def __init__(self, path: Path, key_env: str, clock=None):
        key = os.environ.get(key_env)
        if not key:
            raise RuntimeError(f"audit HMAC key env var {key_env} is not set; refusing to start without it")
        self._key = key.encode()
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self._prev, self._seq = self._tail()

    def _tail(self) -> tuple[str, int]:
        if not self._path.exists():
            return GENESIS, 0
        last = None
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if last is None:
            return GENESIS, 0
        rec = json.loads(last)
        return rec["hash"], rec["seq"]

    def pseudonym(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:32]

    def record(self, event: dict) -> dict:
        with self._lock:
            self._seq += 1
            body = {"seq": self._seq, "ts": self._clock(), **event, "prev_hash": self._prev}
            body["hash"] = hashlib.sha256(_canon(body).encode()).hexdigest()
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(_canon(body) + "\n")
            self._prev = body["hash"]
            return body


def verify_chain(path: Path) -> tuple[bool, str]:
    prev, expected_seq = GENESIS, 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            expected_seq += 1
            claimed = rec.pop("hash")
            if rec.get("prev_hash") != prev or rec.get("seq") != expected_seq:
                return False, f"chain broken at seq {expected_seq}"
            if hashlib.sha256(_canon(rec).encode()).hexdigest() != claimed:
                return False, f"record modified at seq {expected_seq}"
            prev = claimed
    return True, f"{expected_seq} records verified"
