"""Hardening tests found by adversarial probing of the live demo.

Two real gaps were found and closed:

1. SSRF: ``https://192.168.1.5/v1`` passed the scheme-only check, so the
   server would POST the (visitor-supplied) key to internal services —
   cloud metadata, RFC1918 space, loopback. In public mode private
   addresses are now rejected even over HTTPS.
2. Memory-DoS: the endpoint reads the whole body into memory; an
   anonymous visitor could send gigabyte-sized POSTs. Bodies > 64 KB are
   now rejected with 413 BEFORE being read into memory.
"""

from __future__ import annotations

from agentalyze.demo.redaction import registered_secrets
from agentalyze.providers.base import ProviderAuthError
from agentalyze.runner.trace import RunOutcome
from tests.demo.conftest import (
    DEMO_API_KEY,
    make_demo_settings,
    make_fake_trace,
    valid_run_body,
)


def test_private_upstream_rejected_in_public_mode(
    tmp_path: object, make_client: object
) -> None:
    settings = make_demo_settings(tmp_path, demo_https_required=True)
    client = make_client(settings)
    response = client.post(
        "/demo/run",
        json=valid_run_body(base_url="https://192.168.1.5/v1"),
    )
    assert response.status_code == 400
    assert "private/internal" in response.json()["detail"]
    assert registered_secrets() == frozenset()


def test_metadata_and_localhost_upstreams_rejected_in_public_mode(
    tmp_path: object, make_client: object
) -> None:
    settings = make_demo_settings(tmp_path, demo_https_required=True, demo_rate_limit="none")
    client = make_client(settings)
    for host in ("169.254.169.254", "localhost", "[::1]", "10.0.0.7"):
        response = client.post(
            "/demo/run",
            json=valid_run_body(base_url=f"https://{host}/v1"),
        )
        assert response.status_code == 400, host
        assert "private/internal" in response.json()["detail"]


def test_oversized_body_rejected_413(tmp_path: object, make_client: object) -> None:
    client = make_client(make_demo_settings(tmp_path))
    huge = '{"api_key":"' + "A" * (80 * 1024) + '"}'
    response = client.post(
        "/demo/run", content=huge, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert registered_secrets() == frozenset()  # the body was never processed


def test_provider_error_never_echoes_the_key(
    tmp_path: object, make_client: object
) -> None:
    """Worst-case upstream: it echoes the Authorization header back inside
    an error message. The visitor gets a fixed classification — never the
    exception text that could carry the key."""

    async def leaking_run_task(
        task: object, provider: object, run_settings: object
    ) -> object:
        raise ProviderAuthError(
            f"upstream echoed the request: Authorization: Bearer {DEMO_API_KEY}"
        )

    client = make_client(make_demo_settings(tmp_path), leaking_run_task)
    response = client.post("/demo/run", json=valid_run_body())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["error_kind"] == "provider_auth"
    assert DEMO_API_KEY not in response.text
    assert "echoed" not in response.text  # upstream text is never quoted
    assert registered_secrets() == frozenset()


def test_public_mode_still_accepts_public_upstreams(
    tmp_path: object, make_client: object
) -> None:
    """The SSRF guard must not break the normal path (public hostname)."""

    async def stub_run_task(
        task: object, provider: object, run_settings: object
    ) -> object:
        return make_fake_trace("nav-simple-link-01", RunOutcome.SUCCESS)

    settings = make_demo_settings(tmp_path, demo_https_required=True)
    client = make_client(settings, stub_run_task)
    response = client.post(
        "/demo/run",
        json=valid_run_body(base_url="https://openrouter.ai/api/v1"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
