"""Index task snapshots used by situational-memory recall.

Revision ID: 20260822_02
Revises: 20260821_01
Create Date: 2026-08-22
"""
from alembic import op


revision = "20260822_02"
down_revision = "20260821_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_tasks_workspace_status_updated_at",
        "tasks",
        ["workspace_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_workspace_status_updated_at", table_name="tasks")
