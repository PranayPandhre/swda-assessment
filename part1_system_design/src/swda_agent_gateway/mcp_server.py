"""MCP-compatible JSON-RPC 2.0 gateway (tools capability) with stdio and HTTP transports.

Every tools/call passes the same ordered controls:
  authenticate -> authorise (scope; unknown == unauthorised to avoid enumeration) -> rate limit ->
  size + JSON-Schema validation -> PII redaction -> invoke (allow-listed tools, timeout) ->
  output PII check (fail closed) -> audit record + metrics.
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .audit import AuditLog
from .auth import Authenticator, Client, RateLimiter
from .config import Settings, load_settings
from .observability import Metrics, get_logger
from .pii import Redactor
from .registry import AgentRegistry
from .runtime import AgentRuntime, AgentTimeout
from .toolbox import ToolNotPermitted

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603
UNAUTHENTICATED = -32001

log = get_logger()


def _err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _tool_error(req_id, code: str, message: str) -> dict:
    """Tool-execution problems are returned as results with isError=true so the calling model can self-correct."""
    body = {"error_code": code, "message": message}
    return _ok(req_id, {"content": [{"type": "text", "text": json.dumps(body)}], "structuredContent": body, "isError": True})


class McpGateway:
    def __init__(self, settings: Settings, registry: AgentRegistry, runtime: AgentRuntime, auth: Authenticator,
                 limiter: RateLimiter, audit: AuditLog, redactor: Redactor, metrics: Metrics):
        self.s, self.registry, self.runtime = settings, registry, runtime
        self.auth, self.limiter, self.audit, self.redactor, self.metrics = auth, limiter, audit, redactor, metrics

    # ---- entry point ---------------------------------------------------------
    def handle(self, message, client: Client | None) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _err(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "invalid JSON-RPC request")
        req_id, method, params = message.get("id"), message["method"], message.get("params") or {}
        is_notification = "id" not in message
        if client is None:
            self.metrics.inc("mcp_requests_total", outcome="unauthenticated")
            return None if is_notification else _err(req_id, UNAUTHENTICATED, "authentication required")
        if method == "initialize":
            return _ok(req_id, self._initialize(params))
        if method == "ping":
            return _ok(req_id, {})
        if method.startswith("notifications/"):
            return None
        if method == "tools/list":
            return _ok(req_id, {"tools": self._list_tools(client)})
        if method == "tools/call":
            return self._call_tool(req_id, params, client)
        return None if is_notification else _err(req_id, METHOD_NOT_FOUND, f"method not found: {method}")

    # ---- protocol methods ----------------------------------------------------
    def _initialize(self, params: dict) -> dict:
        supported = self.s.server.supported_protocol_versions
        requested = params.get("protocolVersion")
        version = requested if requested in supported else supported[0]   # spec: respond with a version we support
        return {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.s.server.name, "version": self.s.server.version}}

    def _list_tools(self, client: Client) -> list[dict]:
        tools = []
        for la in self.runtime.agents.values():
            if not client.has_scopes(la.record.required_scopes):
                continue                      # callers only discover agents they are entitled to
            tools.append({
                "name": la.record.agent_id, "description": la.record.description, "inputSchema": la.schema,
                "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
                "_meta": {"swda/agent_version": la.record.version, "swda/risk_tier": la.record.risk_tier},
            })
        return tools

    def _call_tool(self, req_id, params: dict, client: Client) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        la = self.runtime.agents.get(name) if isinstance(name, str) else None
        base = {"client_id": client.client_id, "tool": name if isinstance(name, str) else None}
        if la is None or not client.has_scopes(la.record.required_scopes):
            self.audit.record({**base, "outcome": "denied_unknown_or_unauthorised"})
            self.metrics.inc("mcp_tool_calls_total", agent=str(name), outcome="denied")
            return _err(req_id, INVALID_PARAMS, "unknown tool")          # same answer for both: no enumeration
        agent_id, rec = la.record.agent_id, la.record
        base.update(agent_id=agent_id, agent_version=rec.version)

        def done(outcome: str, **extra):
            self.audit.record({**base, "outcome": outcome, **extra})
            self.metrics.inc("mcp_tool_calls_total", agent=agent_id, outcome=outcome)

        if not self.limiter.allow(client):
            done("rate_limited")
            return _tool_error(req_id, "rate_limited", "too many requests; retry later")
        if not isinstance(args, dict) or len(json.dumps(args).encode()) > self.s.limits.max_request_bytes:
            done("invalid_input", reason="size_or_type")
            return _tool_error(req_id, "invalid_input", "arguments too large or not an object")
        errors = sorted(la.validator.iter_errors(args), key=lambda e: list(e.path))
        if errors:                                               # report paths/rules, never echo submitted values
            done("invalid_input", reason="schema")
            msg = "; ".join(f"{'/'.join(map(str, e.path)) or '(root)'}: {e.validator} constraint failed" for e in errors[:5])
            return _tool_error(req_id, "invalid_input", msg)

        clean, pii_found = {}, {}
        for k, v in args.items():
            if isinstance(v, str):
                v, found = self.redactor.redact(v)
                for kind, n in found.items():
                    pii_found[kind] = pii_found.get(kind, 0) + n
            clean[k] = v
        base["input_pseudonym"] = self.audit.pseudonym(json.dumps(clean, sort_keys=True))
        base["request_id"] = clean.get("request_id")
        t0 = time.perf_counter()
        try:
            resp = self.runtime.invoke(agent_id, clean)
        except AgentTimeout:
            done("timeout", pii_redacted=pii_found)
            return _tool_error(req_id, "timeout", "agent exceeded its time budget")
        except ToolNotPermitted as exc:
            done("tool_denied", detail=str(exc), pii_redacted=pii_found)
            return _tool_error(req_id, "agent_error", "agent attempted an unpermitted action")
        except Exception as exc:                                   # noqa: BLE001 - never leak internals to callers
            log.error("agent failure", extra={"ctx": {"agent": agent_id, "err": type(exc).__name__}})
            done("agent_error", error_type=type(exc).__name__, pii_redacted=pii_found)
            return _tool_error(req_id, "agent_error", "agent failed; see audit log with request_id")
        latency = (time.perf_counter() - t0) * 1000
        payload = resp.model_dump(mode="json")
        text = json.dumps(payload)
        if self.redactor.contains_pii(text):
            done("output_blocked", pii_redacted=pii_found)
            return _tool_error(req_id, "output_blocked", "response withheld by output policy")
        self.metrics.observe_latency(agent_id, latency)
        done("ok", latency_ms=round(latency, 3), pii_redacted=pii_found, flags=resp.flags,
             needs_human_review=resp.needs_human_review)
        return _ok(req_id, {"content": [{"type": "text", "text": text}], "structuredContent": payload, "isError": False})


# ---- assembly + transports --------------------------------------------------
def build_gateway(settings: Settings | None = None, registry_path=None) -> McpGateway:
    s = settings or load_settings()
    registry = AgentRegistry(registry_path or s.path("registry"), s.limits.allowed_entrypoint_prefix)
    return McpGateway(
        s, registry, AgentRuntime(s, registry), Authenticator(s.path("clients")),
        RateLimiter(s.limits.rate_limit_per_minute, s.limits.rate_limit_burst),
        AuditLog(s.path("audit_log"), s.audit.hmac_key_env), Redactor(s.pii.patterns, s.pii.redaction_token), Metrics(),
    )


def serve_stdio(gw: McpGateway, stdin=sys.stdin, stdout=sys.stdout) -> None:
    client = gw.auth.authenticate(os.environ.get(gw.s.auth.token_env_stdio))
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out = _err(None, PARSE_ERROR, "parse error")
        else:
            out = gw.handle(msg, client)
        if out is not None:
            stdout.write(json.dumps(out) + "\n")
            stdout.flush()


def make_http_server(gw: McpGateway, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    cap = gw.s.limits.max_request_bytes * 2

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                self._send(200, b'{"status":"ok"}')
            elif self.path == "/readyz":
                ready = bool(gw.runtime.agents)
                self._send(200 if ready else 503, json.dumps({"ready": ready}).encode())
            elif self.path == "/metrics":
                self._send(200, gw.metrics.prometheus().encode(), "text/plain; version=0.0.4")
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):  # noqa: N802
            if self.path != "/mcp":
                return self._send(404, b'{"error":"not found"}')
            length = int(self.headers.get("Content-Length") or 0)
            if length > cap:
                return self._send(413, b'{"error":"payload too large"}')
            auth = self.headers.get("Authorization", "")
            client = gw.auth.authenticate(auth[7:] if auth.startswith("Bearer ") else None)
            if client is None:
                return self._send(401, b'{"error":"unauthenticated"}')
            try:
                msg = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._send(400, json.dumps(_err(None, PARSE_ERROR, "parse error")).encode())
            out = gw.handle(msg, client)
            if out is None:
                return self._send(202, b"")
            self._send(200, json.dumps(out).encode())

        def log_message(self, fmt, *a):  # silence default stderr access log; audit log is the record
            pass

    return ThreadingHTTPServer((host or gw.s.server.http.host, port if port is not None else gw.s.server.http.port), Handler)
