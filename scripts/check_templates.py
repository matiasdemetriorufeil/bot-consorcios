"""Check the claims' WhatsApp templates in Meta: status (APPROVED, PENDING, REJECTED...),
language, category, how many variables and which buttons, against what app.claims.notify
sends. Only reads (GET /{WHATSAPP_WABA_ID}/message_templates); never prints the token.

Usage (needs WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_WABA_ID):
    uv run python scripts/check_templates.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.whatsapp.client import WhatsAppClient, WhatsAppError  # noqa: E402

# name setting -> (variables, quick-reply buttons) app.claims.notify uses.
EXPECTED = {
    "claim_template_provider": (6, ["Recibido", "No puedo atenderlo"]),
    "claim_template_reminder": (3, ["Recibido", "Ya está solucionado"]),
    "claim_template_confirmed": (2, []),
    "claim_template_solved": (2, ["Registrar reclamo"]),
}


def main() -> int:
    settings = get_settings()
    if not settings.whatsapp_waba_id:
        print("Falta WHATSAPP_WABA_ID.")
        return 2
    try:
        client = WhatsAppClient.from_settings(settings)
        data = client._request(  # noqa: SLF001 - a read-only check
            "GET",
            f"{client.base_url}/{settings.whatsapp_waba_id}/message_templates",
            params={"fields": "name,status,language,category,components", "limit": 200},
        )
    except WhatsAppError as exc:
        print(f"No pude consultar Meta: {str(exc).replace(settings.whatsapp_waba_id, '<WABA>')}")
        return 2
    templates = data.get("data", [])
    language = settings.whatsapp_template_lang
    problems = 0
    for setting, (variables, buttons) in EXPECTED.items():
        name = getattr(settings, setting)
        found = [t for t in templates if t.get("name") == name]
        if not found:
            print(f"✗ {name}: no está en la cuenta")
            problems += 1
            continue
        template = next((t for t in found if t.get("language") == language), found[0])
        body = next((c for c in template.get("components", []) if c.get("type") == "BODY"), {}).get(
            "text", ""
        )
        count = len(set(re.findall(r"\{\{\s*(\w+)\s*\}\}", body)))
        titles = [
            b.get("text", "")
            for c in template.get("components", [])
            if c.get("type") == "BUTTONS"
            for b in c.get("buttons", [])
            if b.get("type") == "QUICK_REPLY"
        ]
        ok = (
            template.get("status") == "APPROVED"
            and template.get("language") == language
            and count == variables
            and titles == buttons
        )
        problems += not ok
        print(
            f"{'✓' if ok else '✗'} {name}: {template.get('status')} · {template.get('language')} · "
            f"{template.get('category')} · {count} variables (espero {variables}) · "
            f"botones {titles} (espero {buttons})"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
