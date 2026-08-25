"""Suite runner (Phase 5): every selected task x every selected provider.

This is the orchestration layer on top of the Phase 3 single-run core:
it turns ONE :class:`SuiteRunConfig` into ONE :class:`SuiteRunResult`
holding every produced :class:`RunTrace` plus per-provider
:class:`TaskSuiteMetrics` (Phase 4's ``compute_metrics`` applied per
provider — cross-provider aggregation stays out of the analysis layer).

Deliberate properties of this implementation:

* **Sequential.** Combinations run one after another. Parallelism is a
  performance optimization, not a correctness requirement, and is
  explicitly OUT of scope for this phase: ``max_concurrent > 1`` raises a
  clear error instead of being silently ignored.
* **Crash-tolerant.** One combination failing (an exception escaping
  ``run_task`` — the runner itself converts its internal crashes into
  ``FAILURE_CRASH`` traces) logs the problem and continues with the next
  combination. A suite run must survive isolated breakage.
* **Incrementally persisted.** After EVERY completed combination the full
  ``SuiteRunResult`` snapshot (traces collected so far + fresh metrics)
  is rewritten to ``{results_dir}/{suite_run_id}/suite_run.json``. A crash
  halfway through a multi-hour run must not lose hours of results.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from agentalyze.analysis.metrics import TaskSuiteMetrics, compute_metrics
from agentalyze.config import Settings
from agentalyze.providers.base import Provider
from agentalyze.runner.react_loop import run_task
from agentalyze.runner.trace import RunTrace
from agentalyze.tasks.models import Task, TaskCategory
from agentalyze.tasks.registry import TASKS, TASKS_BY_ID

logger = logging.getLogger(__name__)


class SuiteRunConfig(BaseModel):
    """What to run and with whom.

    Task selection: ``task_ids=None`` means "the whole registry";
    otherwise the named subset (unknown ids are an explicit configuration
    error). ``category_filter`` is applied on top of that subset when
    given, which makes both of the spec's usage styles possible::

        SuiteRunConfig(task_ids=None, ...)                       # all tasks
        SuiteRunConfig(category_filter=[TaskCategory.NAVIGATION], ...)
        SuiteRunConfig(task_ids=["form-fill-basic-01"], ...)
    """

    task_ids: list[str] | None = Field(
        default=None,
        description="None = every registry task; otherwise concrete task ids.",
    )
    provider_names: list[str] = Field(min_length=1)
    category_filter: list[TaskCategory] | None = None
    #: Reserved for future parallel execution. MUST stay 1 in this phase:
    #: run_suite rejects any other value instead of silently ignoring it.
    max_concurrent: int = Field(default=1, ge=1)

    @field_validator("provider_names")
    @classmethod
    def _names_are_non_empty(cls, value: list[str]) -> list[str]:
        if any(not name.strip() for name in value):
            msg = "provider_names must not contain empty strings"
            raise ValueError(msg)
        return value


class SuiteRunResult(BaseModel):
    """The durable record of one whole-suite comparison run."""

    suite_run_id: str = Field(
        description="UUID of THIS suite run (distinct from per-task run_id values).",
    )
    started_at: datetime
    finished_at: datetime
    config: SuiteRunConfig
    traces: list[RunTrace] = Field(default_factory=list)
    #: Phase 4 metrics computed independently per provider over that
    #: provider's traces of this run. Providers with zero completed traces
    #: are absent (compute_metrics refuses to aggregate over nothing).
    metrics_by_provider: dict[str, TaskSuiteMetrics] = Field(default_factory=dict)


def select_tasks(config: SuiteRunConfig) -> list[Task]:
    """Resolve the config against the registry; duplicates removed, order kept."""
    if config.task_ids is None:
        tasks = list(TASKS)
    else:
        requested = list(dict.fromkeys(config.task_ids))  # dedupe, keep order
        unknown = [task_id for task_id in requested if task_id not in TASKS_BY_ID]
        if unknown:
            msg = (
                f"unknown task id(s) in SuiteRunConfig: {unknown}; "
                "run `agentalyze tasks` to list registered ids"
            )
            raise ValueError(msg)
        tasks = [TASKS_BY_ID[task_id] for task_id in requested]

    if config.category_filter is not None:
        allowed = set(config.category_filter)
        tasks = [task for task in tasks if task.category in allowed]

    if not tasks:
        msg = "task selection resolved to an empty list; nothing to run"
        raise ValueError(msg)
    return tasks


def _metrics_snapshot(traces: list[RunTrace]) -> dict[str, TaskSuiteMetrics]:
    """Per-provider compute_metrics over the traces collected so far."""
    grouped: dict[str, list[RunTrace]] = defaultdict(list)
    for trace in traces:
        grouped[trace.provider_name].append(trace)
    return {
        provider: compute_metrics(provider_traces)
        for provider, provider_traces in sorted(grouped.items())
    }


def suite_run_dir(results_dir: Path, suite_run_id: str) -> Path:
    """Artifact directory for one suite run."""
    return Path(results_dir) / suite_run_id


def save_suite_run(result: SuiteRunResult, results_dir: Path) -> Path:
    """Serialize the whole result to ``{results_dir}/{suite_run_id}/suite_run.json``."""
    directory = suite_run_dir(results_dir, result.suite_run_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "suite_run.json"
    path.write_text(result.model_dump_json(), encoding="utf-8")
    return path


def load_suite_run(results_dir: Path, suite_run_id: str) -> SuiteRunResult:
    """Read back a suite run written by :func:`save_suite_run`."""
    path = suite_run_dir(results_dir, suite_run_id) / "suite_run.json"
    return SuiteRunResult.model_validate_json(path.read_text(encoding="utf-8"))


async def run_suite(
    config: SuiteRunConfig,
    providers: dict[str, Provider],
    settings: Settings,
) -> SuiteRunResult:
    """Run every selected task with every selected provider, sequentially.

    Progress is printed to stdout as ``[i/N] task=... provider=... -> outcome``;
    after EVERY completed combination the current :class:`SuiteRunResult`
    snapshot (all traces so far + recomputed per-provider metrics) is
    persisted, so an interrupted run keeps everything finished up to that
    point.

    Raises:
        ValueError: on ``config.max_concurrent > 1`` (parallelism is NOT
            implemented in this phase — fail loudly instead of pretending
            the knob works), unknown provider or task names, or an empty
            resolved task selection.
    """
    if config.max_concurrent != 1:
        msg = (
            f"max_concurrent={config.max_concurrent} is not supported: parallel "
            "execution is not implemented in this phase — Phase 5 runs "
            "combinations strictly sequentially. The parameter is reserved for "
            "the future; pass max_concurrent=1."
        )
        raise ValueError(msg)

    missing_providers = [name for name in config.provider_names if name not in providers]
    if missing_providers:
        available = ", ".join(sorted(providers)) or "<none>"
        msg = f"unknown provider name(s) {missing_providers}; configured: {available}"
        raise ValueError(msg)

    tasks = select_tasks(config)
    selected_providers = [providers[name] for name in config.provider_names]
    combinations = [(task, provider) for task in tasks for provider in selected_providers]

    result = SuiteRunResult(
        suite_run_id=str(uuid.uuid4()),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        config=config,
    )
    settings.ensure_results_dir()
    save_suite_run(result, settings.results_dir)  # initial snapshot: the run has begun

    total = len(combinations)
    print(
        f"Suite run {result.suite_run_id}: {len(tasks)} task(s) x "
        f"{len(selected_providers)} provider(s) = {total} combination(s)",
        flush=True,
    )

    for index, (task, provider) in enumerate(combinations, start=1):
        print(f"[{index}/{total}] task={task.id} provider={provider.name} ... ",
              end="", flush=True)
        try:
            trace = await run_task(task, provider, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # run_task itself converts internal crashes into FAILURE_CRASH
            # traces; an exception here means even that guarantee was broken
            # (or tests injected a failure). Log, keep going.
            logger.exception("combination task=%s provider=%s crashed", task.id,
                             provider.name)
            print(f"CRASHED: {exc}", flush=True)
            continue

        result.traces.append(trace)
        # finished_at tracks "as of this snapshot" on every intermediate save.
        result.finished_at = datetime.now(UTC)
        result.metrics_by_provider = _metrics_snapshot(result.traces)
        save_suite_run(result, settings.results_dir)
        print(f"{trace.outcome.value} ({trace.wall_clock_seconds:.1f}s)", flush=True)

    duration = (result.finished_at - result.started_at).total_seconds()
    print(f"Suite run {result.suite_run_id} finished: "
          f"{len(result.traces)}/{total} combination(s) completed in {duration:.1f}s",
          flush=True)
    return result


