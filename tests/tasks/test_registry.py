"""Consistency checks on the task registry itself (no browser needed)."""

from __future__ import annotations

from pathlib import Path

from agentalyze.tasks.models import TaskCategory
from agentalyze.tasks.reference import REFERENCE
from agentalyze.tasks.registry import TASKS, TASKS_BY_ID
from agentalyze.tasks.verifiers import VERIFIERS

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"


def test_suite_has_between_15_and_20_tasks() -> None:
    assert 15 <= len(TASKS) <= 20


def test_task_ids_are_unique_and_kebab_case() -> None:
    ids = [task.id for task in TASKS]
    assert len(ids) == len(set(ids)), "duplicate task ids found"
    assert set(ids) == set(TASKS_BY_ID), "TASKS_BY_ID is out of sync with TASKS"
    assert all(task.id == task.id.lower() for task in TASKS)


def test_every_category_has_at_least_two_tasks() -> None:
    counts = {category: 0 for category in TaskCategory}
    for task in TASKS:
        counts[task.category] += 1
    for category, count in counts.items():
        assert count >= 2, f"category {category} has only {count} task(s)"


def test_all_verifier_ids_resolve_in_registry() -> None:
    missing = [task.verifier_id for task in TASKS if task.verifier_id not in VERIFIERS]
    assert not missing, f"unresolvable verifier_ids: {missing}"


def test_all_fixture_files_exist() -> None:
    for task in TASKS:
        path = FIXTURES_DIR / task.fixture_path
        assert path.is_file(), f"{task.id}: fixture file missing: {path}"


def test_fixture_url_path_matches_fixture_path() -> None:
    for task in TASKS:
        expected = "/" + task.fixture_path.replace("\\", "/")
        assert task.fixture_url_path == expected, (
            f"{task.id}: fixture_url_path {task.fixture_url_path!r} != {expected!r}"
        )


def test_reference_data_covers_every_task() -> None:
    """Every registered task must have programmatic validation reference data."""
    without_reference = [task.id for task in TASKS if task.id not in REFERENCE]
    assert not without_reference, f"tasks without reference data: {without_reference}"
    unknown = set(REFERENCE) - set(TASKS_BY_ID)
    assert not unknown, f"reference data for unregistered tasks: {unknown}"


def test_difficulties_progress_within_each_category() -> None:
    """Each category must span at least two difficulty levels (easy -> hard ramp)."""
    by_category: dict[TaskCategory, set[str]] = {}
    for task in TASKS:
        by_category.setdefault(task.category, set()).add(task.difficulty)
    for category, levels in by_category.items():
        assert len(levels) >= 2, f"category {category} has no difficulty progression"
