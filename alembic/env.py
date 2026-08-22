import os
import sys

from alembic import context

# Alembic loads this module from /app/alembic, so include the project root
# before importing application metadata.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.core import get_settings
from app.repositories.database import Base
from app.repositories import models, tasks  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().mysql_url)
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    from sqlalchemy import create_engine
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()

if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
