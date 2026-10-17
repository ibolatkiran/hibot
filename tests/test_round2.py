"""Round 2: change_request taxonomy, UI gating, column provenance, labour / user-type templates, source guard, playbooks."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from hibot.agent import axes, config, playbooks, provenance, ui_gating
from hibot.agent.executor import DRY, LIVE
from hibot.agent.investigate import Investigation
from hibot.llm.client import FakeLLM
from tools.eval import add_axes
from tools.eval import score as S

from tests.test_agent import final, incident, registry
from tests.test_playbooks import FakeRunner, LOC_COLS, mk

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ taxonomy

class TaxonomyTests(unittest.TestCase):
    def test_change_request_pair(self):
        self.assertIn("change_request", axes.ROOT_CAUSE_TYPES)
        self.assertIn("route_to_backlog", axes.RESOLUTION_ACTIONS)
        self.assertEqual(axes.derive_classification("change_request", "route_to_backlog"), "change_request")
        self.assertEqual(axes.derive_axes("change_request"), ("change_request", "route_to_backlog"))
        r = axes.normalize({"root_cause_type": "change_request"})
        self.assertEqual((r["resolution_action"], r["classification"]), ("route_to_backlog", "change_request"))
        r = axes.normalize({"classification": "change_request"})
        self.assertEqual((r["root_cause_type"], r["resolution_action"]), ("change_request", "route_to_backlog"))

    def test_legacy_roundtrip_still_stable(self):
        for legacy, (rct, ra) in axes.LEGACY_TO_AXES.items():
            self.assertEqual(axes.derive_classification(rct, ra), legacy)

    def test_report_schema_and_labels(self):
        from hibot.agent import actions
        from hibot.agent.report import CLASS_TR
        f = final(root_cause_type="change_request", resolution_action="route_to_backlog")
        self.assertEqual(actions.validate_action(f), [])
        self.assertIn("change_request", CLASS_TR)
        self.assertIn("change_request", axes.ROOT_TR)
        self.assertIn("route_to_backlog", axes.ACTION_TR)

    def test_cards_accept_new_axes(self):
        from hibot.agent import cards as cardmod
        from hibot.llm.jsonio import validate_schema
        c = {"id": "x_y", "title": "t t t", "symptom_signature": {"keywords": ["a"]}, "root_cause": "root cause", "verification_queries": [],
             "fix_steps": [], "confidence": 0.5, "status": "draft", "root_cause_type": "change_request", "resolution_action": "route_to_backlog",
             "classification": "change_request"}
        self.assertEqual(validate_schema(c, cardmod.CARD_SCHEMA), [])

    def test_label_rule_r9_and_scoring(self):
        it = {"key": "SM-9", "expected_classification": "change_request", "label_basis": "x", "note": "n"}
        self.assertEqual(add_axes.axes_for(it, {}), ("change_request", "route_to_backlog", "R9"))
        self.assertIn("change_request", S.CLASSES)
        rep = {"classification": "change_request", "root_cause_type": "change_request", "resolution_action": "route_to_backlog", "confidence": 0.7,
               "summary": "s", "root_cause": "r", "evidence": [], "fix_steps": []}
        item = {"key": "SM-9", "split": "dev", "stratum": "capability_gating", "expected_classification": "change_request", "label_confidence": "high",
                "expected_root_cause_type": "change_request", "expected_resolution_action": "route_to_backlog", "expected_findings": []}
        s = S.score_ticket(item, {"key": "SM-9", "report": rep, "queries": [], "mode": "live"})
        self.assertTrue(s["exact"] and s["both_ok"])
        self.assertEqual(S.report_axes({"classification": "change_request"}), ("change_request", "route_to_backlog"))

    def test_rules_file_documents_r9(self):
        t = (ROOT / "knowledge" / "eval" / "label_axes_rules.md").read_text(encoding="utf-8")
        self.assertIn("R9", t)
        self.assertIn("route_to_backlog", t)

    def test_existing_labels_unchanged_by_r9(self):
        p = ROOT / "knowledge" / "eval" / "eval_set.json"
        if not p.exists():
            self.skipTest("no eval set")
        items = json.loads(p.read_text(encoding="utf-8"))["tickets"]
        r9 = [i["key"] for i in items if i.get("axes_rule") == "R9"]
        self.assertEqual(r9, ["SM-2172385"])                       # only the new dev ticket carries the new class
        new = {i["key"]: i for i in items if i["key"] in ("SM-2172385", "SM-2169006")}
        self.assertEqual({k: v["split"] for k, v in new.items()}, {"SM-2172385": "dev", "SM-2169006": "dev"})
        self.assertEqual(new["SM-2169006"]["expected_tenant_id"], 59)
        self.assertTrue(any(f.get("query_id") == "product_labour_setup" for f in new["SM-2169006"]["expected_findings"]))


# ------------------------------------------------------------------ templates

class TemplateTests(unittest.TestCase):
    def test_new_templates_prepare_in_both_regions(self):
        reg = registry()
        for region in ("EU", "APAC"):
            pq = reg.prepare("user_type_lookup", {"Ids": "1218,1233", "TenantId": 76}, region)
            self.assertEqual(pq.warnings, [])
            pq = reg.prepare("user_type_lookup", {"TenantId": 76}, region)
            pq = reg.prepare("product_labour_setup", {"TenantId": 59, "Skus": "7440000008,7440000011"}, region)
            self.assertEqual(pq.warnings, [])
            pq = reg.prepare("wo_detail_material_types", {"TenantId": 59, "WorkOrderId": 25947198}, region)
            self.assertEqual(pq.warnings, [])

    def test_user_type_lookup_needs_ids_or_tenant(self):
        from hibot.db.templates import TemplateError
        with self.assertRaises(TemplateError):
            registry().prepare("user_type_lookup", {}, "EU")

    def test_no_warnings_and_no_pii_declared(self):
        reg = registry()
        for qid in ("user_type_lookup", "product_labour_setup", "wo_detail_material_types"):
            t = reg.get(qid)
            self.assertEqual(reg.validate_template(t).warnings, [], qid)
            self.assertEqual(reg.validate_template(t).errors, [], qid)
            self.assertFalse(t.pii, qid)
            self.assertIn("EU", t.regions)

    def test_catalog_lists_new_templates(self):
        cat = registry().catalog_text("EU")
        for qid in ("user_type_lookup", "product_labour_setup", "wo_detail_material_types"):
            self.assertIn(qid, cat)


# ------------------------------------------------------------------ UI gating

SCREEN = """import x from 'y';
// System config
// repairDateEditUserTypeIds: who may edit the repair date
// TODO: add the user types of the new country
const SYSTEM_CONFIG = {
  Test: {
    repairDateEditUserTypeIds: [1238, 1244],
  },
  Live: {
    repairDateEditUserTypeIds: [1218, 1233],
  },
};
function other() { return 1; }
function render() {
  return data.VerificationDate && userTypes.some(x => cfg.repairDateEditUserTypeIds.includes(x.UserTypeId)) && <Edit />;
}
const SetRepairDate = () => { axios.post('/SetRepairDate'); };
"""


class FakeGraph:
    def __init__(self, app="partner", screen="src/views/WorkOrder/Summary.js", call_line=18):
        self.t = {"endpoint": "POST api/Technician/SetRepairDate",
                  "callers": [{"app": app, "screen": screen, "file": screen, "line": call_line}]}

    def trace_endpoint(self, ep):
        return self.t


class UiGatingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        repo = Path(self.tmp.name) / "SiriusServiceShopCallCenter"
        (repo / "src" / "views" / "WorkOrder").mkdir(parents=True)
        (repo / "src" / "views" / "WorkOrder" / "Summary.js").write_text(SCREEN, encoding="utf-8")
        self.old = os.environ.get("HIBOT_REPO_ROOTS")
        os.environ["HIBOT_REPO_ROOTS"] = str(repo)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("HIBOT_REPO_ROOTS", None)
        else:
            os.environ["HIBOT_REPO_ROOTS"] = self.old
        self.tmp.cleanup()

    def test_feature_tokens_and_ticket_detection(self):
        self.assertEqual(ui_gating.feature_tokens("POST api/Technician/SetRepairDate"), ["repair", "date"])
        self.assertEqual(ui_gating.feature_tokens("GET api/Technician/GetSiriusUserTypeList/{EmployeeId}"), ["sirius", "user", "type"])
        self.assertTrue(ui_gating.is_capability_ticket({"summary": "Update the repair date field", "desc": "please make it editable"}))
        self.assertFalse(ui_gating.is_capability_ticket({"summary": "Labour 150 shown twice", "desc": "total is 300"}))

    def test_endpoint_selection_needs_two_shared_tokens(self):
        inc = {"summary": "Update the repair date field", "desc": "The repair date is wrong",
               "screen_hints": [{"endpoints": ["POST api/Technician/SetRepairDate", "GET api/Technician/GetCityRequestByRegion/{regionName}"]}]}
        self.assertEqual(ui_gating.endpoints_from_incident(inc), ["POST api/Technician/SetRepairDate"])
        self.assertEqual(ui_gating.endpoints_from_incident({"summary": "city list", "desc": "", "screen_hints": inc["screen_hints"]}), [])

    def test_scan_finds_lists_todo_and_env_labels(self):
        sc = ui_gating.scan_lines(SCREEN.splitlines(), ["repair", "date"], call_line=18)
        text = "\n".join(t for _, t in sc["lines"])
        self.assertIn("TODO", text)
        self.assertIn("includes(x.UserTypeId)", text)
        envs = {(l["env"], tuple(l["ids"])) for l in sc["id_lists"]}
        self.assertIn(("Live", (1218, 1233)), envs)
        self.assertIn(("Test", (1238, 1244)), envs)

    def test_analyze_reads_caller_screen_and_prefers_live_ids(self):
        res = ui_gating.analyze(FakeGraph(), ["POST api/Technician/SetRepairDate"])
        self.assertIn("UI GATING", res["text"])
        self.assertIn("Summary.js", res["text"])
        self.assertIn("repairDateEditUserTypeIds", res["text"])
        self.assertEqual(res["ids"], [1218, 1233])
        self.assertIn("user_type_lookup", res["text"])

    def test_unreadable_or_missing_graph_is_harmless(self):
        self.assertEqual(ui_gating.analyze(None, ["x"])["text"], "")
        self.assertEqual(ui_gating.analyze(FakeGraph(app="technician"), ["x"])["ids"], [])

    def test_inspect_code_endpoint_observation_carries_gating(self):
        ex, _ = mk(DRY, {})
        ex.code.graph = FakeGraph()
        ex.code.inspect = lambda kind, value, app=None, max_chars=3000: {"ok": True, "result": {"endpoint": value}, "backend": "graph"}
        obs = ex.execute({"action": "inspect_code", "target": {"kind": "endpoint", "value": "POST api/Technician/SetRepairDate"}})
        self.assertIn("UI GATING", obs["text"])
        self.assertIn("1218", obs["text"])
        again = ex.execute({"action": "inspect_code", "target": {"kind": "endpoint", "value": "POST api/Technician/SetRepairDate"}})
        self.assertNotIn("UI GATING", again["text"])                   # shown once


# ------------------------------------------------------------------ provenance

OBJECTS_SQL = """CREATE PROC dbo.PricingX (@WorkOrderId INT) AS
BEGIN
  Declare @LabourCost Decimal(18,2)
    =(SELECT ISNULL(SUM(Price),0)
      FROM Operation.WorkOrderDetail as WOD
      JOIN Catalog.Material as M ON M.Id=WOD.MaterialId AND M.Type=1
      WHERE WOD.WorkorderID=@WorkOrderId)
  UPDATE Operation.WorkOrder
    Set LabourCost=@LabourCost
  WHERE ID=@WorkOrderId
