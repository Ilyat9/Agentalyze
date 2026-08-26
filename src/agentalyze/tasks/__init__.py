"""Task suite: declarative web-task descriptions, local fixtures, verifiers.

This subpackage is the "exam", not the "examinee": it contains task
descriptions, local HTML fixtures and programmatic success verifiers.
No agent / LLM code lives here (that arrives in Phase 3).
"""

from typing import TYPE_CHECKING, Any

from agentalyze.tasks.models import Task, TaskCategory, VerificationResult

if TYPE_CHECKING:
    from agentalyze.tasks.registry import TASKS

__all__ = ["TASKS", "Task", "TaskCategory", "VerificationResult"]


def __getattr__(name: str) -> Any:
    # ``TASKS`` is exposed lazily on purpose: runner.trace imports this package
    # (via agentalyze.tasks.models), and an eager registry import here would
    # close the import cycle registry -> failure_taxonomy -> runner.trace while
    # runner.trace is still initializing. Everyone in-tree imports TASKS from
    # agentalyze.tasks.registry directly; this hook only preserves the
    # historical ``from agentalyze.tasks import TASKS`` spelling.
    if name == "TASKS":
        from agentalyze.tasks.registry import TASKS as _TASKS

        return _TASKS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
