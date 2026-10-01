"""Claude through the official `anthropic` SDK (Messages API with tool use).

Prompt caching: cache_control on the last tool and on the system block (the cached prefix is
tools -> system -> messages) and on the last message, so rounds of the same tool loop reuse
the conversation too. Below the model's minimum cacheable length (4096 tokens for Haiku 4.5)
the API simply does not cache: cache_* usage stays at 0.
"""

import json
from typing import Any

import anthropic

from app.llm.base import (
    AssistantMessage,
    LLMError,
    LLMResponse,
    Message,
    ToolCall,
    ToolResultsMessage,
    ToolSpec,
    Usage,
    UserMessage,
)

MAX_OUTPUT_TOKENS = 1024
_CACHE = {"type": "ephemeral"}


def to_anthropic_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    converted = [
        {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
    ]
    if converted:
        converted[-1]["cache_control"] = _CACHE
    return converted


def _blocks(message: Message) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(message, UserMessage):
        return "user", [{"type": "text", "text": message.text}]
    if isinstance(message, ToolResultsMessage):
        return "user", [
            {
                "type": "tool_result",
                "tool_use_id": r.call_id,
                "content": json.dumps(r.content, ensure_ascii=False),
            }
            for r in message.results
        ]
    blocks: list[dict[str, Any]] = []
    if message.text:
        blocks.append({"type": "text", "text": message.text})
    for call in message.tool_calls:
        blocks.append(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        )
    return "assistant", blocks


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Consecutive messages of the same role are merged (the API expects alternation)."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        role, blocks = _blocks(message)
        if not blocks:
            continue
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    if converted:
        last = converted[-1]["content"][-1]
        converted[-1]["content"][-1] = {**last, "cache_control": _CACHE}
    return converted


def parse_anthropic_response(response: Any) -> LLMResponse:
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in response.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
    u = response.usage
    usage = Usage(
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_read_tokens=u.cache_read_input_tokens or 0,
        cache_write_tokens=u.cache_creation_input_tokens or 0,
    )
    message = AssistantMessage(
        text="\n".join(t for t in texts if t).strip(),
        tool_calls=tuple(calls),
        provider="anthropic",
    )
    return LLMResponse(message=message, usage=usage)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30) -> None:
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=2)

    def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=[{"type": "text", "text": system, "cache_control": _CACHE}],
                tools=to_anthropic_tools(tools),
                messages=to_anthropic_messages(messages),
            )
        except anthropic.APIError as exc:
            raise LLMError(f"anthropic: {type(exc).__name__}") from exc
        return parse_anthropic_response(response)
