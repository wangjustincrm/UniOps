"""Alembic environment — uses sync psycopg2 URL for migrations."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base  # noqa: F401
import app.models  # noqa: F401  — ensures all model modules are registered

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)

target_metadata = Base.metadata

# The database is shared with every other service: autogenerate must only see
# this service's tables, or it would propose dropping everyone else's.
TABLE_PREFIX = "%%table_prefix%%"
VERSION_TABLE = "%%version_table%%"


def include_name(name, type_, parent_names) -> bool:  # noqa: ARG001
    if type_ == "schema":
        return name in (None, "public")
    if type_ == "table":
        return name == VERSION_TABLE or name.startswith(TABLE_PREFIX)
    return True


def include_object(obj, name, type_, reflected, compare_to) -> bool:  # noqa: ARG001
    if type_ == "table":
        return include_name(name, "table", {})
    table = getattr(obj, "table", None)
    return table is None or include_name(table.name, "table", {})


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        include_name=include_name, include_object=include_object,
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
            version_table=VERSION_TABLE,
            include_name=include_name, include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
