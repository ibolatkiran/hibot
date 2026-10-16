"""Shared helpers for the incident tools (stdlib only)."""
import hashlib
import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATASET = os.path.join(ROOT, "docs", "kronik_sorunlar_son_1_yil_data.json")
CODEMAP = os.path.join(ROOT, "knowledge", "codemap")
JIRA_RAW = os.path.join(ROOT, "docs", "jira_export", "raw")
OUT_DIR = os.path.join(ROOT, "knowledge", "incidents")

_TR_MAP = str.maketrans({"ı": "i", "İ": "i", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u",
                         "ş": "s", "Ş": "s", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})


def fold(s):
    """Lowercase + Turkish char folding + strip other diacritics (e.g. Romanian/Polish accents)."""
    s = (s or "").translate(_TR_MAP).lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def words(s):
    return _WORD.findall(fold(s))


def pseudonym(name, prefix="U"):
    """Deterministic pseudonymous id for a person name (not reversible without the name list)."""
    n = fold(name).strip()
    if not n:
        return None
    return prefix + hashlib.sha1(("hibot-incidents|" + n).encode("utf-8")).hexdigest()[:8]


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
