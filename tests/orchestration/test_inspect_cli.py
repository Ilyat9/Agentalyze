"""`agentalyze inspect` tests: tag/outcome filtering over a known suite run."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentalyze.orchestration.suite_runner import SuiteRunConfig, SuiteRunResult, save_suite_run
from agentalyze.runner.cli import main
from agentalyze.runner.trace import RunOutcome
from agentalyze.tasks.models import TaskCategory
from tests.analysis.conftest import make_step, make_trace

SUITE_RUN_ID = "sr-inspect-test-001"
LOOPING_RUN = "run-looping-001"
OK_RUN = "run-ok-001"
PROVIDER_ERR_RUN = "run-proverr-001"


def _build_result() -> SuiteRunResult:
    looping_trace = make_trace(
        [
            *[make_step(n, "click", {"element_id": "e1"}) for n in (1, 2, 3)],
            make_step(4, "done", {"success": False}),
        ],
        RunOutcome.FAILURE_VERIFIER,
        task_id="t-looping",
        category=TaskCategory.FORM_FILL,
        provider_name="provider-a",
        verifier_success=False,
    )
    ok_trace = make_trace(
        [make_step(1, "done", {"success": True})],
        RunOutcome.SUCCESS,
        task_id="t-ok",
        category=TaskCategory.NAVIGATION,
        provider_name="provider-a",
        verifier_success=True,
    )
    provider_err_trace = make_trace(
        [],
        RunOutcome.FAILURE_PROVIDER_ERROR,
        task_id="t-proverr",
        category=TaskCategory.NAVIGATION,
        provider_name="provider-b",
    )
    looping_trace.run_id, ok_trace.run_id = LOOPING_RUN, OK_RUN
    provider_err_trace.run_id = PROVIDER_ERR_RUN

    return SuiteRunResult(
        suite_run_id=SUITE_RUN_ID,
        started_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        finished_at=datetime(2026, 8, 25, 12, 1, tzinfo=UTC),
        config=SuiteRunConfig(provider_names=["provider-a", "provider-b"]),
        traces=[looping_trace, ok_trace, provider_err_trace],
    )


@pytest.fixture
def results_dir(tmp_path):
    save_suite_run(_build_result(), tmp_path)
    return tmp_path


def _run_inspect(monkeypatch, results_dir, capsys, *argv):
    monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", str(results_dir))
    code = main(["inspect", *argv])
    return code, capsys.readouterr()


class TestInspectByTag:
    def test_finds_only_looping_traces(self, monkeypatch, results_dir, capsys) -> None:
        code, out = _run_inspect(monkeypatch, results_dir, capsys,
                                 "--suite-run", SUITE_RUN_ID, "--tag", "looping")

        assert code == 0
        stdout = out.out
        assert LOOPING_RUN in stdout
        assert OK_RUN not in stdout and PROVIDER_ERR_RUN not in stdout
        assert "task=t-looping" in stdout and "steps=4" in stdout
        assert f"{results_dir}/{LOOPING_RUN}/" in stdout  # path to open manually

    def test_unknown_tag_is_a_clean_error(self, monkeypatch, results_dir, capsys) -> None:
        code, out = _run_inspect(monkeypatch, results_dir, capsys,
                                 "--suite-run", SUITE_RUN_ID, "--tag", "nonsense")
        assert code == 2
        assert "unknown failure tag" in out.err


class TestInspectByOutcome:
    def test_finds_only_matching_outcome(self, monkeypatch, results_dir, capsys) -> None:
        code, out = _run_inspect(monkeypatch, results_dir, capsys,
                                 "--suite-run", SUITE_RUN_ID,
                                 "--outcome", "failure_verifier")

        assert code == 0
        assert LOOPING_RUN in out.out
        assert OK_RUN not in out.out and PROVIDER_ERR_RUN not in out.out

    def test_no_matches_prints_a_helpful_message(self, monkeypatch, results_dir,
                                                 capsys) -> None:
        code, out = _run_inspect(monkeypatch, results_dir, capsys,
                                 "--suite-run", SUITE_RUN_ID,
                                 "--outcome", "failure_tool_error")
        assert code == 0
        assert "Nothing matched" in out.out


class TestInspectErrors:
    def test_unknown_suite_run_is_a_clean_error(self, monkeypatch, results_dir,
                                                capsys) -> None:
        code, out = _run_inspect(monkeypatch, results_dir, capsys,
                                 "--suite-run", "does-not-exist")
        assert code == 2
        assert "no suite run 'does-not-exist'" in out.err

    def test_unfiltered_listing_shows_everything(self, monkeypatch, results_dir,
                                                 capsys) -> None:
        code, out = _run_inspect(monkeypatch, results_dir, capsys,
                                 "--suite-run", SUITE_RUN_ID)
        assert code == 0
        assert "3/3 trace(s)" in out.out
