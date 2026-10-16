"""Load secrets from the git-ignored HiBot/.env file into os.environ.

Format: KEY=VALUE per line, '#' comments, optional surrounding quotes. Variables
already set in the real environment win. Values are never printed or logged.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Optional[Path] = None) -> List[str]:
    """Return the NAMES (never values) of the variables that were loaded."""
    p = Path(path) if path else ROOT / ".env"
    if not p.is_file():
        return []
    loaded = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
