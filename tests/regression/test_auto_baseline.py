"""Tests for the opt-in ``--baseline auto`` mode (gate-outcome journal).

The journal is append-only and records FACTS at comparison time;
``find_last_clean_baseline`` reads it newest-first. The manual
``set-baseline`` pointer remains untouched by any of this.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentalyze.orchestration.suite_runner import save_suite_run
from agentalyze.regression.storage import (
    BASELINE_JOURNAL_FILENAME,
    AutoBaselineNotFoundError,
    find_last_clean_baseline,
    get_current_baseline,
    record_gate_outcome,
)
from agentalyze.runner.cli import main
from agentalyze.runner.trace import RunOutcome
from tests.regression.conftest import make_pair_traces, make_result


def _save_run(results_dir: Path, suite_run_id: str, *, outcome: RunOutcome) -> str:
    """Persist a one-pair suite run under an explicit id; returns the id."""
    _, trace = make_pair_traces(
        "task-01",
        baseline_outcome=RunOutcome.SUCCESS,
        new_outcome=outcome,
    )
    assert trace is not None
    result = make_result([trace], suite_run_id)
    save_suite_run(result, results_dir)
    return result.suite_run_id


def _write_journal(results_dir: Path, entries: list[tuple[str, bool]]) -> None:
    lines = [
        json.dumps({
            "suite_run_id": run_id,
            "timestamp": f"2026-08-26T0{i}:00:00+00:00",
            "was_clean_at_promotion_time": was_clean,
        })
        for i, (run_id, was_clean) in enumerate(entries)
    ]
    (results_dir / BASELINE_JOURNAL_FILENAME).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


class TestJournalRoundTrip:
    def test_record_appends_lines_preserving_order(self, tmp_path: Path) -> None:
        record_gate_outcome(tmp_path, "run-a", was_clean=True)
        record_gate_outcome(tmp_path, "run-b", was_clean=False)
        record_gate_outcome(tmp_path, "run-c", was_clean=True)

        lines = (tmp_path / BASELINE_JOURNAL_FILENAME).read_text(
            encoding="utf-8"
        ).splitlines()
        assert [json.loads(line)["suite_run_id"] for line in lines] == [
            "run-a", "run-b", "run-c",
        ]
        assert [json.loads(line)["was_clean_at_promotion_time"] for line in lines] == [
            True, False, True,
        ]

    def test_pointer_file_is_untouched_by_the_journal(self, tmp_path: Path) -> None:
        record_gate_outcome(tmp_path, "run-a", was_clean=True)
        assert get_current_baseline(tmp_path) is None  # auto never promotes


# --- find_last_clean_baseline tests appended below ---


class TestFindLastCleanBaseline:
    def test_picks_the_newest_CLEAN_not_simply_the_newest(self, tmp_path: Path) -> None:
        """The core requirement: a dirty run later in history must not win."""
        _save_run(tmp_path, "clean-old", outcome=RunOutcome.SUCCESS)
        _save_run(tmp_path, "dirty-new", outcome=RunOutcome.FAILURE_VERIFIER)
        _write_journal(tmp_path, [("clean-old", True), ("dirty-new", False)])

        assert find_last_clean_baseline(tmp_path) == "clean-old"

    def test_latest_clean_wins_when_several_are_clean(self, tmp_path: Path) -> None:
        _save_run(tmp_path, "clean-1", outcome=RunOutcome.SUCCESS)
        _save_run(tmp_path, "clean-2", outcome=RunOutcome.SUCCESS)
        _write_journal(tmp_path, [("clean-1", True), ("clean-2", True)])

        assert find_last_clean_baseline(tmp_path) == "clean-2"

    def test_missing_journal_is_an_actionable_error(self, tmp_path: Path) -> None:
        with pytest.raises(AutoBaselineNotFoundError, match="no gate history"):
            find_last_clean_baseline(tmp_path)

    def test_no_clean_entries_at_all_is_an_actionable_error(
        self, tmp_path: Path
    ) -> None:
        _save_run(tmp_path, "only-dirty", outcome=RunOutcome.FAILURE_MAX_STEPS)
        _write_journal(tmp_path, [("only-dirty", False)])

        with pytest.raises(AutoBaselineNotFoundError, match="no clean gated run"):
            find_last_clean_baseline(tmp_path)

    def test_deleted_clean_runs_are_skipped_for_existing_ones(
        self, tmp_path: Path
    ) -> None:
        _save_run(tmp_path, "clean-survivor", outcome=RunOutcome.SUCCESS)
        # NOTE clean-deleted was never saved to disk in this test.
        _write_journal(tmp_path, [("clean-deleted", True), ("clean-survivor", True)])

        assert find_last_clean_baseline(tmp_path) == "clean-survivor"

    def test_torn_trailing_line_does_not_break_the_search(self, tmp_path: Path) -> None:
        _save_run(tmp_path, "clean-run", outcome=RunOutcome.SUCCESS)
        _write_journal(tmp_path, [("clean-run", True)])
        with (tmp_path / BASELINE_JOURNAL_FILENAME).open("a", encoding="utf-8") as fh:
            fh.write('{"suite_run_id": "torn')  # simulated crash mid-write

        assert find_last_clean_baseline(tmp_path) == "clean-run"


class TestAutoBaselineCLI:
    @pytest.fixture
    def chained_history(self, tmp_path: Path) -> Path:
        """Directory where three REAL comparisons will be executed in order:
        suite-r1(clean), suite-r2(regressed), suite-r3(clean)."""
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        _save_run(results_dir, "suite-seed", outcome=RunOutcome.SUCCESS)
        _save_run(results_dir, "suite-r1", outcome=RunOutcome.SUCCESS)
        _save_run(results_dir, "suite-r2", outcome=RunOutcome.FAILURE_VERIFIER)
        _save_run(results_dir, "suite-r3", outcome=RunOutcome.SUCCESS)
        return results_dir

    @staticmethod
    def _check(results_dir: Path, baseline: str | None, new: str) -> int:
        argv = ["regression-check", "--new", new, "--results-dir", str(results_dir)]
        if baseline is not None:
            argv[1:1] = ["--baseline", baseline]
        return main(argv)

    def test_auto_resolves_through_a_mixed_chain(
        self, chained_history: Path, capsys
    ) -> None:
        # Build the journal by actually running the gate three times.
        assert self._check(chained_history, "suite-seed", "suite-r1") == 0  # clean
        assert self._check(chained_history, "suite-seed", "suite-r2") == 1  # regressed
        assert self._check(chained_history, "suite-seed", "suite-r3") == 0  # clean

        # A fourth comparison against AUTO must pick suite-r3 (last CLEAN),
        # not suite-r2 (simply last).
        code = self._check(chained_history, "auto", "suite-r3")
        assert code == 0
        out = capsys.readouterr().out
        assert "Auto baseline resolved to suite run suite-r3" in out

    def test_auto_with_empty_history_exits_2_without_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.chdir(tmp_path)
        results_dir = tmp_path / "results"
        results_dir.mkdir()

        code = self._check(results_dir, "auto", "whatever")

        assert code == 2  # usage/configuration problem, NOT a crash
        err = capsys.readouterr().err
        assert "--baseline auto found no gate history" in err
