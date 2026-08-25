"""compute_metrics against a hand-computed scenario (numbers derived on paper).

Scenario (provider-a over a partial suite):
    S1  SUCCESS    navigation   latencies [1.0, 3.0]   tokens 1000p/1000c  wall 10s
    S2  SUCCESS    navigation   latencies [2.0]        tokens  500p/ 500c  wall 20s
    F1  FAIL_VERIF form_fill    latencies [4.0, 5.0, 6.0]
                                           tokens 1500p/2500c  wall 30s
Pricing: prompt 0.002 USD/1k, completion 0.006 USD/1k.

Hand-derived expectations:
    success_rate      = 2/3
    failure_breakdown = {FAILURE_VERIFIER: 1}
    failure_tag_breakdown = {GRACEFUL_GIVE_UP: 1}   # F1 ends in done(success=False)
    costs: S1 = 1.0*0.002 + 1.0*0.006 = 0.008
           S2 = 0.5*0.002 + 0.5*0.006 = 0.004
           F1 = 1.5*0.002 + 2.5*0.006 = 0.018
           total = 0.030, average = 0.010
    latencies pooled & sorted: [1, 2, 3, 4, 5, 6]
        p50 nearest-rank: ceil(0.50*6)=3 -> 3.0
        p95 nearest-rank: ceil(0.95*6)=6 -> 6.0
    total_wall_clock = 60.0; avg_steps = (2+1+3)/3 = 2.0
    by_category: NAVIGATION {2 tasks, rate 1.0}, FORM_FILL {1 task, rate 0.0}
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentalyze.analysis.failure_taxonomy import FailureTag
from agentalyze.analysis.metrics import CategoryMetrics, TaskSuiteMetrics, compute_metrics
from agentalyze.analysis.pricing import ModelPrice, PricingConfig
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.models import TaskCategory
from tests.analysis.conftest import PROVIDER_B, make_step, make_trace


def _scenario_traces() -> list:
    s1_steps = [
        make_step(1, "navigate", {"url": "/form.html"}, dom_hash="h1", latency_seconds=1.0),
        make_step(2, "done", {"success": True, "confidence": 0.9}, latency_seconds=3.0),
    ]
    s2_steps = [
        make_step(1, "click", {"element_id": "e1"}, dom_hash="h1", latency_seconds=2.0),
    ]
    f1_steps = [
        make_step(1, "click", {"element_id": "e1"}, dom_hash="h1", latency_seconds=4.0),
        make_step(2, "wait_for", {"condition_description": "x"}, latency_seconds=5.0),
        make_step(3, "done", {"success": False}, latency_seconds=6.0),
    ]
    return [
        make_trace(
            s1_steps,
            RunOutcome.SUCCESS,
            task_id="task-nav-01",
            category=TaskCategory.NAVIGATION,
            verifier_success=True,
            wall_clock_seconds=10.0,
            total_prompt_tokens=1000,
            total_completion_tokens=1000,
        ),
        make_trace(
            s2_steps,
            RunOutcome.SUCCESS,
            task_id="task-nav-02",
            category=TaskCategory.NAVIGATION,
            verifier_success=True,
            wall_clock_seconds=20.0,
            total_prompt_tokens=500,
            total_completion_tokens=500,
        ),
        make_trace(
            f1_steps,
            RunOutcome.FAILURE_VERIFIER,
            task_id="task-form-01",
            category=TaskCategory.FORM_FILL,
            verifier_success=False,
            wall_clock_seconds=30.0,
            total_prompt_tokens=1500,
            total_completion_tokens=2500,
        ),
    ]


def _pricing() -> PricingConfig:
    return PricingConfig(
        pricing={
            "provider-a": ModelPrice(
                prompt_price_per_1k_usd=0.002,
                completion_price_per_1k_usd=0.006,
            )
        }
    )


class TestHandComputedMetrics:
    def test_full_scenario(self) -> None:
        metrics = compute_metrics(_scenario_traces(), pricing=_pricing())

        assert metrics.provider_name == "provider-a"
        assert metrics.total_tasks == 3
        assert metrics.success_count == 2
        assert metrics.success_rate == pytest.approx(2 / 3)
        assert metrics.failure_breakdown == {RunOutcome.FAILURE_VERIFIER: 1}
        assert metrics.failure_tag_breakdown == {FailureTag.GRACEFUL_GIVE_UP: 1}
        assert metrics.total_cost_usd == pytest.approx(0.030)
        assert metrics.avg_cost_usd_per_task == pytest.approx(0.010)
        assert metrics.total_wall_clock_seconds == pytest.approx(60.0)
        assert metrics.avg_steps_per_task == pytest.approx(2.0)
        assert metrics.p50_latency_seconds == pytest.approx(3.0)
        assert metrics.p95_latency_seconds == pytest.approx(6.0)

    def test_by_category_breakdown(self) -> None:
        metrics = compute_metrics(_scenario_traces(), pricing=_pricing())

        assert set(metrics.by_category) == {
            TaskCategory.NAVIGATION,
            TaskCategory.FORM_FILL,
        }
        navigation = metrics.by_category[TaskCategory.NAVIGATION]
        form_fill = metrics.by_category[TaskCategory.FORM_FILL]

        assert isinstance(navigation, CategoryMetrics)
        assert navigation.total_tasks == 2
        assert navigation.success_rate == pytest.approx(1.0)
        assert navigation.failure_breakdown == {}
        assert navigation.p50_latency_seconds == pytest.approx(2.0)

        assert form_fill.total_tasks == 1
        assert form_fill.success_count == 0
        assert form_fill.failure_breakdown == {RunOutcome.FAILURE_VERIFIER: 1}
        assert form_fill.total_cost_usd == pytest.approx(0.018)

    def test_bounded_hierarchy_not_recursive(self) -> None:
        # The deliberate design: only TaskSuiteMetrics carries by_category;
        # CategoryMetrics has none, so the structure cannot nest endlessly.
        assert "by_category" not in CategoryMetrics.model_fields
        assert "by_category" in TaskSuiteMetrics.model_fields


class TestErrorCases:
    def test_empty_list_is_an_explicit_error(self) -> None:
        with pytest.raises(ValueError, match="at least one RunTrace"):
            compute_metrics([])

    def test_mixed_providers_are_rejected(self) -> None:
        traces = _scenario_traces()
        traces.append(make_trace([], RunOutcome.SUCCESS, provider_name=PROVIDER_B))
        with pytest.raises(ValueError, match="SINGLE provider"):
            compute_metrics(traces)

    def test_unknown_costs_aggregate_to_none_not_zero(self) -> None:
        # No pricing table, and traces carry no stored cost either:
        # "unknown" must stay None, never collapse into a fake $0.
        metrics = compute_metrics(_scenario_traces())
        assert metrics.total_cost_usd is None
        assert metrics.avg_cost_usd_per_task is None
        assert metrics.by_category[TaskCategory.NAVIGATION].total_cost_usd is None

    def test_traces_without_category_skip_by_category(self) -> None:
        traces = [
            make_trace([], RunOutcome.SUCCESS, category=None, verifier_success=True),
        ]
        metrics = compute_metrics(traces)
        assert metrics.by_category == {}
        assert metrics.total_tasks == 1

    def test_nan_success_rate_rejected_by_model(self) -> None:
        # Sanity: the Pydantic model itself refuses non-finite metric values.
        with pytest.raises(ValidationError):
            CategoryMetrics(
                total_tasks=1,
                success_count=0,
                success_rate=float("nan"),  # type: ignore[arg-type]
                failure_breakdown={},
                failure_tag_breakdown={},
                total_cost_usd=None,
                avg_cost_usd_per_task=None,
                total_wall_clock_seconds=0.0,
                avg_steps_per_task=0.0,
                p50_latency_seconds=0.0,
                p95_latency_seconds=0.0,
            )


