## 1. Project Setup

- [x] 1.1 Create package/CLI skeleton with `enqueue`, `send`, `status`, `requeue`, `import-history` subcommands
- [x] 1.2 Implement config loading (SMTP creds from env/config, daily cap 1,800, rate, max attempts, breaker N, rate-limit cool-down and max pauses, retry wait limit, CSV threshold 5,000, timezone, delimiter, data directory)
- [x] 1.3 Implement timestamped logging to rotating file and console; never log credentials

## 2. CSV Ingestion

- [x] 2.1 Read CSV as strict UTF-8 (tolerate BOM); clear error on decode failure
- [x] 2.2 Reject header-only CSV and all-rows-rejected runs; skip trailing and interior blank lines
- [x] 2.3 Validate header: duplicates (case-insensitive, trimmed), empty names, leading/trailing whitespace, naming offenders
- [x] 2.4 Resolve email column case-insensitively; error if missing
- [x] 2.5 Configurable delimiter with single-column `;`/tab detection error; trim cell values; multi-line quoted fields
- [x] 2.6 Row-count threshold confirmation (prompt, `--yes`, refuse when non-interactive)
- [x] 2.7 Reject wrong field-count rows to timestamped UTF-8 `rejects` file with record number, physical line, reason, original values

## 3. Recipient and Template Validation

- [x] 3.1 Email validation supporting Unicode/IDN; reject empty/malformed, multiple addresses, display-name form, control chars, >254 chars
- [x] 3.2 Email normalization (trim, NFC, lowercase, IDNA) as key within campaign
- [x] 3.3 Duplicate handling: first valid occurrence wins, later logged "duplicate of line N", rejected rows do not count; document behavior
- [x] 3.4 Template tag parser with case/whitespace-tolerant matching against columns
- [x] 3.5 Fail whole run before any DB write if tags lack columns, naming all missing tags
- [x] 3.6 Reject rows with empty fields referenced by template tags
- [x] 3.7 Reject CR/LF in header-bound values; build messages via structured `EmailMessage`
- [x] 3.8 RFC 2047 / SMTPUTF8 subject encoding and UTF-8 body

## 4. Queue and State

- [x] 4.1 SQLite schema (WAL, busy timeout): campaigns (template snapshot + hash), recipients keyed `(campaign_id, email_norm)`, daily_counts, events; status constraint on the five states; `possible_duplicate` flag
- [x] 4.2 Required `--campaign`; snapshot templates; refuse re-enqueue with differing template hash unless confirmed
- [x] 4.3 Idempotent enqueue (`INSERT OR IGNORE`) with new / already-queued-by-status / rejected counts
- [x] 4.4 Atomic claim of next eligible row (`BEGIN IMMEDIATE` + single UPDATE) honoring `next_attempt_at`
- [x] 4.5 OS-level lock so only one `send` runs; verify the lock is released on release (an actual process kill was not tested)
- [x] 4.6 Startup orphan recovery: `sending` -> `retry_pending` with `possible_duplicate`, logged
- [x] 4.7 Verify enqueue is safe alongside an active sender (no updates to existing rows)
- [x] 4.8 `requeue` command (campaign + emails or `--all-dead`, confirmation, attempts reset, logged); document that dead rows are never reset automatically
- [x] 4.9 `import-history` to load prior sent addresses as suppression; imported rows are recorded as `sent` (source `imported`) and reported as already queued at enqueue
- [x] 4.10 DB write failure handling: stop run, never report unrecorded `sent`

## 5. Sending and Failure Handling

- [x] 5.1 SMTP client wrapper (STARTTLS/SSL to smtp.gmail.com, login, send)
- [x] 5.2 Preflight: credentials present, login succeeds, DB writable, before claiming rows
- [x] 5.3 Failure classifier in order: auth, account-level (quota/block/530), rate-limit, transient/connection, permanent recipient-level; unknown 5xx halts and reports
- [x] 5.4 Permanent -> `dead`; transient -> `retry_pending` with exponential backoff + jitter; max attempts -> `dead`
- [x] 5.5 Auth failure: return row unconsumed, stop run with credential message
- [x] 5.6 Account-level failure: return row unconsumed, mark today as blocked (`daily_counts.blocked`) so later runs that day send nothing, stop run
- [x] 5.7 Rate-limit (421-style): global cool-down, row back to `retry_pending`, abort after max pauses
- [x] 5.8 Automatic reconnect with bounded attempts; continue with next row
- [x] 5.9 Circuit breaker on consecutive transient/connection failures only; permanent rejects ignored
- [x] 5.10 Wait or exit when only future retries remain (limit 30 min)
- [x] 5.11 Graceful SIGINT/SIGTERM handling; no row left in `sending`
- [x] 5.12 `--dry-run` (render all, send nothing) and `--test-to` (single sample, no queue changes)

## 6. Rate and Quota

- [x] 6.1 Persisted per-day sent counter and configurable cap; clean stop preserving queue
- [x] 6.2 Token-bucket pacing between sends

## 7. Observability

- [x] 7.1 `status`: five state counts, daily usage vs cap, next retry time, possible-duplicate count, last reject count, ETA, dead-letter list with last error; read-only during active send
- [x] 7.2 Log every attempt/outcome, pause, reconnect, recovery, halt with timestamps

## 8. Tests

- [x] 8.1 Unit tests for CSV/header/row/email/template validation scenarios (multi-address, header injection, first-invalid-duplicate, delimiter, multi-line records)
- [x] 8.2 Queue tests: idempotent enqueue, campaigns, template snapshot, atomic claim under concurrency, orphan recovery, concurrent enqueue, lock after kill
- [x] 8.3 Sender tests with a fake transport (not a real SMTP server) for 550 user unknown, 5.4.5 quota, 451, 535, 421, dropped connection, circuit breaker, bad-address run
- [x] 8.4 Daily cap and pacing tests; subject encoding round-trip; dry-run; graceful stop via stop flag and KeyboardInterrupt (`--test-to` and real signal handling are untested)

## 9. Retirement and Docs

- [x] 9.1 No prior send script exists in this repo (checked); README instructs importing history and deleting any copy elsewhere
- [x] 9.2 README: usage, config, duplicate policy, campaigns, dead-row `requeue` procedure, data-directory placement (outside OneDrive), "single supported send path" statement

## 10. Handlebars Templates

- [x] 10.1 Template engine for the Handlebars subset: `{{x}}` (HTML-escaped), `{{{x}}}`/`{{&x}}`, `{{[x y]}}`, comments; reject blocks, partials, helpers with a named error
- [x] 10.2 `--template FILE.hbs` and optional `--text-template FILE.hbs` replace `--body-file`/`--html-file`; refuse other extensions
- [x] 10.3 Derive the plain-text alternative from rendered HTML when no text template is given
- [x] 10.4 Tests for escaping, comments, bracket names, unsupported constructs, derived text and CLI refusal; README updated

## 11. Banner and Example Template

- [x] 11.1 `--banner` option: validate image type, snapshot bytes with the campaign (in the template hash), migrate older queue databases
- [x] 11.2 Embed the banner as a related part `<banner>`; refuse `cid:banner` templates without a banner
- [x] 11.3 Plain-text alternative skips `display:none` elements (inbox preheader)
- [x] 11.4 `templates/thank-you.hbs` (Teal & Gold, banner + two lines) and tests

