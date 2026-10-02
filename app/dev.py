"""Development-only pages (APP_ENV=development). Elsewhere they answer 404."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from app.config import Settings, get_settings

router = APIRouter(prefix="/dev", include_in_schema=False)

_CHAT_PAGE = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prueba del bot</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto;
           padding: 0 1rem; line-height: 1.5; color: #222; background: #fff; }}
    code {{ background: #f2f2f2; padding: 0 .25rem; }}
  </style>
</head>
<body>
  <h1>Prueba del bot (desarrollo)</h1>
  <p>Abrí el globo de chat de abajo a la derecha y escribí como si fueras un propietario.
     Las conversaciones aparecen en Chatwoot (<a href="{base_url_html}">{base_url_html}</a>).</p>
  <p>{warning}</p>
  <p>Cómo simular un propietario conocido: <code>docs/chatwoot.md</code>.</p>
  <script>
    (function (d, t) {{
      var BASE_URL = {base_url_js};
      var g = d.createElement(t), s = d.getElementsByTagName(t)[0];
      g.src = BASE_URL + "/packs/js/sdk.js";
      g.async = true;
      s.parentNode.insertBefore(g, s);
      g.onload = function () {{
        window.chatwootSDK.run({{ websiteToken: {token_js}, baseUrl: BASE_URL }});
      }};
    }})(document, "script");
  </script>
</body>
</html>
"""


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _js(value: str) -> str:
    """A JS string literal that stays inert inside <script> (no "</script>" breakout)."""
    return json.dumps(value).replace("</", "<\\/")


@router.get("/chat", response_class=HTMLResponse)
def dev_chat(settings: Annotated[Settings, Depends(get_settings)]) -> HTMLResponse:
    if settings.app_env != "development":
        raise HTTPException(404)
    base_url = settings.chatwoot_frontend_url.rstrip("/")
    token = settings.chatwoot_website_token
    warning = (
        ""
        if token
        else "<strong>Falta CHATWOOT_WEBSITE_TOKEN en .env: el chat no va a cargar.</strong>"
    )
    return HTMLResponse(
        _CHAT_PAGE.format(
            base_url_html=_html_escape(base_url),
            base_url_js=_js(base_url),
            token_js=_js(token),
            warning=warning,
        )
    )
