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
  access request with user code(s) in the text                user_access_lookup per code (<= 2) (+ tenant_lookup of the ticket
                                                              country when the user exists); WITHOUT a code a hint tells the LLM
                                                              that "access / grant_access" is the answer even without DB evidence
  integration_failure WITHOUT a resolved work order           time window ticket-created -24h/+24h (UTC): job_health,
                                                              outbound_queue_backlog, db_errors_recent

Live mode only: in dry-run nothing could be learned and the budget would be burnt.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import access, ticket_window
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


def _format_user_access(rows: List[dict]) -> List[str]:
    by: Dict[str, List[dict]] = {}
    for r in rows:
        by.setdefault(str(r.get("Kind")), []).append(r)
    su = by.get("SIRIUS_USER") or []
    if not su:
        return ["  SIRIUS_USER: NONE (the user code does not exist as a Sirius login in the checked region)"]
    L = ["  SIRIUS_USER: %d record(s): %s" % (len(su), "; ".join("tenant=%s active=%s tenantCode=%s userType=%s" % (
        r.get("TenantId"), r.get("Active"), r.get("Detail1"), r.get("Detail2")) for r in su[:3]))]
    ut = by.get("USER_TENANT") or []
    L.append("  USER_TENANT (tenant memberships): %s" % (", ".join(str(r.get("TenantId")) for r in ut[:12]) or "NONE"))
    emp = by.get("EMPLOYEE") or []
    L.append("  EMPLOYEE: %s" % ("; ".join("tenant=%s active=%s role=%s %s" % (r.get("TenantId"), r.get("Active"), _cell(r.get("Detail1"), 30), r.get("Detail2"))
                                          for r in emp[:4]) or "NONE (user not provisioned as employee)"))
    ea = by.get("EMPLOYEE_TENANT_AUTH") or []
    L.append("  EMPLOYEE_TENANT_AUTH (extra tenants): %s" % (", ".join(str(r.get("TenantId")) for r in ea[:12]) or "NONE"))
    return L


def _format_job_health(rows: List[dict]) -> List[str]:
    jobs = [r for r in rows if r.get("Source") == "JobRunLog"]
    errs = [r for r in rows if r.get("Source") == "SiriusIntLog"]
    bad = [r for r in jobs if (r.get("FailuresInWindow") or 0) > 0 or (r.get("RunsInWindow") or 0) == 0 or r.get("LastEnd") in (None, "")]
    L = ["  %d jobs seen, %d with failures / no run in the window / unfinished last run" % (len(jobs), len(bad))]
    L += ["  " + _kv(r, ["Name", "LastAt", "LastEnd", "LastStatus", "RunsInWindow", "FailuresInWindow", "SampleText"], 60) for r in bad[:6]]
    L += ["  SiriusIntLog " + _kv(r, ["Name", "LastAt", "RunsInWindow", "SampleText"], 70).replace("RunsInWindow", "Cnt") for r in errs[:4]]
    return L


def format_observation(qid: str, rec: dict) -> str:
    head = "[%s] %s" % (qid, json.dumps(rec.get("params") or {}, ensure_ascii=False, default=str))
    if not rec.get("executed"):
        return "%s NOT EXECUTED: %s" % (head, _cell(rec.get("error") or "no result", 160))
    rows = _rows(rec)
    L = ["%s -> %d rows, %s ms" % (head, rec.get("row_count") or 0, rec.get("elapsed_ms"))]
    if not rows:
        L += _format_user_access([]) if qid == "user_access_lookup" else ["  (no rows)"]
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
    elif qid == "user_access_lookup":
        L += _format_user_access(rows)
    elif qid == "job_health":
        L += _format_job_health(rows)
    elif qid == "outbound_queue_backlog":
        L += _tail(rows, 8, lambda r: _kv(r, ["Source", "Name", "Cnt", "MaxRetry", "FirstAt", "LastAt", "SampleText"], 70))
    elif qid == "db_errors_recent":
        L += ["  " + _kv(r, ["ErrorProcedure", "ErrorNumber", "Occurrences", "FirstSeen", "LastSeen", "SampleMessage"], 70) for r in rows[:6]]
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
        if access.is_access_incident(inc) and not access.incident_user_codes(inc):       # no DB needed for this hint
            t: List[str] = []
            _access(inc, ex, ex.region, [], t, res)
            if t:
                res["text"] = "## BASELINE EVIDENCE (deterministic playbook)\n" + "\n".join(t)
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
        if not wo and inc.get("problem_type") == "integration_failure":
            _integration_window(inc, ex, region, ran, texts, res)
        if access.is_access_incident(inc):
            _access(inc, ex, region, ran, texts, res)
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


# ----------------------------------------------------------- integration without a work order

WINDOW_HOURS = 24


def ticket_time_window(inc: dict, hours: int = WINDOW_HOURS) -> Optional[Tuple[str, str]]:
    """(FromDate, ToDate) as naive-UTC ISO strings, ticket `created` -/+ hours; None when `created` is unknown."""
    created = ticket_window.parse_created(inc.get("created"))
    if created is None:
        return None
    import datetime as _dt
    f = "%Y-%m-%dT%H:%M:%S"
    return ((created - _dt.timedelta(hours=hours)).strftime(f), (created + _dt.timedelta(hours=hours)).strftime(f))


def _integration_window(inc: dict, ex: Executor, region: str, ran: List[str], texts: List[str], res: dict) -> None:
    w = ticket_time_window(inc)
    if w is None:
        texts.append("[integration window] skipped: ticket creation time unknown")
        return
    params = {"FromDate": w[0], "ToDate": w[1]}
    res["window"] = {"from_utc": w[0], "to_utc": w[1]}
    texts.append("(No work order is known: time-window evidence around the ticket creation, %s .. %s UTC. These logs are platform-wide, "
                 "not tenant scoped; a problem visible here only SUGGESTS the cause - say so.)" % w)
    for qid, why in (("job_health", "baseline: did the background jobs run / fail around the ticket time?"),
                     ("outbound_queue_backlog", "baseline: outbound queue / post errors around the ticket time"),
                     ("db_errors_recent", "baseline: database errors around the ticket time")):
        _run(ex, qid, dict(params), why, region, ran, texts)


# ----------------------------------------------------------------------- access requests

def _access(inc: dict, ex: Executor, region: str, ran: List[str], texts: List[str], res: dict) -> None:
    codes = access.incident_user_codes(inc)
    res["access"] = {"user_codes_found": len(codes)}
    if not codes:
        if inc.get("problem_type") == "access_request":
            texts.append("[access request] No user code / login in the ticket text (\"kullanıcı kodu ticket'ta yok\"). This is still recognisable as an "
                         "ACCESS request from its text: classify root_cause_type=access, resolution_action=grant_access with a MODEST confidence "
                         "(<= 0.6), name the missing user code as the thing to ask for; do NOT answer unknown.")
        return
    if ex.mode != LIVE:
        return
    found_any = False
    for code in codes[:2]:
        rec = _run(ex, "user_access_lookup", {"UserCode": code}, "baseline: access request - does the user exist, which tenants / role?", region, ran, texts)
        if rec and rec.get("executed") and (rec.get("row_count") or 0) > 0:
            found_any = True
    country = str(inc.get("country") or "").upper()
    if found_any and re.fullmatch(r"[A-Z]{2}", country):
        _run(ex, "tenant_lookup", {"CountryCode": country}, "baseline: tenants of the ticket country (to compare with the user's memberships)",
             region, ran, texts)
