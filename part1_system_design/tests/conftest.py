import shutil

import pytest
from swda_agent_gateway.config import ROOT, load_settings
from swda_agent_gateway.mcp_server import build_gateway


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("SWDA_AUDIT_HMAC_KEY", "test-key")


@pytest.fixture
def settings(tmp_path):
    s = load_settings()
    s.paths = s.paths.model_copy(update={"audit_log": str(tmp_path / "audit.jsonl")})
    return s


@pytest.fixture
def gw(settings):
    return build_gateway(settings)


@pytest.fixture
def registry_copy(tmp_path):
    dst = tmp_path / "registry.yaml"
    shutil.copy(ROOT / "config" / "agent_registry.yaml", dst)
    return dst
