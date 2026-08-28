"""Adapter: ``agentalyze.providers.base.Provider`` -> ``smolagents.Model``.

``CodeAgent`` talks to models through ``smolagents.Model.generate(...)``,
which is a *synchronous* method returning a ``smolagents.ChatMessage``.
Agentalyze's own ``Provider.chat_completion(...)`` is async and speaks a
different (but structurally similar) message model
(``agentalyze.providers.base.ChatMessage``). This module is a thin,
lossless translation layer between the two — it does not reimplement any
provider logic, retry policy, or prompting: it only converts messages in
both directions and calls the wrapped ``Provider`` exactly once per
``generate()`` call.

Role-name mismatch (verified against installed smolagents==1.26.0, and the
mapping itself corrected via a live end-to-end run — see below)
------------------------------------------------------------------------
The two ``ChatMessage`` types use different role vocabularies:

* Agentalyze: ``system | user | assistant | tool``
* smolagents (``smolagents.models.MessageRole``): ``system | user |
  assistant | tool-call | tool-response``

``CodeAgent`` does not use OpenAI-style ``tool_calls``/``tool_call_id``
plumbing at all (tools are invoked as plain Python calls inside a code
block, not as structured function calls) — so in practice the messages
``CodeAgent`` sends to ``generate()`` only ever use
``system``/``user``/``assistant``/``tool-response`` roles, never
``tool-call`` (that role exists in smolagents for *rendering* completed
steps back into transcripts, not for what gets sent to `generate`).
``tool-response`` maps to Agentalyze's ``user`` role, NOT ``tool`` — see
``_SMOLAGENTS_TO_AGENTALYZE_ROLE``'s docstring for why (a real bug, found by
actually running ``agentalyze run --agent-style code`` against a live Ollama
provider, not by code review alone).

Tool-calling vs code-generation: this adapter intentionally does NOT do
anything with ``tools_to_call_from`` — that parameter exists on
``Model.generate`` for parity with ``ToolCallingAgent`` (smolagents' OTHER
agent class, structured-tool-calling style), which this project does not
use. ``CodeAgent`` describes tools to the model as Python function
signatures baked into the system prompt (built by smolagents itself from
the ``Tool`` objects passed to ``CodeAgent(tools=[...])``), so this adapter
must never be reused for a structured tool-calling path — it is
code-generation-only by construction.
"""

from __future__ import annotations

import asyncio
import time

import smolagents

from agentalyze.providers.base import (
    ChatMessage as AgentalyzeChatMessage,
)
from agentalyze.providers.base import (
    CompletionResult,
    Provider,
)

#: smolagents role -> agentalyze role.
#:
#: ``tool-response`` maps to ``user``, NOT ``tool`` — found and fixed via a
#: real end-to-end run against a live OpenAI-compatible provider (Ollama),
#: not by inspection alone. Agentalyze's OpenAI-compatible provider
#: (``providers/openai_compatible.py``) enforces the real OpenAI API
#: constraint that a ``role='tool'`` message MUST carry a ``tool_call_id``
#: matching a preceding assistant ``tool_calls`` entry. ``CodeAgent`` never
#: produces either side of that pairing (no structured ``tool_calls``, no
#: matching id) — its "tool response" is really just the next turn of
#: conversation (execution logs/observations), which is exactly what
#: Agentalyze's ``user`` role is for. Mapping it to ``tool`` instead made
#: every real-provider run fail after step 1 with "role='tool' messages
#: require tool_call_id" (confirmed with `agentalyze run --agent-style code`
#: against a live Ollama model). ``tool-call`` maps to ``assistant`` for the
#: same underlying reason: CodeAgent folds the "model asked to call a tool"
#: step into ordinary assistant-authored code, so there is no distinct
#: Agentalyze message type for it either.
_SMOLAGENTS_TO_AGENTALYZE_ROLE: dict[str, str] = {
    "system": "system",
    "user": "user",
    "assistant": "assistant",
    "tool-call": "assistant",
    "tool-response": "user",
}

