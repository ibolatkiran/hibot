"""Known-issue cards: format, validation, storage and matching.

Card file: knowledge/cards/<id>.json

    {
      "id": "missing_tenant_for_sku",                      # [a-z0-9_-]{3,64}, == file name
      "title": "Missing tenant for SKU in Sirius (Poland)",
      "symptom_signature": {
        "problem_type": "master_data_request" | null,      # tools/incidents taxonomy
        "app": "portal" | "crm" | "partner" | "technician" | null,
        "keywords": ["missing tenant", "sku"],             # folded, matched as substrings of the ticket text
        "entity_requirements": ["sku_codes"]               # entity lists (ticket.entities) that must be non-empty
      },
      "root_cause": "...",
      "verification_queries": [{"query_id": "product_sku_lookup",
                                 "params_from_entities": {"Sku": "entities.sku_codes[0]", "TenantId": "$tenant"}}],
      "fix_steps": ["..."],
      "code_locations": [{"repo": "SiriusApi", "path": "...", "line": 1, "why": "..."}],
      "confidence": 0.5,
      "status": "draft" | "confirmed" | "rejected",
      "evidence_tickets": ["SM-..."],
      "created_by": "...", "created_at": "...",
      "history": [{"ts": "...", "event": "created|confirmed|contradicted|evidence_added|...", "run_id": "...", "note": "..."}]
    }

`params_from_entities` values: "entities.<field>[<i>]" (first item by default), "$tenant" (to be resolved with
tenant_lookup first - the LLM is told), or {"const": value}.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

STATUSES = ("draft", "confirmed", "rejected")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
PROBLEM_TYPES = ("master_data_request", "access_request", "data_correction", "integration_failure",
                 "notification_failure", "performance_availability", "mobile_offline", "report_export",
                 "how_to_question", "functional_defect", "other")

CARD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["id", "title", "symptom_signature", "root_cause", "verification_queries", "fix_steps", "confidence", "status"],
    "properties": {
        "id": {"type": "string"}, "title": {"type": "string", "minLength": 3},
        "symptom_signature": {"type": "object", "required": ["keywords"], "properties": {
            "problem_type": {"type": ["string", "null"]}, "app": {"type": ["string", "null"]},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "entity_requirements": {"type": "array", "items": {"type": "string"}}}},
        "root_cause": {"type": "string", "minLength": 3},
        "verification_queries": {"type": "array", "items": {"type": "object", "required": ["query_id"], "properties": {
            "query_id": {"type": "string"}, "params_from_entities": {"type": "object"}}}},
        "fix_steps": {"type": "array", "items": {"type": "string"}},
        "code_locations": {"type": "array", "items": {"type": "object"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "status": {"type": "string", "enum": list(STATUSES)},
        "evidence_tickets": {"type": "array", "items": {"type": "string"}},
        "created_by": {"type": "string"}, "history": {"type": "array", "items": {"type": "object"}},
    },
}


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def fold(s: str) -> str:
    s = (s or "").translate(str.maketrans({"ı": "i", "İ": "i", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
                                           "ş": "s", "Ş": "s", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})).lower()
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", fold(title)).strip("_")[:48]
    return s if len(s) >= 3 else "card_" + s.ljust(3, "x")


def validate_card(card: Any) -> List[str]:
    from hibot.llm.jsonio import validate_schema
    errs = validate_schema(card, CARD_SCHEMA)
    if errs:
        return errs
    if not ID_RE.match(card["id"]):
        errs.append("$.id: must match %s" % ID_RE.pattern)
    return errs


def card_path(card_id: str, directory: Optional[Path] = None) -> Path:
    if not ID_RE.match(card_id or ""):
        raise ValueError("bad card id %r" % card_id)
    return (directory or config.cards_dir()) / (card_id + ".json")


def load_cards(directory: Optional[Path] = None, include_rejected: bool = False) -> List[dict]:
    d = directory or config.cards_dir()
    out = []
    if not d.exists():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if validate_card(c) or c["id"] != f.stem:
            continue                                  # malformed cards are skipped, never crash the run
        if c["status"] == "rejected" and not include_rejected:
            continue
        out.append(c)
    return out


def save_card(card: dict, directory: Optional[Path] = None) -> Path:
    errs = validate_card(card)
    if errs:
        raise ValueError("invalid card: " + "; ".join(errs[:3]))
    p = card_path(card["id"], directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


# ---------------------------------------------------------------- matching

def incident_text(inc: dict) -> str:
    return fold("%s\n%s" % (inc.get("summary", ""), inc.get("desc", "")))


def match_cards(inc: dict, cards: List[dict], limit: int = 3, min_score: float = 0.3) -> List[dict]:
    """Return [{card, score, reasons, missing_entities}] best first.

    score = 0.25 problem_type + 0.15 app + 0.45 * keyword_share + 0.15 entity requirements met;
    unmet entity requirements halve the score (the card is probably not applicable yet).
    Confirmed cards get +0.05; rejected cards are never returned.
    """
    text = incident_text(inc)
    ents = inc.get("entities") or {}
    out = []
    for c in cards:
        if c.get("status") == "rejected":
            continue
        sig = c.get("symptom_signature") or {}
        score, reasons = 0.0, []
        if sig.get("problem_type"):
            if sig["problem_type"] == inc.get("problem_type"):
                score += 0.25
                reasons.append("problem_type=%s" % sig["problem_type"])
        else:
            score += 0.10
        if sig.get("app"):
            if sig["app"] == inc.get("app"):
                score += 0.15
                reasons.append("app=%s" % sig["app"])
        else:
            score += 0.05
        kws = [fold(k) for k in sig.get("keywords") or [] if k.strip()]
        if kws:
            hit = [k for k in kws if k in text]
            if hit:
                score += 0.45 * len(hit) / len(kws)
                reasons.append("keywords %d/%d: %s" % (len(hit), len(kws), ", ".join(hit[:4])))
        reqs = sig.get("entity_requirements") or []
        missing = [r for r in reqs if not ents.get(r)]
        if reqs and not missing:
            score += 0.15
            reasons.append("entities present: " + ", ".join(reqs))
        elif missing:
            score *= 0.5
        if c.get("status") == "confirmed":
            score += 0.05
        score = round(min(score, 1.0), 3)
        if score >= min_score:
            out.append({"card": c, "score": score, "reasons": reasons, "missing_entities": missing})
    out.sort(key=lambda m: (-m["score"], m["card"]["id"]))
    return out[:limit]


_PATH_RE = re.compile(r"^entities\.(\w+)(?:\[(\d+)\])?$")


def resolve_card_queries(card: dict, inc: dict) -> List[dict]:
    """Concrete query suggestions: [{query_id, params, unresolved:[param names]}]."""
    ents = inc.get("entities") or {}
    res = []
    for q in card.get("verification_queries") or []:
        params, unresolved = {}, []
        for name, spec in (q.get("params_from_entities") or {}).items():
            val = None
            if isinstance(spec, dict) and "const" in spec:
                val = spec["const"]
            elif isinstance(spec, str):
                m = _PATH_RE.match(spec)
                if m:
                    lst = ents.get(m.group(1)) or []
                    i = int(m.group(2) or 0)
                    val = lst[i] if i < len(lst) else None
            if val is None:
                unresolved.append(name)
            else:
                params[name] = val
        res.append({"query_id": q["query_id"], "params": params, "unresolved": unresolved})
    return res
