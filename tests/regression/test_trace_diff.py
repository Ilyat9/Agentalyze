"""Tests for the structural trace diff (`inspect --diff-trace`).

Pure-comparison tests use the hand-built StepEvent factories; CLI tests
persist real trace.json files and call `agentalyze.runner.cli.main`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentalyze.config import Settings
from agentalyze.orchestration.cli import cmd_inspect
from agentalyze.regression.trace_diff import compare_traces, render_trace_diff
from agentalyze.runner.cli import main
from agentalyze.runner.trace import RunOutcome, save_trace
from tests.analysis.conftest import make_step, make_trace


def _trace(run_id: str, steps: list, *, task_id: str = "task-01",
           provider: str = "provider-a", outcome: RunOutcome = RunOutcome.SUCCESS):
    trace = make_trace(steps, outcome, task_id=task_id, provider_name=provider)
    trace.run_id = run_id
    return trace


class TestCompareTraces:
    def test_identical_sequences_have_no_divergence(self) -> None:
        steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "done", {"success": True}, dom_hash="h2"),
        ]
        diff = compare_traces(_trace("base", steps), _trace("new", list(steps)))

        assert diff.first_divergent_step is None
        assert diff.steps_only_in_baseline == []
        assert diff.steps_only_in_new == []
        assert all(not c.tool_changed for c in diff.comparisons)
        assert all(not c.dom_state_diverged for c in diff.comparisons)

    def test_tool_change_at_step_two_is_reported_with_exact_location(self) -> None:
        base_steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "type_text", {"element_id": "e2", "text": "hi"},
                      dom_hash="h2"),
        ]
        new_steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            # The regression: same step number, DIFFERENT tool chosen.
            make_step(2, "click", {"element_id": "e9"}, dom_hash="h9",
                      tool_success=False),
        ]
        diff = compare_traces(_trace("base", base_steps), _trace("new", new_steps))

        assert diff.first_divergent_step == 2  # NOT step 1
        cmp_1, cmp_2 = diff.comparisons
        assert not cmp_1.tool_changed and not cmp_1.dom_state_diverged
        assert cmp_2.tool_changed is True
        assert cmp_2.baseline_tool_name == "type_text"
        assert cmp_2.new_tool_name == "click"
        # The wrong tool also FAILED (success flip detected independently).
        assert cmp_2.tool_result_changed is True
        assert cmp_2.dom_state_diverged is True  # h2 != h9 -> different page state

    def test_different_lengths_report_tail_steps_explicitly(self) -> None:
        base_steps = [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "click", {"element_id": "e2"}, dom_hash="h2"),
            make_step(3, "done", {"success": True}, dom_hash="h3"),
        ]
        new_steps = [make_step(1, "click", {"element_id": "e1"}, dom_hash="h1")]
        diff = compare_traces(_trace("base", base_steps), _trace("new", new_steps))

        assert diff.first_divergent_step is None  # common prefix matches
        assert diff.steps_only_in_baseline == [2, 3]
        assert diff.steps_only_in_new == []

    def test_tool_result_failure_flip_is_detected(self) -> None:
        base_steps = [make_step(1, "submit_form", {"element_id": "e5"},
                                tool_success=True, dom_hash="h1")]
        new_steps = [make_step(1, "submit_form", {"element_id": "e5"},
                               tool_success=False, dom_hash="h1")]
        diff = compare_traces(_trace("base", base_steps), _trace("new", new_steps))

        assert diff.comparisons[0].tool_result_changed is True
        assert diff.comparisons[0].dom_state_diverged is False
        assert diff.first_divergent_step == 1

    def test_missing_dom_hashes_do_not_assert_anything(self) -> None:
        steps = [make_step(1, "click", {"element_id": "e1"}, dom_hash=None)]
        diff = compare_traces(_trace("base", steps), _trace("new", list(steps)))

        assert diff.comparisons[0].dom_state_diverged is None

    def test_different_task_or_provider_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="SAME \\(task, provider\\)"):
            compare_traces(
                _trace("base", [make_step(1, "click")]),
                _trace("new", [make_step(1, "click")], task_id="other-task"),
            )


class TestRenderTraceDiff:
    def test_render_names_the_first_divergence_and_tails(self) -> None:
        base = _trace("run-base", [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "type_text", {}, dom_hash="h2"),
            make_step(3, "done", {}, dom_hash="h3"),
        ])
        new = _trace("run-new", [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "click", {}, dom_hash="zz"),
        ])

        text = render_trace_diff(compare_traces(base, new))

        assert "task=task-01" in text and "provider=provider-a" in text
        assert "step 2: type_text -> click" in text
        assert "TOOL CHANGED" in text and "DOM STATE DIVERGED" in text
        assert "Steps only in BASELINE" in text and "3" in text
        assert "FIRST DIVERGENCE at step 2" in text

    def test_identical_traces_render_a_clean_verdict(self) -> None:
        steps = [make_step(1, "done", {}, dom_hash="h1")]
        text = render_trace_diff(compare_traces(_trace("b", steps),
                                                _trace("n", steps)))
        assert "No structural divergence found" in text

# --- CLI tests appended below ---


class TestDiffTraceCLI:
    def _settings(self, tmp_path: Path) -> Settings:
        return Settings(fixtures_dir="/unused", results_dir=tmp_path / "results")

    def _save_trace(self, tmp_path: Path, trace) -> None:
        save_trace(trace, tmp_path / "results")

    @staticmethod
    def _run_inspect(settings: Settings, *extra: str) -> int:
        argv = ["inspect", *extra, "--results-dir", str(settings.results_dir)]
        return main(argv)

    def test_diff_trace_prints_report_and_saves_to_output(
        self, tmp_path: Path, capsys
    ) -> None:
        settings = self._settings(tmp_path)
        base = _trace("run-base", [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "type_text", {}, dom_hash="h2"),
        ])
        new = _trace("run-new", [
            make_step(1, "click", {"element_id": "e1"}, dom_hash="h1"),
            make_step(2, "click", {}, dom_hash="zz"),
        ])
        self._save_trace(tmp_path, base)
        self._save_trace(tmp_path, new)
        out_file = tmp_path / "report.txt"

        code = self._run_inspect(
            settings, "--diff-trace", "run-base", "run-new",
            "--output", str(out_file),
        )

        assert code == 0  # reporting operation, never a gate
        out = capsys.readouterr().out
        assert "FIRST DIVERGENCE at step 2" in out
        saved = out_file.read_text(encoding="utf-8")
        assert "FIRST DIVERGENCE at step 2" in saved  # same content on disk

    def test_missing_trace_is_a_friendly_error_exit_2(
        self, tmp_path: Path, capsys
    ) -> None:
        settings = self._settings(tmp_path)

        code = self._run_inspect(settings, "--diff-trace", "no-such-run", "also-missing")

        assert code == 2
        err = capsys.readouterr().err
        assert "no trace found for run 'no-such-run'" in err

    def test_mismatched_pairs_exit_2_without_traceback(
        self, tmp_path: Path, capsys
    ) -> None:
        settings = self._settings(tmp_path)
        self._save_trace(tmp_path, _trace("base", [make_step(1, "click")]))
        self._save_trace(
            tmp_path,
            _trace("new", [make_step(1, "click")], provider="provider-b"),
        )

        code = self._run_inspect(settings, "--diff-trace", "base", "new")

        assert code == 2
        assert "SAME (task, provider)" in capsys.readouterr().err

    def test_suite_run_listing_still_works_without_diff_trace(self, tmp_path) -> None:
        """The original `inspect --suite-run` path is untouched by the new mode."""
        from datetime import UTC, datetime

        from agentalyze.orchestration.suite_runner import (
            SuiteRunConfig,
            SuiteRunResult,
            save_suite_run,
        )

        settings = self._settings(tmp_path)
        trace = _trace("run-1", [make_step(1, "done")])
        result = SuiteRunResult(
            suite_run_id="suite-x",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            config=SuiteRunConfig(provider_names=["provider-a"]),
            traces=[trace],
        )
        save_suite_run(result, settings.results_dir)

        args = None  # cmd_inspect needs a Namespace; build a minimal one
        import argparse

        args = argparse.Namespace(suite_run="suite-x", tag=None, outcome=None,
                                  diff_trace=None, output=None)
        code = cmd_inspect(args, settings)
        assert code == 0
