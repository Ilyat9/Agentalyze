"""Task suite: declarative web-task descriptions, local fixtures, verifiers.

This subpackage is the "exam", not the "examinee": it contains task
descriptions, local HTML fixtures and programmatic success verifiers.
No agent / LLM code lives here (that arrives in Phase 3).
"""

from agentalyze.tasks.models import Task, TaskCategory, VerificationResult
from agentalyze.tasks.registry import TASKS

__all__ = ["TASKS", "Task", "TaskCategory", "VerificationResult"]
