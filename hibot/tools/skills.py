"""Skills library access (knowledge/skills/*.md): the `read_skill` tool and helpers shared by every brain
(Claude native tool calling, the ArGPT v2 loop, MCP)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from hibot.agent import config

from ._common import err, finish

NAME_RE = re.compile(r"^[a-z0-9_]{2,60}$")


def skills_dir() -> Path:
    return config.knowledge_dir() / "skills"


def skill_names() -> List[str]:
    d = skills_dir()
    return sorted(p.stem for p in d.glob("*.md") if p.stem != "README") if d.is_dir() else []


def skills_index() -> str:
    """The symptom -> skill table of knowledge/skills/README.md (everything after 'Always start with')."""
    f = skills_dir() / "README.md"
    if not f.is_file():
        return "(no skills library)"
    return f.read_text(encoding="utf-8").split("## Always start with", 1)[-1].strip()


def protocol_text() -> str:
    f = skills_dir() / "investigation_protocol.md"
    return f.read_text(encoding="utf-8") if f.is_file() else ""


def read_skill(name: str) -> dict:
    """One investigation method from the skills library (a symptom specific recipe: which tools, in which order)."""
    n = str(name or "").strip().lower()
    if n.endswith(".md"):
        n = n[:-3]
    names = skill_names()
    if not NAME_RE.match(n) or n not in names:
        return err("unknown skill %r" % str(name)[:60], available=names)
    return finish({"ok": True, "name": n, "text": (skills_dir() / (n + ".md")).read_text(encoding="utf-8")}, 16000)
