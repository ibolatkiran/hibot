"""DB, graph, schema, ticket and card tools.  Everything goes through the existing guarded layers."""
from __future__ import annotations

import functools
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from hibot.agent import config

from ._common import ToolError, err, existing_roots, finish, scrub, to_int

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")

# ------------------------------------------------------------------ SQL (hibot.db)
_runners: Dict[str, Any] = {}


def _runner(region: str):
    """Cached QueryRunner per region.  Tests replace this with a fake via set_runner_factory()."""
    if _runner_factory is not None:
        return _runner_factory(region)
    r = _runners.get(region)
    if r is None:
        from hibot.db.runner import QueryRunner
        r = _runners[region] = QueryRunner(region)
    return r


_runner_factory = None


def set_runner_factory(fn) -> None:
    global _runner_factory
    _runner_factory = fn


def _region(region: Optional[str]) -> str:
    r = str(region or "EU").strip().upper()
    if r not in config.REGIONS:
        raise ToolError("region must be EU or APAC")
    return r


def _result(res) -> dict:
    d = res.to_dict() if hasattr(res, "to_dict") else dict(res)
    d.pop("masking", None)
    if not d.get("rewrites"):
        d.pop("rewrites", None)
    if not d.get("warnings"):
        d.pop("warnings", None)
    d = finish(d)
    if d.get("truncated") and d.get("ok"):
        d["note"] = "rows cut by row_limit or the output size cap; add filters or select fewer columns"
    d["row_count_returned"] = len(d.get("rows") or [])
    return d


def sql(query: str, region: str = "EU", row_limit: int = 200) -> dict:
    """Ad-hoc read-only SELECT (validated by hibot.db.safe_sql, PII columns refused, TOP enforced, audited)."""
    try:
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query must be a non-empty SQL string")
        if len(query) > 6000:
            raise ToolError("query too long (max 6000 chars)")
        region = _region(region)
        row_limit = to_int(row_limit, "row_limit", 1, 1000, 200)
        res = _runner(region).run_sql(query, row_limit=row_limit)
    except ToolError as e:
        return err(e)
    except Exception as e:                                     # RunnerError, missing env, driver problems
        return err(_safe_exc(e))
    return _result(res)


def run_template(query_id: str, params: Optional[dict] = None, region: str = "EU") -> dict:
    """Run a vetted query template (see list_templates)."""
    try:
        if not isinstance(query_id, str) or not re.match(r"^[A-Za-z0-9_]{2,60}$", query_id):
            raise ToolError("query_id must be a template id such as wo_header")
        if params is not None and not isinstance(params, dict):
            raise ToolError("params must be an object")
        region = _region(region)
        res = _runner(region).run_template(query_id, params or {})
    except ToolError as e:
        return err(e)
    except Exception as e:
        return err(_safe_exc(e))
    return _result(res)


def list_templates(query_id: Optional[str] = None, region: Optional[str] = None) -> dict:
    """Catalog of query templates; with query_id the full interpretation guide of one template."""
    try:
        from hibot.db.templates import TemplateRegistry
        reg = TemplateRegistry.load()
        if query_id:
            if query_id not in reg.ids():
                return err("unknown template %r" % query_id, known=reg.ids())
            return finish({"ok": True, "query_id": query_id, "description": reg.describe(query_id)}, 12000)
        reg_name = _region(region) if region else None
        return finish({"ok": True, "ids": reg.ids(), "catalog": reg.catalog_text(reg_name)}, 12000)
    except ToolError as e:
        return err(e)
    except Exception as e:
        return err(_safe_exc(e))


def _safe_exc(e: Exception) -> str:
    from hibot.db.runner import scrub as _scrub
    return "%s: %s" % (type(e).__name__, _scrub(str(e))[:300])


# ------------------------------------------------------------------ code graph
@lru_cache(maxsize=1)
def _graph():
    from hibot.knowledge.graph import CodeGraph
    return CodeGraph.load()


def _g(fn):
    @functools.wraps(fn)
    def run(*a, **k):
        try:
            return finish(_clean(fn(_graph(), *a, **k)))
        except FileNotFoundError:
            return err("knowledge/graph/graph.json is missing; run tools/graph/build.py")
        except ToolError as e:
            return err(e)
    return run


