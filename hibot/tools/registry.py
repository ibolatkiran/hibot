"""Tool registry: names, JSON schemas, descriptions, argument validation, dispatch and the ArGPT prompt catalog."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import code, data, git
from ._common import err

S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}


def _p(base: dict, desc: str, **kw) -> dict:
    d = dict(base)
    d["description"] = desc
    d.update(kw)
    return d


@dataclass
class Tool:
    name: str
    func: Callable[..., dict]
    description: str
    when: str
    props: Dict[str, dict]
    required: List[str] = field(default_factory=list)

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": self.props, "required": self.required, "additionalProperties": False}

    def signature(self) -> str:
        parts = []
        for k, v in self.props.items():
            parts.append(k if k in self.required else "%s?" % k)
        return "%s(%s)" % (self.name, ", ".join(parts))


REGION = _p(S, "EU or APAC (default EU)", enum=["EU", "APAC"])

TOOLS: List[Tool] = [
    Tool("code_search", code.code_search,
         "Regex/literal search (ripgrep-style) over the 4 frontends, SiriusApi and the EU/APAC sqlproj repos. "
         "Secret files, node_modules and build output are excluded; credentials are masked. Returns repo-relative file, line, text.",
         "Find where a symbol, message, config key, SP name or column is used; first step to locate code.",
         {"pattern": _p(S, "regex (or literal text when literal=true), max 300 chars"),
          "repos": _p({"type": "array", "items": S}, "repo folder names or fragments, e.g. ['SiriusApi','gfsm-europe']; default all"),
          "glob": _p(S, "file filter, e.g. '*.cs' or '*Controller*'"),
          "max_results": _p(I, "default 50, max 200"), "context": _p(I, "context lines around a hit, 0-5"),
          "literal": _p(B, "treat pattern as plain text")}, ["pattern"]),
    Tool("read_file", code.read_file,
         "Read a window (max 250 lines, numbered) of a source file. path = '<RepoFolder>/<relative path>' as returned by code_search, "
         "or a schema object name like dbo.GetSiriusWorkOrderHistory.",
         "Read the code around a code_search hit or a stored procedure body.",
         {"path": _p(S, "e.g. SiriusApi/Sirius/Controllers/TechnicianController.cs"),
          "start_line": _p(I, "first line (default 1)"), "end_line": _p(I, "last line (default start+119, max 250 lines)")},
         ["path"]),
    Tool("sql", data.sql,
         "Ad-hoc read-only SELECT on the production DB (validated: SELECT only, catalog objects only, PII/credential "
         "columns refused, TOP enforced, results masked, audited). Prefer run_template when one fits.",
         "Check actual data to confirm or refute a hypothesis (always filter by TenantId and a key).",
         {"query": _p(S, "one SELECT/WITH statement"), "region": REGION, "row_limit": _p(I, "default 200, max 1000")},
         ["query"]),
    Tool("run_template", data.run_template,
         "Run a vetted query template by id with parameters (see list_templates). Templates may use PII-safe joins and are masked.",
         "Standard diagnostics: work order header/timeline/field changes, status steps, integration logs.",
         {"query_id": _p(S, "template id, e.g. wo_header"), "params": _p({"type": "object"}, "e.g. {\"TenantId\":5,\"WorkOrderId\":123}"),
          "region": REGION}, ["query_id"]),
    Tool("list_templates", data.list_templates,
         "Catalog of query templates (id, purpose, params); with query_id the full interpretation guide of one template.",
         "Before run_template, to find the right template or learn how to read its result.",
         {"query_id": _p(S, "optional: describe this template"), "region": REGION}),
    Tool("graph_trace_endpoint", data.graph_trace_endpoint,
         "Backend endpoint -> controller/service/repository -> stored procedures -> tables, plus the screens that call it.",
         "Understand what an API route touches in the DB.",
         {"endpoint": _p(S, "route or action, e.g. 'GetWorkOrderDetail' or 'api/technician/...'"),
          "include_backups": _p(B, "include backup/copy objects")}, ["endpoint"]),
    Tool("graph_trace_screen", data.graph_trace_screen,
         "UI screen or route of a frontend app -> endpoints -> stored procedures -> tables.",
         "Start from the screen the user mentions and see what it calls.",
         {"app": _p(S, "frontend app name (e.g. SiriusHeadOffice, SiriusTechnician)"),
          "screen_or_route": _p(S, "screen file/route/title"), "include_backups": _p(B, "include backup/copy objects")},
         ["app", "screen_or_route"]),
    Tool("graph_trace_db_object", data.graph_trace_db_object,
         "Table/proc/view/function -> writers, readers, callers, triggers, and up to endpoints and screens.",
         "Find who writes/reads a table or which screens reach a stored procedure.",
         {"name": _p(S, "e.g. Operation.WorkOrder or dbo.PricingWorkOrder"), "include_backups": _p(B, "include copies"),
          "limit": _p(I, "max items per list (default 30)")}, ["name"]),
    Tool("graph_search_text", data.graph_search_text,
         "BM25 free-text search over graph nodes (screens, routes, endpoints, procs, tables).",
         "Map ticket vocabulary to code entities when you have no exact name.",
         {"text": _p(S, "keywords"), "app": _p(S, "restrict to one frontend app"), "limit": _p(I, "default 10")}, ["text"]),
    Tool("graph_search_error_text", data.graph_search_error_text,
         "Find the code/SP that raises a given error or UI message (also translated texts).",
         "A ticket quotes an error message: find where it comes from.",
         {"text": _p(S, "message text"), "limit": _p(I, "default 8")}, ["text"]),
    Tool("column_writers", data.column_writers,
         "Procedures/triggers that write a table and assign the given column (`Column = ...` in SP source), with SP source path and line numbers.",
         "Who sets this column? e.g. Operation.WorkOrder.LabourCost. Follow with read_file on the reported lines.",
         {"table": _p(S, "schema.Table, e.g. Operation.WorkOrder"), "column": _p(S, "column name, e.g. LabourCost")},
         ["table", "column"]),
    Tool("similar_tickets", data.similar_tickets,
         "Most similar past incidents (BM25) with their recorded resolution. Input: a Jira key or free text.",
         "Check whether this problem was seen and how it was resolved.",
         {"text_or_key": _p(S, "Jira key (SM-123) or description text"), "top_k": _p(I, "default 5, max 15")}, ["text_or_key"]),
    Tool("ticket", data.ticket, "Incident record of a Jira key: summary, description, comments, extracted entities (masked).",
         "Read the ticket itself.", {"key": _p(S, "Jira key, e.g. SM-1777386")}, ["key"]),
    Tool("cards_match", data.cards_match, "Known-issue cards that match a ticket (key or text), with root cause, fix steps and verification queries.",
         "Early check for a known recurring problem.",
         {"text_or_key": _p(S, "Jira key or text"), "limit": _p(I, "default 3")}, ["text_or_key"]),
    Tool("cards_list", data.cards_list, "All known-issue cards (id, title, status, confidence, keywords).",
         "Browse the learned knowledge.", {}),
    Tool("ticket_window", data.ticket_window,
         "What changed on the work order between ticket creation and resolution (field/status changes from the change log vs status history); "
         "flags suspected manual DB fixes. Runs wo_locate/wo_field_changes/wo_status_timeline.",
         "Find out how the ticket was actually fixed by a developer.",
         {"key_or_incident": _p(S, "Jira key"), "work_order_id": _p(I, "Operation.WorkOrder.Id"), "region": REGION,
          "tenant_id": _p(I, "optional; resolved with wo_locate when omitted")}, ["key_or_incident", "work_order_id"]),
    Tool("git_log", git.git_log, "Commit history of an allowed git repo (frontends, SiriusApi): sha, date, author initials, subject. No emails.",
         "Find recent or relevant changes to a file/feature; correlate with the ticket date.",
         {"repo": _p(S, "repo folder, e.g. SiriusApi"), "path": _p(S, "repo-relative file or folder"),
          "since": _p(S, "e.g. '2026-09-01' or '3 weeks ago'"), "grep": _p(S, "text in commit message"),
          "max_count": _p(I, "default 30, max 100")}, ["repo"]),
    Tool("git_blame", git.git_blame, "Last commit/author initials/date per line of a file range (max 80 lines).",
         "Who/when changed a suspicious line.",
         {"repo": _p(S, "repo folder"), "path": _p(S, "repo-relative file"), "start_line": _p(I, "first line"),
          "end_line": _p(I, "last line (default start+39)")}, ["repo", "path", "start_line"]),
]
BY_NAME: Dict[str, Tool] = {t.name: t for t in TOOLS}

_TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


def validate_args(tool: Tool, args: Any) -> Optional[str]:
    """Return an error string, or None.  Integer-looking strings are accepted for integer args (LLM output)."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return "arguments must be an object"
    for k in args:
        if k not in tool.props:
            return "unknown argument %r (allowed: %s)" % (k, ", ".join(tool.props))
    for k in tool.required:
        if args.get(k) in (None, ""):
            return "missing required argument %r" % k
    for k, v in args.items():
        t = tool.props[k].get("type")
        if v is None or t is None:
            continue
        if t == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
            args[k] = int(v)
        elif t == "array" and isinstance(v, str):
            args[k] = [x for x in v.replace(",", " ").split() if x]
        elif t == "boolean" and isinstance(v, str) and v.lower() in ("true", "false"):
            args[k] = v.lower() == "true"
        elif not isinstance(v, _TYPES[t]) or (t == "integer" and isinstance(v, bool)):
            return "argument %r must be %s" % (k, t)
        if tool.props[k].get("enum") and str(args[k]).upper() not in tool.props[k]["enum"]:
            return "argument %r must be one of %s" % (k, tool.props[k]["enum"])
    return None


def call_tool(name: str, arguments: Optional[dict] = None) -> dict:
    """Validate and run one tool; never raises."""
    tool = BY_NAME.get(name)
    if tool is None:
        return err("unknown tool %r" % name, tools=list(BY_NAME))
    args = dict(arguments or {}) if isinstance(arguments or {}, dict) else arguments
    problem = validate_args(tool, args)
    if problem:
        return err(problem, usage=tool.signature())
    try:
        return tool.func(**args)
    except Exception as e:                                       # a tool bug must not kill the loop / server
        return err("tool %s failed: %s: %s" % (name, type(e).__name__, str(e)[:200]))


def tool_catalog() -> str:
    """Compact text for the ArGPT prompt (one block per tool); the model answers with {"tool": name, "args": {...}}."""
    L = ["TOOLS (reply with JSON {\"tool\": \"<name>\", \"args\": {...}}; every result is capped at ~8 KB):"]
    for t in TOOLS:
        L.append("- %s: %s Use: %s" % (t.signature(), t.description, t.when))
    return "\n".join(L)
