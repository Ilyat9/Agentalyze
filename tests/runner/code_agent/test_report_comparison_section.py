"""Unit tests for the "Tool-calling vs Code generation" report section —
pure computation over constructed traces, no browser/provider involved.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentalyze.orchestration.report import (
    build_agent_style_comparison_section,
    render_agent_style_comparison_report,
)
from agentalyze.providers.base import ChatMessage, CompletionResult, ToolCall
from agentalyze.runner.trace import RunOutcome, RunTrace, StepEvent, ToolResult
from agentalyze.tasks.models import VerificationResult


def _completion() -> CompletionResult:
    return CompletionResult(
        message=ChatMessage(role="assistant", content="x"),
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        latency_seconds=0.01,
        finish_reason="stop",
    )


def _step(name: str = "click") -> StepEvent:
    return StepEvent(
        step_number=1,
        timestamp=datetime.now(UTC),
        llm_request_messages=[],
        llm_response=_completion(),
        tool_call=ToolCall(id="c1", name=name, arguments={}),
        tool_result=ToolResult(success=True, output="ok"),
    )


def _trace(
    *,
    agent_style: str,
    outcome: RunOutcome,
    n_steps: int,
    wall_clock: float = 2.0,
    task_id: str = "nav-simple-link-01",
) -> RunTrace:
    return RunTrace(
        run_id=f"run-{agent_style}-{outcome.value}-{n_steps}",
        task_id=task_id,
        provider_name="fake",
        agent_style=agent_style,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        outcome=outcome,
        verifier_result=(
            VerificationResult(success=True, reason="ok")
            if outcome is RunOutcome.SUCCESS
            else None
        ),
        steps=[_step() for _ in range(n_steps)],
        total_prompt_tokens=10 * n_steps,
        total_completion_tokens=5 * n_steps,
        wall_clock_seconds=wall_clock,
    )


class TestBuildComparisonSection:
    def test_missing_a_style_reports_insufficient_data(self) -> None:
        lines = build_agent_style_comparison_section(
            {"tool_calling": [_trace(agent_style="tool_calling", outcome=RunOutcome.SUCCESS, n_steps=2)]}
        )
        text = "\n".join(lines)
        assert "tool_calling" in text
        assert "code" not in text.split("\n")[0]  # heading only, no data table
        assert "| Metrика |" not in text  # never renders a data table without both styles

    def test_success_rate_and_avg_steps_computed_correctly(self) -> None:
        tool_calling_traces = [
            _trace(agent_style="tool_calling", outcome=RunOutcome.SUCCESS, n_steps=4),
            _trace(agent_style="tool_calling", outcome=RunOutcome.FAILURE_MAX_STEPS, n_steps=10),
        ]
        code_traces = [
            _trace(agent_style="code", outcome=RunOutcome.SUCCESS, n_steps=2),
            _trace(agent_style="code", outcome=RunOutcome.SUCCESS, n_steps=3),
        ]
        lines = build_agent_style_comparison_section(
            {"tool_calling": tool_calling_traces, "code": code_traces}
        )
        text = "\n".join(lines)

        # tool_calling: 1/2 success = 50.0%; code: 2/2 = 100.0%
        assert "| Success rate | 50.0% | 100.0% |" in text
        # tool_calling avg steps = (4+10)/2 = 7.0; code avg = (2+3)/2 = 2.5
        assert "| Avg steps | 7.0 | 2.5 |" in text
        # code used fewer steps -> a measured delta sentence should appear
        assert "меньше" in text or "больше" in text

    def test_failure_tags_are_counted_per_style(self) -> None:
        # A run that never calls done and never mutates state -> WRONG_TOOL_CHOICE
        # is not guaranteed here; use a simple, deterministic tag instead:
        # LOOPING via 3 identical consecutive tool calls.
        looping_steps = [
            StepEvent(
                step_number=i,
                timestamp=datetime.now(UTC),
                llm_request_messages=[],
                llm_response=_completion(),
                tool_call=ToolCall(id=f"c{i}", name="click", arguments={"element_id": "e1"}),
                tool_result=ToolResult(success=False, output="fail"),
            )
            for i in range(1, 4)
        ]
        looping_trace = RunTrace(
            run_id="run-loop",
            task_id="nav-simple-link-01",
            provider_name="fake",
            agent_style="code",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            outcome=RunOutcome.FAILURE_MAX_STEPS,
            steps=looping_steps,
            wall_clock_seconds=1.0,
        )
        lines = build_agent_style_comparison_section(
            {
                "tool_calling": [
                    _trace(agent_style="tool_calling", outcome=RunOutcome.SUCCESS, n_steps=1)
                ],
                "code": [looping_trace],
            }
        )
        text = "\n".join(lines)
        assert "`looping`" in text
        assert "| `looping` | 0 | 1 |" in text

    def test_raises_on_mismatched_agent_style_in_bucket(self) -> None:
        import pytest

        wrong_style_trace = _trace(agent_style="code", outcome=RunOutcome.SUCCESS, n_steps=1)
        with pytest.raises(ValueError, match="agent_style"):
            build_agent_style_comparison_section(
                {
                    "tool_calling": [wrong_style_trace],
                    "code": [_trace(agent_style="code", outcome=RunOutcome.SUCCESS, n_steps=1)],
                }
            )


class TestRenderReport:
    def test_render_includes_fake_provider_disclosure(self) -> None:
        report = render_agent_style_comparison_report(
            {
                "tool_calling": [
                    _trace(agent_style="tool_calling", outcome=RunOutcome.SUCCESS, n_steps=2)
                ],
                "code": [_trace(agent_style="code", outcome=RunOutcome.SUCCESS, n_steps=2)],
            },
            fake_provider=True,
        )
        assert "FakeProvider" in report
        assert "собственный бенчмарк smolagents" in report
