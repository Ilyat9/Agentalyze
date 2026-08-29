"""HTTPS-only arrival for POST /demo/run, and HTTPS-only key forwarding."""

from __future__ import annotations

from agentalyze.demo.redaction import registered_secrets
from agentalyze.runner.trace import RunOutcome
from tests.demo.conftest import (
    make_demo_settings,
    make_fake_trace,
    valid_run_body,
)


def _success_stub() -> object:
    async def _run(task: object, provider: object, run_settings: object) -> object:
        return make_fake_trace("nav-simple-link-01", RunOutcome.SUCCESS)

    return _run


def test_key_rejected_over_plain_http_from_external_host(
    tmp_path: object, make_client: object
) -> None:
    """An external plain-HTTP request never reaches key processing."""
    settings = make_demo_settings(tmp_path, demo_https_required=True)
    client = make_client(settings, _success_stub())
    with client as c:
        c.base_url = "http://external.example.com"  # external host, plain http
        response = c.post("/demo/run", json=valid_run_body())
    assert response.status_code == 403
    assert registered_secrets() == frozenset()  # the key was never processed


def test_https_request_is_accepted(
    tmp_path: object, make_client: object
) -> None:
    settings = make_demo_settings(tmp_path, demo_https_required=True)
    client = make_client(settings, _success_stub())
    with client as c:  # TestClient default base is https://testserver
        response = c.post("/demo/run", json=valid_run_body())
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_forwarded_proto_header_enables_https_behind_proxy(
    tmp_path: object, make_client: object
) -> None:
    """Plain HTTP + platform TLS-terminator header = accepted (proxy hop)."""
    settings = make_demo_settings(tmp_path, demo_https_required=True)
    client = make_client(settings, _success_stub())
    with client as c:
        response = c.post(
            "/demo/run",
            json=valid_run_body(),
            headers={"X-Forwarded-Proto": "https"},
        )
    assert response.status_code == 200


def test_key_never_forwarded_to_non_https_base_url(
    tmp_path: object, make_client: object
) -> None:
    """Even over HTTPS arrival, a plain-http upstream endpoint is refused."""
    settings = make_demo_settings(tmp_path)
    client = make_client(settings, _success_stub())
    response = client.post(
        "/demo/run",
        json=valid_run_body(base_url="http://api.example.com/v1"),
    )
    assert response.status_code == 400
    assert "non-HTTPS" in response.json()["detail"]
    assert registered_secrets() == frozenset()  # finished cleanly, key forgotten


def test_localhost_http_upstream_is_allowed_for_dev(
    tmp_path: object, make_client: object
) -> None:
    settings = make_demo_settings(tmp_path)
    client = make_client(settings, _success_stub())
    response = client.post(
        "/demo/run",
        json=valid_run_body(base_url="http://localhost:8000/v1"),
    )
    assert response.status_code == 200
