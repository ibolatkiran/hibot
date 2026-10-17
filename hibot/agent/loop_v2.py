"""Unified investigation loop ("v2"): deterministic case file + open-ended multi-turn investigation with the generic tools.

    deterministic steps (preflight wo_locate, playbooks, known-issue card + verification, ticket window, UI gating,
    column provenance)  ──►  CASE FILE (first user message)
    system message: role, protocol, skills index, tool catalog, classification rules, output protocol
    chat:  assistant JSON {"thought","tool","args"} | {"thought","tools":[..<=3]} | {"final_report":{..}}
           user      OBSERVATION (tool result, truncated)           (real multi-turn chat, ArGPT chat-completion)

ArGPT has NO native tool calling, so the tool protocol is emulated with strict JSON.  Everything after the loop (grounding against
executed queries, deterministic calibration, axes normalisation, card application, Turkish report) is the existing machinery of
`Investigation` (hibot/agent/investigate.py), which this class extends: guards, grounding, fallback report are inherited.

DB access: `run_template` and `sql` go through the Executor (hibot.db: templates, safe_sql, masking, audit), so they are budgeted,
dry-run safe and recorded in `queries` exactly like v1 queries (the scorer, calibration and report read that list).  All other tools go
through `hibot.tools.registry.call_tool`.  Budgets: steps (LLM turns), tool calls, queries, ad-hoc SQL, context chars.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from hibot import prompts
from hibot.llm.client import LLMClient, LLMError, client_from_env
from hibot.llm.jsonio import JsonRepairFailed, complete_json, validate_schema

from . import actions, axes, config
from .executor import Executor, _cut
from .investigate import Investigation
from .prompting import build_case_prompt

MAX_TOOLS_PER_TURN = 3
KEEP_LAST_TURNS = 2                           # newest turns whose observations are never shortened
TRIM_CAPS = (1500, 600, 250, 100)             # progressive per-observation caps for older turns
EVIDENCE_TYPE_MAP = {"sql": "query", "template": "query", "run_template": "query", "db": "query", "database": "query",
                     "file": "code", "source": "code", "sp": "code", "graph": "code", "skill": "code", "git": "code",
                     "ticket": "similar_case", "similar": "similar_case", "similar_ticket": "similar_case", "known_card": "card"}
TOOL_ARGS_CHARS = 160


# --------------------------------------------------------------------------- llm client
def v2_format(env: Optional[dict] = None) -> str:
    """HIBOT_LLM_FORMAT for the unified loop: the ArGPT text-completion format (what .env normally says) is upgraded to `argpt_chat`
    (real chat roles); any other explicit format (openai / anthropic / raw / argpt_chat) is respected.  HIBOT_LLM_FORMAT_V2 wins."""
    e = os.environ if env is None else env
    forced = (e.get("HIBOT_LLM_FORMAT_V2") or "").strip().lower()
    if forced:
        return forced
    cur = (e.get("HIBOT_LLM_FORMAT") or "").strip().lower()
    if cur in ("argpt", "argpt_chat"):
        return "argpt_chat"
    if not cur and re.search(r"/(text|chat)-completion/?$", e.get("HIBOT_LLM_URL") or ""):
        return "argpt_chat"
    return cur or "openai"


def make_client(env: Optional[dict] = None) -> LLMClient:
    return client_from_env("http", fmt=v2_format(env))


# --------------------------------------------------------------------------- turn parsing
def _clip(v: Any, n: int) -> Any:
    return v[:n] if isinstance(v, str) and len(v) > n else v


def normalize_report(rep: dict) -> dict:
    """Make a model-written final_report fit actions.REPORT_SCHEMA (clip over-long strings, coerce common slips)."""
    rep = {k: v for k, v in rep.items() if k not in ("action", "thought")}
    for k, n in (("summary", 1500), ("root_cause", 3000)):
        if isinstance(rep.get(k), str):
            rep[k] = rep[k][:n]
    if isinstance(rep.get("confidence"), str):
        try:
            rep["confidence"] = float(rep["confidence"].replace(",", "."))
        except ValueError:
            pass
    if isinstance(rep.get("confidence"), (int, float)) and not isinstance(rep["confidence"], bool):
        rep["confidence"] = max(0.0, min(1.0, float(rep["confidence"])))
    ev = []
    for e in (rep.get("evidence") if isinstance(rep.get("evidence"), list) else [])[:20]:
        if not isinstance(e, dict):
            continue
        e = dict(e)
        t = str(e.get("type") or "").lower()
        e["type"] = EVIDENCE_TYPE_MAP.get(t, t)
        e["ref"] = _clip(str(e.get("ref") if e.get("ref") is not None else ""), 300)
        e["finding"] = _clip(str(e.get("finding") if e.get("finding") is not None else ""), 1500)
        ev.append(e)
    if "evidence" in rep or ev:
        rep["evidence"] = ev
    fs = rep.get("fix_steps")
    if isinstance(fs, str):
        fs = [fs]
    if isinstance(fs, list):
        rep["fix_steps"] = [_clip(str(x), 800) for x in fs[:20]]
    locs = []
    for c in (rep.get("code_locations") if isinstance(rep.get("code_locations"), list) else [])[:20]:
        if isinstance(c, dict):
            c = dict(c)
            c.setdefault("repo", "")
            c.setdefault("path", "")
            c["why"] = str(c.get("why") or "")
            if not isinstance(c.get("line"), int) or isinstance(c.get("line"), bool):
                try:
                    c["line"] = int(c["line"]) if c.get("line") not in (None, "") else None
                except (TypeError, ValueError):
                    c["line"] = None
            locs.append(c)
    if "code_locations" in rep:
        rep["code_locations"] = locs
    if isinstance(rep.get("verification_query_ids"), list):
        rep["verification_query_ids"] = [str(x) for x in rep["verification_query_ids"][:20]]
    if "suggested_card" in rep and not isinstance(rep["suggested_card"], dict):
        rep.pop("suggested_card")
    return rep


def validate_report(rep: Any) -> List[str]:
    if not isinstance(rep, dict):
        return ["final_report must be an object"]
    errs = validate_schema(rep, actions.REPORT_SCHEMA, "$.final_report")
    if not errs and "classification" not in rep and not ("root_cause_type" in rep and "resolution_action" in rep):
        errs.append("$.final_report.root_cause_type / resolution_action: both are required")
    return errs


def parse_turn(obj: Any, only_final: bool = False) -> Tuple[str, Any, List[str]]:
    """One assistant JSON object -> (kind, payload, errors).  kind: 'final' (payload = normalized report dict),
    'tools' (payload = [(tool, args), ...]) or 'invalid'."""
    if not isinstance(obj, dict):
        return "invalid", None, ["the reply must be ONE JSON object"]
    rep = None
    if isinstance(obj.get("final_report"), dict):
        rep = obj["final_report"]
    elif obj.get("action") == "final_report" or (("root_cause_type" in obj or "classification" in obj) and "confidence" in obj
                                                   and "tool" not in obj and "tools" not in obj):
        rep = obj                                              # flat report (v1 style) is accepted too
    if rep is not None:
        rep = normalize_report(rep)
        errs = validate_report(rep)
        return ("final", rep, []) if not errs else ("invalid", None, errs)
    if only_final:
        return "invalid", None, ['the budget is exhausted: reply with {"final_report": {...}} only']
    calls: List[Tuple[str, Any]] = []
    if "tools" in obj:
        ts = obj["tools"]
        if not isinstance(ts, list) or not ts:
            return "invalid", None, ['"tools" must be a non-empty list of {"tool","args"}']
        for i, t in enumerate(ts):
            if not isinstance(t, dict) or not isinstance(t.get("tool"), str) or not t["tool"].strip():
                return "invalid", None, ['tools[%d] needs a "tool" name' % i]
            if t.get("args") is not None and not isinstance(t["args"], dict):
                return "invalid", None, ['tools[%d].args must be an object' % i]
            calls.append((t["tool"].strip(), t.get("args") or {}))
    elif "tool" in obj:
        if not isinstance(obj["tool"], str) or not obj["tool"].strip():
            return "invalid", None, ['"tool" must be a tool name']
        if obj.get("args") is not None and not isinstance(obj["args"], dict):
            return "invalid", None, ['"args" must be an object']
        calls.append((obj["tool"].strip(), obj.get("args") or {}))
    else:
        return "invalid", None, ['the reply needs "tool" (+ "args"), "tools" or "final_report"']
    return "tools", calls, []


def _fmt_args(args: Any) -> str:
    s = json.dumps(args, ensure_ascii=False, separators=(",", ":"), default=str)
    return s if len(s) <= TOOL_ARGS_CHARS else s[:TOOL_ARGS_CHARS] + "..."


# --------------------------------------------------------------------------- the loop
class InvestigationV2(Investigation):
    def __init__(self, llm: LLMClient, incident: dict, matches: List[dict], code_ctx: List[dict], executor: Executor,
                 *, budget: Optional[config.Budget] = None, system_prompt: Optional[str] = None,
                 on_step: Optional[Callable[[dict], None]] = None, resolved_text: str = "", baseline_text: str = "",
                 card_text: str = "", extra_text: str = "", tool_fn: Optional[Callable[[str, dict], dict]] = None):
        # NOTE: no super().__init__ (it builds the v1 prompts); the attributes below are the ones the inherited methods use.
        self.llm, self.inc, self.matches, self.executor = llm, incident, matches, executor
        self.budget = budget or executor.budget
        self.system = system_prompt or prompts.v2_system_prompt()
        self.on_step = on_step
        self.steps: List[dict] = []
        self.llm_calls = 0
        self.forced = False
        self.warnings: List[str] = []
        self.early_stop_used = False
        self.source_guard_used = False
        self.tool_calls = 0
        self.beyond_case_calls = 0                  # tool calls other than read_skill (those are not investigation)
        self.beyond_ok_calls = 0                    # ... of which succeeded (the depth check counts these)
        self.depth_check_used = False
        self.evidence_guard_used = False
        self.card_decides = bool(card_text) and "DESTEKL" in card_text.upper() and "[confirmed" in card_text
        self.trim_events = 0
        self.tool_fn = tool_fn
        for c in code_ctx or []:
            executor._note_sources(c.get("result") if isinstance(c, dict) else None)
        catalog = executor.registry.catalog_text(executor.region)
        self.case_prompt = build_case_prompt(incident, matches, code_ctx, catalog, mode=executor.mode, region=executor.region,
                                             budget=self.budget, resolved=resolved_text, baseline=baseline_text,
                                             card_section=card_text, extra=extra_text, loop="v2")

    # ------------------------------------------------------------ messages / context control
    def _render_step(self, s: dict, cap: Optional[int]) -> str:
        parts: List[str] = []
        calls = s.get("calls") or []
        for i, c in enumerate(calls, 1):
            t = c["text"]
            if cap is not None and len(t) > cap:
                t = ("[observation omitted to save context: %d chars; call the tool again if you need it]" % len(t)) if cap == 0 else (
                    t[:cap] + " ...[older observation shortened from %d chars]" % len(t))
            parts.append("RESULT %d/%d  %s %s%s\n%s" % (i, len(calls), c["tool"], _fmt_args(c["args"]), "" if c["ok"] else "  (FAILED)", t))
        if s.get("note"):
            parts.append(s["note"])
        return "OBSERVATION (step %d):\n%s\n\n%s" % (s["n"], "\n\n".join(parts), s["status"])

    def _total_chars(self, caps: List[Optional[int]]) -> int:
        n = len(self.system) + len(self.case_prompt)
        for s, cap in zip(self.steps, caps):
            n += len(s["action_text"]) + len(self._render_step(s, cap))
        return n

    def _messages(self, tail: Optional[str] = None) -> List[Dict[str, str]]:
        """system + case file + the whole chat.  Over budget: older observations are shortened progressively (the case file, the
        assistant turns - the model's notebook - and the newest KEEP_LAST_TURNS observations stay intact)."""
        k = len(self.steps)
        caps: List[Optional[int]] = [None] * k
        limit = self.budget.max_context_chars
        if self._total_chars(caps) > limit:
            self.trim_events += 1
            old = max(0, k - KEEP_LAST_TURNS)
            for cap in TRIM_CAPS + (0,):
                for i in range(old):
                    caps[i] = cap
                if self._total_chars(caps) <= limit:
                    break
        msgs: List[Dict[str, str]] = [{"role": "system", "content": self.system}, {"role": "user", "content": self.case_prompt}]
        for s, cap in zip(self.steps, caps):
            msgs.append({"role": "assistant", "content": s["action_text"]})
            msgs.append({"role": "user", "content": self._render_step(s, cap)})
        if tail:
            if msgs[-1]["role"] == "user":                      # one user message per turn: chat endpoints dislike two in a row
                msgs[-1] = {"role": "user", "content": msgs[-1]["content"] + "\n\n" + tail}
            else:
                msgs.append({"role": "user", "content": tail})
        return msgs

    def _status(self, n: int) -> str:
        e, b = self.executor, self.budget
        s = "Budget: step %d/%d, tool calls %d/%d, queries %d/%d (ad-hoc sql %d/%d)." % (
            n, b.max_steps, self.tool_calls, b.max_tool_calls, e.query_count, b.max_queries, e.adhoc_count, b.max_adhoc_sql)
        if n >= b.max_steps or self.tool_calls >= b.max_tool_calls:
            s += ' The budget is used up: your next reply MUST be {"final_report": {...}}.'
        elif n >= b.max_steps - 2 or self.tool_calls >= b.max_tool_calls - 3:
            s += " Budget nearly used up: finish with final_report soon."
        return s

    # ------------------------------------------------------------ llm
    def _ask(self, tail: Optional[str] = None, only_final: bool = False) -> Tuple[str, Any, dict]:
        self.llm_calls += 1
        holder: Dict[str, Any] = {}

        def check(o: Any) -> List[str]:
            kind, payload, errs = parse_turn(o, only_final)
            holder["kind"], holder["payload"] = kind, payload
            return errs

        obj, _raw, attempts = complete_json(self.llm, self._messages(tail), None, validator=check,
                                            max_retries=self.budget.max_llm_retries, max_tokens=3500)
        self.llm_calls += attempts - 1
        return holder["kind"], holder["payload"], obj

    # ------------------------------------------------------------ tools
    def _compact(self, res: Any) -> str:
        text = json.dumps(res, ensure_ascii=False, separators=(",", ":"), default=str)
        cap = self.budget.max_tool_obs_chars
        if len(text) > cap:
            text = text[:cap] + " ...[truncated %d chars: narrow the request (filters, fewer columns, a line window)]" % (len(text) - cap)
        return text

    def _dispatch(self, name: str, args: Any, why: str = "") -> dict:
        """Run one tool.  Returns {"ok": bool, "text": str, "query_no": int|None}; never raises."""
        from hibot.tools import registry
        tool = registry.BY_NAME.get(name)
        if tool is None:
            return {"ok": False, "text": "ERROR: unknown tool %r. Available tools: %s" % (name[:60], ", ".join(registry.BY_NAME))}
        args = dict(args) if isinstance(args, dict) else args
        problem = registry.validate_args(tool, args)
        if problem:
            return {"ok": False, "text": "ERROR: invalid arguments for %s: %s. Usage: %s" % (name, problem, tool.signature())}
        ex = self.executor
        try:
            if name in ("run_template", "sql"):
                region = str(args.get("region") or ex.region).upper()
                if name == "run_template":
                    obs = ex._run_query({"query_id": args["query_id"], "params": args.get("params") or {}, "region": region, "why": why})
                else:
                    obs = ex._run_sql({"sql": args["query"], "region": region, "why": why})
                rec = obs.get("record")
                return {"ok": bool(obs["ok"]), "text": _cut(obs["text"], self.budget.max_obs_chars), "query_no": rec["n"] if rec else None}
            fn = self.tool_fn or registry.call_tool
            res = fn(name, args)
            ok = not (isinstance(res, dict) and res.get("ok") is False)
            extra = ""
            if ok and name == "read_file":
                ex.sources_read += 1
            if ok and name.startswith("graph_"):
                ex._note_sources(res)
                if name == "graph_trace_endpoint":
                    extra = self._gating_text(args.get("endpoint"))
            return {"ok": ok, "text": extra + self._compact(res), "query_no": None}
        except Exception as e:                                          # noqa: BLE001 - a tool bug must not kill the run
            return {"ok": False, "text": "ERROR while running %s: %s: %s" % (name, type(e).__name__, str(e)[:200]), "query_no": None}

    def _gating_text(self, endpoint: Any) -> str:
        """Who may use this endpoint? (UI gating of the calling screens), once per endpoint, as in v1 inspect_code."""
        graph = getattr(self.executor.code, "graph", None)
        if graph is None or not endpoint:
            return ""
        try:
            from . import ui_gating
            g = ui_gating.analyze(graph, [endpoint], max_endpoints=1)
        except Exception:                                               # noqa: BLE001
            return ""
        if g.get("text") and not (set(g.get("endpoints") or []) & self.executor.gating_done):
            self.executor.gating_done.update(g.get("endpoints") or [])
            return g["text"] + "\n"
        return ""

    # ------------------------------------------------------------ logging
    def _log_turn(self, n: int, obj: Any, calls: List[dict], note: str = "") -> None:
        action_text = json.dumps(obj, ensure_ascii=False, default=str)
        obs = "\n\n".join("[%s] %s" % (c["tool"], c["text"]) for c in calls) if calls else note
        qn = next((c["query_no"] for c in calls if c.get("query_no")), None)
        step = {"n": n, "action": obj, "action_text": action_text, "observation": obs, "ok": all(c["ok"] for c in calls) if calls else False,
                "status": self._status(n), "query_no": qn, "note": note,
                "calls": [{"tool": c["tool"], "args": c["args"], "ok": c["ok"], "query_no": c.get("query_no"), "text": c["text"]} for c in calls]}
        self.steps.append(step)
        if self.on_step:
            self.on_step(step)

    # ------------------------------------------------------------ evidence refs
    def _fix_refs(self, rep: dict) -> dict:
        """Map the evidence refs the model wrote ('run_template wo_header', '#3', 'sql') to executed query ids so grounding can find them."""
        qs = self.executor.queries
        ids = {q["query_id"] for q in qs}
        by_no = {q["n"]: q["query_id"] for q in qs}
        for ev in rep.get("evidence") or []:
            if ev.get("type") != "query":
                continue
            ref = str(ev.get("ref") or "")
            m = re.search(r"#(\d+)", ref)
            if m and int(m.group(1)) in by_no:
                ev["ref"] = by_no[int(m.group(1))]
                continue
            if ref.split()[:1] and ref.split()[0] in ids or ref.lower().startswith(("ticket s", "ticket_window")):
                continue
            hit = next((t for t in re.findall(r"[A-Za-z0-9_]+", ref) if t in ids), None)
            if hit:
                ev["ref"] = hit
            elif re.search(r"\bsql\b|ad-?hoc", ref, re.I) and "adhoc" in ids:
                ev["ref"] = "adhoc"
        return rep

    # ------------------------------------------------------------ guards (v1 logic, v2 wording)
    def _early_stop_text(self, shallow: bool = False) -> str:
        t = super()._early_stop_text(shallow)
        return t.replace("read_source", "read_file") + " (Use the run_template / sql tools; this refusal happens once.)"

    def _source_guard_text(self) -> str:
        return super()._source_guard_text().replace("use read_source", "use read_file").replace("read_source", "read_file")

    # ------------------------------------------------------------ depth check (self-critique, D20), once per run
    def _depth_needed(self, n: int) -> bool:
        """The model wants to finish after fewer than 2 real investigation calls (read_skill does not count) and the case file holds no
        confirmed, supported known-issue card that already decides the case: ask ONCE for a self-check (mechanism read? falsified?)."""
        return (not self.depth_check_used and n < self.budget.max_steps and self.beyond_ok_calls < 2 and not self.card_decides
                and self.tool_calls < self.budget.max_tool_calls)

    def _depth_text(self) -> str:
        return ("SELF-CHECK BEFORE FINALISING (asked once): you made %d successful tool call(s) beyond the case file. Check honestly: "
                "(1) MECHANISM: did you read the code (SP / screen condition) that produces the behaviour and can you cite file:line? "
                "(2) FALSIFICATION: which observation would prove your root cause wrong (a working sibling, another tenant, the inputs of the "
                "calculation, the alternative hypothesis) and did you run it? "
                "(3) For 'update / correct field X' or 'cannot do X' requests: how is the value set today (column_writers) and who may do it? "
                "(4) Does every id / number in your evidence come from a real observation? "
                "Run the missing checks NOW with tools (up to 3 per turn). If after honest reflection nothing is missing, resend your "
                "final_report (refined where the checks taught you something)." % self.beyond_ok_calls)

    # ------------------------------------------------------------ run
    def run(self) -> dict:
        report = None
        seen: Dict[str, int] = {}
        b = self.budget
        try:
            for n in range(1, b.max_steps + 1):
                if self.tool_calls >= b.max_tool_calls:
                    break
                kind, payload, obj = self._ask()
                if kind == "final":
                    rep = self._fix_refs(payload)
                    shallow = self._status_shallow()
                    if self._premature(rep, n):
                        self.early_stop_used = True                  # once per run
                        text = self._early_stop_text(shallow)
                        if self._source_unread(rep, n):              # one refusal carries both hints
                            self.source_guard_used = True
                            text += "\n\n" + self._source_guard_text()
                        self._log_turn(n, obj, [], self._with_evidence_hint(text, n))
                        continue
                    if self._source_unread(rep, n):
                        self.source_guard_used = True                # once per run
                        self._log_turn(n, obj, [], self._with_evidence_hint(self._source_guard_text(), n))
                        continue
                    if self._no_evidence(n):
                        self.evidence_guard_used = True              # once per run
                        text = self._evidence_guard_text()
                        if self._depth_needed(n):                    # one refusal carries both
                            self.depth_check_used = True
                            text += chr(10) * 2 + self._depth_text()
                        self._log_turn(n, obj, [], text)
                        continue
                    if self._depth_needed(n):
                        self.depth_check_used = True                 # once per run
                        self._log_turn(n, obj, [], self._depth_text())
                        continue
                    report = actions.split_report(rep)
                    self._log_turn(n, obj, [], "(final report)")
                    break
                thought = str(obj.get("thought") or "")[:300] if isinstance(obj, dict) else ""
                calls: List[dict] = []
                for name, args in payload[:MAX_TOOLS_PER_TURN]:
                    key = name + " " + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
                    if key in seen:
                        r = {"ok": False, "text": "DUPLICATE: identical call already made in step %d; its observation is in the chat above (it may be "
                                                  "shortened). Choose a different call or send final_report." % seen[key]}
                    elif self.tool_calls >= b.max_tool_calls:
                        r = {"ok": False, "text": "REJECTED: tool-call budget used up (%d/%d). Send final_report." % (self.tool_calls, b.max_tool_calls)}
                    else:
                        seen[key] = n
                        self.tool_calls += 1
                        r = self._dispatch(name, args, thought)
                        self.beyond_case_calls += name != "read_skill"
                        self.beyond_ok_calls += name != "read_skill" and bool(r["ok"])       # a failed call gathered nothing
                    calls.append(dict(r, tool=name, args=args))
                note = ""
                if len(payload) > MAX_TOOLS_PER_TURN:
                    note = "NOTE: at most %d tools per turn; the last %d call(s) were ignored." % (MAX_TOOLS_PER_TURN, len(payload) - MAX_TOOLS_PER_TURN)
                self._log_turn(n, obj, calls, note)
            if report is None:
                report = self._force_final()
        except LLMError as e:
            self.warnings.append("LLM transport error: %s" % e)
            report = self._fallback("LLM isteği başarısız oldu: %s" % str(e)[:200])
        except JsonRepairFailed as e:
            self.warnings.append("LLM output invalid: %s" % e)
            report = self._fallback("Model geçerli bir JSON yanıtı üretemedi: %s" % str(e)[:200])
        return {"report": self._ground(report), "steps": self.steps, "queries": self.executor.queries, "llm_calls": self.llm_calls,
                "forced_final": self.forced, "warnings": self.warnings, "tool_calls": self.tool_calls}

    def _force_final(self) -> dict:
        self.forced = True
        tail = ("BUDGET EXHAUSTED. Reply NOW with {\"final_report\": {...}} only, based on the evidence so far. If it is not sufficient, "
                "use root_cause_type \"unknown\" with low confidence and list what is missing in fix_steps.")
        try:
            kind, payload, _obj = self._ask(tail=tail, only_final=True)
            return actions.split_report(self._fix_refs(payload))
        except JsonRepairFailed as e:
            self.warnings.append("forced final_report invalid: %s" % e)
            return self._fallback("Bütçe doldu ve model geçerli bir final_report üretemedi.")
