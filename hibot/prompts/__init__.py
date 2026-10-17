"""Prompt files (*.md) shipped with the agent."""
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def load(name: str) -> str:
    return (_DIR / (name + ".md")).read_text(encoding="utf-8")


def system_prompt() -> str:
    return load("system").rstrip() + "\n\n" + load("actions").rstrip() + "\n"


def classification_rules() -> str:
    """Two-axis classification rules + known-card rule: ONE text shared by the ArGPT v2 loop and the Claude brain."""
    return load("classification").rstrip()


def v2_system_prompt() -> str:
    """System message of the unified loop (hibot/agent/loop_v2.py): role, protocol, skills index, tool catalog, classification rules, output protocol."""
    from hibot.tools import skills
    from hibot.tools.registry import tool_catalog
    t = load("v2_system")
    for k, v in (("@@PROTOCOL@@", skills.protocol_text().strip()), ("@@SKILLS@@", skills.skills_index()),
                 ("@@TOOLS@@", tool_catalog()), ("@@CLASSIFICATION@@", classification_rules())):
        t = t.replace(k, v)
    return t.rstrip() + chr(10)
