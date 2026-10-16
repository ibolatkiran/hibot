"""Card-driven analysis ("known issue shortcut").

After preflight resolved the work order (tenant, current status) and the baseline playbook ran, status-transition cards that carry a
`state_signature` {tenant_id, from_status_id, to_status_id, target_words} are matched on STATE, not on keywords:

    0.40 tenant of the resolved work order == card tenant                      (mandatory)
    0.35 status of the work order AS OF TICKET CREATION == card from_status    (reconstructed from wo_field_changes; live
                                                                               new ticket: created ~ now = current status)
    0.25 ticket text mentions the target action (card target_words)
    +0.05 confirmed card;   at least one of the two last signals is required;   threshold 0.65
Confirmed cards rank above drafts, rejected cards never match.

The best card's verification_queries are then run deterministically (budget counted, deduplicated against the queries that
already ran), evaluated where a pass/fail is possible (status_step_check vs the card's claim `transition_configured`), and
injected into the case file as "BILINEN SORUN KARTI".  `apply_to_report` makes the final report reuse a CONFIRMED card
(classification, fix steps verbatim) and cite the card as evidence (so `hibot feedback` can add the ticket to its evidence).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import axes
from . import cards as cardmod
from . import playbooks, ticket_window
from .executor import LIVE, Executor

THRESHOLD = 0.65
VERDICT_TR = {"supports": "destekliyor", "contradicts": "çelişiyor", "inconclusive": "doğrulanamadı"}
VERDICT_UPPER = {"supports": "DESTEKLİYOR", "contradicts": "ÇELİŞİYOR", "inconclusive": "DOĞRULANAMADI"}
LLM_ADD = "[LLM ek önerisi] "


# ------------------------------------------------------------------ state reconstruction

def _rows(rec: dict) -> List[dict]:
    cols = rec.get("columns") or []
    return [dict(zip(cols, r)) for r in rec.get("rows") or []]


def status_as_of_created(inc: dict, current_status: Any, queries: List[dict]) -> Tuple[Optional[str], str]:
    """(status id as text, basis).  Walks the status changes of Log.WorkOrderJsonLog newer than the ticket creation backwards."""
    cur = None if current_status in (None, "") else str(current_status)
    created = ticket_window.parse_created(inc.get("created"))
    fc = ticket_window._latest(queries, "wo_field_changes")
    if created is None:
        return cur, "current (ticket creation time unknown)"
    if fc is None:
        return cur, "current (wo_field_changes not available, not reconstructed)"
    changes = []
    for r in _rows(fc):
        at = ticket_window.parse_db_time(r.get("LogDate"))
        old, new = ticket_window._s(r.get("OldStatusId")), ticket_window._s(r.get("NewStatusId"))
        if at is not None and old is not None and new is not None and old != new:
            changes.append((at, old, new))
    changes.sort(key=lambda c: c[0], reverse=True)
    status, undone = cur, 0
    for at, old, new in changes:
        if at > created:
            status, undone = old, undone + 1
        else:
            break
    return status, "reconstructed at ticket creation (%d later status change(s) undone)" % undone if undone else \
        "current = at creation (no status change after the ticket was created)"


def match_state_cards(inc: dict, cards: List[dict], pf: dict, queries: List[dict], min_score: float = THRESHOLD) -> List[dict]:
    """[{card, score, reasons, missing_entities, state:{...}}] best first (confirmed before draft)."""
    resolved = pf.get("resolved") or []
    if not resolved:
        return []
    wo = resolved[0]
    try:
        tenant = int(wo["TenantId"])
    except (TypeError, ValueError, KeyError):
        return []
    status, basis = status_as_of_created(inc, wo.get("StatusId"), queries)
    text = cardmod.incident_text(inc)
    out = []
    for c in cards:
        sig = c.get("state_signature")
        if c.get("status") == "rejected" or not sig or int(sig["tenant_id"]) != tenant:
            continue
        score, reasons = 0.40, ["tenant=%d" % tenant]
        from_hit = status is not None and status == str(sig["from_status_id"])
        hits = [w for w in (cardmod.fold(x) for x in sig.get("target_words") or []) if w and w in text]
        if from_hit:
            score += 0.35
            reasons.append("status at ticket creation=%s (%s)" % (status, basis))
        if hits:
            score += 0.25
            reasons.append("ticket mentions target action: " + ", ".join(hits[:3]))
        if not (from_hit or hits):
            continue
        if c.get("status") == "confirmed":
            score += 0.05
        score = round(min(score, 1.0), 3)
        if score >= min_score:
            out.append({"card": c, "score": score, "reasons": reasons, "missing_entities": [], "kind": "state",
                        "state": {"status_at_created": status, "basis": basis, "from_hit": from_hit, "words_hit": hits}})
    out.sort(key=lambda m: (m["card"].get("status") != "confirmed", -m["score"], m["card"]["id"]))
    return out


# ------------------------------------------------------------------ verification

def _find_done(ex: Executor, qid: str, params: Dict[str, Any]) -> Optional[dict]:
    for q in reversed(ex.queries):
        if q.get("query_id") == qid and q.get("executed") and q.get("ok") is not False and \
                all(str((q.get("params") or {}).get(k)) == str(v) for k, v in params.items()):
            return q
    return None


def run_verification(card: dict, inc: dict, ex: Executor, pf: dict) -> List[dict]:
    """Run (or reuse) the card's verification queries.  Returns [{query_id, params, deduped, executed, rec, note}]."""
    wo = (pf.get("resolved") or [None])[0]
    out: List[dict] = []
    for q in cardmod.resolve_card_queries(card, inc):
        qid, params, unresolved = q["query_id"], dict(q["params"]), list(q["unresolved"])
        if qid == "wo_locate":
            continue                                           # preflight already resolved the work order
        if wo:
            if "WorkOrderId" in params or "WorkOrderId" in unresolved:
                params["WorkOrderId"] = wo["WorkOrderId"]
            if "TenantId" in params or "TenantId" in unresolved:
                params["TenantId"] = wo["TenantId"]
            unresolved = [u for u in unresolved if u not in params]
        item: Dict[str, Any] = {"query_id": qid, "params": params, "deduped": False, "executed": False, "rec": None, "note": ""}
        out.append(item)
        if unresolved:
            item["note"] = "skipped: unresolved parameters " + ", ".join(unresolved)
            continue
        done = _find_done(ex, qid, params)
        if done:
            item.update(deduped=True, executed=True, rec=done, note="already run by the baseline (query #%s)" % done.get("n"))
            continue
        if ex.budget.max_queries - ex.query_count <= playbooks.RESERVE:
            item["note"] = "skipped: query budget kept for the investigation"
            continue
        obs = ex._run_query({"query_id": qid, "params": params, "region": (wo or {}).get("Region") or ex.region,
                             "why": "known-issue card %s: verification" % card["id"]})
        rec = obs.get("record")
        if rec is None:
            item["note"] = "REJECTED: " + str(obs.get("text"))[:120]
            continue
        item.update(executed=bool(rec.get("executed")), rec=rec, note="" if rec.get("executed") else "not executed: %s" % (rec.get("error") or "dry-run"))
    return out


