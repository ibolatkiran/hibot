"""Unified loop (hibot/agent/loop_v2.py): JSON tool protocol, dispatch, budgets, context trimming, guards, run records, read_skill,
shared classification rules.  FakeLLM + fake tools/runners: no network, no DB."""
import json
import tempfile
import unittest
from pathlib import Path

from hibot import prompts
from hibot.agent import config, loop_v2, pipeline
from hibot.agent.code_context import CodeContext
from hibot.agent.executor import DRY, LIVE
from hibot.agent.loop_v2 import InvestigationV2, parse_turn
from hibot.db.runner import QueryResult
from hibot.llm.client import FakeLLM, LLMError
from hibot.tools import registry as treg
from hibot.tools import skills
from tools.eval import run_eval
from tools.eval import score as S

from tests.test_agent import incident, registry
from tests.test_playbooks import FC_COLS, FC_MANUAL, LOC_COLS, SM_DATA, TL_COLS, TL_ROWS, FakeRunner, inc_sm, mk

SQL_OK = "SELECT TOP 5 Id FROM Operation.WorkOrder WHERE TenantId = 5 AND Id = 12345678"


def fr(**kw):
    rep = {"summary": "ozet", "root_cause_type": "master_data", "resolution_action": "master_data_change", "root_cause": "kok neden",
           "confidence": 0.5, "evidence": [], "code_locations": [], "fix_steps": ["adim"], "verification_query_ids": []}
    rep.update(kw)
    return {"final_report": rep}


def tool(name, args=None, thought="t"):
    return {"thought": thought, "tool": name, "args": args or {}}


class SqlRunner(FakeRunner):
    def run_sql(self, sql, row_limit=100):
        self.log.append(("sql", sql))
        return QueryResult(ok=True, region=self.region, query_id="adhoc", columns=["Id"], rows=[[1], [2]], row_count=2, elapsed_ms=2)


def fake_tools(log=None, big=None):
    def fn(name, args):
        if log is not None:
            log.append((name, dict(args)))
        if name == "code_search":
            return {"ok": True, "items": [{"file": "SiriusApi/X.cs", "line": 3, "text": "x" * (big or 20)}]}
        if name == "read_file":
            return {"ok": True, "text": "line1\nline2"}
        if name == "boom":
            raise RuntimeError("kaput")
        return {"ok": True, "echo": name}
    return fn


def mkv2(script, mode=LIVE, budget=None, inc=None, tool_fn=None, data=None, runner_cls=SqlRunner, **kw):
    budget = budget or config.Budget.for_loop("v2")
    log = []
    from hibot.agent.executor import Executor
    ex = Executor(registry=registry(), code=CodeContext(use_graph=False), mode=mode, region="EU", budget=budget,
                  runner_factory=lambda r: runner_cls(r, log, SM_DATA if data is None else data))
    llm = FakeLLM(script, default=kw.pop("default", None))
    depth = kw.pop("depth", False)                                  # the self-check guard is tested on its own
    for k in ("plan", "critic", "use_checklist"):                   # round-3 features are tested on their own (default: off here)
        kw.setdefault(k, False)
    inv = InvestigationV2(llm, inc or incident(), [], [], ex, budget=budget, tool_fn=tool_fn or fake_tools(), **kw)
    inv.depth_check_used = not depth
    return llm, inv, ex, log


# ----------------------------------------------------------------------------- protocol

