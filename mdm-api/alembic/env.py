import asyncio
from logging.config import fileConfig
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context
from app.db.base import Base
from app.core.config import settings
from app.models import vendor, department, cost_center, part, user, company, erp_material, erp_supplier, erp_person, erp_sync_state, material, nc_bom, bom, material_supplier, uom_conversion, sync_state, uom  # noqa: F401
# M14 (final-phase review): sync_state (NcSyncState / nc_sync_state) and uom
# (UnitOfMeasure / units_of_measure) were both missing from this import list
# — target_metadata is Base.metadata, which only gets populated with the
# tables whose model modules have actually been imported somewhere. Without
# these two, a future `alembic revision --autogenerate` would see those
# tables as "not in metadata" and propose DROP TABLE statements for them.

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table="alembic_version_mdm",
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version_mdm",
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
