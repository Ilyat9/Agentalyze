"""Shared factories for the Phase 6 regression tests.

Pure object construction again (no browser, no provider): traces come from
the Phase 4 factories in ``tests.analysis.conftest``, suite results are
hand-assembled :class:`SuiteRunResult` objects, mirroring how the Phase 5
orchestration tests work.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentalyze.orchestration.suite_runner import SuiteRunConfig, SuiteRunResult
from agentalyze.runner.trace import RunOutcome, RunTrace
from tests.analysis.conftest import make_step, make_trace

PROVIDER_A = "provider-a"
PROVIDER_B = "provider-b"


def make_pair_traces(
    task_id: str,
    *,
    baseline_outcome: RunOutcome | None,
    new_outcome: RunOutcome | None,
    provider_name: str = PROVIDER_A,
    baseline_steps: int = 3,
    new_steps: int = 3,
    baseline_cost_usd: float | None = None,
    new_cost_usd: float | None = None,
    baseline_latency_seconds: float = 10.0,
    new_latency_seconds: float = 10.0,
) -> tuple[RunTrace | None, RunTrace | None]:
    """Build the (baseline, new) trace pair for one (task, provider) key."""
    def build(outcome: RunOutcome | None, run_id_suffix: str, steps_count: int,
              cost: float | None, latency: float) -> RunTrace:
        trace = make_trace(
            [make_step(i + 1, "click", {"element_id": "e1"}) for i in range(steps_count)],
            outcome,
            task_id=task_id,
            provider_name=provider_name,
            wall_clock_seconds=latency,
            total_cost_usd=cost,
        )
        trace.run_id = f"run-{run_id_suffix}-{task_id}-{outcome.value}"
        return trace

    baseline = (
        build(baseline_outcome, "base", baseline_steps, baseline_cost_usd,
              baseline_latency_seconds)
        if baseline_outcome is not None
        else None
    )
    new = (
        build(new_outcome, "new", new_steps, new_cost_usd, new_latency_seconds)
        if new_outcome is not None
        else None
    )
    return baseline, new


def make_result(
    traces: list[RunTrace | None],
    suite_run_id: str,
    *,
    providers: list[str] | None = None,
) -> SuiteRunResult:
    """Assemble a SuiteRunResult around the given traces (metrics omitted).

    ``None`` entries (an absent side of a pair) are skipped. Pass
    ``providers`` explicitly when a run holds no traces for a provider it
    was configured with.
    """
    real_traces = [t for t in traces if t is not None]
    if providers is None:
        providers = list(dict.fromkeys(t.provider_name for t in real_traces))
    assert providers, "a result needs at least one provider"
    return SuiteRunResult(
        suite_run_id=suite_run_id,
        started_at=datetime(2026, 8, 24, tzinfo=UTC),
        finished_at=datetime(2026, 8, 24, 1, 0, tzinfo=UTC),
        config=SuiteRunConfig(provider_names=providers),
        traces=real_traces,
    )


def save_pair_for_cli(
    results_dir: Any,
    baseline_traces: list[RunTrace],
    new_traces: list[RunTrace],
) -> tuple[str, str]:
    """Persist a baseline/new SuiteRunResult pair; returns (baseline_id, new_id)."""
    from agentalyze.orchestration.suite_runner import save_suite_run

    baseline = make_result(baseline_traces, "suite-baseline-0001")
    new = make_result(new_traces, "suite-new-0002")
    save_suite_run(baseline, results_dir)
    save_suite_run(new, results_dir)
    return baseline.suite_run_id, new.suite_run_id
