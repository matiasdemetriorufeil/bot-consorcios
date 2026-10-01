"""Provider layer: tool/message conversion to each format, parsing, usage and errors."""

import anthropic
import httpx2
import pytest
from google.genai import errors, types

from app.bot.tools import TOOLS
from app.config import Settings
from app.llm import (
    AssistantMessage,
    LLMError,
    Prices,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    ToolSpec,
    Usage,
    UserMessage,
    get_provider,
)
from app.llm.anthropic_provider import (
    parse_anthropic_response,
    to_anthropic_messages,
    to_anthropic_tools,
)
from app.llm.gemini_provider import parse_gemini_response, to_gemini_contents, to_gemini_tools
from tests.llm.fakes import PROVIDERS, Call, Say, scripted_provider

SPEC = ToolSpec(
    name="get_debt",
    description="Deuda de una unidad.",
    parameters={
        "type": "object",
        "properties": {"unit_id": {"type": "integer"}},
        "required": ["unit_id"],
        "additionalProperties": False,
    },
)
OTHER = ToolSpec(name="ping", description="Ping.", parameters={"type": "object", "properties": {}})

CONVERSATION = [
    UserMessage("¿Cuánto debo?"),
    AssistantMessage(
        text="Me fijo.",
        tool_calls=(ToolCall(id="call_1", name="get_debt", arguments={"unit_id": 7}),),
    ),
    ToolResultsMessage((ToolResult("call_1", "get_debt", {"total_debt": "$1.000,00"}),)),
    AssistantMessage(text="Debés *$1.000,00*."),
    UserMessage("Gracias"),
]


# --- Tools --------------------------------------------------------------------------------


def test_anthropic_tools_format_and_cache_breakpoint_on_last() -> None:
    converted = to_anthropic_tools([OTHER, SPEC])
    assert converted[1] == {
        "name": "get_debt",
        "description": "Deuda de una unidad.",
        "input_schema": SPEC.parameters,
        "cache_control": {"type": "ephemeral"},
    }
    assert "cache_control" not in converted[0]


def test_gemini_tools_format() -> None:
    [tool] = to_gemini_tools([OTHER, SPEC])
    declarations = tool.function_declarations
    assert [d.name for d in declarations] == ["ping", "get_debt"]
    assert declarations[1].description == "Deuda de una unidad."
    assert declarations[1].parameters_json_schema == SPEC.parameters
    assert declarations[1].parameters is None


def test_the_real_tools_convert_to_both_formats() -> None:
    assert len(to_anthropic_tools(TOOLS)) == len(TOOLS)
    assert len(to_gemini_tools(TOOLS)[0].function_declarations) == len(TOOLS)


def test_no_tool_accepts_a_phone() -> None:
    for spec in TOOLS:
        props = spec.parameters["properties"]
        assert not any("phone" in p or "tel" in p for p in props), spec.name
        assert spec.parameters["additionalProperties"] is False, spec.name


# --- Messages -----------------------------------------------------------------------------


def test_anthropic_messages() -> None:
    converted = to_anthropic_messages(CONVERSATION)
    assert [m["role"] for m in converted] == ["user", "assistant", "user", "assistant", "user"]
    assert converted[1]["content"][1] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "get_debt",
        "input": {"unit_id": 7},
    }
    result = converted[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "call_1"
    assert "$1.000,00" in result["content"]
    # Conversation cache breakpoint only on the very last block.
    assert converted[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in b for m in converted[:-1] for b in m["content"])


def test_anthropic_merges_consecutive_user_messages() -> None:
    converted = to_anthropic_messages([UserMessage("hola"), UserMessage("¿estás?")])
    assert len(converted) == 1
    assert [b["text"] for b in converted[0]["content"]] == ["hola", "¿estás?"]


def test_gemini_contents() -> None:
    contents = to_gemini_contents(CONVERSATION)
    assert [c.role for c in contents] == ["user", "model", "user", "model", "user"]
    call = contents[1].parts[1].function_call
    assert (call.id, call.name, call.args) == ("call_1", "get_debt", {"unit_id": 7})
    response = contents[2].parts[0].function_response
    assert response.id == "call_1" and response.name == "get_debt"
    assert response.response == {"total_debt": "$1.000,00"}


def test_gemini_reuses_its_raw_content_with_thought_signatures() -> None:
    raw = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(id="x", name="get_debt", args={"unit_id": 1}),
                thought_signature=b"sig",
            )
        ],
    )
    message = AssistantMessage(
        tool_calls=(ToolCall("x", "get_debt", {"unit_id": 1}),), raw=raw, provider="gemini"
    )
    assert to_gemini_contents([UserMessage("a"), message])[1] is raw
    # A raw from another provider is ignored and the turn is rebuilt.
    foreign = AssistantMessage(text="hola", raw=object(), provider="anthropic")
    assert to_gemini_contents([UserMessage("a"), foreign])[1].parts[0].text == "hola"


