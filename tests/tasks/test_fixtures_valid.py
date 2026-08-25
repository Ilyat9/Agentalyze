"""Integration validation: every registered fixture must be programmatically solvable.

Runs ``validate_fixtures.validate_task`` for each task with a real Chromium:
the page must load without JS console errors, the reference steps must be
executable, the success marker must be reachable, and the task's own verifier
must accept the driven final state.

Requires Chromium: marked ``browser`` (excluded from the default pytest run).
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright", reason="requires the 'browser' extra")

from agentalyze.tasks.registry import TASKS
from agentalyze.tasks.validate_fixtures import validate_task

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
async def test_fixture_is_solvable_and_verified(browser, fixture_server, task) -> None:
    report = await validate_task(browser, fixture_server.base_url, task)
    assert report.ok, (
        f"{report.task_id}: {report.reason}\nconsole errors: {report.console_errors or 'none'}"
    )
    assert report.console_errors == []
