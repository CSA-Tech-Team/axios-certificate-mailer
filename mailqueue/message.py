"""Build the outgoing message from a campaign's template snapshot."""
from __future__ import annotations

import re
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html.parser import HTMLParser

from .config import Config
from .ingest import BREAK_CHARS
from .template import RenderError, norm_key, render

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}
_SKIP_TAGS = {"script", "style", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._href = None
        self._hidden_tag: str | None = None  # a display:none element being skipped
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if self._hidden_tag is not None:
            if tag == self._hidden_tag:
                self._hidden_depth += 1
            return
        style = (dict(attrs).get("style") or "").replace(" ", "").lower()
        if "display:none" in style and tag not in ("br", "img", "meta", "link", "hr", "input"):
            self._hidden_tag, self._hidden_depth = tag, 1
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if self._hidden_tag is not None:
            if tag == self._hidden_tag:
                self._hidden_depth -= 1
                if self._hidden_depth == 0:
                    self._hidden_tag = None
            return
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "a" and self._href:
            self.parts.append(f" ({self._href})")
            self._href = None

    def handle_data(self, data):
        if not self._skip and self._hidden_tag is None:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    """Plain-text alternative derived from rendered HTML."""
    ex = _TextExtractor()
    ex.feed(markup)
    ex.close()
    text = re.sub(r"[ \t]+", " ", "".join(ex.parts))
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


BANNER_CID = "<banner>"  # templates reference the embedded banner as src="cid:banner"


def _field(campaign, key):
    try:
        return campaign[key]
    except (KeyError, IndexError):
        return None


def build_message(cfg: Config, campaign, data: dict[str, str], to_addr: str) -> EmailMessage:
    """campaign needs subject_tpl, body_tpl (plain text, may be empty), html_tpl.

    Raises RenderError on any problem. Headers are set through the structured email API
    (never string-concatenated), the subject is RFC 2047-encoded (or raw UTF-8 under
    SMTPUTF8), and values that would inject a header line break are refused.
    The .hbs HTML template is HTML-escaped ({{x}}) / raw ({{{x}}}); when no plain-text
    template exists the text alternative is derived from the rendered HTML.
    """
    values = {norm_key(k): v for k, v in data.items()}
    subject = render(campaign["subject_tpl"], values)
    if any(c in BREAK_CHARS for c in subject):
        raise RenderError("line break in subject")
    html_tpl = campaign["html_tpl"]
    html_rendered = render(html_tpl, values, html=True) if html_tpl else None
    if campaign["body_tpl"]:
        body = render(campaign["body_tpl"], values)
    elif html_rendered:
        body = html_to_text(html_rendered)
    else:
        raise RenderError("campaign has no body template")

    pol = policy.SMTP if to_addr.isascii() else policy.SMTPUTF8
    msg = EmailMessage(policy=pol)
    msg["From"] = formataddr((cfg.from_name, cfg.smtp_user)) if cfg.from_name else cfg.smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=cfg.smtp_user.split("@")[-1])
    if cfg.reply_to:
        msg["Reply-To"] = cfg.reply_to
    if cfg.list_unsubscribe:
        msg["List-Unsubscribe"] = cfg.list_unsubscribe
    msg.set_content(body)
    if html_rendered:
        msg.add_alternative(html_rendered, subtype="html")
        banner = _field(campaign, "banner")
        if banner:
            maintype, _, subtype = (_field(campaign, "banner_type") or "image/png").partition("/")
            msg.get_body(("html",)).add_related(
                banner, maintype=maintype, subtype=subtype, cid=BANNER_CID, filename="banner." + subtype
            )
    return msg
