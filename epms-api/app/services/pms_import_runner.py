"""Background runner for the legacy-PMS → EPMS import, driven by the admin page.

Manually triggered only (no scheduler). The import takes minutes, so each run is
executed in an asyncio background task; callers poll status. Run history is
persisted to a small JSON file (via scripts.import_pms.state) so it survives
container restarts/redeploys — previously it lived only in process memory and
vanished on every restart, showing "No runs yet" even after a successful import.

Assumes a single API worker (prod runs uvicorn without --workers); the in-memory
list mirrors the file and is the source of truth for the running worker.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger(__name__)

_MAX_HISTORY = 50


class SyncBusyError(RuntimeError):
    """Raised when a run is requested while one is already in progress."""


@dataclass
class SyncRun:
    id: str
    phase: str               # "full" | "incremental" | "attachments"
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


_runs: list[SyncRun] | None = None  # lazily loaded from disk on first access
_current: SyncRun | None = None


def _known_fields() -> set[str]:
    return {f.name for f in fields(SyncRun)}


def _load_runs() -> list[SyncRun]:
    """Read persisted history, skipping schema-incompatible legacy rows and
    marking any run left as 'running' (process died mid-import) as an error."""
    from scripts.import_pms import state
    known = _known_fields()
    out: list[SyncRun] = []
    for d in state.load_runs():
        if not isinstance(d, dict):
            continue
        d = {k: v for k, v in d.items() if k in known}
        try:
            run = SyncRun(**d)
        except TypeError:
            continue
        if run.status == "running":
            run.status = "error"
            run.step = "interrupted"
            run.error = run.error or "Interrupted — the service restarted during this run."
            if run.finished_at is None:
                run.finished_at = datetime.now(timezone.utc).isoformat()
        out.append(run)
    return out


def _ensure_loaded() -> list[SyncRun]:
    global _runs
    if _runs is None:
        _runs = _load_runs()
    return _runs


def _persist() -> None:
    from scripts.import_pms import state
    state.save_runs([asdict(r) for r in _ensure_loaded()])


def list_runs() -> list[dict]:
    return [r.summary() for r in _ensure_loaded()]


def get_run(run_id: str) -> dict | None:
    for r in _ensure_loaded():
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
    if phase not in ("full", "incremental", "attachments"):
        raise ValueError("phase must be 'full', 'incremental' or 'attachments'")

    runs = _ensure_loaded()
    run = SyncRun(id=uuid.uuid4().hex, phase=phase, dry_run=dry_run, triggered_by=triggered_by)
    _current = run
    runs.insert(0, run)
    del runs[_MAX_HISTORY:]
    _persist()
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

        # Attachments-only phase: import PR/PO/PA list attachments, nothing else.
        if run.phase == "attachments":
            from scripts.import_pms.extract import DOC_ATTACHMENT_SPEC, extract_list_attachments
            from scripts.import_pms.sharepoint import SharePointClient
            from scripts.import_pms.attachments import sync_doc_attachments

            run.step = "extracting PR/PO/PA attachments from SharePoint"
            _persist()

            def _extract_all() -> None:
                sp = SharePointClient()
                for spec in DOC_ATTACHMENT_SPEC.values():
                    extract_list_attachments(sp, spec, since=None)  # full, idempotent

            await asyncio.to_thread(_extract_all)

            run.step = "loading attachments into EPMS" + (" (dry-run)" if run.dry_run else "")
            _persist()
            doc_att: dict = {}
            for kind in ("pr", "po", "pa"):
                rep = await sync_doc_attachments(kind, dry_run=run.dry_run)
                doc_att[kind] = rep.to_doc_dict()
            run.report = {"dry_run": run.dry_run, "doc_attachments": doc_att}
            run.status = "success"
            run.step = "done"
            logger.info("PMS attachment import %s: success", run.id)
            return  # `finally` still stamps finished_at, persists, clears _current

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
        _persist()
        logger.info("PMS import %s: extracting (since=%s)", run.id, since)
        await asyncio.to_thread(extract, None, since)  # sync httpx → off the event loop

        run.step = "loading into EPMS" + (" (dry-run)" if run.dry_run else "")
        _persist()
        logger.info("PMS import %s: loading (mode=%s dry_run=%s)", run.id, load_mode, run.dry_run)
        report = await run_load(dry_run=run.dry_run, mode=load_mode)
        run.report = report.to_dict(run.dry_run)

        # Invoice attachments (uploads to the file server on a committed run).
        from scripts.import_pms.attachments import sync_invoice_attachments
        run.step = "syncing invoice attachments"
        _persist()
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
        _persist()
        if _current is run:
            _current = None
