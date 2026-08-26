"""Suite runner (Phase 5): every selected task x every selected provider.

This is the orchestration layer on top of the Phase 3 single-run core:
it turns ONE :class:`SuiteRunConfig` into ONE :class:`SuiteRunResult`
holding every produced :class:`RunTrace` plus per-provider
:class:`TaskSuiteMetrics` (Phase 4's ``compute_metrics`` applied per
provider — cross-provider aggregation stays out of the analysis layer).

Deliberate properties of this implementation:

* **Sequential by default, optionally bounded-parallel.** ``max_concurrent``
  defaults to 1, which keeps the original strictly sequential behavior.
  Values above 1 run combinations concurrently behind an
  ``asyncio.Semaphore``: at most ``max_concurrent`` combinations are
  in flight at any moment. The bound is load-bearing, not cosmetic — every
  combination owns a real Chromium instance and a fixture server, so an
  unbounded ``gather`` would multiply browser processes and open provider
  connections without limit. Isolation is guaranteed by ``run_task``'s own
  contract: each combination starts its OWN ``FixtureServer`` on an
  OS-assigned free port, its own browser + context, and writes artifacts
  under its own unique ``{results_dir}/{run_id}/`` directory, so parallel
  combinations share no mutable state.
* **Crash-tolerant.** One combination failing (an exception escaping
  ``run_task`` — the runner itself converts its internal crashes into
  ``FAILURE_CRASH`` traces) logs the problem and continues with the next
  combination. A suite run must survive isolated breakage.
* **Incrementally persisted.** After EVERY completed combination the full
  ``SuiteRunResult`` snapshot (traces collected so far + fresh metrics)
  is rewritten to ``{results_dir}/{suite_run_id}/suite_run.json``. A crash
  halfway through a multi-hour run must not lose hours of results. In
  parallel mode several coroutines can finish at once, so the
  read-modify-write of the shared snapshot is serialized with an
  ``asyncio.Lock`` — an append-only log would be a storage-format change
  serving the same purpose; the lock keeps the existing format and readers
  unchanged while making concurrent completion safe (all work runs on one
  event loop thread, so the lock fully closes the race window).
"""

from __future__ import annotations

import asyncio
import logging
import time
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


def _format_eta(seconds: float) -> str:
    """Human-readable remaining-time estimate ('42s', '3m 05s', '1h 02m').

    Deliberately coarse (no seconds shown above a minute): the estimate is a
    linear extrapolation from already-finished combinations, so displaying
    fake precision would be dishonest.
    """
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


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
    #: How many (task x provider) combinations may run at the same time.
    #: 1 (the default) = strictly sequential, the historically safe behavior.
    #: Values above 1 run combinations concurrently behind an
    #: ``asyncio.Semaphore``; every combination stays fully isolated (own
    #: browser, own fixture server, own artifact directory), but each in-flight
    #: combination holds a real Chromium instance and provider connections,
    #: so raise this with resource cost in mind.
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
    """Run every selected task with every selected provider.

    Progress is printed to stdout as ``[i/N] task=... provider=... -> outcome``.
    From the second combination on, the progress prefix carries an ETA — a
    simple linear extrapolation ``avg(finished combinations) * remaining``.
    After EVERY completed combination the current :class:`SuiteRunResult`
    snapshot (all traces so far + recomputed per-provider metrics) is
    persisted, so an interrupted run keeps everything finished up to that
    point.

    With ``max_concurrent == 1`` (the default) combinations run strictly one
    after another. Larger values run up to ``max_concurrent`` combinations at
    once behind an :class:`asyncio.Semaphore`; the shared snapshot is then
    updated under an :class:`asyncio.Lock` so concurrent completions can never
    interleave a read-modify-write of the persisted file.

    Raises:
        ValueError: on unknown provider or task names, or an empty resolved
            task selection.
    """
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

    #: Wall-clock durations of completed combinations, in run order; feeds the
    #: linear ETA extrapolation below (simple average, no smoothing needed).
    completed_durations: list[float] = []

    if config.max_concurrent == 1:
        await _run_all_sequential(combinations, result, settings, total,
                                  completed_durations)
    else:
        await _run_all_parallel(combinations, result, settings, total,
                                config.max_concurrent, completed_durations)

    duration = (result.finished_at - result.started_at).total_seconds()
    print(f"Suite run {result.suite_run_id} finished: "
          f"{len(result.traces)}/{total} combination(s) completed in {duration:.1f}s",
          flush=True)
    return result


