import email
import tempfile
import unittest
from email import policy
from pathlib import Path

from mailqueue.config import Config
from mailqueue.emailutil import normalize_email
from mailqueue.ingest import IngestError, read_table, validate_records
from mailqueue.message import build_message
from mailqueue.template import RenderError, missing_tags, render, tags_in


def write(text, name="in.csv", raw=None):
    d = tempfile.mkdtemp()
    p = Path(d) / name
    if raw is not None:
        p.write_bytes(raw)
    else:
        p.write_text(text, encoding="utf-8", newline="")
    return p


class EmailTests(unittest.TestCase):
    def test_normalize_case_and_space(self):
        self.assertEqual(normalize_email("  Alice@Example.COM ")[0], "alice@example.com")

    def test_rejects(self):
        for raw, reason in [
            ("", "empty email"),
            ("user@@example.com", "malformed email"),
            ("nodomain", "malformed email"),
            ("a@x.com; b@y.com", "multiple addresses"),
            ("a@x.com, b@y.com", "multiple addresses"),
            ("Alice <a@x.com>", "display-name form not allowed"),
            ("a@localhost", "malformed email"),
            ("a b@x.com", "malformed email"),
            ("a@x.com\r\nBcc: z@y.com", "control characters in email"),
            ("a" * 250 + "@x.com", None),
        ]:
            addr, err = normalize_email(raw)
            self.assertIsNone(addr, raw)
            if reason:
                self.assertEqual(err, reason, raw)

    def test_unicode(self):
        addr, err = normalize_email("józef@exämple.com")
        self.assertIsNone(err)
        self.assertEqual(addr, "józef@xn--exmple-cua.com")
        self.assertIsNone(normalize_email("user@bücher.de")[1])

    def test_nfc_variants_equal(self):
        a = normalize_email("josé@x.com")[0]
        b = normalize_email("josé@x.com")[0]
        self.assertEqual(a, b)


class TemplateTests(unittest.TestCase):
    def test_tolerant_match(self):
        self.assertEqual(missing_tags(["Hi {{ Name }}"], ["name"]), [])
        self.assertEqual(render("Hi {{ Name }}", {"name": "Bo"}), "Hi Bo")

    def test_missing_names_all(self):
        self.assertEqual(missing_tags(["{{name}} {{roll_no}} {{dept}}"], ["name"]), ["roll_no", "dept"])

    def test_empty_value_render_error(self):
        with self.assertRaises(RenderError):
            render("{{name}}", {"name": " "})