class ParseTests(unittest.TestCase):
    def test_single_tool(self):
        k, p, e = parse_turn({"thought": "x", "tool": "code_search", "args": {"pattern": "a"}})
        self.assertEqual((k, p, e), ("tools", [("code_search", {"pattern": "a"})], []))

    def test_missing_args_ok_and_multi_tool(self):
        k, p, _ = parse_turn({"tools": [{"tool": "cards_list"}, {"tool": "ticket", "args": {"key": "SM-1"}}]})
        self.assertEqual((k, p), ("tools", [("cards_list", {}), ("ticket", {"key": "SM-1"})]))

    def test_invalid_shapes(self):
        for bad in ([], "x", {"thought": "only"}, {"tool": ""}, {"tool": "a", "args": [1]}, {"tools": []}, {"tools": [{"args": {}}]}):
            self.assertEqual(parse_turn(bad)[0], "invalid", bad)

    def test_final_nested_flat_and_coercion(self):
        k, rep, e = parse_turn(fr(confidence="0,7", evidence=[{"type": "sql", "ref": "x", "finding": "f"}, "junk"], fix_steps="tek adim"))
        self.assertEqual((k, e), ("final", []))
        self.assertEqual(rep["confidence"], 0.7)
        self.assertEqual(rep["evidence"][0]["type"], "query")
        self.assertEqual(len(rep["evidence"]), 1)
        self.assertEqual(rep["fix_steps"], ["tek adim"])
        flat = dict(fr()["final_report"], action="final_report")
        self.assertEqual(parse_turn(flat)[0], "final")

    def test_final_needs_axes_or_legacy(self):
        rep = fr()["final_report"]
        del rep["root_cause_type"], rep["resolution_action"]
        self.assertEqual(parse_turn({"final_report": rep})[0], "invalid")
        rep["classification"] = "master_data"
        self.assertEqual(parse_turn({"final_report": rep})[0], "final")

    def test_only_final(self):
        self.assertEqual(parse_turn(tool("sql"), only_final=True)[0], "invalid")
        self.assertEqual(parse_turn(fr(), only_final=True)[0], "final")

    def test_long_strings_are_clipped_not_rejected(self):
        k, rep, _ = parse_turn(fr(summary="s" * 5000, root_cause="r" * 9000, fix_steps=["f" * 2000]))
        self.assertEqual(k, "final")
        self.assertEqual((len(rep["summary"]), len(rep["root_cause"]), len(rep["fix_steps"][0])), (1500, 3000, 800))


class RepairTests(unittest.TestCase):
    def test_prose_fence_and_trailing_comma_are_repaired_locally(self):
        txt = 'Tamam, sorguyu calistiriyorum.\n```json\n{"thought": "a", "tool": "code_search", "args": {"pattern": "x",},}\n```'
        llm, inv, ex, _ = mkv2([txt, fr()])
        out = inv.run()
        self.assertEqual(out["tool_calls"], 1)
        self.assertEqual(llm.calls and len(llm.calls), 2)          # no extra repair call needed
        self.assertEqual(out["report"]["root_cause_type"], "master_data")

    def test_invalid_reply_gets_repair_prompt_then_succeeds(self):
        llm, inv, ex, _ = mkv2(["I think the problem is X", {"nothing": 1}, tool("cards_list"), fr()])
        out = inv.run()
        self.assertEqual(out["tool_calls"], 1)
        self.assertEqual(out["llm_calls"], 4)                       # 2 repairs on the first turn, then 2 normal turns
        self.assertIn("invalid", llm.calls[1]["messages"][-1]["content"])
        self.assertIn("invalid", llm.calls[2]["messages"][-1]["content"])

    def test_json_repair_failure_gives_fallback_report(self):
        llm, inv, ex, _ = mkv2(["nope", "nope", "nope"])
        out = inv.run()
        self.assertTrue(out["report"]["fallback"])
        self.assertEqual(out["report"]["classification"], "unknown")
        self.assertTrue(any("invalid" in w for w in out["warnings"]))

    def test_transport_error_gives_fallback(self):
        class Boom(FakeLLM):
            def complete(self, *a, **k):
                raise LLMError("down")
        _, inv, _, _ = mkv2([])
        inv.llm = Boom()
        out = inv.run()
        self.assertTrue(out["report"]["fallback"])
        self.assertTrue(any("transport" in w for w in out["warnings"]))


# ----------------------------------------------------------------------------- dispatch

