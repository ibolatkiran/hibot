import datetime as dt
import tempfile
import unittest
from pathlib import Path

from hibot.agent import config, pipeline, playbooks, ticket_window
from hibot.agent.code_context import CodeContext
from hibot.agent.executor import DRY, LIVE, Executor
from hibot.agent.investigate import Investigation
from hibot.db.runner import QueryResult
from hibot.llm.client import FakeLLM

from tests.test_agent import final, incident, registry

FC_COLS = ["LogId", "LogDate", "OldStatusId", "OldStatusName", "NewStatusId", "NewStatusName", "UpdateUserId", "ChangedFields"]
TL_COLS = ["HistoryId", "WorkOrderId", "StatusId", "StatusName", "OperationDate", "SysDate", "TechnicianCode", "CurrentStatusId"]
LOC_COLS = ["WorkOrderId", "TenantId", "TenantName", "CountryCode", "StatusId", "MatchedOn"]

TL_ROWS = [[1, 22811628, 312, "New", "2026-08-01T10:12:29.597", "2026-08-01T10:12:29.597", "T1", 314],
           [2, 22811628, 329, "No Proof or Money", "2026-08-04T06:44:59.070", "2026-08-04T06:44:59.070", "T1", 314]]
FC_MANUAL = [[10, "2026-08-04T06:44:59", 316, "Accepted", 329, "No Proof or Money", 17970, "StatusId, UpdateUserId, "],
             [11, "2026-09-17T11:41:11", 329, "No Proof or Money", 314, "Cancelled", 17970, "StatusId, "]]


def rec(qid, cols, rows, executed=True):
    return {"query_id": qid, "executed": executed, "ok": executed, "columns": cols, "rows": rows, "row_count": len(rows),
            "params": {}, "n": 1}


def inc_sm(**kw):
    base = incident(key="SM-2139009", summary="Sirius Job: 22811628",
                    desc="Please assist in cancelling job 22811628 - status No proof of money - I cannot cancel it gives an error.",
                    entities={"work_order_ids": ["22811628"]}, problem_type="functional_defect", country="ZA",
                    created="2026-09-17 08:58", resolved="Thu, 17 Sep 2026 14:42:00 +0300")
    base.update(kw)
    return base


