"""One task x one provider -> a complete ``RunTrace``, via smolagents' ``CodeAgent``
instead of the structured tool-calling ReAct loop (``runner/react_loop.py``).

Mirrors ``react_loop.run_task``'s contract and guarantees as closely as the
two action models allow: fresh ``FixtureServer`` + real Chromium per run,
teardown in guaranteed ``finally`` blocks, verification strictly AFTER the
agent's own run finishes (never leaked mid-run), and any unhandled exception
becomes ``RunOutcome.FAILURE_CRASH`` with the traceback preserved.

Step-granularity note
----------------------
``CodeAgent``'s own per-round record (``smolagents.ActionStep``) is one entry
per *code-generation round* — a single generated code block may call several
browser tools before returning, or none at all. Agentalyze's ``StepEvent``/
failure-taxonomy heuristics are written against one ``StepEvent`` per actual
tool invocation (matching the ReAct loop). This module reconciles the two by
reading ``ToolCallRecorder`` (populated by every ``tool_adapters.py`` adapter
call, at ReAct granularity) and bucketing its entries back into the
``ActionStep`` that produced them via wall-clock timestamp against
``ActionStep.timing`` — the two are recorded on the same thread, in the same
strict execution order, so this bucketing is exact, not a heuristic guess.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid
from datetime import UTC, datetime

import smolagents
from playwright.async_api import Page, async_playwright
from smolagents.utils import AgentGenerationError

from agentalyze.config import Settings
from agentalyze.providers.base import (
    ChatMessage,
    CompletionResult,
    Provider,
    ProviderError,
    ToolCall,
)
from agentalyze.runner.code_agent.model_adapter import (
    AgentalyzeModelAdapter,
    smolagents_message_to_agentalyze,
)
from agentalyze.runner.code_agent.tool_adapters import (
    ToolCallRecord,
    ToolCallRecorder,
    build_tool_adapters,
)
from agentalyze.runner.observation import build_observation
from agentalyze.runner.tools import ToolContext
from agentalyze.runner.trace import RunOutcome, RunTrace, StepEvent, save_trace
from agentalyze.tasks.fixture_server import FixtureServer
from agentalyze.tasks.models import Task, VerificationResult
from agentalyze.tasks.verifiers import VERIFIERS

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _build_instructions(task: Task) -> str:
    """Task-specific guidance appended to CodeAgent's own (tool-derived) system prompt.

    CodeAgent builds its own system prompt describing every registered
    ``Tool`` as a Python function signature; this only adds the same
    task-category nudge the ReAct loop's ``SYSTEM_PROMPT`` gives via
    ``extra_rules`` (see ``runner/react_loop.py``), so extraction tasks get
    consistent guidance regardless of which runner executes them.
    """
    lines = [
        ("You are a careful web automation agent working inside a real browser, "
        "acting by calling the provided Python tool functions from code you write."),
        ("Element ids (e.g. 'e3') come from the 'Current page state' text every "
        "tool call returns and change between steps — always use ids from the "
        "MOST RECENT tool output, never from memory."),
        "If an action fails, read the error and adapt; do not repeat it verbatim.",
        ("When you are sure the goal is achieved, call final_answer(success=True). "
        "If you cannot make progress at all, call final_answer(success=False) "
        "instead of looping forever."),
    ]
    if task.category.value == "extraction":
        lines.append(
            "For extraction tasks report what you found via "
            "final_answer(success=True, extracted_value=..., confidence=...) "
            "where confidence is in [0, 1]."
        )
    return "\n".join(lines)


def _synthesize_completion_result(step: smolagents.memory.ActionStep) -> CompletionResult | None:
    """Build an Agentalyze ``CompletionResult`` from one ``ActionStep``'s model call.

    Returns ``None`` when the step never produced a model response at all
    (e.g. a step that errored before generation) — callers must handle that
    case rather than fabricate a response that never happened.
    """
    if step.model_output_message is None:
        return None
    message = smolagents_message_to_agentalyze(step.model_output_message)
    token_usage = step.token_usage
    duration = step.timing.duration if step.timing else None
    return CompletionResult(
        message=message,
        prompt_tokens=token_usage.input_tokens if token_usage else 0,
        completion_tokens=token_usage.output_tokens if token_usage else 0,
        total_tokens=(token_usage.input_tokens + token_usage.output_tokens) if token_usage else 0,
        latency_seconds=duration or 0.0,
        finish_reason="error" if step.error else "stop",
    )


def _request_messages_for_step(step: smolagents.memory.ActionStep) -> list[ChatMessage]:
    if not step.model_input_messages:
        return []
    return [smolagents_message_to_agentalyze(m) for m in step.model_input_messages]


def _records_for_step(
    step: smolagents.memory.ActionStep, records: list[ToolCallRecord], cursor: int
) -> tuple[list[ToolCallRecord], int]:
    """Slice ``records`` (already time-ordered) to those made during ``step``.

    ``cursor`` is the index of the first not-yet-consumed record; records are
    consumed strictly in order, so this never re-attributes a call to two
    steps. A step with ``timing.end_time is None`` (should not normally
    happen for a completed step) takes every remaining record.
    """
    end = step.timing.end_time if step.timing else None
    consumed: list[ToolCallRecord] = []
    while cursor < len(records) and (end is None or records[cursor].timestamp <= end):
        consumed.append(records[cursor])
        cursor += 1
    return consumed, cursor


def _build_step_events(
    memory_steps: list[smolagents.memory.MemoryStep], recorder: ToolCallRecorder
) -> tuple[list[StepEvent], ToolCallRecord | None]:
    """Convert smolagents' per-round memory into ReAct-granularity ``StepEvent`` s.

    ``memory_steps`` (``agent.memory.steps``) mixes several ``MemoryStep``
    subclasses — a ``TaskStep`` recording the initial task, ``PlanningStep``s
    when planning is enabled, and the ``ActionStep``s this function actually
    cares about (each one model-call-and-code-execution round). Non-action
    steps carry no ``model_output_message``/``code_action`` and are skipped.

    Returns the event list plus the ``done``/``final_answer`` record (if any)
    so the caller can read the agent's declared success/failure/confidence.
    """
    events: list[StepEvent] = []
    done_record: ToolCallRecord | None = None
    cursor = 0
    step_number = 0

    action_steps = [s for s in memory_steps if isinstance(s, smolagents.memory.ActionStep)]
    for action_step in action_steps:
        completion = _synthesize_completion_result(action_step)
        request_messages = _request_messages_for_step(action_step)
        records, cursor = _records_for_step(action_step, recorder.records, cursor)

        if completion is None and not records:
            # A round that neither produced a model response nor any tool
            # call (should be rare) carries no information worth a StepEvent.
            continue

        if not records:
            # The model generated code, but it raised/failed to parse before
            # any tool ran (or the round produced no tool call at all).
            step_number += 1
            error_text = None
            if action_step.error is not None:
                error_text = f"{type(action_step.error).__name__}: {action_step.error}"
            events.append(
                StepEvent(
                    step_number=step_number,
                    timestamp=_utcnow(),
                    llm_request_messages=request_messages,
                    llm_response=completion or _empty_completion(),
                    tool_error=error_text,
                )
            )
            continue

        for record in records:
            step_number += 1
            if record.tool_name == "done":
                done_record = record
            events.append(
                StepEvent(
                    step_number=step_number,
                    timestamp=_utcnow(),
                    llm_request_messages=request_messages,
                    llm_response=completion or _empty_completion(),
                    tool_call=ToolCall(
                        id=f"{action_step.step_number}-{step_number}",
                        name=record.tool_name,
                        arguments=record.arguments,
                    ),
                    tool_result=record.result,
                )
            )

    return events, done_record


def _empty_completion() -> CompletionResult:
    """Placeholder for a StepEvent whose owning ActionStep had no model call.

    ``StepEvent.llm_response`` is non-optional by schema (every ReAct step
    really did call the model); a code-agent step attributed only via
    timestamp bucketing without its own ``model_output_message`` still needs
    *some* value here rather than failing validation, so this returns an
    explicitly empty result instead of fabricating token counts.
    """
    return CompletionResult(
        message=ChatMessage(role="assistant", content=""),
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        latency_seconds=0.0,
        finish_reason="stop",
    )


class _CycleResult:
    def __init__(self) -> None:
        self.steps: list[StepEvent] = []
        self.outcome: RunOutcome | None = None
        self.verifier_result: VerificationResult | None = None
        self.error: str | None = None
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0


async def _run_code_agent_cycle(
    task: Task,
    provider: Provider,
    page: Page,
    base_url: str,
    deadline_monotonic: float,
    settings: Settings,
    state: _CycleResult,
) -> None:
    # Both the Playwright Page and (potentially) the provider's HTTP client
    # are bound to THIS loop; CodeAgent.run() executes on a worker thread
    # (see below), so every adapter must marshal its calls back here rather
    # than spin up a fresh loop in that thread — see ToolCallRecorder.loop's
    # and AgentalyzeModelAdapter's docstrings for why (verified empirically:
    # the naive fresh-loop-per-call version deadlocked on the first tool call).
    owning_loop = asyncio.get_running_loop()
    ctx = ToolContext(page=page, base_url=base_url)
    recorder = ToolCallRecorder(loop=owning_loop)
    tools = build_tool_adapters(ctx, recorder)
    model = AgentalyzeModelAdapter(provider, loop=owning_loop)

    executor_type = settings.code_agent_executor_type
    if executor_type in ("docker", "e2b", "modal", "blaxel"):
        # Verified empirically, not assumed: smolagents' remote executors
        # reconstruct each Tool inside the sandbox via a bare
        # `ToolClassName()` call (smolagents.tools.get_tools_definition_code)
        # — no constructor arguments. Every adapter in tool_adapters.py
        # requires (ctx, recorder): a live Playwright Page and the event
        # loop that owns it, neither serializable into a sandbox. Trying
        # this anyway does not raise a clear error — it silently hangs
        # until the task's wall-clock timeout (confirmed with a real Docker
        # daemon: agent.run() never returned, zero steps were recorded, and
        # the run just sat there until FAILURE_TIMEOUT). Fail fast instead.
        # See docs/KNOWN_LIMITATIONS.md.
        msg = (
            f"code_agent_executor_type={executor_type!r} is not supported by "
            "this project's tool adapters: smolagents' remote executors "
            "reconstruct every Tool with a zero-argument constructor inside "
            "the sandbox, but agentalyze's browser-tool adapters require "
            "(ctx, recorder) — a live Playwright Page and its owning event "
            "loop, which cannot be sent into a sandboxed process. Use "
            "executor_type='local' (development/tests only, not a security "
            "boundary) until tool_adapters.py is re-architected as a "
            "host-side RPC bridge. See docs/KNOWN_LIMITATIONS.md."
        )
        raise NotImplementedError(msg)
    if executor_type == "local":
        logger.warning(
            "code_agent_executor_type='local': smolagents' LocalPythonExecutor "
            "is NOT a security sandbox (per its own docstring) — only use this "
            "for development/tests with a FakeProvider that never generates "
            "real model code. See docs/KNOWN_LIMITATIONS.md."
        )

    agent = smolagents.CodeAgent(
        tools=tools,
        model=model,
        max_steps=task.max_steps,
        executor_type=executor_type,
        instructions=_build_instructions(task),
    )

    try:
        await _drive_code_agent(agent, task, deadline_monotonic, recorder, page, state)
    finally:
        # Every CompletionResult the wrapped Provider actually returned is the
        # ground truth for token accounting, regardless of which branch above
        # ended the run (including exceptions) — see model_adapter.py.
        state.total_prompt_tokens = sum(c.prompt_tokens for c in model.completions)
        state.total_completion_tokens = sum(c.completion_tokens for c in model.completions)


async def _drive_code_agent(
    agent: smolagents.CodeAgent,
    task: Task,
    deadline_monotonic: float,
    recorder: ToolCallRecorder,
    page: Page,
    state: _CycleResult,
) -> None:
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        state.outcome = RunOutcome.FAILURE_TIMEOUT
        return

    # Unlike the ReAct loop's _initial_user_message, CodeAgent.run(task_text)
    # gets no page state of its own — the browser was already navigated to
    # the fixture BEFORE this function ran, but the model has no way to know
    # what's there until it makes its first tool call otherwise. Give it the
    # same "TASK + Current page state" opener react_loop builds.
    initial_observation = await build_observation(page)
    task_text = f"TASK: {task.description}\n\nCurrent page state:\n{initial_observation.text}"

    try:
        run_result = await asyncio.wait_for(
            asyncio.to_thread(agent.run, task_text, return_full_result=True),
            timeout=remaining,
        )
    except TimeoutError:
        state.steps, _ = _build_step_events(agent.memory.steps, recorder)
        state.outcome = RunOutcome.FAILURE_TIMEOUT
        return
    except asyncio.CancelledError:
        raise
    except AgentGenerationError as exc:
        # Raised when Model.generate() itself failed — i.e. the wrapped
        # Provider raised a ProviderError smolagents could not recover from.
        # See model_adapter.py: this adapter never retries on its own, so a
        # ProviderError surfacing here means the caller's own
        # RetryingProvider (if any) already exhausted its attempts.
        state.steps, _ = _build_step_events(agent.memory.steps, recorder)
        state.outcome = RunOutcome.FAILURE_PROVIDER_ERROR
        state.error = f"{type(exc).__name__}: {exc}"
        return
    except ProviderError as exc:
        state.steps, _ = _build_step_events(agent.memory.steps, recorder)
        state.outcome = RunOutcome.FAILURE_PROVIDER_ERROR
        state.error = f"{type(exc).__name__}: {exc}"
        return

    state.steps, done_record = _build_step_events(agent.memory.steps, recorder)

    if done_record is not None:
        # The agent called final_answer (do_done) itself — verification
        # happens strictly after this point, never before, matching the
        # ReAct loop's honesty invariant.
        verifier = VERIFIERS[task.verifier_id]
        verifier_result = await verifier.verify(page)
        state.verifier_result = verifier_result
        state.outcome = RunOutcome.SUCCESS if verifier_result.success else RunOutcome.FAILURE_VERIFIER
        return

    if run_result.state == "max_steps_error":
        state.outcome = RunOutcome.FAILURE_MAX_STEPS
        return

    # CodeAgent finished (returned an output) without ever calling our
    # final_answer/do_done adapter — should not happen since our DoneTool IS
    # the registered final_answer tool (see tool_adapters.py), but budget
    # exhaustion is the closest honest classification if it ever does.
    state.outcome = RunOutcome.FAILURE_MAX_STEPS


async def run_task_code_agent(task: Task, provider: Provider, settings: Settings) -> RunTrace:
    """Run one task with one provider via ``CodeAgent`` and persist the trace.

    Same guarantees as ``react_loop.run_task``: guaranteed teardown of the
    fixture server and browser, ``FAILURE_CRASH`` (with traceback) for any
    unhandled exception, verification only after the agent's own run ends.
    """
    run_id = str(uuid.uuid4())
    started_at = _utcnow()
    start_monotonic = time.monotonic()
    deadline_monotonic = start_monotonic + task.timeout_seconds

    state = _CycleResult()
    server = FixtureServer(root=settings.fixtures_dir)
    server.start()
    try:
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                try:
                    page = await context.new_page()
                    await page.goto(server.base_url + task.fixture_url_path, wait_until="load")
                    await _run_code_agent_cycle(
                        task=task,
                        provider=provider,
                        page=page,
                        base_url=server.base_url,
                        deadline_monotonic=deadline_monotonic,
                        settings=settings,
                        state=state,
                    )
                finally:
                    await context.close()
            finally:
                await browser.close()
        finally:
            await pw.stop()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - last resort: record, never crash the runner process
        state.outcome = RunOutcome.FAILURE_CRASH
        state.error = "".join(traceback.format_exception(exc))
    finally:
        server.stop()

    assert state.outcome is not None  # set by every exit path above

    trace = RunTrace(
        run_id=run_id,
        task_id=task.id,
        task_category=task.category,
        provider_name=provider.name,
        agent_style="code",
        started_at=started_at,
        finished_at=_utcnow(),
        outcome=state.outcome,
        verifier_result=state.verifier_result,
        steps=state.steps,
        total_prompt_tokens=state.total_prompt_tokens,
        total_completion_tokens=state.total_completion_tokens,
        total_cost_usd=None,
        wall_clock_seconds=time.monotonic() - start_monotonic,
        error=state.error,
    )
    settings.ensure_results_dir()
    save_trace(trace, settings.results_dir)
    return trace
