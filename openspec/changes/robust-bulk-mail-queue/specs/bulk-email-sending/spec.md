## ADDED Requirements

### Requirement: Single supported bulk-sending tool
The system SHALL be the single supported tool for bulk email sending, and its persistent queue SHALL be the single source of truth for send history. Any prior standalone send script SHALL be retired, and no parallel untracked send path SHALL remain.

#### Scenario: Legacy script retired
- **WHEN** the change is implemented
- **THEN** any prior standalone send script is removed or replaced with a stub that exits with an error directing the operator to this tool

#### Scenario: All sends recorded
- **WHEN** any bulk email is sent
- **THEN** a corresponding queue record and log entry exist for that send

### Requirement: Campaigns and template snapshot
Each `enqueue` invocation SHALL belong to a named campaign (`--campaign`, required). The recipient key SHALL be `(campaign, normalized email)`, so the same address MAY be queued in different campaigns but never twice in one. At enqueue, the system SHALL snapshot the subject, the `.hbs` body template and the optional plain-text `.hbs` template (stored with a content hash) into the campaign, and `send` SHALL render from that snapshot, not from the live template file. Enqueueing into an existing campaign with a different template SHALL be refused unless the operator explicitly confirms.

#### Scenario: Same person, second campaign
- **WHEN** `alice@x.com` was `sent` in campaign `march` and is enqueued in campaign `april`
- **THEN** she is queued as `pending` in `april` and `march` is unchanged

#### Scenario: Template edited after enqueue
- **WHEN** the template file is edited after `enqueue` and before `send`
- **THEN** `send` still renders the snapshotted template for that campaign

#### Scenario: Template differs on re-enqueue
- **WHEN** `enqueue` is re-run for an existing campaign with a template whose hash differs
- **THEN** the command refuses unless the operator confirms, and nothing is changed on refusal

### Requirement: Reject empty CSV
The system SHALL reject a CSV that contains a header but no data rows, or from which no valid row remains after validation (all rows rejected), with a clear error message, and SHALL NOT modify the queue in the header-only case. When all rows are rejected, the command SHALL exit non-zero and still write `rejects.csv`.

#### Scenario: Header-only CSV
- **WHEN** `enqueue` is run on a CSV with only a header row
- **THEN** the command exits non-zero with an error stating the CSV has no data rows

#### Scenario: Every row rejected
- **WHEN** every data row fails validation
- **THEN** the command exits non-zero, reports 0 enqueued, and writes all rows to `rejects.csv`

### Requirement: Skip trailing blank lines
The system SHALL ignore trailing blank lines (including whitespace-only lines) in a CSV and SHALL NOT treat them as invalid rows or log them to rejects. A blank line between data rows SHALL be skipped the same way.

#### Scenario: Trailing blank lines
- **WHEN** a CSV with 10 valid rows is followed by 3 blank lines
- **THEN** exactly 10 rows are processed and no rejects are recorded for the blank lines

### Requirement: Delimiter and cell handling
The system SHALL use a configurable delimiter (default `,`) and SHALL auto-detect nothing silently: if the header parses to a single column containing `;` or a tab, the system SHALL reject with an error suggesting `--delimiter`. Cell values SHALL be trimmed of surrounding whitespace before use. Quoted fields containing newlines SHALL be parsed as one record.

#### Scenario: Semicolon-delimited file
- **WHEN** a `;`-delimited CSV is enqueued with the default delimiter
- **THEN** the command rejects with an error suggesting `--delimiter ";"`

#### Scenario: Padded name
- **WHEN** a `name` cell is `  Alice `
- **THEN** the value used in the email is `Alice`

### Requirement: Reject non-UTF-8 CSV
The system SHALL detect a CSV that is not valid UTF-8 (a leading UTF-8 BOM SHALL be tolerated) and reject it with a clear error rather than an unhandled decode exception.

#### Scenario: Latin-1 encoded file
- **WHEN** `enqueue` is run on a CSV containing bytes invalid in UTF-8
- **THEN** the command exits non-zero with an error stating the file is not valid UTF-8, and no rows are enqueued

