"""Tests for the provider data models and exception hierarchy."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentalyze.providers.base import (
    RETRYABLE_PROVIDER_ERRORS,
    ChatMessage,
    CompletionResult,
    Provider,
    ProviderAuthError,
    ProviderConfigError,
    ProviderConnectionError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ToolCall,
    ToolSpec,
)


def _completion_result() -> CompletionResult:
    return CompletionResult(
        message=ChatMessage(role="assistant", content="ok"),
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_seconds=0.42,
        finish_reason="stop",
        raw_provider_response_id="chatcmpl-1",
    )


class TestModels:
    def test_chat_message_round_trip(self) -> None:
        message = ChatMessage(role="user", content="hello")

        dumped = message.model_dump()
        assert ChatMessage.model_validate(dumped) == message

    def test_chat_message_with_tool_calls_round_trip(self) -> None:
        message = ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_1", name="get_weather", arguments={"city": "Paris"})],
        )

        assert ChatMessage.model_validate(message.model_dump()) == message

    def test_tool_call_arguments_accept_arbitrary_json(self) -> None:
        call = ToolCall(id="call_1", name="click", arguments={"selector": "#a", "index": 2})

        assert call.arguments["index"] == 2

    def test_tool_spec_is_json_schema_passthrough(self) -> None:
        spec = ToolSpec(
            name="get_weather",
            description="Get weather for a city.",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        )

        assert ToolSpec.model_validate(spec.model_dump()) == spec

    def test_completion_result_rejects_negative_tokens(self) -> None:
        with pytest.raises(ValidationError):
            CompletionResult(
                message=ChatMessage(role="assistant", content="x"),
                prompt_tokens=-1,
                completion_tokens=0,
                total_tokens=0,
                latency_seconds=0.0,
                finish_reason="stop",
            )

    def test_completion_result_raw_response_id_optional(self) -> None:
        result = _completion_result().model_copy(update={"raw_provider_response_id": None})

        assert result.raw_provider_response_id is None

    def test_completion_result_round_trip(self) -> None:
        result = _completion_result()

        assert CompletionResult.model_validate(result.model_dump()) == result

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChatMessage(role="wizard", content="hi")  # type: ignore[arg-type]


class TestExceptionHierarchy:
    def test_every_subclass_is_provider_error(self) -> None:
        subclasses = [
            ProviderConnectionError("x"),
            ProviderTimeoutError("x"),
            ProviderRateLimitError("x"),
            ProviderAuthError("x"),
            ProviderInvalidResponseError("x"),
            ProviderConfigError("x"),
        ]

        for exc in subclasses:
            assert isinstance(exc, ProviderError)

    @pytest.mark.parametrize(
        "exc_type",
        [ProviderConnectionError, ProviderTimeoutError, ProviderRateLimitError],
    )
    def test_retryable_errors_are_listed_as_retryable(self, exc_type: type[ProviderError]) -> None:
        assert exc_type in RETRYABLE_PROVIDER_ERRORS

    @pytest.mark.parametrize(
        "exc_type",
        [ProviderAuthError, ProviderInvalidResponseError, ProviderConfigError],
    )
    def test_non_retryable_errors_are_not_retryable(self, exc_type: type[ProviderError]) -> None:
        assert not issubclass(exc_type, tuple(RETRYABLE_PROVIDER_ERRORS))

    def test_error_carries_provider_name(self) -> None:
        exc = ProviderRateLimitError("slow down", provider_name="gpt-4o-mini-via-openrouter")

        assert exc.provider_name == "gpt-4o-mini-via-openrouter"
        assert str(exc) == "slow down"

    def test_provider_protocol_is_runtime_checkable(self) -> None:
        class DummyProvider:
            name = "dummy"

            async def chat_completion(self, messages, tools=None, temperature=0.0, max_tokens=None):
                return _completion_result()

            async def health_check(self) -> bool:
                return True

        assert isinstance(DummyProvider(), Provider)
