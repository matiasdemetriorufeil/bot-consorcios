"""WhatsAppClient against a fake HTTP session (no network), and webhook parsing."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
import requests

from app.bot.choices import Choice
from app.config import Settings
from app.whatsapp.client import (
    MediaTooLargeError,
    WhatsAppClient,
    WhatsAppError,
    parse_rewrites,
)
from app.whatsapp.events import is_silent, parse_delivery
from tests.whatsapp.fakes import (
    CONTACT_WA_ID,
    OTHER_PHONE_NUMBER_ID,
    PHONE_NUMBER_ID,
    button_reply,
    echoes,
    incoming,
    list_reply,
    media_message,
    reaction,
    status,
    statuses,
    text_message,
)

BASE = "https://graph.facebook.com/v26.0"
AT = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)


@dataclass
class FakeResponse:
    status_code: int = 200
    data: Any = None
    chunks: list[bytes] = field(default_factory=list)

    @property
    def content(self) -> bytes:
        return b"" if self.data is None else json.dumps(self.data).encode()

    def json(self) -> Any:
        return self.data

    def iter_content(self, size: int) -> Any:
        return iter(self.chunks)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


@dataclass
class FakeHttp:
    responses: list[FakeResponse] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if self.error:
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse(data={"messages": [{"id": "wamid.OK"}]})

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)


def make_client(http: FakeHttp, rewrites: dict[str, str] | None = None) -> WhatsAppClient:
    return WhatsAppClient(
        "token-inventado",
        PHONE_NUMBER_ID,
        http=http,  # type: ignore[arg-type]
        recipient_rewrites=rewrites,
    )


def test_send_text() -> None:
    http = FakeHttp()

    wamid = make_client(http).send_text(CONTACT_WA_ID, "hola")

    [req] = http.requests
    assert wamid == "wamid.OK"
    assert (req["method"], req["url"]) == ("POST", f"{BASE}/{PHONE_NUMBER_ID}/messages")
    assert req["headers"] == {"Authorization": "Bearer token-inventado"}
    assert req["json"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": CONTACT_WA_ID,
        "type": "text",
        "text": {"preview_url": False, "body": "hola"},
    }


def test_up_to_three_options_go_as_buttons() -> None:
    http = FakeHttp()

    make_client(http).send_choices(
        CONTACT_WA_ID, "¿Te paso?", [Choice("Sí, pasame", "si"), Choice("No, gracias", "no")]
    )

    assert http.requests[0]["json"]["interactive"] == {
        "type": "button",
        "body": {"text": "¿Te paso?"},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": "opt-1", "title": "Sí, pasame"}},
                {"type": "reply", "reply": {"id": "opt-2", "title": "No, gracias"}},
            ]
        },
    }


def test_more_options_go_as_a_list() -> None:
    http = FakeHttp()
    titles = [f"Unidad {n}" for n in range(1, 6)]

    make_client(http).send_choices(CONTACT_WA_ID, "¿Cuál?", [Choice(t, t) for t in titles])

    interactive = http.requests[0]["json"]["interactive"]
    assert interactive["type"] == "list"
    assert interactive["action"]["button"] == "Ver opciones"
    assert [r["title"] for r in interactive["action"]["sections"][0]["rows"]] == titles


def test_list_rows_carry_their_description() -> None:
    http = FakeHttp()
    choices = [
        Choice(f"Problema {n}", f"cat:{n}", "Una aclaración" if n == 1 else "") for n in range(1, 5)
    ]

    make_client(http).send_choices(CONTACT_WA_ID, "¿Cuál?", choices)

    rows = http.requests[0]["json"]["interactive"]["action"]["sections"][0]["rows"]
    assert rows[0] == {"id": "opt-1", "title": "Problema 1", "description": "Una aclaración"}
    assert "description" not in rows[1]


@pytest.mark.parametrize(
    "titles",
    [
        ["Un título de botón demasiado largo", "No"],  # > 20 for buttons
        [f"Opción {n}" for n in range(11)],  # > 10
        ["Sí", "sí"],  # repeated
    ],
)
def test_options_over_the_limits_are_rejected_before_sending(titles: list[str]) -> None:
    http = FakeHttp()

    with pytest.raises(ValueError):
        make_client(http).send_choices(CONTACT_WA_ID, "texto", [Choice(t, t) for t in titles])

    assert http.requests == []


def test_send_template() -> None:
    http = FakeHttp()

    make_client(http).send_template(CONTACT_WA_ID, "seguimiento", components=[{"type": "body"}])

    assert http.requests[0]["json"]["template"] == {
        "name": "seguimiento",
        "language": {"code": "es_AR"},
        "components": [{"type": "body"}],
    }


def test_meta_errors_carry_the_code() -> None:
    http = FakeHttp(
        responses=[FakeResponse(400, {"error": {"code": 131030, "message": "not allowed"}})]
    )

    with pytest.raises(WhatsAppError) as error:
        make_client(http).send_text(CONTACT_WA_ID, "hola")

    assert error.value.code == 131030
    assert "not allowed" in str(error.value)


def test_network_errors_and_missing_ids() -> None:
    with pytest.raises(WhatsAppError):
        make_client(FakeHttp(error=requests.ConnectionError())).send_text(CONTACT_WA_ID, "x")
    with pytest.raises(WhatsAppError):
        make_client(FakeHttp(responses=[FakeResponse(data={})])).send_text(CONTACT_WA_ID, "x")


def test_recipient_rewrite() -> None:
    http = FakeHttp()
    client = make_client(http, {CONTACT_WA_ID: "543511555550101"})

    client.send_text(CONTACT_WA_ID, "hola")
    client.send_text("5493515550202", "hola")

    assert [r["json"]["to"] for r in http.requests] == ["543511555550101", "5493515550202"]


def test_parse_rewrites() -> None:
    assert parse_rewrites("") == {}
    assert parse_rewrites("549351:5435115, +549352 : 5435215") == {
        "549351": "5435115",
        "549352": "5435215",
    }
    assert parse_rewrites("roto,549351:5435115,:1") == {"549351": "5435115"}


@pytest.mark.parametrize(("env", "active"), [("development", True), ("production", False)])
def test_rewrite_only_in_development(env: str, active: bool) -> None:
    settings = Settings(
        _env_file=None,
        app_env=env,
        whatsapp_access_token="token",
        whatsapp_phone_number_id=PHONE_NUMBER_ID,
        whatsapp_dev_recipient_rewrite=f"{CONTACT_WA_ID}:543511555550101",
    )

    client = WhatsAppClient.from_settings(settings)

    assert client._rewrites == ({CONTACT_WA_ID: "543511555550101"} if active else {})


def test_from_settings_needs_token_and_number() -> None:
    with pytest.raises(WhatsAppError):
        WhatsAppClient.from_settings(Settings(_env_file=None))


def test_graph_version_is_configurable() -> None:
    settings = Settings(
        _env_file=None,
        whatsapp_access_token="token",
        whatsapp_phone_number_id=PHONE_NUMBER_ID,
        whatsapp_graph_version="v25.0",
    )

    assert WhatsAppClient.from_settings(settings).base_url.endswith("/v25.0")
    assert Settings(_env_file=None).whatsapp_graph_version == "v26.0"


def test_media_download_with_limit() -> None:
    ok = FakeHttp(responses=[FakeResponse(chunks=[b"ab", b"cd"])])
    big = FakeHttp(responses=[FakeResponse(chunks=[b"ab", b"cd", b"ef"])])

    assert make_client(ok).download("https://lookaside.example/x", max_bytes=4) == b"abcd"
    with pytest.raises(MediaTooLargeError):
        make_client(big).download("https://lookaside.example/x", max_bytes=4)
    with pytest.raises(WhatsAppError):
        make_client(FakeHttp()).download("http://lookaside.example/x", max_bytes=4)
    assert ok.requests[0]["headers"] == {"Authorization": "Bearer token-inventado"}


# --- Parsing ---------------------------------------------------------------------------------


def test_parse_messages_of_every_kind() -> None:
    payload = incoming(
        text_message("hola", AT, message_id="wamid.1"),
        button_reply("Sí, pasame", AT),
        list_reply("Unidad 4", AT),
        media_message("image", AT, caption="el portón"),
        media_message("document", AT, mime="application/pdf", filename="recibo.pdf"),
        reaction(AT, "wamid.1"),
    )

    delivery = parse_delivery(payload, PHONE_NUMBER_ID)

    texts = [(m.message_type, m.text) for m in delivery.messages]
    assert texts == [
        ("text", "hola"),
        ("interactive", "Sí, pasame"),
        ("interactive", "Unidad 4"),
        ("image", "el portón"),
        ("document", ""),
        ("reaction", ""),
    ]
    first = delivery.messages[0]
    assert (first.wamid, first.wa_id, first.profile_name, first.timestamp) == (
        "wamid.1",
        CONTACT_WA_ID,
        "Contacto Prueba",
        AT,
    )
    document = delivery.messages[4]
    assert document.media is not None
    assert (document.media.mime_type, document.media.filename) == ("application/pdf", "recibo.pdf")
    assert document.attachment_types == ("file",)
    assert [is_silent(m.message_type, m.text) for m in delivery.messages] == [
        False,
        False,
        False,
        False,
        False,
        True,
    ]


def test_parse_statuses_and_echoes() -> None:
    delivery = parse_delivery(
        statuses(status("wamid.A", "failed", AT, error=(131030, "Not allowed"))), PHONE_NUMBER_ID
    )
    [failed] = delivery.statuses
    assert (failed.status, failed.error_code) == ("failed", 131030)

    delivery = parse_delivery(echoes(text_message("Hola, soy Marta", AT)), PHONE_NUMBER_ID)
    [echo] = delivery.echoes
    assert (echo.wa_id, echo.text) == (CONTACT_WA_ID, "Hola, soy Marta")


def test_parse_ignores_other_numbers_and_garbage() -> None:
    other = incoming(text_message("hola", AT), phone_number_id=OTHER_PHONE_NUMBER_ID)

    assert parse_delivery(other, PHONE_NUMBER_ID).messages == []
    assert parse_delivery({"entry": "x"}, PHONE_NUMBER_ID).messages == []
    assert parse_delivery({"entry": [{"changes": [{"field": "messages"}]}]}, "").messages == []
