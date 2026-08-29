"""POST /demo/run is rate limited per client IP (slowapi, shared limiter)."""

from __future__ import annotations

from agentalyze.runner.trace import RunOutcome
from tests.demo.conftest import make_demo_settings, make_fake_trace, valid_run_body


def test_requests_over_the_limit_get_429_and_do_not_run(
    tmp_path: object, make_client: object
) -> None:
    settings = make_demo_settings(tmp_path, demo_rate_limit="2 per minute")
    seen_calls: list[int] = []

    async def stub_run_task(task: object, provider: object, run_settings: object) -> object:
        seen_calls.append(1)
        return make_fake_trace("nav-simple-link-01", RunOutcome.SUCCESS)

    client = make_client(settings, stub_run_task)

    responses = [client.post("/demo/run", json=valid_run_body()) for _ in range(4)]
    statuses = [r.status_code for r in responses]

    assert statuses[:2] == [200, 200]
    assert statuses[2] == 429
    assert statuses[3] == 429
    assert sum(seen_calls) == 2  # over-limit requests never reached the runner
    body = responses[2].json()
    assert "rate limit" in body["detail"].lower()
