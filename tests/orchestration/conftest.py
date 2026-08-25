"""Shared fixtures/helpers for the Phase 5 orchestration tests.

The unit tests here deliberately do NOT touch a browser: ``run_suite``'s
contract is orchestration (how many combinations ran, crash isolation,
incremental persistence), while the single-run core ``run_task`` is already
covered end-to-end by the Phase 3 browser tests. Unit tests swap
``agentalyze.orchestration.suite_runner.run_task`` for a deterministic fake
and hand-built :class:`RunTrace` objects (reusing the Phase 4 trace
factories from ``tests.analysis.conftest``), which keeps them fast and part
of the default pytest run. One genuinely end-to-end test (real Chromium +
scripted provider) lives in ``test_suite_runner.py`` under the ``browser``
marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentalyze.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures"


class SuiteFakeProvider:
    """Deterministic Provider stand-in, adapted from the Phase 3 runner-test
    ``FakeProvider`` (same structural contract, trimmed to what the
    orchestrator actually touches: ``name`` + ``health_check``)."""

    def __init__(self, name: str = "fake-provider") -> None:
        self.name = name

    async def chat_completion(self, messages, tools=None, temperature=0.0,
                              max_tokens=None):  # pragma: no cover - never called
        raise AssertionError("orchestration unit tests must not reach chat_completion")

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def suite_settings(tmp_path: Path) -> Settings:
    """Settings with an isolated results dir (fixtures are never touched)."""
    return Settings(fixtures_dir=FIXTURES_DIR, results_dir=tmp_path / "results")
