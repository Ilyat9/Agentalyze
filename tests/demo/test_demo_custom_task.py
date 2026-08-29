"""Visitor-written custom tasks: same cost ceilings, honest self-reported verdict."""

from __future__ import annotations

from typing import Any

from agentalyze.demo.tasks import (
    CUSTOM_INSTRUCTIONS_MAX_CHARS,
    CUSTOM_TASK_ID,
    CUSTOM_TASK_MAX_STEPS,
    build_custom_task,
)
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.verifiers import VERIFIERS
from tests.demo.conftest import (
    DEMO_API_KEY,
    make_demo_settings,
    make_fake_trace,
    valid_run_body,
)


def test_custom_task_without_instructions_is_rejected(
    tmp_path: Any, make_client: Any
) -> None:
    """task_id=custom without goal text must be a clear 400, never a run."""
    client = make_client(make_demo_settings(tmp_path))
    response = client.post(
        "/demo/run", json=valid_run_body(task_id=CUSTOM_TASK_ID)
    )
    assert response.status_code == 400
    assert "custom_instructions" in response.json()["detail"]


def test_custom_task_blank_instructions_are_rejected(
    tmp_path: Any, make_client: Any
) -> None:
    client = make_client(make_demo_settings(tmp_path))
    response = client.post(
        "/demo/run",
        json=valid_run_body(
            task_id=CUSTOM_TASK_ID, custom_instructions="   \n\t  "
        ),
    )
    assert response.status_code == 400


def test_custom_task_over_length_instructions_are_rejected(
    tmp_path: Any, make_client: Any
) -> None:
    """The 500-char cap is enforced at validation (before any run starts)."""
    client = make_client(make_demo_settings(tmp_path))
    response = client.post(
        "/demo/run",
        json=valid_run_body(
            task_id=CUSTOM_TASK_ID,
            custom_instructions="x" * (CUSTOM_INSTRUCTIONS_MAX_CHARS + 1),
        ),
    )
    assert response.status_code == 400
    errors = response.json()["errors"]
    assert any(e["field"] == "custom_instructions" for e in errors)


def test_custom_task_reaches_runner_with_server_fixed_budgets(
    tmp_path: Any, make_client: Any
) -> None:
    """The visitor controls ONLY the goal text; budgets/verifier stay ours."""
    captured: dict[str, Any] = {}

    async def stub_run_task(task: Any, provider: Any, run_settings: Any) -> Any:
        captured["task"] = task
        return make_fake_trace(CUSTOM_TASK_ID, RunOutcome.SUCCESS)

    goal = "Find the support email address on the page and report it."
    client = make_client(make_demo_settings(tmp_path), stub_run_task)
    response = client.post(
        "/demo/run",
        json=valid_run_body(task_id=CUSTOM_TASK_ID, custom_instructions=goal),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["task_id"] == CUSTOM_TASK_ID
    assert DEMO_API_KEY not in response.text

    task = captured["task"]
    assert task.description == goal  # verbatim goal, whitespace-normalized
    assert task.verifier_id == "verify-agent-verdict"  # honest self-report
    assert task.max_steps <= CUSTOM_TASK_MAX_STEPS  # server-fixed ceiling
    assert task.timeout_seconds <= 90
    assert task.fixture_url_path == "/navigation/simple_link_01.html"  # same sandbox


def test_build_custom_task_normalizes_and_truncates_goal() -> None:
    raw = "  Click   the\n\nDocumentation link.  " + "y" * 1000
    task = build_custom_task(raw)
    assert "\n" not in task.description
    assert len(task.description) <= CUSTOM_INSTRUCTIONS_MAX_CHARS
    assert task.description.startswith("Click the Documentation link.")


def test_unknown_task_ids_still_rejected_custom_needs_instructions(
    tmp_path: Any, make_client: Any
) -> None:
    """The allowlist survives: arbitrary registry ids remain forbidden."""
    client = make_client(make_demo_settings(tmp_path))
    response = client.post(
        "/demo/run", json=valid_run_body(task_id="distractor-forms-03")
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "custom" in detail  # the error advertises the custom option


async def test_agent_verdict_verifier_reads_dom_stamp() -> None:
    """Unit: success only for the done(true) stamp; given-up stays failure."""
    from agentalyze.tasks.verifiers import AgentVerdictVerifier

    verifier = VERIFIERS["verify-agent-verdict"]
    assert isinstance(verifier, AgentVerdictVerifier)

    class FakePage:
        def __init__(self, attr: str | None) -> None:
            self._attr = attr

        async def evaluate(self, _script: str) -> str | None:
            return self._attr

    assert (await verifier.verify(FakePage("success"))).success is True
    given_up = await verifier.verify(FakePage("given-up"))
    assert given_up.success is False
    assert "gave up" in given_up.reason
    absent = await verifier.verify(FakePage(None))
    assert absent.success is False
    assert "No agent verdict" in absent.reason
