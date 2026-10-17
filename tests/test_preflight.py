import os
import unittest
from unittest import mock

from hibot import prompts
from hibot.agent import config, pipeline, preflight
from hibot.agent.code_context import CodeContext
from hibot.agent.executor import DRY, LIVE, Executor
from hibot.agent.investigate import Investigation
from hibot.db.runner import QueryResult
from hibot.db.templates import TemplateRegistry
from hibot.llm.client import FakeLLM

from tests.test_agent import final, incident, registry

COLS = ["WorkOrderId", "TenantId", "TenantName", "CountryCode", "WorkCaseId", "ExternalId", "ExternalWorkOrderId",
        "StatusId", "OpenDate", "VerificationDate", "MatchedOn"]
ROW = [22197255, 70, "Egypt", "eg", 1, None, None, 2271, "2026-01-01", None, "Id"]


class FakeRunner:
    """rows_by_region: region -> rows returned for wo_locate; other templates return 0 rows."""
    def __init__(self, region, rows_by_region, log):
        self.region, self.rows_by_region, self.log = region, rows_by_region, log

    def run_template(self, qid, params):
        self.log.append((self.region, qid, params))
        rows = self.rows_by_region.get(self.region, []) if qid == "wo_locate" else []
        return QueryResult(ok=True, region=self.region, query_id=qid, columns=COLS if qid == "wo_locate" else ["x"],
                           rows=rows, row_count=len(rows), elapsed_ms=1)

    def close(self):
        pass


def mk(mode, rows_by_region, log, region="EU", budget=None):
    return Executor(registry=registry(), code=CodeContext(use_graph=False), mode=mode, region=region,
                    budget=budget or config.Budget(), runner_factory=lambda r: FakeRunner(r, rows_by_region, log))


def inc22(**kw):
    return incident(entities={"work_order_ids": ["22197255"], "work_order_ids_strong": ["22197255"]}, country="TR", **kw)


class PreflightTests(unittest.TestCase):
    def test_resolves_tenant_and_injects_context(self):
        log = []
        ex = mk(LIVE, {"EU": [ROW]}, log)
        pf = preflight.run(inc22(), ex)
        self.assertEqual(log, [("EU", "wo_locate", {"Number": "22197255"})])
        self.assertEqual(pf["resolved"][0]["TenantId"], 70)
        self.assertEqual(ex.query_count, 1)
        self.assertTrue(ex.queries[0]["executed"])
        self.assertIn("RESOLVED WORK ORDERS", pf["text"])
        self.assertIn("TenantId=70", pf["text"])
        inv = Investigation(FakeLLM([final()]), inc22(), [], [], ex, resolved_text=pf["text"])
        self.assertIn("TenantName='Egypt'", inv.case_prompt)
        self.assertIn("RESOLVED WORK ORDERS", inv.case_prompt)

    def test_cross_region_fallback_switches_region(self):
        log = []
        with mock.patch.dict(os.environ, {"HIBOT_DB_APAC": "x"}):
            ex = mk(LIVE, {"APAC": [ROW]}, log)
            pf = preflight.run(inc22(), ex)
        self.assertEqual([c[0] for c in log], ["EU", "APAC"])
        self.assertTrue(pf["switched"])
        self.assertEqual(ex.region, "APAC")
        self.assertEqual(pf["resolved"][0]["Region"], "APAC")
        self.assertEqual(ex.query_count, 2)
        self.assertIn("switched to APAC", pf["text"])

    def test_no_fallback_when_other_region_not_configured(self):
        log = []
        env = {k: v for k, v in os.environ.items() if k != "HIBOT_DB_APAC"}
        with mock.patch.dict(os.environ, env, clear=True):
            ex = mk(LIVE, {"APAC": [ROW]}, log)
            pf = preflight.run(inc22(), ex)
        self.assertEqual(len(log), 1)
        self.assertEqual(pf["resolved"], [])
        self.assertIn("0 rows", pf["text"])

    def test_dry_run_marks_not_executed(self):
        ex = mk(DRY, {}, [])
        pf = preflight.run(inc22(), ex)
        self.assertEqual(ex.query_count, 1)
        self.assertFalse(ex.queries[0]["executed"])
        self.assertEqual(ex.queries[0]["query_id"], "wo_locate")
        self.assertIn("NOT executed", pf["text"])

    def test_cap_and_no_numbers(self):
        ex = mk(DRY, {}, [])
        inc = incident(entities={"work_order_ids": ["1111111", "2222222", "3333333", "4444444"]})
        preflight.run(inc, ex)
        self.assertEqual(ex.query_count, 3)
        ex2 = mk(DRY, {}, [])
        self.assertEqual(preflight.run(incident(entities={}), ex2)["text"], "")
        self.assertEqual(ex2.query_count, 0)

    def test_pipeline_records_preflight_query(self):
        import tempfile
        from pathlib import Path
        log = []
        with tempfile.TemporaryDirectory() as d:
            run = pipeline.analyze("SM-1", None, llm=FakeLLM([final()]), incident=inc22(), mode=LIVE, region="EU",
                                   code=CodeContext(use_graph=False), registry=registry(), runs_base=Path(d),
                                   runner_factory=lambda r: FakeRunner(r, {"EU": [ROW]}, log))
        self.assertEqual(run["queries"][0]["query_id"], "wo_locate")
        self.assertEqual(run["preflight"]["resolved"][0]["TenantId"], 70)


