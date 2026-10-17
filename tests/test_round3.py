"""Round 3: skills as checklists (hibot/agent/checklist.py), PLAN and CRITIC passes of the v2 loop, v2 calibration, holdout2 set.
FakeLLM + fake tools/runners: no network, no DB."""
import hashlib
import json
import re
import unittest
from pathlib import Path

from hibot.agent import checklist as cl
from hibot.agent import loop_v2
from hibot.agent.executor import LIVE
from hibot.agent.loop_v2 import parse_critic, parse_plan
from hibot.tools import skills

from tests.test_agent import incident
from tests.test_loop_v2 import SQL_OK, fake_tools, fr, mkv2, tool
from tests.test_playbooks import inc_sm

ROOT = Path(__file__).resolve().parents[1]

WO_LOCATE = tool("run_template", {"query_id": "wo_locate", "params": {"Number": "22811628"}})
PLAN = {"plan": {"hypotheses": [
    {"h": "transition missing in StatusStep", "confirm_by": "status_step_check shows no row", "refute_by": "row with IsRequestedTarget=1"},
    {"h": "user process / deliberate restriction", "confirm_by": "a confirmed card", "refute_by": "no card"}],
    "first_tools": ["run_template wo_locate", "run_template status_step_check"]}}


def nps_inc(**kw):
    return incident(problem_type="notification_failure", summary="NPS SMS should be sent when the work order is changed to Completed",
                    desc="South Africa: please send the NPS SMS at status Completed", entities={"work_order_ids": ["22811628"]}, **kw)


class SkillHeaderTests(unittest.TestCase):
    def test_every_shipped_checklist_skill_parses_and_its_regexes_compile(self):
        specs = {s.name: s for s in cl.load_specs()}
        for want in ("notification_failure", "value_provenance", "feature_and_permission_gating", "status_transition", "master_data_setup",
                     "integration_failure", "access_request", "error_message_trace"):
            self.assertIn(want, specs)
        for s in specs.values():
            ids = [i.id for i in s.items]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertTrue(s.body and not s.body.startswith("---"))
            if s.keywords:
                re.compile(s.keywords)
            for it in s.items:
                self.assertTrue(it.description and it.satisfied_by, (s.name, it.id))
                for a in it.satisfied_by:
                    kind, _, arg = a.partition(":")
                    self.assertIn(kind, ("tool", "read_file", "template", "sql"), a)
                    if kind in ("read_file", "sql"):
                        re.compile(arg)
                if it.when:
                    for atom in it.when.split("&"):
                        atom = atom.strip()
                        self.assertTrue(atom in ("wo", "no_wo", "user_code") or atom.startswith("text~"), atom)
                        if atom.startswith("text~"):
                            re.compile(atom[5:])

    def test_notification_skill_demands_the_spec_evidence(self):
        s = {x.name: x for x in cl.load_specs()}["notification_failure"]
        by = {i.id: i for i in s.items}
        self.assertTrue(any("Nps" in a for a in by["trigger_sp_read"].satisfied_by))
        self.assertTrue(any("MobileStatusId" in a for a in by["status_attribute_query"].satisfied_by))
        v = {x.name: x for x in cl.load_specs()}
        self.assertEqual({i.id for i in v["value_provenance"].items}, {"column_writers", "writer_sp_read", "inputs_query"})
        self.assertEqual({i.id for i in v["feature_and_permission_gating"].items}, {"caller_screen_read", "user_type_lookup"})
        self.assertEqual({i.id for i in v["status_transition"].items}, {"status_history", "transition_check"})

    def test_read_skill_still_returns_the_skill_text(self):
        r = skills.read_skill("notification_failure")
        self.assertTrue(r["ok"])
        self.assertIn("PrepareNPSData", r["text"])

    def test_front_matter_parser(self):
        head, body = cl.split_front_matter("---\nskill: x\nkeywords: a|b\nrequired_evidence:\n- id: i | description: d | satisfied_by: tool:t\n---\nBODY\n")
        self.assertEqual(head["skill"], "x")
        self.assertEqual(head["required_evidence"], ["id: i | description: d | satisfied_by: tool:t"])
        self.assertEqual(body.strip(), "BODY")
        self.assertEqual(cl.split_front_matter("no header")[0], {})