class TicketWindowTests(unittest.TestCase):
    def test_timezones(self):
        utc = dt.timezone.utc
        self.assertEqual(ticket_window.parse_created("2026-09-17 08:58"), dt.datetime(2026, 9, 17, 5, 58, tzinfo=utc))
        self.assertEqual(ticket_window.parse_resolved("Thu, 17 Sep 2026 14:42:00 +0300"), dt.datetime(2026, 9, 17, 11, 42, tzinfo=utc))
        self.assertEqual(ticket_window.parse_resolved("Thu, 17 Sep 2026 14:42:00 +0000"), dt.datetime(2026, 9, 17, 14, 42, tzinfo=utc))
        self.assertEqual(ticket_window.parse_db_time("2026-09-17T11:41:11.1234567"), dt.datetime(2026, 9, 17, 11, 41, 11, 123456, tzinfo=utc))
        self.assertIsNone(ticket_window.parse_created("garbage"))
        self.assertIsNone(ticket_window.parse_resolved(None))

    def _compute(self, fc=FC_MANUAL, tl=TL_ROWS, **kw):
        qs = [rec("wo_field_changes", FC_COLS, fc)]
        if tl is not None:
            qs.append(rec("wo_status_timeline", TL_COLS, tl))
        return ticket_window.compute(inc_sm(**kw), qs)

    def test_manual_fix_detected(self):
        tw = self._compute()
        # DB 11:41:11 UTC == 14:41:11 +03:00 is inside [08:58+03, 14:42+03 + 15 min]; the 08-04 row is outside the window
        self.assertEqual([c["log_id"] for c in tw["changes"]], [11])
        c = tw["changes"][0]
        self.assertTrue(c["status_change"])
        self.assertFalse(c["history_row_found"])
        self.assertTrue(c["update_user_unchanged"])
        self.assertTrue(tw["manual_fix"]["suspected"])
        self.assertEqual(tw["manual_fix"]["level"], "high")
        self.assertIn("MANUEL DB DÜZELTMESİ", tw["text"])
        self.assertIn("329 (No Proof or Money) -> 314 (Cancelled)", tw["text"])
        self.assertIn("Ticket süresince iş emrinde olan değişiklikler", tw["text"])
        self.assertTrue(any("current status 314 differs" in r for r in tw["manual_fix"]["reasons"]))

    def test_window_uses_utc_not_local_time(self):
        tw = self._compute()
        self.assertEqual(tw["window"]["end_utc"], "2026-09-17 11:57:00")
        self.assertEqual(tw["changes"][0]["at_utc"], "2026-09-17 11:41:11")
        late = [[12, "2026-09-17T12:30:00", 329, "x", 314, "y", 17970, "StatusId, "]]
        self.assertEqual(self._compute(fc=late)["changes"], [])

    def test_history_row_present_means_app_flow(self):
        tl = TL_ROWS + [[3, 22811628, 314, "Cancelled", "2026-09-17T11:41:12", "2026-09-17T11:41:12", "T1", 314]]
        fc = [[11, "2026-09-17T11:41:11", 329, "No Proof or Money", 314, "Cancelled", 20001, "StatusId, UpdateUserId, "]]
        tw = self._compute(fc=fc, tl=tl)
        self.assertTrue(tw["changes"][0]["history_row_found"])
        self.assertFalse(tw["changes"][0]["update_user_unchanged"])
        self.assertFalse(tw["manual_fix"]["suspected"])
        self.assertEqual(tw["manual_fix"]["level"], "none")
        self.assertIn("belirtisi yok", tw["text"])

    def test_no_history_but_user_changed_is_medium(self):
        fc = [[11, "2026-09-17T11:41:11", 329, "a", 314, "b", 20001, "StatusId, UpdateUserId, "]]
        self.assertEqual(self._compute(fc=fc)["manual_fix"]["level"], "medium")

    def test_timeline_missing_is_unverified(self):
        tw = self._compute(tl=None)
        self.assertIsNone(tw["changes"][0]["history_row_found"])
        self.assertFalse(tw["manual_fix"]["suspected"])
        self.assertIn("DOĞRULANAMADI", tw["text"])

    def test_unresolved_ticket_has_open_window_and_missing_inputs_give_none(self):
        tw = self._compute(resolved=None)
        self.assertIsNone(tw["window"]["end_utc"])
        self.assertEqual(len(tw["changes"]), 1)
        self.assertIsNone(ticket_window.compute(inc_sm(created=None), [rec("wo_field_changes", FC_COLS, FC_MANUAL)]))
        self.assertIsNone(ticket_window.compute(inc_sm(), []))
        self.assertIsNone(ticket_window.compute(inc_sm(), [rec("wo_field_changes", FC_COLS, FC_MANUAL, executed=False)]))

    def test_non_status_change_listed(self):
        fc = [[11, "2026-09-17T10:00:00", 314, "c", 314, "c", 5, "ServiceShopId, "]]
        tw = self._compute(fc=fc)
        self.assertFalse(tw["changes"][0]["status_change"])
        self.assertEqual(tw["status_changes"], [])
        self.assertFalse(tw["manual_fix"]["suspected"])


class FakeRunner:
    def __init__(self, region, log, data=None):
        self.region, self.log, self.data = region, log, data or {}

    def run_template(self, qid, params):
        self.log.append((qid, params))
        cols, rows = self.data.get(qid, (["x"], []))
        return QueryResult(ok=True, region=self.region, query_id=qid, columns=cols, rows=rows, row_count=len(rows), elapsed_ms=3)

    def close(self):
        pass


SM_DATA = {
    "wo_locate": (LOC_COLS, [[22811628, 68, "South Africa", "za", 314, "Id"]]),
    "wo_header": (["WorkOrderId", "StatusId", "StatusName"], [[22811628, 314, "Cancelled"]]),
    "wo_status_timeline": (TL_COLS, TL_ROWS),
    "wo_field_changes": (FC_COLS, FC_MANUAL),
    "wo_status_transition_check": (["WorkOrderId", "CurrentStatusId", "CurrentStatusName", "LatestHistoryStatusId", "NextStatusId", "NextStatusName"],
                                   [[22811628, 314, "Cancelled", 329, 312, "New"]]),
    "status_step_check": (["FromStatusId", "FromStatusName", "NextStatusId", "NextStatusName", "IsRequestedTarget", "StepsIntoTarget"],
                          [[329, "No Proof or Money", 312, "New", 0, 0], [329, "No Proof or Money", 335, "Completed", 0, 0]]),
}


def mk(mode=LIVE, data=None, budget=None, log=None):
    log = log if log is not None else []
    return Executor(registry=registry(), code=CodeContext(use_graph=False), mode=mode, region="EU", budget=budget or config.Budget(),
                    runner_factory=lambda r: FakeRunner(r, log, SM_DATA if data is None else data)), log


