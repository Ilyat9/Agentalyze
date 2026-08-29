"""The demo surface must NOT exist unless demo mode is explicitly enabled.

This protects self-hosted `agentalyze serve` users: an admin who never asked
for a public BYOK endpoint must never get one by default.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentalyze.api.app import create_app
from agentalyze.config import Settings
from tests.demo.conftest import DEMO_API_KEY, make_demo_settings


def test_settings_default_is_off() -> None:
    """The plain-default Settings never enables the demo surface."""
    assert Settings().demo_mode_enabled is False


def test_demo_endpoints_return_404_when_disabled(tmp_path: object) -> None:
    settings = make_demo_settings(tmp_path, demo_mode_enabled=False)
    with TestClient(create_app(settings)) as client:
        assert client.get("/demo").status_code == 404
        assert client.get("/demo/tasks").status_code == 404
        response = client.post(
            "/demo/run",
            json={
                "provider_kind": "openai_compatible",
                "api_key": DEMO_API_KEY,
                "model_name": "openai/gpt-4o-mini",
                "task_id": "nav-simple-link-01",
            },
        )
        assert response.status_code == 404
