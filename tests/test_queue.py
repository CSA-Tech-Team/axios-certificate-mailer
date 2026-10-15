import tempfile
import threading
import unittest
from pathlib import Path

from mailqueue.lock import FileLock, LockHeld
from mailqueue.store import Store, TemplateMismatch


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = Path(self.dir) / "q.db"
        self.store = Store(self.db)
        self.addCleanup(self.store.close)
        self.cid, _ = self.store.get_or_create_campaign("march", "S {{name}}", "B", None)

    def add(self, n, cid=None):
        return self.store.enqueue(cid or self.cid, [(f"u{i}@x.com", {"name": str(i)}, i + 2) for i in range(n)])

    def test_idempotent_enqueue(self):
        self.assertEqual(self.add(5)["new"], 5)
        again = self.add(5)
        self.assertEqual(again["new"], 0)
        self.assertEqual(again["existing"], {"pending": 5})
        self.assertEqual(self.add(8)["new"], 3)
        self.assertEqual(self.store.counts()["pending"], 8)

    def test_enqueue_never_changes_sent(self):
        self.add(1)
        row = self.store.claim_next(1e12)
        self.store.mark_sent(row["campaign_id"], row["email_norm"], "2026-01-01")
        res = self.store.enqueue(self.cid, [("u0@x.com", {"name": "CHANGED"}, 2)])
        self.assertEqual(res["existing"], {"sent": 1})
        self.assertEqual(self.store.counts()["sent"], 1)

    def test_same_email_in_second_campaign(self):
        self.add(1)
        c2, _ = self.store.get_or_create_campaign("april", "S", "B", None)
        self.assertEqual(self.add(1, c2)["new"], 1)

    def test_template_snapshot_mismatch(self):
        with self.assertRaises(TemplateMismatch):
            self.store.get_or_create_campaign("march", "different", "B", None)
        self.assertEqual(self.store.campaign(self.cid)["subject_tpl"], "S {{name}}")
        _, how = self.store.get_or_create_campaign("march", "different", "B", None, allow_change=True)
        self.assertEqual(how, "changed")

    def test_atomic_claim_no_double_claim_across_connections(self):
        self.add(200)
        claimed, lock = [], threading.Lock()

        def worker():
            s = Store(self.db)
            try:
                while True:
                    r = s.claim_next(1e12)
                    if r is None:
                        return
                    with lock:
                        claimed.append(r["email_norm"])
            finally:
                s.close()

        threads = [threading.Thread(target=worker) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(claimed), 200)
        self.assertEqual(len(set(claimed)), 200)

    def test_orphan_recovery(self):
        self.add(2)
        self.store.claim_next(1e12)
        rec = self.store.recover_orphans()
        self.assertEqual(len(rec), 1)
        self.assertEqual(self.store.counts()["retry_pending"], 1)
        self.assertEqual(self.store.possible_duplicates(), 1)

    def test_transient_to_dead_after_max(self):
        self.add(1)
        for i in range(3):
            r = self.store.claim_next(1e12)
            st = self.store.fail_transient(r["campaign_id"], r["email_norm"], "451", 3, 0)
        self.assertEqual(st, "dead")
        self.assertEqual(self.store.counts()["dead"], 1)

    def test_dead_stays_dead_until_requeue(self):
        self.add(1)
        r = self.store.claim_next(1e12)
        self.store.mark_dead(r["campaign_id"], r["email_norm"], "550")
        self.store.recover_orphans()
        self.add(1)
        self.assertEqual(self.store.counts()["dead"], 1)
        self.assertIsNone(self.store.claim_next(1e12))
        self.assertEqual(self.store.requeue(self.cid, None), 1)
        self.assertEqual(self.store.counts()["retry_pending"], 1)

    def test_release_does_not_consume_attempt(self):
        self.add(1)
        r = self.store.claim_next(1e12)
        self.store.release(r["campaign_id"], r["email_norm"], "535")
        r2 = self.store.claim_next(1e12)
        self.assertEqual(r2["attempts"], 0)

    def test_enqueue_while_other_connection_claims(self):
        self.add(3)
        s2 = Store(self.db)
        self.addCleanup(s2.close)
        r = self.store.claim_next(1e12)
        s2.enqueue(self.cid, [("new@x.com", {}, 9)])
        self.assertEqual(self.store.counts()["sending"], 1)
        self.assertEqual(self.store.counts()["pending"], 3)

    def test_import_history(self):
        self.add(2)
        res = self.store.import_history(self.cid, ["u0@x.com", "old@x.com"])
        self.assertEqual((res["added"], res["updated"]), (1, 1))
        self.assertEqual(self.store.counts()["sent"], 2)

    def test_lock_exclusive_and_released(self):
        p = Path(self.dir) / "l.lock"
        a = FileLock(p)
        a.acquire()
        with self.assertRaises(LockHeld):
            FileLock(p).acquire()
        a.release()
        b = FileLock(p)
        b.acquire()
        b.release()


if __name__ == "__main__":
    unittest.main()
