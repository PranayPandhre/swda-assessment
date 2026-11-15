import json
import os
import threading
import urllib.error
import urllib.request

from p1_helpers import GOOD_ARGS
from swda_agent_gateway.client import StdioMcpClient
from swda_agent_gateway.mcp_server import make_http_server


def test_stdio_end_to_end(settings, tmp_path, monkeypatch):
    env = {**os.environ, "SWDA_MCP_TOKEN": "demo-token-portal", "SWDA_AUDIT_HMAC_KEY": "k"}
    c = StdioMcpClient(env)
    assert c.initialize()["result"]["serverInfo"]["name"] == "swda-agent-gateway"
    assert c.request("tools/list")["result"]["tools"][0]["name"] == "service-recommendation-agent"
    r = c.request("tools/call", {"name": "service-recommendation-agent", "arguments": GOOD_ARGS})
    assert r["result"]["isError"] is False
    c.close()


def test_stdio_without_valid_token_is_refused():
    env = {**os.environ, "SWDA_MCP_TOKEN": "wrong", "SWDA_AUDIT_HMAC_KEY": "k"}
    c = StdioMcpClient(env)
    assert c.request("tools/list")["error"]["code"] == -32001
    c.close()


def test_http_transport_auth_health_and_limits(gw):
    srv = make_http_server(gw, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(body, token="demo-token-portal"):
        req = urllib.request.Request(base + "/mcp", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        return urllib.request.urlopen(req)

    try:
        assert json.load(urllib.request.urlopen(base + "/healthz"))["status"] == "ok"
        assert json.load(urllib.request.urlopen(base + "/readyz"))["ready"] is True
        ok = json.load(post({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "service-recommendation-agent", "arguments": GOOD_ARGS}}))
        assert ok["result"]["isError"] is False
        for bad_token in ("nope", ""):
            try:
                post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=bad_token)
                raise AssertionError("expected 401")
            except urllib.error.HTTPError as e:
                assert e.code == 401
        try:
            post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "pad": "x" * (gw.s.limits.max_request_bytes * 3)})
            raise AssertionError("expected 413")
        except urllib.error.HTTPError as e:
            assert e.code == 413
        assert "mcp_tool_calls_total" in urllib.request.urlopen(base + "/metrics").read().decode()
    finally:
        srv.shutdown()
