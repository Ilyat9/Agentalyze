"""initial service schema: suite_runs, api_keys, baseline_pointer

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suite_runs",
        sa.Column("suite_run_id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False, index=True),
        sa.Column("submitted_by", sa.String(length=128), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key_hash", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("name", name="uq_api_keys_name"),
    )
    op.create_table(
        "baseline_pointer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("suite_run_id", sa.String(length=36), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("baseline_pointer")
    op.drop_table("api_keys")
    op.drop_table("suite_runs")
