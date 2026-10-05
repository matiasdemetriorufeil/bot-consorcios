"""docker-compose.yml invariants that protect what gets exposed (no Docker needed)."""

from pathlib import Path
from typing import Any

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


def test_published_ports_stay_on_localhost() -> None:
    for name, service in services().items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), name
