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


class EmailTemplateTests(unittest.TestCase):
    """The email .hbs templates must be certificate_mailer.py's email, only in Handlebars form."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.data = str(self.dir / "data")
        (self.dir / "r.csv").write_text(
            "name,college,email\nAarav <Kumar>,PSG College of Technology,a@x.com\n", encoding="utf-8")

    def build(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--data-dir", self.data, "enqueue", "--campaign", "axios", "--csv", str(self.dir / "r.csv"),
                         "--subject", DEFAULT_SUBJECT, "--template", str(HTML_TEMPLATE),
                         "--text-template", str(TEXT_TEMPLATE)])
        self.assertEqual(code, 0, err.getvalue())
        store = Store(Path(self.data) / "queue.db")
        try:
            camp = store.campaign_by_name("axios")
            row = store.unsent_rows()[0]
            msg = build_message(Config(), camp, {"name": "Aarav <Kumar>", "email": "a@x.com"}, row["email_norm"])
        finally:
            store.close()
        return email.message_from_bytes(msg.as_bytes(), policy=policy.default)

    def test_text_template_is_default_body(self):
        self.assertEqual(TEXT_TEMPLATE.read_text(encoding="utf-8"), DEFAULT_BODY.replace("{name}", "{{name}}"))

    def test_rendered_email_matches_certificate_mailer(self):
        parsed = self.build()
        self.assertEqual(parsed["Subject"], DEFAULT_SUBJECT)
        text = parsed.get_body(("plain",)).get_content().replace("\r\n", "\n")
        self.assertEqual(text, DEFAULT_BODY.format(name="Aarav <Kumar>"))
        html = parsed.get_body(("html",)).get_content()
        self.assertIn("<p>Hello Aarav &lt;Kumar&gt;,</p>", html)
        self.assertNotIn("{{", html)
        self.assertEqual([p for p in parsed.walk() if p.get_content_maintype() == "image"], [])


if __name__ == "__main__":
    unittest.main()