def _clean(v: Any) -> dict:
    if isinstance(v, list):
        v = {"items": v}
    out = {"ok": "error" not in v} if isinstance(v, dict) else {"ok": True}
    out.update(v)
    return json.loads(json.dumps(out, default=str))


def _need(v: Any, name: str, maxlen: int = 300) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ToolError("%s must be a non-empty string" % name)
    return v.strip()[:maxlen]


@_g
def graph_trace_endpoint(g, endpoint: str, include_backups: bool = False) -> dict:
    """Backend endpoint -> controller -> services/repositories -> stored procs -> tables (+ calling screens)."""
    return g.trace_endpoint(_need(endpoint, "endpoint"), bool(include_backups))


@_g
def graph_trace_screen(g, app: str, screen_or_route: str, include_backups: bool = False) -> dict:
    """UI screen/route of one frontend app -> API endpoints -> stored procs -> tables."""
    return g.trace_screen(_need(app, "app", 60), _need(screen_or_route, "screen_or_route"), bool(include_backups))


@_g
def graph_trace_db_object(g, name: str, include_backups: bool = False, limit: int = 30) -> dict:
    """Table/proc/view/function -> who reads/writes/calls it, up to endpoints and screens."""
    return g.trace_db_object(_need(name, "name"), bool(include_backups), to_int(limit, "limit", 1, 60, 30))


@_g
def graph_search_text(g, text: str, app: Optional[str] = None, limit: int = 10) -> dict:
    """BM25 free-text search over graph nodes (screens, routes, endpoints, procs, tables)."""
    return {"items": g.search_text(_need(text, "text"), app=app, limit=to_int(limit, "limit", 1, 30, 10))}


@_g
def graph_search_error_text(g, text: str, limit: int = 8) -> dict:
    """Map a user-visible or log error message to the code/SP that raises it."""
    return {"items": g.search_error_text(_need(text, "text", 600), limit=to_int(limit, "limit", 1, 20, 8))}


