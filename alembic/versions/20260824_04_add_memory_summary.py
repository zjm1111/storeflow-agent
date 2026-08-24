"""Add a lightweight catalog summary for approved business memories.

Revision ID: 20260824_04
Revises: 20260824_03
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa


revision = "20260824_04"
down_revision = "20260824_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable keeps already-approved historical rows readable.  The service
    # supplies a clearly-labelled legacy catalog fallback until they are next
    # revised, rather than silently fabricating a business summary.
    op.add_column("memory_items", sa.Column("summary", sa.String(length=600), nullable=True))


def downgrade() -> None:
    op.drop_column("memory_items", "summary")
