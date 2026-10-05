# DTP certificate — Frame 47

Static HTML implementation of Figma Frame 47 with script-fillable participant and college fields.

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
