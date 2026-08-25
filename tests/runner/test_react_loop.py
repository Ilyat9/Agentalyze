"""ReAct loop tests with a deterministic FakeProvider + real Chromium.

The key testing level for Phase 3: a hand-rolled Provider implementation
(not a mock library) returns a scripted, fully deterministic sequence of
responses, while the browser side and fixtures are 100% real. Marker:
``browser``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from agentalyze.config import Settings
from agentalyze.providers.base import (
    ChatMessage,
    CompletionResult,
    ProviderConnectionError,
    ToolCall,
)
from agentalyze.runner import run_task
from agentalyze.runner.trace import RunOutcome, load_trace
from agentalyze.tasks.registry import TASKS_BY_ID

pytestmark = pytest.mark.browser


class FakeProvider:
    """Deterministic scripted Provider; may compute answers from the last message."""

    def __init__(self, script) -> None:  # script: list[callable | CompletionResult]
        self.script = list(script)
        self._last_step = None
        self.calls = 0
        self.received_tools = None
        self.name = "fake-provider"

    async def chat_completion(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.calls += 1
        if self.script:
            step = self.script.pop(0)
        else:
            # Script exhausted: keep repeating the last scripted response
            # (used by "never calls done" scenarios).
            assert self._last_step is not None, "FakeProvider ran out of scripted responses"
            step = self._last_step
        completion = step(messages) if callable(step) else step
        self._last_step = step
        return completion

    async def health_check(self):
        return True


def _assistant_with_call(call_id: str, name: str, **arguments) -> CompletionResult:
    return CompletionResult(
        message=ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        ),
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        latency_seconds=0.001,
        finish_reason="tool_calls",
    )


def _plain_text(text: str) -> CompletionResult:
    return CompletionResult(
        message=ChatMessage(role="assistant", content=text),
        prompt_tokens=50,
        completion_tokens=5,
        total_tokens=55,
        latency_seconds=0.001,
        finish_reason="stop",
    )


def _id_of_element_with_text(observation_text: str, fragment: str) -> str:
    match = re.search(rf"\[(e\d+)\] \w+ \"[^\"]*{re.escape(fragment)}", observation_text)
    assert match, f"no element matching {fragment!r} in observation:\n{observation_text}"
    return match.group(1)


class TestSuccessfulRun:
    async def test_scripted_agent_reaches_success_on_navigation_task(
        self, runner_settings: Settings
    ) -> None:
        task = TASKS_BY_ID["nav-simple-link-01"]

        def step_1_click_documentation(messages):
            observation = messages[-1].content
            element_id = _id_of_element_with_text(observation, "Documentation")
            return _assistant_with_call("c1", "click", element_id=element_id)

        def step_2_done(messages):
            # The click result + fresh page snapshot must have arrived as a tool message.
            tool_msg = messages[-1]
            assert tool_msg.role == "tool"
            assert "docs_01" in tool_msg.content or "Clicked" in tool_msg.content
            return _assistant_with_call("c2", "done", success=True)

        provider = FakeProvider([step_1_click_documentation, step_2_done])

        trace = await run_task(task, provider, runner_settings)

        assert trace.outcome is RunOutcome.SUCCESS
        assert trace.success
        assert trace.verifier_result is not None and trace.verifier_result.success
        assert trace.task_id == task.id
        assert trace.provider_name == "fake-provider"
        assert len(trace.steps) == 2
        assert trace.total_prompt_tokens == 200
        assert trace.total_completion_tokens == 20
        assert trace.total_cost_usd is None  # never invented
        assert trace.error is None

        # Artifacts on disk: trace.json + screenshot of the executed action.
        trace_path = runner_settings.results_dir / trace.run_id / "trace.json"
        assert trace_path.is_file()
        restored = load_trace(trace_path)
        assert restored == trace

        screenshot = Path(trace.steps[0].screenshot_path)
        assert screenshot.is_file() and screenshot.stat().st_size > 0
        assert screenshot.name == "step_1.png"
        # `done` performs no page action -> no screenshot for step 2.
        assert trace.steps[1].screenshot_path is None
        # Tool results carry DOM fingerprints.
        assert trace.steps[0].tool_result.dom_snapshot_hash


class TestFailureModes:
    async def test_never_calls_done_ends_in_max_steps(self, runner_settings: Settings) -> None:
        task = TASKS_BY_ID["nav-simple-link-01"].model_copy(update={"max_steps": 3})
        provider = FakeProvider([_plain_text("Hmm...")])  # same reply forever

        trace = await run_task(task, provider, runner_settings)

        assert trace.outcome is RunOutcome.FAILURE_MAX_STEPS
        assert len(trace.steps) == 3  # terminated after exactly the budget, no hang
        assert trace.verifier_result is None  # never reached verification
        assert provider.calls == 3

    async def test_giving_up_maps_to_failure_verifier(self, runner_settings: Settings) -> None:
        task = TASKS_BY_ID["nav-simple-link-01"]
        provider = FakeProvider([_assistant_with_call("c1", "done", success=False)])

        trace = await run_task(task, provider, runner_settings)

        assert trace.outcome is RunOutcome.FAILURE_VERIFIER
        assert trace.verifier_result is not None and not trace.verifier_result.success

    async def test_done_success_but_wrong_page_is_failure_verifier(
        self, runner_settings: Settings
    ) -> None:
        task = TASKS_BY_ID["nav-simple-link-01"]
        # Agent declares victory without doing anything -> verifier disagrees.
        provider = FakeProvider([_assistant_with_call("c1", "done", success=True)])

        trace = await run_task(task, provider, runner_settings)

        assert trace.outcome is RunOutcome.FAILURE_VERIFIER
        assert not trace.verifier_result.success

    async def test_provider_error_after_retries(self, runner_settings: Settings) -> None:
        task = TASKS_BY_ID["nav-simple-link-01"]

        class ExplodingProvider(FakeProvider):
            async def chat_completion(self, messages, tools=None, temperature=0.0, max_tokens=None):
                raise ProviderConnectionError("connection refused", provider_name=self.name)

        trace = await run_task(task, ExplodingProvider([]), runner_settings)

        assert trace.outcome is RunOutcome.FAILURE_PROVIDER_ERROR
        assert trace.error is not None and "connection refused" in trace.error
        assert trace.steps == []
        assert trace.verifier_result is None
        # Resources were still cleaned up and the crash-free trace persisted:
        assert (runner_settings.results_dir / trace.run_id / "trace.json").is_file()

    async def test_task_timeout_interrupts_slow_provider(self, runner_settings: Settings) -> None:
        task = TASKS_BY_ID["nav-simple-link-01"].model_copy(update={"timeout_seconds": 1})

        class SlowProvider(FakeProvider):
            async def chat_completion(self, messages, tools=None, temperature=0.0, max_tokens=None):
                await asyncio.sleep(5)
                raise AssertionError("should have been cancelled by wait_for")

        trace = await run_task(task, SlowProvider([]), runner_settings)

        assert trace.outcome is RunOutcome.FAILURE_TIMEOUT
        assert trace.wall_clock_seconds < 4  # did NOT wait for the full sleep

    async def test_unhandled_provider_exception_becomes_crash(
        self, runner_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A provider violating its contract (non-ProviderError) must not kill the runner."""
        task = TASKS_BY_ID["nav-simple-link-01"]

        class BrokenContractProvider(FakeProvider):
            async def chat_completion(self, messages, tools=None, temperature=0.0, max_tokens=None):
                raise ValueError("provider SDK exploded unexpectedly")

        trace = await run_task(task, BrokenContractProvider([]), runner_settings)

        assert trace.outcome is RunOutcome.FAILURE_CRASH
        assert trace.error is not None
        assert "ValueError" in trace.error and "exploded" in trace.error  # full traceback kept

    async def test_unhandled_tool_exception_becomes_tool_error(
        self, runner_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unexpected exception from a tool implementation aborts with FAILURE_TOOL_ERROR."""
        import agentalyze.runner.react_loop as react_loop_module

        task = TASKS_BY_ID["nav-simple-link-01"]
        provider = FakeProvider([_assistant_with_call("c1", "click", element_id="e1")])

        async def exploding_execute_tool(ctx, call):
            raise RuntimeError("simulated bug inside a tool implementation")

        monkeypatch.setattr(react_loop_module, "execute_tool", exploding_execute_tool)

        trace = await run_task(task, provider, runner_settings)

        assert trace.outcome is RunOutcome.FAILURE_TOOL_ERROR
        assert trace.error is not None and "RuntimeError" in trace.error
        assert trace.steps[0].tool_error is not None

class TestFormFillSuccess:
    async def test_scripted_agent_fills_and_submits_form(self, runner_settings: Settings) -> None:
        """A multi-action scripted run on form-fill-basic-01 (max_steps budget respected)."""
        task = TASKS_BY_ID["form-fill-basic-01"]
        values = {
            "Name": "Ivan Petrov",
            "Email": "ivan@example.com",
            "Message": "My order has not arrived",
        }
        script = []

        def make_type_step(field_label, text):
            def step(messages):
                element_id = _id_of_element_with_text(messages[-1].content, field_label)
                return _assistant_with_call(f"c-{field_label}", "type_text",
                                            element_id=element_id, text=text)
            return step

        for label, value in values.items():
            script.append(make_type_step(label, value))
        script.append(lambda messages: _assistant_with_call(
            "c-submit", "submit_form",
            element_id=_id_of_element_with_text(messages[-1].content, "Send message")))
        script.append(lambda messages: _assistant_with_call("c-done", "done", success=True))

        trace = await run_task(task, FakeProvider(script), runner_settings)

        assert trace.outcome is RunOutcome.SUCCESS
        assert len(trace.steps) == 5
        # Every action step got a screenshot; the final done did not.
        assert all(step.screenshot_path for step in trace.steps[:4])
        assert trace.steps[4].screenshot_path is None


