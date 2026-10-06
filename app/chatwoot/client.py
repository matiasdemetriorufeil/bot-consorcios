"""Chatwoot Application API client (v4.18), with two tokens:

- bot token (the Agent Bot's access token): everything that writes to the conversation, so
  messages show up as sent by the bot and toggle_status pending -> open is Chatwoot's own
  "bot handoff". Chatwoot only lets bots use conversations show/toggle_status/custom
  attributes, messages create and labels index/create.
- api token (a user's access token): reading messages (history) and updating contacts, which
  bots cannot do.

Conversation ids are the display ids Chatwoot shows in the panel and sends in webhooks.
"""

import logging
from collections.abc import Sequence
from typing import Any

import requests

from app.bot.choices import problems
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ChatwootError(Exception):
    """The Chatwoot API failed or is not configured."""


class ChatwootClient:
    def __init__(
        self,
        base_url: str,
        account_id: int,
        *,
        bot_token: str | None,
        api_token: str | None,
        timeout: float = 10,
        http: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self._bot_token = bot_token
        self._api_token = api_token
        self.timeout = timeout
        self._http = http or requests.Session()

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "ChatwootClient":
        s = settings or get_settings()
        if not s.chatwoot_base_url or s.chatwoot_account_id is None:
            raise ChatwootError("faltan CHATWOOT_BASE_URL o CHATWOOT_ACCOUNT_ID")
        return cls(
            s.chatwoot_base_url,
            s.chatwoot_account_id,
            bot_token=s.chatwoot_bot_token.get_secret_value() if s.chatwoot_bot_token else None,
            api_token=s.chatwoot_api_token.get_secret_value() if s.chatwoot_api_token else None,
            timeout=s.chatwoot_timeout_seconds,
        )

    @property
    def can_read_history(self) -> bool:
        return bool(self._api_token)

    def _request(
        self, method: str, path: str, *, token: str | None, **kwargs: Any
    ) -> dict[str, Any]:
        if not token:
            raise ChatwootError(f"falta el token de Chatwoot para {method} {path}")
        url = f"{self.base_url}/api/v1/accounts/{self.account_id}{path}"
        try:
            response = self._http.request(
                method, url, headers={"api_access_token": token}, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise ChatwootError(f"{method} {path}: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ChatwootError(f"{method} {path}: HTTP {response.status_code}")
        return response.json() if response.content else {}

    def _bot(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._request(method, path, token=self._bot_token, **kwargs)

    def _user(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._request(method, path, token=self._api_token, **kwargs)

    # --- Bot token ------------------------------------------------------------------------

    def get_conversation(self, conversation_id: int) -> dict[str, Any]:
        return self._bot("GET", f"/conversations/{conversation_id}")

    def send_message(self, conversation_id: int, content: str, *, private: bool = False) -> None:
        self._bot(
            "POST",
            f"/conversations/{conversation_id}/messages",
            json={"content": content, "message_type": "outgoing", "private": private},
        )

    def send_choices(
        self, conversation_id: int, text: str, options: Sequence[tuple[str, str]]
    ) -> None:
        """An "input_select" message: Chatwoot sends it to WhatsApp Cloud as reply buttons (up
        to 3) or a list (more), and the web widget shows the options. (title, value) pairs;
        titles must fit WhatsApp's limits (app.bot.choices): Chatwoot does not cut them and
        Meta rejects the message later, when this call already returned. ValueError if not."""
        titles = [title for title, _ in options]
        if found := problems(text, titles):
            raise ValueError("; ".join(found))
        self._bot(
            "POST",
            f"/conversations/{conversation_id}/messages",
            json={
                "content": text,
                "content_type": "input_select",
                "content_attributes": {
                    "items": [{"title": title, "value": value} for title, value in options]
                },
                "message_type": "outgoing",
                "private": False,
            },
        )

    def add_private_note(self, conversation_id: int, content: str) -> None:
        self.send_message(conversation_id, content, private=True)

    def toggle_status(self, conversation_id: int, status: str) -> None:
        self._bot(
            "POST", f"/conversations/{conversation_id}/toggle_status", json={"status": status}
        )

    def add_labels(self, conversation_id: int, labels: list[str]) -> None:
        """Adds to the existing labels (Chatwoot's POST replaces the whole list)."""
        path = f"/conversations/{conversation_id}/labels"
        current = self._bot("GET", path).get("payload", [])
        merged = list(dict.fromkeys([*current, *labels]))
        if merged != current:
            self._bot("POST", path, json={"labels": merged})

    # --- User token -----------------------------------------------------------------------

    def get_messages(self, conversation_id: int, *, before: int | None = None) -> list[dict]:
        """The 20 messages before `before` (message id), oldest first, notes included."""
        params = {"before": before} if before is not None else {}
        data = self._user("GET", f"/conversations/{conversation_id}/messages", params=params)
        return list(data.get("payload", []))

    def update_contact_attributes(self, contact_id: int, attributes: dict[str, Any]) -> None:
        """Chatwoot merges custom_attributes: other attributes are kept."""
        self._user("PUT", f"/contacts/{contact_id}", json={"custom_attributes": attributes})
