import json
import re
import unittest
from pathlib import Path

from tools.eval import score as S

ROOT = Path(__file__).resolve().parents[1]
EVAL_SET = ROOT / "knowledge" / "eval" / "eval_set.json"
TICKETS = ROOT / "knowledge" / "incidents" / "tickets_enriched.json"
DEV = {"SM-2139009", "SM-2139002", "SM-2122480", "SM-2126600", "SM-1777386"}


def item(**kw):
    d = {"key": "SM-1", "split": "test", "stratum": "s1", "expected_classification": "data_correction", "label_confidence": "medium",
         "expected_work_order_ids": [100], "expected_tenant_id": 7, "expected_findings": []}
    d.update(kw)
    return d


def rec(cls="data_correction", conf=0.8, **kw):
    r = {"key": "SM-1", "mode": "live", "elapsed_s": 10.0, "llm_calls": 3, "queries": [{"query_id": "wo_locate", "executed": True}],
         "preflight": {"resolved": [{"WorkOrderId": 100, "TenantId": 7}]},
         "report": {"classification": cls, "confidence": conf, "summary": "Status 329 to 314 forced; StatusStep transition missing",
                    "root_cause": "", "evidence": [{"type": "query", "finding": "manual fix"}], "fix_steps": []}}
    r.update(kw)
    return r


class TestScoreTicket(unittest.TestCase):
    def test_exact_and_wo_tenant(self):
        s = S.score_ticket(item(), rec())
        self.assertTrue(s["exact"] and s["lenient"] and s["wo_ok"] and s["tenant_ok"])
        self.assertFalse(s["confidently_wrong"])

    def test_wrong_tenant_not_ok(self):
        s = S.score_ticket(item(), rec(preflight={"resolved": [{"WorkOrderId": 100, "TenantId": 8}]}))
        self.assertTrue(s["wo_ok"])
        self.assertFalse(s["tenant_ok"])

    def test_lenient_table(self):
        s = S.score_ticket(item(), rec(cls="config"))
        self.assertFalse(s["exact"])
        self.assertTrue(s["lenient"])
        self.assertFalse(S.score_ticket(item(), rec(cls="code_defect"))["lenient"])
        self.assertTrue(S.lenient_match("master_data", "config"))
        self.assertFalse(S.lenient_match("master_data", "data_correction"))

    def test_confidently_wrong_vs_honest_unknown(self):
        cw = S.score_ticket(item(), rec(cls="code_defect", conf=0.85))
        self.assertTrue(cw["confidently_wrong"])
        self.assertFalse(cw["honest_unknown"])
        hu = S.score_ticket(item(), rec(cls="unknown", conf=0.2))
        self.assertTrue(hu["honest_unknown"])
        self.assertFalse(hu["confidently_wrong"])
        self.assertFalse(hu["exact"])
        unsure = S.score_ticket(item(), rec(cls="code_defect", conf=0.5))
        self.assertFalse(unsure["confidently_wrong"])

    def test_error_record_is_a_miss(self):
        s = S.score_ticket(item(), {"key": "SM-1", "error": "boom", "queries": [], "report": None})
        self.assertFalse(s["exact"] or s["confidently_wrong"] or s["wo_ok"])

    def test_findings(self):
        tw = {"manual_fix": {"suspected": True}, "status_changes": [{"old_status_id": "329", "new_status_id": "314"}]}
        r = rec()
        r["report"]["ticket_window_changes"] = tw
        r["queries"].append({"query_id": "status_step_check", "executed": True})
        checks = [{"type": "wo_resolved", "work_order_id": 100, "tenant_id": 7},
                  {"type": "manual_fix_detected", "from": 329, "to": 314},
                  {"type": "transition_missing", "tenant_id": 7, "from": 329, "to": 314},
                  {"type": "query_used", "query_id": "wo_locate"}, {"type": "query_used", "query_id": "wo_erp_sap"},
                  {"type": "mentions", "any": ["steps"]}, {"type": "mentions", "any": ["zzz"]},
                  {"type": "not_code_defect"}, {"type": "no_manual_fix"}]
        s = S.score_ticket(item(expected_findings=checks), r)
        self.assertEqual([f["hit"] for f in s["findings"]], [True, True, True, True, False, False, False, True, False])
        self.assertEqual((s["findings_hit"], s["findings_total"]), (5, 9))

    def test_dry_run_query_used_counts_prepared(self):
        r = rec(mode="dry-run")
        r["queries"] = [{"query_id": "wo_erp_sap", "executed": False}]
        self.assertTrue(S.check_finding({"type": "query_used", "query_id": "wo_erp_sap"}, r))
        r["mode"] = "live"
        self.assertFalse(S.check_finding({"type": "query_used", "query_id": "wo_erp_sap"}, r))


