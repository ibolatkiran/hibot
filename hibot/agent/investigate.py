"""Step 3: the investigation loop (no tool calling: the LLM answers JSON, this code validates and executes).

    case file ──► LLM ──► action JSON ──► validate (repair loop) ──► Executor ──► observation ──► LLM ...
                                     └── final_report ──► ground against executed queries ──► report dict
Budgets: max_steps (actions), max_queries / max_adhoc_sql (inside Executor), max_context_chars (prompt shrinking).
When the steps run out the LLM is asked once for a forced final_report; if that fails a fallback report is built.
"""
from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from hibot.llm.client import LLMClient, LLMError
from hibot.llm.jsonio import JsonRepairFailed, complete_json

from . import actions, config
from .executor import Executor
from .prompting import build_case_prompt
from hibot import prompts

NO_QUERY_CONF_CAP = 0.6


class Investigation:
    def __init__(self, llm: LLMClient, incident: dict, matches: List[dict], code_ctx: List[dict], executor: Executor,
                 *, budget: Optional[config.Budget] = None, system_prompt: Optional[str] = None,
                 on_step: Optional[Callable[[dict], None]] = None, resolved_text: str = ""):
        self.llm, self.inc, self.matches, self.executor = llm, incident, matches, executor
        self.budget = budget or executor.budget
        self.system = system_prompt or prompts.system_prompt()
        self.on_step = on_step
        self.steps: List[dict] = []
        self.llm_calls = 0
        self.forced = False
        self.warnings: List[str] = []
        self.early_stop_used = False
        catalog = executor.registry.catalog_text(executor.region)
        self.case_prompt = build_case_prompt(incident, matches, code_ctx, catalog, mode=executor.mode,
                                             region=executor.region, budget=self.budget, resolved=resolved_text)

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
                if act["action"] == "final_report" and self._premature(act, n):
                    self.early_stop_used = True                    # once per run
                    self._log_step(n, act, self._early_stop_text(), False)
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
        if self.executor.query_count * 2 >= self.budget.max_queries:
            return False
        try:
            conf = float(act.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        return act.get("classification") == "unknown" or conf < 0.4

    def _early_stop_text(self) -> str:
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
                "If it is not sufficient, use classification \"unknown\" with low confidence and list what is missing in fix_steps.")
        try:
            act = self._ask(tail=tail, only_final=True)
            return actions.split_report(act)
        except JsonRepairFailed as e:
            self.warnings.append("forced final_report invalid: %s" % e)
            return self._fallback("Bütçe doldu ve model geçerli bir final_report üretemedi.")

    def _fallback(self, reason: str) -> dict:
        ev = [{"type": "query", "ref": q["query_id"], "finding": "Çalıştırma durumu: %s" % ("çalıştı" if q["executed"] else "çalıştırılmadı")}
              for q in self.executor.queries][:10]
        return {"summary": "Analiz tamamlanamadı: " + reason, "classification": "unknown", "root_cause": "Belirsiz (otomatik analiz sonuç üretemedi).",
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
        for ev in rep.get("evidence") or []:
            ev = dict(ev)
            if ev.get("type") == "query":
                ref = str(ev.get("ref", "")).split()[0] if ev.get("ref") else ""
                qs = by_id.get(ref)
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
        conf = float(rep.get("confidence", 0))
        if executed == 0 and conf > NO_QUERY_CONF_CAP:
            rep["confidence_original"] = conf
            rep["confidence"] = NO_QUERY_CONF_CAP
            rep["confidence_note"] = "Hiçbir sorgu çalıştırılmadığı için güven %.2f ile sınırlandı." % NO_QUERY_CONF_CAP
        rep.setdefault("code_locations", [])
        rep.setdefault("verification_query_ids", [])
        return rep
