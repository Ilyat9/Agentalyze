"""Regression mode (Phase 6): diffing two suite runs over time.

This package is the only part of Agentalyze designed primarily for CI/CD
embedding rather than one-off manual analysis: given a baseline suite run
("yesterday's green state") and a new suite run ("after my prompt change"),
it reports exactly which task/provider pairs got worse, got better, appeared
or disappeared — and exits non-zero when regressions exist so a CI job can
act as a gate.

Nothing here re-runs tasks: ``run_task``/``run_suite`` (Phases 3/5) are used
as-is, this layer only reads two already-persisted :class:`SuiteRunResult`
objects.
"""
