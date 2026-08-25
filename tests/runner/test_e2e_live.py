"""The one fully honest end-to-end test: REAL model + REAL Chromium.

Requirements: a running Ollama on localhost:11434 with a small instruct
model. Marked ``e2e_live`` — excluded from default runs AND from CI; run it
explicitly and rarely::

    pytest -m e2e_live

Model selection via env var ``AGENTALYZE_E2E_MODEL`` (default llama3.1:8b).
"""

from __future__ import annotations

import os

import pytest

from agentalyze.providers.ollama import DEFAULT_BASE_URL as OLLAMA_BASE_URL
from agentalyze.providers.ollama import OllamaProvider
from agentalyze.runner import run_task
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.registry import TASKS_BY_ID

pytestmark = [pytest.mark.browser, pytest.mark.e2e_live]

EASY_TASK_IDS = ["nav-simple-link-01", "form-fill-basic-01"]


async def test_real_model_solves_one_easy_task(runner_settings) -> None:
    model = os.environ.get("AGENTALYZE_E2E_MODEL", "llama3.1:8b")
    provider = OllamaProvider(
        base_url=OLLAMA_BASE_URL,
        api_key="ollama",
        name=f"e2e-{model}",
        model_name=model,
    )
    if not await provider.health_check():
        pytest.skip(f"Ollama is not reachable at {OLLAMA_BASE_URL}; start it to run e2e_live")

    task = next(TASKS_BY_ID[task_id] for task_id in EASY_TASK_IDS)
    trace = await run_task(task, provider, runner_settings)

    # We do not hard-assert SUCCESS (small local models are genuinely weak);
    # we assert the harness behaved correctly and print the outcome for eyes.
    assert trace.outcome in set(RunOutcome)
    assert trace.task_id == task.id
    assert trace.wall_clock_seconds > 0
    assert trace.steps, "a live model must produce at least one step"
    print(
        f"\ne2e_live[{model}] task={task.id} outcome={trace.outcome.value} "
        f"steps={len(trace.steps)} tokens={trace.total_prompt_tokens}/"
        f"{trace.total_completion_tokens}"
    )
