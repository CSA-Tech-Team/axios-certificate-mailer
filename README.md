# DTP certificate 

Static HTML implementation of Figma participation certificate with script-fillable participant and college fields.

## Autofill

Run this in the browser after the page loads:

```js
window.fillCertificate({
  name: "Aarav Kumar",
  college: "PSG College of Technology",
});
```

The same API is available as `window.Certificate.setValues(...)`. Current values can be read with `window.Certificate.getValues()` and cleared with `window.Certificate.reset()`.

Participant and college text automatically shrink independently when needed, keeping long values within their underline. If a script changes a field directly, dispatch an `input` event or call `window.Certificate.refit()` afterward.

The fields can also be populated through query parameters:

```text
?name=Aarav%20Kumar&college=PSG%20College%20of%20Technology
```

For DOM automation, the inputs are `#certificate-name` and `#certificate-college`.

## Python certificate mailer

`certificate_mailer.py` accepts participant data, renders one validated PDF per participant, and can send each PDF through Gmail SMTP using an app password. Generation is the default; email is sent only when `--send` is supplied.

### 1. Install

Python 3.10 or newer is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

PDFs are drawn directly with ReportLab. No browser, Chromium installation, or live font service is required.

### 2. Prepare a Gmail app password

1. Turn on 2-Step Verification for the sending Google account.
2. In that account's Google Account security settings, create an **App password** (for example, named `DTP Certificate Mailer`).
3. Create a `.env` file beside `certificate_mailer.py` with the sender address and generated app password. Do not put the password in a CSV, source file, or command-line argument.

```dotenv
GMAIL_SENDER_EMAIL="your-account@gmail.com"
GMAIL_APP_PASSWORD="your-16-character-app-password"
```

The script loads this gitignored `.env` only when `--send` is used. Values already present in the shell take precedence. It uses `smtp.gmail.com` over SSL and does not use the Gmail API, Google Cloud project, OAuth client, or browser sign-in. If `GMAIL_APP_PASSWORD` is omitted when running interactively, it is requested with a hidden prompt instead.

### 3. Add participants

Copy `participants.example.csv` to `participants.csv` and replace the sample values. Required columns are:

```csv
name,college,email
Aarav Kumar,PSG College of Technology,aarav@example.com
```

JSON input is also supported; see `participants.example.json`.

### 4. Generate without sending

```bash
python certificate_mailer.py participants.csv
```

PDFs are written to `generated-certificates/`. Every render validates that both fitted values remain inside their blank before accepting the PDF.

Test a single participant first:

```bash
python certificate_mailer.py participants.csv --limit 1
```

### 5. Send through Gmail

```bash
python certificate_mailer.py participants.csv --send
```

Successful email message IDs are recorded in `send-log.csv`. Later runs skip logged recipients unless `--resend` is explicitly supplied.

Useful options:

```text
--output-dir PATH
--sender-email ADDRESS
--app-password-env VARIABLE
--send-log PATH
--subject TEXT
--body-file PATH
--limit N
--delay SECONDS
--resend
```

A custom body file may use `{name}`, `{college}`, and `{email}` placeholders.

## mailqueue — resumable bulk email sender

**This is the single supported tool for bulk sending from techteam.csa@psgtech.ac.in.**
Its SQLite queue is the single source of truth for send history. There is no other
supported send path: do not send campaigns from any other script, mail-merge or the
Gmail UI, or the history (and the ~2,000/day quota accounting) will be wrong.
If an older standalone send script exists on your machine, first run
`import-history` with the addresses it already mailed, then delete it.

Python 3.10+, standard library only.

### Setup

```
set MAILQ_SMTP_PASSWORD=<google app password>      (PowerShell: $env:MAILQ_SMTP_PASSWORD="...")
```

Keep the data directory **outside OneDrive** (SQLite files can corrupt or lock in synced
folders). Default: `%LOCALAPPDATA%\mail-queue`. Override with `--data-dir` or `MAILQ_DATA_DIR`.
The directory holds `queue.db`, `send.lock`, `logs/mailq.log` (rotating) and `rejects/`; it contains
recipients' personal data, so never commit it.

Any setting can be overridden by `MAILQ_<NAME>` env vars or a JSON file (`--config`): `daily_cap`
(1800), `rate_per_sec` (0.5), `max_attempts` (5), `breaker_n` (10), `cooldown_s` (900),
`max_rate_pauses` (3), `retry_wait_limit_s` (1800), `csv_threshold` (5000), `timezone`
(Asia/Kolkata), `email_column` (email), `delimiter` (`,`), `from_name`, `reply_to`, `list_unsubscribe`.
The password is only ever read from the environment.

### Workflow

```
python -m mailqueue enqueue --campaign axios26 --csv participants.csv --subject "Your AXIOS '26 Certificate of Participation" --template templates/email.hbs --text-template templates/email-text.hbs
python -m mailqueue send --dry-run                     # render everything, send nothing
python -m mailqueue send --test-to you@psgtech.ac.in   # one real sample; queue untouched
python -m mailqueue send [--campaign march] [--limit N]
python -m mailqueue status [--campaign march]
```

