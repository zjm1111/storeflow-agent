"""Remove redundant task snapshot history table.

Revision ID: 20260830_07
Revises: 20260825_06
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa


revision = "20260830_07"
down_revision = "20260825_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("checkpoints")


def downgrade() -> None:
    op.create_table(
        "checkpoints",
        sa.Column("checkpoint_id", sa.String(length=80), primary_key=True),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("node", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
