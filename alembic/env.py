"""Alembic environment.

The database URL is resolved from backend/core/config.py rather than alembic.ini, so
migrations and the running app can never point at different databases. Override for a
one-off target with `alembic -x db_url=mysql+pymysql://... upgrade head`.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import backend.models  # noqa: F401  — ensures all model modules are imported
from alembic import context
from backend.core.config import get_settings

# Importing Base is enough to register every model on its metadata.
from backend.core.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override
    return get_settings().resolved_database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DBAPI connection (`alembic upgrade head --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        section = config.get_section(config.config_ini_section) or {}
        section["sqlalchemy.url"] = _database_url()
        connectable = engine_from_config(
            section,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            _run(connection)
        connectable.dispose()
    else:
        # Reached when a caller hands us an existing connection (tests).
        _run(connectable)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # Needed for SQLite; harmless for MySQL, and keeps local autogenerate usable.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()