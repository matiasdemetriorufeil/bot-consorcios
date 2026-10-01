"""Gemini through the official `google-genai` SDK (generate_content with manual function
calling: automatic function calling is disabled, the agent runs the tools).

The model's own content is sent back as is (AssistantMessage.raw): Gemini 3 needs its thought
signatures back inside the original parts, and function responses carry the call id.

Caching: implicit caching is automatic on Gemini 2.5+ (no code needed) and shows up as
cached_content_token_count. It needs >= 4096 prompt tokens on Gemini 3.x Flash. Explicit
caching (client.caches.create with system + tools) is not used: same minimum, plus TTL
management and hourly storage cost, not worth it for a short system prompt.
"""

import uuid
from typing import Any

from google import genai
from google.genai import errors, types

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

# Includes thinking tokens on Gemini.
MAX_OUTPUT_TOKENS = 2048
# Ids we invent when Gemini returns a call without one; never sent back to the API.
_LOCAL_ID_PREFIX = "local-"


def to_gemini_tools(tools: list[ToolSpec]) -> list[types.Tool]:
    if not tools:
        return []
    declarations = [
        types.FunctionDeclaration(
            name=t.name, description=t.description, parameters_json_schema=t.parameters
        )
        for t in tools
    ]
    return [types.Tool(function_declarations=declarations)]


def _content(message: Message) -> types.Content:
    if isinstance(message, UserMessage):
        return types.Content(role="user", parts=[types.Part(text=message.text)])
    if isinstance(message, ToolResultsMessage):
        parts = [
            types.Part(
                function_response=types.FunctionResponse(
                    id=None if r.call_id.startswith(_LOCAL_ID_PREFIX) else r.call_id,
                    name=r.name,
                    response=r.content,
                )
            )
            for r in message.results
        ]
        return types.Content(role="user", parts=parts)
    if message.provider == "gemini" and isinstance(message.raw, types.Content):
        return message.raw
    parts = [types.Part(text=message.text)] if message.text else []
    parts += [
        types.Part(
            function_call=types.FunctionCall(
                id=None if c.id.startswith(_LOCAL_ID_PREFIX) else c.id,
                name=c.name,
                args=c.arguments,
            )
        )
        for c in message.tool_calls
    ]
    return types.Content(role="model", parts=parts)


def to_gemini_contents(messages: list[Message]) -> list[types.Content]:
    """Consecutive contents of the same role are merged."""
    contents: list[types.Content] = []
    for message in messages:
        content = _content(message)
        if not content.parts:
            continue
        if contents and contents[-1].role == content.role:
            merged = list(contents[-1].parts or []) + list(content.parts)
            contents[-1] = types.Content(role=content.role, parts=merged)
        else:
            contents.append(content)
    return contents


def parse_gemini_response(response: types.GenerateContentResponse) -> LLMResponse:
    if not response.candidates or response.candidates[0].content is None:
        reason = response.prompt_feedback.block_reason if response.prompt_feedback else None
        raise LLMError(f"gemini: respuesta vacía ({reason})")
    content = response.candidates[0].content
    texts: list[str] = []
    calls: list[ToolCall] = []
    for part in content.parts or []:
        if part.function_call is not None:
            fc = part.function_call
            calls.append(
                ToolCall(
                    id=fc.id or f"{_LOCAL_ID_PREFIX}{uuid.uuid4().hex[:12]}",
                    name=fc.name or "",
                    arguments=dict(fc.args or {}),
                )
            )
        elif part.text and not part.thought:
            texts.append(part.text)
    meta = response.usage_metadata
    prompt = (meta.prompt_token_count or 0) if meta else 0
    cached = (meta.cached_content_token_count or 0) if meta else 0
    output = ((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)) if meta else 0
    usage = Usage(input_tokens=prompt - cached, output_tokens=output, cache_read_tokens=cached)
    message = AssistantMessage(
        text="".join(texts).strip(), tool_calls=tuple(calls), raw=content, provider="gemini"
    )
    return LLMResponse(message=message, usage=usage)


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 30,
        thinking_level: str = "",
    ) -> None:
        self.model = model
        self._thinking_level = thinking_level
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000))
        )

    def build_config(self, system: str, tools: list[ToolSpec]) -> types.GenerateContentConfig:
        extra: dict[str, Any] = {}
        if self._thinking_level:
            extra["thinking_config"] = types.ThinkingConfig(
                thinking_level=self._thinking_level.upper()
            )
        return types.GenerateContentConfig(
            system_instruction=system,
            tools=to_gemini_tools(tools),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=MAX_OUTPUT_TOKENS,
            **extra,
        )

    def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=to_gemini_contents(messages),
                config=self.build_config(system, tools),
            )
        except errors.APIError as exc:
            raise LLMError(f"gemini: {type(exc).__name__} {exc.code}") from exc
        return parse_gemini_response(response)
