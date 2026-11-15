"""Small MCP stdio client (used by the demo and the end-to-end test)."""
from __future__ import annotations

import json
import subprocess
import sys


class StdioMcpClient:
    def __init__(self, env: dict, cmd: list[str] | None = None):
        self._p = subprocess.Popen(cmd or [sys.executable, "-m", "swda_agent_gateway.cli", "serve-stdio"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, env=env)
        self._id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self._p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}) + "\n")
        self._p.stdin.flush()
        return json.loads(self._p.stdout.readline())

    def notify(self, method: str, params: dict | None = None) -> None:
        self._p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}) + "\n")
        self._p.stdin.flush()

    def initialize(self) -> dict:
        r = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "swda-demo-client", "version": "0"}})
        self.notify("notifications/initialized")
        return r

    def close(self) -> None:
        self._p.stdin.close()
        self._p.wait(timeout=5)
