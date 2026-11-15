import json

from p1_helpers import AGENT, ANALYST, GOOD_ARGS, PORTAL, call, edit_registry, seal
from swda_agent_gateway.audit import verify_chain
from swda_agent_gateway.auth import RateLimiter
from swda_agent_gateway.mcp_server import build_gateway
from swda_agent_gateway.models import RecommendationResponse


def audit_lines(settings):
    return [json.loads(x) for x in open(settings.path("audit_log")).read().splitlines()]


def test_initialize_negotiates_version(gw):
    r = gw.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}, PORTAL)
    assert r["result"]["protocolVersion"] == "2024-11-05" and "tools" in r["result"]["capabilities"]
    r = gw.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}, PORTAL)
    assert r["result"]["protocolVersion"] == gw.s.server.supported_protocol_versions[0]


def test_unauthenticated_is_refused(gw):
    r = gw.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, None)
    assert r["error"]["code"] == -32001


def test_discovery_is_scoped_to_entitlements(gw):
    tools = gw.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, PORTAL)["result"]["tools"]
    assert [t["name"] for t in tools] == [AGENT]
    assert tools[0]["inputSchema"]["properties"]["text"]["maxLength"] == gw.s.limits.max_text_chars
    assert gw.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, ANALYST)["result"]["tools"] == []


def test_unknown_and_unentitled_look_identical(gw):
    a = call(gw, ANALYST)                       # real agent, no scope
    b = call(gw, PORTAL, name="no-such-agent")
    c = call(gw, PORTAL, name="experimental-triage-agent")   # draft: never exposed
    assert a["error"] == b["error"] == c["error"]


def test_happy_path_is_schema_valid_and_audited(gw, settings):
    r = call(gw, PORTAL)["result"]
    assert r["isError"] is False
    resp = RecommendationResponse(**r["structuredContent"])
    assert resp.recommendations and [t.tool for t in resp.tool_trace] == ["classify_request", "search_programmes", "rank_programmes"]
    rec = audit_lines(settings)[-1]
    assert rec["outcome"] == "ok" and rec["client_id"] == "portal-app" and rec["agent_version"] == "1.0.0"
    assert verify_chain(settings.path("audit_log"))[0]


def test_invalid_input_rejected_without_echoing_values(gw):
    r = call(gw, PORTAL, args={"request_id": "ok", "text": "secret-looking-value", "age": "thirty", "extra": 1})["result"]
    assert r["isError"] and "secret-looking-value" not in json.dumps(r)


def test_oversized_text_rejected(gw):
    args = {**GOOD_ARGS, "text": "a" * (gw.s.limits.max_text_chars + 1)}
    assert call(gw, PORTAL, args=args)["result"]["structuredContent"]["error_code"] == "invalid_input"


def test_pii_redacted_before_processing_and_never_logged(gw, settings):
    args = {**GOOD_ARGS, "text": "retrenched, my NRIC S1234567D and mail x.y@example.com call 91234567"}
    r = call(gw, PORTAL, args=args)["result"]
    assert r["isError"] is False
    log = open(settings.path("audit_log")).read()
    for raw in ("S1234567D", "x.y@example.com", "91234567"):
        assert raw not in log and raw not in json.dumps(r)
    assert audit_lines(settings)[-1]["pii_redacted"] == {"nric": 1, "email": 1, "phone": 1}


def test_rate_limit_enforced_per_client(settings):
    gw = build_gateway(settings)
    t = [0.0]
    gw.limiter = RateLimiter(60, 2, clock=lambda: t[0])
    outcomes = [call(gw, PORTAL)["result"]["isError"] for _ in range(3)]
    assert outcomes == [False, False, True]
    t[0] += 2.0                                               # bucket refills with time
    assert call(gw, PORTAL)["result"]["isError"] is False


def test_agent_cannot_use_tool_outside_its_allowlist(registry_copy, settings):
    edit_registry(registry_copy, lambda a, raw: a.update(allowed_tools=["classify_request"]))
    seal(registry_copy, settings)
    gw = build_gateway(settings, registry_path=registry_copy)
    r = call(gw, PORTAL)["result"]
    assert r["structuredContent"]["error_code"] == "agent_error"
    assert "unpermitted" in r["structuredContent"]["message"]
    last = json.loads(open(settings.path("audit_log")).read().splitlines()[-1])
    assert last["outcome"] == "tool_denied" and "search_programmes" in last["detail"]


def test_output_policy_fails_closed(gw, monkeypatch):
    la = gw.runtime.agents[AGENT]
    orig = la.agent.run

    def leaky(req, tb):
        resp = orig(req, tb)
        resp.recommendations[0].title = "contact leak@example.com"
        return resp
    monkeypatch.setattr(la.agent, "run", leaky)
    assert call(gw, PORTAL)["result"]["structuredContent"]["error_code"] == "output_blocked"


def test_timeout_is_enforced(settings, monkeypatch):
    settings.limits = settings.limits.model_copy(update={"agent_timeout_seconds": 0.05})
    gw = build_gateway(settings)
    import time
    monkeypatch.setattr(gw.runtime.agents[AGENT].agent, "run", lambda r, tb: time.sleep(0.5))
    assert call(gw, PORTAL)["result"]["structuredContent"]["error_code"] == "timeout"


def test_internal_errors_do_not_leak_details(gw, monkeypatch):
    def boom(r, tb):
        raise RuntimeError("db password=hunter2")
    monkeypatch.setattr(gw.runtime.agents[AGENT].agent, "run", boom)
    out = json.dumps(call(gw, PORTAL))
    assert "hunter2" not in out


def test_malformed_requests(gw):
    assert gw.handle({"id": 1}, PORTAL)["error"]["code"] == -32600
    assert gw.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"}, PORTAL)["error"]["code"] == -32601
    assert gw.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, PORTAL) is None
