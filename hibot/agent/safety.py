"""Guarded source reading for the `read_source` action.

Rules (CLAUDE.md "Guvenlik kurallari"):
  * only inside the allowed repositories (config.repo_roots()), symlinks resolved before the check
  * never `..` segments, never denied directories (.git, node_modules, bin, obj ...)
  * never secret files: appsettings*.json, .env*, .env-cmdrc.json, translate.js, web.config, *.pfx (+ keys/certs)
  * only known text suffixes, size cap, line cap
  * output is passed through hibot.db.masking.mask_text and a credential-assignment scrubber
`path` may be absolute, "<RepoFolder>/<relative>" (e.g. "SiriusApi/Sirius/Controllers/TechnicianController.cs"),
or a schema object name like "dbo.GetSiriusWorkOrderHistory" (resolved through knowledge/schema/objects.json).
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from . import config

MAX_FILE_BYTES = 3_000_000
_OBJ_NAME = re.compile(r"^[A-Za-z_][\w]*\.[A-Za-z_][\w]*$")
_CRED = re.compile(r"(?i)\b(password|pwd|secret|api[_-]?key|access[_-]?key|client[_-]?secret|sas[_-]?token|"
                   r"connection[_-]?string|token)\b(\s*[\"']?\s*[:=]\s*)(\"[^\"\r\n]{3,}\"|'[^'\r\n]{3,}'|[^\s;,\"']{3,})")


class SourceError(ValueError):
    pass


@lru_cache(maxsize=1)
def _objects_sources() -> Dict[str, Dict[str, str]]:
    p = config.knowledge_dir() / "schema" / "objects.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k.lower(): v.get("source_files") or {} for k, v in data.items()}


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.realpath(str(p)))


def is_secret_name(name: str) -> bool:
    return bool(config.SECRET_NAME_RE.match(name))


def resolve(path: str) -> Path:
    if not isinstance(path, str) or not path.strip() or "\x00" in path or len(path) > 500:
        raise SourceError("path must be a non-empty string")
    raw = path.strip()
    roots = config.repo_roots()
    if _OBJ_NAME.match(raw) and not os.path.isabs(raw):
        src = _objects_sources().get(raw.lower())
        if not src:
            raise SourceError("unknown schema object %r" % raw)
        raw = src.get("EU") or next(iter(src.values()))
    parts = re.split(r"[\\/]+", raw)
    if ".." in parts:
        raise SourceError("path traversal ('..') is not allowed")
    if os.path.isabs(raw) or re.match(r"^[A-Za-z]:", raw):
        cand = Path(raw)
    else:
        first = parts[0].casefold()
        root = next((r for r in roots if r.name.casefold() == first), None)
        if root is None:
            raise SourceError("path must be absolute or start with a repo folder name (%s)"
                              % ", ".join(r.name for r in roots))
        cand = root.joinpath(*parts[1:])
    real = _norm(cand)
    for r in roots:
        rr = _norm(r)
        try:
            inside = os.path.commonpath([real, rr]) == rr
        except ValueError:                      # different drives
            inside = False
        if inside:
            rel_parts = Path(real).relative_to(Path(rr)).parts if real != rr else ()
            break
    else:
        raise SourceError("path is outside the allowed repositories")
    if any(p.casefold() in config.DENIED_DIRS for p in rel_parts[:-1]):
        raise SourceError("directory is not readable (denied: %s)" % ", ".join(sorted(config.DENIED_DIRS)))
    name = Path(real).name
    if is_secret_name(name):
        raise SourceError("secret file (%s) is never read" % name)
    if Path(real).suffix.lower() not in config.READABLE_SUFFIXES:
        raise SourceError("file type %r is not readable" % Path(real).suffix)
    if not os.path.isfile(real):
        raise SourceError("file not found")
    if os.path.getsize(real) > MAX_FILE_BYTES:
        raise SourceError("file too large")
    return Path(real)


def scrub_source(text: str) -> str:
    from hibot.db import masking
    text = _CRED.sub(lambda m: m.group(1) + m.group(2) + "***", text)
    return masking.mask_text(text)


def read_source(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None,
                max_lines: int = 120) -> dict:
    """Return {ok, path, start_line, end_line, total_lines, text, truncated} or {ok False, error}."""
    try:
        real = resolve(path)
    except SourceError as e:
        return {"ok": False, "error": str(e)}
    try:
        start = int(start_line or 1)
        end = int(end_line) if end_line else start + max_lines - 1
    except (TypeError, ValueError):
        return {"ok": False, "error": "start_line/end_line must be integers"}
    if start < 1 or end < start:
        return {"ok": False, "error": "invalid line range"}
    truncated = end - start + 1 > max_lines
    end = min(end, start + max_lines - 1)
    with open(real, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    chunk = lines[start - 1:end]
    body = "\n".join("%d| %s" % (start + i, ln[:240]) for i, ln in enumerate(chunk))
    return {"ok": True, "path": str(real), "start_line": start, "end_line": start + len(chunk) - 1,
            "total_lines": len(lines), "text": scrub_source(body), "truncated": truncated}
