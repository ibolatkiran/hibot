"""Step 4: code context.  Thin adapter over hibot.knowledge.graph.CodeGraph with a graceful fallback.

Interface used (all return JSON-serialisable dicts/lists carrying `evidence` and file paths):
    CodeGraph.load(), .trace_endpoint(endpoint), .trace_screen(app, screen_or_route), .trace_db_object(name),
    .search_error_text(text, limit), .search_text(text, app=None, limit)

Fallback when the module / graph.json is missing: knowledge/codemap/trace_index.json (endpoint and screen traces only).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from . import config

KINDS = ("endpoint", "screen", "db_object", "error_text", "text")


def _shrink(o: Any, max_list: int, max_str: int) -> Any:
    if isinstance(o, dict):
        return {k: _shrink(v, max_list, max_str) for k, v in o.items()}
    if isinstance(o, list):
        out = [_shrink(v, max_list, max_str) for v in o[:max_list]]
        if len(o) > max_list:
            out.append("...(+%d more)" % (len(o) - max_list))
        return out
    if isinstance(o, str) and len(o) > max_str:
        return o[:max_str] + "..."
    return o


def _compact(obj: Any, limit: int) -> Any:
    """Keep the structure but shrink lists/strings until the JSON fits `limit` chars."""
    def size(x):
        return len(json.dumps(x, ensure_ascii=False, default=str, separators=(",", ":")))
    if size(obj) <= limit:
        return obj
    for max_list, max_str in ((10, 200), (5, 140), (3, 100), (2, 70), (1, 50)):
        small = _shrink(obj, max_list, max_str)
        if size(small) <= limit:
            return small
    s = json.dumps(small, ensure_ascii=False, default=str, separators=(",", ":"))
    return {"truncated": True, "partial_json": s[:limit]}


class CodeContext:
    def __init__(self, graph: Any = None, *, use_graph: bool = True, knowledge_dir=None):
        self.graph = graph
        self.graph_error: Optional[str] = None
        self._kdir = knowledge_dir or config.knowledge_dir()
        self._trace: Optional[dict] = None
        if graph is None and use_graph:
            try:
                from hibot.knowledge.graph import CodeGraph      # another module owns this
                self.graph = CodeGraph.load()
            except Exception as e:                                # noqa: BLE001 - degrade, never crash the run
                self.graph = None
                self.graph_error = "%s: %s" % (e.__class__.__name__, str(e)[:160])

    @property
    def backend(self) -> str:
        return "graph" if self.graph is not None else "trace_index-fallback"

    # ---------------------------------------------------------------- public
    def inspect(self, kind: str, value: str, *, app: Optional[str] = None, limit: int = 5,
                max_chars: int = 3000) -> dict:
        if kind not in KINDS:
            return {"ok": False, "error": "unknown kind %r (use %s)" % (kind, ", ".join(KINDS))}
        value = (value or "").strip()
        if not value:
            return {"ok": False, "error": "empty target value"}
        try:
            res = self._graph_call(kind, value, app, limit) if self.graph is not None else self._fallback(kind, value, app, limit)
        except Exception as e:                                    # noqa: BLE001
            res = {"ok": False, "error": "code lookup failed: %s: %s" % (e.__class__.__name__, str(e)[:160])}
        res.setdefault("ok", True)
        res["backend"] = self.backend
        if "result" in res:
            res["result"] = _compact(res["result"], max_chars)
        return res

    def auto_context(self, incident: dict, max_items: int = 3, max_chars: int = 2500) -> List[dict]:
        """Cheap pre-computed context for the first prompt: endpoint traces of the screen hints + error text hits."""
        out: List[dict] = []
        seen = set()
        for h in (incident.get("screen_hints") or [])[:max_items]:
            for ep in (h.get("endpoints") or [])[:2]:
                if ep in seen:
                    continue
                seen.add(ep)
                r = self.inspect("endpoint", ep, max_chars=max_chars // 2)
                out.append({"target": {"kind": "endpoint", "value": ep}, "from": "screen_hint:%s/%s" % (h.get("app"), h.get("screen")), **r})
                if len(out) >= max_items:
                    return out
        for t in ((incident.get("entities") or {}).get("error_texts") or [])[:2]:
            r = self.inspect("error_text", t, max_chars=max_chars // 2)
            out.append({"target": {"kind": "error_text", "value": t}, "from": "ticket error text", **r})
        return out[:max_items + 2]

    # ------------------------------------------------------------- graph path
    def _graph_call(self, kind, value, app, limit):
        g = self.graph
        if kind == "endpoint":
            return {"result": g.trace_endpoint(value)}
        if kind == "screen":
            return {"result": g.trace_screen(app or "", value)}
        if kind == "db_object":
            return {"result": g.trace_db_object(value)}
        if kind == "error_text":
            return {"result": g.search_error_text(value, limit)}
        return {"result": g.search_text(value, app, limit)}

    # ---------------------------------------------------------------- fallback
    def _trace_index(self) -> dict:
        if self._trace is None:
            p = self._kdir / "codemap" / "trace_index.json"
            self._trace = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return self._trace

    def _fallback(self, kind, value, app, limit):
        ti = self._trace_index()
        if not ti:
            return {"ok": False, "error": "no code graph and no knowledge/codemap/trace_index.json (run tools/code_map/build.py)"}
        v = value.lower()
        if kind == "endpoint":
            hits = []
            for ep, down in ti.get("endpoint_downstream", {}).items():
                if v in ep.lower():
                    hits.append({"endpoint": ep, "screens": ti.get("by_endpoint", {}).get(ep, {}),
                                 "downstream": down, "evidence": "knowledge/codemap/trace_index.json"})
                    if len(hits) >= limit:
                        break
            if not hits:
                return {"ok": False, "error": "no endpoint matching %r in trace_index" % value}
            return {"result": hits}
        if kind == "screen":
            hits = []
            for a, screens in ti.get("by_screen", {}).items():
                if app and a != app:
                    continue
                for f, eps in screens.items():
                    if v in f.lower():
                        hits.append({"app": a, "screen_file": f, "endpoints": eps[:6],
                                     "evidence": "knowledge/codemap/trace_index.json"})
                        if len(hits) >= limit:
                            return {"result": hits}
            if not hits:
                return {"ok": False, "error": "no screen matching %r in trace_index" % value}
            return {"result": hits}
        return {"ok": False, "error": "kind %r needs the code graph (hibot.knowledge.graph / knowledge/graph/graph.json); "
                                      "not available (%s). Try kind endpoint|screen, or read_source." % (kind, self.graph_error or "no graph")}
