"""Unit tests: python -m unittest discover tools/incidents"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build  # noqa: E402
import extract as ex  # noqa: E402
import jira_xml  # noqa: E402
import similarity as sim  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "testdata", "sample_jira.xml")


class JiraXmlTests(unittest.TestCase):
    def test_parse_sample(self):
        issues = jira_xml.parse_file(SAMPLE)
        self.assertEqual([i["key"] for i in issues], ["SM-9000001", "SM-9000002"])
        a = issues[0]
        self.assertEqual(a["resolution"], "Done")
        self.assertEqual(a["components"], ["SIRIUS Portal", "Master Data"])
        self.assertEqual(a["labels"], ["tenant", "poland"])
        self.assertIn("WO: 22156961", a["description"])
        self.assertNotIn("<p>", a["description"])
        self.assertEqual([c["author"] for c in a["comments"]], ["agent1", "rep1"])
        self.assertIn("MM03", a["comments"][0]["text"])
        self.assertEqual(a["custom_fields"]["Root Cause Category"], ["Missing master data", "SAP MM03"])
        self.assertEqual(issues[1]["description"], "Unauthorized error 401 after login.\nSecond line")

    def test_load_dir_missing_and_present(self):
        self.assertEqual(jira_xml.load_dir(os.path.join(HERE, "does_not_exist")), {})
        self.assertEqual(sorted(jira_xml.load_dir(os.path.join(HERE, "testdata"))), ["SM-9000001", "SM-9000002"])

    def test_truncated_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "t.xml")
            with open(p, "w", encoding="utf-8") as f:
                f.write("<rss><channel><item><key>SM-1</key><summary>a</summary></item><item><key>SM-2")
            old, sys.stderr = sys.stderr, open(os.devnull, "w")
            try:
                self.assertEqual([i["key"] for i in jira_xml.parse_file(p)], ["SM-1"])
            finally:
                sys.stderr.close()
                sys.stderr = old

    def test_html_to_text(self):
        self.assertEqual(jira_xml.html_to_text("<p>a &amp; b</p><ul><li>x</li><li>y</li></ul>"), "a & b\n- x\n- y")


class ExtractTests(unittest.TestCase):
    def test_work_orders(self):
        r = ex.extract_ids("Please reject WO *15174442* and 15191107  15185004. case no.16523504 WO22767569 [~26037794] "
                           "model 8100030053 EAN 869991722490 PO order 1100876882 C00646842 WC: 20225914")
        self.assertEqual(r["work_order_ids"], ["15174442", "15191107", "15185004", "16523504", "22767569"])
        self.assertEqual(r["work_case_ids"], ["20225914"])
        self.assertEqual(r["sku_codes"], ["8100030053", "869991722490"])
        self.assertEqual(r["material_codes"], ["C00646842"])
        self.assertEqual(r["sap_documents"], ["1100876882"])

    def test_not_wo(self):
        r = ex.extract_ids("User 92740148 and phone 0532 123 45 67, date 20250131, amount 1.12345678")
        self.assertEqual(r["work_order_ids"], [])

    def test_scrub(self):
        t = ex.scrub("mail a.b@x.com tel +90 532 123 45 67 Password: Abc123 see https://x.y/z [~123456] user 93605544")
        for bad in ("a.b@x.com", "532", "Abc123", "https://", "123456", "93605544"):
            self.assertNotIn(bad, t)
        self.assertIn("<EMAIL>", t)
        self.assertIn("<SECRET>", t)

    def test_person_scrub(self):
        pr = ex.build_person_re(["Jan Testowy"])
        self.assertEqual(ex.scrub("BR jan  testowy", pr), "BR <PERSON>")

    def test_country(self):
        self.assertEqual(ex.detect_country("Missing Tenant in Poland", "")["country"], "PL")
        c = ex.detect_country("AHVN_Sirius error", "")
        self.assertEqual((c["country"], c["company_code"], c["country_source"]), ("VN", "AHVN", "company_code"))
        c = ex.detect_country("Add SKU in Sirius Baltics", "")
        self.assertEqual((c["country"], c["country_region"], c["countries"]), (None, "BALTICS", ["LT", "LV", "EE"]))

    def test_problem_type(self):
        cases = [("Missing Tenant for 859991715080 in Sirius", "", "master_data_request"),
                 ("Need authorization for new user", "", "access_request"),
                 ("Please reject WO 15174442", "", "data_correction"),
                 ("Error - missing FY Document", "SAP integration failed", "integration_failure"),
                 ("NPS SMS not sent", "", "notification_failure"),
                 ("Sirius is very slow", "page loading forever", "performance_availability"),
                 ("Hello", "good day", "other")]
        for title, desc, exp in cases:
            self.assertEqual(ex.classify(title, desc)["problem_type"], exp, title)

    def test_errors(self):
        errs, http = ex.extract_errors('We get error: "Invalid serial number. Please check the entered value." HTTP 500')
        self.assertTrue(any("Invalid serial number" in e for e in errs))
        self.assertEqual(http, ["500"])

    def test_app_guess(self):
        self.assertEqual(ex.guess_app("Sirius", "SIRIUS CRM", "x", "")["app_guess"], "crm")
        self.assertEqual(ex.guess_app("Manusoft", "Manusoft Global", "x", "")["app_guess"], "other:manusoft")

    def test_language(self):
        self.assertEqual(ex.guess_language("Merhaba, lütfen yetki verir misiniz")["language"], "tr")
        self.assertEqual(ex.guess_language("Please help, the page is not loading")["language"], "en")


class SimilarityTests(unittest.TestCase):
    def test_tokenize(self):
        t = sim.tokenize("Please reject WO 15174442 for SKU 8100030053 İşlem")
        self.assertIn("<wo>", t)
        self.assertIn("<id10>", t)
        self.assertIn("islem", t)
        self.assertNotIn("please", t)

    def test_groups(self):
        docs = ["missing tenant for sku in sirius poland", "missing tenant sku sirius poland please", "tenant missing for sku poland",
                "cannot login to portal password reset", "login portal password reset needed", "report export excel dashboard slow"]
        toks = [sim.tokenize(d) for d in docs]
        similar, groups, _ = sim.similar_and_groups(toks, 3, 0.2, 0.3)
        multi = [g for g in groups if len(g) > 1]
        self.assertIn([0, 1, 2], multi)
        self.assertIn([3, 4], multi)
        self.assertEqual(similar[0][0][0] in (1, 2), True)


class BuildTests(unittest.TestCase):
    def test_build_with_and_without_jira(self):
        tickets = {"tickets": [
            {"key": "SM-9000001", "summary": "Sirius Poland - Missing tenant for 859991715080", "status": "x", "priority": "Normal",
             "created": "2026-01-05 09:15", "reporter": "Jan Testowy", "assignee": "Test Agent", "service": "Sirius",
             "sub_service": "SIRIUS Portal", "app": "Sirius", "link": "l", "desc": "Please add tenant for SKU 859991715080 (truncated"},
            {"key": "SM-9000003", "summary": "Sirius Poland - Missing tenant for 869991715081", "status": "x", "priority": "Normal",
             "created": "2026-01-07 09:15", "reporter": "Jan Testowy", "assignee": "Test Agent", "service": "Sirius",
             "sub_service": "SIRIUS Portal", "app": "Sirius", "link": "l", "desc": "Please add tenant for SKU 869991715081"}]}
        with tempfile.TemporaryDirectory() as td:
            ds = os.path.join(td, "ds.json")
            with open(ds, "w", encoding="utf-8") as f:
                json.dump(tickets, f)
            for jira_dir, enriched in ((os.path.join(HERE, "testdata"), True), (os.path.join(td, "nope"), False)):
                out = os.path.join(td, "out_%s" % enriched)
                build.main(["--dataset", ds, "--jira-dir", jira_dir, "--out", out, "--codemap", os.path.join(td, "nocodemap")])
                with open(os.path.join(out, "tickets_enriched.json"), encoding="utf-8") as fh:
                    data = json.load(fh)["tickets"]
                t0 = data[0]
                self.assertEqual(t0["jira_enriched"], enriched)
                self.assertNotIn("reporter", t0)
                self.assertTrue(t0["reporter_id"].startswith("R"))
                self.assertNotIn("Jan Testowy", json.dumps(data, ensure_ascii=False))
                self.assertNotIn("Secret123", json.dumps(data, ensure_ascii=False))
                self.assertEqual(t0["problem_type"], "master_data_request")
                self.assertEqual(t0["country"], "PL")
                if enriched:
                    self.assertEqual(t0["resolution"], "Done")
                    self.assertEqual(len(t0["comments"]), 2)
                    self.assertIn("22156961", t0["entities"]["work_order_ids"])
                    self.assertFalse(t0["desc_truncated"])
                    self.assertEqual(t0["custom_fields"]["Affected Country"], ["Poland"])
                self.assertTrue(os.path.exists(os.path.join(out, "groups.json")))
                self.assertTrue(os.path.exists(os.path.join(out, "stats.md")))


if __name__ == "__main__":
    unittest.main()
