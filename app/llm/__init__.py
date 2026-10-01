"""LLM provider layer: Gemini or Claude chosen by LLM_PROVIDER / LLM_MODEL, no code changes."""

from app.config import Settings, get_settings
from app.llm.base import (
    DEFAULT_MODELS,
    AssistantMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    Prices,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    Usage,
    UserMessage,
)

__all__ = [
    "DEFAULT_MODELS",
    "AssistantMessage",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "Prices",
    "ToolCall",
    "ToolResult",
    "ToolResultsMessage",
    "ToolSpec",
    "Usage",
    "UserMessage",
    "get_prices",
    "get_provider",
]


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    model = settings.llm_model or DEFAULT_MODELS[settings.llm_provider]
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        if settings.anthropic_api_key is None:
            raise LLMError("falta ANTHROPIC_API_KEY")
        return AnthropicProvider(
            settings.anthropic_api_key.get_secret_value(), model, settings.llm_timeout_seconds
        )
    from app.llm.gemini_provider import GeminiProvider

    if settings.gemini_api_key is None:
        raise LLMError("falta GEMINI_API_KEY")
    return GeminiProvider(
        settings.gemini_api_key.get_secret_value(),
        model,
        settings.llm_timeout_seconds,
        settings.gemini_thinking_level,
    )


def get_prices(settings: Settings | None = None) -> Prices:
    settings = settings or get_settings()
    return Prices(
        input=settings.llm_price_input,
        output=settings.llm_price_output,
        cache_read=settings.llm_price_cache_read,
        cache_write=settings.llm_price_cache_write,
    )