# ------------------------------------------------------------------ column_writers
@lru_cache(maxsize=2)
def _objects(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _find_object(objs: dict, name: str) -> Optional[str]:
    low = name.lower()
    for k in objs:
        if k.lower() == low:
            return k
    if "." not in low:
        cands = [k for k in objs if k.lower().endswith("." + low)]
        return cands[0] if len(cands) == 1 else None
    return None


def _repo_rel(path: str) -> str:
    p = os.path.normcase(os.path.realpath(path))
    for r in existing_roots() + [Path(x) for x in config.repo_roots()]:
        rr = os.path.normcase(os.path.realpath(str(r)))
        if p.startswith(rr + os.sep):
            return "%s/%s" % (r.name, os.path.relpath(path, str(r)).replace("\\", "/"))
    return os.path.basename(path)


_COMPARE_RE = re.compile(r"(?i)\b(where|and|or|on|join|having|if|when|is)\b")


def _assignments(src: str, column: str) -> List[dict]:
    rx2 = re.compile(r"\b%s\s*=(?!=)" % re.escape(column), re.I)
    out = []
    for i, ln in enumerate(src.splitlines(), 1):
        code = ln.split("--", 1)[0]
        m = rx2.search(code)
        if not m:
            continue
        before = code[:m.start()]
        kind = "compare" if _COMPARE_RE.search(before) and not re.search(r"(?i)\bset\b", before) else "assign"
        out.append({"line": i, "kind": kind, "text": scrub(ln.strip())[:160]})
    return out


def column_writers(table: str, column: str) -> dict:
    """Stored procedures/triggers that write `table` and contain an assignment `column = ...` (heuristic regex on SP source)."""
    try:
        table, column = _need(table, "table", 120), _need(column, "column", 80)
        if not re.match(r"^[\w.\[\]]+$", table) or not re.match(r"^\w+$", column):
            raise ToolError("table like Operation.WorkOrder, column a plain identifier")
        table = table.replace("[", "").replace("]", "")
        path = str(config.knowledge_dir() / "schema" / "objects.json")
        objs = _objects(path)
    except ToolError as e:
        return err(e)
    except FileNotFoundError:
        return err("knowledge/schema/objects.json is missing; run tools/schema_catalog/build.py")
    key = _find_object(objs, table)
    if not key or objs[key].get("type") not in ("table", "view"):
        sug = [k for k in objs if table.lower().split(".")[-1] in k.lower() and objs[k].get("type") == "table"][:8]
        return err("table %r not found in the schema catalog" % table, suggestions=sug)
    tbl = objs[key]
    cols = [c.get("name") if isinstance(c, dict) else c for c in tbl.get("columns") or []]
    col_ok = any(str(c).lower() == column.lower() for c in cols)
    wr: Dict[str, str] = {}
    for n in tbl.get("written_by") or []:
        wr[n] = "proc"
    for n, o in objs.items():                                         # procs not listed in written_by, and triggers
        if o.get("type") == "procedure" and key in (o.get("written_tables") or []):
            wr.setdefault(n, "proc")
        for t in o.get("triggers") or []:
            if key in (t.get("written_tables") or []):
                wr.setdefault(n + "::" + t.get("name", "?"), "trigger")
    writers, no_assign = [], []
    for name, kind in sorted(wr.items()):
        base = name.split("::")[0]
        o = objs.get(base) or {}
        sf = (o.get("source_files") or {})
        src_path = sf.get("EU") or next(iter(sf.values()), None)
        entry = {"name": name, "kind": kind, "backup_or_copy": bool(o.get("is_backup_or_copy"))}
        if not src_path or not os.path.isfile(src_path):
            entry["source"] = None
            no_assign.append(entry["name"])
            continue
        try:
            with open(src_path, encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError:
            no_assign.append(name)
            continue
        hits = _assignments(src, column)
        if not hits:
            no_assign.append(name)
            continue
        entry["source"] = _repo_rel(src_path)
        entry["assign_lines"] = [h["line"] for h in hits if h["kind"] == "assign"][:12]
        entry["lines"] = hits[:6]
        writers.append(entry)
    writers.sort(key=lambda w: (w["backup_or_copy"], -len(w["assign_lines"]), w["name"]))
    res = {"ok": True, "table": key, "column": column, "column_in_schema": col_ok,
           "writers": writers, "writers_without_assignment": no_assign[:40],
           "note": "Heuristic: `<Column> =` in the SP source (SET/aliases/compare lines marked kind=compare). Writers listed "
                   "without assignment may still INSERT the column or build dynamic SQL. Backend inline SQL: use code_search."}
    if not col_ok:
        res["warning"] = "column not found in the catalog for %s" % key
    return finish(res, 12000)


# ------------------------------------------------------------------ tickets & cards
def _intake():
    from hibot.agent import intake
    return intake


def _mask(s: Any, n: int) -> str:
    from hibot.db import masking
    return masking.mask_text(str(s or ""))[:n]


def _incident(key_or_text: str) -> dict:
    s = _need(key_or_text, "text_or_key", 6000)
    it = _intake()
    if KEY_RE.match(s):
        return it.build_incident(key=s)
    return it.build_incident(text=s)


def similar_tickets(text_or_key: str, top_k: int = 5) -> dict:
    """Most similar past incidents (BM25 over the 3.8k-ticket dataset) with resolution notes."""
    try:
        top_k = to_int(top_k, "top_k", 1, 15, 5)
        s = _need(text_or_key, "text_or_key", 6000)
        it = _intake()
        if KEY_RE.match(s):
            inc = it.build_incident(key=s)
            sim = (inc.get("similar") or [])[:top_k]
        else:
            inc = it.from_text(s)
            sim = it.find_similar_for_text(inc, top_k)
    except ToolError as e:
        return err(e)
    except Exception as e:
        return err("%s: %s" % (type(e).__name__, str(e)[:200]))
    items = [{**x, "summary": _mask(x.get("summary"), 200), "resolution": _mask(x.get("resolution"), 300)} for x in sim]
    return finish({"ok": True, "items": items})


def ticket(key: str) -> dict:
    """Incident record of a Jira key (dataset or inbox Jira XML): summary, description, comments, entities (text is masked)."""
    try:
        key = _need(key, "key", 40)
        if not KEY_RE.match(key):
            raise ToolError("key must look like SM-1234567")
        inc = _intake().build_incident(key=key)
    except ToolError as e:
        return err(e)
    except Exception as e:
        return err("%s: %s" % (type(e).__name__, str(e)[:300]))
    out = {"ok": True, "key": inc.get("key"), "summary": _mask(inc.get("summary"), 300),
           "desc": _mask(inc.get("desc"), 3500),
           "comments": [{"created": c.get("created"), "text": _mask(c.get("text"), 400)} for c in (inc.get("comments") or [])[:6]],
           "entities": inc.get("entities"), "comment_entities": inc.get("comment_entities"),
           "problem_type": inc.get("problem_type"), "app": inc.get("app"), "country": inc.get("country"),
           "region_guess": inc.get("region_guess"), "status": inc.get("status"), "created": inc.get("created"),
           "resolved": inc.get("resolved"), "resolution": _mask(inc.get("resolution"), 400),
           "screen_hints": (inc.get("screen_hints") or [])[:6],
           "similar": [{"key": s.get("key"), "score": s.get("score"), "summary": _mask(s.get("summary"), 120)}
                       for s in (inc.get("similar") or [])[:5]]}
    return finish(out, 12000)


def cards_list() -> dict:
    """Known-issue cards (learned root causes): id, title, status, confidence, keywords."""
    from hibot.agent import cards
    items = [{"id": c.get("id"), "title": c.get("title"), "status": c.get("status"), "confidence": c.get("confidence"),
              "keywords": ((c.get("symptom_signature") or {}).get("keywords") or [])[:8]}
             for c in cards.load_cards()]
    return finish({"ok": True, "count": len(items), "items": items})


def cards_match(text_or_key: str, limit: int = 3) -> dict:
    """Which known-issue cards fit this ticket (key or free text); returns score, reasons and the card root cause."""
    from hibot.agent import cards
    try:
        limit = to_int(limit, "limit", 1, 8, 3)
        inc = _incident(text_or_key)
        ms = cards.match_cards(inc, cards.load_cards(), limit=limit)
    except ToolError as e:
        return err(e)
    except Exception as e:
        return err("%s: %s" % (type(e).__name__, str(e)[:200]))
    items = [{"id": m["card"].get("id"), "title": m["card"].get("title"), "score": m.get("score"),
              "reasons": m.get("reasons"), "missing_entities": m.get("missing_entities"),
              "status": m["card"].get("status"), "root_cause": _mask(m["card"].get("root_cause"), 500),
              "fix_steps": [_mask(x, 200) for x in (m["card"].get("fix_steps") or [])[:5]],
              "verification_queries": m["card"].get("verification_queries")} for m in ms]
    return finish({"ok": True, "items": items}, 12000)


def ticket_window(key_or_incident: Any, work_order_id: Any, region: str = "EU", tenant_id: Optional[int] = None) -> dict:
    """Footprint between ticket creation and resolution: field/status changes on the work order, manual-DB-fix suspicion."""
    from hibot.agent import ticket_window as tw
    try:
        region = _region(region)
        wo = to_int(work_order_id, "work_order_id", 1, 2_000_000_000)
        if isinstance(key_or_incident, dict):
            inc = key_or_incident
        else:
            key = _need(key_or_incident, "key_or_incident", 40)
            if not KEY_RE.match(key):
                raise ToolError("key_or_incident must be a Jira key or an incident object")
            inc = _intake().build_incident(key=key)
        runner = _runner(region)
        tid = tenant_id
        if tid is None:
            loc = runner.run_template("wo_locate", {"Number": str(wo)})
            if not loc.ok:
                return err("wo_locate failed: %s" % loc.error)
            rows = [dict(zip(loc.columns, r)) for r in loc.rows]
            rows = [r for r in rows if str(r.get("WorkOrderId")) == str(wo)] or rows
            if not rows:
                return err("work order %s not found in %s" % (wo, region))
            tid = rows[0].get("TenantId")
        queries = []
        for qid in ("wo_field_changes", "wo_status_timeline"):
            res = runner.run_template(qid, {"TenantId": tid, "WorkOrderId": wo})
            if not res.ok:
                return err("%s failed: %s" % (qid, res.error))
            queries.append({"query_id": qid, "executed": True, "ok": True, "columns": res.columns, "rows": res.rows})
        block = tw.compute(inc, queries)
        if block is None:
            return err("ticket window not computable (ticket has no created time)")
        return finish({"ok": True, "tenant_id": tid, "work_order_id": wo, "text": tw.render_text(block), "window": block},
                      12000)
    except ToolError as e:
        return err(e)
    except Exception as e:
        return err(_safe_exc(e))