def test_gemini_local_ids_are_not_sent() -> None:
    message = AssistantMessage(tool_calls=(ToolCall("local-abc", "ping", {}),))
    results = ToolResultsMessage((ToolResult("local-abc", "ping", {"ok": True}),))
    contents = to_gemini_contents([UserMessage("a"), message, results])
    assert contents[1].parts[0].function_call.id is None
    assert contents[2].parts[0].function_response.id is None


# --- Parsing and usage --------------------------------------------------------------------


def test_parse_anthropic_usage() -> None:
    message = anthropic.types.Message.model_validate(
        {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": [
                {"type": "text", "text": "Me fijo."},
                {"type": "tool_use", "id": "t", "name": "get_debt", "input": {"unit_id": 3}},
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 120,
                "output_tokens": 30,
                "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 500,
            },
        }
    )
    parsed = parse_anthropic_response(message)
    assert parsed.message.text == "Me fijo."
    assert parsed.message.tool_calls == (ToolCall("t", "get_debt", {"unit_id": 3}),)
    assert parsed.usage == Usage(120, 30, cache_read_tokens=2000, cache_write_tokens=500)


def test_parse_gemini_usage_and_skips_thoughts() -> None:
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="pensando...", thought=True),
                        types.Part(text="Hola, "),
                        types.Part(text="¿en qué te ayudo?"),
                        types.Part(function_call=types.FunctionCall(name="ping", args={})),
                    ],
                )
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=5000,
            cached_content_token_count=4100,
            candidates_token_count=25,
            thoughts_token_count=60,
        ),
    )
    parsed = parse_gemini_response(response)
    assert parsed.message.text == "Hola, ¿en qué te ayudo?"
    assert parsed.message.tool_calls[0].name == "ping"
    assert parsed.message.tool_calls[0].id.startswith("local-")
    assert parsed.usage == Usage(input_tokens=900, output_tokens=85, cache_read_tokens=4100)
    assert parsed.message.raw is response.candidates[0].content


def test_gemini_empty_answer_is_an_error() -> None:
    with pytest.raises(LLMError):
        parse_gemini_response(types.GenerateContentResponse(candidates=[]))


def test_prices_estimate() -> None:
    prices = Prices(input=1, output=5, cache_read=0.1, cache_write=1.25)
    usage = Usage(1_000_000, 100_000, cache_read_tokens=1_000_000, cache_write_tokens=0)
    assert prices.estimate(usage) == pytest.approx(1 + 0.5 + 0.1)
    assert Prices().estimate(usage) is None


# --- Calls and errors ---------------------------------------------------------------------


@pytest.mark.parametrize("name", PROVIDERS)
def test_generate_through_the_sdk(name: str) -> None:
    provider, script = scripted_provider(name, [Call("get_debt", {"unit_id": 9}), Say("Listo")])
    first = provider.generate("sistema", [UserMessage("hola")], [SPEC])
    assert first.message.tool_calls[0].name == "get_debt"
    assert first.message.tool_calls[0].arguments == {"unit_id": 9}
    assert first.usage.input_tokens == 1000
    second = provider.generate("sistema", [UserMessage("hola"), first.message], [SPEC])
    assert second.message.text == "Listo"
    request = script.requests[0]
    if name == "anthropic":
        assert request["model"] == "claude-haiku-4-5"
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert request["tools"][0]["name"] == "get_debt"
    else:
        config = request["config"]
        assert request["model"] == "gemini-3.8-flash"
        assert config.automatic_function_calling.disable is True
        assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
        assert config.tools[0].function_declarations[0].name == "get_debt"
        # The model's content goes back untouched (thought signature included).
        assert script.requests[1]["contents"][1].parts[0].thought_signature == b"signature"


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("anthropic", anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))),
        ("gemini", errors.ServerError(503, {"error": {"message": "x", "status": "UNAVAILABLE"}})),
    ],
)
def test_api_errors_become_llm_error(name: str, error: Exception) -> None:
    provider, _ = scripted_provider(name, [error])
    with pytest.raises(LLMError):
        provider.generate("sistema", [UserMessage("hola")], [SPEC])


# --- Configuration ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("anthropic", "", "claude-haiku-4-5"),
        ("gemini", "", "gemini-3.8-flash"),
        ("anthropic", "claude-sonnet-5-5", "claude-sonnet-5-5"),
        ("gemini", "gemini-3.5-flash-lite", "gemini-3.5-flash-lite"),
    ],
)
def test_provider_and_model_from_settings(provider: str, model: str, expected: str) -> None:
    settings = Settings(
        _env_file=None,
        llm_provider=provider,
        llm_model=model,
        gemini_api_key="g",
        anthropic_api_key="a",
    )
    chosen = get_provider(settings)
    assert (chosen.name, chosen.model) == (provider, expected)


def test_missing_api_key() -> None:
    with pytest.raises(LLMError):
        get_provider(Settings(_env_file=None, llm_provider="anthropic"))