class SelectionTests(unittest.TestCase):
    def names(self, inc):
        return [s.name for s in cl.select(inc)]

    def test_notification_ticket(self):
        self.assertEqual(self.names(nps_inc())[0], "notification_failure")

    def test_status_ticket(self):
        self.assertIn("status_transition", self.names(inc_sm(problem_type="data_correction")))

    def test_capability_ticket(self):
        inc = incident(problem_type="other", summary="Update the repair date field", desc="The repair date field is not editable, please update it",
                       entities={})
        self.assertIn("feature_and_permission_gating", self.names(inc))

    def test_master_data_and_integration_and_access(self):
        self.assertEqual(self.names(incident())[0], "master_data_setup")
        self.assertEqual(self.names(incident(problem_type="integration_failure", summary="not sent to SAP", desc="FY document missing"))[0], "integration_failure")
        self.assertEqual(self.names(incident(problem_type="access_request", summary="cannot login", desc="please give access"))[0], "access_request")

    def test_deterministic_max_two_and_unrelated_ticket_gets_none(self):
        inc = nps_inc()
        self.assertEqual(self.names(inc), self.names(inc))
        self.assertLessEqual(len(self.names(inc)), cl.MAX_SKILLS)
        self.assertEqual(self.names(incident(problem_type="other", summary="hello", desc="thanks")), [])


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.spec = {s.name: s for s in cl.load_specs()}["notification_failure"]

    def test_conditions_and_satisfaction(self):
        c = cl.Checklist([self.spec], nps_inc())
        miss = [i.id for _n, i in c.missing([], [], True)]
        self.assertEqual(miss, ["trigger_sp_read", "status_attribute_query", "window_health"])        # no WO resolved: no_wo item, not delivery_rows
        calls = [{"tool": "read_file", "args": {"path": "D:\\repos\\gfsm\\Communication.PrepareNPSData.sql"}, "ok": True}]
        q = [{"query_id": "adhoc", "executed": True, "ok": True, "sql_display": "SELECT MobileStatusId FROM Catalog.Status WHERE TenantId = 68"},
             {"query_id": "wo_locate", "executed": True, "ok": True, "row_count": 1}]
        miss = [i.id for _n, i in c.missing(q, calls, True)]
        self.assertEqual(miss, ["delivery_rows"])                                                        # a WO is resolved now
        q.append({"query_id": "notifications_for_wo", "executed": True, "ok": True})
        self.assertEqual(c.missing(q, calls, True), [])

    def test_failed_calls_and_unexecuted_queries_do_not_count(self):
        c = cl.Checklist([self.spec], nps_inc())
        calls = [{"tool": "read_file", "args": {"path": "Communication.PrepareNPSData.sql"}, "ok": False}]
        self.assertIn("trigger_sp_read", [i.id for _n, i in c.missing([], calls, True)])
        q = [{"query_id": "job_health", "executed": False, "ok": None}]
        self.assertIn("window_health", [i.id for _n, i in c.missing(q, [], True)])
        self.assertNotIn("window_health", [i.id for _n, i in c.missing(q, [], False)])                  # dry-run: prepared counts

    def test_waivers_need_a_concrete_reason(self):
        c = cl.Checklist([self.spec], nps_inc())
        c.apply_waivers([{"id": "trigger_sp_read", "reason": "n/a"}, {"id": "nope", "reason": "a long enough reason here"},
                         {"id": "window_health", "reason": "the ticket names no time window and no work order exists"}], [], [], True)
        self.assertEqual(list(c.waived), ["window_health"])

    def test_section_and_refusal_texts_list_exact_actions(self):
        c = cl.Checklist([self.spec], nps_inc())
        sec = c.section()
        self.assertIn("METHOD FOR THIS CASE", sec)
        self.assertIn("PrepareNPSData", sec)                                                           # the skill body is injected
        self.assertNotIn("required_evidence:", sec)                                                    # header stripped
        self.assertIn("trigger_sp_read", sec)
        txt = c.refusal_text(c.missing([], [], True))
        self.assertIn("REQUIRED EVIDENCE MISSING", txt)
        self.assertIn("read_file on a source file", txt)
        self.assertIn("run_template with query_id job_health or outbound_queue_backlog or db_errors_recent", txt)


