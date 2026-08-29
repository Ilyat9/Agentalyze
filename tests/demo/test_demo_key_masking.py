"""THE most important demo test: the key never leaks — not in responses, not
in logs, not in tracebacks — and is forgotten the moment the request ends.

The worst case is simulated deliberately: the stubbed runner raises an
exception whose MESSAGE embeds the raw key, and the resulting traceback is
rendered through the real structlog pipeline (configure_logging + the global
redaction processor). The captured record must contain the redacted
traceback and never the raw key.
"""

from __future__ import annotations

import logging

import structlog

import agentalyze.demo.routes as demo_routes
from agentalyze.api.observability import configure_logging
from agentalyze.demo.redaction import registered_secrets
from tests.demo.conftest import DEMO_API_KEY, make_demo_settings, valid_run_body


def test_key_masked_in_logs_and_response_on_crash(
    tmp_path: object, make_client: object
) -> None:
    settings = make_demo_settings(tmp_path)

    async def crashing_run_task(
        task: object, provider: object, run_settings: object
    ) -> object:
        # Worst case: an internal component interpolates the key into an
        # exception message. The traceback WILL contain it unless redacted.
        raise RuntimeError(f"provider transport exploded while using {DEMO_API_KEY}")

    # Capture handler attaches AFTER client startup: configure_logging REPLACES
    # all root handlers, and the lifespan's Alembic migrations (fileConfig)
    # replace them again — so only after `with client` are we in the steady
    # production state this test must exercise.
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    capture_handler = _Capture(level=logging.DEBUG)

    client = make_client(settings, crashing_run_task)
    with client as c:
        configure_logging("ERROR")
        logging.getLogger().addHandler(capture_handler)
        # Fresh structlog logger for the routes module bound to THIS
        # configuration (the module-level one may be cached from earlier
        # tests in the same process).
        object.__setattr__(
            demo_routes, "logger", structlog.get_logger("demo.routes.test")
        )
        try:
            response = c.post("/demo/run", json=valid_run_body())
        finally:
            logging.getLogger().removeHandler(capture_handler)

    # 1. The response body never contains the key.
    assert response.status_code == 500
    assert DEMO_API_KEY not in response.text

    # 2. The crash was logged through the pipeline...
    records = [r for r in captured if r.name == "demo.routes.test"]
    assert records, "the demo crash must be logged"
    event = records[0].msg  # the structlog event dict (post-processing)
    assert "demo run crashed unexpectedly" in event["event"]
    # 3. ...and the FULL rendered traceback carries the masked key, never the
    #    raw one.
    exception_text = event["exception"]
    assert DEMO_API_KEY not in exception_text
    assert "[REDACTED]" in exception_text
    assert DEMO_API_KEY not in str(event)

    # 4. The key is forgotten as soon as the request is over: no lingering
    #    redaction registration, no closure holding it.
    assert registered_secrets() == frozenset()


def test_invalid_body_does_not_echo_the_key(
    tmp_path: object, make_client: object
) -> None:
    """A 400 over a partially invalid body must not quote submitted values."""
    client = make_client(make_demo_settings(tmp_path))
    body = valid_run_body()
    body["model_name"] = 12345  # invalid type; FastAPI would echo this input
    response = client.post("/demo/run", json=body)
    assert response.status_code == 400
    assert DEMO_API_KEY not in response.text
    assert "12345" not in response.text


def test_malformed_json_does_not_echo_the_body(
    tmp_path: object, make_client: object
) -> None:
    client = make_client(make_demo_settings(tmp_path))
    response = client.post(
        "/demo/run",
        content=f'{{"api_key": "{DEMO_API_KEY}", broken'.encode(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert DEMO_API_KEY not in response.text
