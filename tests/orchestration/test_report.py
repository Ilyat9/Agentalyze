"""Report-generation tests (Phase 5) on a fully known SuiteRunResult.

The scenario is constructed so that the honest-conclusion divergence is
guaranteed: ``provider-alpha`` leads on success rate (2/3) but
``provider-beta`` is strictly cheaper (avg $0.0080 vs $0.0120 -> 33.3%
cheaper). The markdown is validated programmatically: section headings,
exact formatted numbers from the source metrics, calibration honesty, the
small-sample caveat, and the computed conclusion sentences.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentalyze.analysis.metrics import compute_metrics
from agentalyze.orchestration.report import build_honest_conclusion, generate_report
from agentalyze.orchestration.suite_runner import SuiteRunConfig, SuiteRunResult
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.models import TaskCategory
from tests.analysis.conftest import make_step, make_trace

ALPHA = "provider-alpha"  # best success rate (2/3), pricier
BETA = "provider-beta"    # cheaper ($0.0080 avg), lower success rate (1/3)

SUITE_RUN_ID = "suite-report-test-001"


def _confidence_done_step(confidence: float, success: bool):
    return make_step(1, "done", {"success": success, "confidence": confidence})


def _build_traces() -> list:
    """2 providers x 3 tasks; costs/latencies/confidences chosen by hand."""
    return [
        # --- provider-alpha: successes on t1/t2, verifier failure on t3 ------
        make_trace(
            [_confidence_done_step(0.9, True)], RunOutcome.SUCCESS,
            task_id="t1", category=TaskCategory.NAVIGATION,
            provider_name=ALPHA, verifier_success=True,
            total_cost_usd=0.010,
        ),
        make_trace(
            [_confidence_done_step(0.8, True)], RunOutcome.SUCCESS,
            task_id="t2", category=TaskCategory.FORM_FILL,
            provider_name=ALPHA, verifier_success=True,
            total_cost_usd=0.012,
        ),
        make_trace(
            # done(success=false) -> GRACEFUL_GIVE_UP tag for the breakdown.
            [_confidence_done_step(0.7, False)], RunOutcome.FAILURE_VERIFIER,
            task_id="t3", category=TaskCategory.EXTRACTION,
            provider_name=ALPHA, verifier_success=False,
            total_cost_usd=0.014,
        ),
        # --- provider-beta: one success, two budget failures (no confidence) -
        make_trace(
            [make_step(1, "done", {"success": True})], RunOutcome.SUCCESS,
            task_id="t1", category=TaskCategory.NAVIGATION,
            provider_name=BETA, verifier_success=True,
            total_cost_usd=0.006,
        ),
        make_trace(
            # Three verbatim-identical clicks -> LOOPING tag for the breakdown.
            [make_step(n, "click", {"element_id": "e1"}) for n in (1, 2, 3)],
            RunOutcome.FAILURE_MAX_STEPS,
            task_id="t2", category=TaskCategory.FORM_FILL,
            provider_name=BETA,
            total_cost_usd=0.008,
        ),
        make_trace(
            [make_step(1, "navigate", {"url": "/"})], RunOutcome.FAILURE_TIMEOUT,
            task_id="t3", category=TaskCategory.EXTRACTION,
            provider_name=BETA,
            total_cost_usd=0.010,
        ),
    ]


def _build_result() -> SuiteRunResult:
    traces = _build_traces()
    return SuiteRunResult(
        suite_run_id=SUITE_RUN_ID,
        started_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        finished_at=datetime(2026, 8, 25, 12, 10, tzinfo=UTC),
        config=SuiteRunConfig(provider_names=[ALPHA, BETA]),
        traces=traces,
        metrics_by_provider={
            ALPHA: compute_metrics([t for t in traces if t.provider_name == ALPHA]),
            BETA: compute_metrics([t for t in traces if t.provider_name == BETA]),
        },
    )


class TestReportSectionsAndNumbers:
    def test_all_six_sections_present(self, tmp_path) -> None:
        path = generate_report(_build_result(), tmp_path)
        assert path == tmp_path / SUITE_RUN_ID / "report.md"
        report = path.read_text(encoding="utf-8")

        assert "# Agentalyze — Suite Run Report" in report
        assert "## Summary" in report
        assert "## Breakdown by task category" in report
        assert "## Failure breakdown" in report
        assert "## Confidence calibration" in report
        assert "## Honest conclusion" in report

    def test_summary_numbers_match_source_metrics_exactly(self, tmp_path) -> None:
        result = _build_result()
        report = generate_report(result, tmp_path).read_text(encoding="utf-8")

        alpha = result.metrics_by_provider[ALPHA]
        beta = result.metrics_by_provider[BETA]
        assert f"| `{ALPHA}` | 3 | {alpha.success_rate * 100:.1f}% | $0.0120 |" in report
        assert f"| `{BETA}` | 3 | {beta.success_rate * 100:.1f}% | $0.0080 |" in report
        # p50/p95 of every run's single 0.5s step, identical for both providers.
        assert "| 0.50s | 0.50s |" in report

    def test_category_section_shows_per_category_split(self, tmp_path) -> None:
        report = generate_report(_build_result(), tmp_path).read_text(encoding="utf-8")

        assert "### NAVIGATION" in report
        assert "### FORM_FILL" in report
        assert "### EXTRACTION" in report
        nav_block = report.split("### NAVIGATION")[1].split("### ")[0]
        assert "100.0%" in nav_block

    def test_small_sample_categories_are_explicitly_flagged(self, tmp_path) -> None:
        report = generate_report(_build_result(), tmp_path).read_text(encoding="utf-8")
        # Every category here has exactly 1 task — far below the reliability bar.
        assert report.count("**Малая выборка:**") == 3


class TestFailureBreakdownSection:
    def test_tags_are_listed_with_qualitative_meaning(self, tmp_path) -> None:
        report = generate_report(_build_result(), tmp_path).read_text(encoding="utf-8")

        alpha_block = report.split(f"### `{ALPHA}`")[1].split("### ")[0]
        assert "failure_verifier" in alpha_block
        assert "`graceful_give_up` | 1" in alpha_block

        beta_block = report.split(f"### `{BETA}`")[1].split("### ")[0]
        assert "failure_max_steps" in beta_block
        assert "failure_timeout" in beta_block
        assert "`looping` | 1" in beta_block

    def test_provider_without_tags_gets_explanation_not_blank_table(
        self, tmp_path,
    ) -> None:
        """Failures whose taxonomy heuristics fired nothing (plain timeouts)
        must get an explicit 'no tags' statement, not an empty why-table."""
        from agentalyze.analysis.metrics import compute_metrics
        from agentalyze.orchestration.suite_runner import SuiteRunConfig, SuiteRunResult

        traces = [
            make_trace(
                [make_step(1, "navigate", {"url": "/"})],
                RunOutcome.FAILURE_TIMEOUT,
                task_id="t1", category=TaskCategory.NAVIGATION,
                provider_name="plain-timeouts",
            ),
        ]
        result = SuiteRunResult(
            suite_run_id="sr-empty-tags",
            started_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            finished_at=datetime(2026, 8, 25, 12, 1, tzinfo=UTC),
            config=SuiteRunConfig(provider_names=["plain-timeouts"]),
            traces=traces,
            metrics_by_provider={
                "plain-timeouts": compute_metrics(traces),
            },
        )
        report = generate_report(result, tmp_path).read_text(encoding="utf-8")
        provider_block = report.split("### `plain-timeouts`")[1].split("## ")[0]

        assert "failure_timeout" in provider_block  # outcome still counted...
        assert "| Tag | Count | Что это значит |" not in provider_block  # ...no empty table...
        assert "не получил специфических тегов таксономии" in provider_block  # ...but words.



class TestCalibrationSection:
    def test_valid_ece_printed_and_insufficient_data_declared(self, tmp_path) -> None:
        report = generate_report(_build_result(), tmp_path).read_text(encoding="utf-8")
        calib_section = report.split("## Confidence calibration")[1]

        # Three non-empty bins (0.7/0.8/0.9) -> Phase 4 does NOT flag it.
        alpha_calib_block = calib_section.split(f"### `{ALPHA}`")[1].split("### ")[0]
        assert "ECE = **" in alpha_calib_block
        assert "[0.9, 1.0)" in alpha_calib_block

        beta_calib_block = calib_section.split(f"### `{BETA}`")[1].split("### ")[0]
        assert "Недостаточно данных для оценки калибровки" in beta_calib_block


class TestHonestConclusion:
    def test_divergence_between_best_and_cheapest_is_computed(self, tmp_path) -> None:
        report = generate_report(_build_result(), tmp_path).read_text(encoding="utf-8")
        conclusion_block = report.split("## Honest conclusion")[1]

        assert f"**{ALPHA}** даёт наивысший общий success rate (66.7%)" in conclusion_block
        assert f"**{BETA}** стоит на 33.3% дешевле при success rate 33.3%" in conclusion_block
        assert "Выбор зависит от того, что важнее" in conclusion_block

    def test_coinciding_leader_produces_no_false_divergence(self) -> None:
        dominant = _build_result().metrics_by_provider[ALPHA]
        joined = "\n".join(build_honest_conclusion({"x": dominant, "y": dominant}))
        assert "лидирует одновременно" in joined  # x wins the alphabetical tie

    def test_single_provider_gets_an_honest_non_comparison(self) -> None:
        lines = build_honest_conclusion({ALPHA: _build_result().metrics_by_provider[ALPHA]})
        assert len(lines) == 1 and "только один провайдер" in lines[0]