class ChecklistLoopTests(unittest.TestCase):
    def test_final_report_is_refused_twice_then_accepted_and_capped(self):
        llm, inv, ex, _ = mkv2([tool("code_search", {"pattern": "Nps"})] + [fr(confidence=0.9)] * 3,
                               inc=nps_inc(), use_checklist=True)
        out = inv.run()
        refusals = [s for s in out["steps"] if "REQUIRED EVIDENCE MISSING" in s["observation"]]
        self.assertEqual(len(refusals), 2)
        self.assertIn("refusal 1 of 2", refusals[0]["observation"])
        self.assertIn("trigger_sp_read", refusals[0]["observation"])
        rep = out["report"]
        self.assertEqual(rep["checklist"]["refusals"], 2)
        self.assertIn("trigger_sp_read", rep["checklist"]["missing"])
        self.assertLessEqual(rep["confidence"], 0.5)
        llm.assert_prompt_contains("METHOD FOR THIS CASE", 0)                                        # injected into the case file
        llm.assert_prompt_contains("REQUIRED EVIDENCE CHECKLIST", 0)

    def test_waiver_with_reason_is_accepted_and_recorded(self):
        waivers = [{"id": "trigger_sp_read", "reason": "no repository access to the SP in this environment"},
                   {"id": "status_attribute_query", "reason": "the ticket names no status to look up"},
                   {"id": "window_health", "reason": "no time window can be derived from the ticket"}]
        wfr = {"final_report": dict(fr()["final_report"], checklist_waivers=waivers)}
        _, inv, _, _ = mkv2([tool("code_search", {"pattern": "Nps"}), wfr, wfr], inc=nps_inc(), use_checklist=True)      # 1st final: shallow-status guard (once)
        out = inv.run()
        self.assertEqual([s for s in out["steps"] if "REQUIRED EVIDENCE MISSING" in s["observation"]], [])
        blk = out["report"]["checklist"]
        self.assertEqual(blk["missing"], [])
        self.assertEqual(set(blk["waivers"]), {"trigger_sp_read", "status_attribute_query", "window_health"})
        self.assertNotIn("checklist_waivers", out["report"])

    def test_satisfied_checklist_runs_without_refusal(self):
        script = [WO_LOCATE,
                  {"thought": "t", "tools": [{"tool": "run_template", "args": {"query_id": "wo_status_timeline", "params": {"TenantId": 68, "WorkOrderId": 22811628}}},
                                             {"tool": "run_template", "args": {"query_id": "status_step_check", "params": {"TenantId": 68, "FromStatusId": 329, "ToStatusId": 314}}}]},
                  fr(root_cause_type="config", resolution_action="config_change")]
        _, inv, _, _ = mkv2(script, inc=inc_sm(problem_type="data_correction"), use_checklist=True)
        out = inv.run()
        self.assertEqual([s for s in out["steps"] if "REQUIRED EVIDENCE MISSING" in s["observation"]], [])
        self.assertEqual(out["report"]["checklist"]["missing"], [])
        self.assertIn("status_transition", out["report"]["checklist"]["skills"])

    def test_no_checklist_without_matching_skill(self):
        _, inv, _, _ = mkv2([tool("code_search", {"pattern": "x"}), fr()], inc=incident(problem_type="other", summary="hello", desc="thanks"), use_checklist=True)
        out = inv.run()
        self.assertNotIn("checklist", out["report"])


class PlanTests(unittest.TestCase):
    def test_parse_plan_validation(self):
        ok, errs = parse_plan(PLAN)
        self.assertEqual(errs, [])
        self.assertEqual(len(ok["hypotheses"]), 2)
        for bad in ({}, {"plan": {"hypotheses": [PLAN["plan"]["hypotheses"][0]], "first_tools": ["x"]}},
                    {"plan": {"hypotheses": PLAN["plan"]["hypotheses"], "first_tools": []}},
                    {"plan": {"hypotheses": [{"h": "a long hypothesis", "confirm_by": "x"}] * 2, "first_tools": ["x"]}}, "text"):
            self.assertIsNone(parse_plan(bad)[0])
        self.assertEqual(parse_plan(PLAN["plan"])[1], [])                                             # bare plan object accepted

    def test_plan_is_turn_one_stored_in_steps_and_report(self):
        llm, inv, _, _ = mkv2([PLAN, tool("code_search", {"pattern": "x"}), fr()], plan=True)
        out = inv.run()
        llm.assert_prompt_contains("STEP 0: PLAN FIRST", 0)
        self.assertEqual(out["steps"][0]["n"], 0)
        self.assertIn("plan", out["steps"][0]["action"])
        self.assertEqual(out["report"]["plan"]["first_tools"][0], "run_template wo_locate")
        self.assertIn("PLAN ACCEPTED", llm.calls[1]["messages"][-1]["content"])
        self.assertEqual(out["llm_calls"], 3)

    def test_invalid_plan_is_repaired_and_a_failing_plan_does_not_kill_the_run(self):
        bad = {"plan": {"hypotheses": [PLAN["plan"]["hypotheses"][0]], "first_tools": ["x"]}}
        llm, inv, _, _ = mkv2([bad, PLAN, tool("code_search", {"pattern": "x"}), fr()], plan=True)
        out = inv.run()
        self.assertIsNotNone(out["report"].get("plan"))
        self.assertIn("hypotheses must be a list", llm.calls[1]["messages"][-1]["content"])
        _, inv2, _, _ = mkv2(["nope", "nope", "nope", tool("code_search", {"pattern": "x"}), fr()], plan=True)
        out2 = inv2.run()
        self.assertNotIn("plan", out2["report"])
        self.assertTrue(any("plan invalid" in w for w in out2["warnings"]))
        self.assertFalse(out2["report"].get("fallback"))


