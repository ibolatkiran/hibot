"""Schema catalog loader (knowledge/schema/*.json) used by the SQL validator,
the masker and the template validator.

The catalog is structure-only (no row data).  It is loaded lazily and reduced
to a compact in-memory form.  `SchemaCatalog.from_dict` lets tests build tiny
synthetic catalogs without touching the 5 MB objects.json.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

REGIONS = ("EU", "APAC")

# --- sensitivity rules -------------------------------------------------------

# Words (camelCase / snake_case split) that mark a credential-like column.
SECRET_WORDS = frozenset({
    "password", "passwd", "pwd", "secret", "secrets", "token", "apikey",
    "hash", "hashkey", "salt", "credential", "credentials", "privatekey",
    "connectionstring",
})
# Substrings (lower-cased whole name) that are always secret.
SECRET_SUBSTRINGS = ("password", "passwd", "apikey", "api_key", "secret",
                     "privatekey", "private_key", "accesskey", "access_key")
# Table-name words that make the whole table off limits.
SECRET_TABLE_WORDS = frozenset({"password", "passwd", "secret", "secrets",
                                "credential", "credentials", "apikey"})

# PII kinds (from domains.json) that are masked in results but may be used
# by *templates*; ad-hoc SQL may not touch them at all.
FREE_TEXT_KIND = "free_text_body"

# Columns the heuristic catalog did not flag but which hold personal data or
# free text in practice (table -> {column: kind}).
SUPPLEMENTAL_PII: Dict[str, Dict[str, str]] = {
    "Catalog.Employee": {"Dsc": "name"},
    "Operation.WorkOrder": {"CallCenterNote": FREE_TEXT_KIND,
                            "RejectDescription": FREE_TEXT_KIND,
                            "StatusNote": FREE_TEXT_KIND},
    "Operation.WorkOrderGpsInfo": {"Comment": FREE_TEXT_KIND},
}

_WORD_SPLIT = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def split_words(name: str) -> List[str]:
    """Split an identifier into lower-case words (CamelCase, snake_case)."""
    return [w.lower() for w in _WORD_SPLIT.findall(name)]


def is_secret_name(name: str) -> bool:
    low = name.lower()
    if any(s in low for s in SECRET_SUBSTRINGS):
        return True
    return any(w in SECRET_WORDS for w in split_words(name))


def is_secret_table(full_name: str) -> bool:
    name = full_name.split(".")[-1]
    low = name.lower()
    if any(s in low for s in ("password", "secret", "credential", "apikey")):
        return True
    return any(w in SECRET_TABLE_WORDS for w in split_words(name))


@dataclass
class ObjectInfo:
    name: str                       # canonical "Schema.Name"
    type: str                       # table/view/function/procedure/...
    regions: Tuple[str, ...]
    columns: Tuple[str, ...] = ()
    eu_only_columns: frozenset = frozenset()
    apac_only_columns: frozenset = frozenset()
    has_tenant_id: bool = False
    primary_key: Tuple[str, ...] = ()
    parameters: Tuple[str, ...] = ()
    called_procs: Tuple[str, ...] = ()
    written_tables: Tuple[str, ...] = ()
    is_backup: bool = False
    pii: Dict[str, str] = field(default_factory=dict)   # lower col -> kind

    def columns_for(self, region: Optional[str]) -> List[str]:
        out = []
        for c in self.columns:
            if region == "APAC" and c in self.eu_only_columns:
                continue
            if region == "EU" and c in self.apac_only_columns:
                continue
            out.append(c)
        return out

    def in_region(self, region: Optional[str]) -> bool:
        return region is None or region in self.regions


class SchemaCatalog:
    def __init__(self, objects: Dict[str, ObjectInfo]):
        self._objects = objects
        self._by_lower = {k.lower(): v for k, v in objects.items()}
        self.schemas: Set[str] = {k.split(".")[0].lower() for k in objects}

    # -- construction -----------------------------------------------------
    @classmethod
    def from_dict(cls, objects: dict, domains: Optional[dict] = None) -> "SchemaCatalog":
        """Build from objects.json-shaped data (+ optional domains.json data)."""
        dom_tables = (domains or {}).get("tables", {})
        out: Dict[str, ObjectInfo] = {}
        for key, r in objects.items():
            if key.count(".") != 1:
                continue
            cols = tuple(c["name"] for c in r.get("columns", []))
            pii: Dict[str, str] = {}
            for c in dom_tables.get(key, {}).get("pii_columns", []):
                pii[c["name"].lower()] = c["kind"]
            for c, kind in SUPPLEMENTAL_PII.get(key, {}).items():
                pii.setdefault(c.lower(), kind)
            out[key] = ObjectInfo(
                name=key,
                type=r["type"],
                regions=tuple(r.get("regions", ())),
                columns=cols,
                eu_only_columns=frozenset(r.get("eu_only_columns") or ()),
                apac_only_columns=frozenset(r.get("apac_only_columns") or ()),
                has_tenant_id=bool(r.get("has_TenantId")),
                primary_key=tuple(r.get("primary_key") or ()),
                parameters=tuple(p["name"] for p in r.get("parameters", []) or []),
                called_procs=tuple(r.get("called_procs") or ()),
                written_tables=tuple(r.get("written_tables") or ()),
                is_backup=bool(r.get("is_backup_or_copy")),
                pii=pii,
            )
        return cls(out)

    @classmethod
    def load(cls, schema_dir: Optional[os.PathLike] = None) -> "SchemaCatalog":
        d = Path(schema_dir) if schema_dir else default_schema_dir()
        with open(d / "objects.json", encoding="utf-8") as f:
            objects = json.load(f)
        dom_path = d / "domains.json"
        domains = None
        if dom_path.exists():
            with open(dom_path, encoding="utf-8") as f:
                domains = json.load(f)
        return cls.from_dict(objects, domains)

    # -- queries ----------------------------------------------------------
    def get(self, name: str) -> Optional[ObjectInfo]:
        return self._by_lower.get(name.lower())

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._by_lower

    def __len__(self) -> int:
        return len(self._objects)

    def objects(self) -> Iterable[ObjectInfo]:
        return self._objects.values()

    def has_schema(self, schema: str) -> bool:
        return schema.lower() in self.schemas


def default_schema_dir() -> Path:
    env = os.environ.get("HIBOT_SCHEMA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "knowledge" / "schema"


_CACHE: Optional[SchemaCatalog] = None
_LOCK = threading.Lock()


def get_catalog() -> SchemaCatalog:
    """Process-wide lazily loaded default catalog."""
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            _CACHE = SchemaCatalog.load()
        return _CACHE
