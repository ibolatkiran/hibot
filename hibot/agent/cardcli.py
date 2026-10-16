"""Review CLI for known-issue cards:  python -m hibot cards list|show|confirm|reject|annotate  (see hibot/agent/README.md).

Every review action is written to the card's `history` (who="user", timestamp, note, overrides).  Cards stay valid per
cards.CARD_SCHEMA (they are validated again on save).  Rejected cards are never revived by feedback; only an explicit
`cards confirm` by the user changes that.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import cards as cardmod
from . import config

CONFIRMED_MIN_CONF = 0.85
WHO = "user"


def _dir(directory: Optional[Path]) -> Path:
    return directory or config.cards_dir()


def get_card(card_id: str, directory: Optional[Path] = None) -> dict:
    p = cardmod.card_path(card_id, _dir(directory))
    if not p.exists():
        raise KeyError("card %r not found in %s" % (card_id, _dir(directory)))
    return json.loads(p.read_text(encoding="utf-8"))


def list_cards(status: Optional[str] = None, directory: Optional[Path] = None) -> List[dict]:
    cs = cardmod.load_cards(_dir(directory), include_rejected=True)
    if status:
        cs = [c for c in cs if c["status"] == status]
    order = {"confirmed": 0, "draft": 1, "rejected": 2}
    return sorted(cs, key=lambda c: (order.get(c["status"], 9), -float(c.get("confidence", 0)), c["id"]))


def format_list(cs: List[dict]) -> str:
    rows = [("id", "status", "conf", "support", "title")]
    rows += [(c["id"], c["status"], "%.2f" % float(c.get("confidence", 0)), str(len(c.get("evidence_tickets") or [])), c["title"]) for c in cs]
    w = [max(len(r[i]) for r in rows) for i in range(4)]
    return "\n".join("  ".join(r[i].ljust(w[i]) for i in range(4)) + "  " + r[4] for r in rows) + "\n(%d card(s))" % len(cs)


def format_card(c: dict) -> str:
    sig = c.get("symptom_signature") or {}
    L = ["%s  [%s]  güven %.2f  destek %d ticket" % (c["id"], c["status"].upper(), float(c.get("confidence", 0)), len(c.get("evidence_tickets") or [])),
         "Başlık: " + c["title"]]
    if c.get("classification"):
        L.append("Sınıflandırma: " + c["classification"])
    if c.get("root_cause_type") or c.get("resolution_action"):
        L.append("Kök neden türü / yapılması gereken: %s / %s" % (c.get("root_cause_type"), c.get("resolution_action")))
    L.append("Belirti imzası: problem_type=%s app=%s anahtar kelimeler=%s gerekli varlıklar=%s" % (
        sig.get("problem_type"), sig.get("app"), ", ".join(sig.get("keywords") or []) or "-", ", ".join(sig.get("entity_requirements") or []) or "-"))
    ss = c.get("state_signature")
    if ss:
        L.append("Durum imzası: tenant %s, %s (%s) -> %s (%s), geçiş tanımlı mı: %s, hedef kelimeler: %s" % (
            ss["tenant_id"], ss["from_status_id"], ss.get("from_status_name") or "?", ss["to_status_id"], ss.get("to_status_name") or "?",
            {True: "evet", False: "hayır", None: "bilinmiyor"}[ss.get("transition_configured")], ", ".join(ss.get("target_words") or []) or "-"))
    L += ["", "Kök neden:", "  " + c["root_cause"], "", "Doğrulama sorguları:"]
    for q in c.get("verification_queries") or []:
        L.append("  - %s %s" % (q["query_id"], json.dumps(q.get("params_from_entities") or {}, ensure_ascii=False)))
    L += ["", "Çözüm adımları:"] + ["  %d. %s" % (i, s) for i, s in enumerate(c.get("fix_steps") or [], 1)]
    ev = c.get("evidence_tickets") or []
    L += ["", "Kanıt ticket'ları (%d): %s" % (len(ev), ", ".join(ev) or "-"), "", "Geçmiş:"]
    for h in c.get("history") or []:
        extra = (" (%s)" % ", ".join("%s: %s" % (k, str(v["new"])[:60]) for k, v in (h.get("overrides") or {}).items())) if h.get("overrides") else ""
        L.append("  %s  %s%s%s%s%s" % (h.get("ts"), h.get("event"), " by " + h["who"] if h.get("who") else "",
                                       " run=" + h["run_id"] if h.get("run_id") else "", ": " + h["note"] if h.get("note") else "", extra))
    return "\n".join(L)


def _event(event: str, note: str, **kw: Any) -> dict:
    e = {"ts": cardmod.now(), "event": event, "who": WHO, "run_id": None, "note": (note or "")[:500]}
    e.update({k: v for k, v in kw.items() if v})
    return e


def confirm(card_id: str, note: str = "", *, root_cause: Optional[str] = None, fix_steps: Optional[List[str]] = None,
            classification: Optional[str] = None, title: Optional[str] = None, directory: Optional[Path] = None) -> dict:
    d = _dir(directory)
    c = get_card(card_id, d)
    over: Dict[str, Dict[str, Any]] = {}
    for key, val in (("title", title), ("root_cause", root_cause), ("fix_steps", fix_steps), ("classification", classification)):
        if val:
            over[key] = {"old": c.get(key), "new": val}
            c[key] = val
    was = c["status"]
    c["status"] = "confirmed"
    c["confidence"] = round(max(float(c.get("confidence", 0)), CONFIRMED_MIN_CONF), 2)
    c.setdefault("history", []).append(_event("confirmed", note, overrides=over, status_before=was))
    cardmod.save_card(c, d)                         # validates the schema, atomic write
    return c


def reject(card_id: str, note: str, directory: Optional[Path] = None) -> dict:
    if not (note or "").strip():
        raise ValueError("reject needs --note")
    d = _dir(directory)
    c = get_card(card_id, d)
    was = c["status"]
    c["status"] = "rejected"
    c.setdefault("history", []).append(_event("rejected", note, status_before=was))
    cardmod.save_card(c, d)
    return c


def annotate(card_id: str, note: str, directory: Optional[Path] = None) -> dict:
    if not (note or "").strip():
        raise ValueError("annotate needs --note")
    d = _dir(directory)
    c = get_card(card_id, d)
    c.setdefault("history", []).append(_event("annotated", note))
    cardmod.save_card(c, d)
    return c
