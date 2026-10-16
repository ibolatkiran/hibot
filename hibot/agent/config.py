"""Paths, budgets and safety constants of the agent.  Everything overridable by env for tests."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]


def knowledge_dir() -> Path:
    return Path(os.environ.get("HIBOT_KNOWLEDGE_DIR") or ROOT / "knowledge")


def cards_dir() -> Path:
    return Path(os.environ.get("HIBOT_CARDS_DIR") or knowledge_dir() / "cards")


def feedback_dir() -> Path:
    return Path(os.environ.get("HIBOT_FEEDBACK_DIR") or knowledge_dir() / "feedback")


def runs_dir() -> Path:
    return Path(os.environ.get("HIBOT_RUNS_DIR") or ROOT / "runs")


# Repositories the agent may read source from (CLAUDE.md "Ilgili repolar").  HIBOT_REPO_ROOTS (os.pathsep list) overrides.
DEFAULT_REPO_ROOTS = [
    r"d:\repos\SiriusHeadOffice", r"d:\repos\SiriusCallCenter", r"d:\repos\SiriusServiceShopCallCenter",
    r"d:\repos\SiriusTechnician", r"d:\repos\SiriusApi",
    r"d:\repos\sirius-eu\gfsm-europe", r"d:\repos\gfsm-apac\ahsiriusdbsrv.database.windows.net",
]


def repo_roots() -> List[Path]:
    env = os.environ.get("HIBOT_REPO_ROOTS")
    raw = env.split(os.pathsep) if env else DEFAULT_REPO_ROOTS
    return [Path(r) for r in raw if r.strip()]


# Secret deny-list (CLAUDE.md): file names that are never read, indexed or sent to the LLM.
SECRET_NAME_PATTERNS = [
    r"^appsettings.*\.json$", r"^\.env.*$", r"^\.env-cmdrc\.json$", r"^translate\.js$", r"^web\.config$",
    r".*\.pfx$", r".*\.pem$", r".*\.key$", r".*\.p12$", r".*\.snk$", r"^secrets?\.json$", r"^id_rsa.*$",
    r".*\.publishsettings$", r".*\.user$",
]
SECRET_NAME_RE = re.compile("|".join("(?:%s)" % p for p in SECRET_NAME_PATTERNS), re.I)
DENIED_DIRS = {".git", "node_modules", ".vs", "bin", "obj"}
READABLE_SUFFIXES = {".cs", ".js", ".jsx", ".ts", ".tsx", ".sql", ".json", ".md", ".css", ".scss", ".html",
                     ".sqlproj", ".csproj", ".cshtml", ".yml", ".yaml", ".txt", ".xml", ".proj"}

REGIONS = ("EU", "APAC")
APAC_COUNTRIES = {"CN", "TW", "TH", "VN", "HK", "MY", "SG", "ID", "AU", "NZ", "KR", "JP", "PH", "IN"}


@dataclass
class Budget:
    max_steps: int = 8                # LLM-chosen actions (excluding the forced final report)
    max_queries: int = 10             # preflight + baseline playbook + LLM queries (run_query + run_sql reaching the DB layer)
    max_adhoc_sql: int = 2
    max_context_chars: int = 60000    # whole prompt; older observations are shortened beyond this
    max_obs_chars: int = 4000         # one observation
    max_rows: int = 15                # rows shown to the LLM per query
    max_cell_chars: int = 160
    max_source_lines: int = 120
    max_llm_retries: int = 2          # JSON repair retries per LLM call
