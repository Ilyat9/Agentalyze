"""Composable retry wrapper around any Provider.

``RetryingProvider`` wraps an arbitrary :class:`Provider` and retries only
retryable errors (connection / timeout / rate limit — see
``RETRYABLE_PROVIDER_ERRORS``). Non-retryable errors (bad API key, invalid
model response, misconfiguration) propagate immediately: retrying a request
with a wrong key is pointless by definition.

Retry mechanics are delegated to ``tenacity`` instead of hand-rolled
``asyncio.sleep`` loops, which avoids the classic bugs (no jitter, no attempt
cap, blocking sleep in async code). All timing parameters are configurable
via :class:`RetryPolicy`; defaults are 3 attempts with exponential backoff
(~1s base, x2 multiplier, capped at 30s).
"""

from __future__ import annotations

from dataclasses import dataclass

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from agentalyze.providers.base import (
    RETRYABLE_PROVIDER_ERRORS,
    ChatMessage,
    CompletionResult,
    Provider,
    ToolSpec,
)


@dataclass(frozen=True)
class RetryPolicy:
    """Knobs for the retry wrapper. Defaults follow the phase spec."""

    max_attempts: int = 3
    initial_wait_seconds: float = 1.0
    multiplier: float = 2.0
    max_wait_seconds: float = 30.0
    jitter_seconds: float = 1.0


class RetryingProvider:
    """Decorates any ``Provider`` with transparent retry-on-retryable logic.

    ``name`` is forwarded unchanged from the wrapped provider so that callers
    and logs always see the configured provider identity.
    """

    def __init__(self, inner: Provider, policy: RetryPolicy | None = None) -> None:
        self._inner = inner
        self._policy = policy or RetryPolicy()
        self.name = inner.name

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        policy = self._policy
        retrier = AsyncRetrying(
            stop=stop_after_attempt(policy.max_attempts),
            # Exponential backoff (base * multiplier ** attempt) plus random
            # jitter to avoid thundering-herd retries against rate limits.
            # Tests pass a policy with zero waits to avoid real sleeping.
            wait=wait_exponential_jitter(
                initial=policy.initial_wait_seconds,
                max=policy.max_wait_seconds,
                exp_base=policy.multiplier,
                jitter=policy.jitter_seconds,
            ),
            retry=retry_if_exception_type(RETRYABLE_PROVIDER_ERRORS),
            reraise=True,  # re-raise the original last exception, never RetryError
        )
        try:
            async for attempt in retrier:
                with attempt:
                    return await self._inner.chat_completion(
                        messages=messages,
                        tools=tools,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
        except RetryError as exc:  # defensive; reraise=True should prevent this
            last = exc.last_attempt.exception()
            if last is None:
                msg = "unreachable: retrier exhausted without a recorded exception"
                raise AssertionError(msg) from exc
            raise last from exc
        msg = "unreachable: retrier exhausted without returning or raising"
        raise AssertionError(msg)  # pragma: no cover

    async def health_check(self) -> bool:
        """Health checks are NOT retried: they must stay fast by contract."""
        return await self._inner.health_check()
