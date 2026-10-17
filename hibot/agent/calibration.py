"""Deterministic confidence calibration (round 1, tightened in round 3).

The LLM's self-reported confidence was badly calibrated (baseline: bucket 0.7-1.0 only 50% accurate).  This module post-processes
it with caps that depend on what was really EXECUTED; the raw value is kept in `confidence_raw`, every binding cap in
`confidence_caps_applied` ([{cap, reason, from, to}]).  Shared by the v1 and the v2 loop (Investigation._ground) and re-run by the
pipeline after a known-issue card was applied.

Caps (applied in this order, the lowest wins):
  NO_QUERY   0.50  no query was executed at all
  UNSUPPORTED 0.60  no SUPPORT: no evidence item that points to an executed query with a non-empty relevant result,
                    no supported known-issue card, no ticket-window manual-fix finding
  WEAK       0.69  (round 3; was 0.80) confidence above 0.7 needs a supported CONFIRMED card, or >= 2 INDEPENDENT supporting evidences
                    (distinct template ids, distinct ad-hoc queries, distinct source files that were really READ in this run and that the
                    report's evidence list cites; the ticket-window finding counts as `wo_field_changes`, a supported card as one more).
                    0.69, not 0.70: the scorer treats >= 0.7 as "confident" and a single-evidence answer is not.
  STRONG     0.85  (round 3) confidence above 0.85 needs a supported CONFIRMED card or >= 3 independent supporting evidences
  NO_CHANGE  0.55  (round 3) status / data-correction ticket whose ticket window shows NO status change on the resolved work order: a
                    `user_error` or `config` verdict is capped unless an executed query directly supports it (status_step_check whose rows show
                    the transition is NOT configured) or a supported known-issue card does.  "No change in the window" is not evidence.

  CHECKLIST  0.60  (round 3, v2) required evidence of the selected skill is still missing (and was not waived with a reason)

"Supporting" evidence = evidence item of type query, status `executed`, whose query returned rows (row_count > 0), except
context-only templates (wo_locate, tenant_lookup, wo_header: they identify the order, they do not show a cause).  For templates
where an EMPTY result IS the finding (user_access_lookup: user code unknown; product_sku_lookup / product_labour_setup: SKU missing) 0 rows count too.
The deterministic ticket-window evidence counts only when a manual fix is suspected.  A card counts when its verification
verdict is `supports`.  Evidence of type `code` counts when the cited file was read with read_source / read_file in this run.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

NO_QUERY_CAP = 0.5
UNSUPPORTED_CAP = 0.6
WEAK_CAP = 0.69
STRONG_CAP = 0.85
NO_CHANGE_CAP = 0.55
NO_CHANGE_TYPES = frozenset({"user_error", "config"})
STATUS_TICKET_RE = re.compile(r"cancel|iptal|reject|reddet|status|stat[uü]|durum|reopen|re-open|financ|approved|transfer", re.I)
CONTEXT_ONLY = frozenset({"wo_locate", "tenant_lookup", "wo_header"})
EMPTY_IS_FINDING = frozenset({"user_access_lookup", "product_sku_lookup", "product_labour_setup"})


def _norm_path(p: Any) -> str:
    return str(p or "").replace("\\", "/").strip().lower()


def _read_basenames(code_reads: Optional[List[str]]) -> set:
    return {_norm_path(p).rsplit("/", 1)[-1] for p in code_reads or [] if p}


def supporting_evidence(rep: dict, queries: List[dict], code_reads: Optional[List[str]] = None) -> Dict[str, Any]:
    """{"ids": [independent support ids], "card": bool, "confirmed_card": bool}."""
    by_no = {q.get("n"): q for q in queries}
    ids: List[str] = []
    tw = rep.get("ticket_window_changes") or {}
    reads = _read_basenames(code_reads)
    for ev in rep.get("evidence") or []:
        if ev.get("type") == "code":
            ref = _norm_path(str(ev.get("ref") or "").split(":")[0].split("#")[0])
            base = ref.rsplit("/", 1)[-1]
            if base and base in reads and str(ev.get("finding") or "").strip() and ("code:" + base) not in ids:
                ids.append("code:" + base)
            continue
        if ev.get("type") != "query" or ev.get("status") != "executed":
            continue
        ref = str(ev.get("ref") or "").split()[0] if ev.get("ref") else ""
        qid = ref
        q = by_no.get(ev.get("query_no")) or {}
        qid = q.get("query_id") or qid
        if qid in CONTEXT_ONLY or not qid:
            continue
        if ev.get("deterministic"):
            if not (tw.get("manual_fix") or {}).get("suspected"):
                continue
        else:
            rows = q.get("row_count")
            if not ((isinstance(rows, int) and rows > 0) or (qid in EMPTY_IS_FINDING and rows == 0)):
                continue
        if qid == "adhoc" and q.get("n"):
            qid = "adhoc#%s" % q["n"]                                   # distinct ad-hoc queries are distinct evidence
        if qid not in ids:
            ids.append(qid)
    mc = rep.get("matched_card") or {}
    card = mc.get("verdict") == "supports"
    return {"ids": ids, "card": card, "confirmed_card": card and mc.get("card_status") == "confirmed"}


def _no_status_change(rep: dict) -> bool:
    """The ticket window was computed for the resolved work order and holds NO status change."""
    tw = rep.get("ticket_window_changes")
    return isinstance(tw, dict) and tw.get("status_changes") == [] and bool(tw.get("window"))


def _status_like_ticket(inc: Optional[dict]) -> bool:
    if not inc:
        return False
    if inc.get("problem_type") == "data_correction":
        return True
    return bool(STATUS_TICKET_RE.search("%s\n%s" % (inc.get("summary") or "", inc.get("desc") or "")))


def _transition_missing_proven(queries: List[dict]) -> bool:
    """An executed status_step_check whose rows show that NO configured step reaches the requested target (no row with IsRequestedTarget = 1)."""
    for q in queries:
        if q.get("query_id") != "status_step_check" or not q.get("executed") or q.get("ok") is False:
            continue
        cols, rows = q.get("columns") or [], q.get("rows") or []
        if not rows or "IsRequestedTarget" not in cols:
            continue
        i = cols.index("IsRequestedTarget")
        if not any(len(r) > i and r[i] in (1, True, "1") for r in rows):
            return True
    return False


def apply(rep: dict, queries: List[dict], inc: Optional[dict] = None, code_reads: Optional[List[str]] = None) -> dict:
    """Cap rep["confidence"] in place.  Idempotent: confidence_raw keeps the first (LLM) value, caps are recomputed.
    `inc` (the incident) enables the NO_CHANGE rule; `code_reads` (paths read in this run) lets cited code evidence count."""
    try:
        conf = float(rep.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    rep.setdefault("confidence_raw", conf)
    executed = sum(1 for q in queries if q.get("executed"))
    sup = supporting_evidence(rep, queries, code_reads)
    n_support = len(sup["ids"]) + (1 if sup["card"] else 0)
    ticket_window_fix = bool(((rep.get("ticket_window_changes") or {}).get("manual_fix") or {}).get("suspected"))
    caps: List[dict] = []

    def cap(limit: float, reason: str) -> None:
        nonlocal conf
        if conf > limit:
            caps.append({"cap": limit, "reason": reason, "from": round(conf, 3), "to": limit})
            conf = limit

    if executed == 0:
        cap(NO_QUERY_CAP, "no_query_executed")
    if not (sup["ids"] or sup["card"] or ticket_window_fix):
        cap(UNSUPPORTED_CAP, "no_executed_supporting_evidence")
    if not (sup["confirmed_card"] or n_support >= 2):
        cap(WEAK_CAP, "needs_confirmed_card_or_2_independent_evidences")
    if not (sup["confirmed_card"] or n_support >= 3):
        cap(STRONG_CAP, "needs_confirmed_card_or_3_independent_evidences")
    if (rep.get("checklist") or {}).get("missing"):
        cap(UNSUPPORTED_CAP, "required_checklist_evidence_missing")
    rct = rep.get("root_cause_type")
    if rct in NO_CHANGE_TYPES and _no_status_change(rep) and _status_like_ticket(inc) and not (sup["card"] or _transition_missing_proven(queries)):
        cap(NO_CHANGE_CAP, "no_change_in_ticket_window_is_not_evidence_of_%s" % rct)
    if caps:
        rep.setdefault("confidence_original", rep["confidence_raw"])
        rep["confidence_note"] = "Güven %.2f -> %.2f olarak sınırlandı (%s)." % (
            rep["confidence_raw"], conf, ", ".join(c["reason"] for c in caps))
    else:
        rep.pop("confidence_note", None) if rep.get("confidence_note", "").startswith("Güven ") else None
    prev = rep.get("confidence_caps_applied")
    if not caps and prev and round(conf, 2) == rep.get("confidence") and rep.get("confidence", 0) < rep["confidence_raw"]:
        caps = prev                                             # idempotent re-run: keep the record of the caps that already bound
    rep["confidence"] = round(conf, 2)
    rep["confidence_caps_applied"] = caps
    rep["supporting_evidence"] = {"query_ids": sup["ids"], "card_supported": sup["card"]}
    return rep
