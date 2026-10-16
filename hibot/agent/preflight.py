"""Deterministic preflight: resolve work-order numbers from the ticket to their real tenant BEFORE the LLM starts.

The ticket's country is the REPORTER's country, not the work order's tenant, so for every work-order-like number in the
incident entities the `wo_locate` template runs (cap MAX_NUMBERS).  In live mode it really executes; in dry-run it is
only rendered (marked not executed).  If the selected region returns 0 rows and the other region is configured, that region
is tried as well; when the work order exists only there the executor's region is switched.
All queries go through Executor (hibot.db templates/runner) and count in the query budget.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .executor import LIVE, Executor, live_available

MAX_NUMBERS = 3
TEMPLATE = "wo_locate"
COLS = ("WorkOrderId", "TenantId", "TenantName", "CountryCode", "StatusId", "MatchedOn")


def candidate_numbers(inc: dict) -> List[str]:
    ents = inc.get("entities") or {}
    out: List[str] = []
    for key in ("work_order_ids_strong", "work_order_ids", "work_case_ids"):
        for v in ents.get(key) or []:
            s = str(v).strip()
            if s.isdigit() and s not in out:
                out.append(s)
    return out[:MAX_NUMBERS]


def _rows(rec: dict) -> List[dict]:
    cols = rec.get("columns") or []
    return [dict(zip(cols, r)) for r in rec.get("rows") or []]


def _locate(ex: Executor, number: str, region: str) -> dict:
    obs = ex._run_query({"query_id": TEMPLATE, "params": {"Number": number}, "region": region,
                         "why": "preflight: find the real TenantId of work order %s" % number})
    rec = obs.get("record")
    return {"rec": rec, "ok": bool(rec and rec.get("executed")), "rows": _rows(rec) if rec else [],
            "error": obs["text"] if not rec else rec.get("error")}


def run(inc: dict, ex: Executor) -> Dict[str, Any]:
    """Returns {"numbers", "resolved", "unresolved", "region", "switched", "executed", "text"}."""
    numbers = candidate_numbers(inc)
    res: Dict[str, Any] = {"numbers": numbers, "resolved": [], "unresolved": [], "region": ex.region, "switched": False,
                           "executed": False, "text": ""}
    if not numbers:
        return res
    other = "APAC" if ex.region == "EU" else "EU"
    notes: List[str] = []
    for num in numbers:
        if ex.query_count >= ex.budget.max_queries:
            break
        first = ex.region
        r = _locate(ex, num, first)
        res["executed"] = res["executed"] or r["ok"]
        where = first
        if ex.mode == LIVE and r["ok"] and not r["rows"] and live_available(other) \
                and ex.query_count < ex.budget.max_queries:
            r2 = _locate(ex, num, other)
            if r2["ok"] and r2["rows"]:
                r, where = r2, other
                notes.append("work order %s was NOT found in %s but exists in %s" % (num, first, other))
                if not res["switched"]:                       # switch the run's default region once, on the first hit
                    ex.region = other
                    res["switched"] = True
                    other = first
            elif r2["ok"]:
                notes.append("work order %s: 0 rows in both %s and %s" % (num, first, other))
        if r["rows"]:
            for row in r["rows"][:5]:
                item = {k: row.get(k) for k in COLS}
                item["Region"] = where
                item["Number"] = num
                res["resolved"].append(item)
        else:
            res["unresolved"].append({"Number": num, "executed": r["ok"], "error": r["error"]})
    res["region"] = ex.region
    res["text"] = format_section(res, ex.mode, notes)
    return res


def format_section(res: dict, mode: str, notes: List[str]) -> str:
    L = ["## RESOLVED WORK ORDERS (deterministic preflight, template wo_locate)"]
    if mode != LIVE:
        L.append("dry-run: wo_locate was rendered but NOT executed, so no tenant is resolved. Do not assume a tenant.")
    for it in res["resolved"]:
        L.append("- number %s -> WorkOrderId=%s TenantId=%s TenantName=%r CountryCode=%s StatusId=%s MatchedOn=%s Region=%s" % (
            it["Number"], it["WorkOrderId"], it["TenantId"], it["TenantName"], it["CountryCode"], it["StatusId"],
            it["MatchedOn"], it["Region"]))
    for it in res["unresolved"]:
        if it["executed"]:
            L.append("- number %s: wo_locate returned 0 rows (not a work order Id/ExternalId in the checked region(s))" % it["Number"])
        elif mode == LIVE:
            L.append("- number %s: wo_locate FAILED: %s" % (it["Number"], str(it["error"])[:150]))
    for n in notes:
        L.append("- note: " + n)
    if res["resolved"]:
        L.append("Use the TenantId above for every further query. The ticket country is the reporter's country, NOT the tenant.")
    if res["switched"]:
        L.append("The run's default region was switched to %s because the work order lives there." % res["region"])
    return "\n".join(L)