class DispatchTests(unittest.TestCase):
    def test_generic_tool_goes_through_tool_fn_and_is_logged(self):
        calls = []
        llm, inv, ex, _ = mkv2([tool("code_search", {"pattern": "Foo", "max_results": "3"}), fr()], tool_fn=fake_tools(calls))
        out = inv.run()
        self.assertEqual(calls, [("code_search", {"pattern": "Foo", "max_results": 3})])      # integer coerced by the registry validator
        s = out["steps"][0]
        self.assertTrue(s["ok"])
        self.assertEqual(s["calls"][0]["tool"], "code_search")
        self.assertIn("SiriusApi/X.cs", s["observation"])
        obs = llm.calls[1]["messages"][-1]["content"]                # the observation goes back as a user message
        self.assertTrue(obs.startswith("OBSERVATION (step 1)"))
        self.assertIn("RESULT 1/1  code_search", obs)
        self.assertIn("Budget: step 1/22, tool calls 1/30", obs)

    def test_unknown_tool_and_bad_args_are_error_observations(self):
        llm, inv, ex, _ = mkv2([tool("hack_the_db"), tool("code_search", {"nope": 1}), tool("read_file", {}), fr()])
        out = inv.run()
        texts = [s["calls"][0]["text"] for s in out["steps"][:3]]
        self.assertIn("unknown tool", texts[0])
        self.assertIn("code_search", texts[0])                       # lists the real tool names
        self.assertIn("unknown argument", texts[1])
        self.assertIn("missing required", texts[2])
        self.assertIn("Usage:", texts[2])
        self.assertTrue(all(not s["ok"] for s in out["steps"][:3]))
        self.assertEqual(out["tool_calls"], 3)                       # failed calls count against the tool budget

    def test_params_sent_as_json_text_are_accepted(self):
        _, inv, ex, log = mkv2([tool("run_template", {"query_id": "wo_locate", "params": '{"Number": "22811628"}'}), fr()])
        out = inv.run()
        self.assertTrue(out["queries"][0]["executed"])
        self.assertEqual(log[0], ("wo_locate", {"Number": "22811628"}))
        bad = treg.call_tool("run_template", {"query_id": "wo_locate", "params": "{oops"})
        self.assertFalse(bad["ok"])

    def test_tool_exception_does_not_kill_the_loop(self):
        treg_boom = treg.Tool("boom", lambda: None, "d", "w", {})
        treg.BY_NAME["boom"] = treg_boom
        try:
            _, inv, _, _ = mkv2([tool("boom"), fr()])
            out = inv.run()
        finally:
            del treg.BY_NAME["boom"]
        self.assertIn("kaput", out["steps"][0]["calls"][0]["text"])
        self.assertFalse(out["steps"][0]["ok"])
        self.assertFalse(out["report"].get("fallback"))

    def test_multi_tool_turn_and_cap_of_three(self):
        calls = []
        multi = {"thought": "t", "tools": [{"tool": "cards_list"}, {"tool": "ticket", "args": {"key": "SM-1"}},
                                            {"tool": "similar_tickets", "args": {"text_or_key": "x"}}, {"tool": "cards_match", "args": {"text_or_key": "x"}}]}
        llm, inv, ex, _ = mkv2([multi, fr()], tool_fn=fake_tools(calls))
        out = inv.run()
        self.assertEqual([c[0] for c in calls], ["cards_list", "ticket", "similar_tickets"])
        self.assertEqual(out["tool_calls"], 3)
        self.assertIn("at most 3 tools", out["steps"][0]["note"])
        self.assertEqual(llm.calls[1]["messages"][-1]["content"].count("RESULT "), 3)

    def test_duplicate_call_is_not_executed_again(self):
        calls = []
        _, inv, _, _ = mkv2([tool("cards_list"), tool("cards_list"), fr()], tool_fn=fake_tools(calls))
        out = inv.run()
        self.assertEqual(len(calls), 1)
        self.assertIn("DUPLICATE", out["steps"][1]["calls"][0]["text"])
        self.assertEqual(out["tool_calls"], 1)

    def test_large_tool_result_is_truncated_with_marker(self):
        b = config.Budget.for_loop("v2", max_steps=3)
        b.max_tool_obs_chars = 500
        _, inv, _, _ = mkv2([tool("code_search", {"pattern": "x"}), fr()], budget=b, tool_fn=fake_tools(big=5000))
        out = inv.run()
        t = out["steps"][0]["calls"][0]["text"]
        self.assertLessEqual(len(t), 700)
        self.assertIn("truncated", t)

    def test_run_template_goes_through_executor_and_is_recorded(self):
        llm, inv, ex, log = mkv2([tool("run_template", {"query_id": "wo_locate", "params": {"Number": "22811628"}}), fr()])
        out = inv.run()
        q = out["queries"][0]
        self.assertEqual((q["query_id"], q["region"], q["executed"], q["row_count"], q["kind"]), ("wo_locate", "EU", True, 1, "template"))
        self.assertEqual(q["columns"], LOC_COLS)
        self.assertEqual(log[0][0], "wo_locate")
        self.assertEqual(out["steps"][0]["query_no"], 1)
        self.assertIn("EXECUTED, 1 rows", out["steps"][0]["observation"])
        self.assertIn("how to read", out["steps"][0]["observation"])

    def test_sql_tool_is_validated_and_recorded_as_adhoc(self):
        _, inv, ex, log = mkv2([tool("sql", {"query": SQL_OK}), tool("sql", {"query": "DELETE FROM Operation.WorkOrder"}), fr()])
        out = inv.run()
        self.assertEqual(out["queries"][0]["query_id"], "adhoc")
        self.assertEqual(out["queries"][0]["row_count"], 2)
        self.assertEqual(ex.adhoc_count, 1)
        self.assertIn("REJECTED", out["steps"][1]["calls"][0]["text"])
        self.assertEqual(len(out["queries"]), 1)                     # the rejected statement never reached the DB layer

    def test_dry_run_is_not_executed(self):
        _, inv, ex, log = mkv2([tool("run_template", {"query_id": "wo_locate", "params": {"Number": "1"}}), fr()], mode=DRY)
        out = inv.run()
        self.assertFalse(out["queries"][0]["executed"])
        self.assertEqual(log, [])
        self.assertIn("NOT EXECUTED", out["steps"][0]["observation"])

    def test_read_file_marks_source_read(self):
        _, inv, ex, _ = mkv2([tool("read_file", {"path": "dbo.X"}), fr()])
        inv.run()
        self.assertEqual(ex.sources_read, 1)

    def test_default_dispatch_uses_the_real_registry(self):
        _, inv, _, _ = mkv2([tool("read_skill", {"name": "value_provenance"}), tool("read_skill", {"name": "nope"}), fr()], tool_fn=None)
        inv.tool_fn = None
        out = inv.run()
        self.assertIn("LabourCost", out["steps"][0]["calls"][0]["text"])
        self.assertIn("unknown skill", out["steps"][1]["calls"][0]["text"])


