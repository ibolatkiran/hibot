"""Tool layer + MCP server tests.  No network, no DB, no LLM."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hibot import mcp_server, tools
from hibot.agent import config
from hibot.db.runner import AuditLog, QueryResult, QueryRunner
from hibot.tools import _common, code, data

HAVE_GIT = shutil.which("git") is not None


class FakeRepoCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hibot_tools_")
        cls.repo = Path(cls.tmp) / "FakeRepo"
        w = cls._w
        w("src/Service.cs", "class A {\n  var x = NEEDLE_TOKEN;\n  // password = \"hunter2secret\" NEEDLE_TOKEN\n}\n")
        w("src/view.js", "const a = 1;\nconst NEEDLE_TOKEN = 2;\n")
        w("appsettings.json", '{"k": "NEEDLE_TOKEN"}')
        w("appsettings.Prod.json", '{"k": "NEEDLE_TOKEN"}')
        w(".env", "NEEDLE_TOKEN=1")
        w(".env.local", "NEEDLE_TOKEN=1")
        w("translate.js", "NEEDLE_TOKEN")
        w("web.config", "NEEDLE_TOKEN")
        w("cert.pem", "NEEDLE_TOKEN")
        w("node_modules/pkg/index.js", "NEEDLE_TOKEN")
        w("bin/Debug/out.cs", "NEEDLE_TOKEN")
        w("build/gen.js", "NEEDLE_TOKEN")
        w("many/m.cs", "\n".join("NEEDLE_MANY %d " % i + "x" * 200 for i in range(300)))
        cls._old = os.environ.get("HIBOT_REPO_ROOTS")
        os.environ["HIBOT_REPO_ROOTS"] = str(cls.repo)

    @classmethod
    def _w(cls, rel, text):
        p = cls.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        if cls._old is None:
            os.environ.pop("HIBOT_REPO_ROOTS", None)
        else:
            os.environ["HIBOT_REPO_ROOTS"] = cls._old
        shutil.rmtree(cls.tmp, ignore_errors=True)


class CodeSearchTests(FakeRepoCase):
    def setUp(self):
        p = mock.patch.object(code, "rg_path", return_value=None)     # force the Python engine
        p.start()
        self.addCleanup(p.stop)

    def test_finds_and_denies_secrets(self):
        r = tools.code_search("NEEDLE_TOKEN", literal=True)
        self.assertTrue(r["ok"])
        files = {h["file"] for h in r["hits"]}
        self.assertEqual(files, {"FakeRepo/src/Service.cs", "FakeRepo/src/view.js"})
        self.assertEqual(r["engine"], "python")

    def test_credentials_masked(self):
        r = tools.code_search("NEEDLE_TOKEN")
        blob = json.dumps(r)
        self.assertNotIn("hunter2secret", blob)

    def test_glob_and_regex(self):
        r = tools.code_search(r"NEEDLE_\w+", glob="*.js")
        self.assertEqual([h["file"] for h in r["hits"]], ["FakeRepo/src/view.js"])
        self.assertEqual(r["hits"][0]["line"], 2)

    def test_context_and_repo_filter(self):
        r = tools.code_search("NEEDLE_TOKEN", repos=["fake"], glob="*.js", context=1)
        self.assertEqual(len(r["hits"][0]["context"]), 1)
        bad = tools.code_search("x", repos=["nope"])
        self.assertFalse(bad["ok"])

    def test_size_caps(self):
        r = tools.code_search("NEEDLE_MANY", max_results=500)
        self.assertTrue(r["ok"])
        self.assertLessEqual(len(json.dumps(r).encode()), _common.MAX_BYTES)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["count"], len(r["hits"]))
        r2 = tools.code_search("NEEDLE_MANY", max_results=3)
        self.assertEqual(r2["count"], 3)

    def test_input_validation(self):
        self.assertFalse(tools.code_search("")["ok"])
        self.assertFalse(tools.code_search("(unclosed")["ok"])
        self.assertFalse(tools.code_search("a" * 400)["ok"])

    def test_rg_output_is_refiltered(self):
        root = self.repo
        lines = [json.dumps({"type": "match", "data": {"path": {"text": str(root / n)}, "lines": {"text": "NEEDLE_TOKEN\n"},
                                                       "line_number": 1}})
                 for n in ("appsettings.json", ".env", "node_modules/pkg/index.js", "src/view.js", "cert.pem")]
        hits = code.parse_rg_json(iter(lines), [root], None, 50)
        self.assertEqual([h["file"] for h in hits], ["FakeRepo/src/view.js"])


class ReadFileTests(FakeRepoCase):
    def test_delegates_to_read_source(self):
        with mock.patch("hibot.agent.safety.read_source",
                        return_value={"ok": True, "text": "1| a", "start_line": 1, "end_line": 1}) as m:
            r = tools.read_file("FakeRepo/src/view.js", 1, 5)
        m.assert_called_once()
        self.assertEqual(m.call_args.kwargs["max_lines"], 250)
        self.assertTrue(r["ok"])

    def test_real_read_and_denials(self):
        r = tools.read_file("FakeRepo/src/Service.cs", 1, 3)
        self.assertTrue(r["ok"])
        self.assertNotIn("hunter2secret", r["text"])
        for bad in ("FakeRepo/appsettings.json", "FakeRepo/.env", "FakeRepo/../x.cs", "FakeRepo/cert.pem",
                    "FakeRepo/node_modules/pkg/index.js"):
            self.assertFalse(tools.read_file(bad)["ok"], bad)


class SqlTests(unittest.TestCase):
    def tearDown(self):
        tools.set_runner_factory(None)

    def test_validation_rejection_path(self):
        tmpd = tempfile.mkdtemp()
        try:
            def connect(*a):
                raise AssertionError("must not connect")
            tools.set_runner_factory(lambda region: QueryRunner(region, audit=AuditLog(Path(tmpd) / "a.jsonl"),
                                                                connect=connect, env={"HIBOT_DB_EU": "x"}))
            r = tools.sql("DELETE FROM Catalog.Tenant")
            self.assertFalse(r["ok"])
            self.assertTrue(r["error"])
            self.assertTrue((Path(tmpd) / "a.jsonl").exists())             # rejection is audited
            r2 = tools.sql("SELECT Id FROM Catalog.Tenant; DROP TABLE x")
            self.assertFalse(r2["ok"])
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)

    def test_argument_checks_and_fake_result(self):
        class Fake:
            def run_sql(self, q, row_limit=200):
                return QueryResult(ok=True, region="EU", query_id="adhoc", columns=["Id"],
                                   rows=[[i] for i in range(5000)], row_count=5000)
        tools.set_runner_factory(lambda region: Fake())
        r = tools.sql("SELECT Id FROM Catalog.Tenant")
        self.assertTrue(r["ok"])
        self.assertTrue(r["truncated"])
        self.assertLessEqual(len(json.dumps(r).encode()), _common.MAX_BYTES)
        self.assertFalse(tools.sql("SELECT 1", region="MARS")["ok"])
        self.assertFalse(tools.sql("")["ok"])
        self.assertFalse(tools.run_template("bad id!")["ok"])

    def test_list_templates(self):
        r = tools.list_templates()
        if r["ok"]:
            self.assertIn("wo_header", r["ids"])
        self.assertFalse(tools.list_templates("no_such_template")["ok"])


@unittest.skipUnless((config.knowledge_dir() / "schema" / "objects.json").exists(), "schema catalog not built")
class ColumnWritersTests(unittest.TestCase):
    def test_labour_cost(self):
        r = tools.column_writers("Operation.WorkOrder", "LabourCost")
        self.assertTrue(r["ok"], r)
        names = {w["name"] for w in r["writers"]}
        self.assertIn("dbo.PricingWorkOrder", names)
        w = next(w for w in r["writers"] if w["name"] == "dbo.PricingWorkOrder")
        self.assertTrue(w["assign_lines"])
        self.assertTrue(w["source"].endswith("PricingWorkOrder.sql"))

    def test_unknown(self):
        self.assertFalse(tools.column_writers("No.Such", "X")["ok"])
        self.assertFalse(tools.column_writers("Operation.WorkOrder", "bad col;")["ok"])


@unittest.skipUnless(HAVE_GIT, "git not installed")
class GitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hibot_git_")
        cls.repo = Path(cls.tmp) / "GitRepo"
        cls.repo.mkdir()
        env = dict(os.environ, GIT_AUTHOR_NAME="Jane Doe", GIT_AUTHOR_EMAIL="jane.doe@example.com",
                   GIT_COMMITTER_NAME="Jane Doe", GIT_COMMITTER_EMAIL="jane.doe@example.com")

        def git(*a):
            subprocess.run(["git", "-C", str(cls.repo)] + list(a), check=True, env=env, capture_output=True)
        git("init", "-q")
        (cls.repo / "a.cs").write_text("one\ntwo\nthree\n", encoding="utf-8")
        git("add", ".")
        git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "first commit FIXME-1")
        (cls.repo / "a.cs").write_text("one\nTWO\nthree\n", encoding="utf-8")
        git("-c", "commit.gpgsign=false", "commit", "-qam", "second change")
        cls._old = os.environ.get("HIBOT_REPO_ROOTS")
        os.environ["HIBOT_REPO_ROOTS"] = str(cls.repo)

    @classmethod
    def tearDownClass(cls):
        if cls._old is None:
            os.environ.pop("HIBOT_REPO_ROOTS", None)
        else:
            os.environ["HIBOT_REPO_ROOTS"] = cls._old

        def onerr(f, p, e):
            os.chmod(p, 0o700)
            f(p)
        shutil.rmtree(cls.tmp, onerror=onerr)

    def test_log_strips_emails_and_names(self):
        r = tools.git_log("GitRepo", max_count=10)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["count"], 2)
        blob = json.dumps(r)
        self.assertNotIn("example.com", blob)
        self.assertNotIn("Jane", blob)
        self.assertEqual(r["commits"][0]["author"], "JD")
        g = tools.git_log("GitRepo", path="a.cs", grep="FIXME-1")
        self.assertEqual(g["count"], 1)

    def test_log_validation(self):
        self.assertFalse(tools.git_log("GitRepo", path="../x")["ok"])
        self.assertFalse(tools.git_log("GitRepo", path="appsettings.json")["ok"])
        self.assertFalse(tools.git_log("GitRepo", since="1 day; rm -rf")["ok"])
        self.assertFalse(tools.git_log("Unknown")["ok"])

    def test_blame(self):
        r = tools.git_blame("GitRepo", "a.cs", 1, 3)
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(r["lines"]), 3)
        self.assertNotIn("example.com", json.dumps(r))
        self.assertEqual(r["lines"][1]["summary"], "second change")


class RegistryAndMcpTests(FakeRepoCase):
    def test_catalog_lists_all_tools(self):
        txt = tools.tool_catalog()
        for t in tools.TOOLS:
            self.assertIn(t.name + "(", txt)
        for n in ("code_search", "read_file", "sql", "run_template", "list_templates", "column_writers", "similar_tickets",
                  "ticket", "ticket_window", "cards_match", "cards_list", "git_log", "git_blame", "graph_trace_endpoint",
                  "graph_trace_screen", "graph_trace_db_object", "graph_search_text", "graph_search_error_text"):
            self.assertIn(n, tools.BY_NAME)

    def test_call_tool_validation(self):
        self.assertFalse(tools.call_tool("nope", {})["ok"])
        self.assertIn("missing", tools.call_tool("code_search", {})["error"])
        self.assertIn("unknown argument", tools.call_tool("code_search", {"pattern": "x", "evil": 1})["error"])
        self.assertTrue(tools.call_tool("code_search", {"pattern": "NEEDLE_TOKEN", "max_results": "2", "literal": "true"})["ok"])

    def test_finish_caps(self):
        r = _common.finish({"rows": [["x" * 100] * 10 for _ in range(500)], "s": "y" * 50000}, 4096)
        self.assertLessEqual(len(json.dumps(r).encode()), 4096)
        self.assertTrue(r["truncated"])

    def test_mcp_roundtrip(self):
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "code_search", "arguments": {"pattern": "NEEDLE_TOKEN", "glob": "*.js", "literal": True}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "x"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "bogus"},
        ]
        out = io.StringIO()
        with mock.patch.object(code, "rg_path", return_value=None):
            mcp_server.serve(io.StringIO("\n".join(json.dumps(r) for r in reqs) + "\nnot json\n"), out)
        resp = [json.loads(x) for x in out.getvalue().splitlines()]
        by_id = {r.get("id"): r for r in resp}
        self.assertEqual(len(resp), 6)                                  # no reply to the notification; parse error has id null
        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", by_id[1]["result"]["capabilities"])
        names = {t["name"] for t in by_id[2]["result"]["tools"]}
        self.assertEqual(names, set(tools.BY_NAME))
        for t in by_id[2]["result"]["tools"]:
            self.assertEqual(t["inputSchema"]["type"], "object")
        res = json.loads(by_id[3]["result"]["content"][0]["text"])
        self.assertFalse(by_id[3]["result"]["isError"])
        self.assertEqual(res["hits"][0]["file"], "FakeRepo/src/view.js")
        self.assertTrue(by_id[4]["result"]["isError"])
        self.assertEqual(by_id[5]["error"]["code"], -32601)
        self.assertEqual(by_id[None]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
