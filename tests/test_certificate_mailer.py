import csv
import json
import os
import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import MagicMock, patch

from certificate_mailer import (
    CertificateRenderer,
    Participant,
    ParticipantDataError,
    SendLog,
    calculate_fitted_font_size,
    create_email_message,
    get_smtp_credentials,
    load_participants,
    safe_certificate_filename,
    send_with_retry,
)


class CertificateMailerTests(unittest.TestCase):
    def test_signature_assets_are_present(self):
        assets = Path(__file__).resolve().parents[1] / "dist" / "assets"
        self.assertTrue((assets / "signature.png").is_file())
        self.assertTrue((assets / "megala_sign-clean.png").is_file())

    def test_reportlab_renderer_generates_one_page_pdf_for_long_values(self):
        participant = Participant(
            "Ananya Venkateshwaran Balasubramanian",
            "Sri Ramakrishna Institute of Technology and Applied Sciences",
            "ananya@example.com",
        )
        try:
            renderer = CertificateRenderer()
        except RuntimeError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "certificate.pdf"
            report = renderer.render(participant, output)
            self.assertGreater(output.stat().st_size, 10_000)
            self.assertTrue(report["certificate-name"]["fits"])
            self.assertTrue(report["certificate-college"]["fits"])

    def test_font_size_calculation_keeps_long_text_inside_blank(self):
        fitted = calculate_fitted_font_size(64, 890, 2400)
        self.assertLess(fitted, 64)
        self.assertLessEqual(2400 * (fitted / 64), 890)

    def test_loads_csv_and_long_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participants.csv"
            path.write_text(
                "name,college,email\n"
                "Ananya Venkateshwaran Balasubramanian,"
                "Sri Ramakrishna Institute of Technology and Applied Sciences,"
                "ananya@example.com\n",
                encoding="utf-8",
            )
            participant = load_participants(path)[0]
            self.assertEqual(participant.email, "ananya@example.com")
            self.assertIn("Applied Sciences", participant.college)

    def test_loads_json_object_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participants.json"
            path.write_text(
                json.dumps(
                    {
                        "participants": [
                            {
                                "name": "Aarav Kumar",
                                "college": "PSG College of Technology",
                                "email": "aarav@example.com",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_participants(path)[0].name, "Aarav Kumar")

    def test_rejects_duplicate_recipients(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "participants.csv"
            path.write_text(
                "name,college,email\nA,College,a@example.com\n"
                "B,Other,A@example.com\n",
                encoding="utf-8",
            )
            with self.assertRaises(ParticipantDataError):
                load_participants(path)

    def test_filename_is_safe_and_distinguishes_duplicate_names(self):
        first = Participant("Aarav Kumar", "College", "one@example.com")
        second = Participant("Aarav Kumar", "College", "two@example.com")
        self.assertNotEqual(
            safe_certificate_filename(first), safe_certificate_filename(second)
        )
        self.assertRegex(
            safe_certificate_filename(first),
            r"^axios26-certificate-aarav-kumar-[a-f0-9]{8}\.pdf$",
        )

    def test_smtp_message_contains_sender_and_pdf_attachment(self):
        participant = Participant("Aarav Kumar", "PSG", "aarav@example.com")
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "certificate.pdf"
            pdf.write_bytes(b"%PDF-1.7\ncertificate")
            message = create_email_message(
                participant,
                pdf,
                "Certificate",
                "Hello {name} from {college}",
                "sender@gmail.com",
            )
            parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
            attachments = list(parsed.iter_attachments())
            self.assertEqual(parsed["From"], "sender@gmail.com")
            self.assertEqual(parsed["To"], "aarav@example.com")
            self.assertTrue(parsed["Message-ID"])
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments[0].get_content_type(), "application/pdf")

    def test_smtp_credentials_read_environment_and_strip_app_password_spaces(self):
        with patch.dict(
            os.environ,
            {
                "GMAIL_SENDER_EMAIL": "sender@gmail.com",
                "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop",
            },
            clear=True,
        ):
            sender, password = get_smtp_credentials(None, "GMAIL_APP_PASSWORD")
        self.assertEqual(sender, "sender@gmail.com")
        self.assertEqual(password, "abcdefghijklmnop")

    @patch("certificate_mailer.smtplib.SMTP_SSL")
    def test_smtp_sender_authenticates_and_sends_message(self, smtp_ssl):
        server = MagicMock()
        server.send_message.return_value = {}
        smtp_ssl.return_value.__enter__.return_value = server
        message = EmailMessage()
        message["Message-ID"] = "<test@example.com>"
        message["To"] = "recipient@example.com"
        message.set_content("Certificate attached")

        message_id = send_with_retry(
            "sender@gmail.com", "app-password", message, attempts=1
        )

        smtp_ssl.assert_called_once()
        server.login.assert_called_once_with("sender@gmail.com", "app-password")
        server.send_message.assert_called_once_with(message, from_addr="sender@gmail.com")
        self.assertEqual(message_id, "<test@example.com>")

    def test_send_log_prevents_unintentional_duplicates(self):
        participant = Participant("Aarav Kumar", "PSG", "aarav@example.com")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "send-log.csv"
            log = SendLog(path)
            self.assertFalse(log.contains(participant.email))
            log.append(participant, Path("certificate.pdf"), "message-id")
            self.assertTrue(SendLog(path).contains(participant.email))
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["message_id"], "message-id")


if __name__ == "__main__":
    unittest.main()
