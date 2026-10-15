"""The send loop: pacing, quota, failure handling, circuit breaker, graceful stop."""
from __future__ import annotations

import json
import logging
import random
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from .config import Config
from .emailutil import normalize_email
from .message import build_message
from .smtpclient import Kind, classify
from .store import Store
from .template import RenderError

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_DB = 4
EXIT_ACCOUNT = 5
EXIT_UNKNOWN_5XX = 6
EXIT_RATE = 7
EXIT_BREAKER = 8
EXIT_LOCKED = 9
EXIT_INTERRUPTED = 130

_CAUSE = {
    Kind.CONNECTION: "network/TLS connectivity problem or smtp.gmail.com unreachable",
    Kind.TRANSIENT: "repeated temporary errors from the provider (throttling or an outage)",
}


class RealClock:
    def __init__(self):
        self.stop = threading.Event()

    @property
    def stopped(self) -> bool:
        return self.stop.is_set()

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        self.stop.wait(max(0.0, seconds))


@dataclass
class RunResult:
    exit_code: int
    reason: str
    sent: int = 0
    dead: int = 0
    retried: int = 0


class TokenBucket:
    """Fixed-rate pacing (capacity 1): consecutive sends are >= 1/rate seconds apart."""

    def __init__(self, rate_per_sec: float, clock):
        self.interval = 1.0 / rate_per_sec
        self.clock = clock
        self.next = 0.0

    def wait(self) -> None:
        t = self.clock.now()
        if t < self.next:
            self.clock.sleep(self.next - t)
            t = max(t, self.next)
        self.next = t + self.interval


def backoff_delay(cfg: Config, attempts: int, rng: random.Random) -> float:
    return min(cfg.backoff_cap_s, cfg.backoff_base_s * 2 ** max(0, attempts - 1)) * rng.uniform(0.5, 1.5)


