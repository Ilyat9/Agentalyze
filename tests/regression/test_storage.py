"""Storage tests (Phase 6): baseline pointer + friendly suite-run loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentalyze.orchestration.suite_runner import save_suite_run
from agentalyze.regression.storage import (
    BASELINE_POINTER_FILENAME,
    BaselineNotSetError,
    SuiteRunNotFoundError,
    baseline_pointer_path,
    get_current_baseline,
    load_saved_suite_run,
    require_current_baseline,
    set_baseline,
)
from agentalyze.runner.trace import RunOutcome
from tests.regression.conftest import make_pair_traces, make_result


class TestBaselinePointer:
    def test_set_then_get_round_trips(self, tmp_path: Path) -> None:
        set_baseline(tmp_path, "suite-run-1234")

        assert get_current_baseline(tmp_path) == "suite-run-1234"
        assert require_current_baseline(tmp_path) == "suite-run-1234"

    def test_pointer_is_a_one_line_file_at_expected_path(self, tmp_path: Path) -> None:
        pointer = set_baseline(tmp_path, "abc")

        assert pointer == tmp_path / BASELINE_POINTER_FILENAME
        assert pointer == baseline_pointer_path(tmp_path)
        assert pointer.read_text(encoding="utf-8").strip() == "abc"

    def test_setting_again_overwrites(self, tmp_path: Path) -> None:
        set_baseline(tmp_path, "first")
        set_baseline(tmp_path, "second")

        assert get_current_baseline(tmp_path) == "second"

    def test_get_returns_none_when_never_set(self, tmp_path: Path) -> None:
        assert get_current_baseline(tmp_path) is None

    def test_require_raises_actionable_error_when_unset(self, tmp_path: Path) -> None:
        with pytest.raises(BaselineNotSetError) as excinfo:
            require_current_baseline(tmp_path)

        # The message must tell the user both escape hatches: an explicit
        # --baseline flag and the set-baseline command.
        message = str(excinfo.value)
        assert "--baseline" in message
        assert "set-baseline" in message

    def test_empty_id_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            set_baseline(tmp_path, "   ")
        assert get_current_baseline(tmp_path) is None


class TestLoadSavedSuiteRun:
    def _saved_result(self, results_dir: Path) -> str:
        baseline, _ = make_pair_traces(
            "task-01", baseline_outcome=RunOutcome.SUCCESS, new_outcome=None
        )
        result = make_result([baseline], "suite-xyz")
        save_suite_run(result, results_dir)
        return result.suite_run_id

    def test_loads_what_save_suite_run_wrote(self, tmp_path: Path) -> None:
        suite_run_id = self._saved_result(tmp_path)

        loaded = load_saved_suite_run(tmp_path, suite_run_id)
        assert loaded.suite_run_id == suite_run_id
        assert len(loaded.traces) == 1

    def test_missing_run_raises_friendly_error_not_bare_traceback(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(SuiteRunNotFoundError) as excinfo:
            load_saved_suite_run(tmp_path, "no-such-suite-run")

        message = str(excinfo.value)
        assert "no-such-suite-run" in message  # names the missing id...
        assert str(tmp_path) in message  # ...and where it looked...
        assert "compare" in message  # ...and what to do about it.
