"""Filesystem -> DB migration script: real artifacts, idempotent re-runs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from agentalyze.config import Settings
from agentalyze.orchestration.suite_runner import save_suite_run
from tests.api.conftest import make_fake_run_result


def _find_real_suite_run() -> Path | None:
    """A real run artifact from earlier sessions of THIS repo, if present."""
    results = Path(__file__).resolve().parents[2] / "results"
    if not results.is_dir():
        return None
    for candidate in sorted(results.glob("*/suite_run.json")):
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            json.loads(json.dumps(raw.get("config", {})))
            return candidate
        except (json.JSONDecodeError, OSError):
            continue
    return None


class TestResultsMigration:
    def test_migrates_synthetic_run(self, tmp_path: Path) -> None:
        settings = Settings(
            results_dir=tmp_path / "results",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'm.db'}",
            log_level="WARNING",
        )
        settings.results_dir.mkdir(parents=True)
        result = make_fake_run_result("11111111-2222-3333-4444-555555555555", settings)
        save_suite_run(result, settings.results_dir)

        script = Path(__file__).resolve().parents[2] / "scripts" / "migrate_results_to_db.py"
        env = {**dict(__import__("os").environ),
               "AGENTALYZE_DATABASE_URL": settings.database_url,
               "PYTHONPATH": str(script.parents[1] / "src")}
        first = subprocess.run(
            [sys.executable, str(script), "--results-dir", str(settings.results_dir)],
            capture_output=True, text=True, env=env, check=False,
        )
        assert first.returncode == 0, first.stderr
        assert "IMPORTED" in first.stdout

        # Second run must be a no-op (idempotence).
        second = subprocess.run(
            [sys.executable, str(script), "--results-dir", str(settings.results_dir)],
            capture_output=True, text=True, env=env, check=False,
        )
        assert second.returncode == 0, second.stderr
        assert "IMPORTED" not in second.stdout

    def test_migrates_real_historical_artifact(self, tmp_path: Path) -> None:
        """Uses an actual suite_run.json produced by earlier sessions."""
        real = _find_real_suite_run()
        if real is None:
            __import__("pytest").skip("no historical results/ artifacts in checkout")
        settings = Settings(
            results_dir=tmp_path / "results",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'm.db'}",
            log_level="WARNING",
        )
        shutil.copytree(real.parent, settings.results_dir / real.parent.name)

        script = Path(__file__).resolve().parents[2] / "scripts" / "migrate_results_to_db.py"
        env = {**dict(__import__("os").environ),
               "AGENTALYZE_DATABASE_URL": settings.database_url,
               "PYTHONPATH": str(script.parents[1] / "src")}
        proc = subprocess.run(
            [sys.executable, str(script), "--results-dir", str(settings.results_dir)],
            capture_output=True, text=True, env=env, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert "IMPORTED" in proc.stdout
