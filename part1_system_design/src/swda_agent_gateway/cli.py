"""swda-gateway CLI."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .audit import verify_chain
from .config import load_settings


def _gateway():
    from .mcp_server import build_gateway
    return build_gateway()


def cmd_serve_stdio(_args) -> int:
    from .mcp_server import serve_stdio
    serve_stdio(_gateway())
    return 0


def cmd_serve_http(_args) -> int:
    from .mcp_server import make_http_server
    srv = make_http_server(_gateway())
    print("listening", srv.server_address, file=sys.stderr)
    srv.serve_forever()
    return 0


def cmd_gate(_args) -> int:
    from .evaluation import run_gate
    gw = _gateway()
    ok, report = run_gate(gw.s, gw.runtime)
    print(json.dumps(report, indent=2))
    print("RELEASE GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_batch(args) -> int:
    from .batch import run_batch
    gw = _gateway()
    stats = run_batch(gw.runtime, args.agent, gw.redactor, Path(args.input), Path(args.output), Path(args.dlq))
    print(json.dumps(stats))
    return 0


def cmd_verify_audit(_args) -> int:
    s = load_settings()
    ok, msg = verify_chain(s.path("audit_log"))
    print(("OK: " if ok else "TAMPERING: ") + msg)
    return 0 if ok else 2


def cmd_registry(args) -> int:
    from .registry import AgentRegistry
    s = load_settings()
    reg = AgentRegistry(s.path("registry"), s.limits.allowed_entrypoint_prefix)
    if args.action == "seal":
        reg.seal_file()
        print("registry sealed")
        return 0
    bad = 0
    for r in reg.records:
        problems = reg.exposure_problems(r)
        print(f"{r.agent_id:32} {r.status:10} {'EXPOSED' if not problems else 'blocked: ' + '; '.join(problems)}")
        bad += bool(problems) and r.status == "approved"
    return 1 if bad else 0


def cmd_demo(_args) -> int:
    from .client import StdioMcpClient
    os.environ.setdefault("SWDA_AUDIT_HMAC_KEY", "dev-only-demo-key")
    s = load_settings()

    def session(token: str):
        c = StdioMcpClient({**os.environ, s.auth.token_env_stdio: token})
        init = c.initialize()
        return c, init

    c, init = session("demo-token-portal")
    print("initialize ->", json.dumps(init["result"]["serverInfo"]), init["result"]["protocolVersion"])
    tools = c.request("tools/list")["result"]["tools"]
    print("tools/list ->", [t["name"] for t in tools])
    call = c.request("tools/call", {"name": "service-recommendation-agent", "arguments": {
        "request_id": "demo-1", "employment_status": "unemployed", "age": 38, "preferred_mode": "classroom",
        "text": "I was retrenched last month, my NRIC is S1234567D, email me at a.b@example.com. Need resume and interview help."}})
    print("tools/call ->", json.dumps(call["result"]["structuredContent"], indent=1))
    print("invalid input ->", c.request("tools/call", {"name": "service-recommendation-agent", "arguments": {"request_id": "bad id!", "text": ""}})["result"]["structuredContent"])
    print("unknown tool ->", c.request("tools/call", {"name": "experimental-triage-agent", "arguments": {}})["error"])
    c.close()
    c2, _ = session("demo-token-analyst")
    print("analyst tools/list ->", c2.request("tools/list")["result"]["tools"], "(no entitlement => no discovery)")
    c2.close()
    ok, msg = verify_chain(s.path("audit_log"))
    print("audit chain:", msg if ok else "BROKEN")
    return 0 if ok else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="swda-gateway")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve-stdio").set_defaults(fn=cmd_serve_stdio)
    sub.add_parser("serve-http").set_defaults(fn=cmd_serve_http)
    sub.add_parser("gate").set_defaults(fn=cmd_gate)
    b = sub.add_parser("batch")
    b.add_argument("--agent", default="service-recommendation-agent")
    b.add_argument("--input", required=True)
    b.add_argument("--output", required=True)
    b.add_argument("--dlq", required=True)
    b.set_defaults(fn=cmd_batch)
    sub.add_parser("verify-audit").set_defaults(fn=cmd_verify_audit)
    r = sub.add_parser("registry")
    r.add_argument("action", choices=["validate", "seal"])
    r.set_defaults(fn=cmd_registry)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
