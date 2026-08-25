"""Add second-layer memory review audit fields.

Revision ID: 20260825_06
Revises: 20260825_05
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa


revision = "20260825_06"
down_revision = "20260825_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memory_items", sa.Column("review_action", sa.String(length=32), nullable=True))
    op.add_column("memory_items", sa.Column("review_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("memory_items", "review_comment")
    op.drop_column("memory_items", "review_action")