### Requirement: Validate CSV header
The system SHALL reject a CSV whose header contains duplicate column names (compared case-insensitively after trimming), empty column names, or column names with leading or trailing whitespace, and the error SHALL name each offending column (empty names by position).

#### Scenario: Empty column name
- **WHEN** the header is `email,name,` (trailing comma)
- **THEN** the run is rejected with an error naming column position 3 as empty

#### Scenario: Duplicate column
- **WHEN** the header is `email,name,Name`
- **THEN** the run is rejected with an error naming the duplicate column `Name`

#### Scenario: Whitespace in column name
- **WHEN** the header is `email, name`
- **THEN** the run is rejected with an error naming the column ` name` as having leading/trailing whitespace

### Requirement: Case-insensitive email column resolution
The system SHALL resolve the configured email column name case-insensitively against the CSV header. If no column matches, the system SHALL reject the CSV with an error naming the configured column.

#### Scenario: Case differs
- **WHEN** the configured email column is `email` and the header contains `Email`
- **THEN** the `Email` column is used as the email field

#### Scenario: Column missing
- **WHEN** no header column matches the configured email column
- **THEN** the run is rejected with an error naming the missing column

### Requirement: Large CSV confirmation
The system SHALL require explicit confirmation when the CSV data row count exceeds a configurable threshold (default 5,000), by an interactive prompt or a `--yes` flag. Without confirmation, the system SHALL NOT enqueue anything.

#### Scenario: Above threshold, interactive
- **WHEN** a CSV with 6,000 rows is enqueued and the operator declines the prompt
- **THEN** nothing is enqueued and the command exits non-zero

#### Scenario: Above threshold, non-interactive
- **WHEN** a CSV with 6,000 rows is enqueued without a TTY and without `--yes`
- **THEN** the command refuses and exits non-zero explaining that `--yes` is required

#### Scenario: Above threshold, confirmed
- **WHEN** the same CSV is enqueued with `--yes`
- **THEN** enqueue proceeds normally

### Requirement: Reject rows with wrong field count
The system SHALL reject any data row whose field count differs from the header's column count, write it to `rejects.csv` with the record number, the starting physical line number, and a reason, and continue processing remaining rows. `rejects.csv` SHALL be UTF-8, SHALL be written per enqueue run to a timestamped file (never silently overwriting a prior one), and SHALL include the original row values.

#### Scenario: Short row
- **WHEN** a row has 2 fields and the header has 3 columns
- **THEN** the row is written to `rejects.csv` with reason "field count mismatch (expected 3, got 2)" and is not enqueued

#### Scenario: Multi-line quoted field
- **WHEN** a quoted cell spans two physical lines and a later row is rejected
- **THEN** the rejected row reports both its record number and its correct physical starting line

### Requirement: Validate email field
The system SHALL reject rows whose email field is empty or malformed, logging each to `rejects.csv` with a reason. Validation SHALL accept internationalized addresses (Unicode local part and/or IDN domain) and not only ASCII.

#### Scenario: Empty email
- **WHEN** a row's email field is empty
- **THEN** the row is rejected with reason "empty email"

#### Scenario: Malformed email
- **WHEN** a row's email is `user@@example.com`
- **THEN** the row is rejected with reason "malformed email"

#### Scenario: Unicode address
- **WHEN** a row's email is `józef@exämple.com`
- **THEN** the row is accepted and sent using SMTPUTF8 or IDNA-encoded domain as required

### Requirement: Exactly one bare address per row
The email field SHALL contain exactly one bare address. The system SHALL reject, with reason, a field containing multiple addresses (separated by `;`, `,` or whitespace), a display-name form such as `Name <a@x.com>`, control characters or line breaks, or an address longer than 254 characters.

#### Scenario: Two addresses in one cell
- **WHEN** a row's email field is `a@x.com; b@y.com`
- **THEN** the row is rejected with reason "multiple addresses" and nobody is emailed

