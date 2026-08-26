"""Health endpoints: DB + provider checks, sanitized failure details."""

from __future__ import annotations

from tests.api.conftest import FakeProvider


class TestHealth:
    def test_healthy_everything(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _ = client
        response = test_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["components"]["database"]["ok"] is True
        assert body["components"]["providers"]["ok"] is True

    def test_all_providers_down_is_503(
        self, service_settings, monkeypatch  # type: ignore[no-untyped-def]
    ) -> None:
        from fastapi.testclient import TestClient as TC

        from agentalyze.api.app import create_app
        from agentalyze.api.db import run_migrations

        run_migrations(service_settings.database_url)
        monkeypatch.setattr(
            "agentalyze.api.app.load_providers",
            lambda config_path, settings=None: {"fake-provider": FakeProvider(healthy=False)},
        )
        # Auth off so the health endpoint is reachable without a key here.
        service_settings.api_auth_required = False
        with TC(create_app(service_settings)) as test_client:
            response = test_client.get("/health")
        assert response.status_code == 503
        body = response.json()
        assert body["components"]["providers"]["ok"] is False

    def test_provider_config_missing_is_503_not_500(
        self, service_settings, monkeypatch  # type: ignore[no-untyped-def]
    ) -> None:
        from fastapi.testclient import TestClient as TC

        from agentalyze.api.app import create_app
        from agentalyze.api.db import run_migrations

        run_migrations(service_settings.database_url)
        service_settings.providers_config_path = (
            service_settings.providers_config_path.parent / "absent.yaml"
        )
        service_settings.api_auth_required = False
        with TC(create_app(service_settings)) as test_client:
            response = test_client.get("/health")
        assert response.status_code == 503

    def test_readyz_and_livez(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _ = client
        assert test_client.get("/readyz").status_code == 200
        assert test_client.get("/livez").status_code == 200


class TestMetricsEndpoint:
    def test_metrics_exposed(self, client) -> None:  # type: ignore[no-untyped-def]
        test_client, _ = client
        response = test_client.get("/metrics")
        assert response.status_code == 200
        text = response.text
        assert "agentalyze_suite_runs_total" in text
        assert "agentalyze_provider_call_seconds" in text
