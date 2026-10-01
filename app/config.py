from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables and `.env`."""

    # Empty values (e.g. CHATWOOT_ACCOUNT_ID=) fall back to the defaults.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

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

    chatwoot_base_url: str = ""
    chatwoot_api_token: SecretStr | None = None
    chatwoot_account_id: int | None = None
    chatwoot_webhook_secret: SecretStr | None = None

    # "console" (development: logs the email, including the code) or "smtp".
    email_backend: Literal["console", "smtp"] = "console"
    smtp_host: str = ""
    # 465 uses implicit TLS; any other port uses STARTTLS.
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = ""

    timezone: str = "America/Argentina/Cordoba"


@lru_cache
def get_settings() -> Settings:
    return Settings()
