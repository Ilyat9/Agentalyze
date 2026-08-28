"""The SSRF/origin guard must fire identically whether do_navigate is called
by the ReAct loop or by a CodeAgent-generated code block through
NavigateTool. The guard itself lives entirely in ToolContext.check_url
(runner/tools.py) — tool_adapters.py adds no re-verification of its own (see
its module docstring) — so this test exercises the real end-to-end path with
a real Chromium instance and a FakeProvider whose scripted "generated code"
deliberately tries to navigate off the fixture's own origin.
Marker: ``browser``.
"""

from __future__ import annotations

import pytest

from agentalyze.config import Settings
from agentalyze.providers.base import ChatMessage, CompletionResult
from agentalyze.runner.code_agent.loop import run_task_code_agent
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.registry import TASKS_BY_ID

pytestmark = pytest.mark.browser


class MaliciousCodeProvider:
    """Deterministic scripted Provider: its 'generated code' tries to leave
    the fixture's own origin, then gives up — exactly the shape a jailbroken
    or confused model's code could take."""

    def __init__(self) -> None:
        self.name = "malicious-code-provider"
        self._step = 0

    async def chat_completion(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._step += 1
        if self._step == 1:
            text = (
                "<code>\n"
                "result = navigate(url='http://evil.example.com/steal')\n"
                "print(result)\n"
                "</code>"
            )
        else:
            text = "<code>\nfinal_answer(success=False)\n</code>"
        return CompletionResult(
            message=ChatMessage(role="assistant", content=text),
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_seconds=0.001,
            finish_reason="stop",
        )

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def code_agent_settings(runner_settings: Settings) -> Settings:
    return runner_settings.model_copy(update={"code_agent_executor_type": "local"})


async def test_navigate_off_origin_is_blocked_not_a_crash(
    code_agent_settings: Settings,
) -> None:
    task = TASKS_BY_ID["nav-simple-link-01"]
    provider = MaliciousCodeProvider()

    trace = await run_task_code_agent(task, provider, code_agent_settings)

    # A blocked navigation is a normal (failed) tool observation, not a
    # runner crash: the guard degrades gracefully exactly like the ReAct
    # loop's equivalent test (tests/runner/test_tools.py::test_foreign_origin_blocked).
    assert trace.outcome is not RunOutcome.FAILURE_CRASH
    navigate_steps = [s for s in trace.steps if s.tool_call and s.tool_call.name == "navigate"]
    assert navigate_steps, "expected at least one recorded navigate call"
    blocked = navigate_steps[0]
    assert blocked.tool_result is not None
    assert blocked.tool_result.success is False
    assert "blocked" in blocked.tool_result.output
    assert "evil.example.com" in blocked.tool_result.output
    # The agent then gave up (final_answer(success=False)) rather than
    # having somehow "succeeded" via the blocked navigation.
    assert trace.outcome is RunOutcome.FAILURE_VERIFIER
