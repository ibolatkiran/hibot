"""Deterministic confidence calibration (round 1).

The LLM's self-reported confidence was badly calibrated (baseline: bucket 0.7-1.0 only 50% accurate).  This module post-processes
it with caps that depend on what was really EXECUTED; the raw value is kept in `confidence_raw`, every binding cap in
`confidence_caps_applied` ([{cap, reason, from, to}]).

Caps (applied in this order, the lowest wins):
  NO_QUERY   0.50  no query was executed at all
  UNSUPPORTED 0.60  no SUPPORT: no evidence item that points to an executed query with a non-empty relevant result,
                    no supported known-issue card, no ticket-window manual-fix finding
  WEAK       0.80  confidence above 0.8 needs a supported CONFIRMED card, or >= 2 INDEPENDENT supporting evidences
                    (distinct query ids; the ticket-window finding counts as `wo_field_changes`, a supported card as one more)

"Supporting" evidence = evidence item of type query, status `executed`, whose query returned rows (row_count > 0), except
context-only templates (wo_locate, tenant_lookup, wo_header: they identify the order, they do not show a cause).  For templates
where an EMPTY result IS the finding (user_access_lookup: user code unknown; product_sku_lookup: SKU missing) 0 rows count too.
The deterministic ticket-window evidence counts only when a manual fix is suspected.  A card counts when its verification
verdict is `supports`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

NO_QUERY_CAP = 0.5
UNSUPPORTED_CAP = 0.6
WEAK_CAP = 0.8
CONTEXT_ONLY = frozenset({"wo_locate", "tenant_lookup", "wo_header"})
EMPTY_IS_FINDING = frozenset({"user_access_lookup", "product_sku_lookup"})


def supporting_evidence(rep: dict, queries: List[dict]) -> Dict[str, Any]:
    """{"ids": [independent support ids], "card": bool, "confirmed_card": bool}."""
    by_no = {q.get("n"): q for q in queries}
    ids: List[str] = []
    tw = rep.get("ticket_window_changes") or {}
    for ev in rep.get("evidence") or []:
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
        if qid not in ids:
            ids.append(qid)
    mc = rep.get("matched_card") or {}
    card = mc.get("verdict") == "supports"
    return {"ids": ids, "card": card, "confirmed_card": card and mc.get("card_status") == "confirmed"}


def apply(rep: dict, queries: List[dict]) -> dict:
    """Cap rep["confidence"] in place.  Idempotent: confidence_raw keeps the first (LLM) value, caps are recomputed."""
    try:
        conf = float(rep.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    rep.setdefault("confidence_raw", conf)
    executed = sum(1 for q in queries if q.get("executed"))
    sup = supporting_evidence(rep, queries)
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
    if caps:
        rep.setdefault("confidence_original", rep["confidence_raw"])
        rep["confidence_note"] = "Güven %.2f -> %.2f olarak sınırlandı (%s)." % (
            rep["confidence_raw"], conf, ", ".join(c["reason"] for c in caps))
    else:
        rep.pop("confidence_note", None) if rep.get("confidence_note", "").startswith("Güven ") else None
    rep["confidence"] = round(conf, 2)
    rep["confidence_caps_applied"] = caps
    rep["supporting_evidence"] = {"query_ids": sup["ids"], "card_supported": sup["card"]}
    return rep
