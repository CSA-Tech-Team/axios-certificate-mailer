import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from mailqueue.cli import main
from mailqueue.store import Store


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.data = str(self.dir / "data")
        (self.dir / "body.hbs").write_text("<p>Hi {{ Name }}</p>", encoding="utf-8")
        os.environ.pop("MAILQ_SMTP_PASSWORD", None)

    def csv(self, text, name="r.csv"):
        p = self.dir / name
        p.write_text(text, encoding="utf-8", newline="")
        return str(p)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--data-dir", self.data, *argv])
        return code, out.getvalue(), err.getvalue()

    def enqueue(self, csv_path, *extra, subject="Hello {{name}}"):
        return self.run_cli("enqueue", "--campaign", "c", "--csv", csv_path, "--subject", subject,
                            "--template", str(self.dir / "body.hbs"), *extra)

    def test_enqueue_idempotent_and_status(self):
        p = self.csv("Email,name\nA@x.com,Al\nb@x.com,Bo\nbad,Cy\nb@X.com,Bo2\n")
        code, out, _ = self.enqueue(p)
        self.assertEqual(code, 0)
        self.assertIn("newly enqueued: 2", out)
        self.assertIn("rejected:       2", out)
        rejects = list((Path(self.data) / "rejects").glob("*.csv"))
        self.assertEqual(len(rejects), 1)
        text = rejects[0].read_text(encoding="utf-8")
        self.assertIn("malformed email", text)
        self.assertIn("duplicate of line 3", text)
        code, out, _ = self.enqueue(p)
        self.assertIn("newly enqueued: 0", out)
        self.assertIn("already queued: 2 (pending: 2)", out)
        code, out, _ = self.run_cli("status")
        self.assertIn("pending        2", out)
        self.assertIn("Dead-letter entries: 0", out)

    def test_missing_tag_fails_before_queue(self):
        p = self.csv("email,name\na@x.com,Al\n")
        code, _, err = self.enqueue(p, subject="Hi {{name}} {{roll_no}}")
        self.assertEqual(code, 2)
        self.assertIn("{{roll_no}}", err)
        self.assertFalse((Path(self.data) / "queue.db").exists())

    def test_header_only_and_all_rejected(self):
        self.assertEqual(self.enqueue(self.csv("email,name\n"))[0], 2)
        code, _, err = self.enqueue(self.csv("email,name\nbad,x\n", "b.csv"))
        self.assertEqual(code, 2)
        self.assertIn("no valid rows", err)

    def test_threshold_requires_yes(self):
        rows = "email,name\n" + "".join(f"u{i}@x.com,N\n" for i in range(6))
        p = self.csv(rows)
        code, _, err = self.run_cli("enqueue", "--campaign", "c", "--csv", p, "--subject", "S {{name}}",
                                    "--template", str(self.dir / "body.hbs"))
        self.assertEqual(code, 0)  # 6 rows < default 5000
        os.environ["MAILQ_CSV_THRESHOLD"] = "3"
        try:
            code, _, err = self.run_cli("enqueue", "--campaign", "d", "--csv", p, "--subject", "S {{name}}",
                                        "--template", str(self.dir / "body.hbs"))
            self.assertEqual(code, 2)
            self.assertIn("threshold", err)
            code, out, _ = self.run_cli("enqueue", "--campaign", "d", "--csv", p, "--subject", "S {{name}}",
                                        "--template", str(self.dir / "body.hbs"), "--yes")
            self.assertEqual(code, 0)
        finally:
            del os.environ["MAILQ_CSV_THRESHOLD"]

    def test_template_change_refused_without_flag(self):
        p = self.csv("email,name\na@x.com,Al\n")
        self.assertEqual(self.enqueue(p)[0], 0)
        code, _, err = self.enqueue(p, subject="Other {{name}}")
        self.assertEqual(code, 2)
        self.assertIn("--allow-template-change", err)
        self.assertEqual(self.enqueue(p, "--allow-template-change", subject="Other {{name}}")[0], 0)

    def test_dry_run_and_send_without_password(self):
        p = self.csv("email,name\na@x.com,Al\n")
        self.enqueue(p)
        code, out, _ = self.run_cli("send", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Subject: Hello Al", out)
        code, _, _ = self.run_cli("send")
        self.assertEqual(code, 3)  # no password configured
        s = Store(Path(self.data) / "queue.db")
        self.assertEqual(s.counts()["pending"], 1)
        s.close()

    def test_requeue_requires_confirmation_and_import_history(self):
        p = self.csv("email,name\na@x.com,Al\nb@x.com,Bo\n")
        self.enqueue(p)
        hist = self.csv("a@x.com\nold@x.com\n", "h.txt")
        code, out, _ = self.run_cli("import-history", "--campaign", "c", hist)
        self.assertIn("1 added, 1 updated", out)
        s = Store(Path(self.data) / "queue.db")
        r = s.claim_next(1e12)
        s.mark_dead(r["campaign_id"], r["email_norm"], "550 gone")
        s.close()
        code, _, err = self.run_cli("requeue", "--campaign", "c", "--all-dead")
        self.assertEqual(code, 2)
        code, out, _ = self.run_cli("requeue", "--campaign", "c", "--all-dead", "--yes")
        self.assertIn("requeued 1", out)


if __name__ == "__main__":
    unittest.main()
