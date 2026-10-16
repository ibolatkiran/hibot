import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hibot.db import safe_sql, templates  # noqa: E402
from hibot.db.catalog import get_catalog  # noqa: E402
from hibot.db.templates import TemplateError, TemplateRegistry  # noqa: E402

CAT = get_catalog()
REG = TemplateRegistry.load(catalog=CAT)

REQUIRED_IDS = """
wo_header wo_status_timeline wo_history_function wo_field_changes wo_status_transition_check
wo_status_updater_log workcase_tree wo_lines_totals wo_erp_sap sap_rfc_rest_calls material_orders
db_errors_for_wo db_errors_recent mobile_api_trace notifications_for_wo outbound_queue_backlog
job_health technician_appointment_context product_sku_lookup tenant_lookup user_access_lookup
""".split()


class TestLibrary(unittest.TestCase):
    def test_all_requested_templates_exist(self):
        self.assertEqual(sorted(set(REQUIRED_IDS) - set(REG.ids())), [])

    def test_every_template_valid_for_every_declared_region(self):
        for t in REG:
            rep = REG.validate_template(t)
            with self.subTest(template=t.id):
                self.assertEqual(rep.errors, [], rep.errors)

    def test_no_warnings_in_shipped_templates(self):
        for t in REG:
            rep = REG.validate_template(t)
            with self.subTest(template=t.id):
                self.assertEqual(rep.warnings, [])

    def test_validation_really_passes_safe_sql_per_region(self):
        for t in REG:
            for region in t.regions:
                with self.subTest(template=t.id, region=region):
                    r = safe_sql.validate(t.sql, region=region, catalog=CAT, row_limit=t.row_limit,
                                          allowed_params=[p.name for p in t.params], allow_pii=True)
                    self.assertTrue(r.ok, r.errors)
                    self.assertNotIn("@", r.positional_sql.replace("'@", ""))  # all params positional

    def test_templates_are_rejected_as_adhoc_when_they_touch_pii(self):
        # the same SQL is NOT acceptable as ad-hoc SQL if it selects PII columns
        t = REG.get("wo_header")
        r = safe_sql.validate(t.sql, region="EU", catalog=CAT, allowed_params=[p.name for p in t.params],
                              allow_pii=False)
        self.assertFalse(r.ok)

    def test_region_availability_is_declared_truthfully(self):
        eu_only = {t.id for t in REG if t.regions == ["EU"]}
        self.assertTrue({"wo_status_updater_log", "mobile_api_trace", "wo_discount_log", "wo_debug_log"} <= eu_only)
        both = {t.id for t in REG if set(t.regions) == {"EU", "APAC"}}
        self.assertTrue({"wo_header", "wo_status_timeline", "wo_history_function", "wo_field_changes"} <= both)

    def test_every_param_has_description_and_template_has_interpretation(self):
        for t in REG:
            with self.subTest(template=t.id):
                self.assertTrue(t.interpret and t.purpose and t.title)
                self.assertTrue(all(p.description for p in t.params))
                self.assertTrue(t.next)

    def test_tenant_scoped_templates_require_tenant(self):
        for t in REG:
            reads_tenant_table = any((CAT.get(n) and CAT.get(n).has_tenant_id) for n in t.tables)
            if reads_tenant_table:
                p = t.param("TenantId")
                with self.subTest(template=t.id):
                    self.assertIsNotNone(p)

    def test_sql_never_formats_values_into_text(self):
        for t in REG:
            with self.subTest(template=t.id):
                self.assertNotIn("{", t.sql)
                self.assertNotIn("%s", t.sql)

    def test_catalog_text_compact(self):
        txt = REG.catalog_text("APAC", with_purpose=False)
        self.assertIn("wo_header", txt)
        self.assertNotIn("mobile_api_trace", txt)       # EU only
        self.assertLess(len(REG.catalog_text()), 20000)
        self.assertIn("TenantId:int!", txt)

    def test_describe(self):
        self.assertIn("interpret:", REG.describe("wo_header"))


