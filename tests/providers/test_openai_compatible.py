"""Tests for OpenAICompatibleProvider with a fully mocked HTTP layer.

``respx`` intercepts the httpx transport used by the ``openai`` SDK, giving
realistic control over status codes, payloads and connection failures without
any network access.
"""

from __future__ import annotations

import json

import httpx
import openai
import pytest
import respx
from httpx import Response

from agentalyze.providers.base import (
    ChatMessage,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ToolSpec,
)
from agentalyze.providers.openai_compatible import OpenAICompatibleProvider

BASE_URL = "http://testserver/v1"
CHAT_URL = f"{BASE_URL}/chat/completions"


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=BASE_URL,
        api_key="test-key",
        model_name="gpt-4o-mini",
        name="gpt-4o-mini-test",
        health_check_timeout_seconds=1.0,
    )


def _completion_payload(*, message: dict, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


@respx.mock
async def test_successful_plain_completion() -> None:
    route = respx.post(CHAT_URL).mock(
        Response(200, json=_completion_payload(message={"role": "assistant", "content": "Hello!"}))
    )
    provider = _provider()

    result = await provider.chat_completion([ChatMessage(role="user", content="hi")])

    assert route.called
    assert result.message == ChatMessage(role="assistant", content="Hello!")
    assert result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (11, 7, 18)
    assert result.raw_provider_response_id == "chatcmpl-test"
    assert result.latency_seconds >= 0


@respx.mock
async def test_request_payload_mapping() -> None:
    route = respx.post(CHAT_URL).mock(
        Response(200, json=_completion_payload(message={"role": "assistant", "content": "ok"}))
    )
    provider = _provider()

    await provider.chat_completion(
        [ChatMessage(role="system", content="be brief"), ChatMessage(role="user", content="hi")],
        tools=[
            ToolSpec(
                name="get_weather",
                description="Weather lookup.",
                parameters={"type": "object", "properties": {"city": {"type": "string"}}},
            )
        ],
        temperature=0.3,
        max_tokens=64,
    )

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert body["tools"][0]["function"]["name"] == "get_weather"
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 64


@respx.mock
async def test_successful_tool_call_response_is_parsed() -> None:
    respx.post(CHAT_URL).mock(
        Response(
            200,
            json=_completion_payload(
                message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "get_weather",
                                "arguments": '{"city": "Paris"}',
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
            ),
        )
    )
    provider = _provider()

    result = await provider.chat_completion([ChatMessage(role="user", content="weather?")])

    assert result.finish_reason == "tool_calls"
    assert result.message.content == ""
    assert result.message.tool_calls is not None
    call = result.message.tool_calls[0]
    assert call.id == "call_abc"
    assert call.name == "get_weather"
    assert call.arguments == {"city": "Paris"}


@respx.mock
async def test_malformed_tool_call_arguments_raise_invalid_response_error() -> None:
    respx.post(CHAT_URL).mock(
        Response(
            200,
            json=_completion_payload(
                message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_bad",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": "{not json"},
                        }
                    ],
                },
                finish_reason="tool_calls",
            ),
        )
    )
    provider = _provider()

    with pytest.raises(ProviderInvalidResponseError) as exc_info:
        await provider.chat_completion([ChatMessage(role="user", content="weather?")])

    assert exc_info.value.provider_name == "gpt-4o-mini-test"


@respx.mock
@pytest.mark.parametrize(
    ("status_code", "expected_exc_type"),
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, ProviderRateLimitError),
        (500, ProviderConnectionError),  # transient upstream: retryable
        (400, ProviderError),  # other 4xx: base class, non-retryable
    ],
)
async def test_http_status_errors_are_translated(status_code: int, expected_exc_type) -> None:
    respx.post(CHAT_URL).mock(Response(status_code, json={"error": {"message": "nope"}}))
    provider = _provider()

    with pytest.raises(expected_exc_type):
        await provider.chat_completion([ChatMessage(role="user", content="hi")])


@respx.mock
async def test_connection_error_becomes_provider_connection_error() -> None:
    respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    provider = _provider()

    with pytest.raises(ProviderConnectionError):
        await provider.chat_completion([ChatMessage(role="user", content="hi")])


@respx.mock
async def test_timeout_error_becomes_provider_timeout_error_not_connection_error() -> None:
    # APITimeoutError subclasses APIConnectionError in the SDK; the mapping
    # must distinguish them (timeout is its own retryable class).
    respx.post(CHAT_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    provider = _provider()

    with pytest.raises(ProviderTimeoutError):
        await provider.chat_completion([ChatMessage(role="user", content="hi")])


@respx.mock
@respx.mock
async def test_non_json_2xx_response_raises_invalid_response_error() -> None:
    """Regression: a broken proxy returning plain-text 2xx must NOT leak a raw
    AttributeError past the provider boundary."""
    respx.post(CHAT_URL).mock(Response(200, text="gateway garbage, not JSON"))
    provider = _provider()

    with pytest.raises(ProviderInvalidResponseError) as exc_info:
        await provider.chat_completion([ChatMessage(role="user", content="hi")])

    assert exc_info.value.provider_name == "gpt-4o-mini-test"


async def test_locally_invalid_message_raises_value_error_not_provider_error() -> None:
    """Documented contract: caller bugs (role='tool' without tool_call_id)
    surface as ValueError, deliberately NOT as ProviderError, so the failure
    taxonomy does not mistake runner bugs for provider failures."""
    provider = _provider()

    with pytest.raises(ValueError, match="tool_call_id"), respx.mock:
        respx.post(CHAT_URL).mock(Response(200, text="{}"))
        await provider.chat_completion([ChatMessage(role="tool", content="x")])


async def test_sdk_exceptions_never_leak_unwrapped() -> None:
    """Whatever goes wrong on the wire, callers only ever see ProviderError."""
    failures = (
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("slow"),
        Response(500, text="oops"),
        Response(400, json={"error": {"message": "bad"}}),
        Response(200, text="garbage"),
        Response(200, json={"unexpected": "shape"}),
    )
    for side_effect in failures:
        with respx.mock:
            respx.post(CHAT_URL).mock(side_effect=side_effect)
            with pytest.raises(ProviderError) as exc_info:
                await _provider().chat_completion([ChatMessage(role="user", content="hi")])

            # The raised error must be from OUR hierarchy, never an SDK/httpx type.
            assert not isinstance(exc_info.value, (openai.OpenAIError, httpx.HTTPError))


class TestHealthCheck:
    @respx.mock
    async def test_success(self) -> None:
        route = respx.post(CHAT_URL).mock(
            Response(200, json=_completion_payload(message={"role": "assistant", "content": "p"}))
        )

        assert await _provider().health_check() is True
        body = json.loads(route.calls.last.request.content)
        assert body["max_tokens"] == 1  # deliberately minimal probe

    @respx.mock
    async def test_failure_returns_false_instead_of_raising(self) -> None:
        respx.post(CHAT_URL).mock(side_effect=httpx.ConnectError("down"))

        assert await _provider().health_check() is False

    @respx.mock
    async def test_auth_failure_returns_false_instead_of_raising(self) -> None:
        respx.post(CHAT_URL).mock(Response(401, json={"error": {"message": "bad key"}}))

        assert await _provider().health_check() is False
