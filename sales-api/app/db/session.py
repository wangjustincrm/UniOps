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
    # No SQL echo, also with DEBUG (user 2026-11-26: dev2 wrote ~1.4 GB a day of it to its container log, parameters
    # included). To see the statements for a while: Health → Log levels, "sqlalchemy.engine" at INFO (it expires).
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args=CONNECT_ARGS,
)

# Every session's options, the live ones and the tests' (a guard holds it: tests/test_session_options.py).
SESSION_OPTIONS = {"expire_on_commit": False, "autoflush": False, "autocommit": False}

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, **SESSION_OPTIONS)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