def evaluate(card: dict, results: List[dict], tw: Optional[dict]) -> Dict[str, Any]:
    """verdict supports | contradicts | inconclusive + Turkish detail lines.  Simple pass/fail only (status_step_check)."""
    sig = card.get("state_signature") or {}
    lines: List[str] = []
    verdict = "inconclusive"
    claim = sig.get("transition_configured")
    for r in results:
        if r["query_id"] != "status_step_check" or not r["executed"] or claim is None:
            continue
        p = r["params"]
        if str(p.get("FromStatusId")) != str(sig.get("from_status_id")) or str(p.get("ToStatusId")) != str(sig.get("to_status_id")):
            continue
        rows = _rows(r["rec"])
        configured = any(x.get("IsRequestedTarget") == 1 for x in rows)
        label = "%s -> %s" % (sig.get("from_status_id"), sig.get("to_status_id"))
        lines.append("status_step_check: %s Catalog.StatusStep'te %s (tenant %s)" % (
            label, "TANIMLI" if configured else "TANIMLI DEĞİL", sig.get("tenant_id")))
        verdict = "supports" if configured == bool(claim) else "contradicts"
        lines.append("Kart iddiası: geçiş %s -> sonuç kartla %s." % ("tanımlı" if claim else "tanımlı değil",
                                                                  "UYUMLU" if verdict == "supports" else "ÇELİŞİYOR"))
    forced = None
    if tw:
        for c in tw.get("status_changes") or []:
            if str(c["old_status_id"]) == str(sig.get("from_status_id")) and str(c["new_status_id"]) == str(sig.get("to_status_id")):
                forced = c
        if forced:
            hist = {True: "VAR", False: "YOK", None: "kontrol edilmedi"}[forced["history_row_found"]]
            lines.append("Ticket penceresinde bu geçiş yapılmış: %s UTC LogId=%s, Operation.WorkOrderGpsInfo history satırı: %s, UpdateUserId %s."
                         % (forced["at_utc"], forced["log_id"], hist, "DEĞİŞMEMİŞ" if forced.get("update_user_unchanged") else "değişmiş"))
    manual = bool(forced and forced["history_row_found"] is False)
    if verdict == "inconclusive":
        lines.append("Doğrulama sorguları çalışmadı veya karar vermeye yetmedi.")
    return {"verdict": verdict, "label": VERDICT_TR[verdict], "details": lines, "manual_force": manual}


