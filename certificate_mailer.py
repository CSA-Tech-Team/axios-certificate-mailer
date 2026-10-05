#!/usr/bin/env python3
"""Generate AXIOS certificate PDFs and optionally send them through Gmail SMTP."""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent
ASSET_DIR = PROJECT_ROOT / "dist" / "assets"
DESIGN_WIDTH = 3508.0
DESIGN_HEIGHT = 2480.0
DEFAULT_SUBJECT = "Your AXIOS '26 Certificate of Participation"
DEFAULT_BODY = """Hello {name},

Thank you for participating in AXIOS '26. Please find your certificate of participation attached to this email.

Regards,
Computational Sciences Association
PSG College of Technology
"""
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ParticipantDataError(ValueError):
    """Raised when participant input is incomplete or unsafe to process."""


class CertificateRenderError(RuntimeError):
    """Raised when a generated certificate does not pass layout validation."""


@dataclass(frozen=True, slots=True)
class Participant:
    name: str
    college: str
    email: str

    @classmethod
    def from_mapping(cls, record: dict[str, Any], row_number: int) -> "Participant":
        normalized = {str(key).strip().lower(): value for key, value in record.items()}
        values = {
            field: str(normalized.get(field, "")).strip()
            for field in ("name", "college", "email")
        }
        missing = [field for field, value in values.items() if not value]
        if missing:
            raise ParticipantDataError(
                f"Row {row_number}: missing required field(s): {', '.join(missing)}"
            )
        if not EMAIL_PATTERN.fullmatch(values["email"]):
            raise ParticipantDataError(
                f"Row {row_number}: invalid email address {values['email']!r}"
            )
        return cls(**values)


def load_participants(path: Path) -> list[Participant]:
    """Load and strictly validate participant records from CSV or JSON."""
    if not path.is_file():
        raise ParticipantDataError(f"Participant file does not exist: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            records = list(csv.DictReader(handle))
    elif suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            payload = payload.get("participants")
        if not isinstance(payload, list):
            raise ParticipantDataError(
                "JSON must be a list of participants or an object with a participants list."
            )
        if not all(isinstance(record, dict) for record in payload):
            raise ParticipantDataError("Every JSON participant must be an object.")
        records = payload
    else:
        raise ParticipantDataError("Participant data must be a .csv or .json file.")

    if not records:
        raise ParticipantDataError("The participant file contains no records.")

    participants = [
        Participant.from_mapping(record, row_number)
        for row_number, record in enumerate(records, start=2)
    ]

    duplicate_emails = sorted(
        email
        for email in {participant.email.lower() for participant in participants}
        if sum(p.email.lower() == email for p in participants) > 1
    )
    if duplicate_emails:
        raise ParticipantDataError(
            "Duplicate recipient email(s): " + ", ".join(duplicate_emails)
        )

    return participants


def safe_certificate_filename(participant: Participant) -> str:
    """Create a stable, collision-resistant PDF filename."""
    ascii_name = participant.name.encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "participant"
    slug = slug[:64].rstrip("-")
    recipient_hash = hashlib.sha256(participant.email.lower().encode()).hexdigest()[:8]
    return f"axios26-certificate-{slug}-{recipient_hash}.pdf"


def _format_email_body(template: str, participant: Participant) -> str:
    try:
        return template.format(
            name=participant.name,
            college=participant.college,
            email=participant.email,
        )
    except KeyError as error:
        raise ParticipantDataError(
            f"Unknown email template placeholder: {error.args[0]!r}. "
            "Supported placeholders are {name}, {college}, and {email}."
        ) from error


def create_email_message(
    participant: Participant,
    pdf_path: Path,
    subject: str,
    body_template: str,
    sender_email: str,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender_email
    message["To"] = participant.email
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=sender_email.rsplit("@", 1)[-1])
    message.set_content(_format_email_body(body_template, participant))
    message.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )
    return message


