"""A hanging runner must be cut off by the demo wall-clock, not hang forever."""

from __future__ import annotations

import asyncio
import time

from tests.demo.conftest import make_demo_settings, valid_run_body


def test_run_is_stopped_at_the_demo_timeout(
    tmp_path: object, make_client: object
) -> None:
    settings = make_demo_settings(tmp_path, demo_run_timeout_seconds=1.0)

    async def hanging_run_task(
        task: object, provider: object, run_settings: object
    ) -> object:
        await asyncio.sleep(60)  # deliberately far beyond the demo budget
        raise AssertionError("must have been cancelled by the demo timeout")

    client = make_client(settings, hanging_run_task)

    started = time.monotonic()
    response = client.post("/demo/run", json=valid_run_body())
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "timeout"
    assert "time budget" in body["message"]
    # The request must NOT have hung anywhere near the 60s stub sleep.
    assert elapsed < 15, f"demo run took {elapsed:.1f}s instead of ~1s"
