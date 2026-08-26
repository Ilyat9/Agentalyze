"""Suite-runner orchestration tests (Phase 5, fast, no browser by default).

``run_task`` is replaced with a deterministic fake so these tests exercise
ONLY the orchestration contract: combination count, per-provider metrics,
the max_concurrent guard, crash isolation and incremental persistence.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import agentalyze.orchestration.suite_runner as suite_runner_module
from agentalyze.config import Settings
from agentalyze.orchestration.suite_runner import (
    SuiteRunConfig,
    _format_eta,
    load_suite_run,
    run_suite,
    select_tasks,
)
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.models import TaskCategory
from tests.analysis.conftest import make_step, make_trace
from tests.orchestration.conftest import FIXTURES_DIR, SuiteFakeProvider

TASK_A = "nav-simple-link-01"
TASK_B = "form-fill-basic-01"


def _fake_run_task(recorder: list | None = None):
    """Build a run_task replacement producing a SUCCESS trace per call."""

    async def fake_run_task(task, provider, settings):  # same signature as the real one
        if recorder is not None:
            recorder.append((task.id, provider.name))
        trace = make_trace(
            [make_step(1, "done", {"success": True})],
            RunOutcome.SUCCESS,
            task_id=task.id,
            category=task.category,
            provider_name=provider.name,
        )
        trace.run_id = f"{provider.name}-{task.id}"
        return trace

    return fake_run_task


def _two_providers() -> dict:
    return {
        "fake-a": SuiteFakeProvider("fake-a"),
        "fake-b": SuiteFakeProvider("fake-b"),
    }


def _subset_config(**overrides) -> SuiteRunConfig:
    defaults = {"task_ids": [TASK_A, TASK_B], "provider_names": ["fake-a", "fake-b"]}
    defaults.update(overrides)
    return SuiteRunConfig(**defaults)


class TestBasicSuiteRun:
    async def test_runs_every_task_provider_combination(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder: list[tuple[str, str]] = []
        monkeypatch.setattr(suite_runner_module, "run_task", _fake_run_task(recorder))

        result = await run_suite(_subset_config(), _two_providers(), suite_settings)

        assert len(result.traces) == 4  # 2 tasks x 2 providers
        assert {(t.task_id, t.provider_name) for t in result.traces} == {
            (TASK_A, "fake-a"), (TASK_A, "fake-b"),
            (TASK_B, "fake-a"), (TASK_B, "fake-b"),
        }
        assert recorder == [
            (TASK_A, "fake-a"), (TASK_A, "fake-b"),
            (TASK_B, "fake-a"), (TASK_B, "fake-b"),
        ]

    async def test_metrics_computed_for_each_provider(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suite_runner_module, "run_task", _fake_run_task())

        result = await run_suite(_subset_config(), _two_providers(), suite_settings)

        assert set(result.metrics_by_provider) == {"fake-a", "fake-b"}
        for name in ("fake-a", "fake-b"):
            metrics = result.metrics_by_provider[name]
            assert isinstance(metrics.provider_name, str) and metrics.provider_name == name
            assert metrics.total_tasks == 2
            assert metrics.success_rate == pytest.approx(1.0)

    async def test_result_round_trips_through_disk(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(suite_runner_module, "run_task", _fake_run_task())

        result = await run_suite(_subset_config(), _two_providers(), suite_settings)

        loaded = load_suite_run(suite_settings.results_dir, result.suite_run_id)
        assert len(loaded.traces) == 4
        assert loaded.config == result.config
        assert set(loaded.metrics_by_provider) == {"fake-a", "fake-b"}


class TestMaxConcurrent:
    async def test_max_concurrent_one_keeps_strict_sequential_order(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default (1) preserves the original sequential contract exactly."""
        recorder: list[tuple[str, str]] = []
        monkeypatch.setattr(suite_runner_module, "run_task", _fake_run_task(recorder))

        result = await run_suite(_subset_config(), _two_providers(), suite_settings)

        assert len(result.traces) == 4
        assert recorder == [
            (TASK_A, "fake-a"), (TASK_A, "fake-b"),
            (TASK_B, "fake-a"), (TASK_B, "fake-b"),
        ]

    async def test_zero_or_negative_max_concurrent_is_rejected_by_validation(
        self,
    ) -> None:
        with pytest.raises(ValueError):
            SuiteRunConfig(task_ids=[TASK_A], provider_names=["fake-a"],
                           max_concurrent=0)


