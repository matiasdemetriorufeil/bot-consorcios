from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/bot_consorcios"

    llm_provider: Literal["gemini", "anthropic"] = "gemini"
    llm_model: str = ""
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    consorplus_base_url: str = "https://consorplus.drufeilccios.com.ar/"
    consorplus_user: str = ""
    consorplus_password: SecretStr | None = None

    chatwoot_base_url: str = ""
    chatwoot_api_token: SecretStr | None = None
    chatwoot_account_id: int | None = None
    chatwoot_webhook_secret: SecretStr | None = None

    timezone: str = "America/Argentina/Cordoba"


@lru_cache
def get_settings() -> Settings:
    return Settings()