# ----------------------------------------------------------------------------- budgets

class BudgetTests(unittest.TestCase):
    def test_step_budget_forces_final(self):
        b = config.Budget.for_loop("v2", max_steps=2)
        llm, inv, ex, _ = mkv2([tool("cards_list"), tool("cards_match", {"text_or_key": "x"}), fr(root_cause_type="unknown", resolution_action="unknown")], budget=b)
        out = inv.run()
        self.assertTrue(out["forced_final"])
        self.assertEqual(len(out["steps"]), 2)
        self.assertEqual(len(llm.calls), 3)
        last = llm.calls[2]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertIn("BUDGET EXHAUSTED", last["content"])
        self.assertEqual([m["role"] for m in llm.calls[2]["messages"]].count("user"), 3)      # tail merged, never two user messages in a row
        self.assertIn("MUST be", out["steps"][1]["status"])

    def test_tool_call_budget_forces_final(self):
        b = config.Budget.for_loop("v2", max_tool_calls=2)
        llm, inv, _, _ = mkv2([tool("cards_list"), tool("ticket", {"key": "SM-1"}), fr()], budget=b)
        out = inv.run()
        self.assertTrue(out["forced_final"])
        self.assertEqual(out["tool_calls"], 2)
        self.assertEqual(len(llm.calls), 3)                          # the third call is the forced final (no further tool turn was asked for)
        self.assertIn("BUDGET EXHAUSTED", llm.calls[2]["messages"][-1]["content"])

    def test_forced_final_rejects_tool_call_then_falls_back(self):
        b = config.Budget.for_loop("v2", max_steps=1)
        _, inv, _, _ = mkv2([tool("cards_list"), tool("cards_list", {}), tool("x"), tool("y")], budget=b)
        out = inv.run()
        self.assertTrue(out["forced_final"] and out["report"]["fallback"])

    def test_for_loop_defaults(self):
        v1, v2 = config.Budget.for_loop("v1"), config.Budget.for_loop("v2", max_steps=None, max_queries=30)
        self.assertEqual((v1.max_steps, v1.max_queries), (8, 10))
        self.assertEqual((v2.max_steps, v2.max_queries, v2.max_tool_calls), (22, 30, 30))


# ----------------------------------------------------------------------------- context

class ContextTests(unittest.TestCase):
    def test_old_observations_are_shortened_but_notebook_and_case_file_stay(self):
        b = config.Budget.for_loop("v2", max_steps=10, max_context_chars=9000)
        script = [tool("code_search", {"pattern": "p%d" % i}, thought="FACT-%d found" % i) for i in range(6)] + [fr()]
        llm, inv, ex, _ = mkv2(script, budget=b, tool_fn=fake_tools(big=3000))
        out = inv.run()
        self.assertGreater(inv.trim_events, 0)
        msgs = llm.calls[-1]["messages"]
        self.assertEqual(msgs[1]["content"], inv.case_prompt)       # case file intact
        users = [m["content"] for m in msgs[2:] if m["role"] == "user"]
        asst = [m["content"] for m in msgs[2:] if m["role"] == "assistant"]
        self.assertTrue(all("FACT-%d found" % i in asst[i] for i in range(6)))          # thoughts never trimmed
        self.assertTrue(any("older observation shortened" in u or "observation omitted" in u for u in users[:-2]))
        self.assertNotIn("shortened", users[-1])                    # newest two observations intact
        self.assertNotIn("shortened", users[-2])
        self.assertEqual(out["steps"][0]["calls"][0]["text"].count("x" * 3000), 1)          # the run record keeps the full text

    def test_small_context_is_not_touched(self):
        llm, inv, _, _ = mkv2([tool("cards_list"), fr()])
        inv.run()
        self.assertEqual(inv.trim_events, 0)


