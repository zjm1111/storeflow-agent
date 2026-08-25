"""Add auditable provenance and reviewer relation hints to memories.

Revision ID: 20260825_05
Revises: 20260824_04
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa


revision = "20260825_05"
down_revision = "20260824_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memory_items", sa.Column("origin_task_id", sa.String(length=36), nullable=True))
    op.add_column("memory_items", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memory_items", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column("memory_items", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("memory_items", sa.Column("possible_duplicate_of", sa.String(length=64), nullable=True))
    op.add_column("memory_items", sa.Column("conflicts_with", sa.JSON(), nullable=True))
    op.create_index("ix_memory_items_origin_task_id", "memory_items", ["origin_task_id"])
    op.create_index("ix_memory_items_content_hash", "memory_items", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_memory_items_content_hash", table_name="memory_items")
    op.drop_index("ix_memory_items_origin_task_id", table_name="memory_items")
    op.drop_column("memory_items", "conflicts_with")
    op.drop_column("memory_items", "possible_duplicate_of")
    op.drop_column("memory_items", "revision")
    op.drop_column("memory_items", "content_hash")
    op.drop_column("memory_items", "reviewed_at")
    op.drop_column("memory_items", "origin_task_id")
