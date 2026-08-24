"""Add optimistic concurrency version to task snapshots.

Revision ID: 20260824_03
Revises: 20260822_02
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa


revision = "20260824_03"
down_revision = "20260822_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("tasks", "state_version", server_default=None)


def downgrade() -> None:
    op.drop_column("tasks", "state_version")
