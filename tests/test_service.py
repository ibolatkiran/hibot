"""HiBot HTTP service: contract tests with a fake pipeline (no network, DB or LLM)."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient
    from hibot.service import app as svc_app
    from hibot.service import jobs
    HAVE = True
except ImportError:                                         # fastapi / httpx not installed
    HAVE = False

from hibot.agent import store

KEY = "test-key-123"
H = {"X-HiBot-Key": KEY}
REPORT = {"summary": "s", "root_cause_type": "config", "resolution_action": "data_fix", "classification": "config",
          "confidence": 0.8, "root_cause": "rc", "evidence": [], "code_locations": [], "fix_steps": ["x"]}


def fake_exec_factory(base, delay=0.0, fail=None):
    def fake(key, text, region, loop):
        if delay:
            time.sleep(delay)
        if fail:
            raise RuntimeError(fail)
        run = {"run_id": store.new_run_id(key), "report": dict(REPORT), "input": {"key": key, "text": text}, "incident": {"key": key},
               "queries": [], "steps": [], "region": region or "EU", "mode": "live"}
        d = store.save_run(run, "# rapor", base)
        run["run_dir"] = str(d)
        return run
    return fake


def wait_status(client, rid, want=("done", "failed"), t=5.0):
    end = time.time() + t
    while time.time() < end:
        r = client.get("/analyses/" + rid, headers=H).json()
        if r["status"] in want:
            return r
        time.sleep(0.02)
    raise AssertionError("timeout waiting for run, last=%s" % r)


@unittest.skipUnless(HAVE, "fastapi/httpx not installed")
class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / "runs"
        self.cards = Path(self.tmp.name) / "cards"
        self.cards.mkdir()
        env = mock.patch.dict(os.environ, {"HIBOT_CARDS_DIR": str(self.cards)})
        env.start()
        self.addCleanup(env.stop)
        self.patch_exec(fake_exec_factory(self.base))

    def patch_exec(self, fn):
        p = mock.patch.object(jobs, "execute_analysis", fn)
        p.start()
        self.addCleanup(p.stop)

    def client(self, **kw):
        app = svc_app.create_app(KEY, runs_dir=self.base, **kw)
        c = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(app.state.pool.stop)
        return c

    def post(self, c, **body):
        body.setdefault("requested_by", "u1")
        return c.post("/analyses", json=body, headers=H)

    def test_refuses_without_key(self):
        with self.assertRaises(RuntimeError):
            svc_app.create_app("", runs_dir=self.base)

    def test_auth(self):
        c = self.client()
        self.assertEqual(c.get("/health").status_code, 401)
        self.assertEqual(c.get("/health", headers={"X-HiBot-Key": "wrong"}).status_code, 401)
        self.assertEqual(c.post("/analyses", json={"ticket_key": "SM-1"}).status_code, 401)
        r = c.get("/health", headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertIn("db_regions", r.json())

    def test_create_poll_done(self):
        c = self.client()
        r = self.post(c, ticket_key="sm-2169006")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["status"], "queued")
        rid = r.json()["run_id"]
        res = wait_status(c, rid)
        self.assertEqual(res["status"], "done")
        self.assertEqual(res["ticket_key"], "SM-2169006")
        self.assertEqual(res["report"]["root_cause_type"], "config")
        self.assertEqual(res["report_markdown"], "# rapor")
        self.assertTrue(res["finished_at"])
        self.assertTrue((self.base / rid / "service_state.json").exists())

    def test_text_only_and_region(self):
        c = self.client()
        r = self.post(c, text="printer broken", region="apac")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(wait_status(c, r.json()["run_id"])["status"], "done")

    def test_pipeline_error_is_failed(self):
        self.patch_exec(fake_exec_factory(self.base, fail="boom"))
        c = self.client()
        res = wait_status(c, self.post(c, ticket_key="SM-1").json()["run_id"])
        self.assertEqual(res["status"], "failed")
        self.assertIn("boom", res["error"])
        self.assertNotIn("report", res)

    def test_timeout_fails(self):
        self.patch_exec(fake_exec_factory(self.base, delay=1.0))
        c = self.client(timeout=0.1)
        res = wait_status(c, self.post(c, ticket_key="SM-1").json()["run_id"])
        self.assertEqual(res["status"], "failed")
        self.assertIn("timed out", res["error"])

    def test_invalid_input(self):
        c = self.client()
        bad = [{}, {"ticket_key": "nope"}, {"ticket_key": "SM-1; drop"}, {"text": "x" * 8001}, {"text": "ok", "region": "US"},
               {"text": "ok", "requested_by": ""}, {"text": 5}]
        for b in bad:
            b.setdefault("requested_by", "u1") if b.get("requested_by", 1) != "" else None
            r = c.post("/analyses", json=b, headers=H)
            self.assertEqual(r.status_code, 400, b)
            self.assertIn("error", r.json())
        r = c.post("/analyses", content=b"not json", headers=H)
        self.assertEqual(r.status_code, 400)
        r = self.post(c, text="x" * 8000)
        self.assertEqual(r.status_code, 202)
        wait_status(c, r.json()["run_id"])

    def test_unknown_run(self):
        c = self.client()
        self.assertEqual(c.get("/analyses/20990101-000000-SM-1-abcd", headers=H).status_code, 404)
        self.assertEqual(c.get("/analyses/..%2f..", headers=H).status_code, 404)
        self.assertEqual(c.post("/analyses/nope-nope/feedback", json={"verdict": "correct", "by": "u"}, headers=H).status_code, 404)

    def test_feedback(self):
        c = self.client()
        rid = self.post(c, ticket_key="SM-5").json()["run_id"]
        wait_status(c, rid)
        calls = []

        def fake_fb(run_id, verdict, note="", **kw):
            calls.append((run_id, verdict, note))
            return {"feedback_file": "x", "actions": ["card foo updated (confirmed)"], "entry": {}}
        with mock.patch.object(svc_app.fbmod, "record_feedback", fake_fb):
            self.assertEqual(c.post("/analyses/%s/feedback" % rid, json={"verdict": "bad", "by": "u"}, headers=H).status_code, 400)
            self.assertEqual(c.post("/analyses/%s/feedback" % rid, json={"verdict": "correct"}, headers=H).status_code, 400)
            r = c.post("/analyses/%s/feedback" % rid, json={"verdict": "correct", "note": "yes", "by": "ali"}, headers=H)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["ok"])
            self.assertIn("foo", r.json()["card_effect"])
            r2 = c.post("/analyses/%s/feedback" % rid, json={"verdict": "wrong", "by": "ali"}, headers=H)
            self.assertEqual(r2.status_code, 409)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "correct")
        self.assertIn("yes", calls[0][2])
        self.assertIn("ali", calls[0][2])
        self.assertNotEqual(calls[0][0], rid)                     # the pipeline run id is used, not the service id

    def test_feedback_real_logic_on_unfinished_is_409(self):
        self.patch_exec(fake_exec_factory(self.base, delay=0.5))
        c = self.client()
        rid = self.post(c, ticket_key="SM-6").json()["run_id"]
        r = c.post("/analyses/%s/feedback" % rid, json={"verdict": "correct", "by": "u"}, headers=H)
        self.assertEqual(r.status_code, 409)
        wait_status(c, rid)

    def test_feedback_real_logic(self):
        c = self.client()
        rid = self.post(c, ticket_key="SM-7").json()["run_id"]
        wait_status(c, rid)
        fdir = Path(self.tmp.name) / "fb"
        with mock.patch.dict(os.environ, {"HIBOT_FEEDBACK_DIR": str(fdir), "HIBOT_RUNS_DIR": str(self.base)}):
            from hibot.agent import feedback as fb
            orig = fb.record_feedback
            with mock.patch.object(svc_app.fbmod, "record_feedback", lambda *a, **k: orig(*a, runs_base=self.base, **k)):
                r = c.post("/analyses/%s/feedback" % rid, json={"verdict": "partial", "by": "ali"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue((fdir / "feedback.jsonl").exists())

    def write_card(self, cid, status="confirmed"):
        card = {"id": cid, "title": "Card " + cid, "symptom_signature": {"keywords": ["abc"]}, "root_cause": "because",
                "verification_queries": [], "fix_steps": [], "confidence": 0.7, "status": status,
                "evidence_tickets": ["SM-1", "SM-2"], "root_cause_type": "config", "resolution_action": "data_fix",
                "created_at": "2026-01-01T00:00:00+00:00", "history": [{"ts": "2026-02-02T00:00:00+00:00", "event": "created"}]}
        (self.cards / (cid + ".json")).write_text(json.dumps(card), encoding="utf-8")

    def test_cards(self):
        self.write_card("card_one")
        self.write_card("card_two", "draft")
        c = self.client()
        lst = c.get("/cards", headers=H).json()
        self.assertEqual({x["id"] for x in lst}, {"card_one", "card_two"})
        one = [x for x in lst if x["id"] == "card_one"][0]
        self.assertEqual(one["support"], 2)
        self.assertEqual(one["updated_at"], "2026-02-02T00:00:00+00:00")
        self.assertEqual(one["resolution_action"], "data_fix")
        self.assertEqual([x["id"] for x in c.get("/cards?status=draft", headers=H).json()], ["card_two"])
        self.assertEqual(c.get("/cards?status=zzz", headers=H).status_code, 400)
        d = c.get("/cards/card_one", headers=H)
        self.assertEqual(d.json()["root_cause"], "because")
        self.assertEqual(c.get("/cards/missing_card", headers=H).status_code, 404)
        self.assertEqual(c.get("/cards/BAD..ID", headers=H).status_code, 404)

    def test_restart_recovery(self):
        c = self.client()
        rid = self.post(c, ticket_key="SM-8").json()["run_id"]
        wait_status(c, rid)
        # simulate a crashed process: one stuck running run and one queued
        for name, status in (("20260101-000000-SM-9-aaaa", "running"), ("20260101-000000-SM-10-bbbb", "queued")):
            d = self.base / name
            d.mkdir()
            (d / "service_state.json").write_text(json.dumps({"run_id": name, "status": status, "ticket_key": "SM-9",
                                                               "created_at": "t", "finished_at": None}), encoding="utf-8")
        c2 = self.client()                                          # "restarted" service
        r = c2.get("/analyses/" + rid, headers=H).json()
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["report"]["summary"], "s")
        for name in ("20260101-000000-SM-9-aaaa", "20260101-000000-SM-10-bbbb"):
            r = c2.get("/analyses/" + name, headers=H).json()
            self.assertEqual(r["status"], "failed")
            self.assertEqual(r["error"], "service restarted")

    def test_concurrency_cap(self):
        running, peak, lock = [0], [0], threading.Lock()
        inner = fake_exec_factory(self.base)

        def slow(key, text, region, loop):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.15)
            with lock:
                running[0] -= 1
            return inner(key, text, region, loop)
        self.patch_exec(slow)
        c = self.client(workers=2)
        ids = [self.post(c, ticket_key="SM-%d" % i).json()["run_id"] for i in range(1, 6)]
        for rid in ids:
            self.assertEqual(wait_status(c, rid, t=10)["status"], "done")
        self.assertLessEqual(peak[0], 2)


if __name__ == "__main__":
    unittest.main()
