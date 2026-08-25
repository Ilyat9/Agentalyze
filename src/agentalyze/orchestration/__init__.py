"""Orchestration (Phase 5): running the WHOLE suite across SEVERAL providers
and presenting honest side-by-side reports.

Modules:

* ``suite_runner`` — sequential (task x provider) execution, incremental
  persistence of the ``SuiteRunResult``, progress reporting;
* ``report``       — Markdown rendering of a finished ``SuiteRunResult``,
  including the programmatically computed honest conclusion;
* ``cli``          — ``agentalyze compare`` / ``agentalyze inspect``
  handlers, registered as subcommands of the single Phase 3 entry point.

What this phase deliberately does NOT contain: parallel execution
(``max_concurrent > 1`` is rejected loudly, not silently coerced to 1),
regression diffs between runs (Phase 6), HTML/PDF output.
"""

from __future__ import annotations

from agentalyze.orchestration.report import (
    build_honest_conclusion,
    generate_report,
    render_report,
)
from agentalyze.orchestration.suite_runner import (
    SuiteRunConfig,
    SuiteRunResult,
    load_suite_run,
    run_suite,
    save_suite_run,
)

__all__ = [
    "SuiteRunConfig",
    "SuiteRunResult",
    "build_honest_conclusion",
    "generate_report",
    "load_suite_run",
    "render_report",
    "run_suite",
    "save_suite_run",
]
