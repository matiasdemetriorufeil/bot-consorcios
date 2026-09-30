"""Email backends. No real email is ever sent here; addresses are invented."""

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import pytest

from app.config import Settings
from app.notify import email as email_module
from app.notify.email import (
    ConsoleEmailSender,
    EmailError,
    SmtpEmailSender,
    get_email_sender,
    mask_email,
    verification_email_body,
)


def test_mask_email() -> None:
    assert mask_email("juan.perez@gmail.com") == "j***@gmail.com"
    assert mask_email("a@example.com") == "a***@example.com"
    assert mask_email("sin-arroba") == "***"


def test_body_has_code_expiry_and_warning() -> None:
    body = verification_email_body("123456", 15)

    assert "123456" in body
    assert "15 minutos" in body
    assert "Si no lo pediste vos, ignorá" in body


def test_backend_selection() -> None:
    assert isinstance(get_email_sender(Settings(email_backend="console")), ConsoleEmailSender)
    smtp = Settings(email_backend="smtp", smtp_host="smtp.example.com", smtp_from="a@example.com")
    assert isinstance(get_email_sender(smtp), SmtpEmailSender)
    with pytest.raises(EmailError):
        SmtpEmailSender(Settings(email_backend="smtp", smtp_host="", smtp_from=""))


def test_console_backend_logs_the_code(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        ConsoleEmailSender().send_verification_code("juan@example.com", "654321", 15)

    assert "654321" in caplog.text
    assert "j***@example.com" in caplog.text
    assert "juan@example.com" not in caplog.text


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail = False

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        self.calls.append("quit")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}:{password}")

    def send_message(self, message: EmailMessage) -> None:
        if FakeSMTP.fail:
            raise smtplib.SMTPRecipientsRefused({"juan@example.com": (550, b"no")})
        self.sent.append(message)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.fail = False
    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def smtp_settings(port: int = 587) -> Settings:
    return Settings(
        email_backend="smtp",
        smtp_host="smtp.example.com",
        smtp_port=port,
        smtp_user="bot@example.com",
        smtp_password="clave-inventada",  # noqa: S106
        smtp_from="Estudio <bot@example.com>",
    )


def test_smtp_sends_with_starttls_and_never_logs_the_code(
    fake_smtp: type[FakeSMTP], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        SmtpEmailSender(smtp_settings()).send_verification_code("juan@example.com", "111222", 15)

    (server,) = fake_smtp.instances
    assert server.calls == ["starttls", "login:bot@example.com:clave-inventada", "quit"]
    (message,) = server.sent
    assert message["To"] == "juan@example.com"
    assert "111222" in message.get_content()
    assert "111222" not in caplog.text
    assert "juan@example.com" not in caplog.text


def test_smtp_port_465_uses_implicit_tls(fake_smtp: type[FakeSMTP]) -> None:
    SmtpEmailSender(smtp_settings(465)).send_verification_code("juan@example.com", "111222", 15)

    assert "starttls" not in fake_smtp.instances[0].calls


def test_smtp_failure_raises_email_error_without_addresses(fake_smtp: type[FakeSMTP]) -> None:
    fake_smtp.fail = True
    with pytest.raises(EmailError) as info:
        SmtpEmailSender(smtp_settings()).send_verification_code("juan@example.com", "1", 15)

    assert "juan@example.com" not in str(info.value)
