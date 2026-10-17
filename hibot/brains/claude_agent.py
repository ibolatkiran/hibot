"""HiBot investigation driven by Claude with NATIVE tool calling over the HiBot tool layer.

Experiment for D20/D22: same tools (hibot/tools) and same methods (knowledge/skills) as the coordinator,
with a real tool-calling model instead of the ArGPT JSON emulation.

    python -m hibot.brains.claude_agent SM-2169006 [--model claude-sonnet-5-5] [--effort medium] [--max-usd 1.0] [--max-steps 30]

Needs ANTHROPIC_API_KEY (HiBot/.env). Read-only: every DB access goes through hibot.db (safe_sql, audit log).
Outputs: runs/claude_<timestamp>_<key>/ (transcript.jsonl, report.md, usage.json).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from hibot.envfile import load_env

ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "knowledge" / "skills"
RUNS_DIR = ROOT / "runs"

# $ per 1M tokens: input, output, cache read, cache write (5-minute TTL = 1.25x input)
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
SERVER_FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
TOOL_RESULT_MAX_CHARS = 12000

SYSTEM_TEMPLATE = """You are HiBot, a senior support engineer investigating production incidents of Sirius, \
Arçelik/Beko's field-service platform. Your job: find exactly where the problem comes from, prove it with evidence from \
read-only queries and source code, and tell the developer how to fix it.

## Environment
- Apps: portal = SiriusHeadOffice (SIRIUS Portal), crm = SiriusCallCenter (SIRIUS CRM), partner = SiriusServiceShopCallCenter \
(SIRIUS Partner), technician = SiriusTechnician. Backend: SiriusApi (.NET 5, Dapper + stored procedures; most business \
logic lives in SPs/functions). DB: SQL Server, schema in sqlproj repos gfsm-europe (EU, superset) and gfsm-apac.
- Only the EU database is reachable right now. Every table is tenant-scoped (TenantId); there are no FK constraints.
- Tools are read-only. SQL goes through a validator (SELECT only, TOP limit, no credential/PII columns in ad-hoc SQL); \
prefer templates (list_templates / run_template) for common lookups, use sql for anything else.
- Never print personal data (names, phones, e-mails) in your answer; ids and codes are fine.

## Method
Follow the investigation protocol below. For the symptom-specific method, call read_skill with the skill that fits \
(see the skills index). Go down to the mechanism: read the code that produces the behaviour and query its inputs for \
the concrete records; compare with a working sibling; try to falsify your best hypothesis before concluding.

{protocol}

## Skills index
{skills_index}

