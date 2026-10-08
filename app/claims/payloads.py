"""The payload of a claim's buttons: "clm1.<claim id>.<action>.<signature>", signed with HMAC-SHA256
over the claim, the action and who it is for (a provider's id, or a neighbor's phone). So a
payload someone makes up, or one sent to another provider or another phone, does nothing: the
signature does not match.

Actions: "ack" (Recibido), "decline" (No puedo atenderlo), "solved" (Ya está solucionado) for a
provider; "again" (Registrar reclamo) for a neighbor.

The secret: CLAIMS_PAYLOAD_SECRET, else ADMIN_SECRET_KEY, else WHATSAPP_APP_SECRET. Without any
of them a random one for this process is used (payloads sent before a restart stop working:
the provider is told the claim is no longer his, and the studio closes it from the panel).
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from functools import lru_cache

from app.config import Settings

logger = logging.getLogger(__name__)

PREFIX = "clm1"
ACTIONS = frozenset({"ack", "decline", "solved", "again"})
SIGNATURE_CHARS = 24


@dataclass(frozen=True)
class Payload:
    claim_id: int
    action: str


@lru_cache(maxsize=4)
def _fallback_secret(key: str) -> bytes:
    logger.warning("No secret for the claims' buttons: using one for this process only")
    return secrets.token_bytes(32)


def secret_of(settings: Settings) -> bytes:
    for value in (
        settings.claims_payload_secret,
        settings.admin_secret_key,
        settings.whatsapp_app_secret,
    ):
        if value is not None and value.get_secret_value():
            return value.get_secret_value().encode()
    return _fallback_secret("process")


def _signature(secret: bytes, claim_id: int, action: str, subject: str) -> str:
    message = f"{claim_id}:{action}:{subject}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:SIGNATURE_CHARS]


def provider_subject(provider_id: int) -> str:
    return f"p{provider_id}"


def sign(secret: bytes, claim_id: int, action: str, subject: str) -> str:
    if action not in ACTIONS:
        raise ValueError(action)
    return f"{PREFIX}.{claim_id}.{action}.{_signature(secret, claim_id, action, subject)}"


def read(secret: bytes, payload: str, subject: str) -> Payload | None:
    """The claim and action of a payload signed for this subject; None otherwise."""
    parts = (payload or "").split(".")
    if len(parts) != 4 or parts[0] != PREFIX or parts[2] not in ACTIONS:
        return None
    try:
        claim_id = int(parts[1])
    except ValueError:
        return None
    expected = _signature(secret, claim_id, parts[2], subject)
    if not hmac.compare_digest(expected, parts[3]):
        return None
    return Payload(claim_id, parts[2])


def is_claim_payload(payload: str) -> bool:
    return (payload or "").startswith(f"{PREFIX}.")