#: agentalyze role -> smolagents role (the inverse direction, used for the
#: messages CodeAgent hands us to forward to the Provider). Agentalyze's
#: ``tool`` role never appears among these messages in practice (this
#: adapter never manufactures one — see above), but the mapping stays
#: complete/total for the round-trip tests.
_AGENTALYZE_TO_SMOLAGENTS_ROLE: dict[str, str] = {
    "system": "system",
    "user": "user",
    "assistant": "assistant",
    "tool": "tool-response",
}


def _content_to_text(content: str | list[dict[str, object]] | None) -> str:
    """smolagents message ``content`` can be a string or a list of content
    parts (``[{"type": "text", "text": "..."}, ...]``, possibly with image
    parts). Agentalyze's ``ChatMessage.content`` is a plain string, so
    collapse smolagents' richer representation into text — Agentalyze's
    browser tool set is text-only (see ``runner/tools.py``), it never sends
    or expects image content parts, so no information relevant to this
    project is lost by flattening.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def smolagents_message_to_agentalyze(message: smolagents.ChatMessage) -> AgentalyzeChatMessage:
    """Convert one ``smolagents.ChatMessage`` into an Agentalyze ``ChatMessage``.

    Lossy only in the sense described in ``_content_to_text``: image content
    parts (never produced by this project's tool set) are dropped rather than
    silently corrupted into garbage text.
    """
    role = message.role.value if hasattr(message.role, "value") else str(message.role)
    agentalyze_role = _SMOLAGENTS_TO_AGENTALYZE_ROLE.get(role)
    if agentalyze_role is None:
        msg = f"unknown smolagents message role: {role!r}"
        raise ValueError(msg)
    return AgentalyzeChatMessage(
        role=agentalyze_role,  # type: ignore[arg-type]
        content=_content_to_text(message.content),
    )


def agentalyze_message_to_smolagents(message: AgentalyzeChatMessage) -> smolagents.ChatMessage:
    """Convert one Agentalyze ``ChatMessage`` into a ``smolagents.ChatMessage``.

    Agentalyze's ``tool_call_id``/``tool_calls`` fields are never populated on
    the code-generation path (CodeAgent never emits or expects OpenAI-style
    structured tool calls), so they are intentionally not consulted here.
    """
    smolagents_role = _AGENTALYZE_TO_SMOLAGENTS_ROLE.get(message.role)
    if smolagents_role is None:
        msg = f"unknown agentalyze message role: {message.role!r}"
        raise ValueError(msg)
    return smolagents.ChatMessage(role=smolagents_role, content=message.content)


class AgentalyzeModelAdapter(smolagents.Model):  # type: ignore[misc]  # smolagents ships no py.typed marker
    """Wraps an Agentalyze ``Provider`` so ``smolagents.CodeAgent`` can drive it.

    Construct with an already-configured ``Provider`` — typically the same
    instance ``providers.factory.load_providers`` hands to the tool-calling
    runner, which means it may already be wrapped in
    ``agentalyze.providers.retry.RetryingProvider``. This adapter does not
    add its own retry logic: it calls ``provider.chat_completion(...)``
    exactly once per ``generate()`` call and lets whatever wrapping the
    caller already applied do its job. See
    ``tests/runner/code_agent/test_model_adapter.py::test_retry_propagates``
    for a concrete test that a ``RetryingProvider``-wrapped ``FakeProvider``
    really does retry through this adapter, rather than assuming it from the
    fact that composition "should" work.

    ``smolagents.Model`` is a plain (non-dataclass) base class whose
    ``__init__`` sets several attributes smolagents' own internals read
    later (``flatten_messages_as_text``, ``tool_name_key``,
    ``tool_arguments_key``, ``kwargs``, ``model_id`` — verified against the
    installed ``smolagents==1.26.0`` source). This adapter therefore calls
    ``super().__init__(model_id=...)`` rather than skipping the base
    constructor, so every attribute smolagents expects to find on a
    ``Model`` actually exists.

    ``completions``: every ``CompletionResult`` returned by the wrapped
    provider across this adapter's lifetime, in call order. ``RunTrace``
    construction (see ``code_agent/loop.py``) needs prompt/completion token
    counts and per-step latency; ``smolagents.ActionStep.token_usage`` /
    ``.timing`` do carry *some* of this from ``agent.memory`` after the run,
    but this list is the adapter's own ground truth for the exact
    ``CompletionResult`` the wrapped ``Provider`` returned on each call
    (including retry-related latency the ``ActionStep`` view would not
    separately expose), used by ``loop.py`` to cross-check/backfill.
    """

    def __init__(
        self, provider: Provider, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        """``loop``: the event loop that owns ``provider``'s connections.

        Real providers (``providers/openai_compatible.py`` and friends) hold
        a persistent async HTTP client created against a specific event
        loop; that client's pooled connections cannot be driven from a
        *different* loop (this was verified the hard way: an earlier version
        of this adapter called ``asyncio.run(...)`` here unconditionally,
        which creates a brand-new loop on every single call — for the
        Playwright ``Page`` used by the sibling ``tool_adapters.py`` this
        deadlocked outright, and the same cross-loop hazard applies to any
        provider holding loop-bound connections). When ``loop`` is given,
        ``generate()`` marshals the call onto it via
        ``asyncio.run_coroutine_threadsafe`` instead of spinning up a new
        loop. ``loop`` is optional only so unit tests can call ``generate()``
        directly from a thread with no running loop at all (see
        ``tests/runner/code_agent/test_model_adapter.py``), using a
        FakeProvider that (unlike a real provider) never holds any
        loop-bound resource — never omit it against a real ``Provider``.
        """
        super().__init__(model_id=getattr(provider, "name", "agentalyze-provider"))
        self.provider = provider
        self.loop = loop
        self.completions: list[CompletionResult] = []

    def generate(
        self,
        messages: list[smolagents.ChatMessage],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[smolagents.Tool] | None = None,
        **kwargs: object,
    ) -> smolagents.ChatMessage:
        """Synchronous entry point required by ``smolagents.Model``.

        ``CodeAgent`` calls this from a plain (non-async) call stack — it has
        no awareness that Agentalyze providers are async. This adapter is
        used from ``code_agent/loop.py`` inside a worker thread spawned via
        ``asyncio.to_thread(agent.run, ...)``; see the constructor docstring
        for why the call is marshaled onto ``self.loop`` (the thread that
        owns the Playwright connection and the provider's HTTP client)
        rather than run on a fresh loop created here.

        ``tools_to_call_from`` is intentionally ignored — see the module
        docstring: this adapter is code-generation-only, and ``CodeAgent``
        does not populate this argument in the way ``ToolCallingAgent``
        would.
        """
        agentalyze_messages = [smolagents_message_to_agentalyze(m) for m in messages]

        raw_temperature = kwargs.get("temperature", 0.0)
        temperature = float(raw_temperature) if isinstance(raw_temperature, (int, float, str)) else 0.0
        raw_max_tokens = kwargs.get("max_tokens")
        max_tokens = raw_max_tokens if isinstance(raw_max_tokens, int) else None

        async def _call() -> CompletionResult:
            return await self.provider.chat_completion(
                messages=agentalyze_messages,
                tools=None,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        start = time.monotonic()
        if self.loop is not None:
            result = asyncio.run_coroutine_threadsafe(_call(), self.loop).result()
        else:
            result = asyncio.run(_call())
        elapsed = time.monotonic() - start
        # If the provider's own latency_seconds is zero/unset (e.g. a
        # hand-written FakeProvider in tests didn't bother), fall back to
        # our own wall-clock measurement so downstream latency reporting is
        # never silently zero for a real call.
        if result.latency_seconds <= 0:
            result = result.model_copy(update={"latency_seconds": elapsed})
        self.completions.append(result)

        response = smolagents_chat_message_from_result(result)
        response.token_usage = smolagents.TokenUsage(
            input_tokens=result.prompt_tokens,
            output_tokens=result.completion_tokens,
        )
        return response


def smolagents_chat_message_from_result(result: CompletionResult) -> smolagents.ChatMessage:
    """``CompletionResult.message`` (Agentalyze) -> ``smolagents.ChatMessage``."""
    return agentalyze_message_to_smolagents(result.message)
