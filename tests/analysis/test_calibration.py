"""Calibration tests, including a hand-computed ECE with a known answer.

Hand-computed reference example (10 runs):
    - 5 runs claim confidence=0.9; 4 of them succeed  (acc = 4/5 = 0.8)
    - 5 runs claim confidence=0.5; 2 of them succeed  (acc = 2/5 = 0.4)

With the default 10 bins (width 0.1):
    bin [0.5, 0.6): count=5, avg_conf=0.5, acc=0.4, |gap|=0.1, weight=5/10
    bin [0.9, 1.0]: count=5, avg_conf=0.9, acc=0.8, |gap|=0.1, weight=5/10
    ECE = 0.5*0.1 + 0.5*0.1 = 0.1
"""

from __future__ import annotations

import pytest

from agentalyze.analysis.calibration import (
    DEFAULT_N_BINS,
    compute_calibration_report,
)
from agentalyze.runner.trace import RunOutcome
from tests.analysis.conftest import make_step, make_trace


def _confidence_run(confidence: float | None, success: bool) -> object:
    """A minimal run whose done call reports the given confidence."""
    done_args: dict = {"success": True}
    if confidence is not None:
        done_args["confidence"] = confidence
    steps = [
        make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
        make_step(2, "done", done_args),
    ]
    outcome = RunOutcome.SUCCESS if success else RunOutcome.FAILURE_VERIFIER
    return make_trace(steps, outcome, verifier_success=success)


class TestHandComputedECE:
    def test_reference_example_ece_is_exactly_point_one(self) -> None:
        traces = (
            [_confidence_run(0.9, True)] * 4
            + [_confidence_run(0.9, False)]
            + [_confidence_run(0.5, True)] * 2
            + [_confidence_run(0.5, False)] * 3
        )
        report = compute_calibration_report(traces)

        assert report.n_bins == DEFAULT_N_BINS
        assert report.runs_with_confidence == 10

        # Bin membership: 0.5 -> [0.5, 0.6); 0.9 -> [0.9, 1.0].
        populated = {b.index: b for b in report.bins if b.count > 0}
        assert set(populated) == {5, 9}

        low, high = populated[5], populated[9]
        assert low.count == 5 and high.count == 5
        assert low.avg_confidence == pytest.approx(0.5)
        assert low.success_rate == pytest.approx(0.4)
        assert high.avg_confidence == pytest.approx(0.9)
        assert high.success_rate == pytest.approx(0.8)

        # ECE = (5/10)*|0.5-0.4| + (5/10)*|0.9-0.8| = 0.05 + 0.05 = 0.10
        assert report.ece == pytest.approx(0.1)

    def test_perfect_calibration_gives_zero_ece(self) -> None:
        # Two runs at confidence 1.0, both succeed: claimed == actual.
        traces = [_confidence_run(1.0, True), _confidence_run(1.0, True)]
        report = compute_calibration_report(traces)
        assert report.ece == pytest.approx(0.0)


class TestLowStatisticsWarning:
    def test_few_nonempty_bins_trigger_warning(self) -> None:
        # Only one non-empty bin from two runs: below the 3-bin threshold,
        # so the report MUST carry an explicit significance warning.
        traces = [_confidence_run(0.7, True), _confidence_run(0.7, False)]
        report = compute_calibration_report(traces)
        assert report.runs_with_confidence == 2
        assert sum(1 for b in report.bins if b.count > 0) == 1
        assert report.low_statistics_warning is not None
        assert "Low statistical significance" in report.low_statistics_warning

    def test_enough_bins_no_warning(self) -> None:
        confidences = [0.05, 0.25, 0.45, 0.65, 0.85]
        traces = [_confidence_run(c, True) for c in confidences]
        report = compute_calibration_report(traces)
        assert sum(1 for b in report.bins if b.count > 0) == len(confidences)
        assert report.low_statistics_warning is None


class TestParticipationRules:
    def test_runs_without_confidence_do_not_participate(self) -> None:
        traces = [
            *_confidence_triples(),
            _confidence_run(None, True),   # no confidence -> excluded
            _confidence_run(None, False),  # no confidence -> excluded
        ]
        report = compute_calibration_report(traces)
        assert report.runs_with_confidence == 3

    def test_no_data_at_all_means_undefined_ece(self) -> None:
        report = compute_calibration_report([_confidence_run(None, True)])
        assert report.ece is None
        assert report.runs_with_confidence == 0
        assert report.low_statistics_warning is not None
        assert "No run reported" in report.low_statistics_warning

    def test_invalid_confidence_values_are_skipped_or_clamped(self) -> None:
        good = _confidence_run(0.6, True)
        bad_text = _confidence_run(float("nan"), True)      # non-finite -> skipped
        overconfident = _confidence_run(1.7, True)          # clamped into [0, 1]

        report = compute_calibration_report(
            [good, bad_text, overconfident]  # type: ignore[list-item]
        )
        assert report.runs_with_confidence == 2  # nan excluded, 1.7 clamped in


class TestBoundaryValues:
    def test_exact_decimal_confidences_land_in_their_own_bins(self) -> None:
        # Off-by-one audit: every exact tenth must land in the bin whose
        # range starts with it (except 1.0, which by design belongs to the
        # last bin [0.9, 1.0]). Guards against float under-shoot like
        # 0.29*100 = 28.999999999999996 for non-default n_bins too.
        traces = [_confidence_run(c / 10, True) for c in range(11)]
        report = compute_calibration_report(traces)
        for c in range(9):
            bin_ = report.bins[c]
            assert bin_.count >= 1, f"bin {c} empty: {c/10} misbinned"
            assert bin_.avg_confidence == pytest.approx(c / 10)
        last = report.bins[DEFAULT_N_BINS - 1]
        # 0.9 and the clamped-in 1.0 both belong to the final bin.
        assert last.count == 2

    def test_non_default_bin_count_fp_edge(self) -> None:
        # The concrete FP hazard: 0.29 * 100 == 28.999999999999996 would
        # floor into bin 28 without the epsilon guard.
        traces = [_confidence_run(0.29, True), _confidence_run(0.71, True)]
        report = compute_calibration_report(traces, n_bins=100)
        assert report.bins[29].count == 1
        assert report.bins[71].count == 1
        assert sum(b.count for b in report.bins) == 2


def _confidence_triples() -> list:
    return [
        _confidence_run(0.2, True),
        _confidence_run(0.55, False),
        _confidence_run(0.95, True),
    ]


class TestConfiguration:
    def test_custom_bin_count(self) -> None:
        traces = _confidence_triples()
        report = compute_calibration_report(traces, n_bins=5)
        assert report.n_bins == 5
        assert len(report.bins) == 5
        assert report.bins[-1].upper == pytest.approx(1.0)

    def test_invalid_bin_count_raises(self) -> None:
        with pytest.raises(ValueError, match="n_bins"):
            compute_calibration_report(_confidence_triples(), n_bins=0)
