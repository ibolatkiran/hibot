"""Column provenance (round 2): "why is value X zero / wrong / missing?".

When the ticket is about a stored value (cost, price, labour, total, a date ...) the right first question is WHO computes that column.
This module is deterministic (no LLM, no DB):

  1. pick the column the ticket talks about (CamelCase tokens of a column of the watched tables all appear in the ticket text, after a
     small synonym normalisation: labor->labour, charge/fee/amount->cost ...);
  2. find the procedures that WRITE that column (`knowledge/schema/objects.json` `written_by` of the table) and grep their sqlproj source for
     an assignment `<Column> =` (UPDATE ... SET Column = / DECLARE @Column = ( SELECT ... ));
  3. print a compact window of the best computing procedure(s) (declaration + assignment, file:line), the INPUT tables and filters it reads
     (e.g. Operation.WorkOrderDetail + Catalog.Material.Type = 1) and the query templates that touch those inputs.

The result goes to the case file as "COLUMN PROVENANCE".  The LLM then queries the inputs instead of guessing "config or code".
Entry point: `analyze(inc, registry)` -> dict with `text`, `columns`, `writers`, `input_tables`, `templates`, `source_paths`.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config, safety

WATCH_TABLES = ("Operation.WorkOrder", "Operation.WorkOrderDetail", "Operation.WorkCase")
SYNONYMS = {"labor": "labour", "labours": "labour", "labors": "labour", "charge": "cost", "charges": "cost", "fee": "cost", "fees": "cost",
            "costs": "cost", "amount": "total", "price": "cost", "işçilik": "labour", "iscilik": "labour", "maliyet": "cost", "tutar": "total",
            "ücret": "cost", "ucret": "cost", "toplam": "total", "dates": "date", "tarihi": "date", "tarih": "date", "parts": "material",
            "part": "material", "spare": "material", "transport": "transportaion", "transportation": "transportaion"}
PROBLEM_RE = re.compile(
    r"\b0\b|zero|empty|blank|missing|wrong|incorrect|not (?:showing|shown|display|displaying|calculated|updated|visible|appear)|no [\w ]{0,20}"
    r"(?:showing|shown|displayed|visible)|doesn'?t (?:show|appear)|isn'?t (?:showing|shown)|yanlış|yanlis|eksik|görünm|gorunm|gözükm|gozukm|boş|bos|sıfır|sifir|"
    r"hesaplanm|differ|mismatch|does not match", re.I)
MAX_FILE_BYTES = 400_000
MAX_WINDOW_LINES = 26


def _norm_tokens(text: str) -> set:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    out = set()
    for t in re.findall(r"[A-Za-zÀ-ÿığüşöçİĞÜŞÖÇ0-9]+", parts):
        t = t.lower()
        out.add(SYNONYMS.get(t, t))
    return out


def ticket_text(inc: dict) -> str:
    return "%s\n%s" % (inc.get("summary") or "", inc.get("desc") or "")


def is_value_problem(inc: dict) -> bool:
    return bool(PROBLEM_RE.search(ticket_text(inc)))


@lru_cache(maxsize=1)
def _objects() -> Dict[str, dict]:
    p = config.knowledge_dir() / "schema" / "objects.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def candidate_columns(inc: dict, objects: Optional[Dict[str, dict]] = None, limit: int = 2) -> List[Tuple[str, str]]:
    """[(table, column)] whose (>= 2) CamelCase tokens ALL occur in the ticket text, best (most tokens) first."""
    objects = objects if objects is not None else _objects()
    words = _norm_tokens(ticket_text(inc))
    scored: List[Tuple[int, str, str]] = []
    for tname in WATCH_TABLES:
        t = objects.get(tname)
        if not t:
            continue
        for c in t.get("columns") or []:
            col = c["name"] if isinstance(c, dict) else str(c)
            toks = _norm_tokens(col)
            if len(toks) >= 2 and toks <= words:
                scored.append((len(toks), tname, col))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    return [(t, c) for _, t, c in scored[:limit]]


def _source_text(obj_name: str, objects: Dict[str, dict]) -> Optional[Tuple[str, List[str]]]:
    src = (objects.get(obj_name) or {}).get("source_files") or {}
    path = src.get("EU") or next(iter(src.values()), None)
    if not path:
        return None
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            return None
        return str(p), p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def _score_writer(name: str, assign_lines: List[int], text: str) -> int:
    s = 2 if assign_lines else 0
    if re.search(r"pric|calc|total|cost|sum|recalc|amount", name, re.I):
        s += 3
    if re.search(r"\bSUM\s*\(", text, re.I):
        s += 1
    if re.search(r"ROMANIA|_RP_|^RP_|^PR_|test|backup|bak|old|copy|_\d{6,}", name, re.I):
        s -= 3
    return s


def find_writers(table: str, column: str, objects: Optional[Dict[str, dict]] = None, limit: int = 3) -> List[dict]:
    objects = objects if objects is not None else _objects()
    rx_assign = re.compile(r"(?<![\w.@])\[?%s\]?\s*=" % re.escape(column), re.I)
    rx_var = re.compile(r"@%s\b" % re.escape(column), re.I)
    out = []
    for w in (objects.get(table) or {}).get("written_by") or []:
        o = objects.get(w) or {}
        if o.get("is_backup_or_copy"):
            continue
        got = _source_text(w, objects)
        if not got:
            continue
        path, lines = got
        assign = [i for i, ln in enumerate(lines, 1) if rx_assign.search(ln) and not ln.lstrip().startswith("--")]
        var = [i for i, ln in enumerate(lines, 1) if rx_var.search(ln) and not ln.lstrip().startswith("--")]
        if not assign and not var:
            continue
        out.append({"name": w, "path": path, "lines": lines, "assign": assign, "var": var,
                    "score": _score_writer(w, assign, "\n".join(lines))})
    out.sort(key=lambda x: (-x["score"], x["name"]))
    return out[:limit]


def _window(w: dict, column: str) -> Tuple[List[Tuple[int, str]], str]:
    """Compact lines: every assignment line +-1 and the declaration of @Column with the next few lines (the SELECT that computes it)."""
    lines = w["lines"]
    keep = set()
    for i in w["assign"][:4]:
        keep.update(range(max(1, i - 1), min(len(lines), i + 1) + 1))
    decl = [i for i in w["var"] if re.search(r"\bdeclare\b", lines[i - 1], re.I) or re.search(r"@%s\s*=" % re.escape(column), lines[i - 1], re.I)]
    for i in decl[:2]:
        end = i
        depth = 0
        for j in range(i, min(len(lines), i + 8) + 1):
            depth += lines[j - 1].count("(") - lines[j - 1].count(")")
            end = j
            if depth <= 0 and j > i:
                break
        keep.update(range(i, end + 1))
    sel = sorted(keep)[:MAX_WINDOW_LINES]
    text = "\n".join(lines[n - 1] for n in sel)
    return [(n, lines[n - 1]) for n in sel], text


def input_tables(window_text: str) -> List[str]:
    seen: List[str] = []
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+\[?(\w+)\]?\.\[?(\w+)\]?", window_text, re.I):
        t = "%s.%s" % (m.group(1), m.group(2))
        if t.lower() not in [x.lower() for x in seen]:
            seen.append(t)
    return seen


def templates_for(tables: List[str], registry: Any, limit: int = 4) -> List[str]:
    if registry is None or not tables:
        return []
    want = {t.lower() for t in tables}
    scored = []
    for t in registry:
        have = {x.lower() for x in (t.tables or [])}
        n = len(want & have)
        if n:
            scored.append((n, t.id))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in scored[:limit]]


def analyze(inc: dict, registry: Any = None, objects: Optional[Dict[str, dict]] = None) -> Dict[str, Any]:
    """Provenance for the column the ticket talks about; empty `text` when the ticket is not a value problem or nothing was found."""
    res: Dict[str, Any] = {"columns": [], "writers": [], "input_tables": [], "templates": [], "source_paths": [], "text": ""}
    if not is_value_problem(inc):
        return res
    objects = objects if objects is not None else _objects()
    cols = candidate_columns(inc, objects)
    if not cols:
        return res
    L: List[str] = ["## COLUMN PROVENANCE (deterministic: who computes the column the ticket is about)"]
    all_inputs: List[str] = []
    for table, col in cols[:1]:                               # the best column only: keep the section small
        writers = find_writers(table, col, objects)
        res["columns"].append("%s.%s" % (table, col))
        L.append("Column %s.%s is written by %d procedure(s) with a source assignment; best candidates:" % (
            table, col, len(writers)))
        for w in writers[:3]:
            if len(res["writers"]) >= 2:
                break
            lines, wtext = _window(w, col)
            if not lines:
                continue
            res["writers"].append(w["name"])
            res["source_paths"].append(w["path"])
            ins = input_tables(wtext)
            for t in ins:
                if t.lower() != table.lower() and t not in all_inputs:
                    all_inputs.append(t)
            L.append("- %s (%s; read_source path `%s`)" % (w["name"], w["path"], w["name"]))
            for n, t in lines:
                L.append("  %d| %s" % (n, t.strip()[:150]))
            if ins:
                L.append("  inputs read here: " + ", ".join(ins))
    res["input_tables"] = all_inputs
    res["templates"] = templates_for(all_inputs, registry)
    if all_inputs:
        L.append("-> the column is only as good as its inputs: query the input tables/filters above for the work order(s) in the ticket"
                 + (" (templates touching them: %s)" % ", ".join(res["templates"]) if res["templates"] else "")
                 + ". A value of 0 usually means the input rows (e.g. detail lines of the right Material.Type) do not exist - find out WHY they do not "
                   "(master data that offers them, user did not add them) before blaming the calculation.")
    text = "\n".join(L)
    res["text"] = safety.scrub_source(text) if len(L) > 1 and res["writers"] else ""
    return res
