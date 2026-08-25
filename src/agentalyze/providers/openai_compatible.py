"""OpenAI-compatible provider: one implementation for any /v1/chat/completions API.

OpenAI, OpenRouter, Together, Groq, local Ollama etc. all follow the same
``/v1/chat/completions`` contract, so there is exactly one implementation here
parameterized by ``base_url``/``api_key`` — not a class per vendor. The
official ``openai`` SDK does the reliable HTTP/SSE work; this module only maps
between Agentalyze models (``agentalyze.providers.base``) and SDK payloads,
and translates SDK exceptions into the provider exception hierarchy so that
no SDK type ever leaks to callers.
"""

from __future__ import annotations

import json
import time
from typing import Any

import openai
from openai import AsyncOpenAI

from agentalyze.providers.base import (
    ChatMessage,
    CompletionResult,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ToolCall,
    ToolSpec,
)

#: Role names are identical between our ChatMessage and the OpenAI wire format;
#: validated explicitly so an unexpected role fails fast instead of silently.
_KNOWN_ROLES = frozenset({"system", "user", "assistant", "tool"})


def _message_to_sdk(message: ChatMessage) -> dict[str, Any]:
    """Agentalyze ``ChatMessage`` -> OpenAI ``messages`` entry."""
    if message.role not in _KNOWN_ROLES:
        msg = f"unknown message role: {message.role!r}"
        raise ValueError(msg)
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.role == "tool":
        if message.tool_call_id is None:
            msg = "role='tool' messages require tool_call_id"
            raise ValueError(msg)
        payload["tool_call_id"] = message.tool_call_id
    if message.role == "assistant" and message.tool_calls is not None:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in message.tool_calls
        ]
        # Some strict OpenAI-compatible backends reject assistant tool-call
        # messages with a non-null content; None is the canonical encoding.
        payload["content"] = message.content or None
    return payload


