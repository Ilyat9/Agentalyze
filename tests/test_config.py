"""Tests for agentalyze.config."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentalyze.config import Settings


@pytest.fixture(autouse=True)
def _clean_agentalyze_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure no AGENTALYZE_* variables leak in from the outer environment."""
    for var in ("AGENTALYZE_FIXTURES_DIR", "AGENTALYZE_RESULTS_DIR", "AGENTALYZE_LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)


class TestDefaults:
    def test_defaults_are_loaded(self) -> None:
        settings = Settings(_env_file=None)

        assert settings.fixtures_dir == Path("./fixtures")
        assert settings.results_dir == Path("./results")
        assert settings.log_level == "INFO"


class TestLogLevelValidation:
    def test_valid_log_level_accepted(self) -> None:
        settings = Settings(log_level="DEBUG", _env_file=None)

        assert settings.log_level == "DEBUG"

    def test_log_level_is_normalized_to_upper_case(self) -> None:
        settings = Settings(log_level="warning", _env_file=None)

        assert settings.log_level == "WARNING"

    @pytest.mark.parametrize("bad_value", ["TRACE", "verbose", "", 42])
    def test_invalid_log_level_rejected(self, bad_value: object) -> None:
        with pytest.raises(ValidationError):
            Settings(log_level=bad_value, _env_file=None)


class TestPathResolution:
    def test_paths_are_resolved_as_path_objects(self) -> None:
        settings = Settings(fixtures_dir="./fixtures", results_dir="./results", _env_file=None)

        assert isinstance(settings.fixtures_dir, Path)
        assert isinstance(settings.results_dir, Path)
        assert not settings.fixtures_dir.is_absolute()
        assert not settings.results_dir.is_absolute()

    def test_absolute_paths_and_tilde_are_expanded(self) -> None:
        settings = Settings(
            fixtures_dir="~/agentalyze-fixtures",
            results_dir="/tmp/agentalyze-results",
            _env_file=None,
        )

        assert settings.fixtures_dir == Path("~/agentalyze-fixtures").expanduser()
        assert settings.results_dir == Path("/tmp/agentalyze-results")

    def test_ensure_results_dir_creates_missing_directory(self, tmp_path: Path) -> None:
        results = tmp_path / "nested" / "results"
        settings = Settings(results_dir=results, _env_file=None)

        created = settings.ensure_results_dir()

        assert created == results
        assert results.is_dir()


class TestEnvOverrides:
    def test_env_variables_override_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTALYZE_FIXTURES_DIR", "/tmp/env-fixtures")
        monkeypatch.setenv("AGENTALYZE_RESULTS_DIR", "/tmp/env-results")
        monkeypatch.setenv("AGENTALYZE_LOG_LEVEL", "error")

        settings = Settings(_env_file=None)

        assert settings.fixtures_dir == Path("/tmp/env-fixtures")
        assert settings.results_dir == Path("/tmp/env-results")
        assert settings.log_level == "ERROR"
