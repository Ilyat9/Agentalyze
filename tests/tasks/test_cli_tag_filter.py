"""CLI tests for the failure-mode tag index of `agentalyze tasks`.

The `--tag` flag filters the flat task list down to tasks whose structured
``expected_failure_modes`` field declares the given FailureTag — the
machine-readable counterpart of the registry's "Reveals: …" comments.
"""

from __future__ import annotations

import pytest

from agentalyze.analysis.failure_taxonomy import FailureTag
from agentalyze.runner.cli import main
from agentalyze.tasks.registry import TASKS


def test_tasks_without_tag_lists_everything(capsys) -> None:
    assert main(["tasks"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{len(TASKS)} registered task(s):")
    for task in TASKS:
        assert task.id in out


def test_tag_filter_shows_only_annotated_tasks(capsys) -> None:
    expected = {t.id for t in TASKS if FailureTag.LOOPING in t.expected_failure_modes}
    assert expected, "sanity: at least one task must expect looping"

    assert main(["tasks", "--tag", "looping"]) == 0
    shown = capsys.readouterr().out.splitlines()
    assert shown[0] == f"{len(expected)} registered task(s) matching tag=looping:"
    listed = {line.strip().split()[0] for line in shown[1:] if line.strip()}
    assert listed == expected


def test_every_registered_task_declares_failure_modes() -> None:
    unannotated = [t.id for t in TASKS if not t.expected_failure_modes]
    assert not unannotated, f"tasks without expected_failure_modes: {unannotated}"


def test_unknown_tag_is_a_usage_error(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["tasks", "--tag", "not-a-real-tag"])
    assert excinfo.value.code == 2