class TestParams(unittest.TestCase):
    def test_prepare_positional_args_order(self):
        p = REG.prepare("wo_status_timeline", {"WorkOrderId": 5, "TenantId": 9}, "EU")
        self.assertEqual(p.sql.count("?"), len(p.args))
        self.assertIn(5, p.args)
        self.assertIn(9, p.args)
        self.assertNotIn("@", p.sql)

    def test_snake_case_and_string_numbers(self):
        p = REG.prepare("wo_status_timeline", {"work_order_id": "77", "tenant_id": "3"}, "APAC")
        self.assertIn(77, p.args)

    def test_missing_required(self):
        with self.assertRaises(TemplateError):
            REG.prepare("wo_status_timeline", {"WorkOrderId": 5}, "EU")

    def test_unknown_param_rejected(self):
        with self.assertRaises(TemplateError):
            REG.prepare("wo_status_timeline", {"WorkOrderId": 5, "TenantId": 1, "Evil": "x"}, "EU")

    def test_require_any(self):
        with self.assertRaises(TemplateError):
            REG.prepare("wo_header", {"TenantId": 1}, "EU")
        p = REG.prepare("wo_header", {"TenantId": 1, "ExternalId": "X-1"}, "EU")
        self.assertIn("X-1", p.args)

    def test_region_not_available(self):
        with self.assertRaises(TemplateError) as cm:
            REG.prepare("wo_status_updater_log", {"WorkOrderId": 1, "TenantId": 1}, "APAC")
        self.assertIn("not available", str(cm.exception))

    def test_unknown_query_id(self):
        with self.assertRaises(TemplateError):
            REG.prepare("drop_everything", {}, "EU")

    def test_injection_payload_stays_a_parameter(self):
        evil = "x'; DROP TABLE Operation.WorkOrder; --"
        p = REG.prepare("wo_header", {"TenantId": 1, "ExternalId": evil}, "EU")
        self.assertNotIn("DROP", p.sql)
        self.assertIn(evil, p.args)

    def test_int_validation(self):
        for bad in ["1; DROP", 1.5, True, [1], "abc", 2 ** 40]:
            with self.subTest(bad=bad):
                with self.assertRaises(TemplateError):
                    REG.prepare("wo_status_timeline", {"WorkOrderId": bad, "TenantId": 1}, "EU")

    def test_string_limits_and_control_chars(self):
        with self.assertRaises(TemplateError):
            REG.prepare("wo_header", {"TenantId": 1, "ExternalWorkOrderId": "x" * 13}, "EU")
        with self.assertRaises(TemplateError):
            REG.prepare("wo_header", {"TenantId": 1, "ExternalId": "a\x00b"}, "EU")

    def test_like_param_escaped(self):
        p = REG.prepare("db_errors_recent", {"ErrorProcedure": "Get_[x]%"}, "EU")
        self.assertIn("Get[_][[]x][%]", p.args)

    def test_dates(self):
        p = REG.prepare("db_errors_recent", {"FromDate": "2025-03-01", "ToDate": datetime.datetime(2025, 3, 2, 10)}, "EU")
        self.assertIn(datetime.datetime(2025, 3, 1), p.args)
        with self.assertRaises(TemplateError):
            REG.prepare("db_errors_recent", {"FromDate": "yesterday"}, "EU")
        with self.assertRaises(TemplateError):
            REG.prepare("db_errors_recent", {"FromDate": "2025-03-01'; DROP"}, "EU")

    def test_defaults_applied(self):
        p = REG.prepare("job_health", {}, "APAC")
        self.assertEqual(p.params["WindowHours"], 24)
        with self.assertRaises(TemplateError):
            REG.prepare("job_health", {"WindowHours": 100000}, "APAC")

    def test_bool(self):
        p = REG.prepare("sap_rfc_rest_calls", {"IdValue": 1, "TenantId": 1, "FailedOnly": True}, "EU")
        self.assertEqual(p.params["FailedOnly"], 1)


class TestParsingAndValidationOfBadTemplates(unittest.TestCase):
    GOOD = """/*
id: t1
title: T
purpose: p
param: TenantId | int | required | tenant
regions: EU, APAC
tables: Catalog.Status
interpret: i
next:
*/
SELECT s.Id FROM Catalog.Status s WHERE s.TenantId = @TenantId
"""

    def _registry(self, text, name="t1"):
        d = tempfile.mkdtemp()
        Path(d, name + ".sql").write_text(text, encoding="utf-8")
        return TemplateRegistry.load(d, catalog=CAT)

    def test_good(self):
        reg = self._registry(self.GOOD)
        self.assertTrue(reg.validate_template(reg.get("t1")).ok)

    def test_missing_header(self):
        with self.assertRaises(TemplateError):
            self._registry("SELECT 1")

    def test_id_must_match_filename(self):
        with self.assertRaises(TemplateError):
            self._registry(self.GOOD, name="other")

    def test_unknown_table_detected(self):
        reg = self._registry(self.GOOD.replace("Catalog.Status s", "Catalog.NoSuchTable s").replace("Catalog.Status\n", "Catalog.NoSuchTable\n"))
        self.assertFalse(reg.validate_template(reg.get("t1")).ok)

    def test_dml_template_detected(self):
        reg = self._registry(self.GOOD.replace("SELECT s.Id FROM", "DELETE FROM"))
        self.assertFalse(reg.validate_template(reg.get("t1")).ok)

    def test_undeclared_param_detected(self):
        reg = self._registry(self.GOOD.replace("= @TenantId", "= @TenantId AND s.Id = @Other"))
        rep = reg.validate_template(reg.get("t1"))
        self.assertFalse(rep.ok)

    def test_header_tables_must_match(self):
        reg = self._registry(self.GOOD.replace("tables: Catalog.Status", "tables: Catalog.Status, Catalog.Priority"))
        self.assertFalse(reg.validate_template(reg.get("t1")).ok)

    def test_wrong_region_declared(self):
        reg = self._registry(self.GOOD.replace("Catalog.Status s", "dbo.StatusUpdaterLog s").replace("tables: Catalog.Status", "tables: dbo.StatusUpdaterLog").replace("s.TenantId = @TenantId", "s.WorkOrderId = @TenantId"))
        self.assertFalse(reg.validate_template(reg.get("t1")).ok)

    def test_undeclared_pii_detected(self):
        t = self.GOOD.replace("SELECT s.Id FROM Catalog.Status s WHERE s.TenantId = @TenantId",
                              "SELECT w.ContactPhone FROM Operation.WorkOrder w WHERE w.TenantId = @TenantId").replace(
            "tables: Catalog.Status", "tables: Operation.WorkOrder")
        reg = self._registry(t)
        rep = reg.validate_template(reg.get("t1"))
        self.assertFalse(rep.ok)
        self.assertTrue(any("PII" in e for e in rep.errors))
        reg2 = self._registry(t.replace("interpret: i", "pii: ContactPhone:phone\ninterpret: i"))
        self.assertTrue(reg2.validate_template(reg2.get("t1")).ok)

    def test_unknown_header_key(self):
        with self.assertRaises(TemplateError):
            self._registry(self.GOOD.replace("title: T", "title: T\nbogus: 1"))


if __name__ == "__main__":
    unittest.main()