#### Scenario: Display-name form
- **WHEN** a row's email field is `Alice <a@x.com>`
- **THEN** the row is rejected with reason "display-name form not allowed"

### Requirement: Header injection protection
The system SHALL reject any row where a value substituted into the subject or any other header contains a CR or LF character, and SHALL construct headers only through a structured mail API, never by string concatenation of raw values.

#### Scenario: Newline in subject field
- **WHEN** the subject template uses `{{name}}` and a row's name contains a line break
- **THEN** the row is rejected with reason "line break in header value"

### Requirement: Normalize email for identity
The system SHALL normalize email addresses (trim whitespace, Unicode NFC, lowercase; IDN domains to IDNA form) before using them as the recipient's key within a campaign, so case, whitespace and Unicode-form variants of an address are the same recipient.

#### Scenario: Case variants
- **WHEN** a CSV contains `Alice@Example.com` and ` alice@example.com `
- **THEN** both normalize to `alice@example.com` and are treated as one recipient

### Requirement: Duplicate recipient handling
When the same normalized email appears in multiple rows, the system SHALL keep the first *valid* occurrence, SHALL NOT enqueue later occurrences even if their other columns differ, and SHALL log each later duplicate to `rejects.csv` with reason "duplicate of line N". A row that was itself rejected for another reason SHALL NOT count as the first occurrence. This behavior SHALL be documented.

#### Scenario: Conflicting duplicate data
- **WHEN** line 2 is `a@x.com,Alice` and line 5 is `A@x.com,Alicia`
- **THEN** the recipient is enqueued with name `Alice` and line 5 is logged as a duplicate of line 2

#### Scenario: First occurrence invalid
- **WHEN** line 2 is `a@x.com,` (empty required name) and line 5 is `a@x.com,Alicia`
- **THEN** line 2 is rejected for the empty field and line 5 is enqueued

### Requirement: Reject rows with empty template fields
The system SHALL reject any row where a column referenced by a template tag (in subject or body) is empty or whitespace-only, logging it to `rejects.csv` with a reason naming the field, and SHALL NOT enqueue it.

#### Scenario: Empty name
- **WHEN** the template uses `{{name}}` and a row's `name` is empty
- **THEN** the row is rejected with reason "empty field: name"

### Requirement: Handlebars template files
The email body template SHALL be a Handlebars `.hbs` file (`--template`), with an optional plain-text alternative that is also a `.hbs` file (`--text-template`); files with any other extension SHALL be refused. The system SHALL support the Handlebars subset `{{name}}` (HTML-escaped in the HTML body), `{{{name}}}` and `{{&name}}` (unescaped), `{{[name with spaces]}}`, and `{{! comments }}` / `{{!-- comments --}}`. Any other Handlebars construct (block helpers such as `{{#if}}` or `{{#each}}`, partials, `{{else}}`, helper calls, sub-expressions) SHALL fail the whole run before anything is queued, with an error naming the construct. When no plain-text template is given, the plain-text alternative SHALL be derived from the rendered HTML. The subject line is a plain string using the same tag syntax and is never HTML-escaped.

#### Scenario: Non-.hbs template
- **WHEN** `--template body.html` is given
- **THEN** the command exits non-zero stating that a `.hbs` file is required

#### Scenario: HTML escaping
- **WHEN** the HTML template contains `{{name}}` and a row's name is `<b>&`
- **THEN** the HTML part contains `&lt;b&gt;&amp;`, while `{{{name}}}` would insert it unescaped

#### Scenario: Unsupported block helper
- **WHEN** the template contains `{{#if name}}...{{/if}}`
- **THEN** the run fails before any row is queued with an error naming the unsupported construct

#### Scenario: Derived plain text
- **WHEN** only an HTML `.hbs` template is given
- **THEN** each message carries a plain-text alternative extracted from the rendered HTML (links shown as `text (url)`, scripts and styles dropped)

