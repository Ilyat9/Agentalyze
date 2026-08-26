"""Project configuration for Agentalyze.

A single ``Settings`` model loaded from environment variables with the
``AGENTALYZE_`` prefix (for example ``AGENTALYZE_LOG_LEVEL``), with sensible
defaults so the package works out of the box.

Intentionally minimal: fields are added phase by phase when their context is
known (``providers_config_path`` appeared in Phase 2 together with the
provider layer).
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
    providers_config_path: Path = Field(
        default=Path("./providers.yaml"),
        description=(
            "Path to the named-providers YAML file (see providers.example.yaml). "
            "The file itself contains no secrets — only names of environment "
            "variables holding API keys. Loading is done by "
            "agentalyze.providers.factory.load_providers."
        ),
    )
    regression_config_path: Path = Field(
        default=Path("./regression.yaml"),
        description=(
            "Optional per-task regression-gate configuration (see "
            "regression.example.yaml). A missing file means 'no exclusions' — "
            "the regression-check gate behaves exactly as without this setting."
        ),
    )

    # --- Service mode (HTTP API). These fields are inert for pure CLI usage ---
    database_url: str = Field(
        default="sqlite+aiosqlite:///./agentalyze.db",
        description=(
            "Async SQLAlchemy URL for service-mode metadata (suite-run registry, "
            "API keys, baseline pointers). SQLite is the deliberate default for "
            "single-host/self-hosted deployments; use a postgresql+asyncpg:// URL "
            "when more than one process writes concurrently."
        ),
    )
    log_format: Literal["console", "json"] = Field(
        default="console",
        description=(
            "Log rendering: 'console' (human-readable, the historical behavior) "
            "or 'json' (structured one-object-per-line logs for aggregation)."
        ),
    )
    #: Hard cap on suite runs executing at the same time inside ONE server
    #: process. Each in-flight combination holds a real Chromium instance, so
    #: this protects memory/CPU budget from monopolization even for
    #: authenticated clients that pass the request-rate limit.
    max_active_suite_runs: int = Field(default=2, ge=1)
    #: Request-rate limit for POST /runs per API key ('N per second/minute'),
    #: enforced by slowapi. 'none' disables the limit (not recommended).
    api_runs_rate_limit: str = Field(default="5 per minute")
    #: When true, POST /runs requires Authorization: Bearer <api key>. Service
    #: mode defaults it to True; the flag exists so local development can opt out.
    api_auth_required: bool = Field(default=True)
    #: Base URL of a HashiCorp Vault instance for secret resolution. Empty means
    #'env vars only' — the historical behavior and the default.
    vault_addr: str = Field(default="")
    vault_token_env_var: str = Field(default="VAULT_TOKEN")
    vault_kv_mount: str = Field(default="secret")


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
