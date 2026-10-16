"""Access-request recognition and user-identifier extraction from ticket text (round 1).

Only identifiers that are SAFE to use as a lookup key are extracted (never names, never full e-mail addresses):
  * code-like tokens: one letter + 6-8 digits (e.g. C1234567)
  * a labelled token after "user id / user code / userid / login / kullanıcı (kodu|adı) / technician code / teknisyen kodu / sicil"
    that contains a digit (placeholders like <USERID> never match)
  * the local part of an e-mail address, but only when it is itself code-like
Order of appearance, duplicates removed, capped at MAX_CODES; tokens equal to a SKU / work-order entity are dropped.
"""
from __future__ import annotations

import re
from typing import Iterable, List

MAX_CODES = 3
ACCESS_RE = re.compile(
    r"\baccess\b|\bpermission|\bauthori[sz]|not authori|\blog\s?in\b|\blogin\b|\blogon\b|sign.?in|password|\bokta\b|\bsso\b|\brole\b|"
    r"yetki|eri[sş]im|giri[sş]|[sş]ifre|parola|kullan[ıi]c[ıi]\s+(?:a[cç]|olu[sş]tur|tan[ıi]mla|sil)|hesap|account|"
    r"\buser\b.{0,40}\b(?:create|add|delete|disable|enable|activate|deactivate|block|unlock|reset)|disabled|deactivat|locked|kilit",
    re.I)
CODE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]\d{6,8}(?![A-Za-z0-9])")
LABELLED_RE = re.compile(
    r"(?:user\s*[-_ ]?\s*(?:id|code|name)|userid|login(?:\s*(?:id|name))?|kullan[ıi]c[ıi](?:\s*(?:kodu|ad[ıi]|id))?|technician\s*code|"
    r"teknisyen\s*kodu|sicil(?:\s*no)?)\s*[:=#\-]?\s*([A-Za-z0-9._\-]{4,30})", re.I)
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]{3,40})@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER_CODE_PARAM_MAX = 50


def is_access_text(text: str) -> bool:
    return bool(ACCESS_RE.search(text or ""))


def extract_user_codes(text: str, exclude: Iterable[str] = ()) -> List[str]:
    text = text or ""
    skip = {str(x).strip().lower() for x in exclude}
    cands = []
    for m in re.finditer(CODE_RE.pattern + "|" + LABELLED_RE.pattern + "|" + EMAIL_RE.pattern, text, re.I):
        tok = None
        if m.group(0) and CODE_RE.fullmatch(m.group(0)):
            tok = m.group(0)
        elif m.lastindex:
            g = next((m.group(i) for i in range(1, (m.re.groups or 0) + 1) if m.group(i)), None)
            if g is not None:
                at_email = "@" in m.group(0)
                tok = g if not at_email else (g if CODE_RE.fullmatch(g) else None)
                if tok and not at_email and not re.search(r"\d", tok):
                    tok = None
        if not tok:
            continue
        tok = tok.strip("._-")
        if len(tok) < 4 or len(tok) > USER_CODE_PARAM_MAX or tok.lower() in skip or tok.lower() in {c.lower() for c in cands}:
            continue
        cands.append(tok)
    return cands[:MAX_CODES]


def incident_user_codes(inc: dict) -> List[str]:
    ents = inc.get("entities") or {}
    if ents.get("user_codes"):
        return list(ents["user_codes"])[:MAX_CODES]
    exclude = [str(v) for k in ("sku_codes", "work_order_ids", "work_order_ids_strong", "work_case_ids", "material_codes", "part_numbers")
               for v in ents.get(k) or []]
    return extract_user_codes("%s\n%s" % (inc.get("summary") or "", inc.get("desc") or ""), exclude)


def is_access_incident(inc: dict) -> bool:
    return inc.get("problem_type") == "access_request" or is_access_text("%s\n%s" % (inc.get("summary") or "", inc.get("desc") or ""))
