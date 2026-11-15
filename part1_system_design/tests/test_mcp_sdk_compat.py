"""Interoperability: the official MCP Python SDK client can talk to the gateway (skipped if `mcp` is not installed;
install with `pip install -e .[mcp-compat]`)."""
import asyncio
import os
import sys

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from p1_helpers import AGENT, GOOD_ARGS  # noqa: E402


def g(obj, camel, snake):
    """SDK versions differ in attribute style (camelCase vs snake_case); accept either."""
    return getattr(obj, snake) if hasattr(obj, snake) else getattr(obj, camel)


def test_official_sdk_client_can_discover_and_call_the_agent():
    async def go():
        env = {**os.environ, "SWDA_MCP_TOKEN": "demo-token-portal", "SWDA_AUDIT_HMAC_KEY": "k"}
        params = StdioServerParameters(command=sys.executable, args=["-m", "swda_agent_gateway.cli", "serve-stdio"], env=env)
        async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            res = await session.call_tool(AGENT, GOOD_ARGS)
            bad = await session.call_tool(AGENT, {"request_id": "x"})
            return init, tools, res, bad

    init, tools, res, bad = asyncio.run(go())
    assert g(init, "serverInfo", "server_info").name == "swda-agent-gateway"
    assert [t.name for t in tools.tools] == [AGENT] and g(tools.tools[0].annotations, "readOnlyHint", "read_only_hint") is True
    assert g(res, "isError", "is_error") is False and "ambiguous_category" in g(res, "structuredContent", "structured_content")["flags"]
    assert g(bad, "isError", "is_error") is True
