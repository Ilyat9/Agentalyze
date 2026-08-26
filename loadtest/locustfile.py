"""Locust load test for the Agentalyze HTTP API.

Simulates several concurrent clients running suites and polling status,
verifying under load that (a) rate limiting protects POST /runs, (b) the
suite-runner parallelism behaves correctly across clients.

Run against a LOCAL service stack:

    docker compose -f docker-compose.service.yml up -d --build
    # create a key once:
    docker compose -f docker-compose.service.yml run --rm agentalyze-api \\
        agentalyze create-api-key --name loadtest

    pip install -e ".[loadtest]"
    locust -f loadtest/locustfile.py --host http://localhost:8000 \\
           --users 20 --spawn-rate 2 --run-time 3m

Set AGT_API_KEY env var to the generated key. Expectation: POST /runs
returns 202 at <= the configured rate per key and 429 beyond it; GETs stay
fast; /health never degrades below 200 while the DB is up.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

# A small, real task keeps each accepted run short but fully exercised.
TASK_ID = os.environ.get("AGT_TASK_ID", "nav-simple-link-01")
PROVIDER = os.environ.get("AGT_PROVIDER", "fake-provider")


class ApiUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        key = os.environ.get("AGT_API_KEY", "")
        self.client.headers["Authorization"] = f"Bearer {key}"

    @task(1)
    def submit_run(self) -> None:
        with self.client.post(
            "/runs",
            json={"provider_names": [PROVIDER], "task_ids": [TASK_ID]},
            name="POST /runs",
            catch_response=True,
        ) as response:
            if response.status_code == 429:
                response.success()  # rate limiting WORKING is a pass here
                return
            if response.status_code != 202:
                response.failure(f"unexpected {response.status_code}: {response.text[:200]}")
                return
            run_id = response.json()["suite_run_id"]
            response.success()
            # Poll the just-submitted run a few times like a real client.
            for _ in range(random.randint(1, 3)):
                self.client.get(f"/runs/{run_id}", name="GET /runs/[id]")

    @task(3)
    def read_status(self) -> None:
        self.client.get("/runs", name="GET /runs")

    @task(2)
    def health(self) -> None:
        self.client.get("/readyz", name="GET /readyz")