class SelectionTests(unittest.TestCase):
    WO = {"TenantId": 68, "WorkOrderId": 22811628}

    def ids(self, inc, wo=WO):
        return [s[0] for s in playbooks.select(inc, wo)]

    def test_status_ticket_any_problem_type(self):
        self.assertEqual(self.ids(inc_sm()), ["wo_header", "wo_status_timeline", "wo_field_changes", "wo_status_transition_check"])
        other = inc_sm(problem_type="data_correction", summary="x", desc="cannot change status of 22811628")
        self.assertEqual(self.ids(other)[1], "wo_status_timeline")

    def test_integration_and_notification(self):
        i = inc_sm(problem_type="integration_failure", summary="sap", desc="order 22811628 not sent")
        self.assertEqual(self.ids(i), ["wo_header", "wo_erp_sap", "sap_rfc_rest_calls"])
        n = inc_sm(problem_type="notification_failure", summary="sms", desc="customer got no message")
        self.assertEqual(self.ids(n), ["wo_header", "notifications_for_wo"])
        params = dict((s[0], s[1]) for s in playbooks.select(i, self.WO))
        self.assertEqual(params["sap_rfc_rest_calls"], {"TenantId": 68, "IdValue": 22811628})

    def test_integration_with_status_words_dedups_header(self):
        i = inc_sm(problem_type="integration_failure", desc="cannot close order 22811628, SAP error")
        ids = self.ids(i)
        self.assertEqual(ids.count("wo_header"), 1)
        self.assertIn("wo_erp_sap", ids)
        self.assertIn("wo_field_changes", ids)

    def test_nothing_without_wo_or_keywords(self):
        self.assertEqual(self.ids(inc_sm(), wo=None), [])
        self.assertEqual(self.ids(inc_sm(summary="hello", desc="how do I print a report", problem_type="how_to_question")), [])


class PlaybookRunTests(unittest.TestCase):
    PF = {"resolved": [{"WorkOrderId": 22811628, "TenantId": 68, "Region": "EU"}]}

    def test_status_baseline_runs_counts_budget_and_adds_step_check(self):
        ex, log = mk()
        res = playbooks.run(inc_sm(), ex, self.PF)
        self.assertEqual(res["ran"], ["wo_header", "wo_status_timeline", "wo_field_changes", "wo_status_transition_check", "status_step_check"])
        self.assertEqual(ex.query_count, 5)
        self.assertEqual([q["query_id"] for q in ex.queries], res["ran"])
        self.assertEqual(log[-1][1]["FromStatusId"], 329)
        self.assertEqual(log[-1][1]["ToStatusId"], 314)
        self.assertIn("BASELINE EVIDENCE", res["text"])
        self.assertIn("NO (transition NOT configured", res["text"])
        self.assertIn("MANUEL DB DÜZELTMESİ", res["text"])
        self.assertTrue(res["ticket_window"]["manual_fix"]["suspected"])

    def test_reserve_left_for_llm(self):
        ex, _ = mk(budget=config.Budget(max_queries=5))
        res = playbooks.run(inc_sm(), ex, self.PF)
        self.assertEqual(ex.query_count, 3)                # 5 - RESERVE(2)
        self.assertIn("budget kept for the investigation", res["text"])

    def test_dry_run_and_unresolved_do_nothing(self):
        ex, _ = mk(DRY)
        self.assertEqual(playbooks.run(inc_sm(), ex, self.PF)["text"], "")
        self.assertEqual(ex.query_count, 0)
        ex2, _ = mk()
        self.assertEqual(playbooks.run(inc_sm(), ex2, {"resolved": []})["ran"], [])

    def test_master_data_tenant_then_sku(self):
        data = {"tenant_lookup": (["TenantId", "TenantName", "CountryCode"], [[7, "Poland", "pl"]]),
                "product_sku_lookup": (["SKU", "TenantId", "AssignedToTenant"], [["7131232345", 7, 0]])}
        ex, log = mk(data=data)
        res = playbooks.run(incident(country="PL"), ex, {"resolved": []})
        self.assertEqual(res["ran"], ["tenant_lookup", "product_sku_lookup"])
        self.assertEqual(log[1][1]["TenantId"], 7)
        self.assertEqual(ex.query_count, 2)

    def test_master_data_ambiguous_tenant_skips_sku(self):
        data = {"tenant_lookup": (["TenantId", "TenantName", "CountryCode"], [[7, "a", "pl"], [8, "b", "pl"]])}
        ex, _ = mk(data=data)
        res = playbooks.run(incident(country="PL"), ex, {"resolved": []})
        self.assertEqual(res["ran"], ["tenant_lookup"])
        self.assertIn("2 tenants", res["text"])

    def test_observation_is_compact(self):
        many = [[i, "2026-09-0%dT10:00:00" % (i % 9 + 1), 1, "a", 2, "b", 5, "StatusId, "] for i in range(1, 60)]
        txt = playbooks.format_observation("wo_field_changes", rec("wo_field_changes", FC_COLS, many))
        self.assertIn("older rows omitted", txt)
        self.assertLessEqual(len(txt), playbooks.OBS_CAP + 20)


