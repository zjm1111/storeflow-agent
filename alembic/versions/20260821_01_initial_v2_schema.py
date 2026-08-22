"""Create the initial SupplyMind V2 relational schema.

Revision ID: 20260821_01
Revises:
Create Date: 2026-08-21
"""
from alembic import op

from app.repositories.database import Base
from app.repositories import models, tasks  # noqa: F401

revision = "20260821_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is the one-time baseline for the existing V2 SQLAlchemy schema.
    # Subsequent revisions must use explicit Alembic operations.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
