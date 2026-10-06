"""docker-compose.yml invariants that protect what gets exposed (no Docker needed)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def services() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_tunnel_is_opt_in_and_only_publishes_chatwoot() -> None:
    tunnel = services()["cloudflared"]

    # Never started by `docker compose up` nor with the chatwoot profile alone.
    assert tunnel["profiles"] == ["tunnel"]
    command = tunnel["command"]
    assert command[command.index("--url") + 1] == "http://chatwoot-rails:3000"
    assert "--no-autoupdate" in command
    assert not any("api" in part or "8000" in part for part in command)
    assert "ports" not in tunnel
    # A restart would silently change the quick tunnel URL that Meta has.
    assert tunnel["restart"] == "no"


def test_tunnel_image_is_pinned() -> None:
    image = services()["cloudflared"]["image"]
    assert image.startswith("cloudflare/cloudflared:")
    assert "latest" not in image and ":-" in image  # ${CLOUDFLARED_VERSION:-x.y.z}


@pytest.mark.parametrize("name", ["chatwoot-rails", "chatwoot-sidekiq"])
def test_chatwoot_frontend_url_comes_from_env(name: str) -> None:
    # Chatwoot registers the WhatsApp webhook in Meta as FRONTEND_URL/webhooks/whatsapp/...:
    # it must follow CHATWOOT_FRONTEND_URL (the tunnel) and fall back to localhost.
    service = services()[name]
    assert service["environment"]["FRONTEND_URL"] == (
        "${CHATWOOT_FRONTEND_URL:-http://localhost:3000}"
    )
    assert "env_file" not in service  # nothing else may override it


REWRITE_INITIALIZER = "zz_dev_whatsapp_recipient_rewrite.rb"


@pytest.mark.parametrize("name", ["chatwoot-rails", "chatwoot-sidekiq"])
def test_chatwoot_mounts_dev_recipient_rewrite_read_only(name: str) -> None:
    # Rails sends from both processes (web replies and Sidekiq jobs): both need the patch.
    mount = (
        f"./chatwoot/initializers/{REWRITE_INITIALIZER}"
        f":/app/config/initializers/{REWRITE_INITIALIZER}:ro"
    )
    assert mount in services()[name]["volumes"]
    assert (COMPOSE.parent / "chatwoot" / "initializers" / REWRITE_INITIALIZER).is_file()


@pytest.mark.parametrize("name", ["chatwoot-rails", "chatwoot-sidekiq"])
def test_chatwoot_dev_recipient_rewrite_defaults_to_empty(name: str) -> None:
    # Empty = the initializer does nothing. No real numbers in the repo.
    environment = services()[name]["environment"]
    assert environment["CHATWOOT_DEV_WA_RECIPIENT_REWRITE"] == (
        "${CHATWOOT_DEV_WA_RECIPIENT_REWRITE:-}"
    )


def test_published_ports_stay_on_localhost() -> None:
    for name, service in services().items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), name


LOCALE_OVERRIDES = "zz_bot_overrides.yml"


@pytest.mark.parametrize("name", ["chatwoot-rails", "chatwoot-sidekiq"])
def test_chatwoot_mounts_the_spanish_list_button_label(name: str) -> None:
    # Sidekiq sends WhatsApp lists, with the label of the button that opens them.
    mount = f"./chatwoot/locales/{LOCALE_OVERRIDES}:/app/config/locales/{LOCALE_OVERRIDES}:ro"
    assert mount in services()[name]["volumes"]
    overrides = yaml.safe_load(
        (COMPOSE.parent / "chatwoot" / "locales" / LOCALE_OVERRIDES).read_text(encoding="utf-8")
    )
    for locale in ("en", "es"):  # Sidekiq uses the default locale (en)
        whatsapp = overrides[locale]["conversations"]["messages"]["whatsapp"]
        assert whatsapp == {"list_button_label": "Ver opciones"}
