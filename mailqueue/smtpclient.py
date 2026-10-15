"""SMTP transport and failure classification.

Classification order matters: auth, then account-level (quota/block), then
rate-limit, then transient/connection, then permanent recipient-level.
Unrecognized 5xx replies are UNKNOWN and halt the run rather than killing rows.
"""
from __future__ import annotations

import enum
import re
import smtplib
import ssl

from .config import Config


class Kind(enum.Enum):
    AUTH = "auth"
    QUOTA = "quota"
    ACCOUNT = "account"
    RATE_LIMIT = "rate_limit"
    CONNECTION = "connection"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


_QUOTA_RE = re.compile(
    r"daily user sending|user sending (quota|limit)|daily (sending )?(limit|quota)|sending (quota|limit)|\b5\.4\.5\b",
    re.I,
)
_BLOCK_RE = re.compile(
    r"account.{0,40}(disabled|suspended|blocked|locked)|(suspended|disabled).{0,20}account"
    r"|temporarily blocked|sending.{0,20}(blocked|suspended)",
    re.I,
)
_RATE_RE = re.compile(
    r"rate.?limit|too many (messages|connections|recipients|concurrent)|throttl|sending rate", re.I
)


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def classify_response(code: int, text: str) -> Kind:
    if code in (530, 534, 535):
        return Kind.AUTH
    if 500 <= code < 600:
        if _QUOTA_RE.search(text):
            return Kind.QUOTA
        if _BLOCK_RE.search(text):
            return Kind.ACCOUNT
        if code in (550, 551, 552, 553, 554):
            return Kind.PERMANENT
        return Kind.UNKNOWN
    if 400 <= code < 500:
        if code == 421 or _RATE_RE.search(text):
            return Kind.RATE_LIMIT
        return Kind.TRANSIENT
    return Kind.UNKNOWN


def classify(exc: BaseException) -> tuple[Kind, str]:
    """Map an exception raised while connecting/sending to (Kind, description)."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return Kind.AUTH, f"{exc.smtp_code} {_text(exc.smtp_error)}"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        code, msg = next(iter(exc.recipients.values()), (550, b"recipient refused"))
        text = _text(msg)
        return classify_response(code, text), f"{code} {text}"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        text = _text(exc.smtp_error)
        kind = classify_response(exc.smtp_code, text)
        if kind in (Kind.PERMANENT, Kind.UNKNOWN):
            kind = Kind.ACCOUNT  # the sender is our own account
        return kind, f"{exc.smtp_code} {text}"
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPHeloError)):
        return Kind.CONNECTION, f"{exc.smtp_code} {_text(exc.smtp_error)}"
    if isinstance(exc, smtplib.SMTPResponseException):
        text = _text(exc.smtp_error)
        return classify_response(exc.smtp_code, text), f"{exc.smtp_code} {text}"
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return Kind.CONNECTION, f"server disconnected: {exc}"
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return Kind.PERMANENT, f"not supported by server: {exc}"
    if isinstance(exc, smtplib.SMTPException):
        return Kind.TRANSIENT, f"SMTP error: {exc}"
    if isinstance(exc, ssl.SSLError):
        return Kind.CONNECTION, f"TLS error: {exc}"
    if isinstance(exc, (OSError, EOFError)):
        return Kind.CONNECTION, f"network error: {type(exc).__name__}: {exc}"
    return Kind.TRANSIENT, f"unexpected error: {type(exc).__name__}: {exc}"


class SmtpTransport:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.smtp: smtplib.SMTP | None = None

    @property
    def connected(self) -> bool:
        return self.smtp is not None

    def connect(self) -> None:
        self.close()
        cfg = self.cfg
        ctx = ssl.create_default_context()
        smtp = None
        try:
            if cfg.smtp_port == 465:
                smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout_s, context=ctx)
            else:
                smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout_s)
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.ehlo()
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        except BaseException:
            if smtp is not None:
                try:
                    smtp.close()
                except Exception:
                    pass
            raise
        self.smtp = smtp

    def send(self, msg, rcpt: str) -> None:
        refused = self.smtp.send_message(msg, from_addr=self.cfg.smtp_user, to_addrs=[rcpt])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)

    def close(self) -> None:
        smtp, self.smtp = self.smtp, None
        if smtp is None:
            return
        try:
            smtp.quit()
        except Exception:
            try:
                smtp.close()
            except Exception:
                pass