class IngestTests(unittest.TestCase):
    def test_header_only(self):
        with self.assertRaisesRegex(IngestError, "no data rows"):
            read_table(write("email,name\n"), "email")

    def test_trailing_blank_lines_skipped(self):
        t = read_table(write("email,name\na@x.com,A\n\n  \n,\n"), "email")
        self.assertEqual(len(t.records), 1)

    def test_non_utf8(self):
        with self.assertRaisesRegex(IngestError, "not valid UTF-8"):
            read_table(write("", raw="email,name\na@x.com,Jos\xe9\n".encode("latin-1")), "email")

    def test_bom_ok(self):
        t = read_table(write("", raw=b"\xef\xbb\xbfemail,name\na@x.com,A\n"), "email")
        self.assertEqual(t.header, ["email", "name"])

    def test_duplicate_and_whitespace_header(self):
        with self.assertRaises(IngestError) as cm:
            read_table(write("email,name,Name, city\na@x.com,A,B,C\n"), "email")
        msg = str(cm.exception)
        self.assertIn("'Name'", msg)
        self.assertIn("' city'", msg)

    def test_empty_header_name(self):
        with self.assertRaisesRegex(IngestError, "position 3"):
            read_table(write("email,name,\na@x.com,A,\n"), "email")

    def test_email_column_case_insensitive(self):
        t = read_table(write("Email,name\na@x.com,A\n"), "email")
        self.assertEqual(t.email_idx, 0)

    def test_missing_email_column(self):
        with self.assertRaisesRegex(IngestError, "not found"):
            read_table(write("mail,name\na@x.com,A\n"), "email")

    def test_semicolon_hint(self):
        with self.assertRaisesRegex(IngestError, "--delimiter"):
            read_table(write("email;name\na@x.com;A\n"), "email")

    def test_multiline_record_line_numbers(self):
        t = read_table(write('email,name\na@x.com,"Line1\nLine2"\nb@x.com\n'), "email")
        acc, rej = validate_records(t, {"name"}, set())
        self.assertEqual(len(acc), 1)
        self.assertEqual((rej[0].recno, rej[0].line), (2, 4))
        self.assertIn("field count mismatch (expected 2, got 1)", rej[0].reason)

    def rows(self, csv_text, req=("name",), hdr=()):
        t = read_table(write(csv_text), "email")
        return validate_records(t, set(req), set(hdr))

    def test_empty_template_field_rejected(self):
        acc, rej = self.rows("email,name\na@x.com,\nb@x.com,B\n")
        self.assertEqual([a.email for a in acc], ["b@x.com"])
        self.assertEqual(rej[0].reason, "empty field: name")

    def test_duplicates_first_wins(self):
        acc, rej = self.rows("email,name\na@x.com,Alice\nB@x.com,Bob\nA@x.com,Alicia\n")
        self.assertEqual(acc[0].data["name"], "Alice")
        self.assertEqual(rej[0].reason, "duplicate of line 2")

    def test_first_invalid_duplicate_does_not_count(self):
        acc, rej = self.rows("email,name\na@x.com,\na@x.com,Alicia\n")
        self.assertEqual(acc[0].data["name"], "Alicia")
        self.assertEqual(rej[0].reason, "empty field: name")

    def test_header_injection_rejected(self):
        acc, rej = self.rows('email,name\na@x.com,"Al\nBcc: x@y.com"\n', hdr=("name",))
        self.assertFalse(acc)
        self.assertIn("line break in header value", rej[0].reason)

    def test_all_rows_invalid_returns_no_accepted(self):
        acc, rej = self.rows("email,name\nbad,A\n")
        self.assertEqual((len(acc), len(rej)), (0, 1))


class MessageTests(unittest.TestCase):
    def camp(self, subject="Welcome, José — 🎉 {{name}}", html=None):
        return {"subject_tpl": subject, "body_tpl": "Hi {{name}}, café ☕", "html_tpl": html}

    def test_subject_round_trip_ascii_recipient(self):
        cfg = Config()
        msg = build_message(cfg, self.camp(), {"name": "Zoë"}, "a@x.com")
        parsed = email.message_from_bytes(msg.as_bytes(), policy=policy.default)
        self.assertEqual(parsed["Subject"], "Welcome, José — 🎉 Zoë")
        self.assertIn("=?utf-8?", msg.as_bytes().decode("ascii", "ignore").lower())
        self.assertIn("café ☕", parsed.get_body().get_content())

    def test_subject_round_trip_unicode_recipient(self):
        msg = build_message(Config(), self.camp(), {"name": "Zoë"}, "józef@xn--exmple-cua.com")
        parsed = email.message_from_bytes(msg.as_bytes(), policy=policy.default)
        self.assertEqual(parsed["Subject"], "Welcome, José — 🎉 Zoë")

    def test_html_escaped(self):
        msg = build_message(Config(), self.camp(html="<p>{{name}}</p>"), {"name": "<b>"}, "a@x.com")
        self.assertIn("&lt;b&gt;", msg.get_body(("html",)).get_content())

    def test_line_break_in_subject_refused(self):
        with self.assertRaises(RenderError):
            build_message(Config(), self.camp("{{name}}"), {"name": "a\nBcc: x@y.com"}, "a@x.com")


if __name__ == "__main__":
    unittest.main()
