"""One-off measurement: bounded-parallel speedup + result-set equivalence.

Drives the REAL ``run_suite`` (same code path as ``compare --max-concurrent``)
with a deterministic sleeping fake provider, then compares:
  * wall-clock time of max_concurrent=1 vs max_concurrent=3;
  * that both modes produce the identical SET of results.
"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentalyze.config import Settings
from agentalyze.orchestration.suite_runner import (
    SuiteRunConfig,
    load_suite_run,
    run_suite,
)
from agentalyze.runner.trace import RunOutcome
from tests.analysis.conftest import make_step, make_trace
from tests.orchestration.conftest import SuiteFakeProvider

TASKS = [
    "nav-simple-link-01",
    "nav-dropdown-menu-02",
    "form-fill-basic-01",
    "form-fill-validation-02",
]
PROVIDERS = ["fake-a", "fake-b", "fake-c"]


class SleepingFakeProvider(SuiteFakeProvider):
    async def chat_completion(self, messages, tools=None, temperature=0.0,
                              max_tokens=None):  # pragma: no cover - not reached
        raise AssertionError


async def _run(max_concurrent: int, results_dir: Path) -> tuple[float, set]:
    import agentalyze.orchestration.suite_runner as sr

    async def slow_run_task(task, provider, settings):
        await asyncio.sleep(0.4)  # simulated model/browser latency
        trace = make_trace(
            [make_step(1, "done", {"success": True})],
            RunOutcome.SUCCESS,
            task_id=task.id,
            category=task.category,
            provider_name=provider.name,
        )
        trace.run_id = f"{provider.name}-{task.id}"
        return trace

    sr.run_task = slow_run_task
    config = SuiteRunConfig(
        task_ids=TASKS,
        provider_names=PROVIDERS,
        max_concurrent=max_concurrent,
    )
    providers = {name: SleepingFakeProvider(name) for name in PROVIDERS}
    settings = Settings(fixtures_dir="/unused", results_dir=results_dir)

    start = time.monotonic()
    result = await run_suite(config, providers, settings)
    elapsed = time.monotonic() - start
    loaded = load_suite_run(results_dir, result.suite_run_id)
    keys = sorted((t.task_id, t.provider_name) for t in loaded.traces)
    return elapsed, set(map(tuple, keys))


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        seq_time, seq_keys = await _run(1, Path(tmp) / "seq")
        par_time, par_keys = await _run(3, Path(tmp) / "par")

    print(f"sequential (max_concurrent=1): {seq_time:.2f}s")
    print(f"parallel   (max_concurrent=3): {par_time:.2f}s "
          f"({seq_time / par_time:.1f}x faster)")
    assert par_time < seq_time * 0.7, "expected a real speedup"
    assert seq_keys == par_keys and len(seq_keys) == len(TASKS) * len(PROVIDERS), (
        "result sets differ!"
    )
    print("result sets IDENTICAL:",
          f"{len(seq_keys)} combinations, same composition in both modes")


asyncio.run(main())
