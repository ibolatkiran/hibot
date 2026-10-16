"""tools/learning: eligibility, checkpoint, window block, pattern mining and draft cards (synthetic records, no DB)."""
import json
import tempfile
import unittest
from pathlib import Path

from hibot.agent import cards
from tools.learning import batch_footprint as bf
from tools.learning import mine_patterns as mp


def ticket(key="SM-1", resolved="Thu, 17 Sep 2026 14:42:00 +0300", wo=("22811628",), created="2026-09-17 08:58"):
    return {"key": key, "created": created, "resolved": resolved, "problem_type": "functional_defect", "app_guess": "portal",
            "entities": {"work_order_ids": list(wo), "work_order_ids_strong": [], "work_case_ids": []},
            "text_clean": {"summary": "Cannot cancel job No Proof or Money", "desc": ""}}


def rec(rows, cols):
    return {"executed": True, "ok": True, "columns": cols, "rows": rows}


FC_COLS = ["LogId", "LogDate", "OldStatusId", "OldStatusName", "NewStatusId", "NewStatusName", "ChangedFields", "UpdateUserId"]
TL_COLS = ["HistoryId", "WorkOrderId", "StatusId", "StatusName", "SysDate", "OperationDate", "CurrentStatusId"]


def pattern_record(key, tenant=68, frm="329", to="314", allowed=False, created="2026-09-17 08:58", uuc=True, hist=False):
    ch = {"log_id": 1, "at_utc": "2026-09-17 11:41:11", "status_change": True, "from": frm, "from_name": "No Proof or Money",
          "to": to, "to_name": "Cancelled", "fields": ["StatusId"], "update_user_id": "17970", "update_user_unchanged": uuc,
          "history_row_found": hist, "transition_allowed": allowed, "steps_into_target": 0}
    return {"key": key, "status": "ok", "created": created, "resolved": "x", "problem_type": "functional_defect", "app_guess": "portal",
            "manual_level": "high" if uuc and not hist else "none", "any_change_in_window": True, "any_status_change": True,
            "wos": [{"work_order_id": 1, "tenant_id": tenant, "tenant_name": "ZA", "window": {
                "changes": [ch], "status_changes": [ch], "outside": {"status_changes": 3, "no_history": 0}}}]}


class Eligibility(unittest.TestCase):
    def test_needs_resolved_and_number(self):
        self.assertEqual(bf.eligible_numbers(ticket()), ["22811628"])
        self.assertEqual(bf.eligible_numbers(ticket(resolved=None)), [])
        self.assertEqual(bf.eligible_numbers(ticket(wo=())), [])
        self.assertEqual(bf.eligible_numbers(ticket(wo=("1", "2", "3", "4"))), ["1", "2", "3"])

    def test_select_since_done_limit_newest_first(self):
        ts = [ticket("SM-1", created="2026-01-01 10:00"), ticket("SM-2", created="2026-03-01 10:00"),
              ticket("SM-3", created="2026-05-01 10:00"), ticket("SM-4", resolved=None)]
        self.assertEqual([t["key"] for t in bf.select_tickets(ts)], ["SM-3", "SM-2", "SM-1"])
        self.assertEqual([t["key"] for t in bf.select_tickets(ts, since="2026-02-01")], ["SM-3", "SM-2"])
        self.assertEqual([t["key"] for t in bf.select_tickets(ts, done={"SM-3"}, limit=1)], ["SM-2"])


class Checkpoint(unittest.TestCase):
    def test_roundtrip_and_jsonl_union(self):
        with tempfile.TemporaryDirectory() as d:
            cp, out = Path(d) / "cp.json", Path(d) / "out.jsonl"
            self.assertEqual(bf.load_checkpoint(cp, out), set())
            bf.save_checkpoint({"SM-1", "SM-2"}, cp)
            out.write_text(json.dumps({"key": "SM-9"}) + "\nnot json\n", encoding="utf-8")
            self.assertEqual(bf.load_checkpoint(cp, out), {"SM-1", "SM-2", "SM-9"})