def _tools_to_sdk(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """Agentalyze ``ToolSpec`` list -> OpenAI ``tools`` payload."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in tools
    ]


def _tool_calls_from_sdk(
    raw_calls: list[Any], response_id: str | None, provider_name: str
) -> list[ToolCall]:
    """OpenAI ``tool_calls`` -> parsed Agentalyze ``ToolCall`` list.

    Arguments arrive as a JSON *string* on the wire; malformed JSON from a
    model is a structurally invalid model response, i.e.
    ``ProviderInvalidResponseError`` — deliberately NOT a connection error.
    """
    calls: list[ToolCall] = []
    for raw in raw_calls:
        function = getattr(raw, "function", None)
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        call_id = getattr(raw, "id", None)
        if function is None or not name or call_id is None:
            msg = f"malformed tool call in response {response_id!r}: missing id/function"
            raise ProviderInvalidResponseError(msg, provider_name=provider_name)
        try:
            parsed = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError as exc:
            msg = f"invalid JSON in tool call arguments for {name!r} (response {response_id!r}): {exc}"
            raise ProviderInvalidResponseError(msg, provider_name=provider_name) from exc
        if not isinstance(parsed, dict):
            msg = (
                f"tool call arguments for {name!r} must decode to a JSON object, "
                f"got {type(parsed).__name__}"
            )
            raise ProviderInvalidResponseError(msg, provider_name=provider_name)
        calls.append(ToolCall(id=call_id, name=name, arguments=parsed))
    return calls


class OpenAICompatibleProvider:
    """Provider for any API implementing the OpenAI chat-completions contract.

    Raises only :class:`agentalyze.providers.base.ProviderError` subclasses:
    SDK exceptions are translated in one place (``_translate_error``).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        name: str | None = None,
        timeout_seconds: float = 120.0,
        health_check_timeout_seconds: float = 5.0,
    ) -> None:
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._health_check_timeout_seconds = health_check_timeout_seconds
        self.name = name or model_name
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,  # transport-level retries would double up with RetryingProvider
        )

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Run one completion; see ``agentalyze.providers.base.Provider``.

        Raises:
            ProviderError subclasses: for every failure of the remote side
                (network, timeout, rate limit, auth, malformed responses).
            ValueError: only for *locally* invalid input (e.g. a
                ``role='tool'`` message without ``tool_call_id``). This is a
                caller programming error, deliberately NOT a ``ProviderError``
                so that runner bugs are not misclassified as provider
                failures in the failure taxonomy.
        """
        request: dict[str, Any] = {
            "model": self._model_name,
            "messages": [_message_to_sdk(m) for m in messages],
            "temperature": temperature,
        }
        if tools is not None:
            request["tools"] = _tools_to_sdk(tools)
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        started = time.perf_counter()
        try:
            # NOTE: APITimeoutError is a subclass of APIConnectionError in the
            # openai SDK, so it must be caught before APIConnectionError.
            response = await self._client.chat.completions.create(**request)
        except openai.APITimeoutError as exc:
            raise self._translate_error(exc) from exc
        except openai.RateLimitError as exc:
            raise self._translate_error(exc) from exc
        except openai.AuthenticationError as exc:
            raise self._translate_error(exc) from exc
        except openai.PermissionDeniedError as exc:
            raise self._translate_error(exc) from exc
        except openai.InternalServerError as exc:  # 5xx: transient upstream, retryable
            raise self._translate_error(exc) from exc
        except openai.APIConnectionError as exc:
            raise self._translate_error(exc) from exc
        except openai.APIStatusError as exc:  # remaining 4xx: non-retryable
            raise self._translate_error(exc) from exc
        latency = time.perf_counter() - started

        try:
            choices = response.choices or []
            if len(choices) != 1:
                msg = f"expected exactly 1 choice in response {response.id!r}, got {len(choices)}"
                raise ProviderInvalidResponseError(msg, provider_name=self.name)
            choice = choices[0]
            message = choice.message
            content = message.content or ""
            raw_tool_calls = getattr(message, "tool_calls", None)
            tool_calls = (
                _tool_calls_from_sdk(list(raw_tool_calls), response.id, self.name)
                if raw_tool_calls
                else None
            )
        except ProviderInvalidResponseError:
            raise
        except (AttributeError, TypeError, IndexError, ValueError) as exc:
            # A 2xx reply that does not match the OpenAI response shape at all
            # (e.g. a broken proxy returning plain text) is a structurally
            # invalid provider response — never leak raw parse errors.
            msg = f"[{self.name}] structurally invalid completion response: {exc}"
            raise ProviderInvalidResponseError(msg, provider_name=self.name) from exc
        usage = getattr(response, "usage", None)
        return CompletionResult(
            message=ChatMessage(role=message.role, content=content, tool_calls=tool_calls),
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            latency_seconds=latency,
            finish_reason=choice.finish_reason or "unknown",
            raw_provider_response_id=response.id,
        )

    async def health_check(self) -> bool:
        """Lightweight availability probe.

        A minimal completion with ``max_tokens=1`` under a short, separately
        configurable timeout — deliberately cheaper than a real turn so that
        pre-flight checks never slow down the overall run. Never raises:
        any failure (including unexpected ones) yields ``False``.
        """
        try:
            await self._client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                timeout=self._health_check_timeout_seconds,
            )
        except Exception:  # noqa: BLE001 - health check must not crash callers
            return False
        return True

    def _translate_error(self, exc: openai.OpenAIError) -> ProviderError:
        """Map an SDK exception onto the provider hierarchy (single place)."""
        message = f"[{self.name}] {type(exc).__name__}: {exc}"
        common: dict[str, str] = {"provider_name": self.name}
        if isinstance(exc, openai.APITimeoutError):
            return ProviderTimeoutError(message, **common)
        if isinstance(exc, openai.RateLimitError):
            return ProviderRateLimitError(message, **common)
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return ProviderAuthError(message, **common)
        if isinstance(exc, openai.InternalServerError):
            # 5xx from the upstream API is transient: treat as connection-class
            # so RetryingProvider retries it.
            return ProviderConnectionError(message, **common)
        if isinstance(exc, openai.APIConnectionError):
            return ProviderConnectionError(message, **common)
        status_code = getattr(exc, "status_code", None)
        return ProviderError(f"{message} (status_code={status_code})", **common)