class TestParallelExecution:
    """max_concurrent > 1: same results as sequential, safe concurrent saves."""

    async def test_parallel_run_yields_same_result_set_as_sequential(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Aspect (а): parallelism changes the ORDER, never the SET of results.

        The fake run_task is deterministic per combination, so both modes must
        produce identical traces modulo list order.
        """
        base = _fake_run_task()

        async def slightly_slow_run_task(task, provider, settings):
            # Yield control so the event loop actually interleaves combos.
            await asyncio.sleep(0)
            return await base(task, provider, settings)

        monkeypatch.setattr(suite_runner_module, "run_task", slightly_slow_run_task)

        sequential = await run_suite(_subset_config(), _two_providers(), suite_settings)
        parallel = await run_suite(
            _subset_config(max_concurrent=4), _two_providers(), suite_settings
        )

        def _key(t):
            return (t.task_id, t.provider_name, t.run_id)

        assert sorted(parallel.traces, key=_key) == sorted(sequential.traces, key=_key)
        assert parallel.metrics_by_provider.keys() == sequential.metrics_by_provider.keys()
        for name in parallel.metrics_by_provider:
            assert (
                parallel.metrics_by_provider[name].success_rate
                == sequential.metrics_by_provider[name].success_rate
            )

    async def test_semaphore_bounds_in_flight_combinations(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At most max_concurrent combinations may be inside run_task at once."""
        in_flight = 0
        peak = 0
        fake = _fake_run_task()

        async def counting_run_task(task, provider, settings):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.01)
                return await fake(task, provider, settings)
            finally:
                in_flight -= 1

        monkeypatch.setattr(suite_runner_module, "run_task", counting_run_task)

        await run_suite(_subset_config(max_concurrent=2), _two_providers(),
                        suite_settings)

        assert peak == 2  # 4 combinations ran, but never more than 2 at once


    async def test_intermediate_saves_never_lose_or_duplicate_under_race(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Aspect (в): several combinations finishing AT THE SAME TICK must not
        corrupt the shared snapshot — every on-disk write stays valid JSON and
        the final file holds each trace exactly once."""
        snapshot_counts_at_call_start: list[int] = []
        base = _fake_run_task()
        # asyncio.Barrier (3.11+): all four combos line up here and are released
        # together, so their save sections genuinely contend for the snapshot.
        barrier = asyncio.Barrier(4)

        async def synchronized_run_task(task, provider, settings):
            snapshots = list(suite_settings.results_dir.glob("*/suite_run.json"))
            if snapshots:
                data = json.loads(snapshots[0].read_text(encoding="utf-8"))
                snapshot_counts_at_call_start.append(len(data["traces"]))
            else:
                snapshot_counts_at_call_start.append(0)
            async with barrier:
                pass  # released simultaneously -> simultaneous completions
            return await base(task, provider, settings)

        monkeypatch.setattr(suite_runner_module, "run_task", synchronized_run_task)

        result = await run_suite(_subset_config(max_concurrent=4), _two_providers(),
                                 suite_settings)

        final = load_suite_run(suite_settings.results_dir, result.suite_run_id)
        keys = [(t.task_id, t.provider_name) for t in final.traces]
        assert len(keys) == 4
        assert len(set(keys)) == 4  # no duplicates, no losses
        # Every combination saw a parseable snapshot whose trace count never
        # exceeded what had actually finished before it started.
        assert snapshot_counts_at_call_start == [0, 0, 0, 0]

    async def test_parallel_suite_survives_isolated_crashes(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = _fake_run_task()

        async def crashy_run_task(task, provider, settings):
            if task.id == TASK_B and provider.name == "fake-b":
                raise RuntimeError("simulated isolated runner bug")
            return await base(task, provider, settings)

        monkeypatch.setattr(suite_runner_module, "run_task", crashy_run_task)

        result = await run_suite(_subset_config(max_concurrent=4), _two_providers(),
                                 suite_settings)

        assert {(t.task_id, t.provider_name) for t in result.traces} == {
            (TASK_A, "fake-a"), (TASK_A, "fake-b"), (TASK_B, "fake-a"),
        }

    async def test_parallel_run_with_rate_limited_provider_survives_via_retry(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A throttling provider must not sink a parallel suite: bursts of
        simultaneous requests trigger ProviderRateLimitError, and the Phase 2
        RetryingProvider absorbs them without any suite-level changes."""
        from agentalyze.providers.base import (
            ChatMessage,
            CompletionResult,
            ProviderRateLimitError,
        )
        from agentalyze.providers.retry import RetryingProvider, RetryPolicy

        class ThrottledProvider(SuiteFakeProvider):
            """Fails the FIRST TWO chat_completion calls with a simulated 429."""

            def __init__(self) -> None:
                super().__init__("throttled")
                self.calls = 0
                self.rate_limit_hits = 0

            async def chat_completion(self, messages, tools=None, temperature=0.0,
                                      max_tokens=None):
                self.calls += 1
                if self.calls <= 2:
                    self.rate_limit_hits += 1
                    raise ProviderRateLimitError("simulated 429 from the provider")
                return CompletionResult(
                    message=ChatMessage(role="assistant", content="ok"),
                    prompt_tokens=1, completion_tokens=1, total_tokens=2,
                    latency_seconds=0.001, finish_reason="stop",
                )

        inner = ThrottledProvider()
        provider = RetryingProvider(
            inner,
            RetryPolicy(max_attempts=3, initial_wait_seconds=0, multiplier=1.0,
                        max_wait_seconds=0, jitter_seconds=0),
        )
        base = _fake_run_task()

        async def provider_touching_run_task(task, p, settings):
            # Simulate the provider round-trip every real combination makes;
            # an unhandled rate-limit error here would crash the combination.
            await p.chat_completion(messages=[ChatMessage(role="user", content="go")])
            return await base(task, p, settings)

        monkeypatch.setattr(suite_runner_module, "run_task", provider_touching_run_task)

        result = await run_suite(
            _subset_config(max_concurrent=4),
            {"fake-a": provider, "fake-b": provider},
            suite_settings,
        )

        assert inner.rate_limit_hits == 2  # limits actually happened
        assert len(result.traces) == 4  # nothing was lost to the 429s
        assert all(t.outcome is RunOutcome.SUCCESS for t in result.traces)


class TestCrashIsolation:
    async def test_single_crashing_combination_does_not_abort_the_suite(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Aspect (в): a raising combination must not propagate out of run_suite."""
        base = _fake_run_task()

        async def crashy_run_task(task, provider, settings):
            if task.id == TASK_B and provider.name == "fake-b":
                raise RuntimeError("simulated isolated runner bug")
            return await base(task, provider, settings)

        monkeypatch.setattr(suite_runner_module, "run_task", crashy_run_task)

        # No exception escapes run_suite despite one broken combination.
        result = await run_suite(_subset_config(), _two_providers(), suite_settings)

        # 3 of 4 combinations still completed and landed in the result.
        assert {(t.task_id, t.provider_name) for t in result.traces} == {
            (TASK_A, "fake-a"), (TASK_A, "fake-b"), (TASK_B, "fake-a"),
        }
        assert result.metrics_by_provider["fake-a"].total_tasks == 2
        assert result.metrics_by_provider["fake-b"].total_tasks == 1

    async def test_failure_crash_trace_is_kept_and_suite_continues(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Aspects (а)+(б): the PRIMARY Phase 5 spec scenario — run_task itself
        converts an internal runner bug into a FAILURE_CRASH trace; run_suite
        must keep that trace AS IS and finish every remaining combination."""
        base = _fake_run_task()

        async def crashy_run_task(task, provider, settings):
            if task.id == TASK_B and provider.name == "fake-b":
                trace = make_trace(
                    [],
                    RunOutcome.FAILURE_CRASH,
                    task_id=task.id,
                    category=task.category,
                    provider_name=provider.name,
                )
                trace.run_id = f"{provider.name}-{task.id}"
                trace.error = "Traceback ... simulated runner bug"
                return trace  # returned as-is, NOT raised
            return await base(task, provider, settings)

        monkeypatch.setattr(suite_runner_module, "run_task", crashy_run_task)

        result = await run_suite(_subset_config(), _two_providers(), suite_settings)

        # (а) the crashed combination IS in traces with its FAILURE_* outcome.
        assert len(result.traces) == 4
        crashed = [
            t for t in result.traces
            if t.task_id == TASK_B and t.provider_name == "fake-b"
        ]
        assert len(crashed) == 1
        assert crashed[0].outcome is RunOutcome.FAILURE_CRASH
        assert crashed[0].error is not None

        # (б) all other combinations completed successfully around it.
        others = [t for t in result.traces if t not in crashed]
        assert {(t.task_id, t.provider_name) for t in others} == {
            (TASK_A, "fake-a"), (TASK_A, "fake-b"), (TASK_B, "fake-a"),
        }
        assert all(t.outcome is RunOutcome.SUCCESS for t in others)

        # Metrics include the failed run instead of silently dropping it.
        assert result.metrics_by_provider["fake-b"].total_tasks == 2
        assert (
            result.metrics_by_provider["fake-b"].failure_breakdown
            == {RunOutcome.FAILURE_CRASH: 1}
        )



class TestTaskSelection:
    def test_unknown_task_ids_are_an_error(self) -> None:
        config = SuiteRunConfig(task_ids=["no-such-task"], provider_names=["p"])
        with pytest.raises(ValueError, match="unknown task id"):
            select_tasks(config)

    def test_category_filter_narrows_the_selection(self) -> None:
        config = SuiteRunConfig(category_filter=[TaskCategory.FORM_FILL],
                                provider_names=["p"])
        tasks = select_tasks(config)
        assert {task.category for task in tasks} == {TaskCategory.FORM_FILL}

    def test_empty_selection_is_an_error(self) -> None:
        config = SuiteRunConfig(task_ids=["nav-simple-link-01"],
                                category_filter=[TaskCategory.EXTRACTION],
                                provider_names=["p"])
        with pytest.raises(ValueError, match="empty"):
            select_tasks(config)


class TestIncrementalPersistence:
    async def test_snapshot_saved_after_every_completed_combination(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        traces_on_disk_at_call_start: list[int] = []
        base = _fake_run_task()

        async def observing_run_task(task, provider, settings):
            snapshots = list(settings.results_dir.glob("*/suite_run.json"))
            if snapshots:
                data = json.loads(snapshots[0].read_text(encoding="utf-8"))
                traces_on_disk_at_call_start.append(len(data["traces"]))
            else:
                traces_on_disk_at_call_start.append(0)
            return await base(task, provider, settings)

        monkeypatch.setattr(suite_runner_module, "run_task", observing_run_task)

        result = await run_suite(_subset_config(), _two_providers(), suite_settings)

        # Before combo N the file must already hold the results of combos 1..N-1.
        assert traces_on_disk_at_call_start == [0, 1, 2, 3]
        final = load_suite_run(suite_settings.results_dir, result.suite_run_id)
        assert len(final.traces) == 4

    async def test_crash_mid_suite_still_keeps_finished_traces(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point of incremental saving: an interrupted run keeps its history."""
        base = _fake_run_task()

        async def crashy_run_task(task, provider, settings):
            if task.id == TASK_B and provider.name == "fake-a":
                raise RuntimeError("simulated mid-suite crash")
            return await base(task, provider, settings)

        monkeypatch.setattr(suite_runner_module, "run_task", crashy_run_task)
        settings = Settings(fixtures_dir="/unused", results_dir=tmp_path / "results")

        result = await run_suite(
            SuiteRunConfig(task_ids=[TASK_A, TASK_B], provider_names=["fake-a"]),
            {"fake-a": SuiteFakeProvider("fake-a")},
            settings,
        )

        on_disk = load_suite_run(settings.results_dir, result.suite_run_id)
        assert [t.task_id for t in on_disk.traces] == [TASK_A]
        assert on_disk.metrics_by_provider["fake-a"].total_tasks == 1


# ---------------------------------------------------------------------------
# One genuinely end-to-end orchestration run (real Chromium + fixture server,
# deterministic scripted provider — the Phase 3 FakeProvider approach).
# Opt-in only: pytest -m browser
# ---------------------------------------------------------------------------

@pytest.mark.browser
class TestRealBrowserSuiteRun:
    async def test_two_tasks_one_provider_end_to_end(self, tmp_path) -> None:
        import re

        from agentalyze.providers.base import ChatMessage, CompletionResult, ToolCall

        class ScriptedBrowserProvider(SuiteFakeProvider):
            """Same scripted-provider technique as tests/runner/test_react_loop.py;
            a tiny state machine driven by the task and the latest observation."""

            def __init__(self) -> None:
                super().__init__("scripted-browser")
                self._step = 0

            async def chat_completion(self, messages, tools=None, temperature=0.0,
                                      max_tokens=None):
                if "TASK:" in messages[-1].content:
                    self._step = 0  # fresh run_task session
                self._step += 1

                observation = messages[-1].content
                # Detect the dropdown fixture by its URL path, which every
                # observation carries in its PAGE line (the word "dropdown"
                # also occurs in tool descriptions, so scanning all messages
                # would misdetect).
                is_dropdown = "dropdown_menu_02" in observation

                def find_element(fragment: str) -> str:
                    found = re.search(rf'\[(e\d+)\] \w+ "[^"]*{re.escape(fragment)}',
                                      observation)
                    assert found, f"no element {fragment!r} in:\n{observation}"
                    return found.group(1)

                if not is_dropdown:  # nav-simple-link-01: click Docs, then done
                    if self._step == 1:
                        call = ToolCall(id="c-docs", name="click",
                                        arguments={"element_id": find_element("Documentation")})
                    else:
                        call = ToolCall(id="c-done", name="done",
                                        arguments={"success": True})
                else:  # nav-dropdown-menu-02: open menu, pick item, then done
                    if self._step == 1:
                        call = ToolCall(id="c-menu", name="click",
                                        arguments={"element_id": find_element("Products")})
                    elif self._step == 2:
                        call = ToolCall(id="c-item", name="click",
                                        arguments={"element_id": find_element("Analytics Pro")})
                    else:
                        call = ToolCall(id="c-done", name="done",
                                        arguments={"success": True})

                return CompletionResult(
                    message=ChatMessage(role="assistant", content="", tool_calls=[call]),
                    prompt_tokens=10, completion_tokens=5, total_tokens=15,
                    latency_seconds=0.001, finish_reason="tool_calls",
                )

        settings = Settings(fixtures_dir=FIXTURES_DIR, results_dir=tmp_path / "results")
        config = SuiteRunConfig(
            task_ids=["nav-simple-link-01", "nav-dropdown-menu-02"],
            provider_names=["scripted-browser"],
        )

        result = await run_suite(config, {"scripted-browser": ScriptedBrowserProvider()},
                                 settings)

        assert len(result.traces) == 2
        assert all(trace.outcome is RunOutcome.SUCCESS for trace in result.traces)
        metrics = result.metrics_by_provider["scripted-browser"]
        assert metrics.total_tasks == 2
        assert metrics.success_rate == pytest.approx(1.0)

    async def test_parallel_run_does_not_leak_browser_processes(
        self, tmp_path, capsys
    ) -> None:
        """Aspect (б): a bounded-parallel real-browser suite must leave ZERO
        Chromium processes behind — including one deliberately crashing
        combination — exactly like the sequential runner guarantee."""
        import re
        import subprocess

        from agentalyze.providers.base import ChatMessage, CompletionResult, ToolCall

        def count_chromium_processes() -> int:
            proc = subprocess.run(
                ["ps", "-axo", "command"], capture_output=True, text=True, check=True
            )
            return sum(
                1
                for line in proc.stdout.splitlines()
                if "chromium" in line.lower() or "headless_shell" in line.lower()
            )

        class StatelessScriptedProvider(SuiteFakeProvider):
            """Decides each step from the CURRENT observation only — safe to
            share one instance across concurrent combinations (no per-instance
            mutable step counter, unlike the sequential-only provider above)."""

            def __init__(self) -> None:
                super().__init__("scripted-browser")

            async def chat_completion(self, messages, tools=None, temperature=0.0,
                                      max_tokens=None):
                observation = messages[-1].content

                def has_element(fragment: str) -> bool:
                    return re.search(
                        rf'\[(e\d+)\] \w+ "[^"]*{re.escape(fragment)}', observation
                    ) is not None

                def element_id(fragment: str) -> str:
                    found = re.search(
                        rf'\[(e\d+)\] \w+ "[^"]*{re.escape(fragment)}', observation
                    )
                    assert found, f"no element {fragment!r} in:\n{observation}"
                    return found.group(1)

                # Decide from the CURRENT page URL (the `PAGE:` line) plus the
                # click echo in the first line of the tool message — no mutable
                # instance state, safe under concurrent combinations.
                first_line = observation.splitlines()[0] if observation else ""

                def was_clicked(fragment: str) -> bool:
                    # Only a tool-message click echo counts ("Clicked e4 ..."),
                    # never the TASK description text.
                    return (first_line.startswith("Clicked")
                            and fragment in first_line)

                if "dropdown_menu_02" in observation:
                    # Submenu items exist in the DOM only after Products is
                    # clicked; clicks here do NOT navigate, so the click echo
                    # in the first line is the "already did it" signal. Role-
                    # aware matching keeps the target H2 ("heading") apart
                    # from the submenu BUTTON of the same name.
                    analytics_button = re.search(
                        r'\[(e\d+)\] button "[^"]*Analytics Pro"', observation
                    )
                    if (
                        analytics_button is not None
                        and not was_clicked("Analytics Pro")
                    ):
                        call = ToolCall(id="c-item", name="click",
                                        arguments={"element_id":
                                                   analytics_button.group(1)})
                    elif not was_clicked("Products") and analytics_button is None:
                        call = ToolCall(id="c-menu", name="click",
                                        arguments={"element_id":
                                                   element_id("Products")})
                    else:
                        call = ToolCall(id="c-done", name="done",
                                        arguments={"success": True})
                elif has_element("Documentation"):
                    # Only a LINK named Documentation counts as un-navigated;
                    # on the target docs page the same word is a HEADING.
                    eid, role = None, None
                    m = re.search(r'\[(e\d+)\] (\w+) "[^"]*Documentation"',
                                  observation)
                    if m:
                        eid, role = m.group(1), m.group(2)
                    if eid is not None and role == "link":
                        call = ToolCall(id="c-docs", name="click",
                                        arguments={"element_id": eid})
                    else:
                        call = ToolCall(id="c-done", name="done",
                                        arguments={"success": True})
                else:
                    call = ToolCall(id="c-done", name="done",
                                    arguments={"success": True})

                return CompletionResult(
                    message=ChatMessage(role="assistant", content="", tool_calls=[call]),
                    prompt_tokens=10, completion_tokens=5, total_tokens=15,
                    latency_seconds=0.001, finish_reason="tool_calls",
                )

        class ExplodingProvider(SuiteFakeProvider):
            """Simulates a runner-level crash mid-combination (the emergency
            scenario): run_task must convert this into FAILURE_CRASH while its
            own ``finally`` blocks tear down the browser."""

            def __init__(self) -> None:
                super().__init__("exploding-browser")

            async def chat_completion(self, messages, tools=None, temperature=0.0,
                                      max_tokens=None):
                raise RuntimeError("simulated mid-run explosion")

        settings = Settings(fixtures_dir=FIXTURES_DIR, results_dir=tmp_path / "results")
        config = SuiteRunConfig(
            task_ids=["nav-simple-link-01", "nav-dropdown-menu-02"],
            provider_names=["scripted-browser", "exploding-browser"],
            max_concurrent=4,
        )

        await asyncio.sleep(1.0)  # let stragglers from earlier tests exit
        before = count_chromium_processes()

        result = await run_suite(
            config,
            {"scripted-browser": StatelessScriptedProvider(),
             "exploding-browser": ExplodingProvider()},
            settings,
        )

        outcomes = {(t.task_id, t.provider_name): t.outcome for t in result.traces}
        assert len(result.traces) == 4
        # The three healthy combos succeeded; the exploding one became FAILURE_CRASH.
        assert outcomes[("nav-simple-link-01", "scripted-browser")] is RunOutcome.SUCCESS
        assert outcomes[("nav-dropdown-menu-02", "scripted-browser")] is RunOutcome.SUCCESS
        assert outcomes[("nav-simple-link-01", "exploding-browser")] is \
            RunOutcome.FAILURE_CRASH

        await asyncio.sleep(1.5)  # give the OS a moment to reap exited browsers
        after = count_chromium_processes()
        assert after <= before, (
            f"browser process leak: {before} chromium process(es) before the "
            f"parallel run, {after} after"
        )
        capsys.readouterr()  # keep progress noise out of pytest output


class TestEtaExtrapolation:
    def test_format_eta_units(self) -> None:
        assert _format_eta(0) == "0s"
        assert _format_eta(42) == "42s"
        assert _format_eta(185) == "3m 05s"
        assert _format_eta(3600) == "1h 00m"
        assert _format_eta(3725) == "1h 02m"

    async def test_progress_lines_carry_eta_from_second_combination_on(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """The linear extrapolation kicks in once one combination is done."""
        monkeypatch.setattr(suite_runner_module, "run_task", _fake_run_task())

        result = await run_suite(_subset_config(), _two_providers(), suite_settings)
        assert len(result.traces) == 4

        out = capsys.readouterr().out
        assert "[1/4] task=" in out  # nothing finished yet -> no ETA on line 1
        eta_lines = [line for line in out.splitlines() if "(eta ~" in line]
        # Lines 2..4 each carry an estimate; the last one covers exactly one
        # remaining combination (~instant fake tasks -> '~0s').
        assert len(eta_lines) == 3
        assert "[4/4]" in eta_lines[-1]
        assert "~0s" in eta_lines[-1]

