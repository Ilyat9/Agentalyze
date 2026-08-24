"""Project configuration for Agentalyze.

A single ``Settings`` model loaded from environment variables with the
``AGENTALYZE_`` prefix (for example ``AGENTALYZE_LOG_LEVEL``), with sensible
defaults so the package works out of the box.

Intentionally minimal: fields about LLM providers, task suites, Docker, etc.
will be added in later phases when their context is known.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Global settings for Agentalyze.

    Values are read from environment variables prefixed with ``AGENTALYZE_``
    (e.g. ``AGENTALYZE_LOG_LEVEL=DEBUG``), falling back to an optional ``.env``
    file in the current directory, then to the defaults below.
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENTALYZE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    fixtures_dir: Path = Field(
        default=Path("./fixtures"),
        description=(
            "Directory with local HTML fixtures for agent tasks "
            "(relative paths resolve against the process working directory, "
            "normally the project root)."
        ),
    )
    results_dir: Path = Field(
        default=Path("./results"),
        description="Directory where run artifacts are written.",
    )
    log_level: LogLevel = Field(
        default="INFO",
        description="Logging level. Case-insensitive, normalized to upper case.",
    )

    @field_validator("fixtures_dir", "results_dir", mode="after")
    @classmethod
    def _expand_path(cls, value: Path) -> Path:
        return Path(value).expanduser()

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value

    def ensure_results_dir(self) -> Path:
        """Create ``results_dir`` if it does not exist and return it."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        return self.results_dir

