"""Tickets newer than the dataset: intake from a Jira XML export (inbox or --xml). No network/DB."""
import glob
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hibot.agent import intake

SAMPLE = sorted(glob.glob(str(Path(__file__).resolve().parents[1] / "tools" / "incidents" / "testdata" / "*.xml")))[0]


class XmlIntakeTests(unittest.TestCase):
    def test_explicit_xml(self):
        inc = intake.build_incident("SM-9000001", xml_path=SAMPLE)
        self.assertEqual(inc["source"], "jira_xml")
        self.assertEqual(inc["key"], "SM-9000001")
        self.assertRegex(inc["created"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")   # dataset format, Jira local time
        self.assertTrue(inc["resolved"])
        self.assertIn("comments_note", inc)

    def test_inbox_fallback_for_unknown_key(self):
        d = Path(tempfile.mkdtemp())
        try:
            shutil.copy(SAMPLE, d / "SM-9000001.xml")
            with mock.patch.object(intake, "inbox_dir", return_value=d):
                inc = intake.build_incident("SM-9000001")
            self.assertEqual(inc["source"], "jira_xml")
        finally:
            shutil.rmtree(d)

    def test_unknown_key_without_xml_explains(self):
        with mock.patch.object(intake, "inbox_dir", return_value=Path(tempfile.gettempdir()) / "hibot-no-inbox"):
            with self.assertRaises(intake.IntakeError) as cm:
                intake.build_incident("SM-9999998")
        self.assertIn("Jira XML", str(cm.exception))

    def test_jira_local_conversion(self):
        self.assertEqual(intake._jira_local("Thu, 17 Sep 2026 08:58:00 +0300"), "2026-09-17 08:58")
        self.assertEqual(intake._jira_local("Thu, 17 Sep 2026 05:58:00 +0000"), "2026-09-17 08:58")
        self.assertIsNone(intake._jira_local(""))


if __name__ == "__main__":
    unittest.main()
