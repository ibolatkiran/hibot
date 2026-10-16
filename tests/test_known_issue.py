"""Card review CLI, state signature, state-based card matching, verification injection (no network, no DB)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hibot import __main__ as cli
from hibot.agent import cardcli, config, feedback, known_issue, pipeline, store
from hibot.agent import cards as cardmod
from hibot.agent.code_context import CodeContext
from hibot.agent.executor import LIVE
from hibot.llm.client import FakeLLM
from tests.test_agent import final, incident, registry
from tests.test_playbooks import FakeRunner, LOC_COLS, FC_COLS, TL_COLS, mk
from tools.learning import mine_patterns as mp
from tests.test_learning import pattern_record  # noqa: F401  (fixture reuse)

EGY_FIX = ["Statüyü DB'den zorlama.", "Talep edene açıkla."]


def egypt_card(status="draft", cid="draft_footprint_t70_2271_to_2257", to=2257, words=("transfer",), **kw):
    c = {"id": cid, "title": "Egypt 2271 -> %s" % to,
         "symptom_signature": {"problem_type": "data_correction", "app": "crm", "keywords": ["status"], "entity_requirements": ["work_order_ids"]},
         "state_signature": cardmod.build_state_signature(70, 2271, to, "Financed(Accounted)", "Transfer", False),
         "root_cause": "Tenant 70: 2271 -> %s gecis bilincli kapali." % to,
         "verification_queries": [
             {"query_id": "wo_locate", "params_from_entities": {"Number": "entities.work_order_ids[0]"}},
             {"query_id": "wo_field_changes", "params_from_entities": {"TenantId": {"const": 70}, "WorkOrderId": "entities.work_order_ids[0]"}},
             {"query_id": "status_step_check", "params_from_entities": {"TenantId": {"const": 70}, "FromStatusId": {"const": 2271},
                                                                        "ToStatusId": {"const": to}}}],
        "fix_steps": list(EGY_FIX), "code_locations": [], "confidence": 0.7, "status": status,
         "evidence_tickets": ["SM-1"], "created_by": "test", "history": []}
    c["state_signature"]["target_words"] = list(words)
    if status == "confirmed":
        c["classification"] = "user_error"
        c["confidence"] = 0.85
    c.update(kw)
    return c


def egypt_inc(**kw):
    base = incident(key="SM-2200000", summary="change Status W.O", desc="please change status to transfer W.O 22697806",
                    entities={"work_order_ids": ["22697806"]}, problem_type="data_correction", app="crm", country="EG",
                    created="2026-09-06 11:30", resolved="Mon, 07 Sep 2026 10:00:00 +0300")
    base.update(kw)
    return base


# created 2026-09-06 11:30 (+03) = 08:30 UTC; the forced change happens after it
FC_FORCED = [[5, "2026-09-06T10:00:00", 2271, "Financed(Accounted)", 2257, "Transfer", 17970, "StatusId, "]]
EGY_DATA = {
    "wo_locate": (LOC_COLS, [[22697806, 70, "Egypt", "eg", 2257, "Id"]]),
    "wo_header": (["WorkOrderId", "StatusId", "StatusName"], [[22697806, 2257, "Transfer"]]),
    "wo_status_timeline": (TL_COLS, [[1, 22697806, 2271, "Financed(Accounted)", "2026-09-01T10:00:00", "2026-09-01T10:00:00", "T1", 2257]]),
    "wo_field_changes": (FC_COLS, FC_FORCED),
    "wo_status_transition_check": (["WorkOrderId", "CurrentStatusId", "CurrentStatusName", "LatestHistoryStatusId", "NextStatusId", "NextStatusName"],
                                   [[22697806, 2257, "Transfer", 2271, None, None]]),
    "status_step_check": (["FromStatusId", "FromStatusName", "NextStatusId", "NextStatusName", "IsRequestedTarget", "StepsIntoTarget"],
                          [[2271, "Financed(Accounted)", 2275, "To be sent to SAP", 0, 3]]),
}


def pf_for(status=2257, tenant=70):
    return {"resolved": [{"WorkOrderId": 22697806, "TenantId": tenant, "StatusId": status, "Region": "EU"}], "unresolved": [], "region": "EU"}


def fc_query(rows=FC_FORCED):
    return {"query_id": "wo_field_changes", "executed": True, "ok": True, "columns": FC_COLS, "rows": rows, "row_count": len(rows), "params": {}, "n": 1}


class CardCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        cardmod.save_card(egypt_card(), self.d)
        cardmod.save_card(egypt_card(cid="draft_footprint_t70_2271_to_2260", to=2260), self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_filter_and_show(self):
        self.assertEqual(len(cardcli.list_cards(directory=self.d)), 2)
        self.assertEqual(cardcli.list_cards("confirmed", self.d), [])
        txt = cardcli.format_list(cardcli.list_cards(directory=self.d))
        self.assertIn("draft_footprint_t70_2271_to_2257", txt)
        self.assertIn("support", txt)
        shown = cardcli.format_card(cardcli.get_card("draft_footprint_t70_2271_to_2257", self.d))
        self.assertIn("Kök neden", shown)
        self.assertIn("Durum imzası", shown)
        self.assertIn("Çözüm adımları", shown)

    def test_confirm_with_overrides_keeps_schema_and_evidence(self):
        c = cardcli.confirm("draft_footprint_t70_2271_to_2257", "bilerek kapatıldı", root_cause="yeni kök neden", fix_steps=["a", "b"],
                            classification="user_error", title="Yeni başlık", directory=self.d)
        self.assertEqual((c["status"], c["classification"], c["title"], c["fix_steps"]), ("confirmed", "user_error", "Yeni başlık", ["a", "b"]))
        self.assertGreaterEqual(c["confidence"], 0.85)
        self.assertEqual(c["evidence_tickets"], ["SM-1"])
        h = c["history"][-1]
        self.assertEqual((h["event"], h["who"], h["note"]), ("confirmed", "user", "bilerek kapatıldı"))
        self.assertEqual(h["overrides"]["root_cause"]["new"], "yeni kök neden")
        self.assertEqual(cardmod.validate_card(json.loads((self.d / "draft_footprint_t70_2271_to_2257.json").read_text(encoding="utf-8"))), [])

    def test_confirm_does_not_lower_confidence(self):
        cardmod.save_card(egypt_card(cid="high_conf_card", confidence=0.95), self.d)
        self.assertEqual(cardcli.confirm("high_conf_card", directory=self.d)["confidence"], 0.95)

    def test_reject_annotate_and_errors(self):
        with self.assertRaises(ValueError):
            cardcli.reject("draft_footprint_t70_2271_to_2257", "", self.d)
        c = cardcli.reject("draft_footprint_t70_2271_to_2257", "yanlış", self.d)
        self.assertEqual(c["status"], "rejected")
        c = cardcli.annotate("draft_footprint_t70_2271_to_2260", "not", self.d)
        self.assertEqual((c["status"], c["history"][-1]["event"]), ("draft", "annotated"))
        with self.assertRaises(KeyError):
            cardcli.get_card("nope_nope", self.d)
        self.assertEqual([x["status"] for x in cardcli.list_cards(directory=self.d)], ["draft", "rejected"])

    def test_invalid_classification_rejected_by_schema(self):
        c = egypt_card()
        c["classification"] = "banana"
        self.assertTrue(cardmod.validate_card(c))
        with self.assertRaises(ValueError):
            cardcli.confirm("draft_footprint_t70_2271_to_2257", classification="banana", directory=self.d)

    def test_main_cards_subcommands(self):
        with mock.patch.dict(os.environ, {"HIBOT_CARDS_DIR": str(self.d)}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["cards", "list", "--status", "draft"]), 0)
                self.assertEqual(cli.main(["cards", "confirm", "draft_footprint_t70_2271_to_2257", "--note", "n", "--fix-step", "x",
                                           "--fix-step", "y", "--classification", "user_error"]), 0)
                self.assertEqual(cli.main(["cards", "show", "draft_footprint_t70_2271_to_2257"]), 0)
            self.assertIn("durum=confirmed", out.getvalue())
            self.assertEqual(cardcli.get_card("draft_footprint_t70_2271_to_2257", self.d)["fix_steps"], ["x", "y"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["cards", "reject", "missing_card_id", "--note", "n"]), 2)
                with self.assertRaises(SystemExit):
                    cli.main(["cards", "show"])

    def test_rejected_card_never_revived_by_feedback(self):
        cardcli.reject("draft_footprint_t70_2271_to_2257", "yanlış", self.d)
        with tempfile.TemporaryDirectory() as runs, tempfile.TemporaryDirectory() as fb:
            run = {"run_id": "20260101-000000-SM9-abcd", "input": {}, "steps": [], "queries": [], "incident": {"key": "SM-9"}, "demo": False, "mode": "live",
                   "report": {"evidence": [{"type": "card", "ref": "draft_footprint_t70_2271_to_2257"}], "classification": "config", "confidence": 0.5}}
            store.save_run(run, "md", Path(runs))
            feedback.record_feedback("20260101-000000-SM9-abcd", "correct", runs_base=Path(runs), cards_base=self.d, feedback_base=Path(fb))
        self.assertEqual(cardcli.get_card("draft_footprint_t70_2271_to_2257", self.d)["status"], "rejected")


class StateSignatureTests(unittest.TestCase):
    def test_derive_from_footprint_card(self):
        c = {"verification_queries": [{"query_id": "status_step_check", "params_from_entities": {
            "TenantId": {"const": 70}, "FromStatusId": {"const": 2271}, "ToStatusId": {"const": 2257}}}],
             "root_cause": "Tenant 70 Egypt: status 2271 (Financed(Accounted)) -> 2257 (Transfer) is NOT configured in Catalog.StatusStep"}
        s = cardmod.derive_state_signature(c)
        self.assertEqual((s["tenant_id"], s["from_status_id"], s["to_status_id"], s["transition_configured"]), (70, 2271, 2257, False))
        self.assertEqual((s["from_status_name"], s["to_status_name"]), ("Financed(Accounted)", "Transfer"))
        self.assertIn("transfer", s["target_words"])
        self.assertIn("cancel", cardmod.target_words("Cancelled"))

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            c = egypt_card()
            del c["state_signature"]
            cardmod.save_card(c, Path(d))
            self.assertEqual(cardmod.migrate_state_signatures(Path(d)), [c["id"]])
            self.assertEqual(cardmod.migrate_state_signatures(Path(d)), [])
            self.assertEqual(cardmod.load_cards(Path(d))[0]["state_signature"]["to_status_id"], 2257)

    def test_mine_patterns_emits_state_signature(self):
        recs = [pattern_record("SM-1"), pattern_record("SM-2", created="2026-09-18 09:00")]
        m = mp.mine(recs)
        card = mp.build_card(m["patterns"][0], {})
        self.assertEqual(cardmod.validate_card(card), [])
        self.assertEqual((card["state_signature"]["tenant_id"], card["state_signature"]["from_status_id"],
                          card["state_signature"]["to_status_id"], card["state_signature"]["transition_configured"]), (68, 329, 314, False))

    def test_shipped_cards_are_valid_and_footprint_drafts_have_signature(self):
        for c in cardmod.load_cards(config.ROOT / "knowledge" / "cards", include_rejected=True):
            if c["id"].startswith("draft_footprint_"):
                self.assertIn("state_signature", c, c["id"])


class StateMatchingTests(unittest.TestCase):
    def test_status_as_of_created_undoes_later_changes(self):
        inc = egypt_inc()
        self.assertEqual(known_issue.status_as_of_created(inc, 2257, [fc_query()])[0], "2271")
        late = [[5, "2026-09-06T07:00:00", 2271, "x", 2257, "y", 1, "StatusId, "]]          # BEFORE the ticket (07:00 < 08:30 UTC)
        self.assertEqual(known_issue.status_as_of_created(inc, 2257, [fc_query(late)])[0], "2257")
        self.assertEqual(known_issue.status_as_of_created(inc, 2257, [])[0], "2257")           # no log: current
        two = FC_FORCED + [[6, "2026-09-06T11:00:00", 2257, "y", 2260, "z", 1, "StatusId, "]]
        self.assertEqual(known_issue.status_as_of_created(inc, 2260, [fc_query(two)])[0], "2271")
        self.assertEqual(known_issue.status_as_of_created(dict(egypt_inc(), created=None), 2257, [fc_query()])[0], "2257")

    def test_match_by_reconstructed_status_and_words(self):
        ms = known_issue.match_state_cards(egypt_inc(), [egypt_card()], pf_for(2257), [fc_query()])
        self.assertEqual(len(ms), 1)
        self.assertEqual(ms[0]["score"], 1.0)
        self.assertTrue(ms[0]["state"]["from_hit"])
        self.assertEqual(ms[0]["state"]["status_at_created"], "2271")

    def test_from_status_alone_or_words_alone(self):
        no_words = egypt_inc(summary="please help", desc="the order is stuck")
        ms = known_issue.match_state_cards(no_words, [egypt_card()], pf_for(2257), [fc_query()])
        self.assertEqual(ms[0]["score"], 0.75)
        # status at creation is different (created after the change) but the ticket mentions the target action
        after = egypt_inc(created="2026-09-06 14:00")
        ms = known_issue.match_state_cards(after, [egypt_card()], pf_for(2257), [fc_query()])
        self.assertEqual(ms[0]["score"], 0.65)
        self.assertFalse(ms[0]["state"]["from_hit"])
        # neither
        self.assertEqual(known_issue.match_state_cards(egypt_inc(summary="x", desc="y"), [egypt_card()], pf_for(2257), [fc_query(
            [[5, "2026-09-06T07:00:00", 2271, "x", 2257, "y", 1, "StatusId, "]])]), [])

    def test_tenant_mismatch_rejected_and_no_resolution(self):
        self.assertEqual(known_issue.match_state_cards(egypt_inc(), [egypt_card()], pf_for(2257, tenant=68), [fc_query()]), [])
        self.assertEqual(known_issue.match_state_cards(egypt_inc(), [egypt_card()], {"resolved": []}, []), [])

    def test_rejected_never_matches_and_confirmed_ranks_first(self):
        rej = egypt_card(status="rejected")
        self.assertEqual(known_issue.match_state_cards(egypt_inc(), [rej], pf_for(), [fc_query()]), [])
        conf = egypt_card(status="confirmed", cid="confirmed_other_card", words=())
        ms = known_issue.match_state_cards(egypt_inc(), [egypt_card(), conf], pf_for(), [fc_query()])
        self.assertEqual([m["card"]["id"] for m in ms], ["confirmed_other_card", "draft_footprint_t70_2271_to_2257"])

    def test_cards_without_signature_are_ignored(self):
        c = egypt_card()
        del c["state_signature"]
        self.assertEqual(known_issue.match_state_cards(egypt_inc(), [c], pf_for(), [fc_query()]), [])


class VerificationAndPipelineTests(unittest.TestCase):
    def _run(self, card, data=None, script=None, inc=None, budget=None):
        log = []
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cdir = Path(d.name) / "cards"
        cardmod.save_card(card, cdir)
        with mock.patch.dict(os.environ, {"HIBOT_CARDS_DIR": str(cdir)}):
            run = pipeline.analyze("SM-2200000", None, llm=FakeLLM(script or [final(classification="config", confidence=0.6,
                                   fix_steps=["Catalog.StatusStep satırı ekle", "Statüyü DB'den zorlama."])]),
                                   incident=inc or egypt_inc(), mode=LIVE, region="EU", code=CodeContext(use_graph=False),
                                   registry=registry(), runs_base=Path(d.name) / "runs", budget=budget,
                                   runner_factory=lambda r: FakeRunner(r, log, data or EGY_DATA))
        return run, log, cdir

    def test_confirmed_card_drives_report_and_dedupes_with_playbook(self):
        run, log, cdir = self._run(egypt_card(status="confirmed"))
        ki = run["known_issue"]
        self.assertEqual((ki["card_id"], ki["card_status"], ki["verdict"]), ("draft_footprint_t70_2271_to_2257", "confirmed", "supports"))
        ids = [q for q, _ in log]
        self.assertEqual(ids.count("status_step_check"), 1)                 # playbook ran it, the card reuses the result
        self.assertEqual(ids.count("wo_field_changes"), 1)
        self.assertTrue(any(q["deduped"] for q in ki["queries"] if q["query_id"] == "status_step_check"))
        self.assertEqual(len(run["steps"]), 1)
        rep = run["report"]
        self.assertEqual(rep["classification"], "user_error")
        self.assertEqual(rep["fix_steps"][:2], EGY_FIX)                    # verbatim, in order
        self.assertTrue(any(s.startswith(known_issue.LLM_ADD) for s in rep["fix_steps"][2:]))
        self.assertFalse(any("DB'den zorlama" in s and s.startswith(known_issue.LLM_ADD) for s in rep["fix_steps"]))   # duplicate not re-added
        self.assertIn("SÜREÇ İHLALİ", rep["root_cause"])
        self.assertTrue(any(e["type"] == "card" and e["ref"] == "draft_footprint_t70_2271_to_2257" for e in rep["evidence"]))
        self.assertIn("**Eşleşen kart:** `draft_footprint_t70_2271_to_2257` (confirmed), doğrulama: **destekliyor**", run["markdown"])
        self.assertGreaterEqual(rep["confidence"], 0.85)
        self.assertEqual(run["matches"][0]["card"]["id"], "draft_footprint_t70_2271_to_2257")

    def test_case_file_contains_card_section(self):
        from hibot.agent.prompting import build_case_prompt
        log = []
        ex, _ = mk(data=EGY_DATA, log=log)
        from hibot.agent import playbooks, preflight
        inc = egypt_inc()
        pf = preflight.run(inc, ex)
        bl = playbooks.run(inc, ex, pf)
        ka = known_issue.analyze(inc, ex, pf, bl, [egypt_card(status="confirmed")])
        self.assertEqual(ka["evaluation"]["verdict"], "supports")
        txt = build_case_prompt(inc, ka["matches"], [], "cat", mode="live", region="EU", budget=config.Budget(), card_section=ka["section"])
        self.assertIn("## BİLİNEN SORUN KARTI", txt)
        self.assertIn("DOĞRULAMA SONUCU: DESTEKLİYOR", txt)
        self.assertIn("Statüyü DB'den zorlama.", txt)
        self.assertIn("süreç ihlali", txt)

    def test_draft_card_is_hypothesis_llm_classification_kept(self):
        run, _, _ = self._run(egypt_card(status="draft"))
        rep = run["report"]
        self.assertEqual(run["known_issue"]["card_status"], "draft")
        self.assertEqual(run["known_issue"]["verdict"], "supports")
        self.assertEqual(rep["classification"], "config")                  # draft: no override
        self.assertTrue(any(e["type"] == "card" for e in rep["evidence"]))
        self.assertIn("(draft), doğrulama: **destekliyor**", run["markdown"])

    def test_contradiction_is_reported_and_card_not_forced(self):
        data = dict(EGY_DATA)
        data["status_step_check"] = (EGY_DATA["status_step_check"][0], [[2271, "Financed(Accounted)", 2257, "Transfer", 1, 3]])
        run, _, _ = self._run(egypt_card(status="confirmed"), data=data)
        self.assertEqual(run["known_issue"]["verdict"], "contradicts")
        rep = run["report"]
        self.assertEqual(rep["classification"], "config")                  # not overridden
        self.assertEqual(rep["fix_steps"], ["Catalog.StatusStep satırı ekle", "Statüyü DB'den zorlama."])   # LLM steps untouched
        self.assertFalse(any(e["type"] == "card" for e in rep["evidence"]))
        self.assertIn("doğrulama: **çelişiyor**", run["markdown"])

    def test_no_forced_change_means_no_process_violation(self):
        data = dict(EGY_DATA)
        data["wo_field_changes"] = (FC_COLS, [])
        data["wo_header"] = (["WorkOrderId", "StatusId", "StatusName"], [[22697806, 2271, "Financed(Accounted)"]])
        run, _, _ = self._run(egypt_card(status="confirmed"), data={**data, "wo_locate": (LOC_COLS, [[22697806, 70, "Egypt", "eg", 2271, "Id"]])})
        self.assertEqual(run["known_issue"]["verdict"], "supports")
        self.assertNotIn("SÜREÇ İHLALİ", run["report"]["root_cause"])

    def test_state_only_match_is_hypothesis_not_adopted(self):
        """Round 1: ticket never mentions the card's target action (e.g. an Atlas mismatch report) -> verdict inconclusive, no adoption."""
        run, _, _ = self._run(egypt_card(status="confirmed"), inc=egypt_inc(summary="Status differs in Atlas", desc="WO shows pending there"))
        self.assertEqual(run["known_issue"]["verdict"], "inconclusive")
        rep = run["report"]
        self.assertEqual(rep["classification"], "config")                  # LLM answer kept, card not forced
        self.assertNotIn("SÜREÇ İHLALİ", rep["root_cause"])
        self.assertIn("HİPOTEZDİR", " ".join(run["known_issue"]["details"]))

    def test_confirmed_card_axes_and_calibration_in_report(self):
        run, _, _ = self._run(egypt_card(status="confirmed", root_cause_type="user_error", resolution_action="explain_to_user"))
        rep = run["report"]
        self.assertEqual((rep["root_cause_type"], rep["resolution_action"], rep["classification"]), ("user_error", "explain_to_user", "user_error"))
        self.assertIn("confidence_caps_applied", rep)
        self.assertEqual(rep["confidence_raw"], 0.6)                       # the LLM said 0.6; the supported confirmed card lifts it
        self.assertGreaterEqual(rep["confidence"], 0.85)

    def test_rejected_card_not_used_in_pipeline(self):
        run, log, _ = self._run(egypt_card(status="rejected"))
        self.assertIsNone(run["known_issue"])
        self.assertNotIn("matched_card", run["report"])

    def test_dry_run_has_no_state_match(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cardmod.save_card(egypt_card(status="confirmed"), Path(d.name) / "c")
        with mock.patch.dict(os.environ, {"HIBOT_CARDS_DIR": str(Path(d.name) / "c")}):
            run = pipeline.analyze("SM-2200000", None, llm=FakeLLM([final()]), incident=egypt_inc(), code=CodeContext(use_graph=False),
                                   registry=registry(), runs_base=Path(d.name) / "r")
        self.assertIsNone(run["known_issue"])

    def test_budget_counted_and_skipped_when_exhausted(self):
        from hibot.agent.config import Budget
        run, log, _ = self._run(egypt_card(status="confirmed", cid="card_with_extra_check"), budget=Budget(max_queries=7))
        self.assertLessEqual(len(log), 7)

    def test_feedback_correct_adds_ticket_to_card_evidence(self):
        run, _, cdir = self._run(egypt_card(status="confirmed"), inc=egypt_inc(key="SM-2300000"))
        with tempfile.TemporaryDirectory() as fb:
            res = feedback.record_feedback(run["run_id"], "correct", runs_base=Path(run["run_dir"]).parent, cards_base=cdir,
                                           feedback_base=Path(fb))
        self.assertTrue(any("updated" in a for a in res["actions"]))
        c = json.loads((cdir / "draft_footprint_t70_2271_to_2257.json").read_text(encoding="utf-8"))
        self.assertIn("SM-2300000", c["evidence_tickets"])
        self.assertEqual(c["status"], "confirmed")
        self.assertEqual(cardmod.validate_card(c), [])


if __name__ == "__main__":
    unittest.main()
