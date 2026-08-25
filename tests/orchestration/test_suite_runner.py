"""Suite-runner orchestration tests (Phase 5, fast, no browser by default).

``run_task`` is replaced with a deterministic fake so these tests exercise
ONLY the orchestration contract: combination count, per-provider metrics,
the max_concurrent guard, crash isolation and incremental persistence.
"""

from __future__ import annotations

import json

import pytest

import agentalyze.orchestration.suite_runner as suite_runner_module
from agentalyze.config import Settings
from agentalyze.orchestration.suite_runner import (
    SuiteRunConfig,
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


class TestMaxConcurrentGuard:
    async def test_max_concurrent_gt_one_is_loudly_rejected(
        self, suite_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder: list = []
        monkeypatch.setattr(suite_runner_module, "run_task", _fake_run_task(recorder))
        config = _subset_config(max_concurrent=4)

        with pytest.raises(ValueError, match="max_concurrent=4.*not supported"):
            await run_suite(config, _two_providers(), suite_settings)

        assert recorder == []  # nothing ran — the value was NOT silently coerced to 1


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

