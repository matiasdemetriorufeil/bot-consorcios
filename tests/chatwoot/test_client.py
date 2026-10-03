"""ChatwootClient against a fake HTTP session (no network)."""

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
import requests

from app.chatwoot.client import ChatwootClient, ChatwootError
from app.config import Settings


@dataclass
class FakeResponse:
    status_code: int = 200
    data: Any = None

    @property
    def content(self) -> bytes:
        return b"" if self.data is None else json.dumps(self.data).encode()

    def json(self) -> Any:
        return self.data


@dataclass
class FakeHttp:
    responses: list[FakeResponse] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else FakeResponse(data={})


def make_client(http: FakeHttp, api_token: str | None = "user-token") -> ChatwootClient:
    return ChatwootClient(
        "http://chatwoot:3000/",
        3,
        bot_token="bot-token",
        api_token=api_token,
        http=http,  # type: ignore[arg-type]
    )


def test_send_message_uses_the_bot_token() -> None:
    http = FakeHttp()

    make_client(http).send_message(7, "hola")

    [req] = http.requests
    assert req["method"] == "POST"
    assert req["url"] == "http://chatwoot:3000/api/v1/accounts/3/conversations/7/messages"
    assert req["headers"] == {"api_access_token": "bot-token"}
    assert req["json"] == {"content": "hola", "message_type": "outgoing", "private": False}


def test_private_note_and_toggle_status() -> None:
    http = FakeHttp()
    client = make_client(http)

    client.add_private_note(7, "resumen")
    client.toggle_status(7, "open")

    assert http.requests[0]["json"]["private"] is True
    assert http.requests[1]["url"].endswith("/conversations/7/toggle_status")
    assert http.requests[1]["json"] == {"status": "open"}


def test_add_labels_keeps_existing_ones() -> None:
    http = FakeHttp([FakeResponse(data={"payload": ["vip"]}), FakeResponse(data={})])

    make_client(http).add_labels(7, ["urgente", "vip"])

    assert [r["method"] for r in http.requests] == ["GET", "POST"]
    assert http.requests[1]["json"] == {"labels": ["vip", "urgente"]}


def test_add_labels_skips_post_when_nothing_new() -> None:
    http = FakeHttp([FakeResponse(data={"payload": ["urgente"]})])

    make_client(http).add_labels(7, ["urgente"])

    assert len(http.requests) == 1


def test_history_and_contacts_use_the_user_token() -> None:
    http = FakeHttp([FakeResponse(data={"payload": [{"id": 1}]}), FakeResponse(data={})])
    client = make_client(http)

    assert client.get_messages(7, before=50) == [{"id": 1}]
    client.update_contact_attributes(9, {"verified": True})

    assert http.requests[0]["params"] == {"before": 50}
    assert http.requests[0]["headers"] == {"api_access_token": "user-token"}
    assert http.requests[1]["method"] == "PUT"
    assert http.requests[1]["url"].endswith("/accounts/3/contacts/9")
    assert http.requests[1]["json"] == {"custom_attributes": {"verified": True}}


def test_missing_user_token() -> None:
    client = make_client(FakeHttp(), api_token=None)

    assert not client.can_read_history
    with pytest.raises(ChatwootError, match="falta el token"):
        client.get_messages(7)


def test_http_and_network_errors() -> None:
    with pytest.raises(ChatwootError, match="HTTP 401"):
        make_client(FakeHttp([FakeResponse(status_code=401)])).send_message(7, "x")
    with pytest.raises(ChatwootError, match="ConnectionError"):
        make_client(FakeHttp(error=requests.ConnectionError())).send_message(7, "x")


def test_from_settings_requires_url_and_account(monkeypatch: pytest.MonkeyPatch) -> None:
    # The container's environment may define them; Settings reads it despite _env_file=None.
    for name in (
        "CHATWOOT_BASE_URL",
        "CHATWOOT_ACCOUNT_ID",
        "CHATWOOT_BOT_TOKEN",
        "CHATWOOT_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ChatwootError):
        ChatwootClient.from_settings(Settings(_env_file=None))
    client = ChatwootClient.from_settings(
        Settings(
            _env_file=None,
            chatwoot_base_url="http://chatwoot-rails:3000",
            chatwoot_account_id=1,
            chatwoot_bot_token="b",
        )
    )
    assert client.account_id == 1 and not client.can_read_history
