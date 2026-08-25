"""The ReAct loop: one task x one provider -> a complete RunTrace.

Pipeline per run: start a fresh ``FixtureServer``, launch real Chromium,
open the task fixture, then loop (Reason -> Act -> Observe) until the agent
calls ``done``, a budget runs out, or an unrecoverable error occurs. Only
afterwards is the Phase 1 verifier consulted — never before, so no success
signal can leak to the agent mid-run. Browser context and fixture server are
torn down in guaranteed ``finally`` blocks; any unhandled runner exception
becomes ``FAILURE_CRASH`` with the full traceback preserved in the trace,
never a dead Chromium process or a crashed runner process.
"""

from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import Page, async_playwright

from agentalyze.config import Settings
from agentalyze.providers.base import (
    ChatMessage,
    CompletionResult,
    Provider,
    ProviderError,
    ToolCall,
)
from agentalyze.runner.observation import PageObservation, build_observation
from agentalyze.runner.tools import DONE_TOOL_NAME, TOOL_SPECS, ToolContext, execute_tool
from agentalyze.runner.trace import RunOutcome, RunTrace, StepEvent, ToolResult, save_trace
from agentalyze.tasks.fixture_server import FixtureServer
from agentalyze.tasks.models import Task, VerificationResult
from agentalyze.tasks.verifiers import VERIFIERS


def _utcnow() -> datetime:
    return datetime.now(UTC)


SYSTEM_PROMPT = """\
You are a careful web automation agent working inside a real browser.

You act ONLY through tools. Available tools:
{tool_list}

After every action you receive:
1. The result of your tool call, and
2. A fresh ELEMENTS snapshot of the page with ids like [e3].

Rules:
- Element ids change between steps. Always use ids from the LATEST snapshot.
- If an action fails, read the error and adapt; do not repeat it verbatim.
- When you are sure the goal is achieved, call done(success=true).
  If you cannot make progress at all, call done(success=false) instead of looping forever.
{extra_rules}
"""


def _build_system_prompt(task: Task) -> str:
    tool_list = "\n".join(f"- {s.name}: {s.description}" for s in TOOL_SPECS)
    extra = ""
    if task.category.value == "extraction":
        extra = (
            "- For extraction tasks report what you found via "
            "done(success=true, extracted_value=..., confidence=...) where "
            "confidence is in [0, 1]."
        )
    return SYSTEM_PROMPT.format(tool_list=tool_list, extra_rules=extra)


def _initial_user_message(task: Task, observation: PageObservation) -> str:
    return f"TASK: {task.description}\n\nCurrent page state:\n{observation.text}"


async def _take_screenshot(page: Page, run_artifact_dir: Path, step_number: int) -> str | None:
    """Save a PNG of the current page state; failures must not kill the run."""
    try:
        directory = run_artifact_dir / "screenshots"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"step_{step_number}.png"
        await page.screenshot(path=str(path))
        return str(path)
    except Exception:  # noqa: BLE001 - screenshotting is best-effort
        return None


class _CycleResult:
    """Mutable accumulators shared between the loop and run_task."""

    def __init__(self) -> None:
        self.steps: list[StepEvent] = []
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.outcome: RunOutcome | None = None
        self.verifier_result: VerificationResult | None = None
        self.error: str | None = None


