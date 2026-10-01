"""Provider-neutral types for the conversational agent.

Tools are declared ONCE (ToolSpec: name, description, JSON Schema) and each provider converts
them to its own format. A conversation is a list of UserMessage / AssistantMessage /
ToolResultsMessage; each provider converts it to its own wire format.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    # Stable Flash model listed on ai.google.dev/gemini-api/docs/models (checked 2026-09-30).
    "gemini": "gemini-3.8-flash",
}


class LLMError(Exception):
    """The provider API failed (network, quota, invalid request, blocked answer...)."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema of an object


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: dict[str, Any]  # JSON-serializable


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    # The provider's own representation of this turn (e.g. Gemini content with thought
    # signatures). Only reused by the provider that produced it.
    raw: Any = field(default=None, compare=False, repr=False)
    provider: str | None = None


@dataclass(frozen=True)
class ToolResultsMessage:
    results: tuple[ToolResult, ...]


Message = UserMessage | AssistantMessage | ToolResultsMessage


@dataclass(frozen=True)
class Usage:
    """Tokens of ONE API call. input_tokens excludes cache reads and writes; output_tokens
    includes thinking tokens (both are billed as output)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    message: AssistantMessage
    usage: Usage


class LLMProvider(Protocol):
    name: str
    model: str

    def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        """One model call. Raises LLMError when the API fails."""
        ...


@dataclass(frozen=True)
class Prices:
    """USD per million tokens. None = unknown (no cost estimate)."""

    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None

    def estimate(self, usage: Usage) -> float | None:
        if self.input is None or self.output is None:
            return None
        cache_read = self.cache_read if self.cache_read is not None else self.input
        cache_write = self.cache_write if self.cache_write is not None else self.input
        total = (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_read_tokens * cache_read
            + usage.cache_write_tokens * cache_write
        )
        return total / 1_000_000
