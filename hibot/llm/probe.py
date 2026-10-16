"""Smoke-test the configured LLM endpoint and show the response SHAPE (not secrets).

    python -m hibot.llm.probe            # uses HiBot/.env + environment

Prints: HTTP status, response JSON structure (keys/types, long strings truncated),
the text the adapter extracted, and whether a JSON-only instruction is obeyed.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

from hibot.envfile import load_env
from hibot.llm.client import HttpChatClient, LLMError, _default_opener


def _shape(node, depth=0):
    if depth > 5:
        return "…"
    if isinstance(node, dict):
        return {k: _shape(v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_shape(node[0], depth + 1), "… %d items" % len(node)] if node else []
    if isinstance(node, str):
        return node if len(node) <= 80 else node[:80] + "… (%d chars)" % len(node)
    return node


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                    # noqa: BLE001
            pass
    names = load_env()
    print("Loaded from .env:", ", ".join(names) or "(nothing)")
    captured = {}

    def opener(req: urllib.request.Request, timeout: float):
        status, raw = _default_opener(req, timeout)
        captured["status"], captured["raw"] = status, raw
        return status, raw

    try:
        client = HttpChatClient.from_env(opener=opener, retries=0)
    except LLMError as e:
        print("CONFIG ERROR:", e)
        return 2
    print("Client:", client)
    messages = [
        {"role": "system", "content": "Sen bir test asistanısın. Sadece geçerli JSON döndür, başka hiçbir şey yazma."},
        {"role": "user", "content": 'Şu JSON\'u aynen döndür: {"ok": true, "dil": "tr", "sayi": 42}'},
    ]
    t0 = time.time()
    try:
        text = client.complete(messages, max_tokens=200, temperature=0.0)
        err = None
    except LLMError as e:
        text, err = None, str(e)
    elapsed = time.time() - t0
    print("HTTP status:", captured.get("status"), "| %.1f s" % elapsed)
    raw = captured.get("raw", b"").decode("utf-8", "replace")
    try:
        print("Response shape:\n" + json.dumps(_shape(json.loads(raw)), ensure_ascii=False, indent=2))
    except ValueError:
        print("Response (not JSON, first 300 chars):", raw[:300])
    if err:
        print("ADAPTER ERROR:", err)
        return 1
    print("Extracted text:", repr(text[:300]))
    try:
        ok = json.loads(text.strip().strip("`").removeprefix("json").strip()).get("sayi") == 42
    except Exception:                                        # noqa: BLE001
        ok = False
    print("JSON instruction obeyed:", "YES" if ok else "NO (jsonio repair loop will be needed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
