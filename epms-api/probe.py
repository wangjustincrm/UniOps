import asyncio, os, uuid
os.environ.setdefault("DATABASE_URL","postgresql+asyncpg://epms:epms_dev@uniops_postgres:5432/epms")
import sys; sys.path.insert(0,"/app")
import tests.conftest as cf
from httpx import AsyncClient, ASGITransport

async def main():
    # build test engine + patched session like conftest
    from app.db.session import get_db
    from app.main import app
    eng = cf.create_async_engine(cf._TEST_DB_URL, echo=False)
    from app.db.base import Base
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("probe: schema ready")
asyncio.run(main())
