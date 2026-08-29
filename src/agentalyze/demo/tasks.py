"""The demo-task allowlist: a SHORT, pre-approved, cheap subset of the suite.

The public demo must never let a visitor pick an arbitrary ``task_id`` from
the full registry (30 tasks, some ``hard`` with big step budgets and long
timeouts): even though the visitor pays with THEIR OWN key, the demo server
holds the browser and the wall-clock slot, and an unexpectedly expensive run
would surprise the visitor's budget too. Cost transparency is non-negotiable.

The three tasks below are all ``easy``-difficulty, ≤ 8 ReAct steps and ≤ 90 s
wall-clock each — quick to complete, cheap in tokens, honest as a demo.
"""

from __future__ import annotations

from typing import Any

from agentalyze.tasks.models import Task
from agentalyze.tasks.registry import TASKS_BY_ID

#: The explicit allowlist. Adding a task here is a conscious product decision
#: (cheap + easy + stable fixture), NOT something reachable by passing an id.
DEMO_TASK_IDS: tuple[str, ...] = (
    "nav-simple-link-01",
    "form-fill-basic-01",
    "extract-price-01",
)


def get_demo_task(task_id: str) -> Task | None:
    """Return the task ONLY if it is on the demo allowlist; None otherwise."""
    if task_id not in DEMO_TASK_IDS:
        return None
    return TASKS_BY_ID.get(task_id)


def demo_tasks_payload() -> list[dict[str, Any]]:
    """Human-readable task descriptors for the demo page's task picker."""
    tasks: list[dict[str, Any]] = []
    for task_id in DEMO_TASK_IDS:
        task = TASKS_BY_ID[task_id]
        tasks.append(
            {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "difficulty": task.difficulty,
                "max_steps": task.max_steps,
                "timeout_seconds": task.timeout_seconds,
            }
        )
    return tasks