class Runner:
    def __init__(self, cfg: Config, store: Store, transport, log: logging.Logger | None = None,
                 clock=None, rng: random.Random | None = None,
                 campaign_id: int | None = None, limit: int | None = None):
        self.cfg = cfg
        self.store = store
        self.transport = transport
        self.log = log or logging.getLogger("mailq")
        self.clock = clock or RealClock()
        self.rng = rng or random.Random()
        self.campaign_id = campaign_id
        self.limit = limit
        self.bucket = TokenBucket(cfg.rate_per_sec, self.clock)
        self._campaigns: dict[int, object] = {}
        self.res = RunResult(EXIT_OK, "")

    # ---- helpers ---------------------------------------------------------
    def _day(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.cfg.tz()).strftime("%Y-%m-%d")

    def _iso(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.cfg.tz()).isoformat(timespec="seconds")

    def _campaign(self, cid: int):
        if cid not in self._campaigns:
            self._campaigns[cid] = self.store.campaign(cid)
        return self._campaigns[cid]

    def _finish(self, code: int, reason: str) -> RunResult:
        self.res.exit_code, self.res.reason = code, reason
        (self.log.info if code == EXIT_OK else self.log.error)("run ended: %s", reason)
        return self.res

    def _ensure_connected(self):
        """Connect (with bounded retries). Returns None on success or (Kind, text)."""
        if self.transport.connected:
            return None
        last = (Kind.CONNECTION, "could not connect")
        for i in range(self.cfg.reconnect_attempts):
            try:
                self.transport.connect()
                self.log.info("connected to SMTP server")
                return None
            except Exception as exc:
                kind, text = classify(exc)
                if kind == Kind.AUTH:
                    return kind, text
                last = (kind, text)
                self.log.warning("connect failed (%s): %s", kind.value, text)
                if i < self.cfg.reconnect_attempts - 1:
                    self.clock.sleep(self.cfg.reconnect_delay_s * 2 ** i)
                    if self.clock.stopped:
                        break
        return last

    def _auth_result(self, text: str) -> RunResult:
        return self._finish(
            EXIT_AUTH,
            f"authentication failed ({text}). The app password may be revoked or expired; "
            "generate a new one and set MAILQ_SMTP_PASSWORD. No further attempts were made.",
        )

    # ---- main ------------------------------------------------------------
    def run(self) -> RunResult:
        try:
            return self._run()
        except sqlite3.Error as exc:
            return self._finish(
                EXIT_DB,
                f"queue database error: {exc}. Any row left in 'sending' will be recovered "
                "(flagged possible_duplicate) on the next start.",
            )
        finally:
            self.transport.close()

    def _run(self) -> RunResult:
        cfg, store, log, clock = self.cfg, self.store, self.log, self.clock
        # preflight: credentials, DB writable, login — before any row is claimed
        if not cfg.smtp_password:
            return self._finish(EXIT_AUTH, "SMTP password not configured (set MAILQ_SMTP_PASSWORD)")
        store.check_writable()
        try:
            self.transport.connect()
            log.info("preflight login ok as %s", cfg.smtp_user)
        except Exception as exc:
            kind, text = classify(exc)
            if kind == Kind.AUTH:
                return self._auth_result(text)
            log.warning("preflight connect failed (%s): %s; will retry", kind.value, text)
        for cid, email in store.recover_orphans(clock.now()):
            log.warning("recovered orphaned row %s (campaign %s) -> retry_pending, possible_duplicate", email, cid)

        consecutive = 0
        rate_pauses = 0
        while True:
            if clock.stopped:
                return self._finish(EXIT_INTERRUPTED, "interrupted; no row left in 'sending'")
            if self.limit is not None and self.res.sent >= self.limit:
                return self._finish(EXIT_OK, f"--limit {self.limit} reached")
            now = clock.now()
            day = self._day(now)
            sent_today, blocked = store.daily_state(day)
            if blocked or sent_today >= cfg.daily_cap:
                remaining = sum(store.counts(self.campaign_id)[s] for s in ("pending", "retry_pending"))
                why = "provider quota reached" if blocked and sent_today < cfg.daily_cap else "daily cap reached"
                return self._finish(
                    EXIT_OK,
                    f"{why} ({sent_today}/{cfg.daily_cap} sent on {day}); "
                    f"{remaining} recipients remain queued for the next run",
                )
            err = self._ensure_connected()
            if err:
                kind, text = err
                if kind == Kind.AUTH:
                    return self._auth_result(text)
                consecutive += 1
                if consecutive >= cfg.breaker_n:
                    return self._breaker(consecutive, kind, text)
                clock.sleep(min(cfg.backoff_cap_s, cfg.backoff_base_s))
                continue
            self.bucket.wait()
            if clock.stopped:
                continue
            row = store.claim_next(clock.now(), self.campaign_id)
            if row is None:
                nxt = store.earliest_retry(self.campaign_id)
                if nxt is None:
                    return self._finish(EXIT_OK, "queue drained: nothing left to send")
                wait = nxt - clock.now()
                if wait <= cfg.retry_wait_limit_s:
                    log.info("only retries remain; waiting %.0fs for the next one", max(wait, 0))
                    clock.sleep(wait)
                    continue
                return self._finish(EXIT_OK, f"only retries remain; earliest is due at {self._iso(nxt)}")
            cid, email, attempts = row["campaign_id"], row["email_norm"], row["attempts"]
            try:
                msg = build_message(cfg, self._campaign(cid), json.loads(row["data_json"]), email)
            except RenderError as exc:
                store.mark_dead(cid, email, f"render error: {exc}")
                self.res.dead += 1
                log.error("DEAD %s (campaign %s): render error: %s", email, cid, exc)
                continue
            log.info("ATTEMPT %s (campaign %s, attempt %d)", email, cid, attempts + 1)
            try:
                self.transport.send(msg, email)
            except KeyboardInterrupt:
                store.release(cid, email, "interrupted mid-send; outcome unknown", possible_duplicate=True)
                raise
            except Exception as exc:
                kind, text = classify(exc)
                if kind == Kind.TRANSIENT and not hasattr(exc, "smtp_code") and not isinstance(exc, OSError):
                    log.exception("unexpected error while sending to %s", email)
                out = self._on_failure(kind, text, cid, email, attempts, day)
                if isinstance(out, RunResult):
                    return out
                if out == "rate":
                    rate_pauses += 1
                    if rate_pauses >= cfg.max_rate_pauses:
                        return self._finish(
                            EXIT_RATE,
                            f"provider throttled the run {rate_pauses} times; stopping. Try again later.",
                        )
                    self.transport.close()
                    log.warning("RATE LIMIT: pausing all sending for %.0fs (pause %d/%d)",
                                cfg.cooldown_s, rate_pauses, cfg.max_rate_pauses)
                    clock.sleep(cfg.cooldown_s)
                elif out == "counted":
                    consecutive += 1
                    if consecutive >= cfg.breaker_n:
                        return self._breaker(consecutive, kind, text)
                continue
            # accepted by the server: record it durably right away
            store.mark_sent(cid, email, day)
            consecutive = 0
            self.res.sent += 1
            log.info("SENT %s (campaign %s)", email, cid)

    def _on_failure(self, kind: Kind, text: str, cid: int, email: str, attempts: int, day: str):
        """Returns a RunResult to stop, or 'rate' / 'counted' / 'ignored'."""
        cfg, store, log = self.cfg, self.store, self.log
        if kind == Kind.AUTH:
            store.release(cid, email, text)
            return self._auth_result(text)
        if kind == Kind.QUOTA:
            store.release(cid, email, text)
            store.block_day(day)
            return self._finish(
                EXIT_OK,
                f"provider daily sending quota exceeded ({text}); stopping. Remaining recipients stay queued.",
            )
        if kind == Kind.ACCOUNT:
            store.release(cid, email, text)
            return self._finish(
                EXIT_ACCOUNT,
                f"account-level rejection ({text}); the sending account may be blocked or restricted. "
                "Check the Google Workspace admin console before resuming.",
            )
        if kind == Kind.UNKNOWN:
            store.release(cid, email, text)
            return self._finish(
                EXIT_UNKNOWN_5XX,
                f"unrecognized SMTP error ({text}); halting rather than marking recipients dead.",
            )
        if kind == Kind.RATE_LIMIT:
            store.release(cid, email, text)
            log.warning("RATE-LIMITED on %s: %s", email, text)
            return "rate"
        if kind == Kind.PERMANENT:
            store.mark_dead(cid, email, text)
            self.res.dead += 1
            log.error("DEAD %s (campaign %s): %s", email, cid, text)
            return "ignored"
        # CONNECTION / TRANSIENT
        if kind == Kind.CONNECTION:
            self.transport.close()
        delay = backoff_delay(cfg, attempts + 1, self.rng)
        status = store.fail_transient(cid, email, text, cfg.max_attempts, self.clock.now() + delay)
        self.res.retried += 1
        if status == "dead":
            self.res.dead += 1
            log.error("DEAD %s (campaign %s): max attempts reached; last error: %s", email, cid, text)
        else:
            log.warning("RETRY %s (campaign %s) in %.0fs after %s error: %s", email, cid, delay, kind.value, text)
        return "counted"

    def _breaker(self, n: int, kind: Kind, text: str) -> RunResult:
        return self._finish(
            EXIT_BREAKER,
            f"circuit breaker: {n} consecutive failures. Likely cause: "
            f"{_CAUSE.get(kind, kind.value)}. Last error: {text}. Remaining rows are unchanged.",
        )


