"""HiBot MCP server (stdio, newline-delimited JSON-RPC 2.0), stdlib only.

Run:  python -m hibot.mcp_server        (cwd = repo root; loads .env via hibot.envfile, never prints values)
Implements initialize, ping, tools/list, tools/call (+ notifications).  stdout carries ONLY protocol messages;
stray prints from tool code are redirected to stderr.
"""
from __future__ import annotations

import contextlib
import json
import sys
from typing import IO, Any, Optional

from hibot.tools import BY_NAME, TOOLS, call_tool

SERVER_INFO = {"name": "hibot-tools", "version": "1.0.0"}
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = ("Read-only investigation tools for Sirius incidents: code search/read, graph traces, read-only SQL on "
                "prod (validated, masked), ticket history, git log/blame. Secrets and PII are filtered; outputs are size-capped.")


def tools_list() -> list:
    return [{"name": t.name, "description": "%s Use: %s" % (t.description, t.when), "inputSchema": t.schema} for t in TOOLS]


def handle(msg: Any) -> Optional[dict]:
    """Return the response for one JSON-RPC message, or None for notifications."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        if isinstance(msg, dict) and "id" in msg and "method" not in msg:
            return None                                       # a response to us; ignore
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    is_note = "id" not in msg
    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            ver = want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            return _ok(mid, {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": tools_list()})
        if method == "tools/call":
            name = params.get("name")
            if name not in BY_NAME:
                return _error(mid, -32602, "unknown tool %r" % name)
            with contextlib.redirect_stdout(sys.stderr):
                res = call_tool(name, params.get("arguments") or {})
            text = json.dumps(res, ensure_ascii=False, default=str)
            return _ok(mid, {"content": [{"type": "text", "text": text}], "isError": res.get("ok") is False})
        if is_note:
            return None                                       # notifications/initialized, cancelled, ...
        return _error(mid, -32601, "method not found: %s" % method)
    except Exception as e:                                    # never crash the server
        return None if is_note else _error(mid, -32603, "internal error: %s" % type(e).__name__)


def _ok(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(inp: IO[str], out: IO[str]) -> None:
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = _error(None, -32700, "parse error")
        else:
            if isinstance(msg, list):
                resp = [r for r in (handle(m) for m in msg) if r is not None] or None
            else:
                resp = handle(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=True) + "\n")
            out.flush()


def main() -> int:
    from hibot import envfile
    names = envfile.load_env()
    print("hibot-mcp: started (%d env vars loaded from .env)" % len(names), file=sys.stderr)
    for s in (sys.stdin, sys.stdout):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
