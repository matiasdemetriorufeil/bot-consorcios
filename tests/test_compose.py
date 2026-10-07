"""docker-compose.yml invariants that protect what gets exposed (no Docker needed)."""

from pathlib import Path
from typing import Any

import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def services() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_there_is_no_chatwoot() -> None:
    assert not [name for name in services() if "chatwoot" in name]
    assert "chatwoot" not in COMPOSE.read_text(encoding="utf-8").lower()
    assert not (COMPOSE.parent / "chatwoot").exists()


def test_there_is_only_one_tunnel_profile() -> None:
    profiles = {p for service in services().values() for p in service.get("profiles", [])}
    assert profiles == {"tunnel-wa"}
    tunnels = [name for name, s in services().items() if "cloudflare" in s.get("image", "")]
    assert tunnels == ["cloudflared-wa"]


def test_published_ports_stay_on_localhost() -> None:
    for name, service in services().items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), name


CADDYFILE = COMPOSE.parent / "deploy" / "webhook-proxy" / "Caddyfile"


def test_whatsapp_tunnel_is_opt_in_and_only_reaches_the_proxy() -> None:
    tunnel = services()["cloudflared-wa"]

    assert tunnel["profiles"] == ["tunnel-wa"]
    command = tunnel["command"]
    assert command[command.index("--url") + 1] == "http://webhook-proxy:8080"
    assert "--no-autoupdate" in command
    assert not any("api" in part or "8000" in part for part in command)
    assert "ports" not in tunnel
    # A restart would silently change the quick tunnel URL that Meta has.
    assert tunnel["restart"] == "no"
    # Pinned: ${CLOUDFLARED_VERSION:-x.y.z}.
    image = tunnel["image"]
    assert image.startswith("cloudflare/cloudflared:") and ":-" in image and "latest" not in image


def test_webhook_proxy_publishes_only_the_whatsapp_webhook() -> None:
    proxy = services()["webhook-proxy"]

    assert proxy["profiles"] == ["tunnel-wa"]
    assert "ports" not in proxy
    assert proxy["image"].startswith("caddy:") and ":-" in proxy["image"]
    assert "./deploy/webhook-proxy/Caddyfile:/etc/caddy/Caddyfile:ro" in proxy["volumes"]
    caddyfile = CADDYFILE.read_text(encoding="utf-8")
    # One matcher, exactly the webhook path, and 404 for anything else.
    assert caddyfile.count("reverse_proxy") == 1
    assert "@webhook path /webhooks/whatsapp\n" in caddyfile
    assert "reverse_proxy api:8000" in caddyfile
    assert "respond 404" in caddyfile
    assert "/admin" not in caddyfile.split("{", 2)[-1]


def test_api_keeps_whatsapp_attachments_in_a_volume() -> None:
    assert "wa_media:/data/wa_media" in services()["api"]["volumes"]
