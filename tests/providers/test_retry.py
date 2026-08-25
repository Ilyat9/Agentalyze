"""Tests for RetryingProvider using a fake inner provider (no network at all)."""

from __future__ import annotations

import pytest

from agentalyze.providers.base import (
    ChatMessage,
    CompletionResult,
    Provider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ToolSpec,
)
from agentalyze.providers.retry import RetryingProvider, RetryPolicy

FAST_POLICY = RetryPolicy(
    max_attempts=3, initial_wait_seconds=0.0, jitter_seconds=0.0
)


def _result() -> CompletionResult:
    return CompletionResult(
        message=ChatMessage(role="assistant", content="ok"),
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        latency_seconds=0.01,
        finish_reason="stop",
    )


class FakeProvider:
    """Scripted fake: pops one behavior per call from a list of callables."""

    def __init__(self, *script: object) -> None:
        self.name = "fake"
        self._script = list(script)
        self.calls = 0

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        self.calls += 1
        behavior = self._script.pop(0) if self._script else _result()
        if isinstance(behavior, Exception):
            raise behavior
        assert callable(behavior)
        return behavior()

    async def health_check(self) -> bool:
        return True


class TestRetryableErrors:
    @pytest.mark.parametrize("error", [
        ProviderConnectionError("conn"),
        ProviderTimeoutError("timeout"),
        ProviderRateLimitError("429"),
    ])
    async def test_retries_then_succeeds(self, error: ProviderError) -> None:
        inner = FakeProvider(error, error, _result)

        wrapped = RetryingProvider(inner, policy=FAST_POLICY)  # type: ignore[arg-type]
        result = await wrapped.chat_completion([ChatMessage(role="user", content="hi")])

        assert inner.calls == 3  # two failures + one success
        assert result.message.content == "ok"

    async def test_exhausting_attempts_raises_last_error(self) -> None:
        inner = FakeProvider(ProviderTimeoutError("t"), ProviderTimeoutError("t"), ProviderTimeoutError("t"))

        wrapped = RetryingProvider(inner, policy=FAST_POLICY)  # type: ignore[arg-type]
        with pytest.raises(ProviderTimeoutError):
            await wrapped.chat_completion([ChatMessage(role="user", content="hi")])

        assert inner.calls == 3  # exactly max_attempts, not swallowed


class TestNonRetryableErrors:
    @pytest.mark.parametrize("error", [ProviderAuthError("bad key"), ProviderInvalidResponseError("bad json")])
    async def test_fails_immediately_without_a_single_retry(self, error: ProviderError) -> None:
        inner = FakeProvider(error)
        wrapped = RetryingProvider(inner, policy=FAST_POLICY)  # type: ignore[arg-type]

        with pytest.raises(type(error)):
            await wrapped.chat_completion([ChatMessage(role="user", content="hi")])

        assert inner.calls == 1


class TestWrappingSemantics:
    async def test_name_is_forwarded_unchanged(self) -> None:
        inner = FakeProvider(_result)

        assert RetryingProvider(inner).name == "fake"  # type: ignore[arg-type]

    async def test_health_check_is_not_retried(self) -> None:
        class FailingHealth(FakeProvider):
            def __init__(self) -> None:
                super().__init__()
                self.health_calls = 0

            async def health_check(self) -> bool:
                self.health_calls += 1
                return False

        inner = FailingHealth()
        wrapped = RetryingProvider(inner)  # type: ignore[arg-type]

        assert await wrapped.health_check() is False
        assert inner.health_calls == 1  # delegated directly, no retry loop

    def test_wrapped_provider_satisfies_protocol(self) -> None:
        wrapped = RetryingProvider(FakeProvider(_result))  # type: ignore[arg-type]

        assert isinstance(wrapped, Provider)


class TestPolicyDefaults:
    def test_default_policy_matches_phase_spec(self) -> None:
        policy = RetryPolicy()

        assert policy.max_attempts == 3
        assert policy.initial_wait_seconds == 1.0
        assert policy.multiplier == 2.0
        assert policy.max_wait_seconds == 30.0
