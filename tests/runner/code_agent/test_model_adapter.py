"""Unit tests for AgentalyzeModelAdapter: no browser, no real provider.

Verifies message conversion is lossless in both directions and — the one
thing that must not be assumed — that RetryingProvider's retry logic really
does kick in when a Provider is driven through this adapter.
"""

from __future__ import annotations

import asyncio

import pytest
import smolagents

from agentalyze.providers.base import (
    ChatMessage,
    CompletionResult,
    ProviderConnectionError,
)
from agentalyze.providers.retry import RetryingProvider, RetryPolicy
from agentalyze.runner.code_agent.model_adapter import (
    AgentalyzeModelAdapter,
    agentalyze_message_to_smolagents,
    smolagents_message_to_agentalyze,
)


class FakeProvider:
    def __init__(self, script) -> None:
        self.script = list(script)
        self.name = "fake-code-provider"
        self.calls = 0

    async def chat_completion(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def health_check(self) -> bool:
        return True


def _completion(text: str) -> CompletionResult:
    return CompletionResult(
        message=ChatMessage(role="assistant", content=text),
        prompt_tokens=42,
        completion_tokens=7,
        total_tokens=49,
        latency_seconds=0.5,
        finish_reason="stop",
    )


class TestMessageConversion:
    def test_agentalyze_to_smolagents_roundtrip_every_role_except_tool(self) -> None:
        # 'tool' is intentionally NOT round-trip-symmetric: see
        # test_tool_response_maps_to_user_not_tool below for why.
        for role, content in [
            ("system", "sys prompt"),
            ("user", "do the task"),
            ("assistant", "ok, done"),
        ]:
            original = ChatMessage(role=role, content=content)
            converted = agentalyze_message_to_smolagents(original)
            back = smolagents_message_to_agentalyze(converted)
            assert back.content == original.content
            assert back.role == original.role

    def test_smolagents_tool_call_role_maps_to_assistant(self) -> None:
        msg = smolagents.ChatMessage(role="tool-call", content="calling a tool")
        converted = smolagents_message_to_agentalyze(msg)
        assert converted.role == "assistant"
        assert converted.content == "calling a tool"

    def test_tool_response_maps_to_user_not_tool(self) -> None:
        """Regression test for a real bug found via an end-to-end run against
        a live OpenAI-compatible provider (Ollama): mapping 'tool-response'
        to Agentalyze's 'tool' role made every real-provider run fail after
        step 1 with "role='tool' messages require tool_call_id" (the
        OpenAI-compatible provider enforces that real API constraint, and
        CodeAgent never produces a matching tool_call_id pairing). 'user' is
        correct: CodeAgent's "tool response" is just the next turn of
        conversation, not a structured tool-call acknowledgment."""
        msg = smolagents.ChatMessage(role="tool-response", content="Clicked e3.")
        converted = smolagents_message_to_agentalyze(msg)
        assert converted.role == "user"
        assert converted.tool_call_id is None
        assert converted.content == "Clicked e3."

    def test_content_list_parts_flatten_to_text(self) -> None:
        msg = smolagents.ChatMessage(
            role="assistant",
            content=[{"type": "text", "text": "part one"}, {"type": "text", "text": "part two"}],
        )
        converted = smolagents_message_to_agentalyze(msg)
        assert converted.content == "part one\npart two"

    def test_none_content_becomes_empty_string(self) -> None:
        msg = smolagents.ChatMessage(role="assistant", content=None)
        converted = smolagents_message_to_agentalyze(msg)
        assert converted.content == ""


class TestGenerate:
    def test_generate_calls_provider_and_converts_result(self) -> None:
        provider = FakeProvider([_completion("here is my code")])
        adapter = AgentalyzeModelAdapter(provider)

        response = adapter.generate([smolagents.ChatMessage(role="user", content="go")])

        assert response.content == "here is my code"
        assert response.token_usage.input_tokens == 42
        assert response.token_usage.output_tokens == 7
        assert len(adapter.completions) == 1
        assert adapter.completions[0].message.content == "here is my code"

    def test_generate_safe_from_a_thread_with_no_running_loop(self) -> None:
        """Mirrors how code_agent/loop.py actually calls generate(): from a
        worker thread with no event loop of its own (asyncio.to_thread)."""
        provider = FakeProvider([_completion("threaded")])
        adapter = AgentalyzeModelAdapter(provider)

        async def _drive() -> str:
            return await asyncio.to_thread(
                adapter.generate, [smolagents.ChatMessage(role="user", content="go")]
            )

        response = asyncio.run(_drive())
        assert response.content == "threaded"

    def test_generate_marshals_onto_explicit_loop_when_given(self) -> None:
        """When constructed with `loop`, generate() must run the provider
        call on THAT loop even when invoked from a different thread — this
        is the real code_agent/loop.py usage (see model_adapter.py's
        constructor docstring for why a fresh per-call loop is unsafe for
        a real Provider's loop-bound connections)."""
        provider = FakeProvider([_completion("marshaled")])

        async def _run_on_owning_loop() -> smolagents.ChatMessage:
            owning_loop = asyncio.get_running_loop()
            adapter = AgentalyzeModelAdapter(provider, loop=owning_loop)
            return await asyncio.to_thread(
                adapter.generate, [smolagents.ChatMessage(role="user", content="go")]
            )

        response = asyncio.run(_run_on_owning_loop())
        assert response.content == "marshaled"


class TestRetryPropagates:
    def test_retrying_provider_retries_through_the_adapter(self) -> None:
        """The adapter must add zero retry logic of its own: whatever
        RetryingProvider the caller already wrapped the Provider in should
        transparently retry, exactly as it does for the ReAct loop."""
        provider = FakeProvider(
            [
                ProviderConnectionError("boom 1", provider_name="fake"),
                ProviderConnectionError("boom 2", provider_name="fake"),
                _completion("finally worked"),
            ]
        )
        retrying = RetryingProvider(
            provider,
            policy=RetryPolicy(
                max_attempts=5, initial_wait_seconds=0, max_wait_seconds=0, jitter_seconds=0
            ),
        )
        adapter = AgentalyzeModelAdapter(retrying)

        response = adapter.generate([smolagents.ChatMessage(role="user", content="go")])

        assert response.content == "finally worked"
        assert provider.calls == 3

    def test_retrying_provider_exhaustion_propagates(self) -> None:
        provider = FakeProvider(
            [
                ProviderConnectionError("boom 1", provider_name="fake"),
                ProviderConnectionError("boom 2", provider_name="fake"),
            ]
        )
        retrying = RetryingProvider(
            provider,
            policy=RetryPolicy(
                max_attempts=2, initial_wait_seconds=0, max_wait_seconds=0, jitter_seconds=0
            ),
        )
        adapter = AgentalyzeModelAdapter(retrying)

        with pytest.raises(ProviderConnectionError):
            adapter.generate([smolagents.ChatMessage(role="user", content="go")])
        assert provider.calls == 2
