"""Round 1: two-axis classification, deterministic calibration, access playbook, integration time window, label axes, scoring."""
import json
import tempfile
import unittest
from pathlib import Path

from hibot.agent import access, actions, axes, calibration, cards as cardmod, config, intake, playbooks
from hibot.agent.executor import DRY, LIVE
from hibot.agent.report import render_markdown
from hibot.llm.jsonio import validate_schema
from tools.eval import add_axes
from tools.eval import score as S

from tests.test_agent import final, incident
from tests.test_playbooks import FakeRunner, mk

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- axes

class AxesTests(unittest.TestCase):
    def test_derive_classification_table(self):
        d = axes.derive_classification
        self.assertEqual(d("config", "data_fix"), "data_correction")        # StatusStep gap fixed by hand
        self.assertEqual(d("config", "config_change"), "config")
        self.assertEqual(d("user_error", "explain_to_user"), "user_error")
        self.assertEqual(d("data_issue", "data_fix"), "data_correction")
        self.assertEqual(d("unknown", "data_fix"), "data_correction")
        self.assertEqual(d("unknown", "unknown"), "unknown")
        self.assertEqual(d("master_data", "data_fix"), "master_data")       # direct types win over the action
        self.assertEqual(d("access", "grant_access"), "access")
        self.assertEqual(d("integration", "escalate_other_system"), "integration")
        self.assertEqual(d("code_defect", "code_change"), "code_defect")
        self.assertEqual(d("nonsense", None), "unknown")

    def test_legacy_roundtrip_is_stable(self):
        for legacy, (rct, ra) in axes.LEGACY_TO_AXES.items():
            self.assertEqual(axes.derive_classification(rct, ra), legacy)

    def test_normalize_axes_win_and_fill_missing(self):
        r = axes.normalize({"root_cause_type": "config", "resolution_action": "data_fix", "classification": "user_error"})
        self.assertEqual(r["classification"], "data_correction")
        r = axes.normalize({"classification": "access"})
        self.assertEqual((r["root_cause_type"], r["resolution_action"]), ("access", "grant_access"))
        r = axes.normalize({"root_cause_type": "master_data"})
        self.assertEqual((r["resolution_action"], r["classification"]), ("master_data_change", "master_data"))
        r = axes.normalize({"root_cause_type": "config", "classification": "config"})
        self.assertEqual(r["resolution_action"], "config_change")
        r = axes.normalize({})
        self.assertEqual((r["root_cause_type"], r["resolution_action"], r["classification"]), ("unknown", "unknown", "unknown"))

    def test_final_report_schema_accepts_axes_or_legacy(self):
        ok_axes = final(root_cause_type="config", resolution_action="data_fix")
        del ok_axes["classification"]
        self.assertEqual(actions.validate_action(ok_axes), [])
        self.assertEqual(actions.validate_action(final()), [])                       # legacy only
        bad = final(root_cause_type="config")                                         # one axis + legacy: fine
        self.assertEqual(actions.validate_action(bad), [])
        none = final()
        del none["classification"]
        self.assertTrue(actions.validate_action(none))
        self.assertTrue(actions.validate_action(final(root_cause_type="weird", resolution_action="data_fix")))
        rep = actions.split_report(ok_axes)
        self.assertEqual(rep["classification"], "data_correction")

    def test_report_renders_turkish_labels(self):
        rep = axes.normalize({"summary": "s", "root_cause": "r", "confidence": 0.5, "evidence": [], "fix_steps": [],
                              "root_cause_type": "config", "resolution_action": "data_fix"})
        md = render_markdown({"run_id": "x", "mode": "live", "region": "EU", "llm": "t", "created": "now", "report": rep,
                              "queries": [], "incident": {"key": "SM-1"}})
        self.assertIn("**Kök neden türü:**", md)
        self.assertIn("**Yapılması gereken:**", md)
        self.assertIn("data_correction", md)