# ----------------------------------------------------------------------------- guards

WEAK = fr(root_cause_type="unknown", resolution_action="unknown", confidence=0.2)


class GuardTests(unittest.TestCase):
    def test_early_stop_refused_once(self):
        llm, inv, ex, _ = mkv2([WEAK, WEAK])
        out = inv.run()
        self.assertEqual(len(out["steps"]), 2)
        self.assertIn("EARLY STOP REFUSED", out["steps"][0]["note"])
        self.assertEqual(out["report"]["root_cause_type"], "unknown")        # second weak answer is accepted
        self.assertIn("EARLY STOP REFUSED", llm.calls[1]["messages"][-1]["content"])

    def test_shallow_status_refuses_even_confident_answer_once(self):
        inc = inc_sm()
        llm, inv, ex, _ = mkv2([fr(root_cause_type="user_error", resolution_action="explain_to_user", confidence=0.9), fr(root_cause_type="user_error", resolution_action="explain_to_user", confidence=0.9)], inc=inc)
        out = inv.run()
        self.assertIn("status history", out["steps"][0]["note"])
        self.assertEqual(len(out["steps"]), 2)

    def test_shallow_guard_satisfied_by_status_query(self):
        inc = inc_sm()
        c = fr(root_cause_type="config", resolution_action="data_fix", confidence=0.8)
        _, inv, ex, _ = mkv2([tool("run_template", {"query_id": "wo_status_timeline", "params": {"tenant_id": 68, "work_order_id": 22811628}}), c], inc=inc)
        out = inv.run()
        self.assertEqual(len(out["steps"]), 2)
        self.assertEqual(out["report"]["root_cause_type"], "config")

    def test_source_guard_once_then_read_file_unlocks(self):
        inc = incident()
        _, inv, ex, _ = mkv2([WEAK, WEAK], inc=inc)
        ex.known_sources.append("X/StoredProcedures/dbo.Calc.sql")
        ex.query_count = 9                                          # early stop inactive (fewer than 3 queries left in a 10 budget)
        inv.budget.max_queries = 10
        out = inv.run()
        self.assertIn("SOURCE NOT READ", out["steps"][0]["note"])
        self.assertIn("read_file", out["steps"][0]["note"])
        self.assertNotIn("read_source", out["steps"][0]["note"])
        self.assertEqual(len(out["steps"]), 2)

    def test_source_guard_not_triggered_after_read(self):
        _, inv, ex, _ = mkv2([tool("read_file", {"path": "dbo.Calc"}), WEAK])
        ex.known_sources.append("X/StoredProcedures/dbo.Calc.sql")
        ex.query_count = 9
        inv.budget.max_queries = 10
        out = inv.run()
        self.assertEqual(len(out["steps"]), 2)
        self.assertNotIn("SOURCE NOT READ", out["steps"][1]["note"])

    def test_guards_inactive_on_the_last_step(self):
        b = config.Budget.for_loop("v2", max_steps=1)
        _, inv, _, _ = mkv2([WEAK], budget=b)
        out = inv.run()
        self.assertEqual(len(out["steps"]), 1)
        self.assertFalse(out["forced_final"])


# ----------------------------------------------------------------------------- evidence refs / run records

