"""Outgoing email. Two backends chosen by EMAIL_BACKEND: "console" (development) and "smtp".

Only the console backend ever writes a verification code to the logs, and only with
APP_ENV=development: anywhere else it logs the masked address and hides the code.
"""

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 15
IMPLICIT_TLS_PORT = 465

VERIFICATION_SUBJECT = "Tu código de verificación - Estudio Diego Rufeil"


class EmailError(Exception):
    """The email could not be sent."""


def mask_email(email: str) -> str:
    """'juan.perez@gmail.com' -> 'j***@gmail.com'. Safe to show in chat and logs."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def verification_email_body(code: str, valid_minutes: int) -> str:
    return (
        "Hola,\n\n"
        f"Tu código para verificar tu WhatsApp con el Estudio Diego Rufeil es: {code}\n\n"
        f"Vence en {valid_minutes} minutos. Escribilo en el chat de WhatsApp.\n\n"
        "Si no lo pediste vos, ignorá este email: sin el código nadie puede ver tus datos.\n\n"
        "Estudio Diego Rufeil\n"
    )


class EmailSender(Protocol):
    def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
        """Send the code. Raises EmailError if it could not be sent."""
        ...


class ConsoleEmailSender:
    """Development backend: nothing is sent, the email goes to the log. The code is only
    shown with show_code=True (get_email_sender: APP_ENV=development)."""

    def __init__(self, *, show_code: bool = False) -> None:
        self.show_code = show_code

    def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
        logger.info(
            "[EMAIL_BACKEND=console] Email de verificación (no enviado) a %s - código: %s",
            mask_email(to),
            code if self.show_code else "[oculto: solo se muestra con APP_ENV=development]",
        )


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host or not settings.smtp_from:
            raise EmailError("EMAIL_BACKEND=smtp requiere SMTP_HOST y SMTP_FROM")
        self._settings = settings

    def send_verification_code(self, to: str, code: str, valid_minutes: int) -> None:
        message = EmailMessage()
        message["From"] = self._settings.smtp_from
        message["To"] = to
        message["Subject"] = VERIFICATION_SUBJECT
        message.set_content(verification_email_body(code, valid_minutes))
        self._send(message)
        logger.info("Email de verificación enviado a %s", mask_email(to))

    def _send(self, message: EmailMessage) -> None:
        s = self._settings
        password = s.smtp_password.get_secret_value() if s.smtp_password else ""
        try:
            if s.smtp_port == IMPLICIT_TLS_PORT:
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                    s.smtp_host, s.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
                )
            else:
                smtp = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
            with smtp:
                if s.smtp_port != IMPLICIT_TLS_PORT:
                    smtp.starttls()
                if s.smtp_user:
                    smtp.login(s.smtp_user, password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            # The exception text may echo addresses: only its type is reported.
            raise EmailError(f"envío SMTP falló: {type(exc).__name__}") from exc


def get_email_sender(settings: Settings | None = None) -> EmailSender:
    settings = settings or get_settings()
    if settings.email_backend == "smtp":
        return SmtpEmailSender(settings)
    if settings.app_env != "development":
        logger.warning("EMAIL_BACKEND=console fuera de desarrollo: los emails NO se envían")
    return ConsoleEmailSender(show_code=settings.app_env == "development")
