"""SQLite-backed persistent queue (WAL). All state changes are committed durably.

States: pending -> sending -> sent | retry_pending | dead ; retry_pending -> sending | dead.
Rows leave `dead` only through the explicit `requeue` action.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

STATUSES = ("pending", "sending", "sent", "retry_pending", "dead")

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  subject_tpl TEXT NOT NULL DEFAULT '',
  body_tpl TEXT NOT NULL DEFAULT '',
  html_tpl TEXT,
  tpl_hash TEXT,
  created_at REAL NOT NULL,
  last_enqueue_at REAL,
  last_enqueue_rejects INTEGER
);
CREATE TABLE IF NOT EXISTS recipients(
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  email_norm TEXT NOT NULL,
  data_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','sending','sent','retry_pending','dead')),
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at REAL NOT NULL DEFAULT 0,
  last_error TEXT,
  possible_duplicate INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'csv',
  source_line INTEGER,
  sent_at REAL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(campaign_id, email_norm)
);
CREATE INDEX IF NOT EXISTS idx_recipients_claim ON recipients(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS daily_counts(
  day TEXT PRIMARY KEY,
  sent INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  campaign_id INTEGER,
  email TEXT,
  event TEXT NOT NULL,
  detail TEXT
);
"""


class StoreError(Exception):
    pass


class TemplateMismatch(StoreError):
    pass