async def _run_react_cycle(
    task: Task,
    provider: Provider,
    page: Page,
    base_url: str,
    deadline_monotonic: float,
    run_artifact_dir: Path,
    state: _CycleResult,
) -> None:
    """Run the ReAct loop itself; sets ``state.outcome`` and friends."""
    ctx = ToolContext(page=page, base_url=base_url)
    messages: list[ChatMessage] = [ChatMessage(role="system", content=_build_system_prompt(task))]

    await page.goto(base_url + task.fixture_url_path, wait_until="load")
    observation = await build_observation(page)
    messages.append(ChatMessage(role="user", content=_initial_user_message(task, observation)))

    finished_by_done = False
    timed_out = False

    for step_number in range(1, task.max_steps + 1):
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break

        request_snapshot = list(messages)
        # The provider wait consumes the REMAINING task budget, not a fresh
        # timeout per call — the task's wall-clock limit stays authoritative.
        try:
            completion: CompletionResult = await asyncio.wait_for(
                provider.chat_completion(list(messages), tools=TOOL_SPECS),
                timeout=remaining,
            )
        except TimeoutError:
            timed_out = True
            break
        except asyncio.CancelledError:
            raise
        except ProviderError as exc:
            state.outcome = RunOutcome.FAILURE_PROVIDER_ERROR
            state.error = f"{type(exc).__name__}: {exc}"
            return

        state.total_prompt_tokens += completion.prompt_tokens
        state.total_completion_tokens += completion.completion_tokens

        tool_calls = completion.message.tool_calls or []
        tool_call: ToolCall | None = tool_calls[0] if tool_calls else None

        if tool_call is None:
            # Model "thought out loud" without acting: record it, nudge, retry.
            messages.append(completion.message)
            messages.append(
                ChatMessage(
                    role="user",
                    content=(
                        "You did not call any tool. Act through one of the "
                        f"available tools ({', '.join(s.name for s in TOOL_SPECS)}) "
                        "or declare the result with done(...)."
                    ),
                )
            )
            state.steps.append(
                StepEvent(
                    step_number=step_number,
                    timestamp=_utcnow(),
                    llm_request_messages=request_snapshot,
                    llm_response=completion,
                )
            )
            continue

        messages.append(completion.message)  # assistant message with the tool call

        if tool_call.name == DONE_TOOL_NAME:
            # The agent declared completion. The verifier is NOT consulted
            # before this point — no success signal may leak mid-run.
            ack = await execute_tool(ctx, tool_call)
            state.steps.append(
                StepEvent(
                    step_number=step_number,
                    timestamp=_utcnow(),
                    llm_request_messages=request_snapshot,
                    llm_response=completion,
                    tool_call=tool_call,
                    tool_result=ack,
                )
            )
            finished_by_done = True
            break

        tool_error: str | None = None
        try:
            result: ToolResult = await execute_tool(ctx, tool_call)
        except Exception as exc:  # noqa: BLE001 - unhandled tool bug, not an expected failure
            tool_error = "".join(traceback.format_exception(exc))
        else:
            # Fresh observation after every executed action.
            observation = await build_observation(page)
            result.dom_snapshot_hash = observation.dom_hash
            screenshot_path = await _take_screenshot(page, run_artifact_dir, step_number)
            messages.append(
                ChatMessage(
                    role="tool",
                    content=f"{result.output}\n\nCurrent page state:\n{observation.text}",
                    tool_call_id=tool_call.id,
                )
            )
            state.steps.append(
                StepEvent(
                    step_number=step_number,
                    timestamp=_utcnow(),
                    llm_request_messages=request_snapshot,
                    llm_response=completion,
                    tool_call=tool_call,
                    tool_result=result,
                    tool_error=tool_error,
                    screenshot_path=screenshot_path,
                )
            )
            continue

        state.steps.append(
            StepEvent(
                step_number=step_number,
                timestamp=_utcnow(),
                llm_request_messages=request_snapshot,
                llm_response=completion,
                tool_call=tool_call,
                tool_result=ToolResult(success=False, output="Tool crashed with an unhandled error."),
                tool_error=tool_error,
            )
        )
        state.outcome = RunOutcome.FAILURE_TOOL_ERROR
        state.error = tool_error
        return

    if timed_out:
        state.outcome = RunOutcome.FAILURE_TIMEOUT
        return
    if not finished_by_done:
        state.outcome = RunOutcome.FAILURE_MAX_STEPS
        return

    # Verification happens only after the agent itself called done().
    verifier = VERIFIERS[task.verifier_id]
    verifier_result = await verifier.verify(page)
    state.verifier_result = verifier_result
    state.outcome = RunOutcome.SUCCESS if verifier_result.success else RunOutcome.FAILURE_VERIFIER


async def run_task(task: Task, provider: Provider, settings: Settings) -> RunTrace:
    """Run one task with one provider end-to-end and persist the trace.

    Guarantees:
    - the fixture server, browser context and browser are always torn down,
      even on crashes (``finally`` at every resource level);
    - any unhandled runner exception becomes ``RunOutcome.FAILURE_CRASH``
      with the full traceback in ``RunTrace.error``;
    - the resulting trace is saved to
      ``{settings.results_dir}/{run_id}/trace.json``.
    """
    run_id = str(uuid.uuid4())
    started_at = _utcnow()
    start_monotonic = time.monotonic()
    deadline_monotonic = start_monotonic + task.timeout_seconds
    run_artifact_dir = Path(settings.results_dir) / run_id

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
                    await _run_react_cycle(
                        task=task,
                        provider=provider,
                        page=page,
                        base_url=server.base_url,
                        deadline_monotonic=deadline_monotonic,
                        run_artifact_dir=run_artifact_dir,
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
        task_category=task.category,  # Phase 4: enables per-category metrics
        provider_name=provider.name,
        started_at=started_at,
        finished_at=_utcnow(),
        outcome=state.outcome,
        verifier_result=state.verifier_result,
        steps=state.steps,
        total_prompt_tokens=state.total_prompt_tokens,
        total_completion_tokens=state.total_completion_tokens,
        total_cost_usd=None,  # no pricing configured yet — never invent a number
        wall_clock_seconds=time.monotonic() - start_monotonic,
        error=state.error,
    )
    settings.ensure_results_dir()
    save_trace(trace, settings.results_dir)
    return trace