### Requirement: Embedded banner image
`enqueue` SHALL accept an optional `--banner` image (PNG, JPEG or GIF) that is snapshotted with the campaign's templates (and included in the template hash) and embedded in each message as a related MIME part with Content-ID `<banner>`, so a template can show it with `src="cid:banner"` without any hosted image. The command SHALL refuse a template that references `cid:banner` when no banner was given, and SHALL refuse a non-image banner.

#### Scenario: Banner embedded
- **WHEN** a campaign is enqueued with `--banner banner.png` and a template using `src="cid:banner"`
- **THEN** every sent message contains an image part with Content-ID `<banner>` whose bytes equal the file

#### Scenario: Missing banner
- **WHEN** the template references `cid:banner` and `--banner` is not given
- **THEN** the run fails before anything is queued naming `cid:banner`

#### Scenario: Banner changed later
- **WHEN** the same campaign is re-enqueued with a different banner image
- **THEN** it is treated as a template change and refused unless explicitly confirmed

### Requirement: Case- and whitespace-tolerant template tag matching
The system SHALL match template `{{tag}}` placeholders to CSV columns case-insensitively and ignoring whitespace around the tag name inside the braces. Column names containing spaces SHALL be referenced with the bracket form `{{[first name]}}`.

#### Scenario: Spaced, mixed-case tag
- **WHEN** the template contains `{{ Name }}` and the CSV has column `name`
- **THEN** the tag is resolved to the `name` column

#### Scenario: Column with a space
- **WHEN** the template contains `{{[First Name]}}` and the CSV has column `first name`
- **THEN** the tag is resolved to that column

### Requirement: Fail fast on unmatched template tags
The system SHALL, before enqueueing or sending anything, fail the entire run if any template tag has no matching CSV column, and the error SHALL name every missing tag.

#### Scenario: Missing tag
- **WHEN** the template uses `{{name}}` and `{{roll_no}}` but the CSV lacks `roll_no`
- **THEN** the command exits non-zero naming `roll_no`, and no rows are enqueued or sent

### Requirement: Encode non-ASCII subject
The system SHALL encode the subject header per RFC 2047 (or SMTPUTF8) so non-ASCII characters are delivered intact, in addition to correctly encoding the body as UTF-8.

#### Scenario: Unicode subject
- **WHEN** the subject is `Welcome, José — 🎉`
- **THEN** the sent message's decoded Subject header equals the original string

### Requirement: Persistent crash-safe queue
The system SHALL store recipients and their state in a persistent, transactional store, not an in-memory structure. Each recipient SHALL have exactly one status from: `pending`, `sending`, `sent`, `retry_pending`, `dead`. Permitted transitions are `pending -> sending`, `retry_pending -> sending`, `sending -> sent`, `sending -> retry_pending`, `sending -> dead`, and `retry_pending -> dead` after max retries. State changes SHALL be committed durably before the next action depends on them.

#### Scenario: Process killed mid-run
- **WHEN** the `send` process is killed after 100 recipients have been marked `sent`
- **THEN** on restart those 100 remain `sent` and are never sent again

### Requirement: Idempotent enqueue
`enqueue` SHALL be safe to run repeatedly on the same or an updated CSV. A recipient whose `(campaign, normalized email)` already exists in the queue in any status SHALL NOT be inserted again nor have its status or stored data changed (later changes to other columns are ignored), and the command SHALL report counts of newly enqueued, already-queued (broken down by status, including `dead`), and rejected rows.

#### Scenario: Re-run same CSV
- **WHEN** `enqueue` is run twice on the same CSV
- **THEN** the second run enqueues 0 new recipients and reports all as already queued

#### Scenario: Updated CSV
- **WHEN** the CSV gains 20 new addresses and `enqueue` is re-run
- **THEN** only the 20 new recipients are added, and existing `sent` recipients remain `sent`

### Requirement: Atomic row claiming
The system SHALL claim a recipient for sending with a single atomic transition to `sending` so that concurrent `send` processes can never claim, and therefore never send to, the same recipient. Only one active `send` process per queue SHALL be permitted, enforced by an OS-level file lock that is released automatically when the process dies (no stale PID files); a second SHALL fail fast with a clear message.

