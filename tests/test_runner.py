import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hibot.db import runner  # noqa: E402
from hibot.db.catalog import get_catalog  # noqa: E402
from hibot.db.templates import TemplateRegistry  # noqa: E402

CAT = get_catalog()
REG = TemplateRegistry.load(catalog=CAT)
CONN = "DRIVER={ODBC Driver 18 for SQL Server};SERVER=tcp:secret-host.example.net;DATABASE=Db;UID=svc_ro;PWD=Sup3rS3cret!"


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, list(params) if params else []))
        if sql.startswith("SET "):
            return self
        if self.conn.fail:
            raise RuntimeError(self.conn.fail)
        self.description = [(c,) for c in self.conn.columns]
        self._rows = list(self.conn.rows)
        return self

    def fetchmany(self, n):
        self.conn.fetched = n
        out, self._rows = self._rows[:n], self._rows[n:]
        return out

    def close(self):
        pass


class FakeConn:
    def __init__(self, columns=("A",), rows=(), fail=None):
        self.columns, self.rows, self.fail = list(columns), list(rows), fail
        self.executed = []
        self.fetched = None
        self.timeout = None
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def make(tmp, conn, env=None, region="EU"):
    audit = runner.AuditLog(Path(tmp) / "audit.jsonl")
    seen = {}

    def connect(cs, timeout):
        seen["cs"] = cs
        seen["timeout"] = timeout
        return conn

    r = runner.QueryRunner(region, registry=REG, catalog=CAT, audit=audit, connect=connect,
                           env=env if env is not None else {"HIBOT_DB_EU": CONN, "HIBOT_DB_APAC": CONN})
    return r, audit, seen


def audit_lines(audit):
    return [json.loads(l) for l in Path(audit.path).read_text(encoding="utf-8").splitlines()]


class TestConnectionString(unittest.TestCase):
    def test_appends_readonly_intent(self):
        cs = runner.build_connection_string("Server=x;Database=y;")
        self.assertIn("ApplicationIntent=ReadOnly", cs)

    def test_keeps_existing_intent(self):
        cs = runner.build_connection_string("Server=x;ApplicationIntent=ReadWrite;")
        self.assertEqual(cs.lower().count("applicationintent"), 1)

    def test_missing_env_var_message_has_no_secret(self):
        with self.assertRaises(runner.RunnerError) as cm:
            runner.connection_string_for("EU", {})
        self.assertIn("HIBOT_DB_EU", str(cm.exception))

    def test_scrub(self):
        out = runner.scrub("Login failed; UID=svc;PWD=Sup3rS3cret!;Server=secret-host", [CONN])
        self.assertNotIn("Sup3rS3cret", out)
        self.assertNotIn("secret-host", out)