## Final answer
When you are done (or told the budget is used up), reply WITHOUT calling tools, in Turkish, as Markdown with exactly these \
sections:
# HiBot (Claude) Analiz: <ticket key>
**Kök neden türü:** <config|code_defect|master_data|integration|user_error|access|data_issue|change_request|unknown> · \
**Yapılması gereken:** <data_fix|config_change|code_change|master_data_change|explain_to_user|grant_access|escalate_other_system|route_to_backlog|unknown> · \
**Güven:** <0-1, only as high as executed evidence supports>
## Özet  (2-3 sentences)
## Mekanizma  (how the behaviour is produced; file:line / SP / table names)
## Kanıtlar  (numbered; each = a tool call you actually made + the key result)
## Kod konumları
## Çözüm adımları  (immediate workaround without code, proper fix, who decides)
## Kart önerisi  (if this is a recurring pattern: title + signature + verification query; else "yok")
"""


def _skills_index() -> str:
    readme = (SKILLS_DIR / "README.md").read_text(encoding="utf-8")
    return readme.split("## Always start with", 1)[-1].strip()


def _skill_names() -> List[str]:
    return sorted(p.stem for p in SKILLS_DIR.glob("*.md") if p.stem != "README")


def build_tools() -> List[dict]:
    from hibot.tools.registry import TOOLS
    tools = [{"name": t.name, "description": "%s\nWhen: %s" % (t.description, t.when), "input_schema": t.schema}
             for t in TOOLS]
    tools.append({
        "name": "read_skill",
        "description": "Read one investigation method from the skills library (knowledge/skills). Use it once the symptom is clear.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["name"],
                         "properties": {"name": {"type": "string", "enum": _skill_names()}}},
    })
    return tools  # deterministic order -> stable prompt-cache prefix


def run_tool(name: str, args: Dict[str, Any]) -> tuple:
    if name == "read_skill":
        n = args.get("name")
        if n not in _skill_names():
            return "unknown skill %r; available: %s" % (n, ", ".join(_skill_names())), True
        return (SKILLS_DIR / ("%s.md" % n)).read_text(encoding="utf-8"), False
    from hibot.tools.registry import call_tool
    try:
        res = call_tool(name, args)
    except Exception as e:                                   # noqa: BLE001 - surfaced to the model as a tool error
        return "%s: %s" % (type(e).__name__, str(e)[:500]), True
    text = json.dumps(res, ensure_ascii=False, default=str)
    if len(text) > TOOL_RESULT_MAX_CHARS:
        text = text[:TOOL_RESULT_MAX_CHARS] + ' …[truncated: narrow the query/window]"'
    return text, isinstance(res, dict) and res.get("ok") is False


def incident_message(key: str) -> str:
    from hibot.agent import intake
    inc = intake.build_incident(key)
    keep = ("key", "summary", "desc", "comments", "entities", "problem_type", "app", "country", "company_code",
            "status", "created", "resolved", "resolution", "screen_hints", "similar")
    brief = {k: inc.get(k) for k in keep if inc.get(k) not in (None, "", [], {})}
    return ("Investigate this Sirius incident. Ticket text is scrubbed; Jira comments are unverified.\n\n```json\n%s\n```"
            % json.dumps(brief, ensure_ascii=False, indent=1, default=str))


class Cost:
    def __init__(self, model: str):
        self.p = PRICES.get(model, PRICES["claude-opus-5-5"])
        self.tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    def add(self, usage) -> None:
        self.tok["input"] += getattr(usage, "input_tokens", 0) or 0
        self.tok["output"] += getattr(usage, "output_tokens", 0) or 0
        self.tok["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.tok["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @property
    def usd(self) -> float:
        i, o, cr, cw = self.p
        t = self.tok
        return (t["input"] * i + t["output"] * o + t["cache_read"] * cr + t["cache_write"] * cw) / 1e6


def investigate(key: str, model: str, effort: str, max_usd: float, max_steps: int) -> Path:
    import anthropic

    client = anthropic.Anthropic()
    protocol = (SKILLS_DIR / "investigation_protocol.md").read_text(encoding="utf-8")
    system = SYSTEM_TEMPLATE.format(protocol=protocol, skills_index=_skills_index())
    tools = build_tools()
    messages: List[dict] = [{"role": "user", "content": incident_message(key)}]

    run_dir = RUNS_DIR / ("claude_%s_%s" % (_dt.datetime.now().strftime("%Y%m%d-%H%M%S"), key))
    run_dir.mkdir(parents=True, exist_ok=True)
    log = (run_dir / "transcript.jsonl").open("w", encoding="utf-8")
    cost, steps, final_text, stop = Cost(model), 0, "", None
    extra: Dict[str, Any] = {}
    if model in SERVER_FALLBACK_MODELS:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    t0 = time.time()

    while True:
        budget_hit = cost.usd >= max_usd * 0.85 or steps >= max_steps
        if budget_hit:
            messages.append({"role": "user", "content": "Bütçe doldu. Araç çağırma; şimdiye kadarki kanıtla final raporu yaz."})
        response = client.beta.messages.create(
            model=model, max_tokens=16000, system=system, tools=tools, messages=messages,
            tool_choice={"type": "none"} if budget_hit else {"type": "auto"},
            output_config={"effort": effort}, cache_control={"type": "ephemeral"}, **extra)
        cost.add(response.usage)
        steps += 1
        stop = response.stop_reason
        messages.append({"role": "assistant", "content": response.content})
        uses = [b for b in response.content if b.type == "tool_use"]
        log.write(json.dumps({"step": steps, "stop": stop, "usd": round(cost.usd, 4),
                              "tool_calls": [{"name": b.name, "input": b.input} for b in uses],
                              "text": " ".join(getattr(b, "text", "") for b in response.content if b.type == "text")[:2000]},
                             ensure_ascii=False, default=str) + "\n")
        log.flush()
        print("step %2d  %-10s  $%.3f  %s" % (steps, stop, cost.usd, ", ".join(b.name for b in uses)), flush=True)

        if stop == "refusal" or budget_hit or not uses:
            final_text = "\n".join(b.text for b in response.content if b.type == "text")
            break
        results = []
        for b in uses:
            text, is_err = run_tool(b.name, dict(b.input or {}))
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": is_err})
        messages.append({"role": "user", "content": results})

    log.close()
    (run_dir / "report.md").write_text(final_text or "(no final text; stop_reason=%s)" % stop, encoding="utf-8")
    usage = {"key": key, "model": model, "effort": effort, "steps": steps, "stop_reason": stop,
             "usd": round(cost.usd, 4), "tokens": cost.tok, "seconds": round(time.time() - t0, 1)}
    (run_dir / "usage.json").write_text(json.dumps(usage, indent=1), encoding="utf-8")
    print("\nRapor: %s\nAdım: %d | maliyet: $%.3f | süre: %.0f sn" % (run_dir / "report.md", steps, cost.usd, usage["seconds"]))
    return run_dir


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                    # noqa: BLE001
            pass
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("key")
    # Default: Sonnet 5.5 (user asked for a cheaper model than Opus, 2026-10-17); effort medium is the recommended
    # starting point for multistep tool use on Sonnet 5.5.
    ap.add_argument("--model", default=os.environ.get("HIBOT_CLAUDE_MODEL", "claude-sonnet-5-5"), choices=sorted(PRICES))
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--max-usd", type=float, default=1.0, help="stop and force the final report near this spend")
    ap.add_argument("--max-steps", type=int, default=30)
    a = ap.parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY yok: HiBot/.env dosyasına ekle (ANTHROPIC_API_KEY=...)", file=sys.stderr)
        return 2
    investigate(a.key, a.model, a.effort, a.max_usd, a.max_steps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
