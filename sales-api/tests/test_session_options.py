"""Every session — live and in the tests — is made with the same options (app/db/session.SESSION_OPTIONS): a test that flushes by itself
where the live code does not would pass for that reason alone."""
import ast
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

import app as package
from app.db.session import SESSION_OPTIONS, AsyncSessionLocal

ROOT = Path(package.__path__[0]).parent
MAKERS = {"async_sessionmaker", "sessionmaker", "AsyncSession", "Session", "async_scoped_session", "scoped_session", "create_session"}


def made(source: str) -> list[bool]:
    """Every place a session (or its factory) is made → whether it takes the options and nothing beside them."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and (node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")) in MAKERS:
            own = {k.arg for k in node.keywords if k.arg}
            out.append(any(k.arg is None and getattr(k.value, "id", "") == "SESSION_OPTIONS" for k in node.keywords) and not own & set(SESSION_OPTIONS))
    return out


def test_every_session_is_made_with_the_one_set_of_options():
    found = {p.relative_to(ROOT).as_posix(): made(p.read_text()) for d in ("app", "tests") for p in (ROOT / d).rglob("*.py")}
    found = {k: v for k, v in found.items() if v}
    assert len(found) >= 9 and {k for k, v in found.items() if not all(v)} == set()


def test_the_live_factory_s_options_are_those():
    assert {k: AsyncSessionLocal.kw[k] for k in SESSION_OPTIONS} == SESSION_OPTIONS and SESSION_OPTIONS["autoflush"] is False
    s: AsyncSession = AsyncSessionLocal()
    assert s.sync_session.autoflush is False and s.sync_session.expire_on_commit is False


def test_the_check_sees_what_it_must():
    assert made("async_sessionmaker(e, **SESSION_OPTIONS)") == [True]
    for bad in ("async_sessionmaker(e, expire_on_commit=False)", "x.async_sessionmaker(e)", "AsyncSession(e)", "async_sessionmaker(e, **SESSION_OPTIONS | {})",
                "async_sessionmaker(e, autoflush=True, **SESSION_OPTIONS)"):
        assert made(bad) == [False], bad
