"""Prompt files (*.md) shipped with the agent."""
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def load(name: str) -> str:
    return (_DIR / (name + ".md")).read_text(encoding="utf-8")


def system_prompt() -> str:
    return load("system").rstrip() + "\n\n" + load("actions").rstrip() + "\n"
