"""Tests for hibot.knowledge.graph against the real generated knowledge/graph/graph.json."""
import os
import unittest

from hibot.knowledge.graph import CodeGraph, DEFAULT_PATH

HIST = "GET api/Technician/GetSiriusWorkOrderHistory/{WorkOrderId}"


@unittest.skipUnless(os.path.exists(DEFAULT_PATH),
                     "knowledge/graph/graph.json missing - run: python tools/graph/build.py")
class GraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = CodeGraph.load()

    def names(self, items):
        return {x["name"] for x in items}

    def test_known_forward_chain(self):
        t = self.g.trace_endpoint("Technician/GetSiriusWorkOrderHistory")
        self.assertEqual(t["endpoint"], HIST)
        self.assertEqual(t["file"], "Sirius/Controllers/TechnicianController.cs")
        self.assertAlmostEqual(t["line"], 2093, delta=5)
        m = [x for x in t["methods"] if x["name"] == "TechnicianManager.GetSiriusWorkOrderHistory"]
        self.assertTrue(m)
        self.assertAlmostEqual(m[0]["line"], 6131, delta=5)
        self.assertIn("dbo.GetSiriusWorkOrderHistory", self.names(t["db_objects"]["funcs"]))
        read = self.names(t["tables_read"])
        for tb in ("Operation.WorkOrderGpsInfo", "Operation.WorkOrder", "Catalog.Status", "Catalog.AppTranslationEntry"):
            self.assertIn(tb, read)
        self.assertEqual(t["tables_written"], [])
        self.assertTrue(all("evidence" in x for x in t["tables_read"]))
        self.assertTrue(any(f["path"].endswith("TechnicianController.cs") and f["line"] for f in t["files"]))

    def test_known_callers(self):
        t = self.g.trace_endpoint("api/Technician/GetSiriusWorkOrderHistory/123")
        self.assertEqual(t["endpoint"], HIST)
        self.assertEqual(set(t["callers_by_app"]), {"portal", "crm", "partner", "technician"})
        self.assertIn("src/views/Operation/WorkOrderDetail/History/index.js", t["callers_by_app"]["portal"])

    def test_screen_trace(self):
        s = self.g.trace_screen("portal", "src/views/Operation/WorkOrderDetail/History/index.js")
        self.assertEqual([e["endpoint"] for e in s["endpoints"]], [HIST])
        self.assertIn("Operation.WorkOrderGpsInfo", self.names(s["tables_read"]))
        s2 = self.g.trace_screen("portal", "WorkOrderDetail/History")
        self.assertTrue(s2["screens"])

    def test_reverse_gps_info(self):
        r = self.g.trace_db_object("Operation.WorkOrderGpsInfo")
        self.assertIn(HIST, {e["endpoint"] for e in self.g.trace_db_object("Operation.WorkOrderGpsInfo", limit=2000)["endpoints"]})
        self.assertGreaterEqual(len(r["apps"]), 2)
        self.assertIn("dbo.GetSiriusWorkOrderHistory", self.names(r["readers"]))

    def test_trigger_chain(self):
        # a status update writes Operation.WorkOrder -> TR_WorkOrder_U writes Log.WorkOrderJsonLog
        t = self.g.trace_endpoint("POST api/Technician/SetSiriusWorkOrderStatusUpdate")
        self.assertNotIn("error", t)
        self.assertIn("Operation.WorkOrder", self.names(t["tables_written"]))
        trg = [x for x in t["triggers"] if x["name"] == "TR_WorkOrder_U"]
        self.assertTrue(trg)
        self.assertIn("Log.WorkOrderJsonLog", trg[0]["writes"])
        self.assertIn("Log.WorkOrderJsonLog", self.names(t["tables_written"]))

    def test_db_object_forms(self):
        a = self.g.trace_db_object("dbo.GetSiriusWorkOrderHistory")
        b = self.g.trace_db_object("GetSiriusWorkOrderHistory")
        self.assertEqual(a["id"], b["id"])
        self.assertIn(HIST, {e["endpoint"] for e in a["endpoints"]})
        c = self.g.trace_db_object("Operation.WorkOrder.StatusId")
        self.assertTrue(c["column"]["exists"])
        self.assertIn("error", self.g.trace_db_object("Nope.DoesNotExist"))

    def test_backups_excluded_by_default(self):
        names = {x["name"] for x in self.g.trace_db_object("Operation.WorkOrder", limit=1)["writers"]}
        self.assertFalse(any(self.g.nodes["proc:" + n].get("is_backup_or_copy") for n in names if "proc:" + n in self.g.nodes))
        for h in self.g.search_text("work order", limit=30):
            self.assertFalse(h.get("is_backup_or_copy"))

    def test_error_text_db_trigger(self):
        hits = self.g.search_error_text("Failed to change status please check data!", limit=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["origin"], "TR_WorkOrder_U")
        self.assertTrue(hits[0]["file"] and hits[0]["line"])
        self.assertGreater(hits[0]["n_endpoints"], 0)

    def test_error_text_backend(self):
        hits = self.g.search_error_text("Body required.", limit=5)
        self.assertEqual(hits[0]["kind"], "backend_endpoint")
        self.assertGreater(hits[0]["score"], 0.9)

    def test_error_text_i18n(self):
        hits = self.g.search_error_text("Yeni kanal ekle", limit=5)
        self.assertTrue(any(h["kind"] == "i18n" for h in hits))

    def test_search_text(self):
        hits = self.g.search_text("work order history tab shows nothing", limit=10)
        self.assertTrue(any(h["name"] == HIST for h in hits[:5]))
        self.assertTrue(all(h["evidence"] for h in hits))
        only = self.g.search_text("work order history", app="crm", limit=10)
        self.assertTrue(all(h.get("app", "crm") == "crm" for h in only if h["type"] == "screen"))
        self.assertEqual(self.g.search_text("", limit=3), [])

    def test_json_serializable(self):
        import json
        json.dumps(self.g.trace_endpoint("Technician/GetSiriusWorkOrderHistory"))
        json.dumps(self.g.trace_db_object("Operation.WorkOrder"))
        json.dumps(self.g.search_error_text("Wrong Service Shop Id"))


if __name__ == "__main__":
    unittest.main()
