"""Async SQLAlchemy engine and session factory."""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# JIT off for Sales' connections: the list and dashboard queries carry the computed Status (a
# CASE over many correlated subqueries), whose JIT compilation alone took seconds (Orders list
# 10.5 s → 0.37 s, dashboard 5.3 s → 1.2 s over 8755 orders). Short OLTP queries never gain from it.
CONNECT_ARGS = {"ssl": False, "server_settings": {"jit": "off"}}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args=CONNECT_ARGS,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
