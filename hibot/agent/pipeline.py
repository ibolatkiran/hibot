"""End-to-end analysis: intake -> cards -> code context -> investigation -> report -> run folder."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Callable, Optional, Union

from hibot.db.templates import TemplateRegistry
from hibot.llm.client import LLMClient

from . import calibration
from . import cards as cardmod
from . import config, intake, known_issue, playbooks, preflight, provenance, store, ui_gating
from .code_context import CodeContext
from .executor import DRY, Executor
from .investigate import Investigation
from .report import render_markdown

LLMSource = Union[LLMClient, Callable[[dict, list, str], LLMClient]]


def analyze(key: Optional[str] = None, text: Optional[str] = None, *, llm: LLMSource, mode: str = DRY,
            region: Optional[str] = None, budget: Optional[config.Budget] = None, demo: bool = False,
            code: Optional[CodeContext] = None, registry: Optional[TemplateRegistry] = None,
            runner_factory: Optional[Callable[[str], Any]] = None, runs_base: Optional[Path] = None,
            on_step: Optional[Callable[[dict], None]] = None, incident: Optional[dict] = None, loop: str = "v1") -> dict:
    """Run one analysis, save it under runs/<run_id>/ and return the run dict (with `run_dir`, `markdown`).
    loop: "v1" = one JSON action per turn (Investigation); "v2" = unified multi-turn chat with the generic tools (loop_v2)."""
    if loop not in ("v1", "v2"):
        raise ValueError("loop must be v1 or v2")
    budget = budget or config.Budget.for_loop(loop)
    inc = incident or intake.build_incident(key, text)
    region = (region or inc.get("region_guess") or "EU").upper()
    all_cards = cardmod.load_cards()
    matches = cardmod.match_cards(inc, all_cards)
    code = code or CodeContext()
    ctx = code.auto_context(inc)
    registry = registry or TemplateRegistry.load()
    ex = Executor(registry=registry, code=code, mode=mode, region=region, budget=budget, runner_factory=runner_factory)
    gate = {"endpoints": [], "items": [], "text": "", "ids": []}                 # round 2 deterministic helpers (no DB, no LLM)
    prov = {"columns": [], "writers": [], "text": "", "templates": []}
    try:
        if ui_gating.is_capability_ticket(inc):
            eps = ui_gating.endpoints_from_incident(inc)
            gate = ui_gating.analyze(code.graph, eps)
            ex.gating_done.update(gate.get("endpoints") or [])
        prov = provenance.analyze(inc, registry)
    except Exception as e:                                                    # noqa: BLE001 - a helper bug must not kill the run
        gate_err = "%s: %s" % (e.__class__.__name__, str(e)[:120])
    else:
        gate_err = None
    extra_text = "\n\n".join(t for t in (gate.get("text"), prov.get("text")) if t)
    ex.known_sources.extend(i["file"] for i in gate.get("items") or [] if i.get("lines"))
    ex.known_sources.extend(prov.get("source_paths") or [])
    try:
        pf = preflight.run(inc, ex)                      # may switch ex.region if the WO lives only in the other region
        bl = playbooks.run(inc, ex, pf, gating=gate)                  # live mode: deterministic baseline queries (counted in the budget)
        region = ex.region
        ka = known_issue.analyze(inc, ex, pf, bl, all_cards)      # state-based card match + deterministic verification (live)
        if ka and ka.get("match"):
            matches = list(ka["matches"]) + [m for m in matches if m["card"]["id"] not in {x["card"]["id"] for x in ka["matches"]}]
        client = llm(inc, matches, region) if callable(llm) and not isinstance(llm, LLMClient) else llm
        if loop == "v2":
            from .loop_v2 import InvestigationV2 as Inv
        else:
            Inv = Investigation
        out = Inv(client, inc, matches, ctx, ex, budget=budget, on_step=on_step, resolved_text=pf["text"],
                  baseline_text=bl["text"], card_text=(ka or {}).get("section", ""), extra_text=extra_text).run()
    finally:
        ex.close()
    if ka and ka.get("match"):
        known_issue.apply_to_report(out["report"], ka, out["report"].get("executed_queries", 0))
        calibration.apply(out["report"], out["queries"])       # a card may have raised the confidence: caps are re-evaluated
    if gate_err:
        out["warnings"].append("round-2 helper failed: " + gate_err)
    if ka and ka.get("error") and not ka.get("match"):
        out["warnings"].append("known-issue shortcut failed: " + ka["error"])
    run_id = store.new_run_id(inc.get("key"))
    run = {
        "run_id": run_id, "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "mode": mode, "region": region, "llm": ("fake-demo" if demo else getattr(client, "name", "llm")), "demo": demo,
        "budget": vars(budget), "input": {"key": key, "text": text}, "incident": inc, "matches": matches,
        "report": out["report"], "steps": out["steps"], "queries": out["queries"], "llm_calls": out["llm_calls"],
        "forced_final": out["forced_final"], "loop": loop, "tool_calls": out.get("tool_calls"),
        "llm_format": getattr(client, "fmt", None), "llm_transport": getattr(client, "transport", None),
        "llm_fallback_reason": getattr(client, "fallback_reason", None),
        "preflight": {k: pf[k] for k in ("numbers", "resolved", "unresolved", "region", "switched")},
        "known_issue": (ka or {}).get("summary"),
        "baseline": {"selected": bl["selected"], "ran": bl["ran"]},
        "ui_gating": {"endpoints": gate.get("endpoints"), "ids": gate.get("ids"), "chars": len(gate.get("text") or "")},
        "provenance": {"columns": prov.get("columns"), "writers": prov.get("writers"), "templates": prov.get("templates")},
        "warnings": out["warnings"], "code_backend": code.backend,
    }
    out["report"]["llm_transport"] = run["llm_transport"]
    md = render_markdown(run)
    d = store.save_run(run, md, runs_base)
    run["run_dir"], run["markdown"] = str(d), md
    return run