class EarlyStopTests(unittest.TestCase):
    def _run(self, script, budget=None):
        budget = budget or config.Budget()
        ex = mk(DRY, {}, [], budget=budget)
        llm = FakeLLM(script)
        res = Investigation(llm, incident(), [], [], ex, budget=budget).run()
        return llm, res

    def test_guard_triggers_once(self):
        weak = final(classification="unknown", confidence=0.2)
        llm, res = self._run([weak, weak])
        self.assertEqual(len(res["steps"]), 2)
        self.assertIn("EARLY STOP REFUSED", res["steps"][0]["observation"])
        self.assertEqual(res["report"]["classification"], "unknown")
        self.assertEqual(res["steps"][1]["observation"], "(final report)")

    def test_guard_continues_loop_with_new_action(self):
        weak = final(classification="unknown", confidence=0.2)
        q = {"action": "run_query", "query_id": "wo_header", "params": {"tenant_id": 5, "work_order_id": 1}}
        _, res = self._run([weak, q, final(classification="master_data", confidence=0.7)])
        self.assertEqual(res["report"]["classification"], "master_data")
        self.assertEqual(len(res["steps"]), 3)

    def test_no_guard_when_budget_used_or_confident(self):
        _, res = self._run([final(classification="master_data", confidence=0.6)])
        self.assertEqual(len(res["steps"]), 1)
        b = config.Budget(max_queries=2)
        q = {"action": "run_query", "query_id": "wo_header", "params": {"tenant_id": 5, "work_order_id": 1}}
        _, res = self._run([q, final(classification="unknown", confidence=0.1)], budget=b)
        self.assertEqual(len(res["steps"]), 2)
        self.assertNotIn("EARLY STOP", res["steps"][1]["observation"])


class PromptRuleTests(unittest.TestCase):
    def test_new_rules_present(self):
        s = prompts.system_prompt()
        for needle in ("REPORTER's country", "RESOLVED WORK ORDERS", "tenant_lookup", "0 rows", "wo_status_timeline",
                       "outbound_queue_backlog", "sap_rfc_rest_calls", "wo_field_changes"):
            self.assertIn(needle, s)
        self.assertLess(len(s), 17500)       # round 1: two axes + access/integration/calibration rules; round 2: gating / provenance / master-data / notification rules


if __name__ == "__main__":
    unittest.main()
