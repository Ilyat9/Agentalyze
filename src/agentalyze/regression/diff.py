"""Diff between two suite runs (Phase 6): what changed since the baseline.

Pure comparison layer: :func:`compute_regression` takes two ALREADY LOADED
:class:`~agentalyze.orchestration.suite_runner.SuiteRunResult` objects and
produces a :class:`SuiteRegressionReport`. No I/O, no side effects — loading
from disk is storage.py's job, running suites is Phases 3/5.

Pairing rule: traces are matched by ``(task_id, provider_name)``. Only the
INTERSECTION of providers is compared; providers present in just one of the
runs are reported explicitly in ``providers_only_in_baseline`` /
``providers_only_in_new`` instead of being silently ignored. Tasks added to
or removed from the suite between the two runs get their own explicit
statuses (``NEWLY_ADDED`` / ``REMOVED``) — a grown suite must never silently
distort the comparison.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from agentalyze.orchestration.suite_runner import SuiteRunResult
from agentalyze.runner.trace import RunOutcome, RunTrace


class TaskDiffStatus(str, Enum):
    """Outcome of comparing ONE (task_id, provider_name) pair over time."""

    STILL_PASSING = "still_passing"  # SUCCESS -> SUCCESS
    STILL_FAILING = "still_failing"  # FAILURE_* -> FAILURE_* (any failure kind, both sides)
    REGRESSED = "regressed"  # SUCCESS -> FAILURE_*
    FIXED = "fixed"  # FAILURE_* -> SUCCESS
    NEWLY_ADDED = "newly_added"  # in the new run, absent from the baseline (suite grew)
    REMOVED = "removed"  # was in the baseline, gone from the new run


class TaskDiff(BaseModel):
    """Per-(task, provider) delta between the baseline and the new run."""

    task_id: str
    provider_name: str
    status: TaskDiffStatus
    #: None on the side where the pair does not exist (NEWLY_ADDED / REMOVED).
    baseline_outcome: RunOutcome | None
    new_outcome: RunOutcome | None
    #: Per-run trace ids, letting you jump straight to the failing trace
    #: (``{results_dir}/{run_id}/trace.json``). None on the absent side.
    baseline_run_id: str | None
    new_run_id: str | None
    #: new - baseline; None unless BOTH sides have pricing configured
    #: (the harness never invents numbers, consistent with RunTrace).
    cost_delta_usd: float | None
    steps_delta: int | None
    latency_delta_seconds: float | None


class ProviderRegressionSummary(BaseModel):
    """Per-provider breakdown of the diff (only compared pairs are counted)."""

    provider_name: str
    compared_pairs: int
    regressed_count: int
    fixed_count: int
    still_passing_count: int
    still_failing_count: int
    newly_added_count: int
    removed_count: int


class SuiteRegressionReport(BaseModel):
    """The durable record of one baseline-vs-new comparison."""

    baseline_suite_run_id: str
    new_suite_run_id: str
    diffs: list[TaskDiff] = Field(default_factory=list)
    regressed_count: int = 0
    fixed_count: int = 0
    #: fixed_count - regressed_count; positive = things got better overall.
    net_change: int = 0
    #: Breakdown per provider (intersection only), keyed by provider name.
    provider_summary: dict[str, ProviderRegressionSummary] = Field(default_factory=dict)
    #: Providers seen in only ONE of the two runs. Their pairs were NOT
    #: compared; the mismatch is surfaced instead of hidden.
    providers_only_in_baseline: list[str] = Field(default_factory=list)
    providers_only_in_new: list[str] = Field(default_factory=list)


#: Display order for diffs: bad news first, noise last.
_STATUS_ORDER = [
    TaskDiffStatus.REGRESSED,
    TaskDiffStatus.FIXED,
    TaskDiffStatus.NEWLY_ADDED,
    TaskDiffStatus.REMOVED,
    TaskDiffStatus.STILL_FAILING,
    TaskDiffStatus.STILL_PASSING,
]


def _is_failure(outcome: RunOutcome) -> bool:
    return outcome is not RunOutcome.SUCCESS


# ---------------------------------------------------------------------------
# Pair indexing and per-pair diff construction.
# -------------------------------------------------------------------------


def _index_traces(
    result: SuiteRunResult, label: str
) -> dict[tuple[str, str], RunTrace]:
    """Index traces by (task_id, provider_name); duplicate pairs are a bug."""
    index: dict[tuple[str, str], RunTrace] = {}
    for trace in result.traces:
        key = (trace.task_id, trace.provider_name)
        if key in index:
            msg = (
                f"{label} suite run {result.suite_run_id} contains duplicate "
                f"traces for task={trace.task_id} provider={trace.provider_name}; "
                "a SuiteRunResult must hold at most one trace per pair."
            )
            raise ValueError(msg)
        index[key] = trace
    return index


def _build_diff(
    status: TaskDiffStatus, baseline: RunTrace | None, new: RunTrace | None
) -> TaskDiff:
    assert baseline is not None or new is not None
    cost_delta: float | None = None
    steps_delta: int | None = None
    latency_delta: float | None = None
    if baseline is not None and new is not None:
        # Cost stays None unless pricing was configured on BOTH sides.
        if baseline.total_cost_usd is not None and new.total_cost_usd is not None:
            cost_delta = new.total_cost_usd - baseline.total_cost_usd
        steps_delta = len(new.steps) - len(baseline.steps)
        latency_delta = new.wall_clock_seconds - baseline.wall_clock_seconds
    anchor = baseline if baseline is not None else new
    assert anchor is not None  # guaranteed by the guard above; narrows for mypy
    return TaskDiff(
        task_id=anchor.task_id,
        provider_name=anchor.provider_name,
        status=status,
        baseline_outcome=baseline.outcome if baseline else None,
        new_outcome=new.outcome if new else None,
        baseline_run_id=baseline.run_id if baseline else None,
        new_run_id=new.run_id if new else None,
        cost_delta_usd=cost_delta,
        steps_delta=steps_delta,
        latency_delta_seconds=latency_delta,
    )


def compute_regression(
    baseline: SuiteRunResult, new: SuiteRunResult
) -> SuiteRegressionReport:
    """Compare two loaded suite runs; pure, no side effects.

    Raises:
        ValueError: if either run contains duplicate (task_id, provider_name)
            pairs — that would make the pairing ambiguous.
    """
    baseline_index = _index_traces(baseline, "baseline")
    new_index = _index_traces(new, "new")

    # Providers are taken from the run's CONFIG, not just from the traces: a
    # crash-tolerant run may legitimately hold zero traces for a configured
    # provider, and that provider's pairs must still count for
    # NEWLY_ADDED / REMOVED instead of silently vanishing from the diff.
    baseline_providers = (
        set(baseline.config.provider_names) | {p for _, p in baseline_index}
    )
    new_providers = set(new.config.provider_names) | {p for _, p in new_index}
    common_providers = baseline_providers & new_providers

    diffs: list[TaskDiff] = []
    for task_id, provider_name in sorted(baseline_index.keys() | new_index.keys()):
        if provider_name not in common_providers:
            continue  # reported separately via providers_only_in_* lists
        base_trace = baseline_index.get((task_id, provider_name))
        new_trace = new_index.get((task_id, provider_name))
        if base_trace is None:
            status = TaskDiffStatus.NEWLY_ADDED
        elif new_trace is None:
            status = TaskDiffStatus.REMOVED
        elif base_trace.success and new_trace.success:
            status = TaskDiffStatus.STILL_PASSING
        elif _is_failure(base_trace.outcome) and _is_failure(new_trace.outcome):
            # ANY failure kinds qualify: failure_max_steps -> failure_verifier
            # is still "broken both times", not a regression or a fix.
            status = TaskDiffStatus.STILL_FAILING
        elif new_trace.success:
            status = TaskDiffStatus.FIXED
        else:
            status = TaskDiffStatus.REGRESSED
        diffs.append(_build_diff(status, base_trace, new_trace))

    regressed = sum(1 for d in diffs if d.status is TaskDiffStatus.REGRESSED)
    fixed = sum(1 for d in diffs if d.status is TaskDiffStatus.FIXED)

    summary: dict[str, ProviderRegressionSummary] = {}
    for provider in sorted(common_providers):
        provider_diffs = [d for d in diffs if d.provider_name == provider]
        summary[provider] = ProviderRegressionSummary(
            provider_name=provider,
            compared_pairs=len(provider_diffs),
            regressed_count=sum(
                1 for d in provider_diffs if d.status is TaskDiffStatus.REGRESSED
            ),
            fixed_count=sum(
                1 for d in provider_diffs if d.status is TaskDiffStatus.FIXED
            ),
            still_passing_count=sum(
                1 for d in provider_diffs if d.status is TaskDiffStatus.STILL_PASSING
            ),
            still_failing_count=sum(
                1 for d in provider_diffs if d.status is TaskDiffStatus.STILL_FAILING
            ),
            newly_added_count=sum(
                1 for d in provider_diffs if d.status is TaskDiffStatus.NEWLY_ADDED
            ),
            removed_count=sum(
                1 for d in provider_diffs if d.status is TaskDiffStatus.REMOVED
            ),
        )

    return SuiteRegressionReport(
        baseline_suite_run_id=baseline.suite_run_id,
        new_suite_run_id=new.suite_run_id,
        diffs=sorted(
            diffs,
            key=lambda d: (_STATUS_ORDER.index(d.status), d.task_id, d.provider_name),
        ),
        regressed_count=regressed,
        fixed_count=fixed,
        net_change=fixed - regressed,
        provider_summary=summary,
        providers_only_in_baseline=sorted(baseline_providers - new_providers),
        providers_only_in_new=sorted(new_providers - baseline_providers),
    )
