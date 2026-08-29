"""The FastAPI application: thin HTTP wrappers over existing CLI-layer logic.

EVERY endpoint delegates to the same functions the CLI uses — run_suite /
compute_regression / render_report / the regression storage helpers. No
business logic lives here; this module only translates HTTP ↔ those calls.

Protocol decisions worth their comments:

* ``POST /regression-check`` answers HTTP 200 for every VALID comparison and
  carries the gate outcome in ``"regressed": true|false``. The CLI exit code
  is a CI-process convention, not an HTTP semantic; mapping "regressions
  found" onto a 5xx would misrepresent a successful check as a server fault.
  4xx/5xx remain reserved for usage/infrastructure errors (404 unknown run,
  409 no baseline, 503 unhealthy providers).
* Secrets (provider API keys) never appear in any response: provider configs
  are loaded server-side from providers.yaml + env/Vault, request models
  accept only provider NAMES, and error handlers return sanitized messages.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from slowapi.errors import RateLimitExceeded

from agentalyze.api.auth import ApiKeyAuth
from agentalyze.api.db import (
    SuiteRunRecord,
    make_engine,
    make_session_factory,
    run_migrations,
)
from agentalyze.api.metrics import REGRESSIONS_TOTAL, MetricsProvider
from agentalyze.api.observability import configure_logging
from agentalyze.api.service import (
    STATUS_COMPLETED,
    STATUS_PENDING,
    STATUS_RUNNING,
    SuiteRunManager,
)
from agentalyze.config import Settings
from agentalyze.orchestration.report import generate_report
from agentalyze.orchestration.suite_runner import SuiteRunConfig, load_suite_run
from agentalyze.providers import load_providers
from agentalyze.regression.config import RegressionConfigError, load_regression_config
from agentalyze.regression.diff import TaskDiff, compute_regression
from agentalyze.regression.storage import (
    AutoBaselineNotFoundError,
    BaselineNotSetError,
    SuiteRunNotFoundError,
    find_last_clean_baseline,
    load_saved_suite_run,
    record_gate_outcome,
    require_current_baseline,
    save_regression_report,
)
from agentalyze.tasks.models import TaskCategory

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    """Body of POST /runs — mirrors SuiteRunConfig, provider NAMES only."""

    provider_names: list[str] = Field(min_length=1)
    task_ids: list[str] | None = Field(
        default=None, description="None = every registered task."
    )
    category_filter: list[TaskCategory] | None = None
    max_concurrent: int = Field(default=1, ge=1)


class RunCreatedResponse(BaseModel):
    suite_run_id: str
    status: str
    report_url: str


class RunStatusResponse(BaseModel):
    suite_run_id: str
    status: str
    submitted_by: str | None
    submitted_at: str | None
    started_at: str | None
    finished_at: str | None
    error: str | None
    report_url: str | None


class RegressionCheckRequest(BaseModel):
    new: str = Field(min_length=1, description="suite_run_id of the new run.")
    baseline: str | None = Field(
        default=None,
        description="Baseline suite_run_id, 'auto', or None for the stored pointer.",
    )
    allow_regressions: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_selected_providers(settings: Settings, names: list[str]) -> dict[str, Any]:
    """Load configured providers by name; 400 on unknown names."""
    try:
        all_providers = load_providers(settings.providers_config_path, settings)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"cannot load providers config: {exc}",
        ) from exc
    unknown = [name for name in names if name not in all_providers]
    if unknown:
        available = ", ".join(sorted(all_providers)) or "<none>"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown provider name(s) {unknown}; configured: {available}",
        )
    return {name: all_providers[name] for name in names}


async def _preflight_health_checks(providers: dict[str, Any]) -> list[str]:
    results = await asyncio.gather(*(p.health_check() for p in providers.values()))
    return [name for name, ok in zip(providers, results, strict=True) if not ok]


def _validate_task_selection(config: SuiteRunConfig) -> None:
    from agentalyze.orchestration.suite_runner import select_tasks

    try:
        select_tasks(config)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


def _record_to_response(record: SuiteRunRecord) -> RunStatusResponse:
    report_url = (
        f"/runs/{record.suite_run_id}/report"
        if record.status == STATUS_COMPLETED
        else None
    )
    return RunStatusResponse(
        suite_run_id=record.suite_run_id,
        status=record.status,
        submitted_by=record.submitted_by,
        submitted_at=record.submitted_at.isoformat() if record.submitted_at else None,
        started_at=record.started_at.isoformat() if record.started_at else None,
        finished_at=record.finished_at.isoformat() if record.finished_at else None,
        error=record.error,
        report_url=report_url,
    )


def _diff_to_dict(diff: TaskDiff) -> dict[str, Any]:
    payload: dict[str, Any] = diff.model_dump(mode="json")
    payload["status"] = diff.status.value
    if diff.baseline_outcome is not None:
        payload["baseline_outcome"] = diff.baseline_outcome.value
    if diff.new_outcome is not None:
        payload["new_outcome"] = diff.new_outcome.value
    return payload


def _client_key(request: Request) -> str:
    """Rate-limit bucket key: API-key identity when known, else client host.

    Behind a local reverse proxy / tunnel (cloudflared, nginx on the same
    host) every visitor's socket address is the proxy's (127.0.0.1), which
    would collapse ALL visitors into one rate-limit bucket. In exactly that
    setup the proxy sets ``CF-Connecting-IP`` with the visitor's real IP, so
    it is trusted — but ONLY from a loopback/test client, never from a
    remote socket: a direct visitor could otherwise spoof the header and
    rotate buckets to bypass the limit.
    """
    name = getattr(request.state, "api_key_name", None)
    if name:
        return f"key:{name}"
    remote = request.client.host if request.client else ""
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip and remote in _PROXY_TRUSTED_REMOTE_HOSTS:
        return f"ip:{cf_ip}"
    return f"ip:{remote or 'unknown'}"


#: Socket hosts from which a CF-Connecting-IP header may be trusted (local
#: reverse proxies/tunnels). "testclient" is the httpx TestClient address and
#: exists only in tests.
_PROXY_TRUSTED_REMOTE_HOSTS = {"127.0.0.1", "::1", "testclient"}

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the service app. ``settings`` injection keeps tests hermetic."""
    settings = settings or Settings()
    configure_logging(settings.log_level, json_format=settings.log_format == "json")

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Schema evolution goes through Alembic only; run once at startup,
        # off the event loop (Alembic's API is synchronous).
        await asyncio.to_thread(run_migrations, settings.database_url)
        app.state.manager = SuiteRunManager(settings)
        logger.info("service started", results_dir=str(settings.results_dir))
        yield
        await engine.dispose()

    app = FastAPI(
        title="Agentalyze API",
        version="0.1.0",
        description=(
            "HTTP service over the Agentalyze eval harness. Authentication: "
            "Authorization: Bearer <API key> (keys stored hashed)."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory

    # --- Rate limiting (slowapi): POST /runs per API key, and — when demo ---
    # --- mode is on — POST /demo/run per client IP (anonymous visitors). ----
    rate_limit_value = settings.api_runs_rate_limit.strip().lower()
    demo_limit_value = (
        settings.demo_rate_limit.strip().lower() if settings.demo_mode_enabled else "none"
    )
    limiter = None
    if rate_limit_value not in {"", "none", "disabled"} or demo_limit_value not in {
        "",
        "none",
        "disabled",
    }:
        from slowapi import Limiter

        limiter = Limiter(key_func=_client_key)
        app.state.limiter = limiter

        def _rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
            retry_after = getattr(exc, "retry_after", None)
            headers = (
                {"Retry-After": str(int(retry_after))}
                if retry_after is not None
                else {}
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "rate limit exceeded; slow down and retry later"},
                headers=headers,
            )

        app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    # Deep provider checks are real network probes; cache them briefly so
    # monitoring scrapes of /health don't hammer LLM endpoints.
    health_cache: dict[str, Any] = {"at": 0.0, "healthy": False, "providers": {}}
    HEALTH_CACHE_TTL_SECONDS = 30.0

    # -----------------------------------------------------------------------
    # POST /runs — accept a suite run, execute it in the background.
    # -----------------------------------------------------------------------

    async def post_runs(
        body: RunRequest, request: Request, key: ApiKeyAuth
    ) -> JSONResponse:
        selected = _load_selected_providers(settings, body.provider_names)
        config = SuiteRunConfig(
            task_ids=body.task_ids,
            provider_names=body.provider_names,
            category_filter=body.category_filter,
            max_concurrent=body.max_concurrent,
        )
        _validate_task_selection(config)

        # Same fail-fast contract as `agentalyze compare`: refuse to start a
        # paid/browser-heavy suite with a dead provider BEFORE anything runs.
        unhealthy = await _preflight_health_checks(selected)
        if unhealthy:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"provider(s) failed health check: {unhealthy}; run not started",
            )

        instrumented = {name: MetricsProvider(p) for name, p in selected.items()}
        suite_run_id = str(uuid.uuid4())
        submitted_by = key.name if key is not None else None
        manager: SuiteRunManager = request.app.state.manager
        await manager.submit_and_spawn(
            suite_run_id, config, instrumented, session_factory, submitted_by
        )
        logger.info("run accepted", suite_run_id=suite_run_id, by=submitted_by)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=RunCreatedResponse(
                suite_run_id=suite_run_id,
                status=STATUS_PENDING,
                report_url=f"/runs/{suite_run_id}/report",
            ).model_dump(),
            headers={"Location": f"/runs/{suite_run_id}"},
        )

    if limiter is not None:
        post_runs = limiter.limit(settings.api_runs_rate_limit)(post_runs)

    @app.post(
        "/runs",
        status_code=status.HTTP_202_ACCEPTED,
        summary="Start a suite run asynchronously; returns its id immediately.",
    )
    async def runs_endpoint(
        request: Request, body: RunRequest, key: ApiKeyAuth
    ) -> JSONResponse:
        return await post_runs(body, request, key)

    @app.get("/runs", summary="List recent suite runs (newest first).")
    async def list_runs(
        key: ApiKeyAuth, limit: Annotated[int, Query(ge=1, le=100)] = 20
    ) -> dict[str, Any]:
        from sqlalchemy import select

        async with session_factory() as session:
            result = await session.execute(
                select(SuiteRunRecord)
                .order_by(SuiteRunRecord.submitted_at.desc())
                .limit(limit)
            )
            records = list(result.scalars().all())
        return {"runs": [_record_to_response(r).model_dump() for r in records]}

    @app.get("/runs/{suite_run_id}", summary="Status of one suite run.")
    async def get_run(suite_run_id: str, key: ApiKeyAuth) -> RunStatusResponse:
        from sqlalchemy import select

        async with session_factory() as session:
            result = await session.execute(
                select(SuiteRunRecord).where(SuiteRunRecord.suite_run_id == suite_run_id)
            )
            record = result.scalar_one_or_none()
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no suite run {suite_run_id!r} was ever submitted",
            )
        return _record_to_response(record)

    @app.get(
        "/runs/{suite_run_id}/report",
        summary="Markdown (default) or HTML report of a completed run.",
    )
    async def get_report(
        suite_run_id: str,
        key: ApiKeyAuth,
        format: Literal["markdown", "html"] = "markdown",
    ) -> Response:
        # suite_run_id is used as a PATH COMPONENT below; FastAPI already
        # rejects path separators in a single path parameter, and this explicit
        # check closes any traversal angle (e.g. URL-encoded dots).
        if "/" in suite_run_id or ".." in suite_run_id:
            raise HTTPException(status_code=400, detail="invalid suite run id")
        from sqlalchemy import select

        async with session_factory() as session:
            result = await session.execute(
                select(SuiteRunRecord.status).where(
                    SuiteRunRecord.suite_run_id == suite_run_id
                )
            )
            row = result.first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown suite run {suite_run_id!r}",
            )
        run_status = row[0]
        if run_status in (STATUS_PENDING, STATUS_RUNNING):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"suite run {suite_run_id!r} is still {run_status}; the "
                "report appears when the run completes",
            )
        path = Path(settings.results_dir) / suite_run_id / "report.md"
        if not path.is_file():
            # Completed via CLI against the same shared results dir without a
            # rendered report: render it on demand instead of 404-ing.
            try:
                saved = load_suite_run(Path(settings.results_dir), suite_run_id)
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"report artifacts for {suite_run_id!r} are missing",
                ) from exc
            path = generate_report(saved, Path(settings.results_dir))
        markdown_text = path.read_text(encoding="utf-8")
        if format == "markdown":
            return Response(
                content=markdown_text, media_type="text/markdown; charset=utf-8"
            )
        import markdown as md_lib

        html = md_lib.markdown(markdown_text, extensions=["tables"])
        return Response(content=html, media_type="text/html; charset=utf-8")

    @app.post(
        "/regression-check",
        summary="Diff two runs; the gate outcome is in the body, HTTP 200 means a valid check.",
    )
    async def regression_check(
        body: RegressionCheckRequest, key: ApiKeyAuth
    ) -> dict[str, Any]:
        try:
            if (body.baseline or "").strip().lower() == "auto":
                baseline_id = find_last_clean_baseline(settings.results_dir)
            elif body.baseline:
                baseline_id = body.baseline.strip()
            else:
                baseline_id = require_current_baseline(settings.results_dir)
        except (BaselineNotSetError, AutoBaselineNotFoundError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc

        try:
            baseline = load_saved_suite_run(Path(settings.results_dir), baseline_id)
            new = load_saved_suite_run(Path(settings.results_dir), body.new.strip())
        except SuiteRunNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

        try:
            excluded = load_regression_config(
                Path(settings.regression_config_path)
            ).excluded_task_ids()
        except RegressionConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        # Same pure comparison + storage side effects as the CLI command.
        report = compute_regression(baseline, new, excluded_task_ids=excluded)
        save_regression_report(report, Path(settings.results_dir))
        record_gate_outcome(
            Path(settings.results_dir),
            report.new_suite_run_id,
            was_clean=report.regressed_count == 0,
        )
        regressed = report.regressed_count > 0
        if regressed:
            REGRESSIONS_TOTAL.inc()
        return {
            "baseline_suite_run_id": report.baseline_suite_run_id,
            "new_suite_run_id": report.new_suite_run_id,
            "regressed": regressed,
            "gate_failed": regressed and not body.allow_regressions,
            "allow_regressions": body.allow_regressions,
            "regressed_count": report.regressed_count,
            "fixed_count": report.fixed_count,
            "net_change": report.net_change,
            "providers_only_in_baseline": report.providers_only_in_baseline,
            "providers_only_in_new": report.providers_only_in_new,
            "diffs": [_diff_to_dict(d) for d in report.diffs],
        }

    @app.get(
        "/health",
        summary="Deep health: database reachable AND at least one configured provider up.",
    )
    async def health() -> JSONResponse:
        components: dict[str, Any] = {}
        healthy = True

        try:
            from sqlalchemy import text

            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            components["database"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001 - only the exception TYPE leaks
            components["database"] = {"ok": False, "detail": type(exc).__name__}
            healthy = False

        now = time.monotonic()
        if now - health_cache["at"] > HEALTH_CACHE_TTL_SECONDS:
            provider_results: dict[str, bool] = {}
            try:
                providers = load_providers(settings.providers_config_path, settings)
            except Exception:  # noqa: BLE001 - config problems mean no providers
                providers = {}
            for name, provider in providers.items():
                try:
                    ok = await asyncio.wait_for(provider.health_check(), timeout=8.0)
                except Exception:  # noqa: BLE001 - any failure = not healthy
                    ok = False
                provider_results[name] = bool(ok)
            health_cache.update(
                at=now, healthy=any(provider_results.values()), providers=provider_results
            )
        components["providers"] = {
            "ok": bool(health_cache["healthy"]),
            "checked": health_cache["providers"],
        }
        if not health_cache["healthy"]:
            healthy = False

        return JSONResponse(
            status_code=(
                status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            content={"status": "ok" if healthy else "unavailable", "components": components},
        )

    @app.get("/readyz", summary="Kubernetes readiness: DB connectivity.")
    async def readyz() -> JSONResponse:
        try:
            from sqlalchemy import text

            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"status": "db unreachable"})
        return JSONResponse(content={"status": "ready"})

    @app.get("/livez", summary="Kubernetes liveness: process is up.")
    async def livez() -> JSONResponse:
        return JSONResponse(content={"status": "alive"})

    @app.get("/metrics", summary="Prometheus metrics (text exposition format).")
    async def metrics_endpoint() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if settings.demo_mode_enabled:
        # Opt-in public-demo surface (BYOK). Deliberately mounted ONLY when
        # explicitly enabled: self-hosted `agentalyze serve` deployments must
        # not gain a key-accepting anonymous endpoint by default. The router
        # shares the SAME slowapi limiter as POST /runs (per-IP for demo).
        from agentalyze.demo.routes import create_demo_router

        app.include_router(create_demo_router(settings, limiter))
        logger.info(
            "demo mode enabled",
            demo_rate_limit=settings.demo_rate_limit,
            demo_run_timeout_seconds=settings.demo_run_timeout_seconds,
        )

    return app





