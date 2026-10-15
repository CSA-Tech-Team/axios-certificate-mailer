## Why

We must send personalized emails to 1,500–2,000 recipients per campaign from a single Google Workspace account (techteam.csa@psgtech.ac.in), which is capped at roughly 2,000 messages/day. A naive send loop loses state on crashes, double-sends on re-runs, silently ships incomplete content, and can trip Google's rate/spam controls. We need a reliable, resumable sender with one authoritative record of send history.

## What Changes

- Introduce a CLI tool with `enqueue`, `send`, and `status` commands backed by a persistent, crash-safe queue (single source of truth for send history).
- Strict CSV ingestion: encoding, header, row-shape, email and template-field validation, with rejected rows written to `rejects.csv` with a reason.
- Email body templates are Handlebars `.hbs` files (supported subset: `{{x}}`, `{{{x}}}`, `{{[x y]}}`, comments); unsupported constructs are rejected up front.
- Template validation before any sending: all `{{tag}}` placeholders must map to CSV columns; subject lines are properly encoded for non-ASCII.
- Per-recipient state machine: `pending -> sending -> sent`, or `retry_pending -> dead` after max retries; idempotent enqueue; atomic row claiming; orphan recovery on startup.
- SMTP failure classification (permanent / transient / auth / rate-limit / connection), exponential backoff with jitter, automatic reconnect, and a consecutive-failure circuit breaker.
- Daily quota tracking (default cap 1,800) and paced sending (token bucket or batch-and-sleep).
- Campaigns with per-campaign template snapshots; `requeue` (explicit dead-row reset) and `import-history` (suppression of previously emailed addresses); dry-run/test-send, preflight checks and graceful shutdown.
- Observability: `status` command with per-state counts and dead-letter listing; timestamped log of every attempt and outcome.
- **BREAKING**: Any prior standalone send script is retired. This tool becomes the single supported path for bulk sending; no untracked send path may remain.

## Capabilities

### New Capabilities
- `bulk-email-sending`: CSV/template validation, persistent resumable queue, failure-aware SMTP sending, daily quota and pacing, and status/logging for bulk personalized email.

### Modified Capabilities
<!-- None: no existing specs. -->

## Impact

- New code: CLI entrypoint, CSV/template validation module, queue store (persistent database file), SMTP sender, pacing/quota module, logging.
- New runtime artifacts: queue database, `rejects.csv`, send log, config (SMTP credentials via app password, daily cap, thresholds).
- Removal/deprecation of any existing standalone send script.
- External dependency: Google Workspace SMTP (smtp.gmail.com) and its ~2,000/day limit.