def _eta_prefix(completed_durations: list[float], index: int, total: int) -> str:
    if not completed_durations:
        return ""
    avg_seconds = sum(completed_durations) / len(completed_durations)
    eta_seconds = avg_seconds * (total - index + 1)
    return f" (eta ~{_format_eta(eta_seconds)} left)"


async def _persist_snapshot(result: SuiteRunResult, settings: Settings) -> None:
    """Refresh metrics + finished_at and rewrite the on-disk snapshot."""
    # finished_at tracks "as of this snapshot" on every intermediate save.
    result.finished_at = datetime.now(UTC)
    result.metrics_by_provider = _metrics_snapshot(result.traces)
    save_suite_run(result, settings.results_dir)


async def _run_all_sequential(
    combinations: list[tuple[Task, Provider]],
    result: SuiteRunResult,
    settings: Settings,
    total: int,
    completed_durations: list[float],
) -> None:
    """The original strictly sequential path (``max_concurrent == 1``)."""
    for index, (task, provider) in enumerate(combinations, start=1):
        print(
            f"[{index}/{total}]{_eta_prefix(completed_durations, index, total)} "
            f"task={task.id} provider={provider.name} ... ",
            end="",
            flush=True,
        )
        combo_started = time.monotonic()
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
        else:
            result.traces.append(trace)
            await _persist_snapshot(result, settings)
            print(f"{trace.outcome.value} ({trace.wall_clock_seconds:.1f}s)", flush=True)
        finally:
            # Crashed combinations still consumed wall-clock time; counting
            # them keeps the ETA honest for suites where several combos fail.
            completed_durations.append(time.monotonic() - combo_started)


async def _run_all_parallel(
    combinations: list[tuple[Task, Provider]],
    result: SuiteRunResult,
    settings: Settings,
    total: int,
    max_concurrent: int,
    completed_durations: list[float],
) -> None:
    """Bounded-parallel path (``max_concurrent > 1``).

    The semaphore caps the number of in-flight combinations — each holds a
    real Chromium instance, a fixture server thread and open connections to
    the provider, so an unbounded ``gather`` would multiply all three without
    limit. The lock serializes the shared-snapshot read-modify-write when
    several coroutines finish at once. Everything runs on a single event loop
    thread: plain list appends elsewhere are safe without synchronization.

    Isolation across combinations needs no extra machinery here because it is
    ``run_task``'s own contract: per-combination fixture server (OS-assigned
    free port), browser + context, and artifacts under a unique run_id
    directory. Parallel combinations share no mutable state.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    save_lock = asyncio.Lock()

    async def worker(index: int, task: Task, provider: Provider) -> None:
        async with semaphore:
            print(
                f"[{index}/{total}] START task={task.id} provider={provider.name}",
                flush=True,
            )
            combo_started = time.monotonic()
            try:
                trace = await run_task(task, provider, settings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("combination task=%s provider=%s crashed", task.id,
                                 provider.name)
                print(f"[{index}/{total}] CRASHED: task={task.id} "
                      f"provider={provider.name}: {exc}", flush=True)
                return
            finally:
                completed_durations.append(time.monotonic() - combo_started)

            async with save_lock:
                result.traces.append(trace)
                await _persist_snapshot(result, settings)
            print(
                f"[{index}/{total}] DONE task={task.id} provider={provider.name}"
                f" -> {trace.outcome.value} ({trace.wall_clock_seconds:.1f}s)",
                flush=True,
            )

    await asyncio.gather(
        *(worker(index, task, provider)
          for index, (task, provider) in enumerate(combinations, start=1))
    )


