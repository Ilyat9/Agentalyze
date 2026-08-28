"""executor_type='docker' (and the other smolagents remote executors) status.

The task spec asked for a test confirming REAL network isolation under
Docker. Investigating that turned up something more fundamental first:
smolagents' remote executors (docker/e2b/modal/blaxel) reconstruct every
``Tool`` inside the sandbox via a bare zero-argument ``ToolClassName()`` call
(``smolagents.tools.get_tools_definition_code`` -> ``instance_to_source``),
not by shipping the live instance across. This project's tool adapters
require ``(ctx, recorder)`` — a live Playwright ``Page`` and the event loop
that owns it — which cannot be constructed that way inside a sandbox.

This was verified empirically, not just read off the source: running a real
task against a real local Docker daemon with ``executor_type='docker'``
built the image, started the container, and then just hung — zero
``ActionStep``s were ever recorded, and the run sat there until the task's
own wall-clock timeout fired (``FAILURE_TIMEOUT``), with no error raised
anywhere. ``code_agent/loop.py`` now fails FAST instead (a clear
``NotImplementedError`` before ever touching Docker) so this broken
combination cannot silently eat a task's whole timeout budget. See
docs/KNOWN_LIMITATIONS.md for the full writeup and what re-architecting tool
adapters as a host-side RPC bridge would take to actually support this.

Given that, "confirm real network isolation under Docker" is currently
inapplicable to this project's tools — there is no working docker-mode run
to isolate. This file tests the fail-fast guard itself (no Docker required)
and documents, rather than silently skips over, why the originally-requested
isolation test cannot be written as intended today.
"""

from __future__ import annotations

import pytest

from agentalyze.config import Settings
from agentalyze.runner.code_agent.loop import run_task_code_agent
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.registry import TASKS_BY_ID

pytestmark = pytest.mark.browser


class _NeverCalledProvider:
    """If this provider is ever called, the fail-fast guard did not fire
    before reaching the model — which would be the actual regression this
    test exists to catch."""

    name = "never-called-provider"

    async def chat_completion(self, messages, tools=None, temperature=0.0, max_tokens=None):
        raise AssertionError(
            "provider.chat_completion was called — the executor_type='docker' "
            "guard should have raised before any model call"
        )

    async def health_check(self) -> bool:
        return True


@pytest.mark.parametrize("executor_type", ["docker", "e2b", "modal", "blaxel"])
async def test_remote_executor_types_fail_fast_not_silently_hang(
    runner_settings: Settings, executor_type: str
) -> None:
    """run_task_code_agent (like react_loop.run_task) never lets an
    unhandled exception escape — it converts it to FAILURE_CRASH with the
    traceback preserved (see loop.py's outer try/except). The regression
    this test actually guards against is the SILENT ``FAILURE_TIMEOUT`` that
    was observed before the guard existed: a short 5s timeout here proves
    the guard fires well within the budget, not that it happens to survive
    to the real 120s default."""
    task = TASKS_BY_ID["nav-simple-link-01"].model_copy(update={"timeout_seconds": 5})
    settings = runner_settings.model_copy(update={"code_agent_executor_type": executor_type})

    trace = await run_task_code_agent(task, _NeverCalledProvider(), settings)

    assert trace.outcome is RunOutcome.FAILURE_CRASH
    assert trace.error is not None
    assert "NotImplementedError" in trace.error
    assert "constructor" in trace.error
    assert trace.wall_clock_seconds < 5, (
        "guard should fail immediately, not consume the task's timeout budget"
    )