class DepthCheckTests(unittest.TestCase):
    def test_quick_final_gets_one_self_check_then_is_accepted(self):
        llm, inv, ex, _ = mkv2([tool("read_skill", {"name": "value_provenance"}), fr(), fr(summary="ikinci")], depth=True)
        out = inv.run()
        self.assertEqual(len(out["steps"]), 3)
        self.assertIn("SELF-CHECK", out["steps"][1]["note"])
        self.assertIn("column_writers", out["steps"][1]["note"])
        self.assertEqual(out["report"]["summary"], "ikinci")
        self.assertIn("SELF-CHECK", llm.calls[2]["messages"][-1]["content"])

    def test_skill_reads_do_not_count_as_investigation(self):
        _, inv, _, _ = mkv2([tool("read_skill", {"name": "value_provenance"}), tool("cards_list"), fr()], depth=True)
        inv.run()
        self.assertEqual(inv.beyond_case_calls, 1)
        self.assertTrue(inv.depth_check_used)

    def test_two_real_calls_skip_the_check(self):
        _, inv, _, _ = mkv2([tool("cards_list"), tool("ticket", {"key": "SM-1"}), fr()], depth=True)
        out = inv.run()
        self.assertEqual(len(out["steps"]), 3)
        self.assertFalse(inv.depth_check_used)

    def test_supported_confirmed_card_skips_the_check(self):
        card = "## BİLİNEN SORUN KARTI\nKart: x [confirmed, güven 0.90] t\nDOĞRULAMA SONUCU: DESTEKLİYOR"
        _, inv, _, _ = mkv2([fr()], depth=True, card_text=card)
        out = inv.run()
        self.assertEqual(len(out["steps"]), 1)

    def test_draft_card_does_not_skip(self):
        card = "## BİLİNEN SORUN KARTI\nKart: x [draft, güven 0.90] t\nDOĞRULAMA SONUCU: DESTEKLİYOR"
        _, inv, _, _ = mkv2([fr(), fr()], depth=True, card_text=card)
        self.assertEqual(len(inv.run()["steps"]), 2)


class EvidenceGuardTests(unittest.TestCase):
    def test_zero_evidence_final_is_refused_once_with_suggestions(self):
        _, inv, _, _ = mkv2([fr(), fr(summary="ikinci")])
        out = inv.run()
        self.assertEqual(len(out["steps"]), 2)
        self.assertIn("NO EVIDENCE GATHERED", out["steps"][0]["note"])
        self.assertIn("wo_locate", out["steps"][0]["note"])
        self.assertEqual(out["report"]["summary"], "ikinci")                # accepted the second time (once per run)

    def test_read_skill_alone_is_not_evidence(self):
        _, inv, _, _ = mkv2([tool("read_skill", {"name": "value_provenance"}), fr(), fr()])
        out = inv.run()
        self.assertIn("NO EVIDENCE GATHERED", out["steps"][1]["note"])

    def test_one_investigation_call_unlocks_the_final_report(self):
        _, inv, _, _ = mkv2([tool("cards_list"), fr()])
        out = inv.run()
        self.assertEqual(len(out["steps"]), 2)
        self.assertFalse(inv.evidence_guard_used)

    def test_supported_confirmed_card_is_exempt(self):
        card = "## BİLİNEN SORUN KARTI\nKart: x [confirmed, güven 0.90] t\nDOĞRULAMA SONUCU: DESTEKLİYOR"
        _, inv, _, _ = mkv2([fr()], card_text=card)
        self.assertEqual(len(inv.run()["steps"]), 1)

    def test_draft_card_is_not_exempt(self):
        card = "## BİLİNEN SORUN KARTI\nKart: x [draft, güven 0.90] t\nDOĞRULAMA SONUCU: DESTEKLİYOR"
        _, inv, _, _ = mkv2([fr(), fr()], card_text=card)
        self.assertEqual(len(inv.run()["steps"]), 2)


class RefTests(unittest.TestCase):
    def test_refs_are_mapped_to_executed_query_ids(self):
        ev = [{"type": "query", "ref": "run_template wo_locate", "finding": "f"}, {"type": "query", "ref": "#2", "finding": "f"},
              {"type": "sql", "ref": "sql", "finding": "f"}, {"type": "query", "ref": "wo_header", "finding": "f"}]
        _, inv, ex, _ = mkv2([tool("run_template", {"query_id": "wo_locate", "params": {"Number": "22811628"}}), tool("sql", {"query": SQL_OK}),
                              fr(evidence=ev, confidence=0.7)])
        out = inv.run()
        e = out["report"]["evidence"]
        self.assertEqual([x["ref"] for x in e], ["wo_locate", "adhoc", "adhoc", "wo_header"])
        self.assertEqual([x["status"] for x in e], ["executed", "executed", "executed", "not_run"])


