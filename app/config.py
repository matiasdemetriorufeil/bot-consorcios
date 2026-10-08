from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables and `.env`."""

    # Empty values (e.g. LLM_MODEL=) fall back to the defaults.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    # "development" enables the panel's test chat ("Chat de prueba"). Anything else is treated
    # as production.
    app_env: Literal["development", "production"] = "production"
    # Level of the app's own logs (stdout): DEBUG also shows the ignored webhooks.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/bot_consorcios"

    llm_provider: Literal["gemini", "anthropic"] = "gemini"
    llm_model: str = ""
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    llm_timeout_seconds: float = 30
    # Gemini 3.x: "low" | "medium" | "high" (some also "minimal"). Empty: model default.
    gemini_thinking_level: str = "low"
    # USD per million tokens of LLM_MODEL, to estimate costs in bot_events. Empty: no estimate.
    llm_price_input: float | None = None
    llm_price_output: float | None = None
    llm_price_cache_read: float | None = None
    llm_price_cache_write: float | None = None

    # Office hours (Córdoba time) the bot mentions when handing off. Weekdays: 0 = Monday.
    office_hours_start: str = "09:00"
    office_hours_end: str = "17:00"
    office_weekdays: list[int] = [0, 1, 2, 3, 4]
    # Who to call for an urgency outside office hours (said by the bot after the handoff
    # notice, e.g. "llamá al encargado, Juan, al 351..."). Empty: it suggests the caretaker.
    emergency_contact_text: str = ""
    # Self-service web page (download expensas, receipts). get_debt offers it. Empty: never.
    autogestion_url: str = ""
    # How to use the Siro payment code, said with the debt (copied verbatim by the bot).
    payment_code_how_to: str = (
        "Con este código podés pagar por Pago Mis Cuentas o Red Link (home banking o cajero)."
    )
    # These texts can also be set in the admin panel (Configuración general), which wins
    # over the values here when not empty: see app.bot.bot_config.

    consorplus_base_url: str = "https://consorplus.drufeilccios.com.ar/"
    consorplus_user: str = ""
    consorplus_password: SecretStr | None = None
    # Minimum seconds between two requests to ConsorPlus (production system: be gentle).
    consorplus_min_request_interval: float = 0.5
    # Known unit checked daily to detect changes in ConsorPlus pages (combo values).
    canary_building: str = ""
    canary_unit: str = ""
    # refresh_unit reuses a live snapshot younger than this instead of querying ConsorPlus.
    # 0 disables the cache.
    live_cache_minutes: float = 10
    # Minutes without requests after which the ConsorPlus session is taken as expired (the
    # server's ASP.NET session lasts ~20). The bot then logs in first. 0 disables it.
    consorplus_session_idle_minutes: float = 15

    # WhatsApp Cloud API: the bot's only channel (app.whatsapp). Token of a system user with
    # whatsapp_business_*. In development, without them, only the test chat works.
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str = ""
    whatsapp_waba_id: str = ""
    # The Meta app's secret: signs every webhook (X-Hub-Signature-256). Without it, all 503.
    whatsapp_app_secret: SecretStr | None = None
    # Chosen by us and typed in Meta's webhook setup (the GET verification).
    whatsapp_verify_token: SecretStr | None = None
    # Graph API version (v26.0: the current one in October 2026).
    whatsapp_graph_version: str = "v26.0"
    whatsapp_timeout_seconds: float = 15
    # Incoming attachments are downloaded here (a volume of the api), and the largest allowed.
    whatsapp_media_dir: str = "/data/wa_media"
    whatsapp_media_max_bytes: int = 10 * 1024 * 1024
    # On startup, unanswered messages younger than this are answered; older ones are not.
    whatsapp_recovery_minutes: float = 15
    # ⚠️ DEVELOPMENT ONLY (APP_ENV=development; ignored otherwise). "from:to,..." digits that
    # rewrite the recipient for Meta's test number with Argentine numbers (549... -> 54...15...).
    whatsapp_dev_recipient_rewrite: str = ""
    # The approved templates of the claims (Meta: WhatsApp Manager > Plantillas) and their
    # language. Their variables and buttons are in app.claims.notify.
    whatsapp_template_lang: str = "es_AR"
    claim_template_provider: str = "reclamo_nuevo_proveedor"
    claim_template_reminder: str = "reclamo_recordatorio_proveedor"
    claim_template_confirmed: str = "reclamo_confirmado_vecino"
    claim_template_solved: str = "reclamo_solucionado_vecino"
    # Signs the payload of the claims' buttons (a made-up one never closes a claim). Empty:
    # ADMIN_SECRET_KEY is used.
    claims_payload_secret: SecretStr | None = None

    # "console" (development: logs the email, including the code) or "smtp".
    email_backend: Literal["console", "smtp"] = "console"
    smtp_host: str = ""
    # 465 uses implicit TLS; any other port uses STARTTLS.
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = ""
    # Emails or domains that never receive verification codes (e.g. the studio's own
    # address, loaded in ConsorPlus for owners without email). Comma separated:
    # "a@b.com, otrodominio.com". A unit with only these is treated as "sin email".
    verification_email_exclude: str = "estudiodiegorufeil@gmail.com"

    timezone: str = "America/Argentina/Cordoba"

    # Admin panel (/admin). The .env user is the rescue admin (the others are created in the
    # panel, app.admin.users). Without ADMIN_SECRET_KEY every login is rejected.
    admin_username: str = ""
    # Generate with: uv run python scripts/hash_admin_password.py (never the plain password).
    admin_password_hash: SecretStr | None = None
    # Signs the panel's session cookie.
    admin_secret_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