END
"""


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "PricingX.sql"
        self.p.write_text(OBJECTS_SQL, encoding="utf-8")
        self.objects = {
            "Operation.WorkOrder": {"columns": [{"name": "Id"}, {"name": "LabourCost"}, {"name": "VerificationDate"}], "written_by": ["dbo.PricingX", "dbo.Other"]},
            "dbo.PricingX": {"source_files": {"EU": str(self.p)}},
            "dbo.Other": {"source_files": {"EU": str(self.p.parent / "missing.sql")}},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_value_problem_and_column_candidates(self):
        inc = {"summary": "Sirius is not showing Labour Charge", "desc": "No labor cost showing In sirius"}
        self.assertTrue(provenance.is_value_problem(inc))
        self.assertEqual(provenance.candidate_columns(inc, self.objects), [("Operation.WorkOrder", "LabourCost")])
        self.assertEqual(provenance.candidate_columns({"summary": "labour", "desc": ""}, self.objects), [])
        self.assertFalse(provenance.is_value_problem({"summary": "Please add a model", "desc": "for Serbia"}))

    def test_analyze_finds_writer_inputs_and_templates(self):
        inc = {"summary": "Sirius is not showing Labour Charge", "desc": "No labor cost showing In sirius"}
        res = provenance.analyze(inc, registry(), self.objects)
        self.assertEqual(res["columns"], ["Operation.WorkOrder.LabourCost"])
        self.assertEqual(res["writers"], ["dbo.PricingX"])
        self.assertIn("M.Type=1", res["text"])
        self.assertIn("Set LabourCost=@LabourCost", res["text"])
        self.assertEqual(res["input_tables"], ["Operation.WorkOrderDetail", "Catalog.Material"])
        self.assertIn("wo_detail_material_types", res["templates"])
        self.assertIn("COLUMN PROVENANCE", res["text"])

    def test_no_text_when_not_a_value_problem_or_no_writer(self):
        self.assertEqual(provenance.analyze({"summary": "add model", "desc": "labour cost list"}, None, self.objects)["text"], "")
        objs = dict(self.objects)
        objs["Operation.WorkOrder"] = {"columns": [{"name": "LabourCost"}], "written_by": []}
        self.assertEqual(provenance.analyze({"summary": "labour cost is 0", "desc": ""}, None, objs)["text"], "")

    def test_real_schema_labour_cost_points_to_pricing_workorder(self):
        if not (config.knowledge_dir() / "schema" / "objects.json").exists():
            self.skipTest("schema catalog not generated")
        provenance._objects.cache_clear()
        res = provenance.analyze({"summary": "Labour cost is 0", "desc": "no labour"}, registry())
        if not res["writers"]:
            self.skipTest("sqlproj sources not present")
        self.assertEqual(res["writers"][0], "dbo.PricingWorkOrder")


# ------------------------------------------------------------------ playbooks

class PlaybookRound2Tests(unittest.TestCase):
    def test_labour_ticket_detection(self):
        inc = incident(summary="Labour charge not showing", desc="WO 25947184", entities={"sku_codes": ["7440000008"], "work_order_ids_strong": ["25947184"]})
        self.assertTrue(playbooks.is_labour_ticket(inc))
        self.assertFalse(playbooks.is_labour_ticket(incident(summary="Labour charge not showing", desc="", entities={})))
        self.assertFalse(playbooks.is_labour_ticket(incident(summary="cancel job", desc="status", entities={"work_order_ids_strong": ["1"]})))

    def test_performance_detection_ignores_change_and_hang_inside_words(self):
        self.assertTrue(playbooks.is_performance_ticket(incident(summary="CRM is very slow", desc="pages not loading")))
        self.assertFalse(playbooks.is_performance_ticket(incident(summary="change status W.O", desc="please change status", problem_type="data_correction")))
        self.assertTrue(playbooks.is_performance_ticket(incident(summary="x", desc="y", problem_type="performance_report")))

    def test_labour_playbook_runs_templates_with_tenant_and_skus(self):
        data = {"wo_detail_material_types": (["WorkOrderId", "HeaderLabourCost", "HeaderMaterialCost", "HeaderTransportCost", "HeaderExtraCost", "DetailId",
                                              "DefectId", "MaterialId", "MaterialType", "MaterialCode", "Quantity", "Price"],
                                             [[5, "0.00", "0.00", "30.00", "120.00", 11, None, 7, 3, "TRNS", 1, "30.0"]]),
                "product_labour_setup": (["ProductId", "SKU", "HierarchyId", "AssignedToTenant", "LabourMaterials", "LabourPriceGroups",
                                          "LabourMaterialsAnyTenant", "PriceGroupsAnyTenant", "HierarchyProductsInTenant", "HierarchyProductsWithLabour"],
                                         [[1, "7440000008", 121, 1, 0, 0, 0, 0, 11, 0]])}
        inc = incident(summary="Sirius is not showing Labour Charge", desc="WO 25947198", problem_type="master_data_request", country="BD",
                       entities={"sku_codes": ["7440000008"], "work_order_ids_strong": ["25947198"], "work_order_ids": ["25947198"]})
        ex, log = mk(LIVE, data)
        pf = {"resolved": [{"WorkOrderId": 25947198, "TenantId": 59, "Region": "EU"}]}
        res = playbooks.run(inc, ex, pf)
        self.assertEqual(res["ran"], ["wo_detail_material_types", "product_labour_setup"])
        self.assertEqual(dict(log)["product_labour_setup"], {"TenantId": 59, "Skus": "7440000008"})
        self.assertIn("Type3=1", res["text"])
        self.assertIn("HierarchyProductsWithLabour=0", res["text"])
        self.assertEqual(res["labour"]["tenant"], 59)

    def test_gating_playbook_runs_user_type_lookup_with_ids_and_ticket_tenant(self):
        data = {"tenant_lookup": (["TenantId", "TenantName", "CountryCode", "Active", "LanguageCode"], [[76, "Morocco", "ma", True, "fr"]]),
                "user_type_lookup": (["UserTypeId", "TenantId", "CountryCode", "TenantName", "UserTypeName", "Active", "Service", "InIdList"],
                                    [[1233, 76, "ma", "Morocco", "BackOffice - HQ", 1, False, 1], [920, 76, "ma", "Morocco", "partner", 1, True, 0]])}
        inc = incident(summary="Update the repair date field", desc="x", problem_type="data_correction", country="MA", entities={})
        ex, log = mk(LIVE, data)
        res = playbooks.run(inc, ex, {"resolved": []}, gating={"ids": [1218, 1233]})
        self.assertEqual(res["ran"], ["tenant_lookup", "user_type_lookup"])
        self.assertEqual(dict(log)["user_type_lookup"], {"Ids": "1218,1233", "TenantId": 76})
        self.assertIn("InIdList=1", res["text"])

    def test_notification_ticket_gets_sender_health_window(self):
        data = {"job_health": (["Source", "Name"], []), "outbound_queue_backlog": (["Source", "Name"], [])}
        inc = incident(summary="SMS not sent", desc="x", problem_type="notification_failure", created="2026-09-17 08:58", entities={})
        ex, log = mk(LIVE, data)
        res = playbooks.run(inc, ex, {"resolved": []})
        self.assertEqual(res["ran"], ["job_health", "outbound_queue_backlog"])

    def test_snapshot_caveat_for_add_requests(self):
        data = {"tenant_lookup": (["TenantId", "TenantName", "CountryCode"], [[52, "Serbia", "rs"]]),
                "product_sku_lookup": (["SKU", "TenantId", "AssignedToTenant"], [["8599", 52, 1]])}
        inc = incident(summary="New SKU in Sirius", desc="please add", problem_type="master_data_request", country="RS", entities={"sku_codes": ["8599"]})
        ex, _ = mk(LIVE, data)
        res = playbooks.run(inc, ex, {"resolved": []})
        self.assertIn("SNAPSHOT CAVEAT", res["text"])
        ex2, _ = mk(LIVE, data)
        inc2 = dict(inc, summary="SKU question", desc="what is it")
        self.assertNotIn("SNAPSHOT CAVEAT", playbooks.run(inc2, ex2, {"resolved": []})["text"])


# ------------------------------------------------------------------ source guard

class SourceGuardTests(unittest.TestCase):
    def _run(self, script, known=True):
        budget = config.Budget()
        ex, _ = mk(DRY, {}, budget=budget)
        if known:
            ex.known_sources.append("d:/repos/x/dbo/StoredProcedures/PricingWorkOrder.sql")
        res = Investigation(FakeLLM(script), incident(), [], [], ex, budget=budget).run()
        return ex, res

    def test_unknown_with_unread_known_source_is_refused_once(self):
        weak = final(root_cause_type="unknown", resolution_action="unknown", confidence=0.6)
        ex, res = self._run([weak, weak])
        self.assertIn("SOURCE NOT READ", res["steps"][0]["observation"])           # carried by the early-stop refusal (one step only)
        self.assertIn("EARLY STOP REFUSED", res["steps"][0]["observation"])
        self.assertEqual(res["steps"][1]["observation"], "(final report)")
        self.assertEqual(len(res["steps"]), 2)

    def test_code_vs_config_phrase_triggers(self):
        undecided = final(root_cause_type="config", resolution_action="config_change", confidence=0.6,
                          root_cause="Konfigürasyon mu kod mu belirlenemiyor")
        _, res = self._run([undecided, undecided])
        self.assertIn("SOURCE NOT READ", res["steps"][0]["observation"])
        self.assertNotIn("EARLY STOP", res["steps"][0]["observation"])             # confident enough: only the source guard fires

    def test_not_refused_without_known_source_or_after_reading(self):
        weak = final(root_cause_type="unknown", resolution_action="unknown", confidence=0.6)
        _, res = self._run([weak], known=False)
        self.assertEqual(len(res["steps"]), 1)
        ex, _ = mk(DRY, {})
        ex.known_sources.append("d:/repos/x/a.sql")
        ex.sources_read = 1
        res = Investigation(FakeLLM([weak]), incident(), [], [], ex, budget=ex.budget).run()
        self.assertEqual(len(res["steps"]), 1)

    def test_confident_decided_answer_is_not_refused(self):
        ok = final(root_cause_type="master_data", resolution_action="master_data_change", confidence=0.7)
        _, res = self._run([ok])
        self.assertEqual(len(res["steps"]), 1)


class PromptTests(unittest.TestCase):
    def test_prompt_mentions_round2_rules(self):
        from hibot import prompts
        s = prompts.system_prompt()
        for needle in ("change_request", "route_to_backlog", "user_type_lookup", "COLUMN PROVENANCE", "product_labour_setup", "read_source"):
            self.assertIn(needle, s)


if __name__ == "__main__":
    unittest.main()


class PipelineIntegrationTests(unittest.TestCase):
    def test_gating_and_provenance_reach_the_case_file_and_the_playbook(self):
        from hibot.agent import pipeline
        from hibot.agent.code_context import CodeContext
        tmp = tempfile.TemporaryDirectory()
        repo = Path(tmp.name) / "SiriusServiceShopCallCenter"
        (repo / "src" / "views" / "WorkOrder").mkdir(parents=True)
        (repo / "src" / "views" / "WorkOrder" / "Summary.js").write_text(SCREEN, encoding="utf-8")
        old = os.environ.get("HIBOT_REPO_ROOTS")
        os.environ["HIBOT_REPO_ROOTS"] = str(repo)
        try:
            inc = incident(key="SM-9", summary="Update the repair date field", desc="please make the repair date editable for our users",
                           problem_type="data_correction", country="MA", entities={},
                           screen_hints=[{"app": "partner", "screen": "src/views/WorkOrder/Summary.js", "endpoints": ["POST api/Technician/SetRepairDate"]}])
            data = {"tenant_lookup": (["TenantId", "TenantName", "CountryCode", "Active", "LanguageCode"], [[76, "Morocco", "ma", True, "fr"]]),
                    "user_type_lookup": (["UserTypeId", "TenantId", "UserTypeName", "InIdList"], [[1233, 76, "BackOffice - HQ", 1]])}
            log = []
            llm = FakeLLM([final(root_cause_type="change_request", resolution_action="route_to_backlog", confidence=0.7)])
            with tempfile.TemporaryDirectory() as d:
                run = pipeline.analyze("SM-9", None, llm=llm, incident=inc, mode=LIVE, region="EU", code=CodeContext(graph=FakeGraph()),
                                       registry=registry(), runs_base=Path(d), runner_factory=lambda r: FakeRunner(r, log, data))
            prompt = llm.calls[0]["messages"][1]["content"]
            self.assertIn("UI GATING", prompt)
            self.assertIn("repairDateEditUserTypeIds", prompt)
            self.assertEqual([q[0] for q in log], ["tenant_lookup", "user_type_lookup"])
            self.assertEqual(dict(log)["user_type_lookup"]["Ids"], "1218,1233")
            self.assertEqual(run["ui_gating"]["ids"], [1218, 1233])
            self.assertEqual(run["report"]["classification"], "change_request")
            self.assertIn("Değişiklik talebi", run["markdown"])
        finally:
            if old is None:
                os.environ.pop("HIBOT_REPO_ROOTS", None)
            else:
                os.environ["HIBOT_REPO_ROOTS"] = old
            tmp.cleanup()