class CriticTests(unittest.TestCase):
    CRIT_REVISE = {"verdict": "revise", "unsupported_claims": ["the StatusStep row is missing (never queried)"], "untested_hypotheses": ["deliberate restriction"],
                   "confidence_adjustment": -0.2, "missing_checks": [{"tool": "run_template", "args_hint": "status_step_check for 329 -> 314", "why": "settles the config claim"}]}

    def test_parse_critic_clamps_and_validates(self):
        c, e = parse_critic(dict(self.CRIT_REVISE, confidence_adjustment=-3))
        self.assertEqual((e, c["confidence_adjustment"]), ([], -0.4))
        c, _ = parse_critic(dict(self.CRIT_REVISE, confidence_adjustment=0.5))
        self.assertEqual(c["confidence_adjustment"], 0.0)                                              # the critic can only lower
        self.assertIsNone(parse_critic({"verdict": "maybe"})[0])
        self.assertIsNone(parse_critic([])[0])

    def test_revise_sends_the_model_back_once_then_final(self):
        script = [tool("code_search", {"pattern": "x"}), fr(confidence=0.5), self.CRIT_REVISE,
                  tool("read_file", {"path": "dbo.X.sql"}), fr(confidence=0.5), "SHOULD NOT BE REACHED"]
        llm, inv, _, _ = mkv2(script, critic=True)
        out = inv.run()
        crit = out["report"]["critic"]
        self.assertEqual((crit["verdict"], crit["revision_round"]), ("revise", True))
        self.assertEqual(crit["applied_adjustment"], -0.1)                                             # halved: the model already acted on it
        self.assertEqual(out["report"]["confidence_before_critic"], 0.5)
        self.assertEqual(llm.remaining, 1)                                                              # exactly ONE critic call
        self.assertTrue(any("CRITIC REVIEW" in s["observation"] for s in out["steps"]))
        self.assertIn("EVIDENCE LOG", llm.calls[2]["messages"][1]["content"])
        self.assertIn("CRITIC", llm.calls[2]["messages"][0]["content"])
        self.assertIn("status_step_check for 329", llm.calls[3]["messages"][-1]["content"])

    def test_accept_keeps_the_report_and_lowers_confidence_by_the_adjustment(self):
        crit = {"verdict": "accept", "unsupported_claims": ["a number"], "untested_hypotheses": [], "confidence_adjustment": -0.2, "missing_checks": []}
        _, inv, _, _ = mkv2([tool("code_search", {"pattern": "x"}), fr(confidence=0.5), crit], critic=True)
        out = inv.run()
        rep = out["report"]
        self.assertEqual((rep["critic"]["verdict"], rep["critic"]["revision_round"], rep["critic"]["applied_adjustment"]), ("accept", False, -0.2))
        self.assertLessEqual(rep["confidence"], 0.3)
        self.assertEqual(rep["critic"]["unsupported_claims"], ["a number"])

    def test_critic_failure_never_fails_the_run(self):
        _, inv, _, _ = mkv2([tool("code_search", {"pattern": "x"}), fr(confidence=0.5), "garbage", "garbage"], critic=True)
        out = inv.run()
        self.assertNotIn("critic", out["report"])
        self.assertTrue(any("critic failed" in w for w in out["warnings"]))
        self.assertFalse(out["report"].get("fallback"))

    def test_no_critic_without_any_evidence_step_and_kill_switches(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"HIBOT_V2_CRITIC": "0", "HIBOT_V2_PLAN": "0", "HIBOT_V2_CHECKLIST": "0"}):
            llm, inv, _, _ = mkv2([tool("code_search", {"pattern": "x"}), fr()], critic=True, plan=True, use_checklist=True)
            out = inv.run()
        self.assertNotIn("critic", out["report"])
        self.assertNotIn("plan", out["report"])
        self.assertEqual(len(llm.calls), 2)


