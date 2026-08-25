"""Serialization round-trip tests for the RunTrace model family.

These are pure unit tests: no browser, no provider, no markers needed —
the trace format must be solid on its own because every later phase reads it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentalyze.providers.base import ChatMessage, CompletionResult, ToolCall
from agentalyze.runner.trace import (
    RunOutcome,
    RunTrace,
    StepEvent,
    ToolResult,
    load_trace,
    run_dir,
    save_trace,
    screenshots_dir,
)
from agentalyze.tasks.models import VerificationResult


def _ts(minute: int) -> datetime:
    return datetime(2026, 8, 25, 12, minute, tzinfo=UTC)


def _completion(tool_call: ToolCall | None = None) -> CompletionResult:
    message = ChatMessage(
        role="assistant",
        content="" if tool_call else "Thinking...",
        tool_calls=[tool_call] if tool_call else None,
    )
    return CompletionResult(
        message=message,
        prompt_tokens=120,
        completion_tokens=15,
        total_tokens=135,
        latency_seconds=0.42,
        finish_reason="tool_calls" if tool_call else "stop",
        raw_provider_response_id="resp_123",
    )


def _sample_trace() -> RunTrace:
    call1 = ToolCall(id="call_1", name="click", arguments={"element_id": "e3"})
    step1 = StepEvent(
        step_number=1,
        timestamp=_ts(1),
        llm_request_messages=[
            ChatMessage(role="system", content="You are an agent."),
            ChatMessage(role="user", content="TASK: follow the docs link"),
        ],
        llm_response=_completion(call1),
        tool_call=call1,
        tool_result=ToolResult(success=True, output="Clicked e3.", dom_snapshot_hash="abc123"),
        screenshot_path="/results/run-1/screenshots/step_1.png",
    )
    call2 = ToolCall(id="call_2", name="done", arguments={"success": True})
    step2 = StepEvent(
        step_number=2,
        timestamp=_ts(2),
        llm_request_messages=[
            ChatMessage(role="system", content="You are an agent."),
            ChatMessage(role="user", content="TASK: follow the docs link"),
            ChatMessage(role="assistant", content="", tool_calls=[call1]),
            ChatMessage(role="tool", content="Clicked e3.", tool_call_id="call_1"),
        ],
        llm_response=_completion(call2),
        tool_call=call2,
        tool_result=ToolResult(success=True, output="Agent declared the task completed."),
    )
    return RunTrace(
        run_id="run-1",
        task_id="nav-simple-link-01",
        provider_name="gpt-4o-mini-via-openrouter",
        started_at=_ts(0),
        finished_at=_ts(3),
        outcome=RunOutcome.SUCCESS,
        verifier_result=VerificationResult(success=True, reason="docs reached"),
        steps=[step1, step2],
        total_prompt_tokens=240,
        total_completion_tokens=30,
        total_cost_usd=None,
        wall_clock_seconds=3.5,
    )


class TestRoundTrip:
    def test_save_then_load_is_lossless(self, tmp_path: Path) -> None:
        trace = _sample_trace()
        path = save_trace(trace, tmp_path)
        assert path == run_dir(tmp_path, "run-1") / "trace.json"
        assert load_trace(path) == trace

    def test_model_dump_json_round_trip(self) -> None:
        trace = _sample_trace()
        assert RunTrace.model_validate_json(trace.model_dump_json()) == trace

    def test_nested_models_survive(self, tmp_path: Path) -> None:
        save_trace(_sample_trace(), tmp_path)
        restored = load_trace(run_dir(tmp_path, "run-1") / "trace.json")

        first = restored.steps[0]
        assert first.tool_call is not None
        assert first.tool_call.arguments == {"element_id": "e3"}
        assert first.llm_response.finish_reason == "tool_calls"
        assert first.llm_response.raw_provider_response_id == "resp_123"
        assert first.tool_result is not None
        assert first.tool_result.dom_snapshot_hash == "abc123"
        assert first.timestamp.tzinfo is not None  # timezone survives ISO round-trip

        # The step-2 request history embeds the full prior conversation.
        roles = [m.role for m in restored.steps[1].llm_request_messages]
        assert roles == ["system", "user", "assistant", "tool"]
        assert restored.steps[1].llm_request_messages[3].tool_call_id == "call_1"


class TestStorageLayout:
    def test_directory_layout(self, tmp_path: Path) -> None:
        assert run_dir(tmp_path, "abc") == tmp_path / "abc"
        assert screenshots_dir(tmp_path, "abc") == tmp_path / "abc" / "screenshots"

    def test_save_creates_missing_dirs_and_valid_json(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b"
        path = save_trace(_sample_trace(), deep)
        assert path.is_file()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["outcome"] == "success"
        assert len(raw["steps"]) == 2

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_trace(tmp_path / "nope.json")


class TestModelSemantics:
    def test_all_outcomes_round_trip_through_values(self) -> None:
        for outcome in RunOutcome:
            assert RunOutcome(outcome.value) is outcome

    def test_success_flag_matches_outcome(self) -> None:
        assert _sample_trace().success is True
        failed = _sample_trace().model_copy(update={"outcome": RunOutcome.FAILURE_VERIFIER})
        assert failed.success is False

    def test_optional_fields_default_to_none_or_empty(self) -> None:
        trace = RunTrace(
            run_id="r",
            task_id="t",
            provider_name="p",
            started_at=_ts(0),
            finished_at=_ts(1),
            outcome=RunOutcome.FAILURE_CRASH,
            wall_clock_seconds=60.0,
            error="Traceback (most recent call last): ...",
        )
        assert trace.verifier_result is None
        assert trace.steps == []
        assert trace.total_cost_usd is None
        assert trace.error is not None and trace.error.startswith("Traceback")


