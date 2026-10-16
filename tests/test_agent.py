import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hibot.agent import actions, cards as cardmod, config, feedback, intake, pipeline, safety, store
from hibot.agent.code_context import CodeContext
from hibot.agent.demo import build_demo_llm
from hibot.agent.executor import DRY, LIVE, Executor, render_sql
from hibot.agent.investigate import Investigation
from hibot.agent.report import render_markdown
from hibot.db.runner import QueryResult
from hibot.db.templates import TemplateRegistry
from hibot.llm.client import FakeLLM, LLMError

ROOT = Path(__file__).resolve().parents[1]
HAVE_TICKETS = (ROOT / "knowledge" / "incidents" / "tickets_enriched.json").exists()
_REGISTRY = None


def registry():
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = TemplateRegistry.load()
    return _REGISTRY


def incident(**kw):
    inc = {"source": "text", "key": "SM-1", "summary": "Missing tenant for 7131232345 in Poland",
           "desc": "Product not found for model 7131232345", "comments": [], "similar": [], "group": None,
           "entities": {"sku_codes": ["7131232345"], "work_order_ids": ["12345678"]}, "problem_type": "master_data_request",
           "app": "portal", "country": "PL", "region_guess": "EU", "screen_hints": []}
    inc.update(kw)
    return inc


def final(**kw):
    rep = {"action": "final_report", "summary": "ozet", "classification": "master_data", "root_cause": "kok neden",
           "confidence": 0.5, "evidence": [], "code_locations": [], "fix_steps": ["adim"], "verification_query_ids": []}
    rep.update(kw)
    return rep


def make_executor(mode=DRY, budget=None, runner_factory=None, code=None):
    return Executor(registry=registry(), code=code or CodeContext(use_graph=False), mode=mode, region="EU",
                    budget=budget or config.Budget(), runner_factory=runner_factory)


def investigate(script, mode=DRY, budget=None, inc=None, matches=None, runner_factory=None, default=None):
    budget = budget or config.Budget()
    ex = make_executor(mode, budget, runner_factory)
    llm = FakeLLM(script, default=default)
    inv = Investigation(llm, inc or incident(), matches or [], [], ex, budget=budget)
    return llm, inv.run(), ex


WO_PARAMS = {"tenant_id": 5, "work_order_id": 12345678}


