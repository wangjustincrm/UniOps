"""Alembic environment — uses sync psycopg2 URL for migrations."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base  # noqa: F401
from app.models.routing import ApprovalBackup, DeptRouting  # noqa: F401  — registers new tables

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# approval-api's Settings only exposes an asyncpg DATABASE_URL (no
# DATABASE_URL_SYNC property like budget-api) — swap the driver for the
# sync psycopg2 engine alembic needs.
config.set_main_option(
    "sqlalchemy.url", settings.database_url.replace("+asyncpg", "+psycopg2"),
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_approval",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            version_table="alembic_version_approval",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
