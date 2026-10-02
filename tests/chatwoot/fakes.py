"""In-memory stand-in for ChatwootClient and invented webhook payloads."""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.chatwoot.client import ChatwootError

WEB_INBOX = 1
WHATSAPP_INBOX = 2
ACCOUNT_ID = 1
SECRET = "test-webhook-secret"


@dataclass
class FakeChatwoot:
    """Records every call. `statuses` answers get_conversation in order (the last one
    repeats), so a test can simulate an operator taking the conversation midway."""

    statuses: list[str] = field(default_factory=lambda: ["pending"])
    history: list[dict[str, Any]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    can_read_history: bool = True
    fail_on: set[str] = field(default_factory=set)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def _call(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.fail_on:
            raise ChatwootError(f"{name} falló")

    def get_conversation(self, conversation_id: int) -> dict[str, Any]:
        self._call("get_conversation", conversation_id)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"id": conversation_id, "status": status}

    def send_message(self, conversation_id: int, content: str, *, private: bool = False) -> None:
        self._call("note" if private else "send_message", conversation_id, content)

    def add_private_note(self, conversation_id: int, content: str) -> None:
        self.send_message(conversation_id, content, private=True)

    def toggle_status(self, conversation_id: int, status: str) -> None:
        self._call("toggle_status", conversation_id, status)
        self.statuses = [status]

    def add_labels(self, conversation_id: int, labels: list[str]) -> None:
        self._call("add_labels", conversation_id, labels)
        self.labels = list(dict.fromkeys([*self.labels, *labels]))

    def get_messages(self, conversation_id: int, *, before: int | None = None) -> list[dict]:
        self._call("get_messages", conversation_id, before)
        return [m for m in self.history if before is None or m["id"] < before]

    def update_contact_attributes(self, contact_id: int, attributes: dict[str, Any]) -> None:
        self._call("update_contact_attributes", contact_id, attributes)

    # --- Helpers for assertions -------------------------------------------------------------

    def names(self) -> list[str]:
        return [name for name, _ in self.calls if name != "get_conversation"]

    def sent(self) -> list[str]:
        return [args[1] for name, args in self.calls if name == "send_message"]

    def notes(self) -> list[str]:
        return [args[1] for name, args in self.calls if name == "note"]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for n, args in self.calls if n == name]


def message_payload(
    *,
    message_id: int = 101,
    conversation_id: int = 7,
    inbox_id: int = WEB_INBOX,
    status: str = "pending",
    content: str | None = "hola",
    message_type: str = "incoming",
    private: bool = False,
    event: str = "message_created",
    content_type: str = "text",
    attachments: tuple[str, ...] = (),
    contact_id: int = 55,
    phone: str | None = None,
    custom_attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A message_created payload shaped like Chatwoot v4.18's (invented data)."""
    contact = {
        "id": contact_id,
        "name": "Contacto de Prueba",
        "phone_number": phone,
        "email": None,
        "custom_attributes": custom_attributes or {},
        "additional_attributes": {},
        "identifier": None,
        "thumbnail": "",
        "blocked": False,
        "account": {"id": ACCOUNT_ID, "name": "Cuenta de prueba"},
    }
    payload: dict[str, Any] = {
        "event": event,
        "id": message_id,
        "content": content,
        "content_type": content_type,
        "content_attributes": {},
        "additional_attributes": {},
        "message_type": message_type,
        "private": private,
        "created_at": "2026-09-30T14:00:00.000Z",
        "source_id": None,
        "account": {"id": ACCOUNT_ID, "name": "Cuenta de prueba"},
        "inbox": {"id": inbox_id, "name": "Bandeja de prueba"},
        "sender": contact if message_type == "incoming" else {"id": 1, "type": "agent_bot"},
        "conversation": {
            "id": conversation_id,
            "inbox_id": inbox_id,
            "status": status,
            "channel": "Channel::WebWidget",
            "labels": [],
            "meta": {"sender": {**contact, "type": "contact"}, "assignee": None},
            "messages": [],
            "custom_attributes": {},
        },
    }
    if attachments:
        payload["attachments"] = [
            {"id": i, "message_id": message_id, "file_type": t, "account_id": ACCOUNT_ID}
            for i, t in enumerate(attachments, 1)
        ]
    return payload


def history_message(
    message_id: int, content: str, message_type: int, *, private: bool = False
) -> dict[str, Any]:
    """A message as GET .../messages returns it (message_type is an integer there)."""
    return {
        "id": message_id,
        "content": content,
        "message_type": message_type,
        "content_type": "text",
        "private": private,
        "created_at": 1_790_000_000 + message_id,
    }


def signed_headers(body: bytes, secret: str = SECRET, timestamp: int | None = None) -> dict:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Chatwoot-Timestamp": ts,
        "X-Chatwoot-Signature": f"sha256={digest}",
    }


def body_of(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()