#### Scenario: Two send processes
- **WHEN** two `send` processes are started against the same queue
- **THEN** the second exits with an error that another sender is active, or if claim-level concurrency occurs, no recipient is sent twice

#### Scenario: Lock after crash
- **WHEN** a previous `send` was killed without cleanup
- **THEN** a new `send` acquires the lock without manual intervention

### Requirement: Orphan recovery on startup
On startup, `send` SHALL reset every row in `sending` status left over from a prior crash to `retry_pending` and log each recovered row. Such rows SHALL NOT be lost, and SHALL NOT be marked `sent` without confirmation. Because the message may already have been accepted by the server before the crash, each recovered row SHALL be flagged `possible_duplicate`, and the log and `status` output SHALL surface these rows.

#### Scenario: Crash during send
- **WHEN** a prior run crashed leaving 1 row in `sending`
- **THEN** the next `send` resets it to `retry_pending`, flags it `possible_duplicate`, logs the recovery, and processes it

### Requirement: Safe concurrent enqueue and send
`enqueue` SHALL be safe to run while a `send` process is active on the same queue: enqueue SHALL only insert new rows as `pending` within a transaction, SHALL NOT modify existing rows, and SHALL NOT block the sender beyond a short write lock. Newly enqueued rows MAY be picked up by the active sender.

#### Scenario: Enqueue during send
- **WHEN** `enqueue` adds 50 rows while `send` is running
- **THEN** no existing row's status changes, no row is duplicated, and the sender remains consistent

### Requirement: Dead rows are not auto-reset
The system SHALL NOT automatically move a `dead` row to any other status. Resetting a `dead` row SHALL be an explicit, deliberate operator action outside normal flow, and this SHALL be documented.

The only supported way to reset a `dead` row SHALL be an explicit `requeue` command that names the campaign and either specific emails or `--all-dead`, requires confirmation, resets attempts, and logs the action.

#### Scenario: Subsequent runs
- **WHEN** `send` or `enqueue` runs while dead rows exist
- **THEN** the dead rows remain `dead`

#### Scenario: Deliberate requeue
- **WHEN** the operator runs `requeue --campaign march --email a@x.com` and confirms
- **THEN** that row becomes `retry_pending` with attempts reset, and a log entry records who/when

### Requirement: Import prior send history
The system SHALL provide a way to import previously emailed addresses (from a file) as a suppression list. Suppressed addresses SHALL be recorded as `sent` (source `imported`) or skipped at enqueue with reason "previously sent", so they are never emailed again by this tool.

#### Scenario: Previously emailed address
- **WHEN** `a@x.com` is in the imported history and appears in a new CSV
- **THEN** it is not queued as `pending` and is reported as previously sent

### Requirement: Classify SMTP failures
The system SHALL classify each send failure: permanent recipient-level failures (SMTP 5xx replies scoped to the recipient or message, such as 550/551/553/554 for that address) SHALL mark the row `dead` immediately with no retry; transient (4xx, network errors, timeouts) SHALL set the row to `retry_pending` with exponential backoff plus jitter, incrementing an attempt count; and a row exceeding the configured max attempts SHALL become `dead`. The last error SHALL be stored on the row.

#### Scenario: Permanent failure
- **WHEN** the server replies `550 user unknown`
- **THEN** the row becomes `dead` immediately with the error recorded and no retry is scheduled

#### Scenario: Transient failure
- **WHEN** the server replies `451 try again later`
- **THEN** the row becomes `retry_pending` with a `next_attempt_at` computed with exponential backoff and jitter

#### Scenario: Max retries exceeded
- **WHEN** a row's attempts reach the configured max
- **THEN** the row becomes `dead`

### Requirement: Account-level failures halt the run
The system SHALL distinguish account- or session-level failures from recipient-level ones. A 5xx reply indicating a sending quota exceeded (e.g. `550 5.4.5 Daily user sending quota exceeded`), an account block or policy rejection (e.g. 554 blocking the account), or authentication required (530) SHALL NOT mark the row `dead`. The row SHALL return to `retry_pending` without consuming an attempt, and the run SHALL stop cleanly with a clear message. This SHALL apply even when the local daily counter is below the cap, because the account's quota is shared with other senders.

