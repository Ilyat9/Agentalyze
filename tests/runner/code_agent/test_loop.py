"""End-to-end code-agent runner tests: real Chromium, executor_type='local',
a deterministic FakeProvider returning code text (never a real generated-code
path — see docs/KNOWN_LIMITATIONS.md on why 'local' is dev/test-only).
Marker: ``browser``.
"""

from __future__ import annotations

import re

import pytest

from agentalyze.config import Settings
from agentalyze.providers.base import ChatMessage, CompletionResult
from agentalyze.runner.code_agent.loop import run_task_code_agent
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.registry import TASKS_BY_ID

pytestmark = pytest.mark.browser


class FakeCodeProvider:
    """Deterministic scripted Provider returning smolagents-style code blocks."""

    def __init__(self, script) -> None:  # script: list[callable | str]
        self.script = list(script)
        self._last_step = None
        self.name = "fake-code-provider"
        self.calls = 0

    async def chat_completion(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self.calls += 1
        if self.script:
            step = self.script.pop(0)
        else:
            assert self._last_step is not None, "FakeCodeProvider ran out of scripted responses"
            step = self._last_step
        self._last_step = step
        text = step(messages) if callable(step) else step
        return CompletionResult(
            message=ChatMessage(role="assistant", content=text),
            prompt_tokens=50,
            completion_tokens=20,
            total_tokens=70,
            latency_seconds=0.001,
            finish_reason="stop",
        )

    async def health_check(self) -> bool:
        return True


def _code(body: str) -> str:
    return f"<code>\n{body}\n</code>"


def _id_of_element_with_text(observation_text: str, fragment: str) -> str:
    match = re.search(rf"\[(e\d+)\] \w+ \"[^\"]*{re.escape(fragment)}", observation_text)
    assert match, f"no element matching {fragment!r} in observation:\n{observation_text}"
    return match.group(1)


@pytest.fixture
def code_agent_settings(runner_settings: Settings) -> Settings:
    return runner_settings.model_copy(update={"code_agent_executor_type": "local"})


class TestSuccessfulRun:
    async def test_scripted_agent_reaches_success_on_navigation_task(
        self, code_agent_settings: Settings
    ) -> None:
        task = TASKS_BY_ID["nav-simple-link-01"]

        def step_1_click_documentation(messages):
            # CodeAgent's own task message carries the fixture's starting
            # page state, built from the same build_observation used
            # everywhere else, so the Documentation link's id is right there.
            observation = messages[-1].content
            element_id = _id_of_element_with_text(observation, "Documentation")
            return _code(f"print(click(element_id={element_id!r}))")

        def step_2_done(messages):
            observation = messages[-1].content
            assert "docs_01" in observation or "Documentation" in observation
            return _code("final_answer(success=True)")

        provider = FakeCodeProvider([step_1_click_documentation, step_2_done])

        trace = await run_task_code_agent(task, provider, code_agent_settings)

        assert trace.agent_style == "code"
        assert trace.task_id == task.id
        assert trace.provider_name == "fake-code-provider"
        assert trace.error is None
        assert len(trace.steps) == 2
        assert trace.steps[0].tool_call is not None
        assert trace.steps[0].tool_call.name == "click"
        assert trace.steps[1].tool_call.name == "done"
        assert trace.total_prompt_tokens == 100
        assert trace.total_completion_tokens == 40
        assert trace.outcome is RunOutcome.SUCCESS, (
            f"expected SUCCESS, got {trace.outcome}: {trace.verifier_result}"
        )
        assert trace.verifier_result is not None and trace.verifier_result.success


class TestFailureModes:
    async def test_never_calls_final_answer_ends_in_max_steps(
        self, code_agent_settings: Settings
    ) -> None:
        task = TASKS_BY_ID["nav-simple-link-01"].model_copy(update={"max_steps": 2})
        # Prints something but never calls final_answer/click — a pure no-op
        # loop, deterministic regardless of fixture DOM contents.
        provider = FakeCodeProvider([_code("print('thinking...')")])

        trace = await run_task_code_agent(task, provider, code_agent_settings)

        assert trace.outcome is RunOutcome.FAILURE_MAX_STEPS
        assert trace.verifier_result is None
        assert trace.agent_style == "code"

    async def test_giving_up_maps_to_failure_verifier(
        self, code_agent_settings: Settings
    ) -> None:
        task = TASKS_BY_ID["nav-simple-link-01"]
        provider = FakeCodeProvider([_code("final_answer(success=False)")])

        trace = await run_task_code_agent(task, provider, code_agent_settings)

        assert trace.outcome is RunOutcome.FAILURE_VERIFIER
        assert trace.verifier_result is not None and not trace.verifier_result.success
        assert trace.steps[-1].tool_call.name == "done"
