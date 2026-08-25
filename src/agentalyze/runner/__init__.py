"""Runner (Phase 3): real Chromium + ReAct loop + full run traces.

This subpackage connects the two earlier layers — the task suite
(``agentalyze.tasks``) and the provider layer (``agentalyze.providers``):
it runs ONE task with ONE configured provider in a real browser, records a
self-sufficient :class:`RunTrace`, and writes it under ``Settings.results_dir``.

Suite-wide comparison, parallelism and reporting are later phases built on
top of this core.
"""

from agentalyze.runner.react_loop import run_task
from agentalyze.runner.tools import DONE_TOOL_NAME, TOOL_SPECS, ToolContext, execute_tool
from agentalyze.runner.trace import (
    RunOutcome,
    RunTrace,
    StepEvent,
    ToolResult,
    load_trace,
    save_trace,
)

__all__ = [
    "DONE_TOOL_NAME",
    "TOOL_SPECS",
    "RunOutcome",
    "RunTrace",
    "StepEvent",
    "ToolContext",
    "ToolResult",
    "execute_tool",
    "load_trace",
    "run_task",
    "save_trace",
]
