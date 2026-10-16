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
      "classification": "user_error" | ... | null,           # optional: same values as final_report.classification (reviewer / reports)
      "state_signature": {                                 # optional: status-transition cards (state-based matching, known_issue.py)
        "tenant_id": 70, "from_status_id": 2271, "to_status_id": 2257,
        "from_status_name": "...", "to_status_name": "...",
        "target_words": ["transfer"],                      # folded words of the action the user wants (substring match on the ticket text)
        "transition_configured": false                     # what the card claims about Catalog.StatusStep (null = unknown)
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
from typing import Any, Dict, Iterable, List, Optional

from . import axes, config
from .actions import CLASSIFICATIONS

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
        "classification": {"type": ["string", "null"], "enum": [None, *CLASSIFICATIONS]},
        "root_cause_type": {"type": ["string", "null"], "enum": [None, *axes.ROOT_CAUSE_TYPES]},
        "resolution_action": {"type": ["string", "null"], "enum": [None, *axes.RESOLUTION_ACTIONS]},
        "state_signature": {"type": "object", "required": ["tenant_id", "from_status_id", "to_status_id"], "properties": {
            "tenant_id": {"type": "integer"}, "from_status_id": {"type": "integer"}, "to_status_id": {"type": "integer"},
            "from_status_name": {"type": ["string", "null"]}, "to_status_name": {"type": ["string", "null"]},
            "target_words": {"type": "array", "items": {"type": "string"}},
            "transition_configured": {"type": ["boolean", "null"]}}},
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


# ------------------------------------------------------- state signature (status-transition cards)

_TW_STOP = set("status change order work with from that this have please sirius statu degistir".split())
_NAMES_RE = re.compile(r"status (\d+) \((.*?)\) -> (\d+) \((.*?)\) is ")


def target_words(to_name: Optional[str], summaries: Iterable[str] = ()) -> List[str]:
    """Folded words that tell the user wants to reach the target status: the to-status name (+ its >=5 char tokens as 6-char
    prefixes, so 'Cancelled' also hits 'cancel') and words shared by at least half of the evidence ticket summaries."""
    out: List[str] = []

    def add(w: str) -> None:
        if w and w not in out:
            out.append(w)
    name = fold(to_name or "").strip()
    if len(name) >= 4:
        add(name)
    for tok in re.findall(r"[a-z0-9]{5,}", name):
        add(tok[:6])
    sums = [set(re.findall(r"[a-z0-9]{5,}", fold(x))) for x in summaries if x]
    if len(sums) >= 2:
        for tok in sorted({t for t in set.union(*sums) if sum(t in x for x in sums) * 2 >= len(sums)}):
            if tok not in _TW_STOP:
                add(tok)
    return out[:8]


def build_state_signature(tenant_id: int, from_id: int, to_id: int, from_name: Optional[str] = None, to_name: Optional[str] = None,
                          transition_configured: Optional[bool] = None, summaries: Iterable[str] = ()) -> dict:
    return {"tenant_id": int(tenant_id), "from_status_id": int(from_id), "to_status_id": int(to_id),
            "from_status_name": (from_name or "").strip() or None, "to_status_name": (to_name or "").strip() or None,
            "target_words": target_words(to_name, summaries), "transition_configured": transition_configured}


def derive_state_signature(card: dict, summaries: Iterable[str] = ()) -> Optional[dict]:
    """state_signature of a footprint card from its status_step_check constants (+ names / claim from the root cause text)."""
    for q in card.get("verification_queries") or []:
        if q.get("query_id") != "status_step_check":
            continue
        p = q.get("params_from_entities") or {}
        try:
            vals = [p[k]["const"] for k in ("TenantId", "FromStatusId", "ToStatusId")]
            t, f, to = (int(v) for v in vals)
        except (KeyError, TypeError, ValueError):
            continue
        rc = card.get("root_cause") or ""
        m = _NAMES_RE.search(rc)
        fn, tn = (m.group(2), m.group(4)) if m and int(m.group(1)) == f and int(m.group(3)) == to else (None, None)
        conf = False if "NOT configured" in rc else (True if " is configured" in rc else None)
        return build_state_signature(t, f, to, fn, tn, conf, summaries)
    return None


def migrate_state_signatures(directory: Optional[Path] = None, tickets: Optional[Dict[str, dict]] = None) -> List[str]:
    """Add `state_signature` to cards that have none but carry a status_step_check with constant tenant/from/to. Idempotent."""
    d = directory or config.cards_dir()
    done: List[str] = []
    for c in load_cards(d, include_rejected=True):
        if c.get("state_signature"):
            continue
        sums = [((tickets or {}).get(k) or {}).get("summary") or "" for k in c.get("evidence_tickets") or []]
        sig = derive_state_signature(c, sums)
        if sig:
            c["state_signature"] = sig
            c.setdefault("history", []).append({"ts": now(), "event": "state_signature_added", "run_id": None,
                                                "note": "migration: tenant/from/to taken from status_step_check constants"})
            save_card(c, d)
            done.append(c["id"])
    return done


def card_axes(card: dict):
    """(root_cause_type, resolution_action) of a card: explicit axes, else derived from the legacy classification, else (None, None)."""
    rct, ra = card.get("root_cause_type"), card.get("resolution_action")
    if rct or ra:
        return rct, ra
    if card.get("classification"):
        return axes.derive_axes(card["classification"])
    return None, None


def migrate_axes(directory: Optional[Path] = None) -> List[str]:
    """Round-1 migration (idempotent): add root_cause_type / resolution_action to cards that have none.

    * card with a legacy classification            -> axes.derive_axes (confirmed Egypt card: user_error / explain_to_user)
    * footprint draft (state_signature) w/o classification:
        transition NOT configured (transition_configured False) -> config / data_fix   (StatusStep gap, developers force it by hand)
        transition configured                                   -> data_issue / data_fix (step exists, job/trigger or one-off data problem)
    Cards without classification and without state_signature (examples) are left alone."""
    d = directory or config.cards_dir()
    done: List[str] = []
    for c in load_cards(d, include_rejected=True):
        if c.get("root_cause_type") or c.get("resolution_action"):
            continue
        if c.get("classification"):
            rct, ra = axes.derive_axes(c["classification"])
        elif c.get("state_signature"):
            rct, ra = ("config", "data_fix") if c["state_signature"].get("transition_configured") is False else ("data_issue", "data_fix")
        else:
            continue
        c["root_cause_type"], c["resolution_action"] = rct, ra
        c.setdefault("history", []).append({"ts": now(), "event": "axes_added", "run_id": None,
                                            "note": "migration: root_cause_type=%s, resolution_action=%s" % (rct, ra)})
        save_card(c, d)
        done.append(c["id"])
    return done