class TestAggregate(unittest.TestCase):
    def test_aggregate_and_render(self):
        items = [item(key="A"), item(key="B", stratum="s2", label_confidence="low"), item(key="C", expected_classification="access")]
        recs = {"A": rec(), "B": rec(cls="unknown", conf=0.1), "C": rec(cls="code_defect", conf=0.9)}
        res = S.score_all(items, recs, "test")
        o = res["overall"]
        self.assertEqual(o["n"], 3)
        self.assertEqual(o["n_reliable_labels"], 2)
        self.assertEqual(o["class_exact_acc_reliable"], 0.5)
        self.assertEqual(o["confidently_wrong_n_reliable"], 1)
        self.assertEqual(o["honest_unknown_rate"], round(1 / 3, 4))
        self.assertEqual(set(res["by_stratum"]), {"s1", "s2"})
        hi = [b for b in res["calibration"] if b["bucket"].startswith("0.7")][0]
        self.assertEqual((hi["n"], hi["accuracy"]), (2, 0.5))
        md = S.render_markdown(res, "t")
        self.assertIn("confidently wrong", md)
        self.assertIn("**CW**", md)

    def test_missing_keys_and_split(self):
        items = [item(key="A"), item(key="D", split="dev")]
        res = S.score_all(items, {"A": rec()}, "test")
        self.assertEqual(res["missing_keys"], [])
        res = S.score_all(items, {"A": rec()}, "all")
        self.assertEqual(res["missing_keys"], ["D"])

    def test_empty(self):
        res = S.score_all([], {}, "test")
        self.assertIsNone(res["overall"]["class_exact_acc_all"])


@unittest.skipUnless(EVAL_SET.exists() and TICKETS.exists(), "eval set / tickets_enriched.json not generated locally")
class TestEvalSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(EVAL_SET.read_text(encoding="utf-8"))
        cls.items = cls.data["tickets"]
        cls.tickets = {t["key"]: t for t in json.loads(TICKETS.read_text(encoding="utf-8"))["tickets"]}

    def test_keys_exist_and_unique(self):
        keys = [i["key"] for i in self.items]
        self.assertEqual(len(keys), len(set(keys)))
        for k in keys:
            self.assertIn(k, self.tickets)

    def test_schema(self):
        for i in self.items:
            self.assertIn(i["expected_classification"], S.CLASSES, i["key"])
            self.assertIn(i["label_confidence"], ("high", "medium", "low"), i["key"])
            self.assertEqual(i["needs_human_review"], i["label_confidence"] == "low", i["key"])
            self.assertIn(i["split"], ("test", "dev"))
            self.assertIn(i["region"], ("EU", "APAC"))
            self.assertTrue(1 <= len(i["expected_findings"]) <= 3, i["key"])
            self.assertLessEqual(len(i["note"]), 120)
            for f in i["expected_findings"]:
                self.assertIn(f["type"], {"wo_resolved", "manual_fix_detected", "transition_missing", "query_used", "mentions",
                                          "not_code_defect", "no_manual_fix"}, i["key"])

    def test_holdout_discipline(self):
        dev = {i["key"] for i in self.items if i["split"] == "dev"}
        self.assertEqual(dev, DEV)

    def test_composition(self):
        test = [i for i in self.items if i["split"] == "test"]
        self.assertGreaterEqual(len(test), 38)
        strata = {i["stratum"] for i in test}
        self.assertTrue({"status_footprint", "status_nochange", "functional_defect", "integration_failure", "master_data_request",
                         "access_request", "notification_failure", "performance_report"} <= strata)

    def test_no_pii(self):
        text = EVAL_SET.read_text(encoding="utf-8")
        notes = EVAL_SET.with_name("labeling_notes.md")
        if notes.exists():
            text += notes.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text))
        self.assertIsNone(re.search(r"https?://", text))
        # phone-like: separators between digit groups (plain 8-digit work-order ids are fine)
        self.assertIsNone(re.search(r"\+\d[\d ()-]{8,}|\(\d{2,4}\)[ -]?\d{3,}|\d{2,4}[ -]\d{3,4}[ -]\d{3,4}", text))


if __name__ == "__main__":
    unittest.main()
