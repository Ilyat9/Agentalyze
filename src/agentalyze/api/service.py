"""Background suite-run execution for POST /runs.

QUEUE CHOICE — in-process asyncio tasks over celery/arq+Redis: the target
deployment is a small trusted team whose runs already serialize behind a
global semaphore (each in-flight combination holds a real Chromium); a broker
would add an extra stateful service without changing any of the actual
limits. Run STATE is nevertheless durable in SQL, so any server replica can
answer GET /runs/{id}; the known limitation (documented): with more than one
API replica, work executes inside whichever replica accepted the request and
is lost if that replica dies mid-run — the upgrade path to a shared queue is
confined to this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from agentalyze.api.db import SuiteRunRecord
from agentalyze.api.metrics import SUITE_RUNS_ACTIVE, SUITE_RUNS_TOTAL
from agentalyze.config import Settings
from agentalyze.orchestration.report import generate_report
from agentalyze.orchestration.suite_runner import (
    SuiteRunConfig,
    SuiteRunResult,
    run_suite,
)
from agentalyze.providers.base import Provider

logger = structlog.get_logger(__name__)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


class SuiteRunManager:
    """Accepts runs, persists their status, executes them in the background."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_active_suite_runs)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def submit_and_spawn(
        self,
        suite_run_id: str,
        config: SuiteRunConfig,
        providers: Mapping[str, Provider],
        session_factory: async_sessionmaker[AsyncSession],
        submitted_by: str | None,
    ) -> None:

        """Persist a pending row and start execution; returns immediately."""
        async with session_factory() as session:
            session.add(
                SuiteRunRecord(
                    suite_run_id=suite_run_id,
                    status=STATUS_PENDING,
                    submitted_by=submitted_by,
                    config_json=config.model_dump_json(),
                    started_at=None,
                    finished_at=None,
                )
            )
            await session.commit()
        task = asyncio.create_task(
            self._execute(suite_run_id, config, providers, session_factory),
            name=f"suite-run-{suite_run_id}",
        )
        self._tasks[suite_run_id] = task

    async def _execute(
        self,
        suite_run_id: str,
        config: SuiteRunConfig,
        providers: Mapping[str, Provider],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        async with self._semaphore:
            await self._set_status(session_factory, suite_run_id, STATUS_RUNNING)
            SUITE_RUNS_ACTIVE.inc()
            try:
                result = await run_suite(
                    config,
                    dict(providers),
                    self._settings,
                    suite_run_id=suite_run_id,
                )
                generate_report(result, self._settings.results_dir)
            except asyncio.CancelledError:
                await self._fail(session_factory, suite_run_id, "cancelled")
                raise
            except ValidationError as exc:
                await self._fail(session_factory, suite_run_id, str(exc)[:2000])
                return
            except Exception as exc:
                logger.exception("suite run failed", suite_run_id=suite_run_id)
                await self._fail(session_factory, suite_run_id, repr(exc)[:2000])
                return
        await self._complete(session_factory, suite_run_id)

    @staticmethod
    async def _set_status(
        session_factory: async_sessionmaker[AsyncSession],
        suite_run_id: str,
        status: str,
    ) -> None:
        from sqlalchemy import update

        async with session_factory() as session:
            await session.execute(
                update(SuiteRunRecord)
                .where(SuiteRunRecord.suite_run_id == suite_run_id)
                .values(status=status, started_at=datetime.now(UTC))
            )
            await session.commit()

    @staticmethod
    async def _fail(
        session_factory: async_sessionmaker[AsyncSession],
        suite_run_id: str,
        error: str,
    ) -> None:
        from sqlalchemy import update

        async with session_factory() as session:
            await session.execute(
                update(SuiteRunRecord)
                .where(SuiteRunRecord.suite_run_id == suite_run_id)
                .values(status=STATUS_FAILED, error=error,
                        finished_at=datetime.now(UTC))
            )
            await session.commit()
        SUITE_RUNS_TOTAL.labels(STATUS_FAILED).inc()
        SUITE_RUNS_ACTIVE.dec()
        logger.warning("suite run failed", suite_run_id=suite_run_id, error=error)

    @staticmethod
    async def _complete(
        session_factory: async_sessionmaker[AsyncSession],
        suite_run_id: str,
    ) -> None:
        from sqlalchemy import update

        async with session_factory() as session:
            await session.execute(
                update(SuiteRunRecord)
                .where(SuiteRunRecord.suite_run_id == suite_run_id)
                .values(status=STATUS_COMPLETED, finished_at=datetime.now(UTC))
            )
            await session.commit()
        SUITE_RUNS_TOTAL.labels(STATUS_COMPLETED).inc()
        SUITE_RUNS_ACTIVE.dec()


def load_result_safe(results_dir, suite_run_id: str) -> SuiteRunResult | None:  # type: ignore[no-untyped-def]
    from agentalyze.orchestration.suite_runner import load_suite_run

    try:
        return load_suite_run(results_dir, suite_run_id)
    except FileNotFoundError:
        return None
