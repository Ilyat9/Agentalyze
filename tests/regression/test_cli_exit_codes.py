"""THE critical Phase 6 test: the numeric exit codes of `regression-check`.

The CI-gate promise ("the command fails when tasks got worse") is only real
if the actual returned code is checked, not just "no exception was raised".
These tests call the CLI entry point (`agentalyze.runner.cli.main`, exactly
what the console script wraps in sys.exit) on hand-built datasets and pin:

    regressed dataset               -> exit code 1
    clean dataset                   -> exit code 0
    regressed + --allow-regressions -> exit code 0
    config problems                 -> exit code 2
"""

from __future__ import annotations

from pathlib import Path

from agentalyze.runner.cli import main
from agentalyze.runner.trace import RunOutcome
from tests.regression.conftest import make_pair_traces


def _setup_results_dir(tmp_path: Path) -> Path:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    return results_dir


def _regressed_pair(results_dir: Path) -> tuple[str, str]:
    """Baseline: both tasks pass. New: one task broke."""
    from tests.regression.conftest import save_pair_for_cli

    ok_b, ok_n = make_pair_traces(
        "task-ok", baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.SUCCESS
    )
    broke_b, broke_n = make_pair_traces(
        "task-broke", baseline_outcome=RunOutcome.SUCCESS,
        new_outcome=RunOutcome.FAILURE_VERIFIER,
    )
    return save_pair_for_cli(results_dir, [ok_b, broke_b], [ok_n, broke_n])


def _clean_pair(results_dir: Path) -> tuple[str, str]:
    """Same outcomes on both sides — including a task failing BOTH times."""
    from tests.regression.conftest import save_pair_for_cli

    ok_b, ok_n = make_pair_traces(
        "task-ok", baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.SUCCESS
    )
    broken_b, broken_n = make_pair_traces(
        "task-broken-both-times",
        baseline_outcome=RunOutcome.FAILURE_MAX_STEPS,
        new_outcome=RunOutcome.FAILURE_TIMEOUT,
    )
    return save_pair_for_cli(results_dir, [ok_b, broken_b], [ok_n, broken_n])


class TestRegressionCheckExitCodes:
    def test_regressions_cause_exit_code_1(self, tmp_path, monkeypatch, capsys) -> None:
        results_dir = _setup_results_dir(tmp_path)
        baseline_id, new_id = _regressed_pair(results_dir)
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(results_dir))

        code = main(["regression-check", "--baseline", baseline_id, "--new", new_id])

        assert code == 1  # THE CI gate: a PR with a broken task must go red.
        out = capsys.readouterr().out
        assert "RESULT: FAIL" in out
        assert "task-broke" in out  # names the exact broken pair
        # The full machine-readable report landed next to the NEW run's artifacts.
        assert (results_dir / new_id / "regression_report.json").exists()

    def test_no_regressions_exit_code_0(self, tmp_path, monkeypatch) -> None:
        results_dir = _setup_results_dir(tmp_path)
        baseline_id, new_id = _clean_pair(results_dir)
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(results_dir))

        code = main(["regression-check", "--baseline", baseline_id, "--new", new_id])

        assert code == 0  # failures that existed before are NOT regressions.

    def test_allow_regressions_downgrades_exit_1_to_0(self, tmp_path, monkeypatch,
                                                      capsys) -> None:
        results_dir = _setup_results_dir(tmp_path)
        baseline_id, new_id = _regressed_pair(results_dir)
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(results_dir))

        code = main([
            "regression-check",
            "--baseline", baseline_id,
            "--new", new_id,
            "--allow-regressions",
        ])

        assert code == 0  # informational mode: regressions reported, gate open.
        out = capsys.readouterr().out
        assert "RESULT:" in out and "allow-regressions" in out


class TestBaselinePointerFlowThroughCli:
    def test_set_baseline_then_check_without_explicit_baseline(
        self, tmp_path, monkeypatch
    ) -> None:
        results_dir = _setup_results_dir(tmp_path)
        baseline_id, new_id = _clean_pair(results_dir)
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(results_dir))

        assert main(["set-baseline", "--suite-run", baseline_id]) == 0
        # No --baseline given: the pointer file must be picked up.
        assert main(["regression-check", "--new", new_id]) == 0

    def test_check_without_baseline_and_without_pointer_is_a_clean_error(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        results_dir = _setup_results_dir(tmp_path)
        _, new_id = _clean_pair(results_dir)
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(results_dir))
        assert not (results_dir / "current_baseline.txt").exists()

        code = main(["regression-check", "--new", new_id])

        assert code == 2  # usage problem, not a crash and not a false regression.
        err = capsys.readouterr().err
        assert "set-baseline" in err and "--baseline" in err

    def test_unknown_run_ids_are_a_clean_error(self, tmp_path, monkeypatch,
                                               capsys) -> None:
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR",
                           str(_setup_results_dir(tmp_path)))

        code = main([
            "regression-check",
            "--baseline", "ghost-run", "--new", "phantom-run",
        ])

        assert code == 2
        assert "not found" in capsys.readouterr().err

    def test_set_baseline_for_unknown_run_is_a_clean_error(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(_setup_results_dir(tmp_path)))

        code = main(["set-baseline", "--suite-run", "never-existed"])

        assert code == 2
        assert "not found" in capsys.readouterr().err