#### Scenario: Provider quota exceeded
- **WHEN** the server replies `550 5.4.5 Daily user sending quota exceeded` while the local count is 900
- **THEN** the run stops with a quota message, the row is `retry_pending` with attempts unchanged, no other row is marked dead, and today's counter is set to the cap so later runs the same day do not resume

#### Scenario: Unknown user is still permanent
- **WHEN** the server replies `550 5.1.1 user unknown` for a recipient
- **THEN** only that row becomes `dead`

### Requirement: Distinct authentication failure handling
The system SHALL detect SMTP authentication failure (e.g. 535 or 534, revoked/expired app password) as distinct from transient errors, SHALL NOT retry it as temporary, SHALL return the in-flight row to `retry_pending` without consuming an attempt, SHALL stop the run with a non-zero exit code, and SHALL report a clear message about credentials.

#### Scenario: App password revoked
- **WHEN** the server responds `535 Authentication failed` on login or send
- **THEN** the run stops with a credential-specific error, the affected row is `retry_pending` without an incremented attempt count, and the exit code is non-zero

### Requirement: Rate-limit response pauses the run
The system SHALL detect explicit provider rate-limit or throttling responses (e.g. 421, or 4.x.x codes indicating rate/quota) and SHALL pause pacing for the entire run for a configurable cool-down period (default 15 minutes), not just back off the one recipient. The affected row SHALL return to `retry_pending` without consuming an attempt. After a configurable number of rate-limit pauses in one run (default 3), the run SHALL stop with a clear message.

#### Scenario: 421 response
- **WHEN** the server replies `421 4.7.0 rate limit exceeded`
- **THEN** all sending pauses for the cool-down period, the row is `retry_pending`, and the pause is logged

#### Scenario: Repeated throttling
- **WHEN** the third rate-limit pause occurs in one run
- **THEN** the run stops with a message that the provider keeps throttling

### Requirement: Automatic reconnect
The system SHALL automatically reconnect on a dropped SMTP connection or TLS/certificate negotiation failure, with bounded reconnect attempts, and SHALL continue with the next queued row rather than terminating the run. The interrupted row SHALL follow transient-failure handling.

#### Scenario: Dropped connection
- **WHEN** the connection is dropped mid-run
- **THEN** the system reconnects and continues with the next eligible row

### Requirement: Consecutive-failure circuit breaker
The system SHALL halt the run after N consecutive transient or connection failures (configurable, default 10) with a clear error identifying the likely cause (based on the most recent failure classification) and SHALL leave remaining rows unmodified. Permanent recipient-level failures (e.g. unknown user) SHALL NOT count toward the breaker, and any successful send resets it.

#### Scenario: Breaker trips
- **WHEN** 10 consecutive send attempts fail with transient or connection errors
- **THEN** the run halts with a non-zero exit and an error message naming the likely cause, and remaining rows stay in their current status

#### Scenario: Run of bad addresses
- **WHEN** 15 consecutive recipients each get `550 user unknown`
- **THEN** all 15 are marked `dead` and the run continues

### Requirement: Retry scheduling when only future retries remain
When no row is currently eligible but `retry_pending` rows exist with future `next_attempt_at`, `send` SHALL wait until the earliest is due if that wait is within a configurable limit (default 30 minutes) and the daily cap allows; otherwise it SHALL exit with exit code 0 and report the earliest retry time.

#### Scenario: Long wait
- **WHEN** the earliest retry is due in 3 hours
- **THEN** `send` exits and reports the retry time rather than sleeping

### Requirement: Graceful shutdown
On SIGINT or SIGTERM, `send` SHALL finish or abandon the current attempt safely, return any in-flight row to `retry_pending` when the outcome is unknown or unsent (flagging `possible_duplicate` when the outcome is unknown), release the lock, and exit.

