## Context

The repo currently contains no implementation, only OpenSpec scaffolding. Campaigns of 1,500–2,000 recipients go out from one Google Workspace account (SMTP with an app password) subject to ~2,000 messages/day. The operator runs the tool from a single machine. Requirements are in `specs/bulk-email-sending/spec.md`.

## Goals / Non-Goals

**Goals:**
- Crash-safe, resumable, idempotent bulk sending with one authoritative history.
- Fail early on bad input (before any send) and never send incomplete content.
- Respect the daily cap and avoid burst throttling.

**Non-Goals:**
- Multi-account sending, distributed workers, or a web UI.
- Open/click tracking, bounce processing via IMAP, unsubscribe management.
- Automatic resurrection of `dead` rows.

## Decisions

- **Language/stack: Python 3 with stdlib only** (`csv`, `sqlite3`, `smtplib`, `email`, `argparse`). Alternative: Node. Chosen for a no-dependency, single-file-deployable tool. Confirm if the operator prefers another stack.
- **Queue store: SQLite (WAL mode)**. Transactions give crash safety and atomic claiming without a server. Alternative: JSON/CSV state file (not atomic, corrupts on crash).
- **Schema**: `campaigns(id PK, name UNIQUE, subject_tpl, body_tpl, tpl_hash, created_at)`, `recipients(campaign_id, email_norm, PRIMARY KEY(campaign_id, email_norm), data_json, status, attempts, next_attempt_at, last_error, possible_duplicate, source, source_line, updated_at)`, `daily_counts(date PK, sent)`, `events` table plus a rotating text log file. Templates are snapshotted per campaign so later edits to the template file cannot change a queued campaign (Alternative: read the live file at send time; rejected because it silently mixes versions).
- **Atomic claim**: `UPDATE ... SET status='sending' WHERE rowid = (SELECT ... WHERE status IN ('pending','retry_pending') AND next_attempt_at <= now ORDER BY ... LIMIT 1) RETURNING` inside `BEGIN IMMEDIATE`. Plus an OS-level lock file so only one `send` runs per queue. Alternative: rely on lock only; claim-level atomicity is kept as defense in depth.
- **Orphan recovery**: at `send` startup (after acquiring the lock) `UPDATE ... SET status='retry_pending' WHERE status='sending'`. Trade-off: a crash after SMTP accept but before commit could cause one duplicate; accepted and logged as "recovered" since losing a recipient is worse for this use case. To narrow the window, `sent` is committed immediately after the SMTP response.
- **Enqueue vs. send concurrency**: enqueue uses `INSERT OR IGNORE` in short transactions; SQLite WAL lets the sender keep reading/claiming. Enqueue never updates existing rows.
- **Identity**: `email_norm` = trim, NFC, lowercase after validation. IDN domains are normalized to IDNA form for the key. Validation uses a conservative regex plus IDNA/UTF-8 checks; SMTPUTF8 is used when the local part is non-ASCII.
- **Duplicates**: first valid occurrence wins (rejected rows do not count); later occurrences go to `rejects.csv` as "duplicate of line N".
- **Templates are Handlebars `.hbs` files.** No Handlebars engine exists in the Python standard library and a third-party port would add a dependency, so `template.py` implements the subset that personalization needs: `{{x}}` (HTML-escaped), `{{{x}}}`/`{{&x}}` (raw), `{{[x y]}}`, and comments. Block helpers, partials and helper calls are rejected at enqueue with a clear error (the alternative, silently rendering them as text, would send broken mail). Tags are matched to a lowercase/trimmed column map before any DB writes. The `.hbs` file is the HTML body; a plain-text alternative is derived from the rendered HTML (stdlib `HTMLParser`) unless a `--text-template` `.hbs` is given. Subject is a plain string with the same tags and no escaping.
- **Failure classification** (order matters): (1) auth: `SMTPAuthenticationError`, 530/534/535 -> stop run; (2) account-level: 5xx with enhanced codes/text for quota (5.4.5), account block or policy (554 blocking) -> stop run, set today's counter to cap, row returned unconsumed; (3) rate-limit: 421 or 4.x.x rate text -> global cool-down (default 15 min, abort after 3); (4) transient/connection: other 4xx, `OSError`, `SMTPServerDisconnected`, timeouts, `ssl.SSLError` -> retry with backoff; (5) permanent recipient-level: 550/551/553/554 for that address (5.1.x) -> dead. Unrecognized 5xx defaults to a halt-and-report, not dead, until classified. Auth, account-level and rate-limit do not consume an attempt.
- **Backoff**: `min(cap, base * 2^attempts) * uniform(0.5, 1.5)`, stored as `next_attempt_at`.
- **Pacing**: simple token bucket (configurable rate, default about 1 message/2s, i.e. 1,800 in about 1 hour). Rate-limit hit sets a global pause.
- **Daily cap**: persisted `daily_counts` keyed by date in a configured timezone (Google's quota window is not calendar-midnight local; default cap 1,800 leaves margin).
- **Circuit breaker**: counts consecutive transient/connection failures only; permanent recipient rejects are ignored; reset on success; trips at N.
- **Header safety**: build messages with `email.message.EmailMessage` (structured headers); reject CR/LF in any value substituted into a header. Email field must be a single bare address (no separators, display names, control chars, >254 chars).
- **Multi-line CSV**: use `csv.reader` and report both record number and `reader.line_num` start; per-run timestamped `rejects-<ts>.csv`.
- **Lock**: OS-level lock (`msvcrt.locking` on Windows / `fcntl.flock` elsewhere) on a lock file; released on process death, so no stale PID handling. Graceful SIGINT/SIGTERM returns the in-flight row.
- **Preflight and dry-run**: login and DB writability check before claiming rows; `--dry-run` renders all messages without sending; `--test-to` sends one sample.
- **History import and requeue**: `import-history` loads prior recipients as `sent` (source `imported`); `requeue` is the only way to leave `dead`.
- **Retry wait**: if only future retries remain, wait when within 30 min, else exit 0 reporting earliest time.
- **Config**: env vars / config file for SMTP credentials (never logged or committed), cap, rate, thresholds, max attempts.
- **Legacy script**: none exists in this repo; the retirement requirement is covered by documenting the tool as the only supported path and removing/stubbing any script found on the operator's machine.

## Risks / Trade-offs

- [Duplicate send after crash between SMTP accept and DB commit] → commit `sent` immediately; accept rare duplicate over loss.
- [Google's real limit differs from the calendar-day cap] → default cap below limit; configurable.
- [App password revoked mid-run] → auth detection halts run without burning attempts.
- [Sensitive data in the queue DB and logs] → keep DB/logs out of version control; never log credentials.
- [Provider quota shared with other senders on the account, so local counter can under-count] → server quota error halts the run and pins the counter to the cap.
- [SQLite on OneDrive-synced folder can corrupt or lock] → the project lives under OneDrive; store the DB/lock outside the synced folder or exclude it from sync (configurable path).

## Open Questions

- Preferred implementation language (Python assumed)?
- Timezone for the daily counter (IST assumed)?
- Attachments, HTML vs plain-text body, From display name and unsubscribe header are not specified; assume plain text plus optional HTML body, no attachments, unless told otherwise.
