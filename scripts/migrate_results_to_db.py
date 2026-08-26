#!/usr/bin/env python3
"""One-shot migration: existing filesystem results -> service metadata DB.

The service keeps full traces/screenshots on the filesystem (unchanged);
only the suite-run REGISTRY moves into SQL. This script walks
``{results_dir}/*/suite_run.json`` (written by every `compare` run since
Phase 5) and upserts a ``completed`` ``suite_runs`` row for each — so runs
made with the CLI remain visible/status-queryable through the API.

Idempotent: rows whose suite_run_id already exists are skipped, so the
script is safe to re-run (e.g. after more CLI runs happened meanwhile).

Usage:
    python scripts/migrate_results_to_db.py [--results-dir ./results] [--dry-run]

The database URL comes from AGENTALYZE_DATABASE_URL (default: SQLite in cwd).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Make `src` importable when run from a checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentalyze.api.db import SuiteRunRecord, make_engine, make_session_factory
from agentalyze.config import Settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def migrate(results_dir: Path, *, dry_run: bool) -> tuple[int, int]:
    settings = Settings()
    from sqlalchemy import select

    from agentalyze.api.db import run_migrations

    # Bring the schema to head first (same contract as the service itself).
    await asyncio.to_thread(run_migrations, settings.database_url)

    engine = make_engine(settings.database_url)
    imported = skipped = 0
    try:
        factory = make_session_factory(engine)
        for record_file in sorted(results_dir.glob("*/suite_run.json")):
            try:
                raw = json.loads(record_file.read_text(encoding="utf-8"))
                suite_run_id = str(raw["suite_run_id"])
                config_json = json.dumps(raw.get("config", {}))
                started_at = datetime.fromisoformat(raw["started_at"])
                finished_at = datetime.fromisoformat(raw["finished_at"])
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                print(f"SKIP {record_file}: unreadable ({exc})", file=sys.stderr)
                skipped += 1
                continue

            async with factory() as session:
                existing = await session.execute(
                    select(SuiteRunRecord).where(
                        SuiteRunRecord.suite_run_id == suite_run_id
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    skipped += 1
                    continue
                if not dry_run:
                    session.add(
                        SuiteRunRecord(
                            suite_run_id=suite_run_id,
                            status="completed",
                            submitted_by=None,  # predates the service; unknown
                            config_json=config_json,
                            error=None,
                            submitted_at=started_at,
                            started_at=started_at,
                            finished_at=finished_at,
                        )
                    )
                    await session.commit()
            imported += 1
            print(f"{'WOULD IMPORT' if dry_run else 'IMPORTED'} {suite_run_id}")
    finally:
        await engine.dispose()
    return imported, skipped


def main() -> int:
    args = _parse_args()
    settings = Settings()
    results_dir = Path(args.results_dir or settings.results_dir)
    if not results_dir.is_dir():
        print(f"error: results dir not found: {results_dir}", file=sys.stderr)
        return 2
    imported, skipped = asyncio.run(migrate(results_dir, dry_run=args.dry_run))
    print(f"Done: {imported} imported, {skipped} skipped "
          f"({'dry run' if args.dry_run else 'committed'}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
