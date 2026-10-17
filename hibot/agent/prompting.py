"""Builds the first user message (the case file) for the investigation."""
from __future__ import annotations

import json
from typing import List, Optional

from . import cards as cardmod
from . import config


def _j(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), default=str)


def build_case_prompt(inc: dict, matches: List[dict], code_ctx: List[dict], catalog_text: str, *, mode: str,
                      region: str, budget: config.Budget, max_chars: int = 30000,
                      resolved: str = "", baseline: str = "", card_section: str = "", extra: str = "", loop: str = "v1") -> str:
    v2 = loop == "v2"                              # unified loop: tool names and the closing instruction differ
    L: List[str] = []
    L.append("## TICKET %s (source: %s)" % (inc.get("key") or "free text", inc.get("source")))
    L.append("app=%s country=%s company_code=%s language=%s rule-based problem_type=%s (a guess; may be wrong) status=%s" % (
        inc.get("app"), inc.get("country"), inc.get("company_code"), inc.get("language"), inc.get("problem_type"), inc.get("status")))
    L.append("summary: " + (inc.get("summary") or ""))
    L.append("description: " + (inc.get("desc") or ""))
    L.append("extracted entities (scrubbed): " + _j({k: v for k, v in (inc.get("entities") or {}).items() if v}))
    if inc.get("screen_hints"):
        L.append("screen hints (candidates): " + _j([{k: h.get(k) for k in ("app", "screen", "score", "endpoints")} for h in inc["screen_hints"]]))
    if inc.get("comments"):
        L.append("Jira comments (UNVERIFIED, may be wrong):")
        for c in inc["comments"][:4]:
            L.append("- " + (c.get("text") or "")[:300].replace("\n", " "))
    L.append("\n## SIMILAR CASES (text similarity only; not verified root causes)")
    if inc.get("similar"):
        for s in inc["similar"][:5]:
            L.append("- %s score=%s type=%s app=%s country=%s group=%s: %s" % (
                s["key"], s.get("score"), s.get("problem_type"), s.get("app"), s.get("country"), s.get("group_id"), s.get("summary")))
    else:
        L.append("(none)")
    if inc.get("group"):
        g = inc["group"]
        L.append("recurring group %s: %s tickets, e.g. %r, common terms %s" % (g.get("group_id"), g.get("size"), g.get("representative_title"), g.get("common_terms")))
    L.append("\n## CANDIDATE KNOWN-ISSUE CARDS (hypotheses; verify before reporting)")
    if matches:
        for m in matches:
            c = m["card"]
            L.append("- card %s [%s, match %.2f: %s] %s" % (c["id"], c["status"], m["score"], "; ".join(m["reasons"]), c["title"]))
            L.append("  root cause: " + c["root_cause"][:600])
            for q in cardmod.resolve_card_queries(c, inc):
                L.append("  verify: %s %s params=%s%s" % ("run_template" if v2 else "run_query", q["query_id"], _j(q["params"]),
                         " (need values for: %s)" % ", ".join(q["unresolved"]) if q["unresolved"] else ""))
            L.append("  fix: " + " | ".join(c.get("fix_steps") or [])[:500])
            if m["missing_entities"]:
                L.append("  missing entities: " + ", ".join(m["missing_entities"]))
    else:
        L.append("(no card matched)")
    L.append("\n## CODE CONTEXT (automatic pre-lookup)")
    if code_ctx:
        for c in code_ctx:
            L.append("- %s %s -> %s" % (c.get("target", {}).get("kind"), c.get("target", {}).get("value"),
                                        _j(c.get("result") if c.get("ok") else {"error": c.get("error")})[:1500]))
    else:
        L.append("(none; use inspect_code)")
    pinned_from = len(L)                           # everything from here on is never truncated
    if resolved:
        L.append("\n" + resolved)
    if baseline:
        L.append("\n" + baseline)
    if card_section:
        L.append("\n" + card_section)
    if extra:                                      # round 2: UI gating / column provenance (deterministic helpers)
        L.append("\n" + extra)
    L.append("\n## QUERY TEMPLATE CATALOG (region %s; ! required, ? optional%s)" % (
        region, "; call with the run_template tool: query_id + params" if v2 else ""))
    L.append(catalog_text)
    L.append("\n## RUN SETTINGS")
    L.append("db mode: %s%s" % (mode, "" if mode == "live" else " - queries are validated and rendered but NOT executed; no data comes back"))
    if v2:
        L.append("default region: %s. Budget: %d steps (turns), %d tool calls, %d queries (max %d ad-hoc sql)." % (
            region, budget.max_steps, budget.max_tool_calls, budget.max_queries, budget.max_adhoc_sql))
        L.append('\nStart the investigation. Reply with ONE JSON object: a tool call {"thought","tool","args"} or {"final_report":{...}}.')
    else:
        L.append("default region: %s. Budget: %d investigation steps, %d queries (max %d ad-hoc)." % (
            region, budget.max_steps, budget.max_queries, budget.max_adhoc_sql))
        L.append("\nAnswer with your first action as ONE JSON object.")
    text = "\n".join(L)
    if len(text) > max_chars:                       # shrink the least valuable parts first: resolved/baseline/catalog are kept whole
        head = "\n".join(L[:pinned_from])
        tail = "\n".join(L[pinned_from:])
        text = head[:max(2000, max_chars - len(tail) - 80)] + "\n...[case file truncated]\n" + tail
    return text
