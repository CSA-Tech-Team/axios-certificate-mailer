"""Recipient email validation and normalization (Unicode/IDN aware)."""
from __future__ import annotations

import re
import unicodedata

_ASCII_LOCAL = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_BAD_UNICODE_LOCAL = set('()<>[]:;@\\,"')
MAX_LEN = 254


def normalize_email(raw: str) -> tuple[str | None, str | None]:
    """Return (normalized_address, None) or (None, reason).

    The normalized form is trimmed, NFC, lowercase, with the domain in IDNA
    (ASCII) form. It is used both as the dedup key and as the SMTP recipient.
    """
    s = raw.strip()
    if not s:
        return None, "empty email"
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in s):
        return None, "control characters in email"
    if "<" in s or ">" in s:
        return None, "display-name form not allowed"
    if ";" in s or "," in s or (any(c.isspace() for c in s) and s.count("@") >= 2):
        return None, "multiple addresses"
    if any(c.isspace() for c in s):
        return None, "malformed email"
    parts = s.split("@")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None, "malformed email"
    local = unicodedata.normalize("NFC", parts[0].lower())
    domain = unicodedata.normalize("NFC", parts[1].lower())
    if domain.endswith(".") or domain.startswith("."):
        return None, "malformed email"
    try:
        domain_ascii = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None, "malformed email"
    labels = domain_ascii.split(".")
    if len(labels) < 2 or not all(_LABEL.match(lab) for lab in labels):
        return None, "malformed email"
    if len(local.encode("utf-8")) > 64:
        return None, "malformed email"
    if local.isascii():
        if not _ASCII_LOCAL.match(local):
            return None, "malformed email"
    else:
        if (
            any(c in _BAD_UNICODE_LOCAL for c in local)
            or local.startswith(".")
            or local.endswith(".")
            or ".." in local
        ):
            return None, "malformed email"
    addr = f"{local}@{domain_ascii}"
    if len(addr) > MAX_LEN:
        return None, "email longer than 254 characters"
    return addr, None