# ---- dry run and test send ---------------------------------------------------
def dry_run(cfg: Config, store: Store, campaign_id: int | None, max_errors: int = 20):
    """Render every unsent row; send nothing and change nothing."""
    total, errors, sample = 0, [], None
    campaigns: dict[int, object] = {}
    for row in store.unsent_rows(campaign_id):
        total += 1
        cid = row["campaign_id"]
        campaigns.setdefault(cid, store.campaign(cid))
        try:
            msg = build_message(cfg, campaigns[cid], json.loads(row["data_json"]), row["email_norm"])
        except RenderError as exc:
            errors.append(f"{row['email_norm']}: {exc}")
            continue
        if sample is None:
            body = msg.get_body(preferencelist=("plain",))
            sample = f"To: {msg['To']}\nSubject: {msg['Subject']}\n\n{body.get_content() if body else ''}"
    return total, errors[:max_errors], len(errors), sample


def send_test(cfg: Config, store: Store, transport, campaign_id: int | None, to_addr: str) -> str:
    """Send one rendered sample to to_addr. Queue state and counters are untouched."""
    addr, err = normalize_email(to_addr)
    if err:
        raise ValueError(f"invalid --test-to address: {err}")
    rows = store.unsent_rows(campaign_id)
    if not rows:
        raise ValueError("no unsent rows to build a sample from")
    row = rows[0]
    msg = build_message(cfg, store.campaign(row["campaign_id"]), json.loads(row["data_json"]), addr)
    transport.connect()
    try:
        transport.send(msg, addr)
    finally:
        transport.close()
    return f"sent test message (data of {row['email_norm']}) to {addr}"