class V2CalibrationTests(unittest.TestCase):
    def ev(self, ref, typ="query"):
        return {"type": typ, "ref": ref, "finding": "f"}

    def test_report_goes_through_the_shared_calibration(self):
        _, inv, _, _ = mkv2([tool("sql", {"query": SQL_OK}), fr(confidence=0.95, evidence=[self.ev("sql")])])
        rep = inv.run()["report"]
        self.assertEqual(rep["confidence_raw"], 0.95)
        self.assertEqual(rep["confidence"], 0.69)                                                       # one independent evidence
        self.assertIn("needs_confirmed_card_or_2_independent_evidences", [c["reason"] for c in rep["confidence_caps_applied"]])

    def test_missing_checklist_evidence_caps_at_06(self):
        from hibot.agent import calibration
        qs = [{"n": i, "query_id": q, "executed": True, "row_count": 2} for i, q in enumerate(("wo_status_timeline", "wo_erp_sap", "wo_lines_totals"), 1)]
        evs = [dict(self.ev(q["query_id"]), status="executed", query_no=q["n"]) for q in qs]
        rep = calibration.apply({"confidence": 0.95, "evidence": evs, "checklist": {"missing": ["trigger_sp_read"]}}, qs)
        self.assertEqual(rep["confidence"], 0.6)
        self.assertIn("required_checklist_evidence_missing", [c["reason"] for c in rep["confidence_caps_applied"]])
        ok = calibration.apply({"confidence": 0.95, "evidence": evs, "checklist": {"missing": []}}, qs)
        self.assertEqual(ok["confidence"], 0.95)

    def test_distinct_adhoc_queries_cited_by_number_are_independent(self):
        sql2 = "SELECT TOP 5 Id FROM Operation.WorkOrder WHERE TenantId = 5 AND Id = 12345679"
        script = [tool("sql", {"query": SQL_OK}), tool("sql", {"query": sql2}),
                  fr(confidence=0.8, evidence=[self.ev("sql#1"), self.ev("sql#2")])]
        llm, inv, _, _ = mkv2(script)
        rep = inv.run()["report"]
        self.assertEqual(rep["confidence"], 0.8)
        self.assertIn("(query #1)", llm.calls[1]["messages"][-1]["content"])
        self.assertEqual(sorted(rep["supporting_evidence"]["query_ids"]), ["adhoc#1", "adhoc#2"])

    def test_read_code_counts_when_cited_and_read(self):
        script = [tool("sql", {"query": SQL_OK}), tool("read_file", {"path": "gfsm/Communication.PrepareNPSData.sql"}),
                  fr(confidence=0.8, evidence=[self.ev("sql#1"), self.ev("Communication.PrepareNPSData.sql:19", "code")])]
        rep = mkv2(script)[1].run()["report"]
        self.assertEqual(rep["confidence"], 0.8)
        cited_unread = mkv2([tool("sql", {"query": SQL_OK}), fr(confidence=0.8, evidence=[self.ev("sql#1"), self.ev("Other.sql:19", "code")])])[1].run()["report"]
        self.assertEqual(cited_unread["confidence"], 0.69)


class Holdout2Tests(unittest.TestCase):
    def test_holdout2_is_frozen_disjoint_and_well_formed(self):
        f = ROOT / "knowledge" / "eval" / "holdout2.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        readme = (ROOT / "knowledge" / "eval" / "README.md").read_text(encoding="utf-8")
        m = re.search(r"holdout2\.json sha256: `([0-9a-f]{64})`", readme)
        self.assertTrue(m)
        self.assertEqual(hashlib.sha256(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")).hexdigest(), m.group(1), "holdout2.json changed after it was frozen")
        keys = [t["key"] for t in data["tickets"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertGreaterEqual(len(keys), 38)
        old = {t["key"] for t in json.loads((ROOT / "knowledge" / "eval" / "eval_set.json").read_text(encoding="utf-8"))["tickets"]}
        self.assertEqual(set(keys) & old, set())
        for t in data["tickets"]:
            self.assertTrue(t["expected_root_cause_type"] and t["expected_resolution_action"] and t["axes_rule"], t["key"])
            self.assertEqual(t["split"], "test")


if __name__ == "__main__":
    unittest.main()
