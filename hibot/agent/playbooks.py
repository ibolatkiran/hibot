"""Deterministic playbooks: baseline evidence that is collected BEFORE the LLM starts (after preflight).

The LLM tends to stop at the first plausible explanation ("user_error: already cancelled").  A human analyst always starts
with the same few queries, so the code runs them: which ones depends on the incident problem_type, keywords and the work
order that preflight resolved.  Everything goes through the Executor (hibot.db templates / runner), counts in the query budget
(RESERVE queries are always left to the LLM) and is injected into the case file as compact text ("BASELINE EVIDENCE").

  status / cancel / reject / close / "cannot change status"   wo_header, wo_status_timeline, wo_field_changes,
                                                              wo_status_transition_check (+ status_step_check for the
                                                              last status change found in the ticket window)
  integration_failure                                         wo_header, wo_erp_sap, sap_rfc_rest_calls
  notification_failure                                        wo_header, notifications_for_wo
  master_data_request with a SKU                              (tenant_lookup if no work order) + product_sku_lookup

Live mode only: in dry-run nothing could be learned and the budget would be burnt.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import ticket_window
from .executor import LIVE, Executor

RESERVE = 2                                    # queries always left for the LLM
STATUS_RE = re.compile(r"cancel|iptal|reject|reddet|\bclos|kapat|kapan|status|stat[uü]|durum|cannot|can'?t|unable|"
                       r"edilemi|yapılamı|yapilami|no proof|reopen|re-open|stuck", re.I)
GUARD_RE = re.compile(STATUS_RE.pattern + r"|error|hata|fail|exception", re.I)
STATUS_QIDS = ("wo_status_timeline", "wo_field_changes", "wo_status_transition_check", "status_step_check")
OBS_CAP = 1500                                 # chars per baseline observation


def ticket_text(inc: dict) -> str:
    return "%s\n%s" % (inc.get("summary") or "", inc.get("desc") or "")


def is_status_ticket(inc: dict, rx: "re.Pattern" = STATUS_RE) -> bool:
    return bool(rx.search(ticket_text(inc)))


# ----------------------------------------------------------------- selection

def select(inc: dict, wo: Optional[dict]) -> List[Tuple[str, Dict[str, Any], str]]:
    """Ordered (query_id, params, why) for the work order `wo` (a preflight 'resolved' item) - may be empty."""
    steps: List[Tuple[str, Dict[str, Any], str]] = []

    def add(qid: str, params: Dict[str, Any], why: str) -> None:
        if all(s[0] != qid for s in steps):
            steps.append((qid, params, why))

    ptype = inc.get("problem_type")
    if wo:
        p = {"TenantId": wo["TenantId"], "WorkOrderId": wo["WorkOrderId"]}
        if is_status_ticket(inc):
            add("wo_header", p, "baseline: current state of the work order")
            add("wo_status_timeline", p, "baseline: status history written by the application")
            add("wo_field_changes", p, "baseline: field-level change log (who/when changed what, incl. direct DB updates)")
            add("wo_status_transition_check", p, "baseline: workflow steps allowed from the current status")
        if ptype == "integration_failure":
            add("wo_header", p, "baseline: current state of the work order")
            add("wo_erp_sap", p, "baseline: ERP / SAP documents and errors")
            add("sap_rfc_rest_calls", {"TenantId": wo["TenantId"], "IdValue": wo["WorkOrderId"]},
                "baseline: SAP RFC / REST calls for the work order")
        if ptype == "notification_failure":
            add("wo_header", p, "baseline: current state of the work order")
            add("notifications_for_wo", p, "baseline: SMS / e-mail queue rows of the work order")
    return steps


# -------------------------------------------------------------- formatting

def _cell(v: Any, n: int = 60) -> str:
    s = str(v).replace("\n", " ").replace("|", "/")
    return s if len(s) <= n else s[:n - 1] + "…"


def _rows(rec: dict) -> List[dict]:
    cols = rec.get("columns") or []
    return [dict(zip(cols, r)) for r in rec.get("rows") or []]


def _kv(row: dict, cols: List[str], n: int = 60) -> str:
    return " ".join("%s=%s" % (c, _cell(row[c], n)) for c in cols if row.get(c) not in (None, ""))


def _named(row: dict, idc: str, namec: str) -> str:
    return "%s(%s)" % (row.get(idc), _cell(row.get(namec), 30)) if row.get(namec) else str(row.get(idc))


def _tail(rows: List[dict], cap: int, fn: Callable[[dict], str]) -> List[str]:
    out = ["  %s" % fn(r) for r in rows[-cap:]]
    if len(rows) > cap:
        out.insert(0, "  (... %d older rows omitted; showing the newest %d)" % (len(rows) - cap, cap))
    return out


def format_observation(qid: str, rec: dict) -> str:
    head = "[%s] %s" % (qid, json.dumps(rec.get("params") or {}, ensure_ascii=False, default=str))
    if not rec.get("executed"):
        return "%s NOT EXECUTED: %s" % (head, _cell(rec.get("error") or "no result", 160))
    rows = _rows(rec)
    L = ["%s -> %d rows, %s ms" % (head, rec.get("row_count") or 0, rec.get("elapsed_ms"))]
    if not rows:
        L.append("  (no rows)")
    elif qid == "wo_header":
        L.append("  " + _kv(rows[0], ["StatusId", "StatusName", "OpenDate", "OperationDate", "VerificationDate", "ServiceShopName",
                                      "TechnicianCode", "WarrantyTypeName", "RejectReasonName", "ErpDocumentId", "SKU", "CaseStatusName",
                                      "StatusNote"], 50))
    elif qid == "wo_status_timeline":
        L.append("  CurrentStatusId=%s (Operation.WorkOrder now); last history status=%s" % (
            rows[0].get("CurrentStatusId"), rows[-1].get("StatusId")))
        L += _tail(rows, 8, lambda r: "#%s %s sys=%s op=%s emp=%s" % (
            r.get("HistoryId"), _named(r, "StatusId", "StatusName"), r.get("SysDate"), r.get("OperationDate"), r.get("TechnicianCode")))
    elif qid == "wo_field_changes":
        L.append("  (LogDate is UTC; oldest->newest, newest 10 shown)")
        L += _tail(rows, 10, lambda r: "LogId=%s %s fields=[%s] %s UpdateUserId=%s" % (
            r.get("LogId"), r.get("LogDate"), _cell(r.get("ChangedFields"), 70).rstrip(", "),
            ("status %s -> %s" % (_named(r, "OldStatusId", "OldStatusName"), _named(r, "NewStatusId", "NewStatusName")))
            if r.get("OldStatusId") != r.get("NewStatusId") else "", r.get("UpdateUserId")))
    elif qid == "wo_status_transition_check":
        r0 = rows[0]
        nxt = [_named(r, "NextStatusId", "NextStatusName") for r in rows if r.get("NextStatusId") is not None]
        L.append("  current status %s; latest history status=%s; StatusTenantId=%s" % (
            _named(r0, "CurrentStatusId", "CurrentStatusName"), r0.get("LatestHistoryStatusId"), r0.get("StatusTenantId")))
        L.append("  allowed next statuses from the CURRENT status: %s" % (", ".join(nxt[:12]) or "NONE (dead end)"))
    elif qid == "status_step_check":
        r0 = rows[0]
        nxt = [_named(r, "NextStatusId", "NextStatusName") for r in rows if r.get("NextStatusId") is not None]
        hit = any(r.get("IsRequestedTarget") == 1 for r in rows)
        L.append("  from %s allowed next: %s" % (_named(r0, "FromStatusId", "FromStatusName"), ", ".join(nxt[:12]) or "NONE"))
        L.append("  requested target allowed: %s; StepsIntoTarget (any status -> target, this tenant)=%s" % (
            "YES" if hit else "NO (transition NOT configured in Catalog.StatusStep)", r0.get("StepsIntoTarget")))
    elif qid == "wo_erp_sap":
        L += _tail(rows, 6, lambda r: _kv(r, ["Source", "CreateDate", "DocType", "StatusName", "ErrorDescription", "ErpDocumentStatusId",
                                              "SendDate", "ErpDocumentError"], 70))
    elif qid == "sap_rfc_rest_calls":
        L += _tail(rows, 6, lambda r: _kv(r, ["Source", "CallTime", "Endpoint", "Success", "HttpStatus", "TryCount", "ResponseBody"], 90))
    elif qid == "notifications_for_wo":
        L += _tail(rows, 6, lambda r: _kv(r, ["Source", "QDate", "SentDate", "SendType", "TemplateName", "SendingStatus", "StatusDescription"], 70))
    elif qid == "tenant_lookup":
        L += ["  " + _kv(r, ["TenantId", "TenantName", "CountryCode", "Active", "LanguageCode"], 40) for r in rows[:5]]
    elif qid == "product_sku_lookup":
        L += ["  " + _kv(r, ["SKU", "ProductDescription", "BrandName", "TenantId", "TenantName", "AssignedToTenant", "BomLines",
                            "PendingChangeNotifications"], 50) for r in rows[:4]]
    else:
        cols = (rec.get("columns") or [])[:8]
        L += ["  " + _kv(r, cols, 40) for r in rows[:4]]
    text = "\n".join(L)
    return text if len(text) <= OBS_CAP else text[:OBS_CAP] + "...[truncated]"


# ------------------------------------------------------------------- driver

def _run(ex: Executor, qid: str, params: Dict[str, Any], why: str, region: str, ran: List[str], texts: List[str]) -> Optional[dict]:
    if ex.budget.max_queries - ex.query_count <= RESERVE:
        texts.append("[%s] skipped: query budget kept for the investigation" % qid)
        return None
    obs = ex._run_query({"query_id": qid, "params": params, "region": region, "why": why})
    rec = obs.get("record")
    if rec is None:
        texts.append("[%s] REJECTED: %s" % (qid, _cell(obs.get("text"), 200)))
        return None
    ran.append(qid)
    texts.append(format_observation(qid, rec))
    return rec


def run(inc: dict, ex: Executor, pf: Dict[str, Any]) -> Dict[str, Any]:
    """Run the playbook; returns {"ran", "selected", "text", "ticket_window"}.  Never raises."""
    res: Dict[str, Any] = {"ran": [], "selected": [], "text": "", "ticket_window": None}
    if ex.mode != LIVE:
        return res
    ran: List[str] = res["ran"]
    texts: List[str] = []
    try:
        resolved = pf.get("resolved") or []
        wo = resolved[0] if resolved else None
        steps = select(inc, wo)
        res["selected"] = [s[0] for s in steps]
        region = (wo or {}).get("Region") or ex.region
        for qid, params, why in steps:
            _run(ex, qid, params, why, region, ran, texts)
        if wo and "wo_field_changes" in ran:
            tw = ticket_window.compute(inc, ex.queries)
            res["ticket_window"] = tw
            if tw:
                texts.append(tw["text"])
            sc = tw["status_changes"] if tw else []
            last = sc[-1] if sc else None
            if last and str(last["old_status_id"]).isdigit() and str(last["new_status_id"]).isdigit():
                _run(ex, "status_step_check", {"TenantId": wo["TenantId"], "FromStatusId": int(last["old_status_id"]),
                                               "ToStatusId": int(last["new_status_id"])},
                     "baseline: is the status change %s -> %s found in the ticket window a configured StatusStep?"
                     % (last["old_status_id"], last["new_status_id"]), region, ran, texts)
        if not wo and inc.get("problem_type") == "master_data_request" and (inc.get("entities") or {}).get("sku_codes"):
            _master_data(inc, ex, region, ran, texts)
    except Exception as e:                                            # noqa: BLE001 - a baseline bug must not kill the run
        texts.append("(baseline aborted: %s: %s)" % (e.__class__.__name__, str(e)[:120]))
    if texts:
        res["text"] = ("## BASELINE EVIDENCE (deterministic playbook, already executed and counted in the query budget; do NOT repeat these queries)\n"
                       + "\n".join(texts))
    return res


def _master_data(inc: dict, ex: Executor, region: str, ran: List[str], texts: List[str]) -> None:
    sku = str(inc["entities"]["sku_codes"][0])
    country = str(inc.get("country") or "").upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        texts.append("[product_sku_lookup] skipped: no tenant known (no work order, no ticket country)")
        return
    rec = _run(ex, "tenant_lookup", {"CountryCode": country}, "baseline: tenant of the ticket country (reporter's country!)", region, ran, texts)
    rows = _rows(rec) if rec and rec.get("executed") else []
    if len(rows) == 1:
        _run(ex, "product_sku_lookup", {"TenantId": rows[0]["TenantId"], "Sku": sku}, "baseline: does the SKU exist / is it assigned to the tenant?",
             region, ran, texts)
    else:
        texts.append("[product_sku_lookup] skipped: country %s maps to %d tenants, tenant unknown (ask for it or use tenant_lookup)" % (country, len(rows)))
