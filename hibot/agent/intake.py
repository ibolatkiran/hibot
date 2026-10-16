"""Step 1+2 of the pipeline: build the incident record (Jira key or free text) and find similar cases.

Entity extraction / BM25 are imported from tools/incidents (not copied).  Those modules use `from common import ...`
so tools/incidents is put on sys.path only while importing them.
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from typing import Any, Dict, List, Optional

from . import access, config

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


class IntakeError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _tools():
    d = str(config.ROOT / "tools" / "incidents")
    saved = sys.modules.pop("common", None)
    sys.path.insert(0, d)
    try:
        import extract  # type: ignore
        import similarity  # type: ignore
    finally:
        try:
            sys.path.remove(d)
        except ValueError:
            pass
        sys.modules.pop("common", None)
        if saved is not None:
            sys.modules["common"] = saved
    return extract, similarity


@lru_cache(maxsize=2)
def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _incidents_dir():
    return config.knowledge_dir() / "incidents"


def load_tickets() -> List[dict]:
    p = _incidents_dir() / "tickets_enriched.json"
    if not p.exists():
        raise IntakeError("%s not found - run: python tools/incidents/build.py" % p)
    return _load_json(str(p))["tickets"]


def load_groups() -> Dict[str, dict]:
    p = _incidents_dir() / "groups.json"
    if not p.exists():
        return {}
    return {g["group_id"]: g for g in _load_json(str(p))["groups"]}


def region_guess(country: Optional[str], company_code: Optional[str] = None) -> str:
    if country and country.upper() in config.APAC_COUNTRIES:
        return "APAC"
    if company_code and company_code.upper().startswith("AH") and company_code.upper() not in ("AHTR",):
        return "APAC"
    return "EU"


def _brief(rec: dict, score: Optional[float] = None) -> dict:
    tc = rec.get("text_clean") or {}
    out = {"key": rec["key"], "summary": (tc.get("summary") or "")[:200],
           "problem_type": rec.get("problem_type"), "app": rec.get("app_guess"),
           "country": rec.get("country"), "group_id": rec.get("group_id"),
           "resolution": rec.get("resolution")}
    if score is not None:
        out["score"] = score
    return out


def from_jira_key(key: str, extra_text: str = "") -> dict:
    tickets = load_tickets()
    rec = next((t for t in tickets if t["key"] == key), None)
    if rec is None:
        raise IntakeError("ticket %s not found in knowledge/incidents/tickets_enriched.json" % key)
    by_key = {t["key"]: t for t in tickets}
    tc = rec.get("text_clean") or {}
    desc = tc.get("desc") or ""
    if extra_text:
        desc = (desc + "\n" + extra_text).strip()
    comments = [{"author": c.get("author"), "created": c.get("created"), "text": (c.get("text") or "")[:400]}
                for c in (rec.get("comments") or [])[:6]]
    similar = [_brief(by_key[s["key"]], s.get("score")) for s in rec.get("similar") or [] if s["key"] in by_key]
    group = None
    if rec.get("group_id"):
        g = load_groups().get(rec["group_id"])
        if g:
            group = {k: g.get(k) for k in ("group_id", "size", "representative_key", "representative_title",
                                           "dominant_problem_type", "dominant_app", "dominant_country",
                                           "common_terms", "first_seen", "last_seen")}
    return {
        "source": "jira", "key": key, "summary": tc.get("summary") or "", "desc": desc,
        "comments": comments, "comments_note": "Jira comments are unverified (D13); do not treat as ground truth.",
        "entities": rec.get("entities") or {}, "comment_entities": rec.get("comment_entities") or {},
        "problem_type": rec.get("problem_type"), "app": rec.get("app_guess"), "country": rec.get("country"),
        "company_code": rec.get("company_code"), "language": rec.get("language"),
        "region_guess": region_guess(rec.get("country"), rec.get("company_code")),
        "screen_hints": rec.get("screen_hints") or [], "status": rec.get("status"), "created": rec.get("created"),
        "resolved": rec.get("resolved"), "resolution": rec.get("resolution"), "similar": similar, "group": group,
    }


def from_text(text: str) -> dict:
    extract, _ = _tools()
    text = (text or "").strip()
    if not text:
        raise IntakeError("empty ticket text")
    first, _, rest = text.partition("\n")
    summary, desc = (first[:300], rest.strip()) if rest.strip() else (text[:200], text)
    matcher = extract.ScreenMatcher(str(config.knowledge_dir() / "codemap"))
    ex = extract.Extractor((), matcher if matcher.ok else None)
    r = ex.extract({"summary": summary, "desc": desc, "service": "", "sub_service": ""})
    country = r["country"] or {}
    app = r["app"] or {}
    problem = r["problem"] or {}
    return {
        "source": "text", "key": None, "summary": r["text_clean"]["summary"], "desc": r["text_clean"]["desc"],
        "comments": [], "entities": r["entities"], "comment_entities": {},
        "problem_type": problem.get("problem_type"), "app": app.get("app_guess"),
        "country": country.get("country"), "company_code": country.get("company_code"),
        "language": (r.get("language") or {}).get("language") if isinstance(r.get("language"), dict) else r.get("language"),
        "region_guess": region_guess(country.get("country"), country.get("company_code")),
        "screen_hints": r["screen_hints"], "similar": [], "group": None,
    }


def find_similar_for_text(incident: dict, top_k: int = 5) -> List[dict]:
    """BM25 (tools/incidents/similarity.py) of free text against all known tickets."""
    extract, sim = _tools()
    try:
        tickets = load_tickets()
    except IntakeError:
        return []
    docs = []
    for t in tickets:
        tc = t.get("text_clean") or {}
        docs.append(sim.tokenize(tc.get("summary", ""), bigrams=True) * 2 + sim.tokenize(tc.get("desc", "")))
    q = sim.tokenize(incident["summary"], bigrams=True) * 2 + sim.tokenize(incident["desc"])
    if not q:
        return []
    docs.append(q)
    idx = sim.BM25Index(docs)
    qi = len(docs) - 1
    raw = idx.scores(qi)
    cands = sorted(raw.items(), key=lambda kv: -kv[1])[:top_k * 3]
    out = []
    for j, s in cands:
        den = idx.self_scores[qi] + idx.self_scores[j]
        v = (s + idx._score_pair(j, qi)) / den if den else 0.0
        out.append((j, v))
    out.sort(key=lambda kv: -kv[1])
    return [_brief(tickets[j], round(v, 3)) for j, v in out[:top_k]]


def _add_user_codes(inc: dict) -> dict:
    """Access tickets: user identifiers (code-like tokens only, see access.py) go to entities['user_codes'] for the playbook."""
    if access.is_access_incident(inc):
        ents = dict(inc.get("entities") or {})
        ents.pop("user_codes", None)
        inc["entities"] = ents
        codes = access.incident_user_codes(inc)
        if codes:
            ents["user_codes"] = codes
    return inc


def build_incident(key: Optional[str] = None, text: Optional[str] = None) -> dict:
    """Jira key -> precomputed record; free text -> fresh extraction + BM25 similarity.
    key + text where the key is unknown: text is analysed as free text and the key is kept as a label."""
    if key:
        if not KEY_RE.match(key):
            raise IntakeError("bad Jira key %r" % key)
        try:
            return _add_user_codes(from_jira_key(key, extra_text=text or ""))
        except IntakeError:
            if not text:
                raise
    inc = from_text(text or "")
    inc["key"] = key
    inc["similar"] = find_similar_for_text(inc)
    return _add_user_codes(inc)
