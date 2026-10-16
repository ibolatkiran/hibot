"""Ticket-window footprint (DECISIONS D13): what changed on the resolved work order between ticket creation and resolution.

Developers often fix a ticket by hand in the database.  That leaves a footprint in two places:
  * Log.WorkOrderJsonLog (trigger, template `wo_field_changes`): a field change (e.g. StatusId 329 -> 314) with a LogDate,
  * Operation.WorkOrderGpsInfo (status history written by the application, template `wo_status_timeline`).
A status change that has NO matching history row, and/or whose UpdateUserId did not change, did not go through the
application flow: suspected MANUAL DB FIX.  The ticket's "resolution" is then a data correction, and the interesting question
is WHY the application did not allow it (e.g. a missing Catalog.StatusStep row -> template `status_step_check`).

Time zones (verified against the raw Jira export and a known case, 2026-10-16):
  * ticket `created` (tickets_enriched.json, "2026-09-17 08:58") is Jira server local time = UTC+03:00,
  * `resolved` carries its own offset ("Thu, 17 Sep 2026 14:42:00 +0300"),
  * DB datetimes (LogDate, SysDate) are UTC (LogDate 11:41:11 vs resolved 14:42 +03:00).
Pure functions, no I/O: everything is computed from the executor's query records.
"""
from __future__ import annotations

import datetime as _dt
import email.utils
import re
from typing import Any, Dict, List, Optional

UTC = _dt.timezone.utc
JIRA_LOCAL = _dt.timezone(_dt.timedelta(hours=3))
START_MARGIN_MIN = 5
END_MARGIN_MIN = 15
HISTORY_MATCH_MIN = 5
MAX_TEXT_CHANGES = 12


# ------------------------------------------------------------------ parsing

def parse_created(s: Optional[str]) -> Optional[_dt.datetime]:
    """Jira `created` as stored in tickets_enriched.json (local UTC+03:00, no offset) -> aware UTC."""
    if not s:
        return None
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", str(s))
    if not m:
        return None
    y, mo, d, h, mi, sec = [int(x) if x else 0 for x in m.groups()]
    return _dt.datetime(y, mo, d, h, mi, sec, tzinfo=JIRA_LOCAL).astimezone(UTC)


def parse_resolved(s: Optional[str]) -> Optional[_dt.datetime]:
    """RFC 2822 with offset ("Thu, 17 Sep 2026 14:42:00 +0300") or ISO -> aware UTC."""
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(str(s))
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        d = parse_db_time(s)
        if d is not None:
            return d
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=JIRA_LOCAL)
    return d.astimezone(UTC)


def parse_db_time(v: Any) -> Optional[_dt.datetime]:
    """DB datetime (ISO string or datetime) -> aware UTC (DB values are UTC; an explicit offset is honoured)."""
    if v is None or v == "":
        return None
    if isinstance(v, _dt.datetime):
        d = v
    else:
        s = str(v).strip().replace(" ", "T")
        s = re.sub(r"(\.\d{6})\d+", r"\1", s)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            d = _dt.datetime.fromisoformat(s)
        except ValueError:
            return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _fmt(d: Optional[_dt.datetime]) -> Optional[str]:
    return d.strftime("%Y-%m-%d %H:%M:%S") if d else None


def _rows(rec: dict) -> List[dict]:
    cols = rec.get("columns") or []
    return [dict(zip(cols, r)) for r in rec.get("rows") or []]


def _s(v: Any) -> Optional[str]:
    return None if v is None or v == "" else str(v)


def _latest(queries: List[dict], qid: str) -> Optional[dict]:
    for q in reversed(queries or []):
        if q.get("query_id") == qid and q.get("executed") and q.get("ok") is not False:
            return q
    return None


# --------------------------------------------------------------- computation

