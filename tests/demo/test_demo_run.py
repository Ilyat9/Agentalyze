"""The happy path: compact, frontend-friendly, honest run summary."""

from __future__ import annotations

from agentalyze.demo.redaction import registered_secrets
from agentalyze.runner.trace import RunOutcome
from tests.demo.conftest import (
    DEMO_API_KEY,
    make_demo_settings,
    make_fake_trace,
    valid_run_body,
)


def test_demo_page_and_tasks_are_served(
    tmp_path: object, make_client: object
) -> None:
    client = make_client(make_demo_settings(tmp_path))
    with client as c:
        page = c.get("/demo")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        assert "How your API key is handled" in page.text  # the visible consent text

        tasks = c.get("/demo/tasks")
        assert tasks.status_code == 200
        payload = tasks.json()
        assert len(payload["tasks"]) == 3
        for task in payload["tasks"]:
            assert task["difficulty"] == "easy"
            assert task["max_steps"] <= 8
            assert task["description"]  # human-readable text, not raw ids


def test_successful_run_returns_compact_summary(
    tmp_path: object, make_client: object
) -> None:
    async def stub_run_task(
        task: object, provider: object, run_settings: object
    ) -> object:
        return make_fake_trace("nav-simple-link-01", RunOutcome.SUCCESS)

    client = make_client(make_demo_settings(tmp_path), stub_run_task)
    response = client.post("/demo/run", json=valid_run_body())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["success"] is True
    assert body["outcome"] == "success"
    assert body["task_id"] == "nav-simple-link-01"
    assert body["step_count"] == 2
    assert body["total_prompt_tokens"] == 240
    assert body["total_cost_usd"] is None  # never invented
    assert isinstance(body["wall_clock_seconds"], float)
    assert body["verifier_reason"].startswith("success marker")
    first = body["steps"][0]
    assert first["action"] == "click"
    assert first["ok"] is True
    assert "Clicked [e3]" in first["observation"]
    assert DEMO_API_KEY not in response.text
    assert registered_secrets() == frozenset()


def test_failed_run_is_reported_honestly(
    tmp_path: object, make_client: object
) -> None:
    async def stub_run_task(
        task: object, provider: object, run_settings: object
    ) -> object:
        return make_fake_trace("form-fill-basic-01", RunOutcome.FAILURE_VERIFIER)

    client = make_client(make_demo_settings(tmp_path), stub_run_task)
    response = client.post("/demo/run", json=valid_run_body(task_id="form-fill-basic-01"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["success"] is False
    assert body["outcome"] == "failure_verifier"


def test_demo_artifacts_do_not_touch_shared_results_dir(
    tmp_path: object, make_client: object
) -> None:
    """The demo run must never write into the shared ``results/`` storage."""
    settings = make_demo_settings(tmp_path)
    results_dir = settings.results_dir
    assert not results_dir.exists()

    async def stub_run_task(
        task: object, provider: object, run_settings: object
    ) -> object:
        # The runner writes its trace into the settings it was handed: for
        # demo runs that MUST be the ephemeral per-request temp dir (its
        # prefix is controlled by the handler).
        assert run_settings.results_dir != results_dir
        assert "agentalyze-demo-" in str(run_settings.results_dir)
        return make_fake_trace("nav-simple-link-01", RunOutcome.SUCCESS)

    client = make_client(settings, stub_run_task)
    with client as c:  # lifespan startup also must not create the shared dir
        assert c.post("/demo/run", json=valid_run_body()).status_code == 200
    assert not results_dir.exists()
