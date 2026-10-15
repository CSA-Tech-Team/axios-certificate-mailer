import random
import smtplib
import tempfile
import unittest
from pathlib import Path

from mailqueue import sender
from mailqueue.config import Config
from mailqueue.sender import Runner, dry_run
from mailqueue.smtpclient import Kind, classify
from mailqueue.store import Store


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t
        self.stopped = False
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += max(s, 0)


class FakeTransport:
    """script: list of exceptions/None consumed per send; default success."""

    def __init__(self, script=None, connect_script=None):
        self.script = list(script or [])
        self.connect_script = list(connect_script or [])
        self.connected = False
        self.sent = []
        self.connects = 0

    def connect(self):
        self.connects += 1
        if self.connect_script:
            exc = self.connect_script.pop(0)
            if exc:
                raise exc
        self.connected = True

    def close(self):
        self.connected = False

    def send(self, msg, rcpt):
        item = self.script.pop(0) if self.script else None
        if item is not None:
            raise item
        self.sent.append((rcpt, msg["Subject"]))


def refused(code, text):
    return smtplib.SMTPRecipientsRefused({"x@y.com": (code, text.encode())})


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(Path(self.dir) / "q.db")
        self.addCleanup(self.store.close)
        self.cfg = Config(data_dir=Path(self.dir), smtp_password="pw", rate_per_sec=1000,
                          breaker_n=3, reconnect_attempts=2, max_attempts=3, cooldown_s=900)
        self.cid, _ = self.store.get_or_create_campaign("c", "Hello {{name}}", "Body {{name}}", None)

    def fill(self, n):
        self.store.enqueue(self.cid, [(f"u{i}@x.com", {"name": f"N{i}"}, i + 2) for i in range(n)])

    def run_it(self, transport, clock=None, **kw):
        r = Runner(self.cfg, self.store, transport, clock=clock or FakeClock(),
                   rng=random.Random(1), **kw)
        return r.run()

    def test_happy_path_and_pacing(self):
        self.fill(3)
        self.cfg.rate_per_sec = 0.5
        clock = FakeClock()
        t = FakeTransport()
        res = self.run_it(t, clock)
        self.assertEqual((res.exit_code, res.sent), (0, 3))
        self.assertEqual(self.store.counts()["sent"], 3)
        self.assertEqual([round(s) for s in clock.sleeps[:2]], [2, 2])
        self.assertEqual(t.sent[0][1], "Hello N0")

    def test_permanent_dead_immediately_and_not_counted_by_breaker(self):
        self.fill(6)
        t = FakeTransport([refused(550, "5.1.1 user unknown")] * 5)
        res = self.run_it(t)
        self.assertEqual(res.exit_code, 0)
        c = self.store.counts()
        self.assertEqual((c["dead"], c["sent"]), (5, 1))

    def test_transient_backoff_then_retry(self):
        self.fill(1)
        clock = FakeClock()
        t = FakeTransport([smtplib.SMTPDataError(451, b"4.3.0 try again later")])
        res = self.run_it(t, clock)
        self.assertEqual((res.exit_code, res.sent), (0, 1))
        self.assertTrue(any(30 <= s <= 90 for s in clock.sleeps))  # base 60 * jitter

    def test_max_attempts_dead(self):
        self.fill(1)
        t = FakeTransport([smtplib.SMTPDataError(451, b"x")] * 3)
        self.run_it(t)
        self.assertEqual(self.store.counts()["dead"], 1)

    def test_auth_failure_stops_without_consuming_attempt(self):
        self.fill(3)
        t = FakeTransport([smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials")])
        res = self.run_it(t)
        self.assertEqual(res.exit_code, sender.EXIT_AUTH)
        c = self.store.counts()
        self.assertEqual((c["retry_pending"], c["pending"], c["dead"]), (1, 2, 0))
        self.assertEqual(self.store.claim_next(1e12)["attempts"], 0)

    def test_preflight_auth_failure_touches_nothing(self):
        self.fill(2)
        t = FakeTransport(connect_script=[smtplib.SMTPAuthenticationError(535, b"bad")])
        res = self.run_it(t)
        self.assertEqual(res.exit_code, sender.EXIT_AUTH)
        self.assertEqual(self.store.counts()["pending"], 2)

    def test_missing_password(self):
        self.cfg.smtp_password = ""
        self.assertEqual(self.run_it(FakeTransport()).exit_code, sender.EXIT_AUTH)

    def test_provider_quota_5xx_is_not_dead_and_blocks_day(self):
        self.fill(4)
        t = FakeTransport([None, smtplib.SMTPDataError(550, b"5.4.5 Daily user sending quota exceeded.")])
        clock = FakeClock()
        res = self.run_it(t, clock)
        self.assertEqual(res.exit_code, 0)
        self.assertIn("quota", res.reason)
        c = self.store.counts()
        self.assertEqual((c["sent"], c["dead"], c["retry_pending"], c["pending"]), (1, 0, 1, 2))
        # later run the same day sends nothing
        t2 = FakeTransport()
        res2 = self.run_it(t2, clock)
        self.assertEqual(len(t2.sent), 0)
        self.assertIn("remain queued", res2.reason)

    def test_rate_limit_pauses_whole_run_and_aborts_after_max(self):
        self.fill(3)
        t = FakeTransport([smtplib.SMTPResponseException(421, b"4.7.0 rate limit exceeded")] * 3)
        clock = FakeClock()
        res = self.run_it(t, clock)
        self.assertEqual(res.exit_code, sender.EXIT_RATE)
        self.assertEqual(clock.sleeps.count(900), 2)
        self.assertEqual(self.store.counts()["dead"], 0)

    def test_rate_limit_then_success(self):
        self.fill(2)
        t = FakeTransport([smtplib.SMTPResponseException(421, b"4.7.0 rate limit")])
        clock = FakeClock()
        res = self.run_it(t, clock)
        self.assertEqual((res.exit_code, res.sent), (0, 2))
        self.assertIn(900, clock.sleeps)

    def test_dropped_connection_reconnects_and_continues(self):
        self.fill(3)
        t = FakeTransport([smtplib.SMTPServerDisconnected("dropped")])
        res = self.run_it(t)
        self.assertEqual((res.exit_code, res.sent), (0, 3))
        self.assertGreaterEqual(t.connects, 2)

    def test_tls_failure_on_reconnect_then_ok(self):
        import ssl
        self.fill(2)
        t = FakeTransport([ConnectionResetError("reset")],
                          connect_script=[None, ssl.SSLCertVerificationError("bad cert"), None])
        res = self.run_it(t)
        self.assertEqual((res.exit_code, res.sent), (0, 2))

    def test_circuit_breaker(self):
        self.fill(10)
        t = FakeTransport([smtplib.SMTPDataError(451, b"4.4.2 timeout")] * 10)
        res = self.run_it(t)
        self.assertEqual(res.exit_code, sender.EXIT_BREAKER)
        self.assertIn("Likely cause", res.reason)
        self.assertEqual(self.store.counts()["pending"], 7)

    def test_daily_cap_stops_cleanly_and_resumes_next_day(self):
        self.cfg.daily_cap = 2
        self.fill(5)
        clock = FakeClock()
        res = self.run_it(FakeTransport(), clock)
        self.assertEqual((res.exit_code, res.sent), (0, 2))
        self.assertIn("daily cap", res.reason)
        self.assertEqual(self.store.counts()["pending"], 3)
        t2 = FakeTransport()
        res2 = self.run_it(t2, clock)
        self.assertEqual(len(t2.sent), 0)
        clock.t += 86400
        res3 = self.run_it(FakeTransport(), clock)
        self.assertEqual(res3.sent, 2)

    def test_orphan_recovered_on_start_and_sent(self):
        self.fill(2)
        self.store.claim_next(1e12)  # simulated crash leaves it in 'sending'
        res = self.run_it(FakeTransport())
        self.assertEqual(res.sent, 2)
        self.assertEqual(self.store.possible_duplicates(), 1)

    def test_only_far_future_retries_exits(self):
        self.fill(1)
        r = self.store.claim_next(1e12)
        self.store.fail_transient(r["campaign_id"], r["email_norm"], "451", 5, 1_800_000_000.0 + 10_000)
        res = self.run_it(FakeTransport())
        self.assertEqual(res.exit_code, 0)
        self.assertIn("earliest is due", res.reason)

    def test_near_future_retry_waits(self):
        self.fill(1)
        r = self.store.claim_next(1e12)
        self.store.fail_transient(r["campaign_id"], r["email_norm"], "451", 5, 1_800_000_000.0 + 60)
        res = self.run_it(FakeTransport())
        self.assertEqual(res.sent, 1)

    def test_stop_flag_leaves_no_sending_rows(self):
        self.fill(5)
        clock = FakeClock()
        t = FakeTransport()
        orig = t.send

        def send(msg, rcpt):
            orig(msg, rcpt)
            clock.stopped = True

        t.send = send
        res = self.run_it(t, clock)
        self.assertEqual(res.exit_code, sender.EXIT_INTERRUPTED)
        self.assertEqual(self.store.counts()["sending"], 0)
        self.assertEqual(res.sent, 1)

    def test_keyboard_interrupt_mid_send_flags_duplicate(self):
        self.fill(1)
        with self.assertRaises(KeyboardInterrupt):
            self.run_it(FakeTransport([KeyboardInterrupt()]))
        self.assertEqual(self.store.counts()["sending"], 0)
        self.assertEqual(self.store.possible_duplicates(), 1)

    def test_dry_run_changes_nothing(self):
        self.fill(3)
        total, errors, n_err, sample = dry_run(self.cfg, self.store, self.cid)
        self.assertEqual((total, n_err), (3, 0))
        self.assertIn("Subject: Hello N0", sample)
        self.assertEqual(self.store.counts()["pending"], 3)


class ClassifierTests(unittest.TestCase):
    def kind(self, exc):
        return classify(exc)[0]

    def test_kinds(self):
        self.assertEqual(self.kind(smtplib.SMTPAuthenticationError(535, b"x")), Kind.AUTH)
        self.assertEqual(self.kind(smtplib.SMTPDataError(530, b"5.7.0 Authentication Required")), Kind.AUTH)
        self.assertEqual(self.kind(smtplib.SMTPDataError(550, b"5.4.5 Daily user sending quota exceeded")), Kind.QUOTA)
        self.assertEqual(self.kind(smtplib.SMTPDataError(554, b"Your account has been suspended")), Kind.ACCOUNT)
        self.assertEqual(self.kind(refused(550, "5.1.1 no such user")), Kind.PERMANENT)
        self.assertEqual(self.kind(smtplib.SMTPDataError(501, b"syntax")), Kind.UNKNOWN)
        self.assertEqual(self.kind(smtplib.SMTPDataError(451, b"try again later")), Kind.TRANSIENT)
        self.assertEqual(self.kind(smtplib.SMTPDataError(421, b"4.7.0 slow down")), Kind.RATE_LIMIT)
        self.assertEqual(self.kind(smtplib.SMTPServerDisconnected("x")), Kind.CONNECTION)
        self.assertEqual(self.kind(TimeoutError()), Kind.CONNECTION)
        self.assertEqual(self.kind(smtplib.SMTPSenderRefused(550, b"denied", "a@b.c")), Kind.ACCOUNT)


if __name__ == "__main__":
    unittest.main()
