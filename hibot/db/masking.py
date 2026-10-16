"""PII masking for query results.

Result rows are masked *before* they leave the database layer, so neither the
LLM prompt nor any log ever sees raw personal data.  Three layers:

1. Declared PII columns (from the query template header or the catalog):
   the whole value is masked according to its kind.
2. Heuristics on result column names (email, phone, first/last name, address,
   IBAN, tax/id number, secrets ...) for strings.
3. Regex masking inside *every* string value (e-mails, IBAN with mod-97
   check, payment cards with Luhn, international phone numbers, JWT / bearer
   tokens, and values of PII-looking keys in JSON / XML / key=value text), plus
   truncation of long bodies.

Masking is deliberately conservative about *identifiers*: plain digit strings
(work order ids, SAP document numbers) are not touched unless a PII key or
column says so.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import re
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

DEFAULT_BODY_LIMIT = 600      # chars kept from request/response/json bodies
DEFAULT_TEXT_LIMIT = 2000     # chars kept from any other string cell
ERROR_TEXT_LIMIT = 1500       # error/message columns carry the diagnosis

# ----------------------------------------------------------------- regexes

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]*")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{12,}")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_CARD = re.compile(r"(?<![\w.+])(?:\d[ -]?){13,19}(?![\w])")
# protected shapes that look phone-ish but are not
_PHONE_ALT = re.compile(
    r"(?P<date>\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b|\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b)"
    r"|(?P<guid>\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b)"
    r"|(?P<ip>\b\d{1,3}(?:\.\d{1,3}){3}\b)"
    r"|(?P<intl>(?<![\w+])\+\d[\d\s().\-]{6,18}\d)"
    r"|(?P<grp>(?<![\w.:/])(?:\(?\d{2,4}\)?[\s.\-]){2,5}\d{2,4}(?![\w.:/]))"
)
# "key": "value" / "key": 12345 / <key>value</key> / key=value
_JSON_PAIR = re.compile(r'("(?P<k>[A-Za-z0-9_.\-]{2,50})"\s*:\s*)(?:"(?P<sv>(?:[^"\\]|\\.)*)"|(?P<nv>-?\d[\d.]*))')
_XML_PAIR = re.compile(r"<(?P<k>[A-Za-z][A-Za-z0-9_.\-]{1,50})>(?P<v>[^<>]{1,300})</(?P=k)>")
_KV_PAIR = re.compile(r"(?i)(?<![\w.])(?P<k>[A-Za-z][A-Za-z0-9_\-]{1,40})(?P<sep>\s*[=:]\s*)(?P<v>[^\s&;,\"'<>]{1,200})")

_KEY_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("secret", ("password", "passwd", "pwd", "secret", "token", "apikey", "authorization",
                "connectionstring", "privatekey", "accesskey")),
    ("email", ("email", "mailaddress")),
    ("phone", ("phone", "mobile", "gsm", "whatsapp", "fax", "telephone", "msisdn")),
    ("iban_bank", ("iban", "accountnumber", "cardnumber", "cvv", "bankaccount")),
    ("identity", ("taxnumber", "taxid", "idnumber", "nationalid", "identitynumber", "tckn",
                  "vkn", "ssn", "passport", "birthdate", "birthday", "dateofbirth")),
    ("name", ("firstname", "lastname", "surname", "fullname", "middlename", "contactname",
              "customername", "consumername", "recipientname", "technicianname",
              "employeename", "username", "adsoyad")),
    ("address", ("address", "street", "postcode", "zipcode", "postalcode", "district",
                 "neighborhood")),
)
_EXACT_KEYS = {"tel": "phone", "gsm": "phone", "phone": "phone", "mail": "email", "email": "email"}


def classify_key(key: str) -> Optional[str]:
    """Map a JSON key / column name to a PII kind (or None)."""
    k = re.sub(r"[^a-z0-9]", "", key.lower())
    if not k:
        return None
    if k in _EXACT_KEYS:
        return _EXACT_KEYS[k]
    for kind, needles in _KEY_RULES:
        if any(n in k for n in needles):
            return kind
    if k.startswith("tel") and len(k) <= 8:      # tel1, tel2, telno
        return "phone"
    return None


_BODY_HINTS = ("body", "payload", "request", "response", "json", "xml", "stacktrace",
               "sendingmessage", "messagetext", "data")
_ERROR_HINTS = ("error", "message", "exception", "note", "comment", "description", "text", "debug")


# ---------------------------------------------------------- value maskers

def mask_email(s: str) -> str:
    m = re.match(r"([^@]+)@(.+)\.([A-Za-z]{2,})$", s.strip())
    if not m:
        return "[EMAIL]"
    local, dom, tld = m.groups()
    return "%s***@%s***.%s" % (local[:1], dom[:1], tld)


def mask_phone(s: Any) -> str:
    digits = re.sub(r"\D", "", str(s))
    if len(digits) < 4:
        return "[PHONE]"
    return "***" + digits[-2:]


def mask_name(s: Any) -> str:
    parts = [p for p in re.split(r"(\s+)", str(s)) if p != ""]
    out = []
    for p in parts:
        out.append(p if p.isspace() else p[:1] + "***")
    return "".join(out) or "[NAME]"


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "").upper()
    if not (15 <= len(s) <= 34) or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", s):
        return False
    rearr = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in rearr)
    return int(num) % 97 == 1


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _mask_kind(kind: str, value: str) -> str:
    if kind == "email":
        return mask_email(value) if "@" in value else "[EMAIL]"
    if kind == "phone":
        return mask_phone(value)
    if kind == "contact":           # e-mail address or phone number
        return mask_email(value) if "@" in value else mask_phone(value)
    if kind == "name":
        return mask_name(value)
    if kind == "address":
        return "[ADDRESS]"
    if kind == "secret":
        return "[REDACTED]"
    return "[REDACTED]"          # identity, iban_bank, anything else


def _sub_json(m: "re.Match") -> str:
    kind = classify_key(m.group("k"))
    if kind is None:
        return m.group(0)
    val = m.group("sv") if m.group("sv") is not None else m.group("nv")
    if val is None or val == "":
        return m.group(0)
    return '%s"%s"' % (m.group(1), _mask_kind(kind, val))


def _sub_xml(m: "re.Match") -> str:
    kind = classify_key(m.group("k"))
    if kind is None or not m.group("v").strip():
        return m.group(0)
    return "<%s>%s</%s>" % (m.group("k"), _mask_kind(kind, m.group("v")), m.group("k"))


def _sub_kv(m: "re.Match") -> str:
    kind = classify_key(m.group("k"))
    if kind is None:
        return m.group(0)
    return m.group("k") + m.group("sep") + _mask_kind(kind, m.group("v"))


def _sub_phone(m: "re.Match") -> str:
    if m.group("intl") is not None or m.group("grp") is not None:
        txt = m.group(0)
        if len(re.sub(r"\D", "", txt)) >= 9:
            return mask_phone(txt)
    return m.group(0)


def _sub_card(m: "re.Match") -> str:
    digits = re.sub(r"\D", "", m.group(0))
    if 13 <= len(digits) <= 19 and digits[0] in "23456" and _luhn_ok(digits) \
            and (len(set(digits)) > 1):
        return "[CARD]"
    return m.group(0)


def _sub_iban(m: "re.Match") -> str:
    return "[IBAN]" if _iban_ok(m.group(0)) else m.group(0)


def mask_text(s: str) -> str:
    """Regex-mask PII inside free text / JSON / XML (no truncation)."""
    if not s:
        return s
    s = _JSON_PAIR.sub(_sub_json, s)
    s = _XML_PAIR.sub(_sub_xml, s)
    if "=" in s or ":" in s:
        s = _KV_PAIR.sub(_sub_kv, s)
    s = _JWT.sub("[TOKEN]", s)
    s = _BEARER.sub(lambda m: m.group(1) + " [TOKEN]", s)
    s = _EMAIL.sub(lambda m: mask_email(m.group(0)), s)
    s = _IBAN.sub(_sub_iban, s)
    s = _CARD.sub(_sub_card, s)
    s = _PHONE_ALT.sub(_sub_phone, s)
    return s


def truncate(s: str, limit: int) -> str:
    if limit and len(s) > limit:
        return s[:limit] + "...[truncated %d chars]" % (len(s) - limit)
    return s


# ----------------------------------------------------------- row masking

class MaskReport:
    def __init__(self):
        self.column_kinds: Dict[str, str] = {}
        self.truncated_cells = 0
        self.masked_cells = 0

    def to_dict(self) -> dict:
        return {"column_kinds": dict(self.column_kinds),
                "truncated_cells": self.truncated_cells,
                "masked_cells": self.masked_cells}


def _limit_for(col_low: str, kind: Optional[str], body_limit: int, text_limit: int) -> int:
    if kind == "free_text_body" or any(h in col_low for h in _BODY_HINTS):
        return body_limit
    if any(h in col_low for h in _ERROR_HINTS):
        return max(body_limit, ERROR_TEXT_LIMIT)
    return text_limit


def mask_value(value: Any, kind: Optional[str], declared: bool, col_low: str,
               body_limit: int, text_limit: int, report: Optional[MaskReport] = None) -> Any:
    """Return a JSON-serialisable, masked copy of one cell."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<binary %d bytes>" % len(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        out: Any = str(value)
    elif isinstance(value, (int, float)):
        out = value
    else:
        out = str(value)
    if kind in ("email", "phone", "contact", "name", "address", "identity", "iban_bank", "secret") \
            and (declared or isinstance(out, str)):
        if report:
            report.masked_cells += 1
        return _mask_kind(kind, str(out))
    if isinstance(out, str):
        masked = mask_text(out)
        if masked != out and report:
            report.masked_cells += 1
        lim = _limit_for(col_low, kind, body_limit, text_limit)
        t = truncate(masked, lim)
        if t != masked and report:
            report.truncated_cells += 1
        return t
    return out


def mask_rows(columns: Sequence[str], rows: Iterable[Sequence[Any]], *,
              pii: Optional[Mapping[str, str]] = None,
              body_limit: int = DEFAULT_BODY_LIMIT,
              text_limit: int = DEFAULT_TEXT_LIMIT) -> Tuple[List[List[Any]], MaskReport]:
    """Mask every cell.  `pii` maps result column name (case-insensitive) to a
    kind: email, phone, name, address, identity, iban_bank, free_text_body."""
    declared = {k.lower(): v for k, v in (pii or {}).items()}
    report = MaskReport()
    plan = []
    for c in columns:
        low = (c or "").lower()
        if low in declared:
            kind, dec = declared[low], True
        else:
            kind, dec = classify_key(low), False
        if kind:
            report.column_kinds[c] = kind + ("" if dec else " (heuristic)")
        plan.append((kind, dec, re.sub(r"[^a-z0-9]", "", low)))
    out = []
    for row in rows:
        out.append([mask_value(v, plan[i][0], plan[i][1], plan[i][2], body_limit, text_limit, report)
                    for i, v in enumerate(row)])
    return out, report
