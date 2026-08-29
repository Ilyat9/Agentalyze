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

    #: Isolation for the code-agent runner's (agentalyze.runner.code_agent)
    #: generated-code execution: smolagents' 'docker'/'local'/'e2b'/'modal'/
    #: 'blaxel' executor types. Default is 'local' — NOT 'docker' — because
    #: 'docker'/'e2b'/'modal'/'blaxel' are all "remote executor" modes in
    #: smolagents, and remote executors reconstruct each Tool via a bare
    #: `ToolClassName()` call inside the sandbox (see
    #: smolagents.tools.get_tools_definition_code); this project's tool
    #: adapters require `(ctx, recorder)` constructor arguments (a live
    #: Playwright Page + the owning event loop cannot be serialized into a
    #: sandbox), so every remote executor mode fails for THIS project's
    #: tools (verified empirically: it does not raise, it silently hangs
    #: until the task's wall-clock timeout — code_agent/loop.py now fails
    #: fast instead). See docs/KNOWN_LIMITATIONS.md. 'local' is NOT a
    #: security boundary either (smolagents' own LocalPythonExecutor
    #: docstring says so) — the runner logs a warning whenever it is used,
    #: and it must only be driven by a Provider (like FakeProvider in tests)
    #: that never generates untrusted real code.
    code_agent_executor_type: Literal["local", "docker", "e2b", "modal", "blaxel"] = Field(
        default="local",
    )

    # --- Public demo mode (src/agentalyze/demo). Inert unless explicitly ------
    # --- enabled: regular self-hosted `agentalyze serve` deployments must ------
    # --- never gain this (BYOK-key-accepting) API surface by accident. ---------
    demo_mode_enabled: bool = Field(
        default=False,
        description=(
            "Enable the public BYOK demo endpoints (GET /demo, POST /demo/run). "
            "OFF by default — this surface accepts provider API keys from "
            "anonymous visitors and must only be enabled on a dedicated demo "
            "deployment (see docs/DEMO_DEPLOYMENT.md)."
        ),
    )
    demo_https_required: bool = Field(
        default=True,
        description=(
            "When true, POST /demo/run refuses requests that did not arrive "
            "over HTTPS (checked via X-Forwarded-Proto set by the hosting "
            "platform's TLS terminator; localhost is always allowed for dev)."
        ),
    )
    demo_rate_limit: str = Field(
        default="3 per hour",
        description=(
            "Request-rate limit for POST /demo/run per client IP (slowapi), "
            "e.g. '3 per hour'. 'none' disables it (strongly discouraged for "
            "a public, unauthenticated endpoint)."
        ),
    )
    demo_run_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        description=(
            "Hard wall-clock budget for ONE demo run, enforced from the HTTP "
            "handler with asyncio.wait_for. The response never hangs longer "
            "than this, whatever the task/browser/provider do."
        ),
    )
    demo_provider_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        description=(
            "Per-completion timeout for the demo-built provider. Deliberately "
            "shorter than the eval default: a demo run must fail fast against "
            "a slow/broken endpoint instead of burning the visitor's budget."
        ),
    )
    demo_max_concurrent_runs: int = Field(
        default=1,
        ge=1,
        description=(
            "How many demo runs may hold a real Chromium at the same time "
            "inside this process. Excess requests get an honest 503."
        ),
    )
    chromium_launch_args: str = Field(
        default="",
        description=(
            "Comma-separated extra Chromium launch args applied by the runner "
            "(react_loop) to shrink headless-browser memory/CPU usage on "
            "small hosting tiers, e.g. "
            "'--disable-dev-shm-usage,--disable-gpu'. Empty = Playwright "
            "defaults (the historical behavior)."
        ),
    )
    browser_cdp_endpoint: str = Field(
        default="",
        description=(
            "Optional remote-browser CDP endpoint (ws/wss URL, e.g. "
            "Browserless 'wss://production-sfo.browserless.io?token=...'). "
            "When set, the runner connects to this browser instead of "
            "launching a local Chromium — enables split deployments where "
            "the API host stays lightweight and the browser runs elsewhere. "
            "Empty = local Playwright launch (historical behavior)."
        ),
    )
    demo_fixture_base_url: str = Field(
        default="",
        description=(
            "Public base URL under which the demo router serves task "
            "fixtures (GET /demo/fixtures/...). REQUIRED when "
            "browser_cdp_endpoint is set: a remote browser cannot reach the "
            "orchestrator's 127.0.0.1 fixture server."
        ),
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

    def chromium_args(self) -> list[str]:
        """Parse :attr:`chromium_launch_args` into a clean list for Playwright."""
        return [arg.strip() for arg in self.chromium_launch_args.split(",") if arg.strip()]

    def ensure_results_dir(self) -> Path:
        """Create ``results_dir`` if it does not exist and return it."""
        self.results_dir.mkdir(parents=True, exist_ok=True)
        return self.results_dir
