"""Scripted fakes of BOTH provider SDK clients. The real provider classes are used, only
their SDK client is replaced, so conversion and parsing code runs for real."""

from dataclasses import dataclass, field
from itertools import count
from typing import Any

import anthropic
from google.genai import types

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.gemini_provider import GeminiProvider

PROVIDERS = ["anthropic", "gemini"]


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Call:
    name: str
    arguments: dict[str, Any]


Step = Say | Call | Exception

_ids = count(1)


def _anthropic_message(step: Say | Call) -> anthropic.types.Message:
    if isinstance(step, Say):
        content = [{"type": "text", "text": step.text}]
        stop = "end_turn"
    else:
        content = [
            {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": step.name,
             "input": step.arguments}
        ]  # fmt: skip
        stop = "tool_use"
    return anthropic.types.Message.model_validate(
        {
            "id": f"msg_{next(_ids)}",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 50,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )


def _gemini_response(step: Say | Call) -> types.GenerateContentResponse:
    if isinstance(step, Say):
        parts = [types.Part(text=step.text)]
    else:
        parts = [
            types.Part(
                function_call=types.FunctionCall(
                    id=f"fc_{next(_ids)}", name=step.name, args=step.arguments
                ),
                thought_signature=b"signature",
            )
        ]
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1000, candidates_token_count=40, thoughts_token_count=10
        ),
    )


@dataclass
class _Scripted:
    steps: list[Step]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def next(self, kwargs: dict[str, Any]) -> Any:
        self.requests.append(kwargs)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class _FakeAnthropicClient:
    def __init__(self, script: _Scripted) -> None:
        self.script = script
        self.messages = self

    def create(self, **kwargs: Any) -> anthropic.types.Message:
        return _anthropic_message(self.script.next(kwargs))


class _FakeGeminiClient:
    def __init__(self, script: _Scripted) -> None:
        self.script = script
        self.models = self

    def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        return _gemini_response(self.script.next(kwargs))


def scripted_provider(
    name: str, steps: list[Step]
) -> tuple[AnthropicProvider | GeminiProvider, _Scripted]:
    """A real provider whose SDK client answers the given steps in order."""
    script = _Scripted(list(steps))
    if name == "anthropic":
        provider: AnthropicProvider | GeminiProvider = AnthropicProvider(
            "test-key", "claude-haiku-4-5"
        )
        provider._client = _FakeAnthropicClient(script)  # type: ignore[assignment]
    else:
        provider = GeminiProvider("test-key", "gemini-3.8-flash", thinking_level="low")
        provider._client = _FakeGeminiClient(script)  # type: ignore[assignment]
    return provider, script


def last_tool_result(name: str, request: dict[str, Any]) -> str:
    """The text of the last tool result sent to the model in a request, as a string."""
    if name == "anthropic":
        block = request["messages"][-1]["content"][-1]
        assert block["type"] == "tool_result"
        return block["content"]
    part = request["contents"][-1].parts[-1]
    assert part.function_response is not None
    return str(part.function_response.response)


def last_user_text(name: str, request: dict[str, Any]) -> str:
    if name == "anthropic":
        return request["messages"][-1]["content"][0]["text"]
    return request["contents"][-1].parts[0].text


def system_of(name: str, request: dict[str, Any]) -> str:
    if name == "anthropic":
        return request["system"][0]["text"]
    return request["config"].system_instruction
