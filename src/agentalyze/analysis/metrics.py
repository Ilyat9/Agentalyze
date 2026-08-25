"""Aggregated metrics for ONE provider over a (possibly partial) task suite.

This is the computational heart of Phase 4: it turns a list of ``RunTrace``
objects into one :class:`TaskSuiteMetrics` structure. It deliberately computes
only — no comparison across providers and no human-readable rendering; both
belong to Phase 5, which consumes these structures as-is.

Cost semantics (see ``analysis.cost``): ``total_cost_usd is None`` means "at
least one run's cost could not be computed because its provider has no price
entry" — NOT "everything was free". A genuinely free suite (local Ollama with
``free: true`` pricing) yields ``0.0``.
"""

from __future__ import annotations

import math
from collections import Counter

from pydantic import BaseModel, Field

from agentalyze.analysis.cost import compute_run_cost, summarize_costs
from agentalyze.analysis.failure_taxonomy import FailureTag, classify_failure
from agentalyze.analysis.pricing import PricingConfig
from agentalyze.runner.trace import RunOutcome, RunTrace
from agentalyze.tasks.models import TaskCategory


class CategoryMetrics(BaseModel):
    """Metrics over a subset of runs (the whole suite or one task category).

    Design note on the recursive ``by_category`` idea: a *bounded* hierarchy
    is implemented instead. ``TaskSuiteMetrics`` subclasses this model and is
    the ONLY level that carries ``by_category``; this class itself has none.
    A self-referential ``dict[TaskCategory, TaskSuiteMetrics]`` would be
    meaningless (categories don't nest inside categories) and would invite
    unbounded recursion in the type for zero analytical gain, so the clean
    two-level split (suite -> categories) is used instead of literal recursion.
    """

    total_tasks: int = Field(ge=1)
    success_count: int = Field(ge=0)
    success_rate: float = Field(ge=0.0, le=1.0)
    #: How many runs ended in each outcome (SUCCESS excluded — that count
    #: lives in ``success_count``; this mapping is for failures only).
    failure_breakdown: dict[RunOutcome, int]
    #: Tag counts across ALL unsuccessful runs, summed per tag. A run may
    #: carry several tags, so the totals are intentionally non-exclusive.
    failure_tag_breakdown: dict[FailureTag, int]
    total_cost_usd: float | None
    avg_cost_usd_per_task: float | None
    total_wall_clock_seconds: float = Field(ge=0)
    avg_steps_per_task: float = Field(ge=0)
    p50_latency_seconds: float = Field(ge=0)
    p95_latency_seconds: float = Field(ge=0)


class TaskSuiteMetrics(CategoryMetrics):
    """Full metrics for one provider's run over a task suite."""

    provider_name: str
    #: Per-category recomputation of the same metrics over only that
    #: category's runs. Critical for real diagnosis ("strong at NAVIGATION,
    #: collapses on ERROR_RECOVERY"). Traces without a recorded
    #: ``task_category`` (pre-Phase-4 traces) are skipped here.
    by_category: dict[TaskCategory, CategoryMetrics] = Field(default_factory=dict)


def _percentile_nearest_rank(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile (deterministic, no interpolation smoothing).

    Chosen over interpolated variants because the result must be one of the
    actually observed latencies — an invented in-between number is harder to
    explain when auditing a report.
    """
    if not sorted_values:
        return 0.0  # no LLM calls recorded at all; documented degenerate case
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _run_cost(trace: RunTrace, pricing: PricingConfig | None) -> float | None:
    """Cost of one run: computed from pricing when available, else stored value.

    A Phase-3-era trace may carry ``total_cost_usd`` already; when a pricing
    table is supplied it wins, so a whole suite is always priced consistently
    from one table rather than mixing sources.
    """
    if pricing is not None:
        return compute_run_cost(trace, pricing)
    return trace.total_cost_usd


def _core_metrics(traces: list[RunTrace], pricing: PricingConfig | None) -> CategoryMetrics:
    latencies = sorted(
        step.llm_response.latency_seconds for trace in traces for step in trace.steps
    )
    costs = [_run_cost(trace, pricing) for trace in traces]
    total_cost, avg_cost = summarize_costs(costs)

    failure_outcomes = Counter(
        trace.outcome for trace in traces if trace.outcome is not RunOutcome.SUCCESS
    )
    tag_counts: Counter[FailureTag] = Counter()
    for trace in traces:
        if trace.outcome is RunOutcome.SUCCESS:
            continue
        tag_counts.update(classify_failure(trace))

    success_count = sum(1 for trace in traces if trace.outcome is RunOutcome.SUCCESS)
    total_tasks = len(traces)
    return CategoryMetrics(
        total_tasks=total_tasks,
        success_count=success_count,
        success_rate=success_count / total_tasks,
        failure_breakdown=dict(failure_outcomes),
        failure_tag_breakdown=dict(tag_counts),
        total_cost_usd=total_cost,
        avg_cost_usd_per_task=avg_cost,
        total_wall_clock_seconds=sum(trace.wall_clock_seconds for trace in traces),
        avg_steps_per_task=sum(len(trace.steps) for trace in traces) / total_tasks,
        p50_latency_seconds=_percentile_nearest_rank(latencies, 50),
        p95_latency_seconds=_percentile_nearest_rank(latencies, 95),
    )


def compute_metrics(
    traces: list[RunTrace],
    pricing: PricingConfig | None = None,
) -> TaskSuiteMetrics:
    """Compute suite metrics over ONE provider's traces.

    Args:
        traces: runs of a single provider over any subset of the suite.
        pricing: optional editable price table; when omitted, cost fields
            fall back to each trace's own ``total_cost_usd``.

    Raises:
        ValueError: on an empty list (an aggregate over nothing is undefined
            — this is an explicit error, never NaN or division by zero), or
            when the traces mix several providers (cross-provider comparison
            is Phase 5's job and must not happen implicitly).
    """
    if not traces:
        msg = (
            "compute_metrics requires at least one RunTrace; there is nothing "
            "to aggregate over (refusing to emit NaN-filled metrics)."
        )
        raise ValueError(msg)

    providers = {trace.provider_name for trace in traces}
    if len(providers) > 1:
        msg = (
            "compute_metrics expects traces of a SINGLE provider, got several: "
            f"{sorted(providers)}. Cross-provider comparison is Phase 5."
        )
        raise ValueError(msg)

    core = _core_metrics(traces, pricing)

    by_category: dict[TaskCategory, CategoryMetrics] = {}
    grouped: dict[TaskCategory, list[RunTrace]] = {}
    for trace in traces:
        # Pre-Phase-4 traces carry task_category=None and simply do not
        # participate in per-category breakdowns (backward compatible).
        if trace.task_category is not None:
            grouped.setdefault(trace.task_category, []).append(trace)
    for category, category_traces in grouped.items():
        by_category[category] = _core_metrics(category_traces, pricing)

    provider_name = providers.pop()
    return TaskSuiteMetrics(
        **core.model_dump(),
        provider_name=provider_name,
        by_category=by_category,
    )

