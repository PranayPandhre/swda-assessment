"""Typed, validated settings loaded from YAML. No tunable value lives in code."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(os.environ.get("SWDA_P1_ROOT", Path(__file__).resolve().parents[2]))


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpCfg(_Strict):
    host: str
    port: int


class ServerCfg(_Strict):
    name: str
    version: str
    supported_protocol_versions: list[str]
    http: HttpCfg


class PathsCfg(_Strict):
    registry: str
    clients: str
    audit_log: str
    release_gates: str
    golden_set: str


class LimitsCfg(_Strict):
    max_request_bytes: int
    max_text_chars: int
    rate_limit_per_minute: int
    rate_limit_burst: int
    agent_timeout_seconds: float
    allowed_entrypoint_prefix: str


class AuditCfg(_Strict):
    hmac_key_env: str


class PiiCfg(_Strict):
    redaction_token: str
    patterns: dict[str, str]


class AuthCfg(_Strict):
    token_env_stdio: str


class Settings(_Strict):
    server: ServerCfg
    paths: PathsCfg
    limits: LimitsCfg
    audit: AuditCfg
    pii: PiiCfg
    auth: AuthCfg
    root: Path = Field(default=ROOT, exclude=True)

    def path(self, name: str) -> Path:
        return self.root / getattr(self.paths, name)


def load_settings(config_path: Path | None = None, root: Path | None = None) -> Settings:
    root = root or ROOT
    raw = load_yaml(config_path or root / "config" / "gateway.yaml")
    return Settings(**raw, root=root)
