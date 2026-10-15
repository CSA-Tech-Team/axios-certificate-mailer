import contextlib
import email
import io
import tempfile
import unittest
from email import policy
from pathlib import Path

from certificate_mailer import DEFAULT_BODY, DEFAULT_SUBJECT
from mailqueue.cli import main
from mailqueue.config import Config
from mailqueue.message import build_message
from mailqueue.store import Store
from mailqueue.template import render

ROOT = Path(__file__).resolve().parent.parent
HTML_TEMPLATE = ROOT / "templates" / "email.hbs"
TEXT_TEMPLATE = ROOT / "templates" / "email-text.hbs"
CERTIFICATE_TEMPLATE = ROOT / "templates" / "certificate.hbs"


class CertificateHbsTests(unittest.TestCase):
    """templates/certificate.hbs must be dist/index.html with only the two field values templated."""

    def test_renders_master_certificate_with_values(self):
        source = CERTIFICATE_TEMPLATE.read_text(encoding="utf-8")
        page = render(source, {"name": "Aarav <Kumar>", "college": "PSG College of Technology"}, html=True)
        self.assertIn('value="Aarav &lt;Kumar&gt;"', page)
        self.assertIn('value="PSG College of Technology"', page)
        self.assertNotIn("{{", page)
        expected = (ROOT / "dist" / "index.html").read_text(encoding="utf-8")
        expected = (expected.replace('href="styles.css"', 'href="../dist/styles.css"')
                    .replace('src="script.js"', 'src="../dist/script.js"')
                    .replace('src="assets/', 'src="../dist/assets/'))
        got = page.replace('              value="Aarav &lt;Kumar&gt;"\n', "").replace(
            '              value="PSG College of Technology"\n', "")
        self.assertEqual(got.strip().splitlines(), expected.strip().splitlines())

    def test_every_referenced_asset_exists(self):
        import re
        source = CERTIFICATE_TEMPLATE.read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="\.\./(dist/[^"]+)"', source)
        self.assertGreater(len(refs), 10)
        for ref in refs:
            self.assertTrue((ROOT / ref).is_file(), ref)


def build_email(test, subject, html_template, text_template, row):
    """Enqueue one CSV row with mailqueue and return the parsed message it would send."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "r.csv").write_text(",".join(row) + "\n" + ",".join(row.values()) + "\n", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--data-dir", str(tmp / "data"), "enqueue", "--campaign", "axios", "--csv", str(tmp / "r.csv"),
                     "--subject", subject, "--template", str(html_template), "--text-template", str(text_template)])
    test.assertEqual(code, 0, err.getvalue())
    store = Store(tmp / "data" / "queue.db")
    try:
        camp = store.campaign_by_name("axios")
        msg = build_message(Config(), camp, row, store.unsent_rows()[0]["email_norm"])
    finally:
        store.close()
    return email.message_from_bytes(msg.as_bytes(), policy=policy.default)


def assert_axios_layout(test, html):
    """The layout from mail-templates/emails/*.tsx survives conversion."""
    for piece in ('src="https://public.amcspsgtech.in/content/axios.png"', "content/bg.png",
                  'href="https://www.instagram.com/axios.psgtech"', 'href="https://linktr.ee/techteam.csa"',
                  'href="https://axios.psgtech.ac.in"', "content/linktree-icon.png",
                  'bgcolor="#1b1b1b"', "Copyright © 2024, Computational Sciences Association"):
        test.assertIn(piece, html)
    test.assertNotIn("{{", html)


class EmailTemplateTests(unittest.TestCase):
    """email.hbs is basic-axios-mail.tsx's layout carrying certificate_mailer.py's message."""

    ROW = {"name": "Aarav <Kumar>", "college": "PSG College of Technology", "email": "a@x.com"}

    def test_text_template_is_default_body(self):
        self.assertEqual(TEXT_TEMPLATE.read_text(encoding="utf-8"), DEFAULT_BODY.replace("{name}", "{{name}}"))

    def test_rendered_email_matches_certificate_mailer(self):
        parsed = build_email(self, DEFAULT_SUBJECT, HTML_TEMPLATE, TEXT_TEMPLATE, self.ROW)
        self.assertEqual(parsed["Subject"], DEFAULT_SUBJECT)
        text = parsed.get_body(("plain",)).get_content().replace("\r\n", "\n")
        self.assertEqual(text, DEFAULT_BODY.format(name="Aarav <Kumar>"))
        html = parsed.get_body(("html",)).get_content()
        self.assertIn("<b>Hello Aarav &lt;Kumar&gt;</b>,", html)
        self.assertIn("Please find your certificate of participation attached to this email.", html)
        self.assertIn("Regards,<br /> Computational Sciences Association<br /> PSG College of Technology", html)
        self.assertNotIn("Lorem ipsum", html)
        assert_axios_layout(self, html)


class OtpTemplateTests(unittest.TestCase):
    """otp-mail.hbs is otp-mail.tsx with the code as {{otp}}."""

    def test_rendered_otp_email(self):
        parsed = build_email(self, "Your AXIOS verification code", ROOT / "templates" / "otp-mail.hbs",
                             ROOT / "templates" / "otp-mail-text.hbs", {"email": "a@x.com", "otp": "482913"})
        html = parsed.get_body(("html",)).get_content()
        self.assertIn('font-weight:700;margin:16px 0">482913</p>', html)
        self.assertIn("Your mail verification OTP: 482913", html)
        assert_axios_layout(self, html)
        text = parsed.get_body(("plain",)).get_content().replace("\r\n", "\n")
        self.assertIn("\n482913\n", text)
        self.assertTrue(text.endswith("With Regards,\nAxios Team\n"))


if __name__ == "__main__":
    unittest.main()
