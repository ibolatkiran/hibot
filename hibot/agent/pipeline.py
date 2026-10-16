"""End-to-end analysis: intake -> cards -> code context -> investigation -> report -> run folder."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Callable, Optional, Union

from hibot.db.templates import TemplateRegistry
from hibot.llm.client import LLMClient

from . import cards as cardmod
from . import config, intake, store
from .code_context import CodeContext
from .executor import DRY, Executor
from .investigate import Investigation
from .report import render_markdown

LLMSource = Union[LLMClient, Callable[[dict, list, str], LLMClient]]


def analyze(key: Optional[str] = None, text: Optional[str] = None, *, llm: LLMSource, mode: str = DRY,
            region: Optional[str] = None, budget: Optional[config.Budget] = None, demo: bool = False,
            code: Optional[CodeContext] = None, registry: Optional[TemplateRegistry] = None,
            runner_factory: Optional[Callable[[str], Any]] = None, runs_base: Optional[Path] = None,
            on_step: Optional[Callable[[dict], None]] = None, incident: Optional[dict] = None) -> dict:
    """Run one analysis, save it under runs/<run_id>/ and return the run dict (with `run_dir`, `markdown`)."""
    budget = budget or config.Budget()
    inc = incident or intake.build_incident(key, text)
    region = (region or inc.get("region_guess") or "EU").upper()
    matches = cardmod.match_cards(inc, cardmod.load_cards())
    code = code or CodeContext()
    ctx = code.auto_context(inc)
    registry = registry or TemplateRegistry.load()
    client = llm(inc, matches, region) if callable(llm) and not isinstance(llm, LLMClient) else llm
    ex = Executor(registry=registry, code=code, mode=mode, region=region, budget=budget, runner_factory=runner_factory)
    try:
        out = Investigation(client, inc, matches, ctx, ex, budget=budget, on_step=on_step).run()
    finally:
        ex.close()
    run_id = store.new_run_id(inc.get("key"))
    run = {
        "run_id": run_id, "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "mode": mode, "region": region, "llm": ("fake-demo" if demo else getattr(client, "name", "llm")), "demo": demo,
        "budget": vars(budget), "input": {"key": key, "text": text}, "incident": inc, "matches": matches,
        "report": out["report"], "steps": out["steps"], "queries": out["queries"], "llm_calls": out["llm_calls"],
        "forced_final": out["forced_final"], "warnings": out["warnings"], "code_backend": code.backend,
    }
    md = render_markdown(run)
    d = store.save_run(run, md, runs_base)
    run["run_dir"], run["markdown"] = str(d), md
    return run
