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
from agentalyze.tasks.verifiers import AGENT_VERDICT_ID

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


def get_allowed_demo_task_ids() -> tuple[str, ...]:
    """The allowlist, for error messages (never the full registry)."""
    return DEMO_TASK_IDS


#: Sentinel task_id for a visitor-WRITTEN task (free-form instructions).
CUSTOM_TASK_ID = "custom"
#: Hard cap on custom instructions — they go verbatim into the agent prompt.
CUSTOM_INSTRUCTIONS_MAX_CHARS = 500
#: The custom task runs in the SAME cheap sandbox as the allowlisted tasks:
#: the Acme portal start page (its links/forms are the visitor's playground).
_CUSTOM_SANDBOX_TASK_ID = "nav-simple-link-01"
#: Custom-task budgets are FIXED BY THE SERVER, not by the visitor: identical
#: cost ceiling to the most expensive allowlisted demo task.
CUSTOM_TASK_MAX_STEPS = 8
CUSTOM_TASK_TIMEOUT_SECONDS = 90


def build_custom_task(instructions: str) -> Task:
    """Build the visitor-written task: their goal, the server's budgets.

    Cost model is identical to the allowlist (≤ 8 steps, ≤ 90 s): the visitor
    controls only the *goal text*, never the budget, the sandbox page or the
    verifier. Success is the agent's own done(true) verdict recorded in the
    page DOM (``verify-agent-verdict``) — an arbitrary goal has no objective
    DOM marker, and the demo reports this honestly as self-reported.
    """
    base = TASKS_BY_ID[_CUSTOM_SANDBOX_TASK_ID]
    cleaned = " ".join(instructions.split())[:CUSTOM_INSTRUCTIONS_MAX_CHARS]
    return base.model_copy(
        update={
            "id": CUSTOM_TASK_ID,
            "title": cleaned[:80],
            "description": cleaned,
            "verifier_id": AGENT_VERDICT_ID,
            "max_steps": CUSTOM_TASK_MAX_STEPS,
            "timeout_seconds": CUSTOM_TASK_TIMEOUT_SECONDS,
            "tags": ["custom"],
        }
    )


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