def calculate_fitted_font_size(
    base_size: float,
    available_width: float,
    measured_width: float,
    safety_factor: float = 0.94,
) -> float:
    """Return a font size that keeps measured text inside a fixed blank."""
    if base_size <= 0 or available_width <= 0 or measured_width <= 0:
        return base_size
    return base_size * min(1.0, (available_width * safety_factor) / measured_width)


class CertificateRenderer:
    """Render the fixed Figma certificate directly with ReportLab."""

    FONT_FILES = {
        "Adamina": "Adamina-Regular.ttf",
        "Italianno": "Italianno-Regular.ttf",
        "BeVietnamPro-ExtraBold": "BeVietnamPro-ExtraBold.ttf",
    }

    def __init__(self) -> None:
        try:
            from reportlab.graphics import renderPDF
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.pdfgen.canvas import Canvas
            from svglib.svglib import svg2rlg
        except ImportError as error:
            raise RuntimeError(
                "PDF dependencies are not installed. Run: pip install -r requirements.txt"
            ) from error

        self.colors = colors
        self.pdfmetrics = pdfmetrics
        self.Canvas = Canvas
        self.renderPDF = renderPDF
        self.page_width, self.page_height = landscape(A4)
        self.scale_x = self.page_width / DESIGN_WIDTH
        self.scale_y = self.page_height / DESIGN_HEIGHT

        for font_name, filename in self.FONT_FILES.items():
            font_path = ASSET_DIR / filename
            if not font_path.is_file():
                raise FileNotFoundError(f"Certificate font not found: {font_path}")
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font_name, str(font_path)))

        corner_path = ASSET_DIR / "corner-lines.svg"
        self.corner_drawing = svg2rlg(str(corner_path))
        if self.corner_drawing is None:
            raise CertificateRenderError(f"Could not read SVG artwork: {corner_path}")

    def __enter__(self) -> "CertificateRenderer":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _x(self, value: float) -> float:
        return value * self.scale_x

    def _y(self, top: float, height: float = 0) -> float:
        return self.page_height - (top + height) * self.scale_y

    def _font_size(self, design_pixels: float) -> float:
        return design_pixels * self.scale_x

    def _draw_image(
        self,
        canvas: Any,
        filename: str,
        x: float,
        top: float,
        width: float,
        height: float,
    ) -> None:
        path = ASSET_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(f"Certificate asset not found: {path}")
        canvas.drawImage(
            str(path),
            self._x(x),
            self._y(top, height),
            self._x(width),
            height * self.scale_y,
            mask="auto",
        )

    def _baseline_for_top(self, font_name: str, font_size: float, top: float) -> float:
        ascent = self.pdfmetrics.getAscent(font_name) / 1000 * font_size
        return self._y(top) - ascent

    def _draw_centered_top(
        self,
        canvas: Any,
        text: str,
        font_name: str,
        design_font_size: float,
        center_x: float,
        top: float,
        color: Any | None = None,
    ) -> None:
        font_size = self._font_size(design_font_size)
        canvas.setFont(font_name, font_size)
        canvas.setFillColor(color or self.colors.black)
        canvas.drawCentredString(
            self._x(center_x),
            self._baseline_for_top(font_name, font_size, top),
            text,
        )

    def _fit_field(
        self, text: str, font_name: str, base_design_size: float, width: float
    ) -> tuple[float, dict[str, float | bool]]:
        base_size = self._font_size(base_design_size)
        available = self._x(width)
        measured_at_base = self.pdfmetrics.stringWidth(text, font_name, base_size)
        fitted_size = calculate_fitted_font_size(
            base_size, available, measured_at_base
        )
        measured = self.pdfmetrics.stringWidth(text, font_name, fitted_size)
        return fitted_size, {
            "available": available,
            "measured": measured,
            "fontSize": fitted_size / self.scale_x,
            "fits": measured <= available + 0.01,
        }

    def _draw_statement(
        self, canvas: Any, participant: Participant
    ) -> dict[str, dict[str, float | bool]]:
        font_name = "Adamina"
        base_size = self._font_size(64)
        teal = self.colors.HexColor("#145752")
        baseline = self._baseline_for_top(font_name, base_size, 1541)

        canvas.setFillColor(self.colors.black)
        canvas.setFont(font_name, base_size)
        canvas.drawString(self._x(339), baseline, "This is to certify that Mr./Ms.")

        name_x, name_width = 1240, 890
        college_x, college_width = 2270, 1000
        underline_y = baseline - self._x(7)
        canvas.setStrokeColor(teal)
        canvas.setLineWidth(self._x(4))
        canvas.line(
            self._x(name_x), underline_y, self._x(name_x + name_width), underline_y
        )
        canvas.line(
            self._x(college_x),
            underline_y,
            self._x(college_x + college_width),
            underline_y,
        )

        canvas.setFillColor(self.colors.black)
        canvas.setFont(font_name, base_size)
        canvas.drawString(self._x(2165), baseline, "of")

        name_size, name_report = self._fit_field(
            participant.name, font_name, 64, name_width - 24
        )
        college_size, college_report = self._fit_field(
            participant.college, font_name, 64, college_width - 24
        )
        canvas.setFillColor(teal)
        canvas.setFont(font_name, name_size)
        canvas.drawCentredString(
            self._x(name_x + name_width / 2),
            underline_y + self._x(12),
            participant.name,
        )
        canvas.setFont(font_name, college_size)
        canvas.drawCentredString(
            self._x(college_x + college_width / 2),
            underline_y + self._x(12),
            participant.college,
        )

        second_line = (
            "has participated in the 3rd edition of AXIOS ‘26, an Inter-Collegiate "
            "Technical Fest conducted at"
        )
        second_available = self._x(2940)
        second_measured = self.pdfmetrics.stringWidth(
            second_line, font_name, base_size
        )
        second_size = calculate_fitted_font_size(
            base_size, second_available, second_measured, 0.99
        )
        canvas.setFillColor(self.colors.black)
        canvas.setFont(font_name, second_size)
        canvas.drawString(
            self._x(339),
            self._baseline_for_top(font_name, second_size, 1661),
            second_line,
        )
        canvas.setFont(font_name, base_size)
        canvas.drawString(
            self._x(339),
            self._baseline_for_top(font_name, base_size, 1781),
            "PSG College of Technology.",
        )

        return {
            "certificate-name": name_report,
            "certificate-college": college_report,
        }

    def render(self, participant: Participant, output_path: Path) -> dict[str, Any]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas = self.Canvas(str(output_path), pagesize=(self.page_width, self.page_height))
        paper = self.colors.HexColor("#fffaf3")
        teal = self.colors.HexColor("#145752")

        canvas.setFillColor(paper)
        canvas.rect(0, 0, self.page_width, self.page_height, fill=1, stroke=0)

        canvas.saveState()
        canvas.translate(self._x(-142), self._y(-72, 2775))
        canvas.scale(
            self._x(3982) / self.corner_drawing.width,
            (2775 * self.scale_y) / self.corner_drawing.height,
        )
        self.renderPDF.draw(self.corner_drawing, canvas, 0, 0)
        canvas.restoreState()

        canvas.setFillColor(paper)
        canvas.rect(
            self._x(1),
            self._y(-10, 533),
            self._x(612),
            533 * self.scale_y,
            fill=1,
            stroke=0,
        )

        self._draw_image(canvas, "psg-crest.png", 22, 110, 209, 209)
        self._draw_image(canvas, "75-years.png", 251, 105, 205, 205)
        self._draw_image(canvas, "csa.png", 476, 121, 244, 162)
        self._draw_image(canvas, "psg-wordmark.png", 2816, 136, 329, 160)
        self._draw_image(canvas, "psg-centenary.png", 3165, 66, 298, 298)

        self._draw_centered_top(
            canvas, "PSG COLLEGE OF TECHNOLOGY", "Adamina", 100, 1767.5, 95
        )
        self._draw_centered_top(
            canvas,
            "DEPARTMENT OF APPLIED MATHEMATICS AND COMPUTATIONAL SCIENCES",
            "Adamina",
            50,
            1766,
            242,
        )
        self._draw_centered_top(
            canvas,
            "COMPUTATIONAL SCIENCES ASSOCIATION",
            "Adamina",
            50,
            1766,
            300,
        )
        self._draw_centered_top(
            canvas,
            "Certificate  of  Participation",
            "Italianno",
            250,
            1828.5,
            389,
        )

        self._draw_image(canvas, "sponsors.png", 1229, 745, 1050, 166)
        self._draw_image(canvas, "axios26.png", 1054, 995, 1401, 381)
        self._draw_centered_top(
            canvas,
            "SEPTEMBER 25 & 26",
            "BeVietnamPro-ExtraBold",
            48,
            2557.5,
            1360,
            teal,
        )

        fit_report = self._draw_statement(canvas, participant)
        overflowing = [
            field for field, result in fit_report.items() if not result["fits"]
        ]
        if overflowing:
            raise CertificateRenderError(
                f"Text overflow for {participant.email}: {', '.join(overflowing)}"
            )

        # Keep supplied signatures directly above their assigned signature rules.
        self._draw_image(canvas, "signature.png", 492, 1973, 502, 204)
        self._draw_image(canvas, "megala_sign-clean.png", 1508, 1910, 495, 265)

        signature_specs = [
            (461, 1025, 743, "SECRETARY"),
            (1474, 2038, 1756, "FACULTY ADVISOR"),
            (2443, 3007, 2725, "PRINCIPAL"),
        ]
        canvas.setStrokeColor(teal)
        canvas.setLineWidth(self._x(4))
        for line_start, line_end, center_x, label in signature_specs:
            canvas.line(
                self._x(line_start),
                self._y(2181),
                self._x(line_end),
                self._y(2181),
            )
            self._draw_centered_top(
                canvas, label, "Adamina", 58, center_x, 2239
            )

        canvas.showPage()
        canvas.save()
        if not output_path.is_file() or output_path.stat().st_size < 10_000:
            raise CertificateRenderError(
                f"Rendered PDF is missing or unexpectedly small: {output_path}"
            )
        return fit_report