class WindowBlock(unittest.TestCase):
    def test_manual_fix_detected_and_outside_baseline(self):
        fc = rec([[1, "2026-09-17T11:41:11", 329, "No Proof", 314, "Cancelled", "StatusId, ", 17970],
                  [2, "2026-01-01T10:00:00", 1, "A", 2, "B", "StatusId, UpdateUserId, ", 5]], FC_COLS)
        tl = rec([[10, 1, 2, "B", "2026-01-01T10:00:00", "2026-01-01T10:00:00", 314]], TL_COLS)
        for r in (fc, tl):
            r["query_id"] = "wo_field_changes" if r is fc else "wo_status_timeline"
        b = bf.window_block(ticket(), [tl, fc])
        self.assertEqual(b["manual_level"], "high")
        self.assertEqual(len(b["status_changes"]), 1)
        self.assertEqual((b["status_changes"][0]["from"], b["status_changes"][0]["to"]), ("329", "314"))
        self.assertEqual(b["outside"], {"status_changes": 1, "no_history": 0})
        self.assertFalse(b["no_change_in_window"])
        bf.attach_transitions(b, lambda f, t: {"allowed": False, "steps_into_target": 0})
        self.assertIs(b["status_changes"][0]["transition_allowed"], False)

    def test_no_change(self):
        fc = dict(rec([], FC_COLS), query_id="wo_field_changes")
        b = bf.window_block(ticket(), [fc])
        self.assertTrue(b["no_change_in_window"])

    def test_parse_step_check(self):
        cols = ["FromStatusId", "NextStatusId", "IsRequestedTarget", "StepsIntoTarget"]
        self.assertTrue(bf.parse_step_check(rec([[1, 2, 0, 1], [1, 3, 1, 1]], cols))["allowed"])
        self.assertFalse(bf.parse_step_check(rec([[1, 2, 0, 0]], cols))["allowed"])
        self.assertIsNone(bf.parse_step_check(rec([], cols))["allowed"])
        self.assertIsNone(bf.parse_step_check({"executed": False})["allowed"])


class Mining(unittest.TestCase):
    def setUp(self):
        self.recs = [pattern_record("SM-1"), pattern_record("SM-2", created="2026-09-18 09:00"),
                     pattern_record("SM-3", tenant=70, frm="1", to="2", allowed=True, uuc=False),
                     {"key": "SM-4", "status": "not_found_eu", "wos": []},
                     {"key": "SM-5", "status": "ok", "problem_type": "how_to_question", "any_change_in_window": False,
                      "any_status_change": False, "manual_level": "none",
                      "wos": [{"tenant_id": 68, "window": {"changes": [], "status_changes": [], "outside": {"status_changes": 0, "no_history": 0}}}]}]
        self.tickets = {k: ticket(k) for k in ("SM-1", "SM-2", "SM-3")}

    def test_funnel_and_patterns(self):
        m = mp.mine(self.recs)
        self.assertEqual(m["funnel"]["not_found_eu"], 1)
        self.assertEqual(m["funnel"]["found_in_eu"], 4)
        self.assertEqual(m["funnel"]["no_change_in_window"], 1)
        top = m["patterns"][0]
        self.assertEqual((top["tenant"], top["from"], top["to"], top["allowed"], top["count"]), (68, "329", "314", False, 2))
        self.assertEqual(top["keys"], ["SM-2", "SM-1"])
        self.assertEqual(m["by_problem_type"]["how_to_question"]["no_change"], 1)

    def test_dedup_by_key_in_loader(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.jsonl"
            p.write_text(json.dumps({"key": "A", "v": 1}) + "\n" + json.dumps({"key": "A", "v": 2}) + "\n", encoding="utf-8")
            self.assertEqual(mp.load_records(p), [{"key": "A", "v": 2}])

    def test_card_is_valid_draft_and_min_tickets(self):
        m = mp.mine(self.recs)
        card = mp.build_card(m["patterns"][0], self.tickets)
        self.assertEqual(cards.validate_card(card), [])
        self.assertEqual((card["status"], card["created_by"]), ("draft", "batch_footprint"))
        self.assertLessEqual(card["confidence"], mp.MAX_DRAFT_CONF)
        self.assertEqual(card["id"], "draft_footprint_t68_329_to_314")
        with tempfile.TemporaryDirectory() as d:
            res = mp.write_cards(m["patterns"], self.tickets, Path(d))
            self.assertEqual(res["written"], ["draft_footprint_t68_329_to_314"])      # tenant 70 pattern has 1 ticket

    def test_never_overwrites_confirmed(self):
        m = mp.mine(self.recs)
        with tempfile.TemporaryDirectory() as d:
            card = mp.build_card(m["patterns"][0], self.tickets)
            card["status"] = "confirmed"
            cards.save_card(card, Path(d))
            res = mp.write_cards(m["patterns"], self.tickets, Path(d))
            self.assertEqual(res["written"], [])
            self.assertEqual(res["skipped_existing"], [card["id"]])
            self.assertEqual(json.loads((Path(d) / (card["id"] + ".json")).read_text(encoding="utf-8"))["status"], "confirmed")

    def test_rewrite_of_own_draft_appends_history(self):
        m = mp.mine(self.recs)
        with tempfile.TemporaryDirectory() as d:
            mp.write_cards(m["patterns"], self.tickets, Path(d))
            mp.write_cards(m["patterns"], self.tickets, Path(d))
            c = json.loads((Path(d) / "draft_footprint_t68_329_to_314.json").read_text(encoding="utf-8"))
            self.assertEqual([h["event"] for h in c["history"]], ["created", "evidence_added"])

    def test_summary_renders(self):
        m = mp.mine(self.recs)
        txt = mp.render_summary(m, total_tickets=10, resolved=8, eligible=5, card_ids=["draft_footprint_t68_329_to_314"])
        self.assertIn("329 ", txt)
        self.assertIn("Limitations", txt)


if __name__ == "__main__":
    unittest.main()
