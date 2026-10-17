"""Step 3: the investigation loop (no tool calling: the LLM answers JSON, this code validates and executes).

    case file ──► LLM ──► action JSON ──► validate (repair loop) ──► Executor ──► observation ──► LLM ...
                                     └── final_report ──► ground against executed queries ──► report dict
Budgets: max_steps (actions), max_queries / max_adhoc_sql (inside Executor), max_context_chars (prompt shrinking).
When the steps run out the LLM is asked once for a forced final_report; if that fails a fallback report is built.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional

from hibot.llm.client import LLMClient, LLMError
from hibot.llm.jsonio import JsonRepairFailed, complete_json

from . import actions, axes, calibration, config, playbooks, ticket_window
from .executor import Executor
from .prompting import build_case_prompt
from hibot import prompts

NO_QUERY_CONF_CAP = calibration.NO_QUERY_CAP


class Investigation:
    def __init__(self, llm: LLMClient, incident: dict, matches: List[dict], code_ctx: List[dict], executor: Executor,
                 *, budget: Optional[config.Budget] = None, system_prompt: Optional[str] = None,
                 on_step: Optional[Callable[[dict], None]] = None, resolved_text: str = "", baseline_text: str = "", card_text: str = "", extra_text: str = ""):
        self.llm, self.inc, self.matches, self.executor = llm, incident, matches, executor
        self.budget = budget or executor.budget
        self.system = system_prompt or prompts.system_prompt()
        self.on_step = on_step
        self.steps: List[dict] = []
        self.llm_calls = 0
        self.forced = False
        self.warnings: List[str] = []
        self.early_stop_used = False
        self.source_guard_used = False
        self.evidence_guard_used = False
        self.card_decides = bool(card_text) and "DESTEKL" in card_text.upper() and "[confirmed" in card_text
        for c in code_ctx or []:                                   # sources the automatic code context already points at
            executor._note_sources(c.get("result") if isinstance(c, dict) else None)
        catalog = executor.registry.catalog_text(executor.region)
        self.case_prompt = build_case_prompt(incident, matches, code_ctx, catalog, mode=executor.mode,
                                             region=executor.region, budget=self.budget, resolved=resolved_text,
                                             baseline=baseline_text, card_section=card_text, extra=extra_text)

    # ------------------------------------------------------------ prompts
    def _messages(self, tail: Optional[str] = None) -> List[Dict[str, str]]:
        msgs: List[Dict[str, str]] = [{"role": "system", "content": self.system},
                                      {"role": "user", "content": self.case_prompt}]
        obs_limit = [None] * len(self.steps)
        total = len(self.system) + len(self.case_prompt) + sum(len(s["action_text"]) + len(s["observation"]) for s in self.steps)
        if total > self.budget.max_context_chars:                  # shorten old observations, newest two stay intact
            for cap in (600, 250, 100):
                for i in range(len(self.steps) - 2):
                    obs_limit[i] = cap
                total = len(self.system) + len(self.case_prompt) + sum(
                    len(s["action_text"]) + min(len(s["observation"]), obs_limit[i] or 10 ** 9) for i, s in enumerate(self.steps))
                if total <= self.budget.max_context_chars:
                    break
        for i, s in enumerate(self.steps):
            obs = s["observation"]
            if obs_limit[i] and len(obs) > obs_limit[i]:
                obs = obs[:obs_limit[i]] + " ...[older observation shortened]"
            msgs.append({"role": "assistant", "content": s["action_text"]})
            msgs.append({"role": "user", "content": "OBSERVATION (step %d):\n%s\n\n%s" % (s["n"], obs, s["status"])})
        if tail:
            msgs.append({"role": "user", "content": tail})
        return msgs

    def _status(self, n: int) -> str:
        e, b = self.executor, self.budget
        s = "Budget: steps %d/%d, queries %d/%d (ad-hoc %d/%d)." % (n, b.max_steps, e.query_count, b.max_queries, e.adhoc_count, b.max_adhoc_sql)
        if n >= b.max_steps:
            s += " This was the last investigation step: your next answer MUST be final_report."
        return s

    def _ask(self, tail: Optional[str] = None, only_final: bool = False) -> dict:
        self.llm_calls += 1
        obj, _raw, attempts = complete_json(
            self.llm, self._messages(tail), actions.ACTION_ENVELOPE,
            validator=lambda o: actions.validate_action(o, only_final=only_final),
            max_retries=self.budget.max_llm_retries, max_tokens=2500)
        self.llm_calls += attempts - 1
        return obj

    # --------------------------------------------------------------- run
    def run(self) -> dict:
        report = None
        seen: Dict[str, int] = {}
        try:
            for n in range(1, self.budget.max_steps + 1):
                act = self._ask()
                shallow = act["action"] == "final_report" and self._status_shallow()
                if act["action"] == "final_report" and self._premature(act, n):
                    self.early_stop_used = True                    # once per run
                    text = self._early_stop_text(shallow)
                    if self._source_unread(act, n):                # one refusal carries both hints (a second refusal would waste a step)
                        self.source_guard_used = True
                        text += "\n\n" + self._source_guard_text()
                    self._log_step(n, act, self._with_evidence_hint(text, n), False)
                    continue
                if act["action"] == "final_report" and self._source_unread(act, n):
                    self.source_guard_used = True                  # once per run
                    self._log_step(n, act, self._with_evidence_hint(self._source_guard_text(), n), False)
                    continue
                if act["action"] == "final_report" and self._no_evidence(n):
                    self.evidence_guard_used = True                # once per run
                    self._log_step(n, act, self._evidence_guard_text(), False)
                    continue
                if act["action"] == "final_report":
                    report = actions.split_report(act)
                    self._log_step(n, act, "(final report)", True)
                    break
                key = json.dumps({k: v for k, v in act.items() if k != "why"}, sort_keys=True, ensure_ascii=False)
                if key in seen:
                    obs = {"ok": False, "text": "DUPLICATE: identical request already handled in step %d; its observation is above. Choose a different action or send final_report." % seen[key]}
                else:
                    seen[key] = n
                    obs = self.executor.execute(act)
                self._log_step(n, act, obs["text"], obs["ok"], obs.get("record"))
            if report is None:
                report = self._force_final()
        except LLMError as e:
            self.warnings.append("LLM transport error: %s" % e)
            report = self._fallback("LLM isteği başarısız oldu: %s" % str(e)[:200])
        except JsonRepairFailed as e:
            self.warnings.append("LLM output invalid: %s" % e)
            report = self._fallback("Model geçerli bir JSON yanıtı üretemedi: %s" % str(e)[:200])
        return {"report": self._ground(report), "steps": self.steps, "queries": self.executor.queries,
                "llm_calls": self.llm_calls, "forced_final": self.forced, "warnings": self.warnings}

    # ------------------------------------------------------- early-stop guard
    def _premature(self, act: dict, n: int) -> bool:
        """Weak conclusion (unknown / confidence < 0.4) with at least half of the query budget unused and steps left."""
        if self.early_stop_used or n >= self.budget.max_steps:
            return False
        if self._status_shallow():
            return True
        if self.budget.max_queries - self.executor.query_count < 3:
            return False
        try:
            conf = float(act.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        return axes.normalize(dict(act)).get("classification") == "unknown" or conf < 0.4

    # ------------------------------------------------------- minimum-evidence guard
    def _evidence_count(self) -> int:
        """Evidence-gathering done so far: queries that really ran (dry-run: prepared ones) plus, in v2, tool calls other than read_skill."""
        ex = self.executor
        n = sum(1 for q in ex.queries if q.get("executed") or ex.mode != "live")
        return n + int(getattr(self, "beyond_case_calls", 0) or 0)

    def _no_evidence(self, n: int) -> bool:
        """A final_report with zero evidence gathered (no query, no code/graph/ticket tool) is refused once, unless a confirmed
        known-issue card matched and its verification supported it (FINDINGS section 22: SM-1808060 got a first-turn report with 0 queries)."""
        if self.evidence_guard_used or n >= self.budget.max_steps or self.card_decides:
            return False
        return self._evidence_count() == 0

    def _with_evidence_hint(self, text: str, n: int) -> str:
        """Another guard refuses this final_report anyway: carry the minimum-evidence hint in the same refusal (no extra step)."""
        if self._no_evidence(n):
            self.evidence_guard_used = True
            return text + chr(10) * 2 + self._evidence_guard_text()
        return text

    def _evidence_guard_text(self) -> str:
        ents = self.inc.get("entities") or {}
        wo = (ents.get("work_order_ids_strong") or ents.get("work_order_ids") or ents.get("work_case_ids") or [])[:1]
        tips = []
        if wo:
            tips.append("wo_locate / wo_header for work order %s (then wo_status_timeline, wo_field_changes)" % wo[0])
        tips.append("search the code / graph for the screen, field or error text named in the ticket and read the relevant source")
        tips.append("look at similar tickets or the ticket window of the affected record")
        tips.append("sql / run_template to check the actual data behind the claim")
        return ("NO EVIDENCE GATHERED (refused once): your final_report comes before any query or code/graph tool was executed in this investigation, "
                "so nothing in it is backed by an observation. Before concluding, gather evidence: " + "; ".join(tips) +
                ". Send final_report after at least one real observation.")

    UNDECIDED_RE = re.compile(r"belirlen(?:e)?(?:mi|em)|tespit edilem|kod mu|konfig\w* mu|code or config|cannot (?:tell|determine|decide)|"
                              r"unclear whether|not possible to (?:tell|determine)|ayırt edilem|ayirt edilem|netleştirilem|netlestirilem", re.I)

    def _source_unread(self, act: dict, n: int) -> bool:
        """The LLM gives up ("unknown" / "cannot tell code from configuration") although the case file or an inspection named a stored
        procedure / screen whose source was never read.  Refused once per run (round 2)."""
        if self.source_guard_used or n >= self.budget.max_steps:
            return False
        if self.executor.sources_read > 0 or not self.executor.known_sources:
            return False
        rep = axes.normalize(dict(act))
        text = " ".join(str(act.get(k) or "") for k in ("summary", "root_cause"))
        return rep.get("root_cause_type") == "unknown" or bool(self.UNDECIDED_RE.search(text))

    def _source_guard_text(self) -> str:
        srcs = [s.replace("\\", "/") for s in self.executor.known_sources][:4]
        return ("SOURCE NOT READ (refused once): your final_report is 'unknown' / cannot tell code from configuration, but the stored procedure or "
                "screen behind the symptom is known and its source was never opened. Before concluding use read_source on the relevant part "
                "(sources named so far: %s; give start_line/end_line around the calculation / the permission condition, <= 120 lines) and read the "
                "COLUMN PROVENANCE / UI GATING sections of the case file: they name the computing procedure and its input tables. Then query the "
                "inputs (e.g. the template that reads them) and decide on the evidence. A calculation that sums rows which do not exist is not a code "
                "defect: ask why the rows do not exist." % "; ".join(srcs))

    def _status_shallow(self) -> bool:
        """Ticket talks about a status / cancel / error, a work order is known, but no query looked at status history or
        transitions (baseline playbook queries count) and query budget is left."""
        if self.executor.query_count >= self.budget.max_queries:
            return False
        if not playbooks.is_status_ticket(self.inc, playbooks.GUARD_RE):
            return False
        ents = self.inc.get("entities") or {}
        if not (ents.get("work_order_ids") or ents.get("work_order_ids_strong") or ents.get("work_case_ids")):
            return False
        dry = self.executor.mode != "live"
        for q in self.executor.queries:
            touched = q["query_id"] in playbooks.STATUS_QIDS or (
                q["query_id"] == "adhoc" and re.search(r"WorkOrderGpsInfo|WorkOrderJsonLog|StatusStep", q.get("sql") or "", re.I))
            if touched and (q.get("executed") or dry):
                return False
        return True

    def _early_stop_text(self, shallow: bool = False) -> str:
        if shallow:
            return ("EARLY STOP REFUSED (once): the ticket is about a status / cancel / error, but no executed query has looked at the "
                    "status history or the allowed transitions. Before concluding run wo_status_timeline, wo_field_changes (who/when "
                    "changed the status, incl. direct DB updates) and wo_status_transition_check / status_step_check "
                    "(is the transition configured for the tenant?) with the TenantId from RESOLVED WORK ORDERS. "
                    "A work order that is already in the requested status does NOT prove user_error: find out how it got there.")
        zero = [q for q in self.executor.queries if q.get("executed") and q.get("row_count") == 0]
        tips = ["%s returned 0 rows (params %s): try another identifier (wo_locate, ExternalId), the other region, or a related template"
                % (q["query_id"], json.dumps(q.get("params") or {}, ensure_ascii=False)) for q in zero]
        if not tips:
            tips.append("run the templates named in the 'next:' lines of the queries already run (e.g. wo_header, wo_status_timeline, "
                        "wo_field_changes, wo_erp_sap, sap_rfc_rest_calls, outbound_queue_backlog) or inspect_code on the symptom")
        return ("EARLY STOP REFUSED (once): you used %d/%d queries and still have steps, but your final_report is 'unknown' or "
                "has confidence < 0.4. A 0-row result is a finding, not a reason to stop. You still have budget; before concluding try: %s. "
                "Send final_report later only if these are exhausted." % (self.executor.query_count, self.budget.max_queries, "; ".join(tips)))

    def _log_step(self, n: int, act: dict, obs_text: str, ok: bool, record: Optional[dict] = None) -> None:
        step = {"n": n, "action": act, "action_text": json.dumps(act, ensure_ascii=False), "observation": obs_text,
                "ok": ok, "status": self._status(n), "query_no": record["n"] if record else None}
        self.steps.append(step)
        if self.on_step:
            self.on_step(step)

    def _force_final(self) -> dict:
        self.forced = True
        tail = ("BUDGET EXHAUSTED. Reply NOW with action=final_report only, based on the evidence so far. "
                "If it is not sufficient, use root_cause_type \"unknown\" with low confidence and list what is missing in fix_steps.")
        try:
            act = self._ask(tail=tail, only_final=True)
            return actions.split_report(act)
        except JsonRepairFailed as e:
            self.warnings.append("forced final_report invalid: %s" % e)
            return self._fallback("Bütçe doldu ve model geçerli bir final_report üretemedi.")

    def _fallback(self, reason: str) -> dict:
        ev = [{"type": "query", "ref": q["query_id"], "finding": "Çalıştırma durumu: %s" % ("çalıştı" if q["executed"] else "çalıştırılmadı")}
              for q in self.executor.queries][:10]
        return {"summary": "Analiz tamamlanamadı: " + reason, "classification": "unknown", "root_cause_type": "unknown", "resolution_action": "unknown",
                "root_cause": "Belirsiz (otomatik analiz sonuç üretemedi).",
                "confidence": 0.0, "evidence": ev, "code_locations": [], "fix_steps": ["Analizi elle sürdürün; hazırlanan sorgular raporun 'Çalıştırılan sorgular' bölümünde."],
                "verification_query_ids": sorted({q["query_id"] for q in self.executor.queries if q["query_id"] != "adhoc"}),
                "fallback": True}

    # ------------------------------------------------------------ grounding
    def _ground(self, rep: dict) -> dict:
        """Check the LLM's claims against what really ran.  Never lets an unexecuted query count as evidence."""
        rep = dict(rep)
        by_id: Dict[str, List[dict]] = {}
        for q in self.executor.queries:
            by_id.setdefault(q["query_id"], []).append(q)
        evid = []
        tw = ticket_window.compute(self.inc, self.executor.queries)
        evidence = list(rep.get("evidence") or [])
        if tw:
            rep["ticket_window_changes"] = {k: v for k, v in tw.items() if k != "evidence_text"}
            evidence.insert(0, {"type": "query", "ref": "wo_field_changes", "finding": tw["evidence_text"], "deterministic": True})
        for ev in evidence:
            ev = dict(ev)
            if ev.get("type") == "query":
                ref = str(ev.get("ref", "")).split()[0] if ev.get("ref") else ""
                if str(ev.get("ref", "")).lower().startswith(("ticket s", "ticket_window")):
                    ref = "wo_field_changes"                    # the model cited the deterministic ticket-window section
                qs = by_id.get(ref)
                m_ad = re.match(r"adhoc#(\d+)$", ref)
                if m_ad:                                         # v2: ad-hoc SQL cited by query number
                    qs = [q for q in by_id.get("adhoc", []) if q["n"] == int(m_ad.group(1))]
                    ev["ref"] = "adhoc"
                if not qs:
                    ev["status"] = "not_run"
                    ev["note"] = "Bu sorgu bu analizde hiç çalıştırılmadı/render edilmedi."
                elif any(q["executed"] for q in qs):
                    ev["status"] = "executed"
                    ev["query_no"] = next(q["n"] for q in qs if q["executed"])
                else:
                    ev["status"] = "not_executed"
                    ev["query_no"] = qs[0]["n"]
                    ev["note"] = "Sorgu çalıştırılmadı (%s); bulgu doğrulanmış değil." % qs[0]["mode"]
            evid.append(ev)
        rep["evidence"] = evid
        executed = sum(1 for q in self.executor.queries if q["executed"])
        rep["executed_queries"] = executed
        rep["mode"] = self.executor.mode
        rep = axes.normalize(rep)
        calibration.apply(rep, self.executor.queries, inc=self.inc, code_reads=self.executor.read_paths)
        rep.setdefault("code_locations", [])
        rep.setdefault("verification_query_ids", [])
        return rep
