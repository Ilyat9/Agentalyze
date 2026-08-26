"""Persistence for the regression mode (Phase 6).

Two responsibilities, both reusing the Phase 5 on-disk layout — no new
directory structure is invented here:

1. Loading a persisted :class:`SuiteRunResult` by ``suite_run_id`` from
   ``{results_dir}/{suite_run_id}/suite_run.json`` (exactly where
   ``save_suite_run`` writes it), converting a raw ``FileNotFoundError``
   into an actionable error message.

2. The baseline pointer and the gate history. Which run is "the baseline" is
   by default a deliberate user decision (``agentalyze set-baseline``), never
   an automatic side effect of finishing a run. A one-line pointer file
   ``{results_dir}/current_baseline.txt`` was chosen over a ``Settings``
   field because: it lives with the results it refers to (different results
   dirs = different baselines, which a global config field cannot express),
   and updating it needs no Pydantic config round-trip.

   ON TOP of that, an OPT-IN convenience exists: ``regression-check --baseline
   auto`` picks the newest run whose recorded gate outcome was clean. To make
   that O(1) instead of re-diffing all runs on every call, every
   ``regression-check`` appends one line to the append-only journal
   ``{results_dir}/baseline_journal.jsonl`` recording whether that run was
   clean at its own comparison time. The journal records facts; it never
   moves the pointer by itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
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


class AutoBaselineNotFoundError(RegressionStorageError):
    """``--baseline auto`` found no clean gated run in the journal."""


BASELINE_POINTER_FILENAME = "current_baseline.txt"
#: Append-only gate history: one JSON object per line,
#: ``{"suite_run_id": ..., "timestamp": ..., "was_clean_at_promotion_time": ...}``.
#: Written by every ``regression-check`` invocation; read by ``--baseline auto``.
BASELINE_JOURNAL_FILENAME = "baseline_journal.jsonl"


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


def baseline_journal_path(results_dir: Path) -> Path:
    """Location of the append-only gate-outcome journal."""
    return Path(results_dir) / BASELINE_JOURNAL_FILENAME


def record_gate_outcome(results_dir: Path, suite_run_id: str, *, was_clean: bool) -> Path:
    """Append one gate outcome to the linear journal; returns the journal path.

    Called by ``regression-check`` after every comparison. The entry records
    the FACT of that moment: ``was_clean=True`` means the run passed its gate
    (``regressed_count == 0`` against the baseline used at that time). Nothing
    is ever recomputed retroactively -- ``--baseline auto`` reads this journal
    instead of re-diffing history, which would be O(runs) file parsing per
    invocation.
    """
    path = baseline_journal_path(results_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "suite_run_id": suite_run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "was_clean_at_promotion_time": was_clean,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def find_last_clean_baseline(results_dir: Path) -> str:
    """Return the newest clean-gated suite_run_id from the journal.

    Iterates the journal newest-first and returns the first clean entry whose
    result directory still exists on disk (deleted runs are skipped rather
    than selected). Raises :class:`AutoBaselineNotFoundError` when there is no
    usable candidate -- e.g. the very first runs of a fresh project.
    """
    path = baseline_journal_path(results_dir)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        msg = (
            f"--baseline auto found no gate history ({path} does not exist). "
            "Run `agentalyze regression-check` at least once first, or mark a "
            "baseline explicitly with `agentalyze set-baseline --suite-run <id>`."
        )
        raise AutoBaselineNotFoundError(msg) from exc

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn/corrupt trailing line must not break auto mode
        if not entry.get("was_clean_at_promotion_time"):
            continue
        suite_run_id = str(entry.get("suite_run_id", "")).strip()
        if suite_run_id and (Path(results_dir) / suite_run_id / "suite_run.json").exists():
            return suite_run_id

    msg = (
        f"--baseline auto found no clean gated run in {path}: every recorded "
        "comparison regressed (or the clean runs were deleted). Mark a baseline "
        "explicitly with `agentalyze set-baseline --suite-run <id>`."
    )
    raise AutoBaselineNotFoundError(msg)


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