class ActionValidationTests(unittest.TestCase):
    def test_valid_actions(self):
        ok = [{"action": "run_query", "query_id": "wo_header", "params": WO_PARAMS, "region": "EU", "why": "x"},
              {"action": "run_sql", "sql": "SELECT 1 FROM Catalog.Tenant"},
              {"action": "inspect_code", "target": {"kind": "endpoint", "value": "X"}},
              {"action": "read_source", "path": "a/b.cs", "start_line": 1, "end_line": 5}, final()]
        for a in ok:
            self.assertEqual(actions.validate_action(a), [], a)

    def test_invalid_actions(self):
        bad = [[], {}, {"action": "drop_db"}, {"action": "run_query"}, {"action": "run_query", "query_id": "x", "region": "US"},
               {"action": "run_sql", "sql": "x"}, {"action": "inspect_code", "target": {"kind": "magic", "value": "a"}},
               {"action": "read_source", "path": "a", "start_line": 0}, final(classification="bug"), final(confidence=1.5),
               {k: v for k, v in final().items() if k != "root_cause"}]
        for a in bad:
            self.assertTrue(actions.validate_action(a), a)

    def test_only_final(self):
        self.assertTrue(actions.validate_action({"action": "run_query", "query_id": "x"}, only_final=True))
        self.assertEqual(actions.validate_action(final(), only_final=True), [])


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "SiriusApi"
        (self.root / "Sirius").mkdir(parents=True)
        (self.root / "node_modules").mkdir()
        (self.root / "Sirius" / "Ok.cs").write_text("\n".join("line %d" % i for i in range(1, 400)) + "\n", encoding="utf-8")
        (self.root / "Sirius" / "Conn.cs").write_text('var s = "x";\nvar password = "hunter2hunter2";\n', encoding="utf-8")
        for n in ("appsettings.json", "appsettings.Production.json", ".env", ".env.local", ".env-cmdrc.json", "translate.js",
                  "web.config", "cert.pfx"):
            (self.root / "Sirius" / n).write_text("secret", encoding="utf-8")
        (self.root / "node_modules" / "x.js").write_text("x", encoding="utf-8")
        self.outside = Path(self.tmp.name) / "other"
        self.outside.mkdir()
        (self.outside / "leak.cs").write_text("leak", encoding="utf-8")
        self.p = mock.patch.dict(os.environ, {"HIBOT_REPO_ROOTS": str(self.root)})
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_reads_allowed_file_with_line_cap(self):
        r = safety.read_source("SiriusApi/Sirius/Ok.cs", 10, 500, max_lines=20)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["start_line"], r["end_line"], r["truncated"]), (10, 29, True))
        self.assertIn("10| line 10", r["text"])
        r2 = safety.read_source(str(self.root / "Sirius" / "Ok.cs"))
        self.assertTrue(r2["ok"])

    def test_secret_files_denied(self):
        for n in ("appsettings.json", "appsettings.Production.json", ".env", ".env.local", ".env-cmdrc.json",
                  "translate.js", "web.config", "cert.pfx"):
            r = safety.read_source("SiriusApi/Sirius/" + n)
            self.assertFalse(r["ok"], n)
            self.assertIn("secret", r["error"], n)

    def test_traversal_and_outside_denied(self):
        for p in ("SiriusApi/../other/leak.cs", "SiriusApi/Sirius/../../other/leak.cs", str(self.outside / "leak.cs"),
                  "other/leak.cs", "C:\\Windows\\win.ini", "/etc/passwd", "", "SiriusApi/Sirius/missing.cs"):
            self.assertFalse(safety.read_source(p)["ok"], p)

    def test_denied_dir_and_suffix(self):
        self.assertFalse(safety.read_source("SiriusApi/node_modules/x.js")["ok"])
        (self.root / "Sirius" / "a.exe").write_text("x")
        self.assertFalse(safety.read_source("SiriusApi/Sirius/a.exe")["ok"])

    def test_credentials_scrubbed_from_output(self):
        r = safety.read_source("SiriusApi/Sirius/Conn.cs")
        self.assertTrue(r["ok"])
        self.assertNotIn("hunter2", r["text"])

    def test_symlink_escape_denied(self):
        link = self.root / "Sirius" / "link.cs"
        try:
            os.symlink(self.outside / "leak.cs", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        self.assertFalse(safety.read_source("SiriusApi/Sirius/link.cs")["ok"])

    def test_default_roots_cover_claude_md_repos(self):
        names = {Path(r).name for r in config.DEFAULT_REPO_ROOTS}
        self.assertTrue({"SiriusApi", "SiriusHeadOffice", "SiriusCallCenter", "SiriusServiceShopCallCenter", "SiriusTechnician",
                         "gfsm-europe"} <= names)


class ExecutorTests(unittest.TestCase):
    def test_dry_run_query_validates_and_renders(self):
        ex = make_executor()
        obs = ex.execute({"action": "run_query", "query_id": "wo_header", "params": WO_PARAMS})
        self.assertTrue(obs["ok"])
        self.assertIn("NOT EXECUTED", obs["text"])
        q = ex.queries[0]
        self.assertFalse(q["executed"])
        self.assertEqual(q["mode"], DRY)
        self.assertIn("12345678", q["sql_display"])
        self.assertNotIn("?", q["sql_display"])
        self.assertIn("?", q["sql"])                     # execution form still uses bound parameters

    def test_unknown_query_id_rejected_and_free(self):
        ex = make_executor()
        obs = ex.execute({"action": "run_query", "query_id": "drop_everything", "params": {}})
        self.assertFalse(obs["ok"])
        self.assertIn("REJECTED", obs["text"])
        self.assertIn("wo_header", obs["text"])          # lists known ids
        self.assertEqual((ex.query_count, ex.queries), (0, []))

    def test_bad_params_and_region_rejected(self):
        ex = make_executor()
        for a in ({"action": "run_query", "query_id": "wo_header", "params": {"work_order_id": 1}},          # tenant missing
                  {"action": "run_query", "query_id": "wo_header", "params": {"tenant_id": "1; DROP TABLE x", "work_order_id": 1}},
                  {"action": "run_query", "query_id": "wo_header", "params": {"tenant_id": 1, "work_order_id": 1, "evil": 1}},
                  {"action": "run_query", "query_id": "wo_debug_log", "params": WO_PARAMS, "region": "APAC"}):    # EU-only template
            obs = ex.execute(a)
            self.assertFalse(obs["ok"], a)
            self.assertIn("REJECTED", obs["text"])
        self.assertEqual(ex.query_count, 0)

    def test_unsafe_sql_rejected(self):
        ex = make_executor()
        for sql in ("DELETE FROM Operation.WorkOrder WHERE Id = 1", "SELECT 1; DROP TABLE Catalog.Tenant",
                    "EXEC sp_who", "SELECT * FROM sys.sql_logins", "UPDATE Catalog.Tenant SET Dsc = 'x'",
                    "SELECT Id FROM Catalog.NoSuchTable_xyz WHERE TenantId = 1"):
            obs = ex.execute({"action": "run_sql", "sql": sql})
            self.assertFalse(obs["ok"], sql)
            self.assertIn("REJECTED", obs["text"])
        self.assertEqual((ex.query_count, ex.adhoc_count), (0, 0))

    def test_pii_column_in_adhoc_sql_rejected(self):
        ex = make_executor()
        obs = ex.execute({"action": "run_sql", "sql": "SELECT TOP 5 Dsc FROM Catalog.Employee WHERE TenantId = 1"})
        self.assertFalse(obs["ok"], obs["text"])

    def test_adhoc_sql_ok_in_dry_run_gets_top(self):
        ex = make_executor()
        obs = ex.execute({"action": "run_sql", "sql": "SELECT Id, Dsc FROM Catalog.Tenant WHERE Id = 5"})
        self.assertTrue(obs["ok"], obs["text"])
        self.assertIn("TOP", ex.queries[0]["sql_display"].upper())
        self.assertEqual((ex.query_count, ex.adhoc_count, ex.queries[0]["kind"]), (1, 1, "adhoc"))

    def test_query_and_adhoc_budgets(self):
        ex = make_executor(budget=config.Budget(max_queries=2, max_adhoc_sql=1))
        sql = {"action": "run_sql", "sql": "SELECT Id FROM Catalog.Tenant WHERE Id = 5"}
        self.assertTrue(ex.execute(sql)["ok"])
        second = ex.execute(dict(sql, sql="SELECT Id FROM Catalog.Tenant WHERE Id = 6"))
        self.assertFalse(second["ok"])
        self.assertIn("ad-hoc SQL budget", second["text"])
        self.assertTrue(ex.execute({"action": "run_query", "query_id": "wo_header", "params": WO_PARAMS})["ok"])
        third = ex.execute({"action": "run_query", "query_id": "tenant_lookup", "params": {"country_code": "PL"}})
        self.assertFalse(third["ok"])
        self.assertIn("query budget exhausted", third["text"])
        self.assertEqual(ex.query_count, 2)

    def test_read_source_rejections_via_executor(self):
        ex = make_executor()
        for p in ("SiriusApi/Sirius/appsettings.json", "SiriusApi/../../Windows/win.ini", "d:\\repos\\SiriusApi\\.env"):
            obs = ex.execute({"action": "read_source", "path": p})
            self.assertFalse(obs["ok"], p)
            self.assertIn("REJECTED", obs["text"])

    def test_inspect_code_unavailable_kind_degrades(self):
        with mock.patch.dict(os.environ, {"HIBOT_KNOWLEDGE_DIR": tempfile.gettempdir() + "/nonexistent-hibot-k"}):
            ex = make_executor(code=CodeContext(use_graph=False))
            obs = ex.execute({"action": "inspect_code", "target": {"kind": "text", "value": "history"}})
        self.assertFalse(obs["ok"])
        self.assertIn("FAILED", obs["text"])

    def test_live_mode_uses_runner_and_truncates_rows_for_llm(self):
        calls = []

        class FakeRunner:
            def run_template(self, qid, params):
                calls.append((qid, params))
                rows = [[i, "name %d" % i, "x" * 500] for i in range(40)]
                return QueryResult(ok=True, region="EU", query_id=qid, columns=["Id", "Name", "Long"], rows=rows,
                                   row_count=40, elapsed_ms=7)

            def close(self):
                pass
        ex = make_executor(mode=LIVE, runner_factory=lambda region: FakeRunner())
        obs = ex.execute({"action": "run_query", "query_id": "wo_header", "params": WO_PARAMS})
        self.assertTrue(obs["ok"])
        self.assertEqual(calls[0][0], "wo_header")
        q = ex.queries[0]
        self.assertTrue(q["executed"])
        self.assertEqual(q["row_count"], 40)
        self.assertIn("EXECUTED, 40 rows", obs["text"])
        self.assertIn("25 more rows not shown", obs["text"])
        self.assertLessEqual(len(obs["text"]), ex.budget.max_obs_chars + 100)
        self.assertIn("how to read", obs["text"])

    def test_live_mode_runner_error_is_not_executed(self):
        class BadRunner:
            def run_template(self, qid, params):
                return QueryResult(ok=False, region="EU", query_id=qid, error="RunnerError: HIBOT_DB_EU is not set")

            def close(self):
                pass
        ex = make_executor(mode=LIVE, runner_factory=lambda r: BadRunner())
        obs = ex.execute({"action": "run_query", "query_id": "wo_header", "params": WO_PARAMS})
        self.assertFalse(obs["ok"])
        self.assertFalse(ex.queries[0]["executed"])
        self.assertEqual(ex.queries[0]["mode"], LIVE)

    def test_render_sql_ignores_question_marks_in_strings(self):
        self.assertEqual(render_sql("SELECT 'a?b', ? , ?, ?", [1, "it's", None]), "SELECT 'a?b', 1 , 'it''s', NULL")


class InvestigationTests(unittest.TestCase):
    def test_happy_path_steps_and_grounding(self):
        script = [
            {"action": "run_query", "query_id": "wo_header", "params": WO_PARAMS, "why": "look"},
            final(confidence=0.9, evidence=[{"type": "query", "ref": "wo_header", "finding": "x"},
                                            {"type": "query", "ref": "never_ran", "finding": "y"},
                                            {"type": "similar_case", "ref": "SM-2", "finding": "z"}]),
        ]
        llm, out, ex = investigate(script)
        rep = out["report"]
        self.assertEqual([s["action"]["action"] for s in out["steps"]], ["run_query", "final_report"])
        self.assertEqual(rep["evidence"][0]["status"], "not_executed")
        self.assertEqual(rep["evidence"][1]["status"], "not_run")
        self.assertEqual(rep["executed_queries"], 0)
        self.assertEqual(rep["confidence"], 0.6)                          # capped: nothing was executed
        self.assertIn("confidence_note", rep)
        llm.assert_prompt_contains("OBSERVATION (step 1)", call=1)
        llm.assert_prompt_contains("NOT EXECUTED", call=1)
        llm.assert_prompt_contains("wo_header", call=0)                   # catalog is in the first prompt
        llm.assert_prompt_contains("db mode: dry-run", call=0)

    def test_executed_queries_keep_confidence(self):
        class R:
            def run_template(self, qid, params):
                return QueryResult(ok=True, region="EU", query_id=qid, columns=["A"], rows=[[1]], row_count=1)

            def close(self):
                pass
        script = [{"action": "run_query", "query_id": "wo_header", "params": WO_PARAMS},
                  final(confidence=0.9, evidence=[{"type": "query", "ref": "wo_header", "finding": "x"}])]
        _, out, _ = investigate(script, mode=LIVE, runner_factory=lambda r: R())
        self.assertEqual(out["report"]["confidence"], 0.9)
        self.assertEqual(out["report"]["evidence"][0]["status"], "executed")

    def test_step_budget_forces_final_report(self):
        def respond(messages):
            if "BUDGET EXHAUSTED" in messages[-1]["content"]:
                return final(summary="forced")
            return {"action": "inspect_code", "target": {"kind": "endpoint", "value": "n%d" % len(messages)}}
        budget = config.Budget(max_steps=3)
        llm, out, _ = investigate([respond] * 10, budget=budget)
        self.assertEqual(len(out["steps"]), 3)
        self.assertTrue(out["forced_final"])
        self.assertEqual(out["report"]["summary"], "forced")
        self.assertEqual(len(llm.calls), 4)
        llm.assert_prompt_contains("MUST be final_report", call=3)

    def test_forced_final_invalid_gives_fallback(self):
        budget = config.Budget(max_steps=1)
        _, out, _ = investigate([], budget=budget, default={"action": "inspect_code", "target": {"kind": "endpoint", "value": "a"}})
        rep = out["report"]
        self.assertTrue(rep["forced_final"] if "forced_final" in rep else out["forced_final"])
        self.assertTrue(rep.get("fallback"))
        self.assertEqual((rep["classification"], rep["confidence"]), ("unknown", 0.0))

    def test_duplicate_action_not_reexecuted(self):
        q = {"action": "run_query", "query_id": "wo_header", "params": WO_PARAMS}
        _, out, ex = investigate([q, dict(q, why="again"), final()])
        self.assertEqual(ex.query_count, 1)
        self.assertIn("DUPLICATE", out["steps"][1]["observation"])

    def test_rejected_actions_are_fed_back_and_cost_steps(self):
        script = [{"action": "run_query", "query_id": "bogus", "params": {}},
                  {"action": "read_source", "path": "SiriusApi/Sirius/appsettings.json"},
                  final()]
        llm, out, ex = investigate(script)
        self.assertEqual(len(out["steps"]), 3)
        self.assertFalse(out["steps"][0]["ok"])
        self.assertFalse(out["steps"][1]["ok"])
        llm.assert_prompt_contains("REJECTED", call=2)
        self.assertEqual(ex.query_count, 0)

    def test_malformed_answer_is_repaired_without_consuming_a_step(self):
        script = ["sorry, let me think", {"action": "nope"}, final()]
        llm, out, _ = investigate(script)
        self.assertEqual(len(out["steps"]), 1)
        self.assertEqual(out["llm_calls"], 3)
        llm.assert_prompt_contains("previous answer was invalid", call=2)

    def test_unrepairable_output_gives_fallback_report(self):
        _, out, _ = investigate([], default="not json at all")
        self.assertTrue(out["report"]["fallback"])
        self.assertTrue(out["warnings"])

    def test_llm_transport_error_gives_fallback_report(self):
        def boom(messages):
            raise LLMError("HTTP 500")
        _, out, _ = investigate([boom])
        self.assertTrue(out["report"]["fallback"])
        self.assertIn("HTTP 500", out["warnings"][0])

    def test_old_observations_are_shortened_when_context_is_large(self):
        budget = config.Budget(max_context_chars=9000, max_steps=6)

        def respond(messages):
            n = sum(1 for m in messages if m["role"] == "assistant")
            if n >= 5:
                return final()
            return {"action": "inspect_code", "target": {"kind": "endpoint", "value": "ep%d" % n}}
        big = "B" * 3000
        ex = make_executor(budget=budget)
        ex.code.inspect = lambda kind, value, **kw: {"ok": True, "result": {"blob": big}, "backend": "t"}
        inv = Investigation(FakeLLM([respond] * 6), incident(), [], [], ex, budget=budget)
        inv.run()
        last = inv.llm.calls[-1]["messages"]
        self.assertIn("older observation shortened", "\n".join(m["content"] for m in last))

    def test_card_match_in_prompt(self):
        card = cardmod.load_cards(ROOT / "knowledge" / "cards") or [_card("c1")]
        inc = incident(summary="Missing tenant for SKU", desc="sku missing tenant product not found")
        matches = cardmod.match_cards(inc, [_card("tenant_card")])
        llm, _, _ = investigate([final()], inc=inc, matches=matches)
        llm.assert_prompt_contains("card tenant_card", call=0)
        llm.assert_prompt_contains("run_query product_sku_lookup", call=0)
        self.assertTrue(card)


def _card(cid="tenant_card", status="draft", **kw):
    c = {"id": cid, "title": "Missing tenant for SKU", "symptom_signature": {
        "problem_type": "master_data_request", "app": None, "keywords": ["missing tenant", "sku"], "entity_requirements": ["sku_codes"]},
        "root_cause": "SKU not assigned to tenant", "verification_queries": [
            {"query_id": "product_sku_lookup", "params_from_entities": {"Sku": "entities.sku_codes[0]", "TenantId": "$tenant"}}],
        "fix_steps": ["assign"], "code_locations": [], "confidence": 0.5, "status": status, "evidence_tickets": [],
        "created_by": "test", "history": []}
    c.update(kw)
    return c


class CardTests(unittest.TestCase):
    def test_validate_and_roundtrip(self):
        self.assertEqual(cardmod.validate_card(_card()), [])
        self.assertTrue(cardmod.validate_card(_card(status="maybe")))
        self.assertTrue(cardmod.validate_card(_card(cid="../evil")))
        with tempfile.TemporaryDirectory() as d:
            cardmod.save_card(_card(), Path(d))
            self.assertEqual([c["id"] for c in cardmod.load_cards(Path(d))], ["tenant_card"])
            (Path(d) / "broken.json").write_text("{not json", encoding="utf-8")
            (Path(d) / "wrongname.json").write_text(json.dumps(_card("other_id")), encoding="utf-8")
            self.assertEqual(len(cardmod.load_cards(Path(d))), 1)       # malformed / mismatching cards are skipped

    def test_card_path_traversal(self):
        with self.assertRaises(ValueError):
            cardmod.card_path("../../etc/passwd")

    def test_matching_scores_and_rejected_excluded(self):
        inc = incident()
        m = cardmod.match_cards(inc, [_card(), _card("rejected_card", status="rejected"),
                                      _card("other_card", title="x", symptom_signature={"problem_type": "access_request", "app": None, "keywords": ["password"], "entity_requirements": []})])
        self.assertEqual([x["card"]["id"] for x in m], ["tenant_card"])
        self.assertGreater(m[0]["score"], 0.6)
        inc2 = incident(entities={})                                       # missing required entity halves the score
        m2 = cardmod.match_cards(inc2, [_card()], min_score=0.0)
        self.assertEqual(m2[0]["missing_entities"], ["sku_codes"])
        self.assertLess(m2[0]["score"], m[0]["score"])

    def test_confirmed_outranks_draft(self):
        m = cardmod.match_cards(incident(), [_card("a_draft"), _card("b_conf", status="confirmed")])
        self.assertEqual(m[0]["card"]["id"], "b_conf")

    def test_resolve_queries(self):
        qs = cardmod.resolve_card_queries(_card(), incident())
        self.assertEqual(qs[0]["params"], {"Sku": "7131232345"})
        self.assertEqual(qs[0]["unresolved"], ["TenantId"])

    def test_shipped_example_cards(self):
        cs = cardmod.load_cards(ROOT / "knowledge" / "cards")
        if not cs:
            self.skipTest("no shipped cards (knowledge/**/*.json is git-ignored)")
        for c in cs:
            self.assertEqual(c["status"], "draft")
            self.assertIn("EXAMPLE", c["title"])
            reg = registry()
            for q in c["verification_queries"]:
                self.assertIn(q["query_id"], reg.ids())


class CodeContextTests(unittest.TestCase):
    def test_graph_adapter_calls_interface(self):
        class G:
            def trace_endpoint(self, e): return {"endpoint": e, "evidence": "g"}
            def trace_screen(self, app, s): return {"app": app, "screen": s}
            def trace_db_object(self, n): return {"obj": n}
            def search_error_text(self, t, limit): return [{"t": t, "limit": limit}]
            def search_text(self, t, app=None, limit=5): return [{"t": t, "app": app}]
        c = CodeContext(G())
        self.assertEqual(c.backend, "graph")
        self.assertEqual(c.inspect("endpoint", "X")["result"]["endpoint"], "X")
        self.assertEqual(c.inspect("screen", "S", app="crm")["result"], {"app": "crm", "screen": "S"})
        self.assertEqual(c.inspect("db_object", "dbo.F")["result"], {"obj": "dbo.F"})
        self.assertEqual(c.inspect("error_text", "e")["result"][0]["limit"], 5)
        self.assertEqual(c.inspect("text", "t", app="portal")["result"][0]["app"], "portal")
        self.assertFalse(c.inspect("weird", "t")["ok"])
        self.assertFalse(c.inspect("text", "  ")["ok"])

    def test_graph_exception_is_contained(self):
        class G:
            def trace_endpoint(self, e): raise RuntimeError("boom")
        r = CodeContext(G()).inspect("endpoint", "x")
        self.assertFalse(r["ok"])
        self.assertIn("boom", r["error"])

    def test_large_result_is_compacted(self):
        class G:
            def trace_endpoint(self, e): return {"blob": "z" * 50000}
        r = CodeContext(G()).inspect("endpoint", "x", max_chars=500)
        self.assertLess(len(json.dumps(r["result"])), 600)
        self.assertTrue(r["result"]["blob"].endswith("..."))

    def test_fallback_to_trace_index(self):
        if not (ROOT / "knowledge" / "codemap" / "trace_index.json").exists():
            self.skipTest("trace_index.json missing")
        c = CodeContext(use_graph=False)
        self.assertEqual(c.backend, "trace_index-fallback")
        r = c.inspect("endpoint", "GetSiriusWorkOrderHistory")
        self.assertTrue(r["ok"], r)
        self.assertIn("dbo.GetSiriusWorkOrderHistory", json.dumps(r["result"]))
        self.assertTrue(c.inspect("screen", "History", app="crm")["ok"])
        self.assertFalse(c.inspect("db_object", "dbo.X")["ok"])

    def test_missing_graph_module_degrades(self):
        with mock.patch.dict("sys.modules", {"hibot.knowledge.graph": None}):
            c = CodeContext()
        self.assertIsNone(c.graph)
        self.assertTrue(c.graph_error)


class ReportTests(unittest.TestCase):
    def _run(self, executed):
        q = {"n": 1, "kind": "template", "query_id": "wo_header", "region": "EU", "params": {"TenantId": 5}, "sql": "SELECT ?",
             "sql_display": "SELECT 5 AS x", "why": "w", "mode": LIVE if executed else DRY, "executed": executed, "ok": executed,
             "row_count": 1 if executed else None, "truncated": False, "columns": ["A", "B"], "rows": [[1, "x|y"]] if executed else [], "error": None}
        rep = {"summary": "S", "classification": "access", "root_cause": "RC", "confidence": 0.55,
               "evidence": [{"type": "query", "ref": "wo_header", "finding": "F1", "status": "executed" if executed else "not_executed", "query_no": 1},
                            {"type": "code", "ref": "a.cs:1", "finding": "F2"}],
               "code_locations": [{"repo": "SiriusApi", "path": "A/B.cs", "line": 12, "why": "because"}],
               "fix_steps": ["one", "two"], "verification_query_ids": ["wo_header"]}
        return {"run_id": "r1", "created": "t", "mode": q["mode"], "region": "EU", "llm": "fake", "report": rep, "queries": [q],
                "incident": {"key": "SM-9", "similar": [{"key": "SM-8", "score": 0.4, "problem_type": "x", "summary": "sim"}]}, "matches": []}

    def test_sections_and_sql(self):
        md = render_markdown(self._run(True))
        for h in ("## Özet", "## Kök neden", "## Kanıtlar", "## Çalıştırılan sorgular", "## Kod konumu", "## Çözüm adımları",
                  "## Güven", "## Benzer vakalar", "## Geri bildirim"):
            self.assertIn(h, md)
        self.assertIn("SELECT 5 AS x", md)
        self.assertIn("ÇALIŞTIRILDI", md)
        self.assertIn("x/y", md)                                   # pipe escaped in table cell
        self.assertIn("A/B.cs", md)
        self.assertNotIn("hiçbir sorgu gerçekten çalıştırılmadı", md)

    def test_dry_run_is_clearly_marked(self):
        md = render_markdown(self._run(False))
        self.assertIn("hiçbir sorgu gerçekten çalıştırılmadı", md)
        self.assertIn("ÇALIŞTIRILMADI", md)
        self.assertIn("0 gerçekten çalıştırıldı", md)


class PipelineAndFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.runs, self.cards, self.fb = t / "runs", t / "cards", t / "fb"
        self.env = mock.patch.dict(os.environ, {"HIBOT_RUNS_DIR": str(self.runs), "HIBOT_CARDS_DIR": str(self.cards),
                                                "HIBOT_FEEDBACK_DIR": str(self.fb)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _analyze(self, script, inc=None, **kw):
        return pipeline.analyze("SM-1", None, llm=FakeLLM(script), incident=inc or incident(), code=CodeContext(use_graph=False),
                                registry=registry(), **kw)

    def test_run_folder_contents(self):
        run = self._analyze([{"action": "run_query", "query_id": "wo_header", "params": WO_PARAMS}, final()])
        d = Path(run["run_dir"])
        for f in ("input.json", "incident.json", "matches.json", "steps.jsonl", "queries.json", "report.json", "report.md"):
            self.assertTrue((d / f).exists(), f)
        self.assertEqual(len((d / "steps.jsonl").read_text(encoding="utf-8").splitlines()), 2)
        self.assertEqual(store.load_run(run["run_id"])["report"]["classification"], "master_data")

    def test_run_id_validation(self):
        for bad in ("../x", "a/b", "", "x"):
            with self.assertRaises(ValueError):
                store.run_path(bad)

    def test_feedback_correct_creates_confirmed_card(self):
        sug = {"title": "Missing tenant for SKU", "symptom_signature": {"problem_type": "master_data_request", "app": None,
               "keywords": ["missing tenant"], "entity_requirements": ["sku_codes"]}, "root_cause": "not assigned",
               "verification_queries": [{"query_id": "product_sku_lookup", "params_from_entities": {"Sku": "entities.sku_codes[0]"}},
                                        {"query_id": "no_such_query"}], "fix_steps": ["assign"]}
        run = self._analyze([final(suggested_card=sug, confidence=0.8)])
        res = feedback.record_feedback(run["run_id"], "correct", "yes")
        cid = "missing_tenant_for_sku"
        card = json.loads((self.cards / (cid + ".json")).read_text(encoding="utf-8"))
        self.assertEqual((card["status"], card["evidence_tickets"]), ("confirmed", ["SM-1"]))
        self.assertEqual([q["query_id"] for q in card["verification_queries"]], ["product_sku_lookup"])   # unknown id dropped
        self.assertEqual(cardmod.validate_card(card), [])
        lines = (self.fb / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["verdict"], "correct")
        self.assertTrue(res["actions"])
        # second correct run with the same suggestion adds evidence instead of duplicating
        run2 = pipeline.analyze("SM-2", None, llm=FakeLLM([final(suggested_card=sug)]), incident=incident(key="SM-2"),
                                code=CodeContext(use_graph=False), registry=registry())
        feedback.record_feedback(run2["run_id"], "correct")
        card = json.loads((self.cards / (cid + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(card["evidence_tickets"], ["SM-1", "SM-2"])
        self.assertEqual(len(list(self.cards.glob("*.json"))), 1)

    def test_feedback_partial_draft_and_wrong_no_card(self):
        sug = {"title": "Some pattern here", "root_cause": "root cause text", "symptom_signature": {"keywords": ["pattern"]}}
        run = self._analyze([final(suggested_card=sug)])
        feedback.record_feedback(run["run_id"], "wrong")
        self.assertFalse(self.cards.exists() and list(self.cards.glob("*.json")))
        feedback.record_feedback(run["run_id"], "partial")
        card = json.loads(next(self.cards.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(card["status"], "draft")

    def test_feedback_updates_used_card_and_wrong_lowers_confidence(self):
        cardmod.save_card(_card("tenant_card"), self.cards)
        rep = final(evidence=[{"type": "card", "ref": "tenant_card", "finding": "matches"}])
        run = self._analyze([rep])
        feedback.record_feedback(run["run_id"], "correct")
        c = json.loads((self.cards / "tenant_card.json").read_text(encoding="utf-8"))
        self.assertEqual((c["status"], c["evidence_tickets"], c["confidence"]), ("confirmed", ["SM-1"], 0.6))
        feedback.record_feedback(run["run_id"], "wrong", "not it")
        c = json.loads((self.cards / "tenant_card.json").read_text(encoding="utf-8"))
        self.assertEqual(c["confidence"], 0.45)
        self.assertEqual([h["event"] for h in c["history"]], ["confirmed", "contradicted"])

    def test_feedback_never_revives_rejected_card_and_validates_input(self):
        cardmod.save_card(_card("tenant_card", status="rejected"), self.cards)
        run = self._analyze([final(evidence=[{"type": "card", "ref": "tenant_card", "finding": "x"}])])
        feedback.record_feedback(run["run_id"], "correct")
        self.assertEqual(json.loads((self.cards / "tenant_card.json").read_text(encoding="utf-8"))["status"], "rejected")
        with self.assertRaises(ValueError):
            feedback.record_feedback(run["run_id"], "great")
        with self.assertRaises(FileNotFoundError):
            feedback.record_feedback("20200101-000000-nope-0000", "correct")

    def test_demo_run_never_teaches_cards(self):
        sug = {"title": "Demo pattern here", "root_cause": "root cause text", "symptom_signature": {"keywords": ["demo"]}}
        run = self._analyze([final(suggested_card=sug)], demo=True)
        res = feedback.record_feedback(run["run_id"], "correct")
        self.assertFalse(self.cards.exists() and list(self.cards.glob("*.json")))
        self.assertIn("demo run", res["actions"][0])

    @unittest.skipUnless(HAVE_TICKETS, "knowledge/incidents/tickets_enriched.json missing")
    def test_end_to_end_demo_jira_key(self):
        inc = intake.build_incident("SM-1777386")
        self.assertEqual(inc["source"], "jira")
        run = pipeline.analyze("SM-1777386", None, llm=lambda i, m, r: build_demo_llm(i, m, r), demo=True, incident=inc)
        self.assertEqual([s["action"]["action"] for s in run["steps"]],
                         ["inspect_code", "run_query", "run_query", "final_report"])
        self.assertEqual([q["query_id"] for q in run["queries"]], ["wo_history_function", "wo_status_timeline"])
        self.assertFalse(any(q["executed"] for q in run["queries"]))
        self.assertLessEqual(run["report"]["confidence"], 0.6)
        md = (Path(run["run_dir"]) / "report.md").read_text(encoding="utf-8")
        self.assertIn("hiçbir sorgu gerçekten çalıştırılmadı", md)
        self.assertIn("dbo.GetSiriusWorkOrderHistory", md)

    @unittest.skipUnless(HAVE_TICKETS, "knowledge/incidents/tickets_enriched.json missing")
    def test_cli_analyze_and_feedback(self):
        from hibot.__main__ import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["analyze", "SM-1777386", "--llm", "fake"])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("report.md", text)
        run_id = next(self.runs.iterdir()).name
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["feedback", run_id, "--verdict", "partial", "--note", "n"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.fb / "feedback.jsonl").exists())

    @unittest.skipUnless(HAVE_TICKETS, "knowledge/incidents/tickets_enriched.json missing")
    def test_cli_free_text_and_error_paths(self):
        from hibot.__main__ import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["analyze", "--text", "Work order history shows only one record\nOrder 12345678 in CRM", "--llm", "fake"])
        self.assertEqual(rc, 0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main(["analyze"]), 2)
            self.assertEqual(main(["analyze", "SM-9999999999"]), 2)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HIBOT_DB_EU", None)
            os.environ.pop("HIBOT_DB_APAC", None)
            with contextlib.redirect_stderr(err):
                self.assertEqual(main(["analyze", "--text", "x y z history", "--live", "--region", "EU", "--llm", "fake"]), 2)
        self.assertIn("HIBOT_DB_EU", err.getvalue())

    def test_intake_rejects_bad_input(self):
        for k, t in (("not a key", None), (None, "   ")):
            with self.assertRaises(intake.IntakeError):
                intake.build_incident(k, t)


if __name__ == "__main__":
    unittest.main()
