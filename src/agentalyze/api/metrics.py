"""Prometheus instrumentation for the service.

Exports ONLY — no Prometheus/Grafana deployment lives here. The /metrics
endpoint serves the standard text format; external monitoring scrapes it.

What is measured:

* ``agentalyze_suite_runs_total{status}``      — run outcomes over time
* ``agentalyze_suite_runs_active``             — in-flight runs right now
* ``agentalyze_provider_calls_total{provider,error}``  — LLM call results by
  exception class (ProviderConnectionError / ProviderRateLimitError / ...)
* ``agentalyze_provider_call_seconds{provider}`` — per-call latency histogram
* ``agentalyze_regressions_total``             — regressions found by gates
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from prometheus_client import Counter, Gauge, Histogram

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from agentalyze.providers.base import (
        ChatMessage,
        CompletionResult,
        ToolSpec,
    )

_T = TypeVar("_T")


SUITE_RUNS_TOTAL = Counter(
    "agentalyze_suite_runs_total",
    "Suite runs by terminal status.",
    ["status"],
)
SUITE_RUNS_ACTIVE = Gauge(
    "agentalyze_suite_runs_active",
    "Suite runs currently executing.",
)
PROVIDER_CALLS_TOTAL = Counter(
    "agentalyze_provider_calls_total",
    "LLM provider chat-completion calls by outcome.",
    ["provider", "error"],
)
PROVIDER_CALL_SECONDS = Histogram(
    "agentalyze_provider_call_seconds",
    "Latency of individual provider chat-completion calls.",
    ["provider"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)
REGRESSIONS_TOTAL = Counter(
    "agentalyze_regressions_total",
    "Regressions detected by regression-check invocations.",
)

_ERROR_LABEL = "error"  # "" means success, else exception class name


def observe_provider_call(provider_name: str) -> _CallObserver:
    """Start observing one provider call."""
    return _CallObserver(provider_name)


class _CallObserver:
    """Records latency + outcome of one awaited coroutine."""

    def __init__(self, provider_name: str) -> None:
        self._provider = provider_name

    async def observe(self, coro: Coroutine[Any, Any, _T]) -> _T:
        import asyncio

        started = asyncio.get_running_loop().time()
        error_label = ""
        try:
            result = await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_label = type(exc).__name__
            raise
        finally:
            elapsed = asyncio.get_running_loop().time() - started
            PROVIDER_CALLS_TOTAL.labels(self._provider, error_label).inc()
            PROVIDER_CALL_SECONDS.labels(self._provider).observe(elapsed)
        return result


class MetricsProvider:
    """Wraps any :class:`~agentalyze.providers.base.Provider` implementation
    with metric collection. Structurally satisfies the Provider protocol;
    health checks are NOT counted as calls."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.name: str = inner.name

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        observer = _CallObserver(self.name)
        result: CompletionResult = await observer.observe(
            self._inner.chat_completion(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )
        return result

    async def health_check(self) -> bool:
        return bool(await self._inner.health_check())
