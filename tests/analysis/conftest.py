"""Shared hand-built RunTrace/StepEvent factories for Phase 4 unit tests.

Everything here is pure object construction: no browser, no provider, no
network — the analysis layer must be testable from in-memory traces alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentalyze.providers.base import ChatMessage, CompletionResult, ToolCall
from agentalyze.runner.trace import RunOutcome, RunTrace, StepEvent, ToolResult
from agentalyze.tasks.models import TaskCategory, VerificationResult

PROVIDER_A = "provider-a"
PROVIDER_B = "provider-b"


def _ts(minute: int) -> datetime:
    return datetime(2026, 8, 25, 12, minute, tzinfo=UTC)


def observation_message(*element_ids: str) -> ChatMessage:
    """A tool-role message mimicking the runner's ELEMENTS observation."""
    lines = ["PAGE: fixture (/some/page.html)", "ELEMENTS:"]
    lines.extend(f'[{eid}] button "Button {eid}"' for eid in element_ids)
    return ChatMessage(role="tool", content="\n".join(lines))


def make_completion(latency_seconds: float = 0.5) -> CompletionResult:
    return CompletionResult(
        message=ChatMessage(role="assistant", content="acting"),
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        latency_seconds=latency_seconds,
        finish_reason="tool_calls",
    )


def make_step(
    number: int,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    tool_success: bool = True,
    output: str = "ok",
    dom_hash: str | None = None,
    latency_seconds: float = 0.5,
    observed_ids: tuple[str, ...] = ("e1", "e2"),
) -> StepEvent:
    """One step; ``observed_ids`` is what the model saw right before acting.

    The step's request carries system + observation messages, faithfully
    reproducing the layout ``failure_taxonomy`` relies on (the LAST user/tool
    message is the freshest observation).
    """
    call = ToolCall(id=f"call_{number}", name=tool_name, arguments=dict(arguments or {}))
    request = [
        ChatMessage(role="system", content="system prompt"),
        observation_message(*observed_ids),
    ]
    result = ToolResult(success=tool_success, output=output, dom_snapshot_hash=dom_hash)
    return StepEvent(
        step_number=number,
        timestamp=_ts(number),
        llm_request_messages=request,
        llm_response=make_completion(latency_seconds),
        tool_call=call,
        tool_result=result,
    )


def make_trace(
    steps: list[StepEvent],
    outcome: RunOutcome,
    *,
    task_id: str = "task-01",
    category: TaskCategory | None = TaskCategory.NAVIGATION,
    provider_name: str = PROVIDER_A,
    verifier_success: bool | None = None,
    wall_clock_seconds: float = 10.0,
    total_prompt_tokens: int = 0,
    total_completion_tokens: int = 0,
    total_cost_usd: float | None = None,
) -> RunTrace:
    verifier = (
        VerificationResult(success=verifier_success, reason="test verdict")
        if verifier_success is not None
        else None
    )
    return RunTrace(
        run_id=f"run-{task_id}-{outcome.value}",
        task_id=task_id,
        task_category=category,
        provider_name=provider_name,
        started_at=_ts(0),
        finished_at=_ts(1),
        outcome=outcome,
        verifier_result=verifier,
        steps=list(steps),
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        total_cost_usd=total_cost_usd,
        wall_clock_seconds=wall_clock_seconds,
    )
