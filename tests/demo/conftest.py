"""Hermetic fixtures for the public-demo tests.

No network, no browser, no real provider: the runner seam
(``agentalyze.demo.routes.run_task``) is monkeypatched per test, exactly the
same way the HTTP-service tests stub ``run_suite``. The HTTP layer, request
parsing, allowlist checks, rate limiting, timeouts and log redaction are all
real code paths exercised here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentalyze.api.app import create_app
from agentalyze.config import Settings
from agentalyze.providers.base import ChatMessage, CompletionResult, ToolCall
from agentalyze.runner.trace import RunOutcome, RunTrace, StepEvent, ToolResult
from agentalyze.tasks.models import VerificationResult

#: A syntactically plausible fake key used across tests. Must NEVER appear in
#: logs or responses of any tested code path.
DEMO_API_KEY = "sk-demo-TEST-KEY-0123456789abcdef"


def make_demo_settings(tmp_path: Any, **overrides: Any) -> Settings:
    """A demo-enabled Settings bundle fully isolated inside ``tmp_path``."""
    base: dict[str, Any] = {
        "fixtures_dir": tmp_path / "fixtures",
        "results_dir": tmp_path / "results",
        "providers_config_path": tmp_path / "providers.yaml",
        "regression_config_path": tmp_path / "regression.yaml",
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}",
        "demo_mode_enabled": True,
        # Tests talk plain HTTP to TestClient; the HTTPS guard is covered by
        # its own dedicated test with an explicitly external host.
        "demo_https_required": False,
        "api_runs_rate_limit": "none",
        "log_level": "WARNING",
    }
    base.update(overrides)
    return Settings(**base)


def make_fake_trace(task_id: str, outcome: RunOutcome = RunOutcome.SUCCESS) -> RunTrace:
    """A small but REAL RunTrace (no mocks of the trace model itself)."""

    def _completion() -> CompletionResult:
        return CompletionResult(
            message=ChatMessage(role="assistant", content="ok"),
            prompt_tokens=120,
            completion_tokens=15,
            total_tokens=135,
            latency_seconds=0.01,
            finish_reason="stop",
        )

    steps = [
        StepEvent(
            step_number=1,
            timestamp=datetime.now(UTC),
            llm_request_messages=[ChatMessage(role="user", content="Do the task.")],
            llm_response=_completion(),
            tool_call=ToolCall(
                id="call_1", name="click", arguments={"element_id": "[e3]"}
            ),
            tool_result=ToolResult(success=True, output="Clicked [e3]."),
        ),
        StepEvent(
            step_number=2,
            timestamp=datetime.now(UTC),
            llm_request_messages=[ChatMessage(role="user", content="Do the task.")],
            llm_response=_completion(),
        ),
    ]
    verifier: VerificationResult | None = None
    if outcome in (RunOutcome.SUCCESS, RunOutcome.FAILURE_VERIFIER):
        verifier = VerificationResult(
            success=outcome is RunOutcome.SUCCESS,
            reason="success marker found on the page"
            if outcome is RunOutcome.SUCCESS
            else "agent declared done but the page disagrees",
        )
    return RunTrace(
        run_id="demo-run-0001",
        task_id=task_id,
        provider_name="demo:openai/gpt-4o-mini",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        outcome=outcome,
        verifier_result=verifier,
        steps=steps,
        total_prompt_tokens=240,
        total_completion_tokens=30,
        total_cost_usd=None,  # never invent a number
        wall_clock_seconds=1.234,
    )


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Factory: build a TestClient over a demo-enabled app.

    ``run_task_impl`` replaces the runner seam; when omitted, any invocation
    is a test failure (used by validation-only tests).
    """

    def _make(settings: Settings, run_task_impl: Any = None) -> TestClient:
        if run_task_impl is not None:
            monkeypatch.setattr("agentalyze.demo.routes.run_task", run_task_impl)

        async def _forbid(*args: Any, **kwargs: Any) -> RunTrace:
            raise AssertionError("demo run must not execute a task in this test")

        if run_task_impl is None:
            monkeypatch.setattr("agentalyze.demo.routes.run_task", _forbid)
        return TestClient(create_app(settings))

    return _make


def valid_run_body(**overrides: Any) -> dict[str, Any]:
    """A minimal valid POST /demo/run body."""
    body: dict[str, Any] = {
        "provider_kind": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": DEMO_API_KEY,
        "model_name": "openai/gpt-4o-mini",
        "task_id": "nav-simple-link-01",
    }
    body.update(overrides)
    return body
