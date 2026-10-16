import datetime
import decimal
import json
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hibot.db import masking  # noqa: E402


class TestMaskText(unittest.TestCase):
    def test_email(self):
        out = masking.mask_text("contact john.smith@example.com now")
        self.assertNotIn("john.smith", out)
        self.assertNotIn("example", out)
        self.assertIn("j***@e***.com", out)

    def test_several_emails(self):
        out = masking.mask_text("a@b.co; second.user@mail.example.org")
        self.assertNotIn("second.user", out)

    def test_international_phone(self):
        for s in ["+90 532 123 45 67", "+49 (30) 1234-5678", "+4915112345678"]:
            with self.subTest(s=s):
                out = masking.mask_text("call " + s + " today")
                self.assertNotRegex(out, r"\d{4}")
                self.assertIn("***", out)

    def test_grouped_local_phone(self):
        out = masking.mask_text("tel 0532 123 45 67")
        self.assertNotIn("0532", out)

    def test_dates_times_guid_ip_untouched(self):
        for s in ["2024-05-01 12:30:45", "01.05.2024 12:30", "2024-05-01T12:30:45.123",
                  "3f2504e0-4f89-11d3-9a0c-0305e82c3301", "10.200.30.40", "12:30:45"]:
            with self.subTest(s=s):
                self.assertEqual(masking.mask_text(s), s)

    def test_plain_ids_untouched(self):
        for s in ["WorkOrderId 123456789", "doc 4900123456", "status=3", "Id: 12"]:
            with self.subTest(s=s):
                self.assertEqual(masking.mask_text(s), s)

    def test_iban_valid_masked(self):
        for iban in ["DE89 3704 0044 0532 0130 00", "TR330006100519786457841326", "GB82WEST12345698765432"]:
            with self.subTest(iban=iban):
                out = masking.mask_text("pay to " + iban + " please")
                self.assertIn("[IBAN]", out)
                self.assertNotIn("0532", out)

    def test_iban_like_invalid_checksum_untouched(self):
        s = "code AB12 CDEF GHIJ KLMN"
        self.assertEqual(masking.mask_text(s), s)

    def test_card_luhn(self):
        self.assertIn("[CARD]", masking.mask_text("card 4111 1111 1111 1111 exp"))
        self.assertNotIn("[CARD]", masking.mask_text("order 4111 1111 1111 1112 x"))

    def test_json_pii_keys(self):
        body = json.dumps({"Phone": "05321234567", "email": "x@y.com", "FirstName": "Maria",
                           "LastName": "Garcia", "Address": "Main St 5", "IBAN": "TR330006100519786457841326",
                           "TaxNumber": "1234567890", "Status": "OK", "WorkOrderId": 123456})
        out = masking.mask_text(body)
        for leak in ["05321234567", "x@y.com", "Maria", "Garcia", "Main St", "TR33", "1234567890"]:
            self.assertNotIn(leak, out)
        self.assertIn('"Status": "OK"', out)
        self.assertIn("123456", out)
        json.loads(out)  # still valid JSON

    def test_json_numeric_phone(self):
        out = masking.mask_text('{"mobile": 5321234567}')
        self.assertNotIn("5321234567", out)

    def test_xml_pii(self):
        out = masking.mask_text("<Customer><Phone>05321234567</Phone><City>Berlin</City><Email>a@b.com</Email></Customer>")
        self.assertNotIn("05321234567", out)
        self.assertNotIn("a@b.com", out)
        self.assertIn("<City>Berlin</City>", out)

    def test_key_value_text(self):
        out = masking.mask_text("user=bob; password=Secr3t!; token=abcdef; phone=05321234567")
        self.assertNotIn("Secr3t", out)
        self.assertNotIn("abcdef", out)
        self.assertNotIn("05321234567", out)

    def test_secrets_in_bodies(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123SIGNATURE"
        out = masking.mask_text('{"Authorization":"Bearer abcdefghijklmnop1234"} and ' + jwt)
        self.assertNotIn("abcdefghijklmnop1234", out)
        self.assertNotIn("eyJhbGci", out)

    def test_empty_and_none(self):
        self.assertEqual(masking.mask_text(""), "")


class TestMaskRows(unittest.TestCase):
    def test_declared_columns_masked_even_if_numeric(self):
        cols = ["ContactName", "ContactPhone", "WorkOrderId", "Idn"]
        rows = [["Ayse Yilmaz", "05321234567", 42, 12345678901]]
        out, rep = masking.mask_rows(cols, rows, pii={"ContactName": "name", "ContactPhone": "phone", "Idn": "identity"})
        self.assertEqual(out[0][0], "A*** Y***")
        self.assertTrue(out[0][1].endswith("67") and "532" not in out[0][1])
        self.assertEqual(out[0][2], 42)
        self.assertEqual(out[0][3], "[REDACTED]")
        self.assertEqual(rep.column_kinds["ContactName"], "name")

    def test_heuristic_column_names(self):
        cols = ["CustomerEmail", "MobilePhone", "FullName", "BillingAddress", "TaxNumber", "Password", "StatusName"]
        rows = [["a@b.com", "05321234567", "Jo Bloggs", "1 High St", "9988776655", "pw", "Open"]]
        out, _ = masking.mask_rows(cols, rows)
        r = out[0]
        self.assertNotIn("a@b.com", r[0])
        self.assertNotIn("0532", r[1])
        self.assertEqual(r[2], "J*** B***")
        self.assertEqual(r[3], "[ADDRESS]")
        self.assertEqual(r[4], "[REDACTED]")
        self.assertEqual(r[5], "[REDACTED]")
        self.assertEqual(r[6], "Open")

    def test_status_name_not_masked(self):
        out, _ = masking.mask_rows(["StatusName", "ServiceShopName", "FieldText"], [["Closed", "ACME Service", "Open"]])
        self.assertEqual(out[0], ["Closed", "ACME Service", "Open"])

    def test_contact_kind(self):
        out, _ = masking.mask_rows(["SendingAddress"], [["a@b.com"], ["+905321234567"]], pii={"SendingAddress": "contact"})
        self.assertNotIn("a@b.com", out[0][0])
        self.assertNotIn("532", out[1][0])

    def test_long_body_truncated_after_masking(self):
        body = "x" * 5000 + " tail@example.com"
        out, rep = masking.mask_rows(["ResponseBody"], [[body]], pii={"ResponseBody": "free_text_body"}, body_limit=600)
        self.assertLessEqual(len(out[0][0]), 600 + 60)
        self.assertIn("truncated", out[0][0])
        self.assertEqual(rep.truncated_cells, 1)

    def test_email_at_truncation_boundary_never_leaks(self):
        body = "y" * 590 + "secret.person@example.com" + "z" * 100
        out, _ = masking.mask_rows(["RequestBody"], [[body]], pii={"RequestBody": "free_text_body"}, body_limit=600)
        self.assertNotIn("secret.person", out[0][0])
        self.assertNotIn("secret", out[0][0].replace("secret.person", ""))

    def test_error_message_keeps_more_text(self):
        msg = "Conversion failed " + "q" * 1000
        out, _ = masking.mask_rows(["ErrorMessage"], [[msg]])
        self.assertNotIn("truncated", out[0][0])

    def test_value_types_json_serialisable(self):
        row = [datetime.datetime(2025, 1, 2, 3, 4, 5), datetime.date(2025, 1, 2), decimal.Decimal("12.50"),
               uuid.UUID(int=5), b"\x00\x01", None, True, 3.5, 7]
        out, _ = masking.mask_rows(["a"] * 9, [row])
        json.dumps(out)
        self.assertEqual(out[0][0], "2025-01-02T03:04:05")
        self.assertEqual(out[0][2], "12.50")
        self.assertEqual(out[0][4], "<binary 2 bytes>")
        self.assertIsNone(out[0][5])
        self.assertIs(out[0][6], True)

    def test_masks_every_row(self):
        rows = [["x%d@example.com" % i] for i in range(50)]
        out, _ = masking.mask_rows(["Notes"], rows)
        self.assertTrue(all("@example.com" not in r[0] for r in out))


if __name__ == "__main__":
    unittest.main()