- **Templates are Handlebars `.hbs` files.** `--template email.hbs` is the HTML body. Supported syntax:
  `{{name}}` (HTML-escaped), `{{{name}}}` or `{{&name}}` (raw, e.g. a link column), `{{[first name]}}`
  for columns with spaces, and `{{! comments }}`. Block helpers (`{{#if}}`, `{{#each}}`), partials and
  helper calls are **not supported** and make `enqueue` fail with an error. The plain-text version is
  derived from the HTML unless you pass `--text-template plain.hbs`. The subject (`--subject`) is a plain
  string that can use the same `{{tags}}`.
- **Banner image.** The banner is supplied separately: `--banner banner.png` (png/jpg/gif) embeds it in
  the email, and the template shows it with `<img src="cid:banner">`. It is snapshotted with the template
  (changing it later needs `--allow-template-change`). `enqueue` refuses a template that uses `cid:banner`
  without `--banner`. Nothing needs to be hosted online.
- **AXIOS '26 templates** (`templates/`), all taken from the existing certificate project, only in `.hbs` form:
  - `certificate.hbs` is `dist/index.html` with `value="{{name}}"` and `value="{{college}}"` on the two
    fields. It uses the existing `dist/styles.css`, `dist/script.js` (auto-fit) and `dist/assets/`, so the
    design is unchanged; render it into a folder beside `dist/` so the `../dist/` paths resolve.
  - `email.hbs` is the AXIOS email layout from `mail-templates/emails/basic-axios-mail.tsx` (rendered by
    React Email) carrying `certificate_mailer.py`'s default message; `email-text.hbs` is that message as
    plain text (`{name}` → `{{name}}`). The CSV needs a `name` column. mailqueue does not attach files, so
    the certificate PDF is not sent by this path.
  - `otp-mail.hbs` / `otp-mail-text.hbs` are `mail-templates/emails/otp-mail.tsx` with the code as `{{otp}}`
    (needs an `otp` column).
  - The AXIOS layouts load their logo, background and social icons from `public.amcspsgtech.in`; a solid
    `#1b1b1b` fallback keeps the white text readable when a client blocks remote images.
- **`mail-templates/`** is the merged `axios-mail-templates` React Email project (source of the layouts
  above). To regenerate HTML after editing a `.tsx`: `cd mail-templates && yarn && yarn dev`.
- **Campaigns.** The key is `(campaign, email)`: the same person can be in a later campaign,
  never twice in one. The subject/body templates are snapshotted at first enqueue; later edits
  to the files do not change a queued campaign. Re-enqueueing with a different template is refused
  unless `--allow-template-change`.
- **Enqueue is idempotent** and never touches rows already queued (any status). It can run while
  `send` is active; new rows are added as `pending` and may be picked up by the running sender.
- **CSV rules.** UTF-8 (BOM ok). Blank rows (including all-empty-cell rows) are skipped. Header must
  have no duplicate (case-insensitive), empty or padded names. The email column and `{{tags}}`
  match columns case-insensitively and ignore surrounding whitespace (`{{ Name }}` = `name`). Every tag must
  have a column or the whole run fails before anything is queued. More than 5,000 rows needs `--yes`.
- **Rejected rows** go to `rejects/rejects-<campaign>-<time>.csv` with record number, line and reason:
  wrong field count, empty/malformed email (Unicode/IDN accepted), several addresses or
  `Name <a@b>` in one cell, empty value for a tag, line break in a subject value, duplicates.
- **Duplicates.** Emails are normalized (trim, NFC, lowercase, IDN to punycode). The first *valid*
  occurrence wins; later ones are rejected as `duplicate of line N` even if other columns differ.

### Row states

`pending -> sending -> sent`, or `sending -> retry_pending` (backoff + jitter) `-> dead` after
`max_attempts`. A permanent recipient error (e.g. `550 5.1.1 user unknown`) goes straight to `dead`.

Run-wide conditions stop or pause the run instead of killing rows:

| Condition | Behaviour |
|---|---|
| Auth failure (535/534/530) | stop, exit 3, row returned without using an attempt |
| Provider quota (`5.4.5`), account block | stop; quota pins today as done; exit 0 / 5 |
| Rate limit (421-style) | pause *all* sending for `cooldown_s`; abort after `max_rate_pauses` |
| Dropped connection / TLS error | reconnect, continue with next row |
| N consecutive transient failures | circuit breaker, exit 8 (bad addresses do not count) |
| Unrecognized 5xx | halt, exit 6 |
| Daily cap reached | stop cleanly, exit 0; remaining rows wait for tomorrow |

If `send` is killed, rows stuck in `sending` are reset to `retry_pending` on the next start and
flagged **possible_duplicate** (the server may have accepted the message before the crash). `status`
shows the count; the log names each row.

### Dead rows

The system never revives a `dead` row by itself. Resetting one is a deliberate operator action:

```
python -m mailqueue requeue --campaign march --email a@x.com      # or --all-dead
```

### Exit codes

0 ok/finished/cap reached · 2 bad input or config · 3 auth · 4 database/IO error · 5 account blocked ·
6 unknown 5xx · 7 repeated throttling · 8 circuit breaker · 9 another sender is running · 130 interrupted.

### Tests

`python -m unittest discover -s tests -t .` (uses a fake SMTP transport; no network).
Before the first real campaign, run `send --test-to` once: the real Gmail transport is not covered by tests.
