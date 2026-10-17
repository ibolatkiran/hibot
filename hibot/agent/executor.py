"""Executes validated LLM actions and turns the outcome into a compact observation.

Every executed or rendered query is appended to `self.queries` (full record for the report / run folder);
the LLM only gets the compact `text`.

DB modes:
  live     hibot.db.runner.QueryRunner (needs HIBOT_DB_EU / HIBOT_DB_APAC); results are masked by the runner.
  dry-run  template parameters are coerced and the SQL validated + rendered, nothing is sent to a database.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from hibot.db import safe_sql
from hibot.db.runner import ENV_VARS, QueryRunner
from hibot.db.templates import TemplateError, TemplateRegistry

from . import config, safety, ui_gating
from .code_context import CodeContext

LIVE, DRY = "live", "dry-run"


def live_available(region: str, env=None) -> bool:
    return bool((os.environ if env is None else env).get(ENV_VARS[region]))


def render_sql(sql: str, args: List[Any]) -> str:
    """Inline bound values for DISPLAY only ('?' outside string literals).  Execution always binds parameters."""
    out, it, i, in_str = [], iter(args), 0, False
    while i < len(sql):
        c = sql[i]
        if in_str:
            out.append(c)
            if c == "'":
                if sql[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 1
                else:
                    in_str = False
        elif c == "'":
            in_str = True
            out.append(c)
        elif c == "?":
            try:
                out.append(_lit(next(it)))
            except StopIteration:
                out.append("?")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _lit(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float, decimal.Decimal)):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return "'%s'" % v.isoformat(sep=" ") if isinstance(v, _dt.datetime) else "'%s'" % v.isoformat()
    return "'%s'" % str(v).replace("'", "''")


def _jsonable(v: Any) -> Any:
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return str(v)
    return v


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "...[truncated %d chars]" % (len(s) - n)


class Executor:
    def __init__(self, *, registry: TemplateRegistry, code: CodeContext, mode: str = DRY, region: str = "EU",
                 budget: Optional[config.Budget] = None, runner_factory: Optional[Callable[[str], Any]] = None):
        if mode not in (LIVE, DRY):
            raise ValueError("mode must be 'live' or 'dry-run'")
        self.registry, self.code, self.mode = registry, code, mode
        self.region = region
        self.budget = budget or config.Budget()
        self._runner_factory = runner_factory
        self._runners: Dict[str, Any] = {}
        self.queries: List[dict] = []
        self.query_count = 0
        self.adhoc_count = 0
        self._described: set = set()
        self.gating_done: set = set()          # endpoints whose UI gating is already in the case file
        self.known_sources: List[str] = []       # sources the case file / inspections pointed at (source-reading guard)
        self.sources_read = 0                    # successful read_source actions
        self.read_paths: List[str] = []          # paths of those reads (round 3: cited code evidence counts for the calibration)

    # ------------------------------------------------------------- dispatch
    def execute(self, action: dict) -> dict:
        """Returns {"ok": bool, "text": str, "record": dict|None}.  Never raises on bad requests."""
        kind = action.get("action")
        try:
            if kind == "run_query":
                obs = self._run_query(action)
            elif kind == "run_sql":
                obs = self._run_sql(action)
            elif kind == "inspect_code":
                obs = self._inspect(action)
            elif kind == "read_source":
                obs = self._read_source(action)
            else:
                obs = {"ok": False, "text": "REJECTED: unsupported action %r" % kind}
        except Exception as e:                                    # noqa: BLE001 - a bug must not kill the run
            obs = {"ok": False, "text": "ERROR while executing the action: %s: %s" % (e.__class__.__name__, str(e)[:200])}
        obs["text"] = _cut(obs["text"], self.budget.max_obs_chars)
        return obs

    # -------------------------------------------------------------- queries
    def _region(self, action: dict) -> str:
        return (action.get("region") or self.region).upper()

    def _budget_error(self, adhoc: bool = False) -> Optional[str]:
        if self.query_count >= self.budget.max_queries:
            return "query budget exhausted (%d/%d). Use what you have and send final_report." % (
                self.query_count, self.budget.max_queries)
        if adhoc and self.adhoc_count >= self.budget.max_adhoc_sql:
            return "ad-hoc SQL budget exhausted (%d/%d). Use run_query templates." % (
                self.adhoc_count, self.budget.max_adhoc_sql)
        return None

    def _runner(self, region: str):
        if region not in self._runners:
            if self._runner_factory:
                self._runners[region] = self._runner_factory(region)
            else:
                self._runners[region] = QueryRunner(region, registry=self.registry)
        return self._runners[region]

    def _run_query(self, a: dict) -> dict:
        qid, params, region = a["query_id"], a.get("params") or {}, self._region(a)
        head = "[run_query %s %s]" % (qid, region)
        err = self._budget_error()
        if err:
            return {"ok": False, "text": "%s REJECTED: %s" % (head, err)}
        try:
            prepared = self.registry.prepare(qid, params, region)
        except TemplateError as e:
            known = "" if qid in self.registry.ids() else " Known ids: " + ", ".join(self.registry.ids())
            return {"ok": False, "text": "%s REJECTED: %s%s" % (head, e, known)}
        self.query_count += 1
        rec = {"n": len(self.queries) + 1, "kind": "template", "query_id": qid, "region": region,
               "params": {k: _jsonable(v) for k, v in prepared.params.items()},
               "sql": prepared.sql, "sql_display": render_sql(prepared.sql, prepared.args),
               "why": a.get("why", ""), "warnings": prepared.warnings, "mode": self.mode,
               "executed": False, "ok": None, "row_count": None, "truncated": False, "columns": [], "rows": [],
               "error": None}
        self.queries.append(rec)
        if self.mode == DRY:
            return {"ok": True, "record": rec, "text": self._dry_text(head, rec)}
        res = self._runner(region).run_template(qid, params)
        return self._live_obs(head, rec, res, qid)

    def _run_sql(self, a: dict) -> dict:
        sql, region = a["sql"], self._region(a)
        head = "[run_sql %s]" % region
        err = self._budget_error(adhoc=True)
        if err:
            return {"ok": False, "text": "%s REJECTED: %s" % (head, err)}
        v = safe_sql.validate(sql, region=region, catalog=self.registry.catalog, row_limit=100)   # allow_pii=False
        if not v.ok:
            return {"ok": False, "text": "%s REJECTED by safe_sql: %s. Prefer a run_query template." % (head, "; ".join(v.errors[:4]))}
        self.query_count += 1
        self.adhoc_count += 1
        rec = {"n": len(self.queries) + 1, "kind": "adhoc", "query_id": "adhoc", "region": region, "params": {},
               "sql": v.positional_sql, "sql_display": v.rewritten_sql, "why": a.get("why", ""),
               "warnings": v.warnings, "rewrites": v.rewrites, "mode": self.mode, "executed": False, "ok": None,
               "row_count": None, "truncated": False, "columns": [], "rows": [], "error": None}
        self.queries.append(rec)
        if self.mode == DRY:
            return {"ok": True, "record": rec, "text": self._dry_text(head, rec)}
        res = self._runner(region).run_sql(sql, row_limit=100)
        return self._live_obs(head, rec, res, "adhoc")

    def _dry_text(self, head: str, rec: dict) -> str:
        t = ("%s NOT EXECUTED (dry-run mode: no database connection, so there is NO data). The request is valid and was "
             "rendered. You cannot know the result: do not assume it. If this query would confirm your hypothesis, say so in "
             "the report as a recommended verification and lower the confidence.\nSQL: %s" % (head, rec["sql_display"]))
        if rec.get("warnings"):
            t += "\nwarnings: " + "; ".join(rec["warnings"][:3])
        return t

    def _live_obs(self, head: str, rec: dict, res: Any, qid: str) -> dict:
        rec["ok"] = bool(res.ok)
        rec["error"] = res.error
        rec["elapsed_ms"] = res.elapsed_ms
        if not res.ok:
            return {"ok": False, "record": rec, "text": "%s FAILED (not executed successfully): %s" % (head, res.error)}
        rec.update(executed=True, row_count=res.row_count, truncated=res.truncated, columns=list(res.columns),
                   rows=[[_jsonable(c) for c in r] for r in res.rows[:200]])
        b = self.budget
        lines = ["%s EXECUTED, %d rows%s, %d ms (values masked)" % (head, res.row_count, " (truncated by row limit)" if res.truncated else "", res.elapsed_ms),
                 "columns: " + " | ".join(res.columns)]
        for i, r in enumerate(res.rows[:b.max_rows], 1):
            lines.append("%d: %s" % (i, " | ".join(_cut("" if c is None else str(_jsonable(c)), b.max_cell_chars) for c in r)))
        if res.row_count > b.max_rows:
            lines.append("... %d more rows not shown" % (res.row_count - b.max_rows))
        if res.row_count == 0:
            lines.append("(no rows)")
        if qid != "adhoc" and qid not in self._described:
            self._described.add(qid)
            try:
                lines.append("how to read: " + self.registry.get(qid).interpret)
            except TemplateError:
                pass
        return {"ok": True, "record": rec, "text": "\n".join(lines)}

    # ----------------------------------------------------------------- code
    def _inspect(self, a: dict) -> dict:
        t = a["target"]
        res = self.code.inspect(t["kind"], t["value"], app=t.get("app"), max_chars=self.budget.max_obs_chars - 400)
        head = "[inspect_code %s: %s]" % (t["kind"], t["value"])
        if not res.get("ok"):
            return {"ok": False, "text": "%s FAILED: %s" % (head, res.get("error"))}
        self._note_sources(res.get("result"))
        gating = ""
        if t["kind"] == "endpoint" and getattr(self.code, "graph", None) is not None:       # round 2: who may use it? (UI gating of the callers)
            try:
                g = ui_gating.analyze(self.code.graph, [t["value"]], max_endpoints=1)
            except Exception:                                                                # noqa: BLE001
                g = {}
            if g.get("text") and not (set(g.get("endpoints") or []) & self.gating_done):
                gating = g["text"] + "\n"
                self.gating_done.update(g.get("endpoints") or [])
        body = json.dumps(res.get("result"), ensure_ascii=False, default=str, separators=(",", ":"))
        return {"ok": True, "text": "%s (source: %s)\n%s%s" % (head, res.get("backend"), gating, body)}

    def _note_sources(self, result: Any) -> None:
        """Remember stored-procedure / screen files an inspection pointed at (used by the 'read the source before giving up' guard)."""
        def walk(o: Any, depth: int = 0) -> None:
            if depth > 6 or len(self.known_sources) > 40:
                return
            if isinstance(o, dict):
                f = o.get("file")
                if isinstance(f, str) and re.search(r"\.(sql|js|cs)$", f, re.I) and f not in self.known_sources and (
                        "StoredProcedures" in f or "/views/" in f or "\\views\\" in f or f.endswith(".sql")):
                    self.known_sources.append(f)
                for v in o.values():
                    walk(v, depth + 1)
            elif isinstance(o, list):
                for v in o[:30]:
                    walk(v, depth + 1)
        walk(result)

    def _read_source(self, a: dict) -> dict:
        res = safety.read_source(a["path"], a.get("start_line"), a.get("end_line"), self.budget.max_source_lines)
        head = "[read_source %s]" % a["path"]
        if not res["ok"]:
            return {"ok": False, "text": "%s REJECTED: %s" % (head, res["error"])}
        self.sources_read += 1
        self.read_paths.append(str(a["path"]))
        return {"ok": True, "text": "%s lines %d-%d of %d%s\n%s" % (
            head, res["start_line"], res["end_line"], res["total_lines"],
            " (range capped)" if res["truncated"] else "", res["text"])}

    def close(self) -> None:
        for r in self._runners.values():
            try:
                r.close()
            except Exception:                                      # noqa: BLE001
                pass
