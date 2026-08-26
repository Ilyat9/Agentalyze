"""Persistent metadata store for service mode.

SCOPE DELIBERATELY NARROW: only *metadata* lives in SQL — the registry of
suite runs (status machine pending→running→completed|failed), hashed API keys
and the baseline pointer. Full traces and screenshots stay on the filesystem
under ``results_dir`` exactly as before; they are bulky binary-ish artifacts
whose natural home remains disk/object storage, and every existing reader
(``load_suite_run``, reports, regression storage) keeps working unchanged.

Concurrency note: the default URL is SQLite (perfect for single-process
self-hosted deployments; WAL mode is enabled below). Multi-replica production
deployments must point ``AGENTALYZE_DATABASE_URL`` at PostgreSQL — SQLite's
write lock does not survive concurrent writers.

Schema management goes through Alembic (see ``migrations/``); startup runs
``upgrade head`` automatically so a fresh deployment needs no manual step.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SuiteRunRecord(Base):
    """One row per accepted POST /runs — the durable status machine."""

    __tablename__ = "suite_runs"

    suite_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: pending -> running -> completed | failed
    status: Mapped[str] = mapped_column(String(16), index=True)
    #: Human-readable identity of the API key that submitted the run
    #: (the key itself is never stored anywhere).
    submitted_by: Mapped[str | None] = mapped_column(String(128))
    config_json: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(default=_utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class ApiKeyRecord(Base):
    """A hashed Bearer token. The plaintext key exists ONLY at creation time."""

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("name", name="uq_api_keys_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    #: Format: ``scrypt$n$r$p$salt_hex$hash_hex`` — verification is constant-time.
    key_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    is_active: Mapped[bool] = mapped_column(default=True)


class BaselinePointer(Base):
    """Singleton row mirroring the CLI's current_baseline.txt for service mode."""

    __tablename__ = "baseline_pointer"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    suite_run_id: Mapped[str] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow)


def make_engine(database_url: str) -> AsyncEngine:
    """Create the async engine; enables WAL pragmas for SQLite URLs."""
    if database_url.startswith("sqlite"):
        engine = create_async_engine(
            database_url,
            connect_args={"check_same_thread": False}
            if "aiosqlite" in database_url
            else {},
        )
        return engine
    return create_async_engine(database_url)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def sync_database_url(async_url: str) -> str:
    """Translate an async driver URL into its sync equivalent for Alembic."""
    replacements = {
        "sqlite+aiosqlite": "sqlite",
        "postgresql+asyncpg": "postgresql+psycopg2",
        "postgresql+psycopg": "postgresql+psycopg2",
    }
    for async_prefix, sync_prefix in replacements.items():
        if async_url.startswith((async_prefix + "://", async_prefix + "+")):
            return sync_prefix + async_url[len(async_prefix):]
    return async_url


def run_migrations(database_url: str, base_dir: Path | None = None) -> None:
    """Bring the schema to head via Alembic (synchronous, used at startup).

    Uses the programmatic Alembic API against THIS project's ``migrations/``
    directory — never ad-hoc CREATE TABLE statements. The project root is
    located via (1) explicit argument, (2) ``AGENTALYZE_MIGRATIONS_HOME``,
    (3) the process working directory (Docker/k8s images ship alembic.ini +
    migrations/ at /app), (4) this file's location in a source checkout.
    """
    import os

    from alembic import command
    from alembic.config import Config

    candidates: list[Path] = []
    if base_dir is not None:
        candidates.append(base_dir)
    env_home = os.environ.get("AGENTALYZE_MIGRATIONS_HOME")
    if env_home:
        candidates.append(Path(env_home))
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve().parents[3])

    root = next(
        (
            candidate
            for candidate in candidates
            if (candidate / "alembic.ini").is_file()
            and (candidate / "migrations" / "env.py").is_file()
        ),
        None,
    )
    if root is None:
        msg = (
            "alembic.ini + migrations/ not found (searched: "
            f"{[str(c) for c in candidates]}). Set AGENTALYZE_MIGRATIONS_HOME "
            "to the deployment's migration files directory."
        )
        raise RuntimeError(msg)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", sync_database_url(database_url))
    command.upgrade(config, "head")


async def get_api_key_hashes(session: AsyncSession) -> list[ApiKeyRecord]:
    result = await session.execute(select(ApiKeyRecord).where(ApiKeyRecord.is_active))
    return list(result.scalars().all())
