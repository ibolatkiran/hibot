"""Developer one-click feedback (D13) and card promotion.

    python -m hibot feedback <run_id> --verdict correct|wrong|partial [--note "..."]

* every verdict is appended to knowledge/feedback/feedback.jsonl
* correct : cards used as evidence get the ticket as evidence (+confirmed); `suggested_card` is created as `confirmed`
            (or the existing card gets the evidence); wrong: used cards get a `contradicted` history entry and lose confidence;
* partial : used cards get a note; `suggested_card` is stored as `draft` only.
Rejected cards are never revived automatically.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from . import cards as cardmod
from . import config, store

VERDICTS = ("correct", "wrong", "partial")


def _known_query_ids() -> Optional[set]:
    try:
        from hibot.db.templates import TemplateRegistry
        return set(TemplateRegistry.load().ids())
    except Exception:                                            # noqa: BLE001
        return None


def build_card(suggested: dict, run: dict, status: str, known_ids: Optional[set]) -> dict:
    rep, inc = run["report"], run.get("incident") or {}
    sig = dict(suggested.get("symptom_signature") or {})
    if not sig.get("keywords"):
        sig["keywords"] = [w for w in cardmod.fold(suggested.get("title") or inc.get("summary") or "").split() if len(w) > 3][:5]
    sig.setdefault("problem_type", None)
    sig.setdefault("app", None)
    sig.setdefault("entity_requirements", [])
    vqs = []
    for q in suggested.get("verification_queries") or []:
        if isinstance(q, dict) and q.get("query_id") and (known_ids is None or q["query_id"] in known_ids):
            vqs.append({"query_id": q["query_id"], "params_from_entities": q.get("params_from_entities") or {}})
    ev = [inc["key"]] if inc.get("key") else ["run:" + run["run_id"]]
    title = suggested.get("title") or (rep.get("summary") or "")[:80]
    card = {
        "id": suggested.get("id") or cardmod.slugify(title), "title": title, "symptom_signature": sig,
        "root_cause": suggested.get("root_cause") or rep.get("root_cause", ""),
        "verification_queries": vqs, "fix_steps": suggested.get("fix_steps") or rep.get("fix_steps") or [],
        "code_locations": suggested.get("code_locations") or rep.get("code_locations") or [],
        "confidence": round(min(float(rep.get("confidence", 0.5)), 0.9), 2), "status": status,
        "evidence_tickets": ev, "created_by": "hibot (run %s) + developer feedback" % run["run_id"],
        "created_at": cardmod.now(),
        "history": [{"ts": cardmod.now(), "event": "created" if status == "draft" else "confirmed", "run_id": run["run_id"],
                     "note": "created from suggested_card of the analysis"}],
    }
    return card


def _touch(card: dict, event: str, run: dict, note: str = "") -> None:
    card.setdefault("history", []).append({"ts": cardmod.now(), "event": event, "run_id": run["run_id"], "note": note[:300]})


def record_feedback(run_id: str, verdict: str, note: str = "", *, runs_base: Optional[Path] = None,
                    cards_base: Optional[Path] = None, feedback_base: Optional[Path] = None) -> dict:
    if verdict not in VERDICTS:
        raise ValueError("verdict must be one of %s" % ", ".join(VERDICTS))
    run = store.load_run(run_id, runs_base)
    rep, inc = run["report"], run.get("incident") or {}
    cards_dir = cards_base or config.cards_dir()
    actions: List[str] = []
    ticket = inc.get("key")
    used = [ev["ref"] for ev in rep.get("evidence") or [] if ev.get("type") == "card"]
    sug = rep.get("suggested_card")
    if run.get("demo"):                       # scripted demo output must never teach the knowledge base
        used, sug = [], None
        actions.append("demo run: verdict recorded, cards untouched")
    for cid in dict.fromkeys(used):
        try:
            p = cardmod.card_path(cid, cards_dir)
        except ValueError:
            continue
        if not p.exists():
            continue
        card = json.loads(p.read_text(encoding="utf-8"))
        if card.get("status") == "rejected":
            continue
        if verdict == "correct":
            if ticket and ticket not in card.setdefault("evidence_tickets", []):
                card["evidence_tickets"].append(ticket)
            if card["status"] == "draft":
                card["status"] = "confirmed"
            card["confidence"] = round(min(0.95, float(card.get("confidence", 0.5)) + 0.1), 2)
            _touch(card, "confirmed", run, note)
        elif verdict == "wrong":
            card["confidence"] = round(max(0.05, float(card.get("confidence", 0.5)) - 0.15), 2)
            _touch(card, "contradicted", run, note)
        else:
            _touch(card, "partially_correct", run, note)
        cardmod.save_card(card, cards_dir)
        actions.append("%s: card %s updated (%s)" % (verdict, cid, card["status"]))
    if sug and verdict in ("correct", "partial"):
        card = build_card(sug, run, "confirmed" if verdict == "correct" else "draft", _known_query_ids())
        p = cardmod.card_path(card["id"], cards_dir)
        if p.exists():
            old = json.loads(p.read_text(encoding="utf-8"))
            if old.get("status") == "rejected":
                actions.append("suggested card %s skipped: an existing card with this id was rejected" % card["id"])
            else:
                for t in card["evidence_tickets"]:
                    if t not in old.setdefault("evidence_tickets", []):
                        old["evidence_tickets"].append(t)
                if verdict == "correct" and old["status"] == "draft":
                    old["status"] = "confirmed"
                _touch(old, "evidence_added", run, note)
                cardmod.save_card(old, cards_dir)
                actions.append("existing card %s: evidence added (%s)" % (card["id"], old["status"]))
        else:
            errs = cardmod.validate_card(card)
            if errs:
                actions.append("suggested card invalid, not stored: %s" % "; ".join(errs[:2]))
            else:
                cardmod.save_card(card, cards_dir)
                actions.append("card %s created (%s)" % (card["id"], card["status"]))
    entry = {"ts": cardmod.now(), "run_id": run_id, "ticket": ticket, "verdict": verdict, "note": note[:1000],
             "classification": rep.get("classification"), "root_cause_type": rep.get("root_cause_type"),
             "resolution_action": rep.get("resolution_action"), "confidence": rep.get("confidence"),
             "mode": run.get("mode"), "executed_queries": rep.get("executed_queries"), "card_actions": actions}
    fdir = feedback_base or config.feedback_dir()
    fdir.mkdir(parents=True, exist_ok=True)
    with open(fdir / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"feedback_file": str(fdir / "feedback.jsonl"), "actions": actions, "entry": entry}