class PromptAndGuardTests(unittest.TestCase):
    def test_baseline_in_case_prompt_and_survives_truncation(self):
        ex, _ = mk()
        inv = Investigation(FakeLLM([final()]), inc_sm(desc="x " * 20000), [], [], ex, baseline_text="## BASELINE EVIDENCE\nmarker-xyz",
                            resolved_text="## RESOLVED WORK ORDERS\nmarker-res")
        self.assertIn("marker-xyz", inv.case_prompt)
        self.assertIn("marker-res", inv.case_prompt)
        self.assertIn("QUERY TEMPLATE CATALOG", inv.case_prompt)
        self.assertIn("case file truncated", inv.case_prompt)

    def _run(self, script, ex=None, inc=None, budget=None):
        budget = budget or config.Budget()
        ex = ex or mk(LIVE, {}, budget)[0]
        return Investigation(FakeLLM(script), inc or inc_sm(), [], [], ex, budget=budget).run()

    def test_shallow_guard_fires_once_even_when_confident(self):
        confident = final(classification="user_error", confidence=0.9)
        res = self._run([confident, confident])
        self.assertEqual(len(res["steps"]), 2)
        self.assertIn("EARLY STOP REFUSED", res["steps"][0]["observation"])
        self.assertIn("status history", res["steps"][0]["observation"])
        self.assertEqual(res["report"]["classification"], "user_error")

    def test_no_guard_when_baseline_touched_status_history(self):
        ex, _ = mk()
        playbooks.run(inc_sm(), ex, PlaybookRunTests.PF)
        res = self._run([final(classification="config", confidence=0.9)], ex=ex)
        self.assertEqual(len(res["steps"]), 1)
        self.assertEqual(res["steps"][0]["observation"], "(final report)")

    def test_no_guard_for_non_status_ticket_or_without_wo(self):
        res = self._run([final(classification="master_data", confidence=0.9)], inc=incident())
        self.assertEqual(len(res["steps"]), 1)
        res = self._run([final(classification="master_data", confidence=0.9)], inc=inc_sm(entities={}))
        self.assertEqual(len(res["steps"]), 1)

    def test_executing_status_query_satisfies_guard(self):
        q = {"action": "run_query", "query_id": "wo_status_timeline", "params": {"tenant_id": 68, "work_order_id": 22811628}}
        res = self._run([q, final(classification="config", confidence=0.9)])
        self.assertEqual(len(res["steps"]), 2)
        self.assertNotIn("EARLY STOP", res["steps"][1]["observation"])


class PipelineTests(unittest.TestCase):
    def test_pipeline_end_to_end_ticket_window_in_report(self):
        log = []
        script = [final(classification="config", confidence=0.8,
                        evidence=[{"type": "query", "ref": "status_step_check", "finding": "329->314 yok"}])]
        with tempfile.TemporaryDirectory() as d:
            run = pipeline.analyze("SM-2139009", None, llm=FakeLLM(script), incident=inc_sm(), mode=LIVE, region="EU",
                                   code=CodeContext(use_graph=False), registry=registry(), runs_base=Path(d),
                                   runner_factory=lambda r: FakeRunner(r, log, SM_DATA))
            self.assertEqual([q["query_id"] for q in run["queries"]][:2], ["wo_locate", "wo_header"])
            rep = run["report"]
            self.assertTrue(rep["ticket_window_changes"]["manual_fix"]["suspected"])
            self.assertTrue(rep["evidence"][0].get("deterministic"))
            self.assertEqual(rep["evidence"][0]["status"], "executed")
            self.assertIn("## Ticket süresince iş emrinde olan değişiklikler", run["markdown"])
            self.assertIn("ticket_window_changes", (Path(run["run_dir"]) / "report.json").read_text(encoding="utf-8"))
            self.assertIn("status_step_check", run["baseline"]["ran"])
            self.assertEqual(len(run["steps"]), 1)


if __name__ == "__main__":
    unittest.main()