class TestRunner(unittest.TestCase):
    def test_template_run_masks_and_audits(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = FakeConn(columns=["WorkOrderId", "ContactName", "ContactPhone", "StatusNote"],
                            rows=[[1, "Ayse Yilmaz", "05321234567", "mail me at a.b@example.com"]])
            r, audit, seen = make(tmp, conn)
            res = r.run_template("wo_header", {"TenantId": 5, "WorkOrderId": 1})
            self.assertTrue(res.ok, res.error)
            row = res.rows[0]
            self.assertEqual(row[1], "A*** Y***")
            self.assertNotIn("0532", row[2])
            self.assertNotIn("a.b@example.com", row[3])
            self.assertIn("ApplicationIntent=ReadOnly", seen["cs"])
            executed = [e[0] for e in conn.executed]
            self.assertIn("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED", executed)
            sql, params = conn.executed[-1]
            self.assertIn("?", sql)
            self.assertNotIn("@", sql)
            self.assertIn(5, params)
            self.assertEqual(conn.timeout, runner.DEFAULT_TIMEOUT_SECONDS)
            line = audit_lines(audit)[-1]
            self.assertEqual(line["query_id"], "wo_header")
            self.assertEqual(line["region"], "EU")
            self.assertEqual(line["row_count"], 1)
            self.assertIsNone(line["error"])
            self.assertEqual(line["params"]["TenantId"], 5)
            blob = json.dumps(line)
            self.assertNotIn("Ayse", blob)
            self.assertNotIn("0532", blob)
            self.assertNotIn("Sup3rS3cret", blob)

    def test_row_cap_and_truncated_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = FakeConn(columns=["Id"], rows=[[i] for i in range(5000)])
            r, audit, _ = make(tmp, conn)
            res = r.run_sql("SELECT Id FROM Catalog.Status", row_limit=50)
            self.assertTrue(res.ok, res.error)
            self.assertEqual(res.row_count, 50)
            self.assertTrue(res.truncated)
            self.assertEqual(conn.fetched, 51)
            self.assertIn("TOP 50", conn.executed[-1][0])
            self.assertEqual(audit_lines(audit)[-1]["query_id"], "adhoc")
            self.assertTrue(audit_lines(audit)[-1]["truncated"])

    def test_not_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = FakeConn(columns=["Id"], rows=[[1], [2]])
            r, _, _ = make(tmp, conn)
            res = r.run_sql("SELECT Id FROM Catalog.Status")
            self.assertFalse(res.truncated)
            self.assertEqual(res.row_count, 2)

    def test_adhoc_rejected_never_reaches_db_but_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = FakeConn()
            r, audit, seen = make(tmp, conn)
            res = r.run_sql("DELETE FROM Operation.WorkOrder")
            self.assertFalse(res.ok)
            self.assertEqual(conn.executed, [])
            self.assertNotIn("cs", seen)            # not even connected
            line = audit_lines(audit)[-1]
            self.assertTrue(line["error"].startswith("rejected"))
            self.assertEqual(line["query_id"], "adhoc")

    def test_bad_template_params_rejected_and_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = FakeConn()
            r, audit, _ = make(tmp, conn)
            res = r.run_template("wo_header", {"TenantId": 1})
            self.assertFalse(res.ok)
            self.assertEqual(conn.executed, [])
            self.assertEqual(audit_lines(audit)[-1]["query_id"], "wo_header")

    def test_db_error_is_scrubbed_and_connection_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = FakeConn(fail="Login failed for user; UID=svc_ro;PWD=Sup3rS3cret!;Server=secret-host.example.net; "
                                 "converting 'john@example.com'")
            r, audit, _ = make(tmp, conn)
            res = r.run_sql("SELECT Id FROM Catalog.Status")
            self.assertFalse(res.ok)
            for leak in ("Sup3rS3cret", "secret-host", "john@example.com"):
                self.assertNotIn(leak, res.error)
                self.assertNotIn(leak, json.dumps(audit_lines(audit)))
            self.assertTrue(conn.closed)

    def test_missing_connection_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, audit, _ = make(tmp, FakeConn(), env={})
            res = r.run_sql("SELECT Id FROM Catalog.Status")
            self.assertFalse(res.ok)
            self.assertIn("HIBOT_DB_EU", res.error)

    def test_audit_unwritable_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = FakeConn()
            r, audit, seen = make(tmp, conn)
            audit.path = Path(tmp) / "a_file" / "sub" / "audit.jsonl"
            Path(tmp, "a_file").write_text("x")          # parent is a file -> mkdir fails
            with self.assertRaises(runner.AuditError):
                r.run_sql("SELECT Id FROM Catalog.Status")
            self.assertEqual(conn.executed, [])

    def test_request_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = FakeConn(columns=["Id"], rows=[[1]])
            r, _, _ = make(tmp, conn)
            self.assertTrue(r.run_request({"query_id": "tenant_lookup", "params": {"CountryCode": "TR"}}).ok)
            self.assertTrue(r.run_request({"sql": "SELECT Id FROM Catalog.Status"}).ok)
            self.assertFalse(r.run_request({"foo": 1}).ok)
            self.assertFalse(r.run_request("DROP TABLE x").ok)

    def test_region_apac_uses_apac_env_and_rejects_eu_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = FakeConn(columns=["Id"], rows=[[1]])
            r, _, seen = make(tmp, conn, region="APAC", env={"HIBOT_DB_APAC": "Server=a;"})
            self.assertFalse(r.run_template("mobile_api_trace", {"WorkOrderId": 1, "TenantId": 1}).ok)
            self.assertTrue(r.run_template("tenant_lookup", {"CountryCode": "TR"}).ok)
            self.assertIn("Server=a", seen["cs"])

    def test_no_result_set(self):
        class NoRs(FakeConn):
            def cursor(self):
                c = FakeCursor(self)
                orig = c.execute

                def ex(sql, params=None):
                    orig(sql, params)
                    if not sql.startswith("SET "):
                        c.description = None
                    return c
                c.execute = ex
                return c
        with tempfile.TemporaryDirectory() as tmp:
            r, _, _ = make(tmp, NoRs())
            self.assertFalse(r.run_sql("SELECT Id FROM Catalog.Status").ok)

    def test_pyodbc_not_required_at_import(self):
        self.assertNotIn("pyodbc", sys.modules)


if __name__ == "__main__":
    unittest.main()