def get_smtp_credentials(
    sender_email: str | None, app_password_env: str
) -> tuple[str, str]:
    """Read SMTP credentials without placing the app password on the command line."""
    sender = (sender_email or os.environ.get("GMAIL_SENDER_EMAIL", "")).strip()
    if not EMAIL_PATTERN.fullmatch(sender):
        raise ParticipantDataError(
            "Set GMAIL_SENDER_EMAIL to the Gmail address that will send mail, "
            "or pass it with --sender-email."
        )

    app_password = os.environ.get(app_password_env, "")
    if not app_password:
        if not sys.stdin.isatty():
            raise RuntimeError(
                f"Set {app_password_env} to a Gmail app password before using --send."
            )
        app_password = getpass.getpass("Gmail app password: ")
    app_password = re.sub(r"\s+", "", app_password)
    if not app_password:
        raise RuntimeError("A non-empty Gmail app password is required.")
    return sender, app_password


def send_with_retry(
    sender_email: str,
    app_password: str,
    message: EmailMessage,
    host: str = "smtp.gmail.com",
    port: int = 465,
    attempts: int = 3,
) -> str:
    """Send a MIME email via authenticated Gmail SMTP over SSL."""
    for attempt in range(1, attempts + 1):
        try:
            with smtplib.SMTP_SSL(
                host, port, context=ssl.create_default_context(), timeout=30
            ) as server:
                server.login(sender_email, app_password)
                refused = server.send_message(message, from_addr=sender_email)
            if refused:
                raise RuntimeError(f"SMTP refused recipient(s): {refused}")
            return str(message["Message-ID"])
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused):
            raise
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, OSError):
            if attempt == attempts:
                raise
            time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