# ------------------------------------------------------------------ driver + case file section

def analyze(inc: dict, ex: Executor, pf: dict, bl: dict, all_cards: List[dict]) -> Optional[dict]:
    """Returns None (no state match) or {match, matches, card, results, evaluation, section, summary}.  Never raises."""
    if ex.mode != LIVE:
        return None
    try:
        matches = match_state_cards(inc, all_cards, pf, ex.queries)
        if not matches:
            return None
        top = matches[0]
        card = top["card"]
        results = run_verification(card, inc, ex, pf)
        ev = evaluate(card, results, bl.get("ticket_window"))
        if ev["verdict"] == "supports" and not top["state"]["words_hit"]:
            # Matched on STATE only (tenant + status): the ticket never asks for the card's target action (e.g. it is an Atlas
            # mismatch report), so the card's claim says nothing about THIS ticket -> hypothesis only, never adopted.
            ev = dict(ev, verdict="inconclusive", label=VERDICT_TR["inconclusive"], manual_force=False,
                      details=ev["details"] + ["Kart yalnızca durum (tenant + statü) ile eşleşti; ticket metni kartın hedef eylemini anmıyor: "
                                               "kart bu ticket için sadece HİPOTEZDİR, benimsenmez."])
        ka = {"match": top, "matches": matches, "card": card, "results": results, "evaluation": ev}
        ka["summary"] = summary(ka)
        ka["section"] = section_text(ka)
        return ka
    except Exception as e:                                                # noqa: BLE001 - must not kill the run
        return {"error": "%s: %s" % (e.__class__.__name__, str(e)[:150]), "match": None}


def summary(ka: dict) -> dict:
    c, m, ev = ka["card"], ka["match"], ka["evaluation"]
    return {"card_id": c["id"], "card_status": c["status"], "title": c["title"], "score": m["score"], "reasons": m["reasons"],
            "state": m["state"], "verdict": ev["verdict"], "verdict_tr": ev["label"], "details": ev["details"],
            "manual_force": ev["manual_force"], "classification": c.get("classification"),
            "queries": [{"query_id": r["query_id"], "deduped": r["deduped"], "executed": r["executed"], "note": r["note"]}
                        for r in ka["results"]]}


