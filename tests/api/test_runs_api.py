"""POST /runs lifecycle: acceptance, background execution, report delivery."""

from __future__ import annotations


class TestRunsApi:
    def test_submit_returns_202_with_id(
        self, client, auth_headers, wait_for_terminal  # type: ignore[no-untyped-def]
    ) -> None:
        test_client, key = client
        response = test_client.post(
            "/runs",
            json={"provider_names": ["fake-provider"],
                  "task_ids": ["nav-simple-link-01"]},
            headers=auth_headers(key),
        )
        assert response.status_code == 202, response.text
        body = response.json()
        run_id = body["suite_run_id"]
        assert body["status"] == "pending"
        assert response.headers["Location"] == f"/runs/{run_id}"

        status_value = wait_for_terminal(test_client, run_id, auth_headers(key))
        assert status_value == "completed"

    def test_report_available_after_completion(
        self, client, auth_headers, wait_for_terminal  # type: ignore[no-untyped-def]
    ) -> None:
        test_client, key = client
        headers = auth_headers(key)
        run_id = test_client.post(
            "/runs",
            json={"provider_names": ["fake-provider"],
                  "task_ids": ["nav-simple-link-01"]},
            headers=headers,
        ).json()["suite_run_id"]
        wait_for_terminal(test_client, run_id, headers)

        md = test_client.get(f"/runs/{run_id}/report", headers=headers)
        assert md.status_code == 200
        assert "text/markdown" in md.headers["content-type"]
        assert md.text.strip()

        html = test_client.get(f"/runs/{run_id}/report?format=html", headers=headers)
        assert html.status_code == 200
        assert "text/html" in html.headers["content-type"]

    def test_unknown_provider_is_400(self, client, auth_headers) -> None:  # type: ignore[no-untyped-def]
        test_client, key = client
        response = test_client.post(
            "/runs",
            json={"provider_names": ["no-such-provider"]},
            headers=auth_headers(key),
        )
        assert response.status_code == 400
        assert "unknown provider" in response.json()["detail"]

    def test_unknown_task_is_400(self, client, auth_headers) -> None:  # type: ignore[no-untyped-def]
        test_client, key = client
        response = test_client.post(
            "/runs",
            json={"provider_names": ["fake-provider"], "task_ids": ["not-a-task"]},
            headers=auth_headers(key),
        )
        assert response.status_code == 400

    def test_status_of_unknown_run_is_404(self, client, auth_headers) -> None:  # type: ignore[no-untyped-def]
        test_client, key = client
        response = test_client.get(
            "/runs/00000000-0000-0000-0000-000000000000", headers=auth_headers(key)
        )
        assert response.status_code == 404

    def test_list_runs_shows_submitted_run(
        self, client, auth_headers, wait_for_terminal  # type: ignore[no-untyped-def]
    ) -> None:
        test_client, key = client
        headers = auth_headers(key)
        run_id = test_client.post(
            "/runs",
            json={"provider_names": ["fake-provider"],
                  "task_ids": ["nav-simple-link-01"]},
            headers=headers,
        ).json()["suite_run_id"]
        wait_for_terminal(test_client, run_id, headers)

        listing = test_client.get("/runs", headers=headers).json()["runs"]
        assert any(entry["suite_run_id"] == run_id for entry in listing)

    def test_path_traversal_in_report_id_blocked(self, client, auth_headers) -> None:  # type: ignore[no-untyped-def]
        test_client, key = client
        response = test_client.get(
            "/runs/..%2F..%2Fetc/report", headers=auth_headers(key)
        )
        # Whatever the router does with this, it must never return file
        # contents from outside the results dir.
        if response.status_code == 200:
            raise AssertionError("path traversal reached the report handler")
        assert response.status_code in (400, 404)


class TestRateLimit:
    def test_rate_limit_returns_429(
        self, service_settings, monkeypatch  # type: ignore[no-untyped-def]
    ) -> None:
        from fastapi.testclient import TestClient as TC

        from agentalyze.api.db import run_migrations
        from tests.api.conftest import FakeProvider, make_fake_run_result

        service_settings.api_runs_rate_limit = "2/minute"
        run_migrations(service_settings.database_url)

        fake = FakeProvider()
        monkeypatch.setattr(
            "agentalyze.api.app.load_providers",
            lambda config_path, settings=None: {"fake-provider": fake},
        )

        async def fake_run_suite(config, providers, settings, *, suite_run_id=None):  # type: ignore[no-untyped-def]
            return make_fake_run_result(suite_run_id or "x", settings)

        monkeypatch.setattr("agentalyze.api.service.run_suite", fake_run_suite)

        from agentalyze.api.app import create_app
        from agentalyze.api.auth import generate_api_key, hash_api_key
        from agentalyze.api.db import (
            ApiKeyRecord,
            make_engine,
            make_session_factory,
        )

        plaintext = generate_api_key()

        async def seed() -> None:
            engine = make_engine(service_settings.database_url)
            try:
                factory = make_session_factory(engine)
                async with factory() as session:
                    session.add(
                        ApiKeyRecord(name="rl", key_hash=hash_api_key(plaintext))
                    )
                    await session.commit()
            finally:
                await engine.dispose()

        import asyncio as _asyncio

        _asyncio.run(seed())

        with TC(create_app(service_settings)) as c:
            headers = {"Authorization": f"Bearer {plaintext}"}
            codes = [
                c.post(
                    "/runs",
                    json={"provider_names": ["fake-provider"],
                          "task_ids": ["nav-simple-link-01"]},
                    headers=headers,
                ).status_code
                for _ in range(3)
            ]
        assert codes[:2] == [202, 202]
        assert codes[2] == 429


# MARKER
