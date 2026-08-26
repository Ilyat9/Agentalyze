"""Shared fixtures for the HTTP-service tests.

Everything is hermetic: tmp results dir, tmp SQLite database (real Alembic
migrations run against it), and provider loading monkeypatched so no network
call ever happens. The suite-run execution itself is stubbed at the SAME
seam the real implementation plugs into (``run_suite``), keeping these tests
fast while still exercising the full HTTP -> DB -> background-task -> report
pipeline.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from agentalyze.api.auth import generate_api_key, hash_api_key
from agentalyze.api.db import (
    ApiKeyRecord,
    make_engine,
    make_session_factory,
    run_migrations,
)
from agentalyze.config import Settings
from agentalyze.orchestration.suite_runner import SuiteRunResult


class FakeProvider:
    """Provider-protocol stand-in: healthy, never touches the network."""

    def __init__(self, name: str = "fake-provider", healthy: bool = True) -> None:
        self.name = name
        self._healthy = healthy
        self.chat_completion_calls = 0

    async def chat_completion(self, *args: Any, **kwargs: Any) -> None:
        self.chat_completion_calls += 1
        raise AssertionError("tests must not invoke real completions")

    async def health_check(self) -> bool:
        return self._healthy


@pytest.fixture
def service_settings(tmp_path: Any) -> Settings:
    results = tmp_path / "results"
    results.mkdir()
    providers_yaml = tmp_path / "providers.yaml"
    providers_yaml.write_text(
        yaml.safe_dump(
            {
                "providers": [
                    {
                        "name": "fake-provider",
                        "kind": "ollama",
                        "model_name": "fake-model",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        fixtures_dir=tmp_path / "fixtures",
        results_dir=results,
        providers_config_path=providers_yaml,
        regression_config_path=tmp_path / "regression.yaml",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}",
        api_auth_required=True,
        api_runs_rate_limit="none",
        log_level="WARNING",
    )


def make_fake_run_result(suite_run_id: str, settings: Settings) -> SuiteRunResult:
    from agentalyze.orchestration.suite_runner import SuiteRunConfig

    return SuiteRunResult(
        suite_run_id=suite_run_id,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        config=SuiteRunConfig(provider_names=["fake-provider"]),
    )


@pytest.fixture
def client(
    service_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, str]]:
    """App under test + a valid plaintext API key."""
    run_migrations(service_settings.database_url)

    # Seed one API key.
    plaintext = generate_api_key()

    async def _seed() -> None:
        engine = make_engine(service_settings.database_url)
        try:
            factory = make_session_factory(engine)
            async with factory() as session:
                session.add(
                    ApiKeyRecord(name="test-client", key_hash=hash_api_key(plaintext))
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    # Provider loading never hits the network in tests.
    fake = FakeProvider()
    monkeypatch.setattr(
        "agentalyze.api.app.load_providers",
        lambda config_path, settings=None: {"fake-provider": fake},
    )
    # Suite execution is stubbed but still produces a real report artifact via
    # the manager's generate_report call.
    from agentalyze.orchestration.report import generate_report

    async def fake_run_suite(config: Any, providers: Any, settings: Settings,
                             *, suite_run_id: str | None = None) -> SuiteRunResult:
        result = make_fake_run_result(suite_run_id or "missing-id", settings)
        generate_report(result, settings.results_dir)
        return result

    monkeypatch.setattr("agentalyze.api.service.run_suite", fake_run_suite)

    from agentalyze.api.app import create_app

    app = create_app(service_settings)
    with TestClient(app) as test_client:
        yield test_client, plaintext


@pytest.fixture
def auth_headers() -> Callable[[str], dict[str, str]]:
    def _make(key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}"}

    return _make


@pytest.fixture
def wait_for_terminal() -> Callable[[TestClient, str, dict[str, str]], str]:
    """Poll GET /runs/{id} until the status leaves pending/running."""

    import time as _time

    def _wait(client: TestClient, run_id: str, headers: dict[str, str]) -> str:
        for _ in range(200):
            response = client.get(f"/runs/{run_id}", headers=headers)
            status_value = response.json()["status"]
            if status_value not in ("pending", "running"):
                return str(status_value)
            _time.sleep(0.05)
        raise AssertionError(f"run {run_id} did not finish in time")

    return _wait