#### Scenario: Ctrl-C between sends
- **WHEN** the operator presses Ctrl-C while idle in pacing sleep
- **THEN** no row is left in `sending` and the process exits promptly

### Requirement: Preflight checks
Before claiming any row, `send` SHALL verify that credentials are configured, that the SMTP login succeeds, and that the queue database is writable, and SHALL fail with a specific message otherwise.

#### Scenario: Bad password at startup
- **WHEN** the app password is wrong
- **THEN** `send` exits with a credentials error before any row leaves `pending`

### Requirement: Dry run and test send
`send` SHALL support `--dry-run`, which renders every message from the snapshot, reports render errors and a sample, and sends nothing or changes no row status. It SHALL also support `--test-to <address>`, which sends a single rendered sample to that address without touching queue state or the daily send history of recipients.

#### Scenario: Dry run
- **WHEN** `send --dry-run` is run on a queue with 1,500 pending rows
- **THEN** no SMTP send occurs, no status changes, and a rendered sample is shown

### Requirement: Store-level robustness
The system SHALL configure a busy timeout on the queue database and retry briefly on lock contention. On a full disk or database error it SHALL stop the run without marking any row `sent` that it cannot durably record.

#### Scenario: Cannot record result
- **WHEN** the database write recording `sent` fails
- **THEN** the run stops with an error and the row is not reported as sent

### Requirement: Daily send cap
The system SHALL count sends per calendar day (in a configured timezone) persistently and SHALL enforce a configurable daily cap (default 1,800). On reaching the cap, the system SHALL stop cleanly before exceeding it, preserve all remaining queue state, and report how many remain. A later run on the same day SHALL still respect the persisted count, and a run on a new day SHALL resume from remaining rows.

#### Scenario: Cap reached
- **WHEN** 1,800 emails have been sent today and rows remain
- **THEN** `send` stops with exit code 0 and a message that the daily cap was reached, with the remaining rows unchanged

#### Scenario: Restart same day
- **WHEN** `send` is re-run after the cap was hit earlier the same day
- **THEN** it sends nothing and reports the cap is still reached

#### Scenario: Next day
- **WHEN** `send` runs on the next calendar day
- **THEN** the counter starts at 0 and remaining rows are sent

### Requirement: Paced sending
The system SHALL pace sends with a token bucket or batch-and-sleep mechanism with configurable rate, to avoid bursts.

#### Scenario: Pacing applied
- **WHEN** the configured rate is 1 message per 2 seconds
- **THEN** consecutive sends are spaced at least approximately 2 seconds apart

### Requirement: Status command
The system SHALL provide a `status` command (optionally per campaign) that shows counts per state (`pending`, `sending`, `sent`, `retry_pending`, `dead`), today's sent count against the cap, the earliest next retry time, the count of `possible_duplicate` rows, the reject count of the last enqueue, an estimated time to finish at the configured pace, and lists every `dead` row with its email and last error. It SHALL work read-only while a `send` is active.

#### Scenario: Status output
- **WHEN** `status` is run
- **THEN** it prints a count for each of the five states, the daily usage, the next retry time, and each dead entry with its last error

#### Scenario: Status during send
- **WHEN** `status` is run while `send` is active
- **THEN** it returns consistent counts without blocking the sender

### Requirement: Data handling and log rotation
Queue database, logs and `rejects.csv` files contain personal data. The system SHALL support a configurable data directory (default outside any cloud-synced folder is recommended and documented), SHALL never write credentials to logs, and SHALL rotate log files by size.

#### Scenario: Credentials not logged
- **WHEN** a login failure is logged
- **THEN** the log contains no password or app-password value

### Requirement: Timestamped attempt logging
The system SHALL log every send attempt and its outcome (success or failure with classification) with a timestamp, plus pauses, reconnects, recoveries, and halts.

#### Scenario: Attempt logged
- **WHEN** a send attempt succeeds or fails
- **THEN** a log entry with an ISO-8601 timestamp, the normalized recipient, and the outcome is recorded
