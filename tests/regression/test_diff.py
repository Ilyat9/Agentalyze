"""compute_regression unit tests (Phase 6): one test per TaskDiffStatus.

All six statuses are covered explicitly — including the easily-forgotten
NEWLY_ADDED / REMOVED cases. Delta assertions use hand-computed numbers
(written out in the comments) rather than re-deriving them from the code.
"""

from __future__ import annotations

import pytest

from agentalyze.regression.diff import (
    SuiteRegressionReport,
    TaskDiffStatus,
    compute_regression,
)
from agentalyze.runner.trace import RunOutcome
from tests.regression.conftest import PROVIDER_A, PROVIDER_B, make_pair_traces, make_result

TASK = "nav-simple-link-01"


def _diff_of(report):
    assert len(report.diffs) == 1, "single-pair scenario must yield exactly one diff"
    return report.diffs[0]


class TestEachStatus:
    def test_still_passing(self) -> None:
        baseline, new = make_pair_traces(
            TASK, baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.SUCCESS
        )
        report = compute_regression(make_result([baseline], "b"), make_result([new], "n"))

        diff = _diff_of(report)
        assert diff.status is TaskDiffStatus.STILL_PASSING
        assert diff.baseline_outcome is RunOutcome.SUCCESS
        assert diff.new_outcome is RunOutcome.SUCCESS
        assert report.regressed_count == 0 and report.fixed_count == 0
        assert report.net_change == 0

    def test_still_failing_across_different_failure_kinds(self) -> None:
        # Any failure kind on both sides counts: switching HOW it fails is not
        # a regression and not a fix.
        baseline, new = make_pair_traces(
            TASK,
            baseline_outcome=RunOutcome.FAILURE_VERIFIER,
            new_outcome=RunOutcome.FAILURE_MAX_STEPS,
        )
        report = compute_regression(make_result([baseline], "b"), make_result([new], "n"))

        diff = _diff_of(report)
        assert diff.status is TaskDiffStatus.STILL_FAILING
        assert diff.baseline_outcome is RunOutcome.FAILURE_VERIFIER
        assert diff.new_outcome is RunOutcome.FAILURE_MAX_STEPS
        assert report.regressed_count == 0 and report.fixed_count == 0

    def test_regressed(self) -> None:
        baseline, new = make_pair_traces(
            TASK, baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.FAILURE_TIMEOUT
        )
        report = compute_regression(make_result([baseline], "b"), make_result([new], "n"))

        diff = _diff_of(report)
        assert diff.status is TaskDiffStatus.REGRESSED
        assert report.regressed_count == 1
        assert report.fixed_count == 0
        assert report.net_change == -1

    def test_fixed(self) -> None:
        baseline, new = make_pair_traces(
            TASK,
            baseline_outcome=RunOutcome.FAILURE_PROVIDER_ERROR,
            new_outcome=RunOutcome.SUCCESS,
        )
        report = compute_regression(make_result([baseline], "b"), make_result([new], "n"))

        diff = _diff_of(report)
        assert diff.status is TaskDiffStatus.FIXED
        assert report.fixed_count == 1
        assert report.regressed_count == 0
        assert report.net_change == +1

    def test_newly_added(self) -> None:
        # The task exists only in the NEW run (suite grew between runs):
        # an explicit status, never a silent skip and never a fake regression.
        _, new = make_pair_traces(
            TASK, baseline_outcome=None, new_outcome=RunOutcome.SUCCESS
        )
        report = compute_regression(
            make_result([], "b", providers=[PROVIDER_A]),  # baseline never saw the task
            make_result([new], "n"),
        )

        diff = _diff_of(report)
        assert diff.status is TaskDiffStatus.NEWLY_ADDED
        assert diff.baseline_outcome is None
        assert diff.baseline_run_id is None
        assert diff.new_run_id == new.run_id
        # A brand-new pair has no "before" values to subtract from.
        assert diff.cost_delta_usd is None
        assert diff.steps_delta is None
        assert diff.latency_delta_seconds is None
        assert report.regressed_count == 0  # must NOT count as a regression

    def test_removed(self) -> None:
        # The task was in the baseline but was dropped from the suite.
        baseline, _ = make_pair_traces(
            TASK, baseline_outcome=RunOutcome.SUCCESS, new_outcome=None
        )
        report = compute_regression(
            make_result([baseline], "b"),
            make_result([], "n", providers=[PROVIDER_A]),  # new run dropped it
        )

        diff = _diff_of(report)
        assert diff.status is TaskDiffStatus.REMOVED
        assert diff.baseline_run_id == baseline.run_id
        assert diff.new_outcome is None
        assert diff.steps_delta is None
        assert report.regressed_count == 0