class CardAxesTests(unittest.TestCase):
    def _card(self, **kw):
        c = {"id": "draft_footprint_x", "title": "title x", "symptom_signature": {"keywords": ["a"]}, "root_cause": "root cause",
             "verification_queries": [], "fix_steps": [], "confidence": 0.5, "status": "draft",
             "state_signature": {"tenant_id": 1, "from_status_id": 2, "to_status_id": 3, "transition_configured": False}}
        c.update(kw)
        return c

    def test_schema_accepts_axes_and_rejects_bad(self):
        self.assertEqual(cardmod.validate_card(self._card(root_cause_type="config", resolution_action="data_fix")), [])
        self.assertTrue(cardmod.validate_card(self._card(root_cause_type="nope")))

    def test_migration(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            cardmod.save_card(self._card(), dd)
            cardmod.save_card(self._card(id="draft_footprint_y", state_signature={"tenant_id": 1, "from_status_id": 2, "to_status_id": 4,
                                                                                  "transition_configured": True}), dd)
            cardmod.save_card(self._card(id="confirmed_egypt", status="confirmed", classification="user_error"), dd)
            cardmod.save_card(self._card(id="example_none", state_signature=None) if False else
                              {k: v for k, v in self._card(id="example_none").items() if k != "state_signature"}, dd)
            done = cardmod.migrate_axes(dd)
            self.assertEqual(sorted(done), ["confirmed_egypt", "draft_footprint_x", "draft_footprint_y"])
            by = {c["id"]: c for c in cardmod.load_cards(dd, include_rejected=True)}
            self.assertEqual((by["draft_footprint_x"]["root_cause_type"], by["draft_footprint_x"]["resolution_action"]), ("config", "data_fix"))
            self.assertEqual(by["draft_footprint_y"]["root_cause_type"], "data_issue")
            self.assertEqual((by["confirmed_egypt"]["root_cause_type"], by["confirmed_egypt"]["resolution_action"]), ("user_error", "explain_to_user"))
            self.assertNotIn("root_cause_type", by["example_none"])
            self.assertEqual(cardmod.migrate_axes(dd), [])                            # idempotent

    def test_shipped_cards(self):
        by = {c["id"]: c for c in cardmod.load_cards(include_rejected=True)}
        egypt = by.get("draft_footprint_t70_2271_to_2257")
        if egypt:
            self.assertEqual((egypt["root_cause_type"], egypt["resolution_action"]), ("user_error", "explain_to_user"))
        for cid, c in by.items():
            if cid.startswith("draft_footprint_") and c["status"] == "draft" and c["state_signature"].get("transition_configured") is False:
                self.assertEqual((c["root_cause_type"], c["resolution_action"]), ("config", "data_fix"), cid)


# ---------------------------------------------------------------- calibration

def q(n, qid, rows=1, executed=True):
    return {"n": n, "query_id": qid, "executed": executed, "row_count": rows}


def ev(ref, n=None, status="executed", **kw):
    d = {"type": "query", "ref": ref, "finding": "x", "status": status}
    if n:
        d["query_no"] = n
    d.update(kw)
    return d


class CalibrationTests(unittest.TestCase):
    def run_cal(self, conf, evidence, queries, **extra):
        rep = {"confidence": conf, "evidence": evidence}
        rep.update(extra)
        return calibration.apply(rep, queries)

    def test_no_query_cap(self):
        r = self.run_cal(0.9, [], [q(1, "wo_header", executed=False)])
        self.assertEqual(r["confidence"], 0.5)
        self.assertEqual(r["confidence_raw"], 0.9)
        self.assertEqual(r["confidence_caps_applied"][0]["reason"], "no_query_executed")

    def test_unsupported_capped_at_06_context_queries_do_not_support(self):
        r = self.run_cal(0.86, [ev("wo_header", 1)], [q(1, "wo_header")])
        self.assertEqual(r["confidence"], 0.6)
        self.assertEqual([c["cap"] for c in r["confidence_caps_applied"]], [0.6, ])

    def test_empty_result_does_not_support_except_lookup_templates(self):
        r = self.run_cal(0.8, [ev("wo_erp_sap", 1)], [q(1, "wo_erp_sap", rows=0)])
        self.assertEqual(r["confidence"], 0.6)
        r = self.run_cal(0.7, [ev("user_access_lookup", 1)], [q(1, "user_access_lookup", rows=0)])
        self.assertEqual(r["confidence"], 0.7)                                      # "user does not exist" IS the finding

    def test_single_support_caps_at_08_two_allow_more(self):
        one = self.run_cal(0.95, [ev("wo_status_timeline", 1)], [q(1, "wo_status_timeline")])
        self.assertEqual(one["confidence"], 0.8)
        two = self.run_cal(0.95, [ev("wo_status_timeline", 1), ev("status_step_check", 2)], [q(1, "wo_status_timeline"), q(2, "status_step_check")])
        self.assertEqual(two["confidence"], 0.95)
        self.assertEqual(two["confidence_caps_applied"], [])
        same = self.run_cal(0.95, [ev("wo_status_timeline", 1), ev("wo_status_timeline", 2)],
                            [q(1, "wo_status_timeline"), q(2, "wo_status_timeline")])
        self.assertEqual(same["confidence"], 0.8)                                    # not independent

    def test_supported_confirmed_card_allows_high(self):
        card = {"type": "card", "ref": "c1", "finding": "f"}
        r = self.run_cal(0.9, [card], [q(1, "wo_header")], matched_card={"verdict": "supports", "card_status": "confirmed"})
        self.assertEqual(r["confidence"], 0.9)
        d = self.run_cal(0.9, [card], [q(1, "wo_header")], matched_card={"verdict": "supports", "card_status": "draft"})
        self.assertEqual(d["confidence"], 0.8)
        c = self.run_cal(0.9, [card], [q(1, "wo_header")], matched_card={"verdict": "contradicts", "card_status": "confirmed"})
        self.assertEqual(c["confidence"], 0.6)

    def test_ticket_window_manual_fix_supports(self):
        tw_ev = ev("wo_field_changes", 1, deterministic=True)
        r = self.run_cal(0.9, [tw_ev], [q(1, "wo_field_changes")], ticket_window_changes={"manual_fix": {"suspected": True}})
        self.assertEqual(r["confidence"], 0.8)                                       # one independent finding
        r2 = self.run_cal(0.9, [tw_ev, ev("status_step_check", 2)], [q(1, "wo_field_changes"), q(2, "status_step_check")],
                          ticket_window_changes={"manual_fix": {"suspected": True}})
        self.assertEqual(r2["confidence"], 0.9)
        r3 = self.run_cal(0.9, [tw_ev], [q(1, "wo_field_changes")], ticket_window_changes={"manual_fix": {"suspected": False}})
        self.assertEqual(r3["confidence"], 0.6)

    def test_idempotent_and_low_confidence_untouched(self):
        r = self.run_cal(0.3, [], [q(1, "wo_header")])
        self.assertEqual((r["confidence"], r["confidence_caps_applied"]), (0.3, []))
        r = self.run_cal(0.9, [], [q(1, "wo_header", executed=False)])
        calibration.apply(r, [q(1, "wo_header", executed=False)])
        self.assertEqual((r["confidence"], r["confidence_raw"]), (0.5, 0.9))


# ---------------------------------------------------------------- access playbook

class AccessTests(unittest.TestCase):
    def test_extraction(self):
        self.assertEqual(access.extract_user_codes("access for the user C4800375, copy from C4800154"), ["C4800375", "C4800154"])
        self.assertEqual(access.extract_user_codes("kullanıcı kodu: ab12345 için yetki"), ["ab12345"])
        self.assertEqual(access.extract_user_codes("User ID <USERID> cannot log in"), [])                # scrubbed placeholder
        self.assertEqual(access.extract_user_codes("write to c1234567@example.com or john.doe@example.com"), ["c1234567"])   # local part only if code-like
        self.assertEqual(access.extract_user_codes("user D2523744 vs sku", exclude=["D2523744"]), [])
        self.assertEqual(len(access.extract_user_codes("A1111111 B2222222 C3333333 D4444444")), access.MAX_CODES)
        self.assertEqual(access.extract_user_codes("hello"), [])

    def test_access_detection(self):
        self.assertTrue(access.is_access_text("Manusoft yetkisi rica ederim"))
        self.assertTrue(access.is_access_text("user cannot login"))
        self.assertFalse(access.is_access_text("status is wrong on the work order"))

    def test_intake_adds_user_codes_for_access_tickets(self):
        inc = {"problem_type": "access_request", "summary": "Access", "desc": "please add C4800375", "entities": {}}
        out = intake._add_user_codes(inc)
        self.assertEqual(out["entities"]["user_codes"], ["C4800375"])
        other = intake._add_user_codes({"problem_type": "functional_defect", "summary": "x", "desc": "totally fine", "entities": {}})
        self.assertNotIn("user_codes", other["entities"])

    def _inc(self, desc, **kw):
        return incident(problem_type="access_request", summary="Access request", desc=desc, entities={}, country="PL", **kw)

    def test_lookup_runs_with_code_and_tenant_lookup_follows(self):
        data = {"user_access_lookup": (["Kind", "RefId", "Code", "TenantId", "Active", "Detail1", "Detail2"],
                                       [["SIRIUS_USER", 1, "C4800375", 7, 0, "PL", "3"], ["USER_TENANT", 2, "C4800375", 7, None, None, None]]),
                "tenant_lookup": (["TenantId", "TenantName", "CountryCode"], [[7, "Poland", "pl"]])}
        ex, log = mk(data=data)
        res = playbooks.run(self._inc("please give access to C4800375"), ex, {"resolved": []})
        self.assertEqual(res["ran"], ["user_access_lookup", "tenant_lookup"])
        self.assertEqual(log[0][1]["UserCode"], "C4800375")
        self.assertIn("active=0", res["text"])
        self.assertIn("USER_TENANT", res["text"])
        self.assertEqual(res["access"]["user_codes_found"], 1)

    def test_unknown_user_reported_and_no_tenant_lookup(self):
        ex, _ = mk(data={"user_access_lookup": (["Kind"], [])})
        res = playbooks.run(self._inc("please give access to C4800375"), ex, {"resolved": []})
        self.assertEqual(res["ran"], ["user_access_lookup"])
        self.assertIn("does not exist", res["text"])

    def test_no_code_gives_hint_without_queries_even_in_dry_run(self):
        for mode in (LIVE, DRY):
            ex, log = mk(mode=mode)
            res = playbooks.run(self._inc("Manusoft için yetki talep ediyorum"), ex, {"resolved": []})
            self.assertEqual(ex.query_count, 0)
            self.assertIn("kullanıcı kodu ticket'ta yok", res["text"])
            self.assertIn("grant_access", res["text"])
            self.assertEqual(log, [])

    def test_non_access_ticket_unaffected(self):
        ex, _ = mk(mode=DRY)
        self.assertEqual(playbooks.run(incident(country="PL"), ex, {"resolved": []})["text"], "")


# ---------------------------------------------------------------- integration time window

class IntegrationWindowTests(unittest.TestCase):
    def _inc(self, **kw):
        base = incident(problem_type="integration_failure", summary="Orders not sent to SAP", desc="no document created", entities={},
                        created="2026-09-17 08:58")
        base.update(kw)
        return base

    def test_window_math(self):
        w = playbooks.ticket_time_window({"created": "2026-09-17 08:58"})
        self.assertEqual(w, ("2026-09-16T05:58:00", "2026-09-18T05:58:00"))          # created 05:58 UTC +- 24 h
        self.assertIsNone(playbooks.ticket_time_window({}))

    def test_three_queries_run_with_window_params(self):
        data = {"job_health": (["Source", "Name", "LastAt", "LastEnd", "LastStatus", "RunsInWindow", "FailuresInWindow", "SampleText"],
                               [["JobRunLog", "SendToSap", "2026-09-17T03:00:00", None, "Running", 0, 0, None]]),
                "outbound_queue_backlog": (["Source", "Name", "Cnt", "MaxRetry", "FirstAt", "LastAt", "SampleText"], [["Gateway.Notification", "p", 40, 9, "a", "b", "err"]]),
                "db_errors_recent": (["ErrorProcedure", "ErrorNumber", "Occurrences", "FirstSeen", "LastSeen", "SampleMessage"], [["dbo.X", 515, 3, "a", "b", "m"]])}
        ex, log = mk(data=data)
        res = playbooks.run(self._inc(), ex, {"resolved": []})
        self.assertEqual(res["ran"], ["job_health", "outbound_queue_backlog", "db_errors_recent"])
        for _, params in log:
            self.assertEqual(params["FromDate"].isoformat() if hasattr(params["FromDate"], "isoformat") else params["FromDate"][:19], "2026-09-16T05:58:00")
        self.assertIn("1 with failures / no run in the window / unfinished", res["text"])
        self.assertIn("SendToSap", res["text"])
        self.assertIn("MaxRetry=9", res["text"])
        self.assertEqual(res["window"]["to_utc"], "2026-09-18T05:58:00")

    def test_not_run_when_work_order_resolved_or_no_created(self):
        ex, _ = mk()
        res = playbooks.run(self._inc(), ex, {"resolved": [{"WorkOrderId": 5, "TenantId": 7, "Region": "EU"}]})
        self.assertNotIn("job_health", res["ran"])
        ex, _ = mk()
        res = playbooks.run(self._inc(created=None), ex, {"resolved": []})
        self.assertEqual(res["ran"], [])
        self.assertIn("creation time unknown", res["text"])

    def test_templates_accept_window_params(self):
        from tests.test_agent import registry
        reg = registry()
        for qid in ("job_health", "outbound_queue_backlog", "db_errors_recent"):
            pq = reg.prepare(qid, {"FromDate": "2026-09-16T05:58:00", "ToDate": "2026-09-18T05:58:00"}, "EU")
            self.assertTrue(any(str(a).startswith("2026-09-16") for a in pq.args), qid)
            self.assertTrue(any(str(a).startswith("2026-09-18") for a in pq.args), qid)


# ---------------------------------------------------------------- label axes + scoring

def eval_item(**kw):
    d = {"key": "SM-1", "split": "test", "stratum": "s", "expected_classification": "data_correction", "label_confidence": "medium",
         "label_basis": "footprint: manual fix 329->314, transition not in StatusStep", "note": "n", "expected_findings": [
             {"type": "manual_fix_detected", "from": 329, "to": 314}]}
    d.update(kw)
    return d


class LabelAxesTests(unittest.TestCase):
    def a(self, **kw):
        return add_axes.axes_for(eval_item(**kw), {})

    def test_rules(self):
        self.assertEqual(self.a(), ("config", "data_fix", "R8a"))
        self.assertEqual(self.a(label_basis="footprint: manual fix 653->651 (step exists), no history row"), ("data_issue", "data_fix", "R8b"))
        self.assertEqual(self.a(label_basis="status change request; no change inside window", expected_findings=[]), ("unknown", "data_fix", "R8c"))
        self.assertEqual(self.a(label_basis="wrong warranty type on WO", expected_findings=[]), ("data_issue", "data_fix", "R8d"))
        self.assertEqual(self.a(expected_classification="master_data"), ("master_data", "master_data_change", "R1"))
        self.assertEqual(self.a(expected_classification="access"), ("access", "grant_access", "R2"))
        self.assertEqual(self.a(expected_classification="user_error"), ("user_error", "explain_to_user", "R3"))
        self.assertEqual(self.a(expected_classification="code_defect"), ("code_defect", "code_change", "R4"))
        self.assertEqual(self.a(expected_classification="integration", label_basis="SAP returned error"), ("integration", "escalate_other_system", "R5a"))
        self.assertEqual(self.a(expected_classification="integration", label_basis="recurring error", note="x"), ("integration", "unknown", "R5b"))
        self.assertEqual(self.a(expected_classification="config"), ("config", "config_change", "R6"))
        self.assertEqual(self.a(expected_classification="unknown"), ("unknown", "unknown", "R7"))

    def test_footprint_fact_wins_over_basis_words(self):
        facts = {("SM-1", "329", "314"): True}
        self.assertEqual(add_axes.axes_for(eval_item(label_basis="footprint: manual fix 329->314, no history row"), facts)[:2], ("data_issue", "data_fix"))
        facts = {("SM-1", "329", "314"): False}
        self.assertEqual(add_axes.axes_for(eval_item(label_basis="footprint: manual fix 329->314, no history row"), facts)[:2], ("config", "data_fix"))

    def test_eval_set_has_axes_for_every_ticket(self):
        p = ROOT / "knowledge" / "eval" / "eval_set.json"
        if not p.exists():
            self.skipTest("eval set missing")
        data = json.loads(p.read_text(encoding="utf-8"))
        for t in data["tickets"]:
            self.assertIn(t["expected_root_cause_type"], axes.ROOT_CAUSE_TYPES, t["key"])
            self.assertIn(t["expected_resolution_action"], axes.RESOLUTION_ACTIONS, t["key"])
            self.assertEqual(add_axes.axes_for(t, {})[2][:2], t["axes_rule"][:2], t["key"])      # mechanical: same rule family from the stored fields
        self.assertTrue((ROOT / "knowledge" / "eval" / "label_axes_rules.md").exists())


def srec(cls=None, conf=0.8, **rep_kw):
    rep = {"confidence": conf, "summary": "", "root_cause": "", "evidence": [], "fix_steps": []}
    if cls:
        rep["classification"] = cls
    rep.update(rep_kw)
    return {"key": "SM-1", "mode": "live", "queries": [], "preflight": {}, "report": rep, "elapsed_s": 1}


class AxisScoringTests(unittest.TestCase):
    def item(self, **kw):
        d = {"key": "SM-1", "split": "test", "stratum": "s", "expected_classification": "data_correction", "label_confidence": "medium",
             "expected_root_cause_type": "config", "expected_resolution_action": "data_fix"}
        d.update(kw)
        return d

    def test_new_report_axes(self):
        s = S.score_ticket(self.item(), srec("data_correction", root_cause_type="config", resolution_action="data_fix"))
        self.assertTrue(s["rct_ok"] and s["ra_ok"] and s["both_ok"] and s["exact"])
        self.assertFalse(s["cw_rct"])
        s = S.score_ticket(self.item(), srec("user_error", root_cause_type="user_error", resolution_action="explain_to_user", conf=0.9))
        self.assertTrue(s["cw_rct"] and s["confidently_wrong"])

    def test_legacy_report_axes_are_derived(self):
        s = S.score_ticket(self.item(), srec("config"))              # baseline-style report: only the legacy label
        self.assertEqual((s["got_rct"], s["got_ra"]), ("config", "config_change"))
        self.assertTrue(s["rct_ok"])
        self.assertFalse(s["ra_ok"])                                 # legacy config cannot say data_fix (documented caveat)
        self.assertFalse(s["exact"])
        s = S.score_ticket(self.item(), srec("data_correction"))
        self.assertEqual((s["got_rct"], s["got_ra"]), ("data_issue", "data_fix"))
        self.assertFalse(s["rct_ok"])
        self.assertTrue(s["ra_ok"])

    def test_expected_axes_fall_back_to_legacy_mapping(self):
        it = self.item()
        del it["expected_root_cause_type"], it["expected_resolution_action"]
        self.assertEqual(S.expected_axes(it), ("data_issue", "data_fix"))

    def test_unknown_expected_needs_unknown_answer(self):
        it = self.item(expected_root_cause_type="unknown", expected_resolution_action="data_fix")
        self.assertTrue(S.score_ticket(it, srec("data_correction", root_cause_type="unknown", resolution_action="data_fix", conf=0.4))["both_ok"])
        s = S.score_ticket(it, srec("user_error", root_cause_type="user_error", resolution_action="explain_to_user", conf=0.8))
        self.assertTrue(s["cw_rct"])

    def test_aggregate_has_axis_metrics_and_markdown(self):
        items = [self.item(key="A"), self.item(key="B", expected_root_cause_type="access", expected_resolution_action="grant_access",
                                               expected_classification="access")]
        recs = {"A": srec("data_correction", root_cause_type="config", resolution_action="data_fix", conf=0.5),
                "B": srec("user_error", root_cause_type="user_error", resolution_action="explain_to_user", conf=0.9)}
        res = S.score_all(items, recs, "test")
        o = res["overall"]
        self.assertEqual((o["rct_acc_reliable"], o["both_acc_reliable"], o["cw_rct_n_reliable"]), (0.5, 0.5, 1))
        self.assertEqual(len(res["calibration_rct"]), 3)
        md = S.render_markdown(res, "t")
        self.assertIn("confidently wrong on root_cause_type", md)
        self.assertIn("**CW-rct**", md)
        self.assertIn("both axes correct", md)


if __name__ == "__main__":
    unittest.main()
