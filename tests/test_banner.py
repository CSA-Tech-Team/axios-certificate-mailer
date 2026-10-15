import contextlib
import email
import io
import struct
import tempfile
import unittest
import zlib
from email import policy
from pathlib import Path

from mailqueue.cli import main
from mailqueue.config import Config
from mailqueue.message import build_message
from mailqueue.store import Store

SUBJECT = "Your AXIOS '26 Certificate of Participation"
TEMPLATE_HTML = '<img src="cid:banner" alt="banner">\n<p>Hello {{name}},</p>\n<p>Your certificate is attached.</p>\n'


def tiny_png() -> bytes:
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = b"\x00\xf2\xb7\x05"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class BannerTemplateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.data = str(self.dir / "data")
        self.png = self.dir / "banner.png"
        self.png.write_bytes(tiny_png())
        (self.dir / "r.csv").write_text("name,email\nAarav Kumar,a@x.com\nPriya S,b@x.com\n", encoding="utf-8")
        self.template = self.dir / "banner.hbs"
        self.template.write_text(TEMPLATE_HTML, encoding="utf-8")

    def cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--data-dir", self.data, "enqueue", "--campaign", "axios", "--csv", str(self.dir / "r.csv"),
                         "--subject", SUBJECT, "--template", str(self.template), *extra])
        return code, out.getvalue(), err.getvalue()

    def test_needs_banner_when_template_uses_cid(self):
        code, _, err = self.cli()
        self.assertEqual(code, 2)
        self.assertIn("cid:banner", err)

    def test_rejects_non_image_banner(self):
        (self.dir / "b.txt").write_text("x")
        code, _, err = self.cli("--banner", str(self.dir / "b.txt"))
        self.assertEqual(code, 2)
        self.assertIn("--banner", err)

    def test_end_to_end_message_has_banner(self):
        code, out, err = self.cli("--banner", str(self.png))
        self.assertEqual(code, 0, err)
        self.assertIn("newly enqueued: 2", out)
        store = Store(Path(self.data) / "queue.db")
        try:
            camp = store.campaign_by_name("axios")
            self.assertEqual(bytes(camp["banner"]), self.png.read_bytes())
            row = store.unsent_rows()[0]
            msg = build_message(Config(), camp, {"name": "Aarav Kumar", "email": "a@x.com"}, row["email_norm"])
        finally:
            store.close()
        parsed = email.message_from_bytes(msg.as_bytes(), policy=policy.default)
        self.assertEqual(parsed["Subject"], SUBJECT)
        self.assertEqual(parsed.get_content_type(), "multipart/alternative")
        related = parsed.get_body(("related",)) or [p for p in parsed.walk() if p.get_content_type() == "multipart/related"][0]
        images = [p for p in parsed.walk() if p.get_content_maintype() == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["Content-ID"], "<banner>")
        self.assertEqual(images[0].get_content(), self.png.read_bytes())
        html = parsed.get_body(("html",)).get_content()
        self.assertIn('src="cid:banner"', html)
        text = parsed.get_body(("plain",)).get_content()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertEqual(lines, ["Hello Aarav Kumar,", "Your certificate is attached."])
        self.assertNotIn("{{", html)

    def test_changing_banner_counts_as_template_change(self):
        self.assertEqual(self.cli("--banner", str(self.png))[0], 0)
        other = self.dir / "other.png"
        other.write_bytes(tiny_png() + b"\x00")
        code, _, err = self.cli("--banner", str(other))
        self.assertEqual(code, 2)
        self.assertIn("--allow-template-change", err)

    def test_old_database_is_migrated(self):
        import sqlite3
        db = Path(self.dir) / "old.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE campaigns(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,"
                    " subject_tpl TEXT NOT NULL DEFAULT '', body_tpl TEXT NOT NULL DEFAULT '', html_tpl TEXT,"
                    " tpl_hash TEXT, created_at REAL NOT NULL, last_enqueue_at REAL, last_enqueue_rejects INTEGER)")
        con.commit()
        con.close()
        s = Store(db)
        cols = {r["name"] for r in s.conn.execute("PRAGMA table_info(campaigns)")}
        s.close()
        self.assertTrue({"banner", "banner_type"} <= cols)


if __name__ == "__main__":
    unittest.main()