class SendLog:
    FIELDNAMES = ["sent_at_utc", "email", "name", "pdf", "message_id"]

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sent_emails: set[str] = set()
        if path.is_file():
            with path.open("r", encoding="utf-8", newline="") as handle:
                self.sent_emails = {
                    row["email"].strip().lower()
                    for row in csv.DictReader(handle)
                    if row.get("email")
                }

    def contains(self, email: str) -> bool:
        return email.lower() in self.sent_emails

    def append(self, participant: Participant, pdf_path: Path, message_id: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
            if is_new:
                writer.writeheader()
            writer.writerow(
                {
                    "sent_at_utc": datetime.now(timezone.utc).isoformat(),
                    "email": participant.email,
                    "name": participant.name,
                    "pdf": str(pdf_path),
                    "message_id": message_id,
                }
            )
            handle.flush()
            os.fsync(handle.fileno())
        self.sent_emails.add(participant.email.lower())


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate AXIOS '26 certificate PDFs and optionally email them."
    )
    parser.add_argument("participants", type=Path, help="CSV or JSON participant file")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("generated-certificates")
    )
    parser.add_argument("--send", action="store_true", help="Send generated PDFs")
    parser.add_argument(
        "--resend", action="store_true", help="Allow emails already present in send log"
    )
    parser.add_argument(
        "--sender-email",
        help="Gmail sender address (defaults to GMAIL_SENDER_EMAIL)",
    )
    parser.add_argument(
        "--app-password-env",
        default="GMAIL_APP_PASSWORD",
        help="Environment variable holding the Gmail app password (default: GMAIL_APP_PASSWORD)",
    )
    parser.add_argument("--send-log", type=Path, default=Path("send-log.csv"))
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    parser.add_argument(
        "--body-file",
        type=Path,
        help="UTF-8 text template supporting {name}, {college}, and {email}",
    )
    parser.add_argument("--limit", type=int, help="Process only the first N records")
    parser.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between sent emails"
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    participants = load_participants(args.participants)
    if args.limit is not None:
        if args.limit < 1:
            raise ParticipantDataError("--limit must be at least 1.")
        participants = participants[: args.limit]

    body_template = DEFAULT_BODY
    if args.body_file:
        body_template = args.body_file.read_text(encoding="utf-8")
    # Validate the template before rendering or authenticating.
    for participant in participants:
        _format_email_body(body_template, participant)

    if not ASSET_DIR.is_dir():
        raise FileNotFoundError(f"Certificate assets not found: {ASSET_DIR}")

    sender_email = ""
    app_password = ""
    send_log = SendLog(args.send_log)
    if args.send:
        sender_email, app_password = get_smtp_credentials(
            args.sender_email, args.app_password_env
        )

    generated: list[tuple[Participant, Path]] = []
    with CertificateRenderer() as renderer:
        for participant in participants:
            pdf_path = args.output_dir / safe_certificate_filename(participant)
            fit_report = renderer.render(participant, pdf_path)
            smallest_size = min(result["fontSize"] for result in fit_report.values())
            print(
                f"[generated] {participant.email} -> {pdf_path} "
                f"(minimum field size: {smallest_size:.1f}px)"
            )
            generated.append((participant, pdf_path))

    if not args.send:
        print(f"Generated {len(generated)} certificate(s). No email was sent.")
        return 0

    failures = 0
    for participant, pdf_path in generated:
        if send_log.contains(participant.email) and not args.resend:
            print(f"[skipped] {participant.email} is already in {args.send_log}")
            continue
        try:
            message = create_email_message(
                participant, pdf_path, args.subject, body_template, sender_email
            )
            message_id = send_with_retry(sender_email, app_password, message)
            send_log.append(participant, pdf_path, message_id)
            print(f"[sent] {participant.email} (message {message_id})")
            if args.delay > 0:
                time.sleep(args.delay)
        except Exception as error:
            failures += 1
            print(f"[failed] {participant.email}: {error}", file=sys.stderr)

    if failures:
        print(f"Completed with {failures} failed email(s).", file=sys.stderr)
        return 1
    print(f"Email processing complete for {len(generated)} certificate(s).")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except (ParticipantDataError, CertificateRenderError, FileNotFoundError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
