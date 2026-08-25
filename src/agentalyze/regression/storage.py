"""Persistence for the regression mode (Phase 6).

Two responsibilities, both reusing the Phase 5 on-disk layout — no new
directory structure is invented here:

1. Loading a persisted :class:`SuiteRunResult` by ``suite_run_id`` from
   ``{results_dir}/{suite_run_id}/suite_run.json`` (exactly where
   ``save_suite_run`` writes it), converting a raw ``FileNotFoundError``
   into an actionable error message.

2. The baseline pointer. Which run is "the baseline" is a deliberate user
   decision (``agentalyze set-baseline``), never an automatic side effect
   of finishing a run. A one-line pointer file
   ``{results_dir}/current_baseline.txt`` was chosen over a ``Settings``
   field because: it lives with the results it refers to (different results
   dirs = different baselines, which a global config field cannot express),
   and updating it needs no Pydantic config round-trip.
"""

from __future__ import annotations

from pathlib import Path

from agentalyze.orchestration.suite_runner import (
    SuiteRunResult,
    load_suite_run,
)
from agentalyze.regression.diff import SuiteRegressionReport


class RegressionStorageError(Exception):
    """Base class for regression-storage problems with actionable messages."""


class SuiteRunNotFoundError(RegressionStorageError):
    """No ``{results_dir}/{suite_run_id}/suite_run.json`` on disk."""


class BaselineNotSetError(RegressionStorageError):
    """No baseline has been marked via `agentalyze set-baseline` yet."""


BASELINE_POINTER_FILENAME = "current_baseline.txt"


def baseline_pointer_path(results_dir: Path) -> Path:
    """Location of the one-line baseline pointer file."""
    return Path(results_dir) / BASELINE_POINTER_FILENAME


def set_baseline(results_dir: Path, suite_run_id: str) -> Path:
    """Mark ``suite_run_id`` as the current baseline; returns the pointer path.

    Deliberately explicit-only: nothing in the harness calls this after a
    run — only the ``set-baseline`` CLI command does.
    """
    cleaned = (suite_run_id or "").strip()
    if not cleaned:
        msg = "suite_run_id must be a non-empty identifier"
        raise ValueError(msg)
    path = baseline_pointer_path(results_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cleaned + "\n", encoding="utf-8")
    return path


def get_current_baseline(results_dir: Path) -> str | None:
    """Return the current baseline suite_run_id, or None when unset."""
    path = baseline_pointer_path(results_dir)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def require_current_baseline(results_dir: Path) -> str:
    """Return the current baseline id; raise :class:`BaselineNotSetError` if none."""
    value = get_current_baseline(results_dir)
    if value is None:
        msg = (
            f"no current baseline is set ({baseline_pointer_path(results_dir)} "
            "does not exist). Either pass --baseline <suite_run_id> explicitly, "
            "or first mark one with: agentalyze set-baseline --suite-run <suite_run_id>"
        )
        raise BaselineNotSetError(msg)
    return value


def load_saved_suite_run(results_dir: Path, suite_run_id: str) -> SuiteRunResult:
    """Load ``{results_dir}/{suite_run_id}/suite_run.json`` with a clear error.

    Thin wrapper over Phase 5's :func:`load_suite_run`; exists so CLI code
    can turn a missing directory into a friendly message instead of a bare
    ``FileNotFoundError`` traceback.
    """
    try:
        return load_suite_run(Path(results_dir), suite_run_id)
    except FileNotFoundError as exc:
        msg = (
            f"suite run {suite_run_id!r} not found under "
            f"{Path(results_dir)} (expected {suite_run_id}/suite_run.json). "
            "Run `agentalyze compare ...` first, or check the id with "
            "`ls <results-dir>`."
        )
        raise SuiteRunNotFoundError(msg) from exc


def save_regression_report(
    report: SuiteRegressionReport, results_dir: Path
) -> Path:
    """Persist the report next to the NEW run's artifacts (Phase 5 style).

    Saved as ``{results_dir}/{new_suite_run_id}/regression_report.json`` so
    everything about that comparison lives in the new run's directory.
    """
    path = (
        Path(results_dir)
        / report.new_suite_run_id
        / "regression_report.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path