class TestDeltas:
    def test_deltas_are_exact_differences(self) -> None:
        # Hand-computed expectations:
        #   cost:    0.0031 - 0.0020 = +0.0011 USD
        #   steps:   5 - 3          = +2
        #   latency: 12.5 - 10.0     = +2.5 s
        baseline, new = make_pair_traces(
            TASK,
            baseline_outcome=RunOutcome.SUCCESS,
            new_outcome=RunOutcome.SUCCESS,
            baseline_steps=3,
            new_steps=5,
            baseline_cost_usd=0.0020,
            new_cost_usd=0.0031,
            baseline_latency_seconds=10.0,
            new_latency_seconds=12.5,
        )
        report = compute_regression(make_result([baseline], "b"), make_result([new], "n"))

        diff = _diff_of(report)
        assert diff.cost_delta_usd == pytest.approx(0.0011)
        assert diff.steps_delta == 2
        assert diff.latency_delta_seconds == pytest.approx(2.5)

    def test_negative_deltas(self) -> None:
        # Hand-computed: cost 0.0010 - 0.0040 = -0.0030; steps 2 - 6 = -4;
        # latency 8.0 - 20.0 = -12.0.
        baseline, new = make_pair_traces(
            TASK,
            baseline_outcome=RunOutcome.SUCCESS,
            new_outcome=RunOutcome.SUCCESS,
            baseline_steps=6,
            new_steps=2,
            baseline_cost_usd=0.0040,
            new_cost_usd=0.0010,
            baseline_latency_seconds=20.0,
            new_latency_seconds=8.0,
        )
        report = compute_regression(make_result([baseline], "b"), make_result([new], "n"))

        diff = _diff_of(report)
        assert diff.cost_delta_usd == pytest.approx(-0.0030)
        assert diff.steps_delta == -4
        assert diff.latency_delta_seconds == pytest.approx(-12.0)

    def test_cost_delta_is_none_unless_both_sides_have_pricing(self) -> None:
        # The harness never invents numbers: one side unpriced => no delta.
        baseline, new = make_pair_traces(
            TASK,
            baseline_outcome=RunOutcome.SUCCESS,
            new_outcome=RunOutcome.SUCCESS,
            baseline_cost_usd=None,
            new_cost_usd=0.0020,
        )
        report = compute_regression(make_result([baseline], "b"), make_result([new], "n"))

        diff = _diff_of(report)
        assert diff.cost_delta_usd is None
        # Steps/latency are always known, so their deltas still exist.
        assert diff.steps_delta == 0


class TestProviderMismatchAndAggregates:
    def test_providers_present_in_only_one_run_are_reported_not_compared(self) -> None:
        base_a, new_a = make_pair_traces(
            TASK, baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.SUCCESS
        )
        base_x, _ = make_pair_traces(
            TASK, baseline_outcome=RunOutcome.SUCCESS, new_outcome=None,
            provider_name="only-baseline",
        )
        _, new_y = make_pair_traces(
            TASK, baseline_outcome=None, new_outcome=RunOutcome.SUCCESS,
            provider_name="only-new",
        )
        report = compute_regression(
            make_result([base_a, base_x], "b"),
            make_result([new_a, new_y], "n"),
        )

        assert report.providers_only_in_baseline == ["only-baseline"]
        assert report.providers_only_in_new == ["only-new"]
        # Only the common provider's pair was compared:
        assert len(report.diffs) == 1
        assert all(d.provider_name == PROVIDER_A for d in report.diffs)

    def test_counts_and_net_change_over_mixed_scenario(self) -> None:
        regressed_b, regressed_n = make_pair_traces(
            "task-regressed",
            baseline_outcome=RunOutcome.SUCCESS,
            new_outcome=RunOutcome.FAILURE_TIMEOUT,
        )
        fixed_b, fixed_n = make_pair_traces(
            "task-fixed",
            baseline_outcome=RunOutcome.FAILURE_CRASH,
            new_outcome=RunOutcome.SUCCESS,
        )
        calm_b, calm_n = make_pair_traces(
            "task-calm",
            baseline_outcome=RunOutcome.SUCCESS,
            new_outcome=RunOutcome.SUCCESS,
        )
        report = compute_regression(
            make_result([regressed_b, fixed_b, calm_b], "b"),
            make_result([regressed_n, fixed_n, calm_n], "n"),
        )

        assert report.regressed_count == 1
        assert report.fixed_count == 1
        assert report.net_change == 0  # 1 fixed - 1 regressed
        summary = report.provider_summary[PROVIDER_A]
        assert summary.compared_pairs == 3
        assert summary.regressed_count == 1 and summary.fixed_count == 1
        assert summary.still_passing_count == 1
        assert set(report.provider_summary) == {PROVIDER_A}

    def test_per_provider_summary_when_two_common_providers(self) -> None:
        pairs = [
            make_pair_traces(
                "task-x", baseline_outcome=RunOutcome.SUCCESS,
                new_outcome=RunOutcome.SUCCESS, provider_name=name,
            )
            for name in (PROVIDER_A, PROVIDER_B)
        ]
        report = compute_regression(
            make_result([p[0] for p in pairs], "b"),
            make_result([p[1] for p in pairs], "n"),
        )

        assert sorted(report.provider_summary) == sorted([PROVIDER_A, PROVIDER_B])
        assert all(s.compared_pairs == 1 for s in report.provider_summary.values())

    def test_duplicate_pairs_within_one_run_are_rejected(self) -> None:
        base_1, new_trace = make_pair_traces(
            TASK, baseline_outcome=RunOutcome.SUCCESS, new_outcome=RunOutcome.SUCCESS
        )
        base_2, _ = make_pair_traces(  # same (task, provider) key as base_1
            TASK, baseline_outcome=RunOutcome.FAILURE_TIMEOUT,
            new_outcome=RunOutcome.SUCCESS,
        )
        with pytest.raises(ValueError, match="duplicate"):
            compute_regression(
                make_result([base_1, base_2], "b"), make_result([new_trace], "n")
            )

    def test_report_round_trips_through_json(self) -> None:
        baseline, new = make_pair_traces(
            TASK,
            baseline_outcome=RunOutcome.SUCCESS,
            new_outcome=RunOutcome.FAILURE_TIMEOUT,
        )
        report = compute_regression(make_result([baseline], "b"), make_result([new], "n"))
        restored = SuiteRegressionReport.model_validate_json(report.model_dump_json())

        assert restored == report
