"""The demo HTTP router: GET /demo, GET /demo/tasks, POST /demo/run.

TRUST MODEL (read this before touching the endpoint): the provider API key
comes from an ANONYMOUS visitor in the request body and must satisfy, at
once:

* HTTPS-only arrival (403 otherwise, localhost exempt for development);
* the key is read from the RAW request body — never routed through
  FastAPI/pydantic request validation, whose 422 responses echo the offending
  ``input`` value back (that would leak the key into a response body);
* the key is registered for log-redaction at the EARLIEST moment (before any
  other processing) and unregistered in a ``finally``, so it outlives the
  request by nothing;
* the key is used to build a Provider ON THE FLY (OpenAICompatibleProvider
  directly, NOT providers/factory.py — that path loads keys from env/Vault
  and is a different trust model) and the provider object is dropped as soon
  as the handler returns;
* the run's trace artifacts go to a per-request TEMP directory, never the
  shared ``results/`` storage — demo runs are ephemeral and are not part of
  Agentalyze's run history. The temp dir (which contains NO key — traces hold
  messages and screenshots only) is deleted when the handler exits;
* hard wall-clock budget via ``asyncio.wait_for``: whatever hangs (browser,
  provider, verifier), the visitor gets an honest "ran out of time" answer.

Rate limiting is per-IP via the SAME slowapi limiter the service already
uses; concurrency is bounded by a semaphore because every run holds a real
Chromium.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError

from agentalyze.config import Settings
from agentalyze.demo.redaction import redact_text, register_secret, unregister_secret
from agentalyze.demo.tasks import DEMO_TASK_IDS, get_demo_task
from agentalyze.providers.base import (
    Provider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
)
from agentalyze.providers.openai_compatible import OpenAICompatibleProvider
from agentalyze.runner import run_task  # module attr is the monkeypatch seam for tests
from agentalyze.runner.trace import RunTrace

logger = structlog.get_logger(__name__)

#: Where the static demo page lives (packaged with the module; see the
#: ``[tool.setuptools.package-data]`` entry in pyproject.toml).
STATIC_DIR = Path(__file__).parent / "static"

#: Default OpenAI-compatible endpoint for the demo (OpenRouter — the most
#: common BYOK aggregator; visitors may override it with their own base_url).
DEFAULT_DEMO_BASE_URL = "https://openrouter.ai/api/v1"

#: Hosts for which plain HTTP is tolerated (local development only).
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "testserver"}

#: Hostnames of OpenAI-compatible endpoints a key may be sent to over plain
#: HTTP (only for local development against a local model server).
_LOCAL_PROVIDER_HOSTNAMES = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

_MAX_OBSERVATION_CHARS = 220

#: Cap on the raw request body. The endpoint reads the whole body into
#: memory; without a cap an anonymous visitor could memory-DoS a small
#: hosting instance with a handful of gigabyte-sized POSTs. A valid demo
#: body is a few hundred bytes; 64 KB is a generous ceiling.
_MAX_BODY_BYTES = 64 * 1024


def _host_is_private(hostname: str) -> bool:
    """True when ``hostname`` is (or resolves to) a non-public address.

    SSRF guard for the public demo: the visitor controls ``base_url``, and
    the server would otherwise happily POST the (visitor-supplied) key to
    internal services — cloud metadata (169.254.169.254), RFC1918 space,
    loopback. Hostnames are resolved so a name pointing at a private IP is
    caught too. Unresolvable names are allowed through: the provider call
    then fails honestly with a connection error.
    """
    try:
        candidates = [str(ipaddress.ip_address(hostname))]
    except ValueError:
        try:
            infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except OSError:
            return False
        candidates = sorted({str(info[4][0]) for info in infos})
    for candidate in candidates:
        try:
            addr = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if any(addr in network for network in _FAKE_IP_VPN_RANGES):
            # 198.18.0.0/15 (RFC 2544 benchmark range): used by local VPN
            # clients (Clash/Surge fake-IP DNS mode) to answer ALL DNS
            # queries — including perfectly public hostnames like
            # openrouter.ai — on the demo host itself. No real internal
            # service lives in this range, so it is deliberately excluded.
            continue
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        ):
            return True
    return False


#: See _host_is_private: fake-IP DNS ranges of local VPN clients.
_FAKE_IP_VPN_RANGES = (ipaddress.ip_network("198.18.0.0/15"),)


class DemoRunRequest(BaseModel):
    """Validated demo-run body.

    NOTE: this model is applied MANUALLY inside the endpoint (``model_validate``
    on the already-parsed dict), never as a FastAPI request-body parameter —
    see the module docstring for why (422 echo protection).
    """

    provider_kind: Literal["openai_compatible"] = Field(
        description=(
            "Only cloud OpenAI-compatible providers are offered: an anonymous "
            "visitor almost never has a PUBLIC Ollama endpoint."
        )
    )
    base_url: str | None = Field(
        default=None,
        max_length=500,
        description="OpenAI-compatible API root; None = OpenRouter.",
    )
    api_key: str = Field(min_length=1, max_length=512)
    model_name: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=100)


def _short(text: str) -> str:
    """Truncate long observations for the compact frontend-friendly payload."""
    text = " ".join(text.split())
    if len(text) <= _MAX_OBSERVATION_CHARS:
        return text
    return text[: _MAX_OBSERVATION_CHARS - 1] + "…"


def _https_arrival(request: Request) -> bool:
    """True when the request reached the TLS-terminated public entrypoint.

    Behind the hosting platform's proxy the inner hop is plain HTTP, so the
    standard ``X-Forwarded-Proto`` header (set/overridden by the platform's
    TLS terminator) is the authoritative signal; localhost is always exempt.
    """
    forwarded = request.headers.get("x-forwarded-proto")
    scheme = (forwarded.split(",")[0].strip() if forwarded else None) or (
        request.url.scheme
    )
    if scheme == "https":
        return True
    host = (request.headers.get("host") or "").split(":")[0].lower()
    return host in _LOCAL_HOSTNAMES


def _trace_to_summary(trace: RunTrace) -> dict[str, Any]:
    """Compact, human-readable summary for the demo page — NOT the RunTrace.

    Full traces (complete LLM message context, per-step screenshots) are far
    too heavy for a single HTTP response and are not the demo's point.
    """
    steps: list[dict[str, Any]] = []
    for step in trace.steps:
        call = step.tool_call
        result = step.tool_result
        observation = ""
        if result is not None:
            observation = result.output
        elif step.tool_error is not None:
            observation = f"tool error: {step.tool_error}"
        steps.append(
            {
                "step": step.step_number,
                "action": call.name if call is not None else "(no tool call)",
                "arguments": (
                    _short(json.dumps(call.arguments, ensure_ascii=False))
                    if call is not None
                    else ""
                ),
                "ok": result.success if result is not None else None,
                "observation": _short(observation),
            }
        )
    return {
        "task_id": trace.task_id,
        "model": trace.provider_name,
        "outcome": trace.outcome.value,
        "success": trace.success,
        "step_count": len(trace.steps),
        "verifier_reason": (
            trace.verifier_result.reason if trace.verifier_result is not None else None
        ),
        "total_prompt_tokens": trace.total_prompt_tokens,
        "total_completion_tokens": trace.total_completion_tokens,
        "total_cost_usd": trace.total_cost_usd,
        "wall_clock_seconds": round(trace.wall_clock_seconds, 1),
        "steps": steps,
    }


def _provider_error_response(exc: ProviderError) -> JSONResponse:
    """Map provider failures onto honest, non-leaky demo responses."""
    if isinstance(exc, ProviderAuthError):
        kind = "provider_auth"
        message = (
            "The provider rejected the API key (401/403). Check that the key "
            "is valid and allowed to use the selected model."
        )
    elif isinstance(exc, ProviderTimeoutError):
        kind = "provider_timeout"
        message = (
            "The provider did not answer in time. Try again or pick a faster model."
        )
    elif isinstance(exc, ProviderConnectionError):
        kind = "provider_connection"
        message = (
            "Could not reach the provider endpoint. Check the base URL "
            "(it must be publicly reachable over HTTPS)."
        )
    else:
        kind = "provider_error"
        message = "The provider returned an error during the run."
    # str(exc) may embed upstream response text — redact defensively anyway;
    # the visitor-facing message itself never quotes it.
    logger.warning(
        "demo run provider error", error_kind=kind, detail=redact_text(str(exc))[:500]
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "error", "error_kind": kind, "message": message},
    )


def create_demo_router(settings: Settings, limiter: Any | None) -> APIRouter:
    """Build the demo router (mounted only when ``demo_mode_enabled``)."""
    router = APIRouter(prefix="/demo", tags=["public-demo"])
    # Every demo run holds a real Chromium; hard-cap how many at once.
    semaphore = asyncio.Semaphore(settings.demo_max_concurrent_runs)

    async def _require_https(request: Request) -> None:
        if settings.demo_https_required and not _https_arrival(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Demo API keys are accepted over HTTPS only.",
            )

    @router.get(
        "",
        summary="The public demo page (static HTML).",
        include_in_schema=False,
    )
    async def demo_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @router.get("/tasks", summary="Demo task allowlist with human-readable metadata.")
    async def demo_tasks() -> dict[str, Any]:
        from agentalyze.demo.tasks import demo_tasks_payload

        return {
            "tasks": demo_tasks_payload(),
            "rate_limit": settings.demo_rate_limit,
            "default_base_url": DEFAULT_DEMO_BASE_URL,
        }

    async def demo_run(request: Request) -> JSONResponse:
        # ---- 0. Body-size cap: reject BEFORE reading anything into memory. -
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "request body too large"},
            )
        body_bytes = await request.body()
        if len(body_bytes) > _MAX_BODY_BYTES:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "request body too large"},
            )

        # ---- 1. Raw-body parsing: BEFORE anything else touches the key. -----
        # We parse JSON ourselves: FastAPI's request validation would echo the
        # offending input value in its 422 response, which is an unacceptable
        # leak surface for a secret.
        try:
            raw: Any = json.loads(body_bytes or b"{}")
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "request body must be a JSON object"},
            )
        if not isinstance(raw, dict):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "request body must be a JSON object"},
            )

        api_key = raw.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "field 'api_key' must be a non-empty string"},
            )

        # EARLIEST masking moment: from here on, wherever this key ends up in
        # a log line or traceback, the global redaction processor scrubs it.
        register_secret(api_key)
        try:
            return await _demo_run_inner(api_key, raw)
        finally:
            # The key is forgotten NOW: no closure, no long-lived object and
            # no log-mask registration survives the request.
            unregister_secret(api_key)

    async def _demo_run_inner(api_key: str, raw: dict[str, Any]) -> JSONResponse:
        # ---- 2. Manual validation; error messages never echo input values. --
        try:
            body = DemoRunRequest.model_validate(raw)
        except ValidationError as exc:
            errors = [
                {
                    "field": ".".join(str(part) for part in error["loc"]),
                    "type": error["type"],
                }
                for error in exc.errors()
                # 'input' is deliberately NOT forwarded: it can contain the key.
                if error["type"] != "missing"
            ]
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "invalid request", "errors": errors},
            )

        # ---- 3. Allowlist check: NO arbitrary task ids. ---------------------
        task = get_demo_task(body.task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unknown demo task {body.task_id!r}; allowed: "
                    f"{list(DEMO_TASK_IDS)}"
                ),
            )

        # ---- 4. The key must only travel to HTTPS endpoints. ---------------
        base_url = body.base_url or DEFAULT_DEMO_BASE_URL
        parsed = urlparse(base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "base_url must be an absolute http(s) URL"},
            )
        if parsed.scheme != "https" and parsed.hostname not in _LOCAL_PROVIDER_HOSTNAMES:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "detail": "refusing to send the API key to a non-HTTPS endpoint"
                },
            )

        # ---- 4b. SSRF guard: in public mode the key must never travel to --
        # ---- private/internal addresses (metadata service, RFC1918,       --
        # ---- loopback), even over HTTPS. Dev mode (https off) keeps       --
        # ---- localhost allowed for local model servers.                   --
        if settings.demo_https_required and await asyncio.to_thread(
            _host_is_private, parsed.hostname
        ):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "detail": (
                        "refusing to send the API key to a private/internal "
                        "endpoint (SSRF guard)"
                    )
                },
            )

        # ---- 5. Concurrency: one Chromium per run, bounded. -----------------
        if semaphore.locked():
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "detail": (
                        "the demo is busy with another run right now; "
                        "please retry in a minute"
                    )
                },
            )

        # ---- 6. Provider built ON THE FLY from the request-supplied key. ----
        # Deliberately NOT providers/factory.load_providers(): that resolves
        # keys from providers.yaml + env/Vault (admin trust model). Also no
        # RetryingProvider: a demo must fail fast, not burn the visitor's
        # tokens on retries.
        provider: Provider = OpenAICompatibleProvider(
            name=f"demo:{body.model_name}",
            model_name=body.model_name,
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=settings.demo_provider_timeout_seconds,
        )

        # Cap the task's own wall-clock to the demo budget as well, so the
        # ReAct loop's internal deadline cannot outlive the HTTP one.
        demo_task = task.model_copy(
            update={
                "timeout_seconds": min(
                    task.timeout_seconds, int(settings.demo_run_timeout_seconds)
                )
            }
        )

        # ---- 7. Ephemeral artifacts: NEVER the shared results/ storage. -----
        # trace.json / screenshots are written to a per-request temp dir (they
        # contain no key) and deleted when this handler exits.
        with tempfile.TemporaryDirectory(prefix="agentalyze-demo-") as tmp:
            run_settings = settings.model_copy(update={"results_dir": Path(tmp)})
            async with semaphore:
                try:
                    trace = await asyncio.wait_for(
                        run_task(demo_task, provider, run_settings),
                        timeout=settings.demo_run_timeout_seconds,
                    )
                except TimeoutError:
                    logger.warning("demo run timed out", task_id=demo_task.id)
                    return JSONResponse(
                        content={
                            "status": "timeout",
                            "task_id": demo_task.id,
                            "timeout_seconds": int(settings.demo_run_timeout_seconds),
                            "message": (
                                "The run did not finish within the demo time "
                                f"budget of {int(settings.demo_run_timeout_seconds)} "
                                "seconds and was stopped. This is an honest "
                                "limit of the public demo, not necessarily a "
                                "model failure."
                            ),
                        }
                    )
                except ProviderError as exc:
                    return _provider_error_response(exc)
                except Exception as exc:
                    # logger.exception renders the traceback through the
                    # global structlog pipeline; the redaction processor has
                    # scrubbed any occurrence of the key by then.
                    logger.exception(
                        "demo run crashed unexpectedly", task_id=demo_task.id
                    )
                    return JSONResponse(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        content={
                            "status": "error",
                            "error_kind": "internal",
                            "message": redact_text(str(exc))[:500] or "internal error",
                        },
                    )
                finally:
                    # Drop references eagerly so the (key-holding) provider is
                    # collectable as soon as the run is over.
                    provider = None  # type: ignore[assignment]

        result = _trace_to_summary(trace)
        logger.info(
            "demo run finished",
            task_id=trace.task_id,
            outcome=trace.outcome.value,
            steps=len(trace.steps),
        )
        return JSONResponse(
            content={
                "status": "completed" if trace.success else "failed",
                **result,
            }
        )

    # Rate limiting (per IP) wraps the endpoint LAST so it fires before the
    # body is even read: rejected requests never parse (or register) a key.
    if limiter is not None:
        demo_run = limiter.limit(settings.demo_rate_limit)(demo_run)

    router.post("/run", dependencies=[Depends(_require_https)])(demo_run)
    return router