def compute(inc: dict, queries: List[dict], *, now: Optional[_dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """Return the ticket-window block or None when it cannot be computed (no created time, no wo_field_changes result)."""
    created = parse_created(inc.get("created"))
    fc = _latest(queries, "wo_field_changes")
    if created is None or fc is None:
        return None
    resolved = parse_resolved(inc.get("resolved"))
    start = created - _dt.timedelta(minutes=START_MARGIN_MIN)
    end = (resolved + _dt.timedelta(minutes=END_MARGIN_MIN)) if resolved else None
    tl = _latest(queries, "wo_status_timeline")
    tl_rows = _rows(tl) if tl else []

    def in_window(d: Optional[_dt.datetime]) -> bool:
        return d is not None and d >= start and (end is None or d <= end)

    changes: List[Dict[str, Any]] = []
    for r in _rows(fc):
        at = parse_db_time(r.get("LogDate"))
        if not in_window(at):
            continue
        old, new = _s(r.get("OldStatusId")), _s(r.get("NewStatusId"))
        fields = [f.strip() for f in str(r.get("ChangedFields") or "").split(",") if f.strip()]
        status_change = old != new
        ch: Dict[str, Any] = {
            "log_id": r.get("LogId"), "at_utc": _fmt(at), "status_change": status_change,
            "old_status_id": old, "old_status_name": r.get("OldStatusName"),
            "new_status_id": new, "new_status_name": r.get("NewStatusName"),
            "changed_fields": fields, "update_user_id": _s(r.get("UpdateUserId")),
            "update_user_unchanged": ("UpdateUserId" not in fields) if status_change else None,
            "history_row_found": None,
        }
        if status_change and tl is not None:
            ch["history_row_found"] = any(
                _s(h.get("StatusId")) == new and any(
                    (t is not None and abs((t - at).total_seconds()) <= HISTORY_MATCH_MIN * 60)
                    for t in (parse_db_time(h.get("SysDate")), parse_db_time(h.get("OperationDate"))))
                for h in tl_rows)
        changes.append(ch)

    history = [{"history_id": h.get("HistoryId"), "status_id": _s(h.get("StatusId")), "status_name": h.get("StatusName"),
                "sys_date_utc": _fmt(parse_db_time(h.get("SysDate"))), "operation_date_utc": _fmt(parse_db_time(h.get("OperationDate")))}
               for h in tl_rows if in_window(parse_db_time(h.get("SysDate"))) or in_window(parse_db_time(h.get("OperationDate")))]

    manual = _assess(changes, tl_rows)
    out: Dict[str, Any] = {
        "window": {"created_utc": _fmt(created), "start_utc": _fmt(start), "end_utc": _fmt(end), "resolved_utc": _fmt(resolved),
                   "resolved_known": resolved is not None,
                   "assumptions": "ticket created = UTC+03:00 (Jira local), DB datetimes = UTC, margin -%d/+%d min" % (START_MARGIN_MIN, END_MARGIN_MIN)},
        "work_order_id": next((r.get("WorkOrderId") for r in tl_rows if r.get("WorkOrderId")), None),
        "changes": changes, "history_rows_in_window": history, "history_checked": tl is not None,
        "manual_fix": manual,
        "status_changes": [c for c in changes if c["status_change"]],
    }
    out["text"] = render_text(out)
    out["evidence_text"] = evidence_text(out)
    return out


def _assess(changes: List[dict], tl_rows: List[dict]) -> Dict[str, Any]:
    reasons: List[str] = []
    suspects: List[dict] = []
    for c in changes:
        if not c["status_change"]:
            continue
        flags = []
        if c["history_row_found"] is False:
            flags.append("no_history_row")
        if c["update_user_unchanged"]:
            flags.append("update_user_unchanged")
        if "no_history_row" in flags:
            suspects.append({"log_id": c["log_id"], "at_utc": c["at_utc"], "from": c["old_status_id"], "to": c["new_status_id"],
                             "flags": flags})
            reasons.append("%s UTC: status %s -> %s in Log.WorkOrderJsonLog has NO matching Operation.WorkOrderGpsInfo row%s"
                           % (c["at_utc"], c["old_status_id"], c["new_status_id"],
                              " and UpdateUserId did not change" if "update_user_unchanged" in flags else ""))
    # independent of the window: current status without any history row
    if tl_rows:
        cur = _s(tl_rows[0].get("CurrentStatusId"))
        last = max(tl_rows, key=lambda r: (str(r.get("OperationDate")), str(r.get("HistoryId"))))
        if cur and _s(last.get("StatusId")) != cur:
            reasons.append("current status %s differs from the last history row status %s (no history row for the current status)"
                           % (cur, _s(last.get("StatusId"))))
    level = "none"
    if suspects:
        level = "high" if any("update_user_unchanged" in s["flags"] for s in suspects) else "medium"
    elif reasons:
        level = "low"
    return {"suspected": bool(suspects), "level": level, "reasons": reasons, "suspect_changes": suspects}


# ------------------------------------------------------------------- text

def _st(i: Optional[str], name: Any) -> str:
    return "%s (%s)" % (i, name) if name else str(i)


def render_text(tw: Dict[str, Any]) -> str:
    w = tw["window"]
    L = ["## Ticket süresince iş emrinde olan değişiklikler (deterministik; WorkOrder %s)" % (tw.get("work_order_id") or "?"),
         "Pencere (UTC): %s -> %s%s. Varsayım: %s." % (w["start_utc"], w["end_utc"] or "şimdi (ticket çözülmemiş)",
                                                    "" if w["resolved_known"] else " [resolved tarihi yok]", w["assumptions"])]
    ch = tw["changes"]
    if not ch:
        L.append("- Pencerede Log.WorkOrderJsonLog kaydı YOK (iş emrinde ticket süresince tracked alan değişmedi).")
    for c in ch[-MAX_TEXT_CHANGES:]:
        if c["status_change"]:
            extra = "; history satırı: %s; UpdateUserId=%s%s" % (
                {True: "VAR", False: "YOK", None: "kontrol edilmedi"}[c["history_row_found"]], c["update_user_id"],
                " (DEĞİŞMEDİ)" if c["update_user_unchanged"] else "")
            L.append("- %s UTC LogId=%s StatusId %s -> %s%s" % (c["at_utc"], c["log_id"], _st(c["old_status_id"], c["old_status_name"]),
                                                              _st(c["new_status_id"], c["new_status_name"]), extra))
        else:
            L.append("- %s UTC LogId=%s alanlar: %s; UpdateUserId=%s" % (c["at_utc"], c["log_id"], ", ".join(c["changed_fields"]) or "(izlenen alan yok)", c["update_user_id"]))
    if len(ch) > MAX_TEXT_CHANGES:
        L.append("- ... %d eski değişiklik gösterilmedi" % (len(ch) - MAX_TEXT_CHANGES))
    if tw["history_checked"]:
        hs = tw["history_rows_in_window"]
        L.append("Pencerede Operation.WorkOrderGpsInfo (durum geçmişi) satırı: %d%s" % (
            len(hs), "" if not hs else " -> " + ", ".join("%s@%s" % (_st(h["status_id"], h["status_name"]), h["sys_date_utc"]) for h in hs[-5:])))
    else:
        L.append("Durum geçmişi (wo_status_timeline) çalıştırılmadı: manuel düzeltme kontrolü yapılamadı.")
    m = tw["manual_fix"]
    if m["suspected"]:
        L.append("SONUÇ: MANUEL DB DÜZELTMESİ ŞÜPHESİ (%s). %s" % (m["level"], " ".join(m["reasons"])))
        L.append("Anlamı: ticket'ı çözen developer büyük olasılıkla doğrudan DB'de güncelleme yaptı (veri düzeltme). Asıl soru: uygulama "
                 "bu değişikliğe NEDEN izin vermedi (örn. Catalog.StatusStep'te geçiş yok -> status_step_check).")
    elif m["reasons"]:
        L.append("Not: " + " ".join(m["reasons"]))
    elif not tw["history_checked"] and tw["status_changes"]:
        L.append("SONUÇ: Durum geçmişi sorgulanmadığı için manuel düzeltme DOĞRULANAMADI (wo_status_timeline çalıştırın).")
    else:
        L.append("SONUÇ: Pencerede manuel DB düzeltmesi belirtisi yok.")
    return "\n".join(L)


def evidence_text(tw: Dict[str, Any]) -> str:
    m = tw["manual_fix"]
    sc = tw["status_changes"]
    if sc:
        c = sc[-1]
        s = "Ticket penceresinde (%s - %s UTC) %d değişiklik; son durum değişikliği %s UTC: %s -> %s." % (
            tw["window"]["start_utc"], tw["window"]["end_utc"] or "şimdi", len(tw["changes"]), c["at_utc"],
            _st(c["old_status_id"], c["old_status_name"]), _st(c["new_status_id"], c["new_status_name"]))
    else:
        s = "Ticket penceresinde (%s - %s UTC) %d değişiklik, durum değişikliği yok." % (
            tw["window"]["start_utc"], tw["window"]["end_utc"] or "şimdi", len(tw["changes"]))
    if m["suspected"]:
        s += " Operation.WorkOrderGpsInfo'da karşılık gelen geçmiş satırı yok" + (
            ", UpdateUserId değişmemiş" if m["level"] == "high" else "") + " -> manuel DB düzeltmesi şüphesi (%s)." % m["level"]
    return s