def template_hash(subject: str, body: str, html: str | None, banner: bytes | None = None) -> str:
    banner_hash = hashlib.sha256(banner).hexdigest() if banner else None
    payload = json.dumps([subject, body, html, banner_hash], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(campaigns)")}
        for col, ddl in (("banner", "BLOB"), ("banner_type", "TEXT")):
            if col not in cols:  # migrate queues created before banner support
                self.conn.execute(f"ALTER TABLE campaigns ADD COLUMN {col} {ddl}")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def check_writable(self) -> None:
        with self._tx():
            self.conn.execute("SELECT 1")

    def _event(self, cid, email, event, detail=None, now=None) -> None:
        self.conn.execute(
            "INSERT INTO events(ts,campaign_id,email,event,detail) VALUES(?,?,?,?,?)",
            (now or time.time(), cid, email, event, detail),
        )

    def log_event(self, cid, email, event, detail=None, now=None) -> None:
        with self._tx():
            self._event(cid, email, event, detail, now)

    # ---- campaigns -------------------------------------------------------
    def campaign_by_name(self, name: str):
        return self.conn.execute("SELECT * FROM campaigns WHERE name=?", (name,)).fetchone()

    def campaign(self, cid: int):
        return self.conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()

    def campaigns(self):
        return self.conn.execute("SELECT * FROM campaigns ORDER BY id").fetchall()

    def _ensure_campaign_untemplated(self, name: str, now: float) -> int:
        self.conn.execute(
            "INSERT OR IGNORE INTO campaigns(name, created_at) VALUES(?,?)", (name, now)
        )
        return self.campaign_by_name(name)["id"]

    def get_or_create_campaign(
        self, name: str, subject: str, body: str, html: str | None,
        allow_change: bool = False, now: float | None = None,
        banner: bytes | None = None, banner_type: str | None = None,
    ) -> tuple[int, str]:
        """Return (campaign_id, 'created'|'existing'|'adopted'|'changed').

        Raises TemplateMismatch when the campaign has a different template
        snapshot and allow_change is False. Nothing is modified on refusal.
        """
        now = now or time.time()
        h = template_hash(subject, body, html, banner)
        with self._tx():
            row = self.campaign_by_name(name)
            if row is None:
                cur = self.conn.execute(
                    "INSERT INTO campaigns(name,subject_tpl,body_tpl,html_tpl,tpl_hash,created_at,banner,banner_type)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (name, subject, body, html, h, now, banner, banner_type),
                )
                return cur.lastrowid, "created"
            upd = ("UPDATE campaigns SET subject_tpl=?,body_tpl=?,html_tpl=?,tpl_hash=?,banner=?,banner_type=?"
                   " WHERE id=?", (subject, body, html, h, banner, banner_type, row["id"]))
            if row["tpl_hash"] is None:
                self.conn.execute(*upd)
                return row["id"], "adopted"
            if row["tpl_hash"] == h:
                return row["id"], "existing"
            if not allow_change:
                raise TemplateMismatch(
                    f"campaign {name!r} already has a different template snapshot"
                )
            self.conn.execute(*upd)
            self._event(row["id"], None, "template_changed", f"{row['tpl_hash'][:8]} -> {h[:8]}", now)
            return row["id"], "changed"

    def ensure_campaign_for_import(self, name: str, now: float | None = None) -> int:
        with self._tx():
            return self._ensure_campaign_untemplated(name, now or time.time())

    # ---- enqueue ---------------------------------------------------------
    def enqueue(self, cid: int, rows, rejects: int = 0, now: float | None = None) -> dict:
        """rows: iterable of (email_norm, data_dict, source_line). Never touches existing rows."""
        now = now or time.time()
        new = 0
        existing: dict[str, int] = {}
        with self._tx():
            for email, data, line in rows:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO recipients(campaign_id,email_norm,data_json,source_line,updated_at)"
                    " VALUES(?,?,?,?,?)",
                    (cid, email, json.dumps(data, ensure_ascii=False), line, now),
                )
                if cur.rowcount:
                    new += 1
                else:
                    st = self.conn.execute(
                        "SELECT status FROM recipients WHERE campaign_id=? AND email_norm=?",
                        (cid, email),
                    ).fetchone()["status"]
                    existing[st] = existing.get(st, 0) + 1
            self.conn.execute(
                "UPDATE campaigns SET last_enqueue_at=?, last_enqueue_rejects=? WHERE id=?",
                (now, rejects, cid),
            )
            self._event(cid, None, "enqueue", f"new={new} existing={sum(existing.values())} rejects={rejects}", now)
        return {"new": new, "existing": existing}

    def import_history(self, cid: int, emails, now: float | None = None) -> dict:
        now = now or time.time()
        added = updated = already = 0
        with self._tx():
            for email in emails:
                row = self.conn.execute(
                    "SELECT status FROM recipients WHERE campaign_id=? AND email_norm=?", (cid, email)
                ).fetchone()
                if row is None:
                    self.conn.execute(
                        "INSERT INTO recipients(campaign_id,email_norm,data_json,status,source,sent_at,updated_at)"
                        " VALUES(?,?,'{}','sent','imported',?,?)",
                        (cid, email, now, now),
                    )
                    added += 1
                elif row["status"] in ("pending", "retry_pending", "dead"):
                    self.conn.execute(
                        "UPDATE recipients SET status='sent', source='imported', sent_at=?, updated_at=?"
                        " WHERE campaign_id=? AND email_norm=?",
                        (now, now, cid, email),
                    )
                    updated += 1
                else:
                    already += 1
            self._event(cid, None, "import_history", f"added={added} updated={updated} already={already}", now)
        return {"added": added, "updated": updated, "already": already}

    # ---- claiming and transitions ---------------------------------------
    def claim_next(self, now: float, cid: int | None = None):
        """Atomically move one eligible row to `sending` (single UPDATE ... RETURNING)."""
        sql = (
            "UPDATE recipients SET status='sending', updated_at=? WHERE rowid=("
            " SELECT rowid FROM recipients WHERE status IN ('pending','retry_pending')"
            " AND next_attempt_at<=?"
        )
        params: list = [now, now]
        if cid is not None:
            sql += " AND campaign_id=?"
            params.append(cid)
        sql += " ORDER BY next_attempt_at, rowid LIMIT 1)"
        sql += " RETURNING campaign_id, email_norm, data_json, attempts"
        rows = self.conn.execute(sql, params).fetchall()
        return rows[0] if rows else None

    def _require_sending(self, cur: sqlite3.Cursor, cid, email) -> None:
        if cur.rowcount != 1:
            raise StoreError(f"row {email} (campaign {cid}) was not in 'sending' state")

    def mark_sent(self, cid: int, email: str, day: str, now: float | None = None) -> None:
        now = now or time.time()
        with self._tx():
            cur = self.conn.execute(
                "UPDATE recipients SET status='sent', attempts=attempts+1, last_error=NULL,"
                " sent_at=?, updated_at=? WHERE campaign_id=? AND email_norm=? AND status='sending'",
                (now, now, cid, email),
            )
            self._require_sending(cur, cid, email)
            self.conn.execute(
                "INSERT INTO daily_counts(day,sent) VALUES(?,1)"
                " ON CONFLICT(day) DO UPDATE SET sent=sent+1",
                (day,),
            )
            self._event(cid, email, "sent", None, now)

    def mark_dead(self, cid: int, email: str, error: str, now: float | None = None) -> None:
        now = now or time.time()
        with self._tx():
            cur = self.conn.execute(
                "UPDATE recipients SET status='dead', attempts=attempts+1, last_error=?, updated_at=?"
                " WHERE campaign_id=? AND email_norm=? AND status='sending'",
                (error, now, cid, email),
            )
            self._require_sending(cur, cid, email)
            self._event(cid, email, "dead", error, now)

    def fail_transient(
        self, cid: int, email: str, error: str, max_attempts: int, next_at: float,
        now: float | None = None,
    ) -> str:
        now = now or time.time()
        with self._tx():
            row = self.conn.execute(
                "SELECT attempts FROM recipients WHERE campaign_id=? AND email_norm=? AND status='sending'",
                (cid, email),
            ).fetchone()
            if row is None:
                raise StoreError(f"row {email} (campaign {cid}) was not in 'sending' state")
            attempts = row["attempts"] + 1
            status = "dead" if attempts >= max_attempts else "retry_pending"
            self.conn.execute(
                "UPDATE recipients SET status=?, attempts=?, last_error=?, next_attempt_at=?, updated_at=?"
                " WHERE campaign_id=? AND email_norm=?",
                (status, attempts, error, 0 if status == "dead" else next_at, now, cid, email),
            )
            self._event(cid, email, status, error, now)
            return status

    def release(
        self, cid: int, email: str, error: str, possible_duplicate: bool = False,
        now: float | None = None,
    ) -> None:
        """Return an in-flight row to retry_pending without consuming an attempt."""
        now = now or time.time()
        with self._tx():
            cur = self.conn.execute(
                "UPDATE recipients SET status='retry_pending', last_error=?, next_attempt_at=0,"
                " possible_duplicate=possible_duplicate OR ?, updated_at=?"
                " WHERE campaign_id=? AND email_norm=? AND status='sending'",
                (error, int(possible_duplicate), now, cid, email),
            )
            self._require_sending(cur, cid, email)
            self._event(cid, email, "released", error, now)

    def recover_orphans(self, now: float | None = None) -> list[tuple[int, str]]:
        now = now or time.time()
        with self._tx():
            rows = self.conn.execute(
                "UPDATE recipients SET status='retry_pending', possible_duplicate=1, next_attempt_at=0,"
                " last_error='recovered after crash while sending', updated_at=?"
                " WHERE status='sending' RETURNING campaign_id, email_norm",
                (now,),
            ).fetchall()
            for r in rows:
                self._event(r["campaign_id"], r["email_norm"], "recovered", "orphaned in 'sending'", now)
        return [(r["campaign_id"], r["email_norm"]) for r in rows]

    def requeue(self, cid: int, emails: list[str] | None, now: float | None = None) -> int:
        """Explicit operator action: dead -> retry_pending. emails=None means all dead rows."""
        now = now or time.time()
        with self._tx():
            if emails is None:
                cur = self.conn.execute(
                    "UPDATE recipients SET status='retry_pending', attempts=0, next_attempt_at=0,"
                    " updated_at=? WHERE campaign_id=? AND status='dead'",
                    (now, cid),
                )
                n = cur.rowcount
                self._event(cid, None, "requeue", f"all dead rows ({n})", now)
            else:
                n = 0
                for e in emails:
                    cur = self.conn.execute(
                        "UPDATE recipients SET status='retry_pending', attempts=0, next_attempt_at=0,"
                        " updated_at=? WHERE campaign_id=? AND email_norm=? AND status='dead'",
                        (now, cid, e),
                    )
                    n += cur.rowcount
                    if cur.rowcount:
                        self._event(cid, e, "requeue", "explicit", now)
        return n

    # ---- daily quota -----------------------------------------------------
    def daily_state(self, day: str) -> tuple[int, bool]:
        row = self.conn.execute("SELECT sent, blocked FROM daily_counts WHERE day=?", (day,)).fetchone()
        return (row["sent"], bool(row["blocked"])) if row else (0, False)

    def block_day(self, day: str) -> None:
        with self._tx():
            self.conn.execute(
                "INSERT INTO daily_counts(day,sent,blocked) VALUES(?,0,1)"
                " ON CONFLICT(day) DO UPDATE SET blocked=1",
                (day,),
            )

    # ---- queries ---------------------------------------------------------
    def counts(self, cid: int | None = None) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        sql = "SELECT status, COUNT(*) n FROM recipients"
        params: tuple = ()
        if cid is not None:
            sql += " WHERE campaign_id=?"
            params = (cid,)
        for r in self.conn.execute(sql + " GROUP BY status", params):
            out[r["status"]] = r["n"]
        return out

    def dead_list(self, cid: int | None = None):
        sql = ("SELECT c.name campaign, r.email_norm, r.attempts, r.last_error FROM recipients r"
               " JOIN campaigns c ON c.id=r.campaign_id WHERE r.status='dead'")
        params: tuple = ()
        if cid is not None:
            sql += " AND r.campaign_id=?"
            params = (cid,)
        return self.conn.execute(sql + " ORDER BY r.rowid", params).fetchall()

    def possible_duplicates(self, cid: int | None = None) -> int:
        sql = "SELECT COUNT(*) n FROM recipients WHERE possible_duplicate=1"
        params: tuple = ()
        if cid is not None:
            sql += " AND campaign_id=?"
            params = (cid,)
        return self.conn.execute(sql, params).fetchone()["n"]

    def earliest_retry(self, cid: int | None = None) -> float | None:
        sql = "SELECT MIN(next_attempt_at) t FROM recipients WHERE status='retry_pending'"
        params: tuple = ()
        if cid is not None:
            sql += " AND campaign_id=?"
            params = (cid,)
        return self.conn.execute(sql, params).fetchone()["t"]

    def unsent_rows(self, cid: int | None = None):
        sql = ("SELECT r.campaign_id, r.email_norm, r.data_json, r.source_line FROM recipients r"
               " WHERE r.status IN ('pending','retry_pending')")
        params: tuple = ()
        if cid is not None:
            sql += " AND r.campaign_id=?"
            params = (cid,)
        return self.conn.execute(sql + " ORDER BY r.rowid", params).fetchall()
