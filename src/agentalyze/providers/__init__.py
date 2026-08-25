"""Provider abstraction for Agentalyze.

A single provider interface (:class:`agentalyze.providers.base.Provider`)
hides concrete LLM backends behind one async ``chat_completion`` call plus a
cheap ``health_check``. Two implementations ship out of the box:

* :class:`agentalyze.providers.openai_compatible.OpenAICompatibleProvider` —
  any API following the OpenAI ``/v1/chat/completions`` contract.
* :class:`agentalyze.providers.ollama.OllamaProvider` — a thin wrapper around
  the former, pointed at a local Ollama server.

The public interface is deliberately implementation-agnostic: no OpenAI SDK
types or Ollama-specific fields leak through it, so the Phase 3 runner works
exclusively with these models and ``ProviderError`` subclasses.
"""

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
from agentalyze.providers.factory import load_providers

__all__ = [
    "RETRYABLE_PROVIDER_ERRORS",
    "ChatMessage",
    "CompletionResult",
    "Provider",
    "ProviderAuthError",
    "ProviderConfigError",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderInvalidResponseError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ToolCall",
    "ToolSpec",
    "load_providers",
]
