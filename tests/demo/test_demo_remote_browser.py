"""Config plumbing for the split deployment (remote browser via CDP).

The orchestrator (lightweight, free 512MB tier) delegates the browser to a
browser-infra provider (Browserless free tier). Two contracts are tested:

* remote browser configured WITHOUT a public fixture base URL = a server
  misconfiguration surfaced as an honest, explicit error;
* remote browser WITH fixture URL = ``fixture_base_url`` is forwarded to the
  runner seam (and the local fixture server is skipped);
* local-browser mode (default) keeps passing no fixture kwarg at all;
* fixtures are served publicly under GET /demo/fixtures/... .
"""

from __future__ import annotations

from agentalyze.runner.trace import RunOutcome
from tests.demo.conftest import (
    make_demo_settings,
    make_fake_trace,
    valid_run_body,
)


def test_cdp_without_fixture_url_is_a_config_error(
    tmp_path: object, make_client: object
) -> None:
    settings = make_demo_settings(
        tmp_path, browser_cdp_endpoint="wss://b.example?token=x"
    )
    client = make_client(settings)  # any runner invocation is a failure here
    response = client.post("/demo/run", json=valid_run_body())
    assert response.status_code == 500
    body = response.json()
    assert body["error_kind"] == "config"
    assert "demo_fixture_base_url" in body["message"]


def test_cdp_mode_passes_public_fixture_base_url_to_runner(
    tmp_path: object, make_client: object
) -> None:
    captured: dict[str, object] = {}

    async def stub_run_task(
        task: object,
        provider: object,
        run_settings: object,
        *,
        fixture_base_url: object = None,
    ) -> object:
        captured["fixture_base_url"] = fixture_base_url
        return make_fake_trace("nav-simple-link-01", RunOutcome.SUCCESS)

    settings = make_demo_settings(
        tmp_path,
        browser_cdp_endpoint="wss://b.example?token=x",
        demo_fixture_base_url="https://demo.example.com/demo/fixtures/",
    )
    client = make_client(settings, stub_run_task)
    response = client.post("/demo/run", json=valid_run_body())

    assert response.status_code == 200
    assert captured["fixture_base_url"] == "https://demo.example.com/demo/fixtures"


def test_local_browser_mode_keeps_historical_call_signature(
    tmp_path: object, make_client: object
) -> None:
    """No CDP endpoint -> run_task is called WITHOUT the fixture kwarg."""
    captured: dict[str, object] = {}

    async def stub_run_task(task: object, provider: object, run_settings: object, **kwargs: object) -> object:
        captured["kwargs"] = kwargs
        return make_fake_trace("nav-simple-link-01", RunOutcome.SUCCESS)

    client = make_client(make_demo_settings(tmp_path), stub_run_task)
    response = client.post("/demo/run", json=valid_run_body())

    assert response.status_code == 200
    assert captured["kwargs"] == {}


def test_fixtures_are_served_publicly(tmp_path: object, make_client: object) -> None:
    settings = make_demo_settings(tmp_path)
    page = settings.fixtures_dir / "navigation" / "simple_link_01.html"
    page.parent.mkdir(parents=True)
    page.write_text("<html><body>fixture-ok</body></html>", encoding="utf-8")

    client = make_client(settings)
    response = client.get("/demo/fixtures/navigation/simple_link_01.html")
    assert response.status_code == 200
    assert "fixture-ok" in response.text.replace("_", "-")