class PipelineV2Tests(unittest.TestCase):
    def setUp(self):                                                # the real knowledge/cards change with reviews: isolate the tests from them
        import os
        from unittest import mock
        self._env = mock.patch.dict(os.environ, {"HIBOT_CARDS_DIR": tempfile.mkdtemp(), "HIBOT_V2_PLAN": "0", "HIBOT_V2_CRITIC": "0", "HIBOT_V2_CHECKLIST": "0"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _run(self, script, **kw):
        log = []
        d = tempfile.mkdtemp()
        run = pipeline.analyze("SM-2139009", None, llm=FakeLLM(script), incident=inc_sm(), mode=LIVE, region="EU", loop="v2",
                               code=CodeContext(use_graph=False), registry=registry(), runs_base=Path(d),
                               runner_factory=lambda r: SqlRunner(r, log, SM_DATA), **kw)
        return run, log

    def test_run_record_is_scorer_compatible(self):
        script = [{"thought": "t", "tools": [{"tool": "run_template", "args": {"query_id": "status_step_check", "params": {"tenant_id": 68, "from_status_id": 329, "to_status_id": 314}}},
                                              {"tool": "read_file", "args": {"path": "dbo.X"}}]},
                  fr(root_cause_type="config", resolution_action="data_fix", confidence=0.8,
                     evidence=[{"type": "query", "ref": "status_step_check", "finding": "329->314 yok"}])]
        script.append(script[1])                                       # the one-time self-check (only 1 successful call) re-asks
        run, log = self._run(script)
        self.assertEqual(run["loop"], "v2")
        self.assertEqual(run["tool_calls"], 2)
        self.assertEqual([q["query_id"] for q in run["queries"]][:2], ["wo_locate", "wo_header"])      # deterministic preflight + playbook first
        rep = run["report"]
        self.assertTrue(rep["ticket_window_changes"]["manual_fix"]["suspected"])
        self.assertEqual(rep["classification"], "data_correction")
        self.assertIn("confidence_raw", rep)
        self.assertTrue(run["preflight"]["resolved"])
        d = Path(run["run_dir"])
        for f in ("steps.jsonl", "queries.json", "report.json", "report.md", "incident.json"):
            self.assertTrue((d / f).exists(), f)
        steps = [json.loads(x) for x in (d / "steps.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(steps[0]["calls"][0]["tool"], "run_template")
        qs = json.loads((d / "queries.json").read_text(encoding="utf-8"))
        self.assertTrue(all({"query_id", "params", "executed", "rows", "n"} <= set(q) for q in qs))
        rj = json.loads((d / "report.json").read_text(encoding="utf-8"))
        self.assertIn("preflight", rj)
        self.assertIn("ticket_window_changes", rj["report"])
        self.assertIn("## Ticket süresince iş emrinde olan değişiklikler", run["markdown"])
        # the unchanged scorer takes the record of run_eval.summarize_run
        item = {"key": "SM-2139009", "split": "dev", "stratum": "status_footprint", "expected_classification": "config",
                "label_confidence": "high", "expected_work_order_ids": [22811628], "expected_tenant_id": 68,
                "expected_findings": [{"type": "wo_resolved", "work_order_id": 22811628, "tenant_id": 68}, {"type": "manual_fix_detected", "from": 329, "to": 314},
                                      {"type": "query_used", "query_id": "status_step_check"}]}
        rec = run_eval.summarize_run(run, 12.0, item)
        self.assertEqual((rec["loop"], rec["tool_calls"]), ("v2", 2))
        sc = S.score_ticket(item, rec)
        self.assertTrue(sc["wo_ok"] and sc["tenant_ok"])
        self.assertEqual(sc["findings_hit"], 3)
        self.assertEqual(sc["tool_calls"], 2)
        self.assertEqual(S.aggregate([sc])["overall"]["avg_tool_calls"], 2)

    def test_v1_default_unchanged(self):
        d = tempfile.mkdtemp()
        from tests.test_agent import final
        run = pipeline.analyze("SM-2139009", None, llm=FakeLLM([final(classification="config", confidence=0.5)]), incident=inc_sm(), mode=LIVE,
                               region="EU", code=CodeContext(use_graph=False), registry=registry(), runs_base=Path(d),
                               runner_factory=lambda r: SqlRunner(r, [], SM_DATA))
        self.assertEqual(run["loop"], "v1")

    def test_v2_first_message_is_the_case_file_with_v2_wording(self):
        llm = FakeLLM([fr()])
        d = tempfile.mkdtemp()
        pipeline.analyze("SM-2139009", None, llm=llm, incident=inc_sm(), mode=LIVE, region="EU", loop="v2", code=CodeContext(use_graph=False),
                         registry=registry(), runs_base=Path(d), runner_factory=lambda r: SqlRunner(r, [], SM_DATA))
        m = llm.calls[0]["messages"]
        self.assertEqual([x["role"] for x in m], ["system", "user"])
        case = m[1]["content"]
        for needle in ("RESOLVED WORK ORDERS", "BASELINE EVIDENCE", "run_template tool", "tool calls", '"final_report"'):
            self.assertIn(needle, case)
        self.assertNotIn("Answer with your first action", case)

    def test_bad_loop_rejected(self):
        with self.assertRaises(ValueError):
            pipeline.analyze("SM-1", None, llm=FakeLLM([]), incident=incident(), loop="v3")


# ----------------------------------------------------------------------------- read_skill + prompts + format

class SkillAndPromptTests(unittest.TestCase):
    def test_read_skill_is_a_registry_tool(self):
        self.assertIn("read_skill", treg.BY_NAME)
        self.assertIn("read_skill(name)", treg.tool_catalog())
        r = treg.call_tool("read_skill", {"name": "value_provenance"})
        self.assertTrue(r["ok"])
        self.assertIn("provenance", r["text"].lower())
        self.assertTrue(treg.call_tool("read_skill", {"name": "value_provenance.md"})["ok"])
        bad = treg.call_tool("read_skill", {"name": "../../CLAUDE"})
        self.assertFalse(bad["ok"])
        self.assertIn("investigation_protocol", bad["available"])
        self.assertFalse(treg.call_tool("read_skill", {})["ok"])
        self.assertNotIn("README", skills.skill_names())

    def test_v2_system_prompt_parts(self):
        s = prompts.v2_system_prompt()
        for needle in ("Investigation protocol", "Skills index", "TOOLS (reply with JSON", "read_skill(name)", "Classification rules",
                       "KNOWN-CARD RULE", "OUTPUT PROTOCOL", '"final_report"', "change_request", "route_to_backlog", "SNAPSHOT CAVEAT", "NOT evidence of user_error"):
            self.assertTrue(needle in s, "missing in v2 system prompt: " + needle)
        self.assertNotIn("@@", s)
        self.assertLess(len(s), 30000)

    def test_classification_rules_cover_the_decisions(self):
        t = prompts.classification_rules()
        for needle in ("DELIBERATE RESTRICTION", "user_error / explain_to_user", "config AND resolution_action data_fix", "HARD-CODED LIST", "change_request / route_to_backlog",
                       "MISSING SETUP DATA", "master_data / master_data_change", "OTHER-SYSTEM STATE", "escalate_other_system", "SNAPSHOT CAVEAT",
                       "NO CHANGE IN THE TICKET WINDOW", "KNOWN-CARD RULE", "CONFIRMED card", "DRAFT card", "ADOPT"):
            self.assertIn(needle, t)

    def test_claude_prompt_has_the_same_rules(self):
        from hibot.brains import claude_agent
        s = claude_agent.build_system()
        self.assertIn(prompts.classification_rules(), s)
        self.assertIn("KNOWN-CARD RULE", s)
        self.assertIn("route_to_backlog", s)
        names = [t["name"] for t in claude_agent.build_tools()]
        self.assertEqual(names, [t.name for t in treg.TOOLS])
        rs = next(t for t in claude_agent.build_tools() if t["name"] == "read_skill")
        self.assertIn("value_provenance", rs["input_schema"]["properties"]["name"]["enum"])
        text, is_err = claude_agent.run_tool("read_skill", {"name": "access_request"})
        self.assertFalse(is_err)
        self.assertTrue(claude_agent.run_tool("read_skill", {"name": "zzz"})[1])

    def test_v2_format_selection(self):
        f = loop_v2.v2_format
        self.assertEqual(f({"HIBOT_LLM_FORMAT": "argpt"}), "argpt_chat")
        self.assertEqual(f({"HIBOT_LLM_FORMAT": "argpt_chat"}), "argpt_chat")
        self.assertEqual(f({"HIBOT_LLM_FORMAT": "openai"}), "openai")
        self.assertEqual(f({"HIBOT_LLM_FORMAT": "argpt", "HIBOT_LLM_FORMAT_V2": "raw"}), "raw")
        self.assertEqual(f({"HIBOT_LLM_URL": "https://x/api/public/text-completion"}), "argpt_chat")
        self.assertEqual(f({}), "openai")


if __name__ == "__main__":
    unittest.main()
