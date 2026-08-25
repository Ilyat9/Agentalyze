"""Provider interface shared by every LLM backend.

This module defines the *public* contract used by the rest of the codebase
(from Phase 3 on): Pydantic message/result models, an exception hierarchy
classified into retryable vs non-retryable errors, and a structural
:class:`Provider` protocol. Nothing here may reference a concrete SDK
(OpenAI, Ollama, ...) so that adding a third provider later never changes
the calling code.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """A single tool invocation requested by the model."""

    id: str = Field(description="Provider-side tool call identifier.")
    name: str = Field(description="Name of the tool being called.")
    arguments: dict[str, Any] = Field(
        description="Parsed tool arguments (JSON object), not a raw string.",
    )


class ChatMessage(BaseModel):
    """One message in a chat conversation, provider-agnostic."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(default="", description="Message text ('' when only tool_calls are present).")
    tool_call_id: str | None = Field(
        default=None,
        description="Required for role='tool': id of the tool call this message answers.",
    )
    tool_calls: list[ToolCall] | None = Field(
        default=None,
        description="Set for role='assistant' when the model requests tool invocations.",
    )


class ToolSpec(BaseModel):
    """Declarative tool description handed to the model.

    ``parameters`` is a JSON Schema object; providers translate it into their
    native format (for OpenAI-compatible APIs it is passed through as-is).
    """

    name: str
    description: str
    parameters: dict[str, Any]


class CompletionResult(BaseModel):
    """Result of a single chat completion call.

    ``raw_provider_response_id`` is kept only for debugging/tracing; callers
    must not parse anything deeper from the raw response than what this model
    already exposes.
    """

    message: ChatMessage
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_seconds: float = Field(ge=0)
    finish_reason: str = Field(
        description="Why generation stopped ('stop', 'tool_calls', 'length', ...).",
    )
    raw_provider_response_id: str | None = None


class ProviderError(Exception):
    """Base class for all provider failures.

    Do NOT catch-and-swallow these silently: the runner (Phase 3) classifies
    failures via subclasses of this exception for the failure taxonomy, so
    the hierarchy below is part of the public contract.

    Retryable vs non-retryable is encoded in the hierarchy *now*, even though
    the actual retry mechanism lives in ``agentalyze.providers.retry``:

    * retryable:   ``ProviderConnectionError``, ``ProviderTimeoutError``,
                   ``ProviderRateLimitError``
    * non-retryable: ``ProviderAuthError``, ``ProviderInvalidResponseError``,
                     ``ProviderConfigError``
    """

    def __init__(self, message: str, *, provider_name: str | None = None) -> None:
        self.provider_name = provider_name
        super().__init__(message)


class ProviderConnectionError(ProviderError):
    """Could not reach the provider endpoint (DNS, TCP, TLS, HTTP 5xx)."""


class ProviderTimeoutError(ProviderError):
    """The request exceeded its time budget."""


class ProviderRateLimitError(ProviderError):
    """The provider rejected the request due to rate limiting (HTTP 429)."""


class ProviderAuthError(ProviderError):
    """Authentication/authorization failed (bad or missing key). Non-retryable."""


class ProviderInvalidResponseError(ProviderError):
    """The model returned a structurally invalid response (e.g. malformed JSON
    in tool call arguments).

    This is NOT a network problem and must be classified separately in the
    future failure taxonomy; retrying verbatim is usually pointless.
    """


class ProviderConfigError(ProviderError):
    """The provider was configured incorrectly (e.g. missing API-key env var).
    Non-retryable by definition."""


#: Errors worth retrying (see ``RetryingProvider``); everything else fails fast.
RETRYABLE_PROVIDER_ERRORS: tuple[type[ProviderError], ...] = (
    ProviderConnectionError,
    ProviderTimeoutError,
    ProviderRateLimitError,
)


@runtime_checkable
class Provider(Protocol):
    """Structural interface every LLM backend must satisfy.

    Implementations are plain classes (no inheritance required); use
    ``isinstance(x, Provider)`` freely thanks to ``@runtime_checkable``.
    """

    name: str  # human-readable id of the *configured* model, e.g. "gpt-4o-mini-via-openrouter"

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Run one chat completion turn.

        ``temperature`` defaults to ``0.0`` because evals should be as
        deterministic as possible. The interface still allows overriding it:
        some OpenAI-compatible providers/models do not honor ``temperature=0``
        literally (they round or ignore it). That caveat cannot be fixed at
        the interface level — it is a property of specific models — so it is
        documented here rather than worked around in code.

        Raises only ``ProviderError`` subclasses (or ``KeyboardInterrupt``/
        ``asyncio.CancelledError``); SDK-specific exceptions must never leak
        past a concrete provider implementation.
        """
        ...

    async def health_check(self) -> bool:
        """Cheap availability probe; must never raise, returns False on failure."""
        ...
