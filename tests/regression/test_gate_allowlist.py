"""Per-task gate exclusions via the optional regression.yaml allowlist.

Covers both layers of the feature:

* pure diffing — an excluded task's REGRESSED pair stays VISIBLE but does not
  count towards regressed_count / net_change / provider breakdown;
* the CLI gate — with only excluded regressions, `regression-check` exits 0
  and marks the pairs "excluded from gate"; without a config file, behaviour
  is byte-for-byte the old one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentalyze.regression.config import RegressionConfigError, load_regression_config
from agentalyze.regression.diff import TaskDiffStatus, compute_regression
from agentalyze.runner.cli import main
from agentalyze.runner.trace import RunOutcome
from tests.regression.conftest import PROVIDER_A, make_pair_traces, make_result

NOISY = "err-flaky-widget-03"
SOLID = "nav-simple-link-01"


def _two_regressions() -> tuple[object, object]:
    """Baseline: both tasks pass. New: the noisy AND the solid task broke."""
    noisy_b, noisy_n = make_pair_traces(
        NOISY, baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.FAILURE_VERIFIER
    )
    solid_b, solid_n = make_pair_traces(
        SOLID, baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.FAILURE_TIMEOUT
    )
    return (
        make_result([noisy_b, solid_b], "b"),
        make_result([noisy_n, solid_n], "n"),
    )


class TestAllowlistDiffing:
    def test_excluded_regression_is_visible_but_not_counted(self) -> None:
        baseline, new = _two_regressions()
        report = compute_regression(
            baseline, new, excluded_task_ids=frozenset({NOISY})
        )

        by_task = {d.task_id: d for d in report.diffs}
        # The excluded task still shows up as an explicit REGRESSED diff...
        assert by_task[NOISY].status is TaskDiffStatus.REGRESSED
        assert by_task[NOISY].gate_excluded is True
        # ...but only the SOLID regression counts for the gate:
        assert report.regressed_count == 1
        assert report.net_change == -1
        assert report.provider_summary[PROVIDER_A].regressed_count == 1

    def test_without_allowlist_behaviour_is_unchanged(self) -> None:
        baseline, new = _two_regressions()
        report_default = compute_regression(baseline, new)
        report_empty = compute_regression(baseline, new, excluded_task_ids=frozenset())

        assert report_default == report_empty
        assert report_default.regressed_count == 2
        assert all(not d.gate_excluded for d in report_default.diffs)

    def test_fixed_pairs_are_never_affected_by_the_allowlist(self) -> None:
        base_b, new_b = make_pair_traces(
            NOISY, baseline_outcome=RunOutcome.FAILURE_CRASH, new_outcome=RunOutcome.SUCCESS
        )
        report = compute_regression(
            make_result([base_b], "b"), make_result([new_b], "n"),
            excluded_task_ids=frozenset({NOISY}),
        )

        diff = report.diffs[0]
        assert diff.status is TaskDiffStatus.FIXED
        assert diff.gate_excluded is False  # exclusion waives regressions only
        assert report.fixed_count == 1


class TestRegressionConfigLoading:
    def test_missing_file_means_no_exclusions(self, tmp_path: Path) -> None:
        config = load_regression_config(tmp_path / "absent.yaml")
        assert config.excluded_from_gate == []
        assert config.excluded_task_ids() == frozenset()

    def test_string_and_mapping_entries_both_parse(self, tmp_path: Path) -> None:
        path = tmp_path / "regression.yaml"
        path.write_text(
            "excluded_from_gate:\n"
            "  - task_id: err-flaky-widget-03\n"
            "    reason: verifier flakes\n"
            "  - nav-tabs-secret-03\n",
            encoding="utf-8",
        )
        config = load_regression_config(path)
        assert config.excluded_task_ids() == frozenset(
            {"err-flaky-widget-03", "nav-tabs-secret-03"}
        )

    def test_duplicate_ids_are_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "regression.yaml"
        path.write_text(
            "excluded_from_gate:\n"
            "  - task_id: err-flaky-widget-03\n"
            "  - err-flaky-widget-03\n",
            encoding="utf-8",
        )
        with pytest.raises(RegressionConfigError, match="duplicate"):
            load_regression_config(path).excluded_task_ids()

    def test_invalid_yaml_reports_file_and_reason(self, tmp_path: Path) -> None:
        path = tmp_path / "regression.yaml"
        path.write_text("excluded_from_gate: [unclosed", encoding="utf-8")
        with pytest.raises(RegressionConfigError, match="regression.yaml"):
            load_regression_config(path)


class TestGateExitCodeWithAllowlist:
    def test_only_excluded_regressions_open_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        from tests.regression.conftest import save_pair_for_cli

        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (tmp_path / "regression.yaml").write_text(
            "excluded_from_gate:\n  - task_id: err-flaky-widget-03\n"
            "    reason: verifier flakes\n",
            encoding="utf-8",
        )
        noisy_b, noisy_n = make_pair_traces(
            NOISY, baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.FAILURE_VERIFIER
        )
        baseline_id, new_id = save_pair_for_cli(results_dir, [noisy_b], [noisy_n])
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(results_dir))

        code = main([
            "regression-check", "--baseline", baseline_id, "--new", new_id,
            "--regression-config", str(tmp_path / "regression.yaml"),
        ])

        assert code == 0  # THE new gate semantics: allowlisted noise cannot fail CI
        out = capsys.readouterr().out
        assert "RESULT: OK" in out
        assert NOISY in out                      # nothing hidden from the human
        assert "[excluded from gate]" in out     # explicit waiver marker

    def test_unlisted_regression_still_fails_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.regression.conftest import save_pair_for_cli

        results_dir = tmp_path / "results"
        results_dir.mkdir()
        solid_b, solid_n = make_pair_traces(
            SOLID, baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.FAILURE_TIMEOUT
        )
        baseline_id, new_id = save_pair_for_cli(results_dir, [solid_b], [solid_n])
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(results_dir))

        code = main([
            "regression-check", "--baseline", baseline_id, "--new", new_id,
            "--regression-config", str(tmp_path / "regression.yaml"),  # absent file
        ])

        assert code == 1  # backward compat: no config -> old strict behaviour