"""Adapters: existing async browser-tool functions (``runner/tools.py``) ->
``smolagents.Tool`` subclasses that ``CodeAgent`` can call as plain Python
functions from generated code.

Every adapter's ``forward()`` is a thin synchronous wrapper around the
existing ``do_*`` coroutine — it does not reimplement any tool logic,
element-resolution, or the SSRF/origin guard. That guard lives entirely
inside ``ToolContext.check_url`` (called from ``do_navigate`` itself, see
``runner/tools.py``), so any caller of ``do_navigate`` — the ReAct loop or
this adapter — inherits it automatically; there is nothing to duplicate or
re-verify here (verified by ``tests/runner/code_agent/test_ssrf_guard_via_code_agent.py``,
a real-Chromium test that exercises this exact code path).

Sync-over-async, verified rather than assumed
----------------------------------------------
``smolagents.Tool`` (installed version 1.26.0) has no native async support:
``forward`` is a plain sync method and ``Tool`` carries no ``is_async``
attribute or comparable hook. Every adapter below therefore runs its wrapped
coroutine from ``forward``, on the worker thread ``code_agent/loop.py``
spawns for ``CodeAgent.run(...)`` (via ``asyncio.to_thread(...)``) — a thread
with no event loop of its own. A naive ``asyncio.run(coro)`` there would spin
up a *fresh* loop per call, which is NOT safe here: it was tried, and it
deadlocked on the very first ``navigate`` call, because the Playwright
``Page`` these tools drive is bound to the loop that created it (the loop
running ``run_task_code_agent``), not to whatever throwaway loop
``asyncio.run`` creates in this thread. Every adapter therefore marshals its
coroutine onto that owning loop via ``asyncio.run_coroutine_threadsafe``
(see ``ToolCallRecorder.loop``'s docstring below); the bare-``asyncio.run``
path only remains as a fallback for unit tests that mock the ``do_*``
functions and never touch a real ``Page``.

Feeding the model a fresh observation after every action
----------------------------------------------------------
The ReAct loop (``runner/react_loop.py``) appends a fresh ELEMENTS snapshot
(``build_observation``) to every tool result before showing it to the model
— that snapshot, not just the action's own success/failure text, is what
lets the model find the *next* element to act on. A ``CodeAgent`` tool must
do the same: each adapter's ``forward()`` re-observes the page after its
action and appends the same "Current page state:" block used by the ReAct
loop, so the model driving ``CodeAgent`` has exactly the same information
available at exactly the same point in its own loop.

One shared ``ToolContext`` and ``ToolCallRecorder`` per task run
------------------------------------------------------------------
All adapters below take an already-constructed ``ToolContext`` (the live
Playwright ``Page`` plus the run's allowed origin) and ``ToolCallRecorder``
in their constructor — the same two objects, shared across every tool
instance for one ``run_task_code_agent`` call — rather than building either
per invocation.

``ToolCallRecorder`` exists because ``CodeAgent``'s own per-step record
(``smolagents.ActionStep``) is one entry per *code-generation round*, not
per individual tool call — a single generated code block can call
``click(...)`` and then ``extract_text(...)`` in the same step. Agentalyze's
``StepEvent``/failure-taxonomy heuristics (``analysis/failure_taxonomy.py``)
are written against one ``StepEvent`` per actual tool invocation (matching
what the ReAct loop produces), so ``code_agent/loop.py`` reconstructs that
granularity for the trace by reading this recorder rather than by walking
``ActionStep`` alone. Every adapter call appends one entry here: tool name,
arguments, the resulting ``ToolResult`` (with ``dom_snapshot_hash`` filled
in, unlike the bare ``do_*`` return value), and a wall-clock timestamp used
by ``loop.py`` to bucket recorder entries back into their owning
``ActionStep`` via ``ActionStep.timing``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field

import smolagents

from agentalyze.runner.observation import build_observation
from agentalyze.runner.tools import (
    ToolContext,
    do_click,
    do_done,
    do_extract_text,
    do_navigate,
    do_select_option,
    do_submit_form,
    do_type_text,
    do_wait_for,
)
from agentalyze.runner.trace import ToolResult


@dataclass
class ToolCallRecord:
    """One actual tool invocation, at the same granularity as a ReAct ``StepEvent``."""

    timestamp: float
    tool_name: str
    arguments: dict[str, object]
    result: ToolResult


@dataclass
class ToolCallRecorder:
    """Shared, append-only log of every tool call made during one CodeAgent run.

    ``loop``: the event loop that owns the Playwright ``Page`` these tools
    drive (the loop running ``code_agent/loop.py``'s ``run_task_code_agent``).
    ``forward()`` runs on a *worker thread* (spawned via
    ``asyncio.to_thread(agent.run, ...)``), and async Playwright objects are
    bound to the loop that created them — calling ``page.goto()`` etc. from a
    *different* loop (e.g. one freshly created by a bare ``asyncio.run(...)``
    inside that thread) hangs rather than raising, which is exactly what an
    earlier version of this module did (verified empirically: a smoke run
    calling ``navigate`` from a fresh per-call loop deadlocked on the very
    first tool call). Every adapter below therefore marshals its coroutine
    onto ``loop`` via ``asyncio.run_coroutine_threadsafe`` instead. ``loop``
    is optional only so unit tests can call adapters with mocked ``do_*``
    functions (no real Playwright ``Page`` involved) from a thread with no
    running loop at all — never omit it against a real ``Page``.
    """

    records: list[ToolCallRecord] = field(default_factory=list)
    loop: asyncio.AbstractEventLoop | None = None

    def record(self, tool_name: str, arguments: dict[str, object], result: ToolResult) -> None:
        self.records.append(
            ToolCallRecord(
                timestamp=time.time(), tool_name=tool_name, arguments=arguments, result=result
            )
        )


async def _act_and_observe(ctx: ToolContext, coro: Coroutine[object, object, ToolResult]) -> ToolResult:
    """Run one tool coroutine, then re-observe the page like the ReAct loop does.

    Returns a ``ToolResult`` whose ``output`` already carries the fresh
    ELEMENTS snapshot text and whose ``dom_snapshot_hash`` is set — mirroring
    exactly what ``react_loop._run_react_cycle`` does after ``execute_tool``.
    """
    result = await coro
    observation = await build_observation(ctx.page)
    combined_output = f"{result.output}\n\nCurrent page state:\n{observation.text}"
    return ToolResult(
        success=result.success, output=combined_output, dom_snapshot_hash=observation.dom_hash
    )


def _run(
    ctx: ToolContext, recorder: ToolCallRecorder, coro: Coroutine[object, object, ToolResult]
) -> ToolResult:
    """Synchronous entry point for ``forward()`` — see ``ToolCallRecorder.loop``'s docstring."""
    if recorder.loop is not None:
        return asyncio.run_coroutine_threadsafe(_act_and_observe(ctx, coro), recorder.loop).result()
    return asyncio.run(_act_and_observe(ctx, coro))


class NavigateTool(smolagents.Tool):  # type: ignore[misc]  # smolagents ships no py.typed marker
    name = "navigate"
    description = (
        "Navigate the current tab to a URL. Only URLs on this task's own "
        "site (relative paths like '/docs/index.html' or absolute URLs "
        "with the same host and port you started on) are allowed. Returns "
        "a short text description of what happened plus the fresh page state."
    )
    inputs = {  # noqa: RUF012 - matches smolagents.Tool's own (non-ClassVar) convention
        "url": {"type": "string", "description": "Relative path or absolute URL."},
    }
    output_type = "string"

    def __init__(self, ctx: ToolContext, recorder: ToolCallRecorder) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder = recorder

    def forward(self, url: str) -> str:
        result = _run(self.ctx, self.recorder, do_navigate(self.ctx, url))
        self.recorder.record("navigate", {"url": url}, result)
        return result.output


class ClickTool(smolagents.Tool):  # type: ignore[misc]  # smolagents ships no py.typed marker
    name = "click"
    description = (
        "Click a page element by its id from the latest ELEMENTS "
        "observation (e.g. 'e3'). A short natural-language description of "
        "the element is accepted as a fallback but ids are preferred. "
        "Returns a short text description of what happened plus the fresh page state."
    )
    inputs = {  # noqa: RUF012 - matches smolagents.Tool's own (non-ClassVar) convention
        "element_id": {
            "type": "string",
            "description": "Element id from the observation, e.g. 'e3'.",
        },
    }
    output_type = "string"

    def __init__(self, ctx: ToolContext, recorder: ToolCallRecorder) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder = recorder

    def forward(self, element_id: str) -> str:
        result = _run(self.ctx, self.recorder, do_click(self.ctx, element_id))
        self.recorder.record("click", {"element_id": element_id}, result)
        return result.output


class TypeTextTool(smolagents.Tool):  # type: ignore[misc]  # smolagents ships no py.typed marker
    name = "type_text"
    description = (
        "Replace the content of a text field (textbox) with the given text. "
        "Returns a short text description plus the fresh page state."
    )
    inputs = {  # noqa: RUF012 - matches smolagents.Tool's own (non-ClassVar) convention
        "element_id": {"type": "string", "description": "Textbox id from the observation."},
        "text": {"type": "string", "description": "Text to enter."},
    }
    output_type = "string"

    def __init__(self, ctx: ToolContext, recorder: ToolCallRecorder) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder = recorder

    def forward(self, element_id: str, text: str) -> str:
        result = _run(self.ctx, self.recorder, do_type_text(self.ctx, element_id, text))
        self.recorder.record("type_text", {"element_id": element_id, "text": text}, result)
        return result.output


class SelectOptionTool(smolagents.Tool):  # type: ignore[misc]  # smolagents ships no py.typed marker
    name = "select_option"
    description = (
        "Choose an option in a dropdown (combobox). Tries to match the "
        "option by value first, then by visible label. Returns a short "
        "text description plus the fresh page state."
    )
    inputs = {  # noqa: RUF012 - matches smolagents.Tool's own (non-ClassVar) convention
        "element_id": {"type": "string", "description": "Combobox id from the observation."},
        "value": {"type": "string", "description": "Option value or visible label."},
    }
    output_type = "string"

    def __init__(self, ctx: ToolContext, recorder: ToolCallRecorder) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder = recorder

    def forward(self, element_id: str, value: str) -> str:
        result = _run(self.ctx, self.recorder, do_select_option(self.ctx, element_id, value))
        self.recorder.record("select_option", {"element_id": element_id, "value": value}, result)
        return result.output


class SubmitFormTool(smolagents.Tool):  # type: ignore[misc]  # smolagents ships no py.typed marker
    name = "submit_form"
    description = (
        "Submit a form: click the given submit button id, or, without an "
        "id, click the page's primary submit button. Returns a short text "
        "description plus the fresh page state."
    )
    inputs = {  # noqa: RUF012 - matches smolagents.Tool's own (non-ClassVar) convention
        "element_id": {
            "type": "string",
            "description": "Optional submit button id from the observation.",
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(self, ctx: ToolContext, recorder: ToolCallRecorder) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder = recorder

    def forward(self, element_id: str | None = None) -> str:
        result = _run(self.ctx, self.recorder, do_submit_form(self.ctx, element_id))
        self.recorder.record("submit_form", {"element_id": element_id}, result)
        return result.output


class ExtractTextTool(smolagents.Tool):  # type: ignore[misc]  # smolagents ships no py.typed marker
    name = "extract_text"
    description = (
        "Read the visible text content of any element from the "
        "observation (headings, paragraphs, table cells, inputs). Returns "
        "the text plus the fresh page state."
    )
    inputs = {  # noqa: RUF012 - matches smolagents.Tool's own (non-ClassVar) convention
        "element_id": {"type": "string", "description": "Element id from the observation."},
    }
    output_type = "string"

    def __init__(self, ctx: ToolContext, recorder: ToolCallRecorder) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder = recorder

    def forward(self, element_id: str) -> str:
        result = _run(self.ctx, self.recorder, do_extract_text(self.ctx, element_id))
        self.recorder.record("extract_text", {"element_id": element_id}, result)
        return result.output


class WaitForTool(smolagents.Tool):  # type: ignore[misc]  # smolagents ships no py.typed marker
    name = "wait_for"
    description = (
        "Wait until the given text appears on the page (or a CSS selector "
        "if one is provided). Use after actions whose effect is async. "
        "Returns a short text description plus the fresh page state."
    )
    inputs = {  # noqa: RUF012 - matches smolagents.Tool's own (non-ClassVar) convention
        "condition_description": {
            "type": "string",
            "description": (
                "Text expected to appear (quote it, e.g. \"'Report generated'\") "
                "or a CSS selector like '#success-marker'."
            ),
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "How long to wait, in seconds (1-30).",
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(self, ctx: ToolContext, recorder: ToolCallRecorder) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder = recorder

    def forward(self, condition_description: str, timeout_seconds: int | None = None) -> str:
        effective_timeout = timeout_seconds if timeout_seconds is not None else 5
        result = _run(
            self.ctx,
            self.recorder,
            do_wait_for(self.ctx, condition_description, timeout_seconds=effective_timeout),
        )
        self.recorder.record(
            "wait_for",
            {"condition_description": condition_description, "timeout_seconds": effective_timeout},
            result,
        )
        return result.output


class DoneTool(smolagents.Tool):  # type: ignore[misc]  # smolagents ships no py.typed marker
    """Adapter for ``do_done`` — registered under the name ``final_answer``.

    This name is not cosmetic. ``smolagents.MultiStepAgent._setup_tools``
    (verified against the installed source) does::

        self.tools.setdefault("final_answer", FinalAnswerTool())

    i.e. it injects its OWN default ``final_answer`` tool unless a tool by
    that exact name is already present in the list passed to
    ``CodeAgent(tools=[...])``. If we registered ``do_done`` under the name
    ``"done"`` instead, ``CodeAgent`` would silently ALSO have its own
    ``final_answer`` available, the model could call either one, and calling
    the built-in ``final_answer`` would never touch ``do_done`` — so no
    success/failure/confidence signal would ever reach ``RunTrace``. Naming
    this tool ``final_answer`` (matching smolagents' own convention for
    "this ends the run") is the only way to guarantee ``do_done`` is what
    actually terminates a ``CodeAgent`` run in this project.

    ``forward`` does NOT re-observe the page (unlike the other adapters
    above): the run is ending, there is no next step that needs a fresh
    ELEMENTS snapshot, and ``do_done`` never touches the DOM.
    """

    name = "final_answer"
    description = (
        "Declare the task finished. Call this exactly once when you are "
        "confident the goal is achieved — or use success=False to give up. "
        "For extraction tasks pass extracted_value (the fact you found) "
        "and your confidence in [0, 1]. Nothing runs after this call."
    )
    inputs = {  # noqa: RUF012 - matches smolagents.Tool's own (non-ClassVar) convention
        "success": {"type": "boolean", "description": "Whether the task was completed."},
        "extracted_value": {
            "type": "string",
            "description": "The value you extracted, for extraction tasks.",
            "nullable": True,
        },
        "confidence": {
            "type": "number",
            "description": "Your confidence in [0, 1], for extraction tasks.",
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(self, ctx: ToolContext, recorder: ToolCallRecorder) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder = recorder

    def forward(
        self,
        success: bool,
        extracted_value: str | None = None,
        confidence: float | None = None,
    ) -> str:
        # A bare asyncio.run(...) is safe here specifically (unlike every
        # other adapter above): do_done never awaits anything and never
        # touches ctx.page, so there is no cross-loop Playwright object for
        # a throwaway loop to deadlock on.
        result = asyncio.run(do_done(self.ctx, success, extracted_value, confidence))
        self.recorder.record(
            "done",
            {"success": success, "extracted_value": extracted_value, "confidence": confidence},
            result,
        )
        return result.output


def build_tool_adapters(
    ctx: ToolContext, recorder: ToolCallRecorder
) -> list[smolagents.Tool]:
    """Construct one instance of every adapter above, sharing ``ctx``/``recorder``.

    Returns the list ready to hand to ``CodeAgent(tools=...)``.
    """
    return [
        NavigateTool(ctx, recorder),
        ClickTool(ctx, recorder),
        TypeTextTool(ctx, recorder),
        SelectOptionTool(ctx, recorder),
        SubmitFormTool(ctx, recorder),
        ExtractTextTool(ctx, recorder),
        WaitForTool(ctx, recorder),
        DoneTool(ctx, recorder),
    ]
