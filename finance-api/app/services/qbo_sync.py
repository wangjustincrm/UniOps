"""API-facing wrapper over the scripts-layer QBO import orchestrator.

The orchestrator is async and owns DB writes; to run it from a request without
blocking the event loop or borrowing the request session, we launch it on a
worker thread that builds its own async engine + session and asyncio.run()s it.
"""
import asyncio
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from scripts.qbo_import.client import ENV_PATH, load_cfg
from scripts.qbo_import.orchestrator import DEFAULT_ENTITIES, run_sync

STALE_AFTER = timedelta(minutes=30)
FULL_CONFIRM = "RELOAD"
_REQUIRED = ("CLIENT_ID", "CLIENT_SECRET", "REALM_ID", "REFRESH_TOKEN")


def qbo_configured() -> bool:
    if not ENV_PATH.exists():
        return False
    try:
        cfg = load_cfg(ENV_PATH)
    except Exception:  # noqa: BLE001
        return False
    return all(cfg.get(k) for k in _REQUIRED)


def launch_sync(mode: str, entities: list[str] | None, with_attachments: bool) -> None:
    """Run the orchestrator (and optional attachments) on a worker thread.

    run_sync creates and owns its QboSyncRun row and records failures on it.
    """
    def _worker() -> None:
        async def _go() -> None:
            engine = create_async_engine(settings.database_url, connect_args={"ssl": False})
            Session = async_sessionmaker(engine, expire_on_commit=False)
            from scripts.qbo_import.attachments import load_attachments
            from scripts.qbo_import.client import QboClient
            from scripts.qbo_import.extract import extract_entity
            client = QboClient()
            async with Session() as db:
                await run_sync(db, client, mode=mode, entities=entities or DEFAULT_ENTITIES)
            if with_attachments:
                async with Session() as db:
                    objs = extract_entity(client, "Attachable", since=None)
                    await load_attachments(db, objs)
            await engine.dispose()
        asyncio.run(_go())

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _worker)
