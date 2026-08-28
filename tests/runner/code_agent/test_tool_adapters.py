"""Unit tests for the tool_adapters.py Tool subclasses.

These mock the existing do_* functions and build_observation (already
covered by tests/runner/test_tools.py and tests/runner/test_observation.py)
and check only the adapter's own job: calling the right function with the
right arguments, appending the fresh observation, setting dom_snapshot_hash,
and recording a matching ToolCallRecord. No real browser/Page involved.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agentalyze.runner.code_agent.tool_adapters import (
    ClickTool,
    DoneTool,
    ExtractTextTool,
    NavigateTool,
    SelectOptionTool,
    SubmitFormTool,
    ToolCallRecorder,
    TypeTextTool,
    WaitForTool,
    build_tool_adapters,
)
from agentalyze.runner.observation import PageObservation
from agentalyze.runner.trace import ToolResult


class _FakeCtx:
    """Stand-in for ToolContext: no real Playwright Page needed."""

    def __init__(self) -> None:
        self.page = object()
        self.base_url = "http://127.0.0.1:9"


@pytest.fixture
def ctx() -> _FakeCtx:
    return _FakeCtx()


@pytest.fixture
def recorder() -> ToolCallRecorder:
    return ToolCallRecorder()


@pytest.fixture(autouse=True)
def _fake_observation():
    obs = PageObservation(text="PAGE: x (/)\nELEMENTS:\n[e1] link \"Docs\"", dom_hash="deadbeef")
    with patch(
        "agentalyze.runner.code_agent.tool_adapters.build_observation",
        new=AsyncMock(return_value=obs),
    ) as mocked:
        yield mocked


class TestNavigateTool:
    def test_forward_calls_do_navigate_and_appends_observation(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_navigate",
            new=AsyncMock(return_value=ToolResult(success=True, output="Navigated to /x")),
        ) as mocked:
            tool = NavigateTool(ctx, recorder)
            output = tool.forward(url="/x")

        mocked.assert_awaited_once_with(ctx, "/x")
        assert "Navigated to /x" in output
        assert "Current page state" in output
        assert "[e1] link" in output
        assert len(recorder.records) == 1
        record = recorder.records[0]
        assert record.tool_name == "navigate"
        assert record.arguments == {"url": "/x"}
        assert record.result.success is True
        assert record.result.dom_snapshot_hash == "deadbeef"


class TestClickTool:
    def test_forward_calls_do_click_with_element_id(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_click",
            new=AsyncMock(return_value=ToolResult(success=True, output="Clicked e3")),
        ) as mocked:
            tool = ClickTool(ctx, recorder)
            tool.forward(element_id="e3")

        mocked.assert_awaited_once_with(ctx, "e3")
        assert recorder.records[0].arguments == {"element_id": "e3"}

    def test_forward_reports_failure_result(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_click",
            new=AsyncMock(return_value=ToolResult(success=False, output="No element matches")),
        ):
            tool = ClickTool(ctx, recorder)
            output = tool.forward(element_id="e99")

        assert "No element matches" in output
        assert recorder.records[0].result.success is False


class TestTypeTextTool:
    def test_forward_passes_both_arguments(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_type_text",
            new=AsyncMock(return_value=ToolResult(success=True, output="Typed")),
        ) as mocked:
            TypeTextTool(ctx, recorder).forward(element_id="e2", text="hello")

        mocked.assert_awaited_once_with(ctx, "e2", "hello")
        assert recorder.records[0].arguments == {"element_id": "e2", "text": "hello"}


class TestSelectOptionTool:
    def test_forward_passes_both_arguments(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_select_option",
            new=AsyncMock(return_value=ToolResult(success=True, output="Selected")),
        ) as mocked:
            SelectOptionTool(ctx, recorder).forward(element_id="e4", value="opt-a")

        mocked.assert_awaited_once_with(ctx, "e4", "opt-a")


class TestSubmitFormTool:
    def test_forward_default_element_id_is_none(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_submit_form",
            new=AsyncMock(return_value=ToolResult(success=True, output="Submitted")),
        ) as mocked:
            SubmitFormTool(ctx, recorder).forward()

        mocked.assert_awaited_once_with(ctx, None)
        assert recorder.records[0].arguments == {"element_id": None}


class TestExtractTextTool:
    def test_forward_calls_do_extract_text(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_extract_text",
            new=AsyncMock(return_value=ToolResult(success=True, output="Text of e5: hi")),
        ) as mocked:
            output = ExtractTextTool(ctx, recorder).forward(element_id="e5")

        mocked.assert_awaited_once_with(ctx, "e5")
        assert "Text of e5: hi" in output


class TestWaitForTool:
    def test_forward_defaults_timeout_to_five_seconds(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_wait_for",
            new=AsyncMock(return_value=ToolResult(success=True, output="Condition met")),
        ) as mocked:
            WaitForTool(ctx, recorder).forward(condition_description="'Done'")

        mocked.assert_awaited_once_with(ctx, "'Done'", timeout_seconds=5)
        assert recorder.records[0].arguments["timeout_seconds"] == 5

    def test_forward_passes_explicit_timeout(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_wait_for",
            new=AsyncMock(return_value=ToolResult(success=True, output="Condition met")),
        ) as mocked:
            WaitForTool(ctx, recorder).forward(condition_description="'Done'", timeout_seconds=15)

        mocked.assert_awaited_once_with(ctx, "'Done'", timeout_seconds=15)


class TestDoneTool:
    def test_forward_calls_do_done_and_records_structured_call(self, ctx, recorder) -> None:
        with patch(
            "agentalyze.runner.code_agent.tool_adapters.do_done",
            new=AsyncMock(return_value=ToolResult(success=True, output="Agent declared done.")),
        ) as mocked:
            output = DoneTool(ctx, recorder).forward(
                success=True, extracted_value="42", confidence=0.9
            )

        mocked.assert_awaited_once_with(ctx, True, "42", 0.9)
        assert "Agent declared done." in output
        # Done does NOT re-observe the page (see module docstring): the
        # autouse build_observation mock must not appear in its output.
        assert "Current page state" not in output
        record = recorder.records[0]
        assert record.tool_name == "done"
        assert record.arguments == {"success": True, "extracted_value": "42", "confidence": 0.9}

    def test_registered_tool_name_is_final_answer(self) -> None:
        # Not cosmetic: smolagents.MultiStepAgent._setup_tools does
        # self.tools.setdefault("final_answer", FinalAnswerTool()) — unless
        # OUR tool is already registered under exactly this name, CodeAgent
        # silently adds its own final_answer and do_done is never reached.
        assert DoneTool.name == "final_answer"


def test_build_tool_adapters_returns_one_of_each_sharing_ctx_and_recorder(ctx, recorder) -> None:
    tools = build_tool_adapters(ctx, recorder)
    names = {tool.name for tool in tools}
    assert names == {
        "navigate",
        "click",
        "type_text",
        "select_option",
        "submit_form",
        "extract_text",
        "wait_for",
        "final_answer",
    }
    for tool in tools:
        assert tool.ctx is ctx
        assert tool.recorder is recorder
