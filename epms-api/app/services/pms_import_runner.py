"""Background runner for the legacy-PMS → EPMS import, driven by the admin page.

Manually triggered only (no scheduler). The import takes minutes, so each run is
executed in an asyncio background task; callers poll status. Run history is kept
in memory (last N runs) — sufficient for an admin tool and avoids a schema change.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger(__name__)

_MAX_HISTORY = 50


class SyncBusyError(RuntimeError):
    """Raised when a run is requested while one is already in progress."""


@dataclass
class SyncRun:
    id: str
    phase: str               # "full" | "incremental"
    dry_run: bool
    triggered_by: str
    status: str = "running"  # running | success | error
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    step: str = "starting"   # human-readable progress
    report: dict | None = None
    error: str | None = None

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("report", None)  # keep list payload light
        return d


_runs: list[SyncRun] = []
_current: SyncRun | None = None


def list_runs() -> list[dict]:
    return [r.summary() for r in _runs]


def get_run(run_id: str) -> dict | None:
    for r in _runs:
        if r.id == run_id:
            return asdict(r)
    return None


def current_run() -> dict | None:
    return _current.summary() if _current else None


def start_run(phase: str, dry_run: bool, triggered_by: str) -> dict:
    """Kick off a background sync. Raises SyncBusyError if one is running."""
    global _current
    if _current is not None and _current.status == "running":
        raise SyncBusyError("A PMS import is already running.")
    if phase not in ("full", "incremental"):
        raise ValueError("phase must be 'full' or 'incremental'")

    run = SyncRun(id=uuid.uuid4().hex, phase=phase, dry_run=dry_run, triggered_by=triggered_by)
    _current = run
    _runs.insert(0, run)
    del _runs[_MAX_HISTORY:]
    asyncio.create_task(_execute(run))
    return run.summary()


async def _execute(run: SyncRun) -> None:
    global _current
    try:
        # Import lazily so the app boots even if scripts/ deps shift.
        from scripts.import_pms import state
        from scripts.import_pms.extract import extract
        from scripts.import_pms.load import run_load

        if not settings.SP_USER or not settings.SP_PASSWORD:
            raise RuntimeError("SharePoint credentials not configured (SP_USER / SP_PASSWORD).")
        os.environ.setdefault("SP_USER", settings.SP_USER)
        os.environ.setdefault("SP_PASSWORD", settings.SP_PASSWORD)
        os.environ.setdefault("SP_TENANT", settings.SP_TENANT)
        os.environ.setdefault("SP_SITE", settings.SP_SITE)

        # Watermark captured BEFORE extract so we never miss changes made mid-run.
        run_started = state.now_iso()
        if run.phase == "incremental":
            since = state.get_last_sync()
            if not since:
                raise RuntimeError("No previous sync watermark — run a full import first.")
            load_mode = "upsert"
        else:
            since = None
            load_mode = "insert"

        run.step = f"extracting from SharePoint ({run.phase})"
        logger.info("PMS import %s: extracting (since=%s)", run.id, since)
        await asyncio.to_thread(extract, None, since)  # sync httpx → off the event loop

        run.step = "loading into EPMS" + (" (dry-run)" if run.dry_run else "")
        logger.info("PMS import %s: loading (mode=%s dry_run=%s)", run.id, load_mode, run.dry_run)
        report = await run_load(dry_run=run.dry_run, mode=load_mode)
        run.report = report.to_dict(run.dry_run)

        # Invoice attachments (uploads to the file server on a committed run).
        from scripts.import_pms.attachments import sync_invoice_attachments
        run.step = "syncing invoice attachments"
        att = await sync_invoice_attachments(dry_run=run.dry_run)
        run.report["attachments"] = att.to_dict()

        # Advance the watermark only on a real (committed) run.
        if not run.dry_run:
            state.set_last_sync(run_started)
            run.report["watermark_advanced_to"] = run_started

        run.status = "success"
        run.step = "done"
        logger.info("PMS import %s: success", run.id)
    except Exception as exc:  # noqa: BLE001
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"
        run.step = "failed"
        logger.exception("PMS import %s failed", run.id)
    finally:
        run.finished_at = datetime.now(timezone.utc).isoformat()
        if _current is run:
            _current = None