def section_text(ka: dict) -> str:
    c, m, ev = ka["card"], ka["match"], ka["evaluation"]
    L = ["## BİLİNEN SORUN KARTI (deterministik durum eşleşmesi + doğrulama; sorgular çalıştırıldı ve bütçeden sayıldı, TEKRAR ETME)",
         "Kart: %s [%s, güven %.2f] %s" % (c["id"], c["status"], float(c.get("confidence", 0)), c["title"]),
         "Eşleşme skoru %.2f: %s" % (m["score"], "; ".join(m["reasons"])),
         "Kök neden (kart): " + c["root_cause"]]
    if c.get("classification"):
        L.append("Sınıflandırma (kart): " + c["classification"])
    crct, cra = cardmod.card_axes(c)
    if crct or cra:
        L.append("Kök neden türü / yapılması gereken (kart): %s / %s" % (crct, cra))
    L.append("Çözüm adımları (kart):")
    L += ["  %d. %s" % (i, s) for i, s in enumerate(c.get("fix_steps") or [], 1)]
    L.append("Kartın doğrulama sorguları:")
    for r in ka["results"]:
        L.append("  - %s %s: %s" % (r["query_id"], r["params"], r["note"] or "çalıştırıldı"))
    L.append("DOĞRULAMA SONUCU: %s" % VERDICT_UPPER[ev["verdict"]])
    L += ["  " + d for d in ev["details"]]
    L.append("KURAL: Doğrulama destekliyorsa kartı benimse: evidence'a {type:card, ref:%s} ekle%s; kart fix_steps'ini kullan, kendi "
             "eklemelerini ayrıca belirt. Doğrulama çelişiyorsa bunu raporda AÇIKÇA söyle ve kartı kök neden olarak kullanma. "
             "Doğrulanamadıysa kartı sadece hipotez olarak an." % (
                 c["id"], ", sınıflandırmayı karttan al (%s)" % c["classification"] if c.get("classification") else ""))
    if ev["manual_force"]:
        L.append("NOT: Aynı geçiş geçmişte elle (DB'den) zorlanmış görünüyor; raporda süreç ihlali olarak belirt.")
    return "\n".join(L)


# ------------------------------------------------------------------ final report

def _dup(step: str, steps: List[str]) -> bool:
    f = cardmod.fold(step).strip()
    return any(cardmod.fold(s).strip() == f for s in steps)


def apply_to_report(rep: dict, ka: dict, executed_queries: int = 1) -> dict:
    """Make the final report consistent with the matched card.  Mutates and returns `rep`."""
    c, ev = ka["card"], ka["evaluation"]
    rep["matched_card"] = ka["summary"]
    if ev["verdict"] == "contradicts":
        return rep
    cited = any(e.get("type") == "card" and e.get("ref") == c["id"] for e in rep.get("evidence") or [])
    if not cited:
        rep.setdefault("evidence", []).append({
            "type": "card", "ref": c["id"], "deterministic": True,
            "finding": "Bilinen sorun kartı '%s' (%s) eşleşti (skor %.2f); doğrulama: %s. %s" % (
                c["title"], c["status"], ka["match"]["score"], ev["label"], " ".join(ev["details"][:2]))})
    if c["status"] == "confirmed" and ev["verdict"] == "supports":
        card_steps = list(c.get("fix_steps") or [])
        extra = [LLM_ADD + s for s in rep.get("fix_steps") or [] if not _dup(s, card_steps) and not s.startswith(LLM_ADD)]
        rep["fix_steps"] = card_steps + extra
        crct, cra = cardmod.card_axes(c)
        if crct and cra:
            rep["root_cause_type"], rep["resolution_action"] = crct, cra
            rep.pop("classification", None)
            axes.normalize(rep)                     # derives the legacy label from the axes
            if c.get("classification"):
                rep["classification"] = c["classification"]      # an explicit reviewed card label wins
        llm_rc = (rep.get("root_cause") or "").strip()
        rc = c["root_cause"]
        if ev["manual_force"]:
            rc += (" SÜREÇ İHLALİ: Bu geçiş ticket süresince DB'den elle zorlanmış görünüyor (Log.WorkOrderJsonLog'da durum değişikliği var, "
                   "Operation.WorkOrderGpsInfo'da history satırı yok).")
        if llm_rc and cardmod.fold(c["root_cause"])[:60] not in cardmod.fold(llm_rc):
            rc += "\n\nEk bulgular (LLM): " + llm_rc
        rep["root_cause"] = rc
        if executed_queries > 0:
            rep["confidence"] = round(max(float(rep.get("confidence", 0)), min(float(c.get("confidence", 0.85)), 0.9)), 2)
    return rep
