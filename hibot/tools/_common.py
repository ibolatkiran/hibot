"""Shared helpers of the tool layer: output size caps, scrubbing, repo lookup."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List, Optional

from hibot.agent import config

MAX_BYTES = 8192            # default cap of one tool result (serialized JSON)


class ToolError(ValueError):
    """Bad input; the message is safe to show to the LLM / developer."""


def err(msg: Any, **extra: Any) -> dict:
    d = {"ok": False, "error": str(msg)}
    d.update(extra)
    return d


def scrub(text: str) -> str:
    """Credential assignments + PII regex masking (same chain as hibot.agent.safety.read_source)."""
    from hibot.agent import safety
    return safety.scrub_source(text or "")


def _size(o: Any) -> int:
    return len(json.dumps(o, ensure_ascii=False, default=str).encode("utf-8"))


def _lists(o: Any, found: list) -> None:
    if isinstance(o, dict):
        for v in o.values():
            _lists(v, found)
    elif isinstance(o, list):
        if o:
            found.append(o)
        for v in o:
            _lists(v, found)


def _longest_string(o: Any):
    best = None
    items = o.items() if isinstance(o, dict) else enumerate(o) if isinstance(o, list) else ()
    for k, v in items:
        if isinstance(v, str):
            if best is None or len(v) > len(best[0][best[1]]):
                best = (o, k)
        else:
            sub = _longest_string(v)
            if sub is not None and (best is None or len(sub[0][sub[1]]) > len(best[0][best[1]])):
                best = sub
    return best


def finish(result: dict, limit: int = MAX_BYTES) -> dict:
    """Make `result` fit `limit` serialized bytes: drop trailing list items (largest list first), then cut the
    longest string.  Sets result['truncated']=True when anything was removed."""
    if _size(result) <= limit:
        result.setdefault("truncated", False)
        return result
    result["truncated"] = True
    for _ in range(400):
        if _size(result) <= limit:
            return result
        lists: List[list] = []
        _lists(result, lists)
        if not lists:
            break
        big = max(lists, key=_size)
        if len(big) > 1:
            del big[max(1, len(big) * 2 // 3):]
        else:
            big.clear()
    for _ in range(100):
        if _size(result) <= limit:
            break
        best = _longest_string(result)
        if best is None or len(best[0][best[1]]) < 40:
            break
        o, k = best
        o[k] = o[k][:max(20, len(o[k]) // 2)] + "...[cut]"
    return result


def existing_roots() -> List[Path]:
    return [r for r in config.repo_roots() if r.is_dir()]


def select_roots(repos: Optional[Any]) -> List[Path]:
    """repos: None/[] = all allowed repos; names match the folder name exactly or as a substring (case-insensitive)."""
    roots = existing_roots()
    if not repos:
        return roots
    if isinstance(repos, str):
        repos = [r for r in re.split(r"[,\s;]+", repos) if r]
    out: List[Path] = []
    for want in repos:
        w = str(want).strip().casefold()
        hit = [r for r in roots if r.name.casefold() == w] or [r for r in roots if w and w in r.name.casefold()]
        if not hit:
            raise ToolError("unknown repo %r (allowed: %s)" % (want, ", ".join(r.name for r in roots)))
        for r in hit:
            if r not in out:
                out.append(r)
    return out


def to_int(v: Any, name: str, lo: int, hi: int, default: Optional[int] = None) -> int:
    if v is None or v == "":
        if default is None:
            raise ToolError("%s is required" % name)
        return default
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ToolError("%s must be an integer" % name)
    return max(lo, min(hi, n))
