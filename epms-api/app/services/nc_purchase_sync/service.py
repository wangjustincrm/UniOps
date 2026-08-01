"""NC purchase (order + arrival) sync service — UI-triggered full / incremental.

Mirrors finance-api/app/services/nc_sync.py's run harness:
  * a module-level single-flight `threading.Lock` so two UI clicks can't both
    start a run,
  * a `nc_purchase_sync_runs` registry row per run with a STALE_AFTER sweep that
    auto-fails a crashed container's orphaned 'running' row,
  * a synchronous `_run_worker` (the API layer threads it) that reads NC
    (reader.fetch_nc) → maps (transform.transform) → writes over one psycopg2
    transaction (writer.upsert),
  * an incremental watermark carried on `watermark_to` (NC max_modifiedtime),
  * a superseded-run guard so a run swept as abandoned mid-flight never commits.

Full reload deletes the NC-sourced mirror first, but PRESERVES any NC PO that
has already been consumed downstream (a matched invoice with a gr_id — the
3-way gate condition) so a human's work is never destroyed.
"""
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import register_uuid
from sqlalchemy.engine.url import make_url

from app.core.config import settings
from app.services.nc_purchase_sync import reader, writer
from app.services.nc_purchase_sync.reader import nc_configured  # noqa: F401 — re-export
from app.services.nc_purchase_sync.transform import transform

register_uuid()

logger = logging.getLogger(__name__)

_start_lock = threading.Lock()
STALE_AFTER = timedelta(minutes=30)
FULL_CONFIRM = "FULL RELOAD"

# Earliest NC order date to import (dbilldate >= cutover). Overridable via an
# optional `nc_purchase_cutover` setting; defaults to a wide-open window.
_DEFAULT_CUTOVER = "2000-01-01 00:00:00"


class SyncAlreadyRunning(Exception):
    pass


def _pg_dsn() -> str:
    u = make_url(settings.DATABASE_URL)
    return (f"host={u.host} port={u.port or 5432} dbname={u.database} "
            f"user={u.username} password={u.password}")


def _cutover() -> str:
    return getattr(settings, "nc_purchase_cutover", None) or _DEFAULT_CUTOVER


def latest_watermark(cur) -> str | None:
    """Newest successful watermark_to (NC modifiedtime) — feeds the next
    incremental run's `modifiedtime >= :wm` filter."""
    cur.execute("select watermark_to from nc_purchase_sync_runs "
                "where status='success' and watermark_to is not null "
                "order by started_at desc limit 1")
    row = cur.fetchone()
    return row[0] if row else None


def ensure_system_user_sync(cur) -> uuid.UUID:
    """Re-export of writer.ensure_system_user_sync for API-layer convenience."""
    return writer.ensure_system_user_sync(cur)


def _mark(dsn, run_id, **fields):
    """Small autocommit update on the run row over its OWN connection (the worker's
    main connection is mid-transaction). With no fields it is a bare heartbeat that
    only refreshes updated_at, keeping a legitimately-long run from being swept as
    stale by start_run's STALE_AFTER sweep."""
    con = psycopg2.connect(dsn); con.autocommit = True
    cur = con.cursor()
    sets = "".join(f"{k} = %s, " for k in fields)
    cur.execute(f"update nc_purchase_sync_runs set {sets}updated_at = now() where id = %s",
                (*fields.values(), run_id))
    con.close()


def _mark_terminal(dsn, run_id, **fields):
    """Terminal update that refuses to overwrite an already-terminal row (a run
    swept as abandoned must not flip back to success)."""
    con = psycopg2.connect(dsn); con.autocommit = True
    cur = con.cursor()
    sets = ", ".join(f"{k} = %s" for k in fields)
    cur.execute(f"update nc_purchase_sync_runs set {sets}, updated_at = now() "
                f"where id = %s and status = 'running'",
                (*fields.values(), run_id))
    con.close()


def _full_reload_delete(cur) -> int:
    """Delete the NC-sourced mirror ahead of a full reload, but PRESERVE any NC
    PO a human/downstream doc already touched. Returns the number of POs kept.

    "Touched" = referenced by ANY invoice row, not just a consumed (matched+gr)
    one. `invoices.po_id` is ON DELETE RESTRICT, so deleting a PO that any invoice
    points at (even a draft/unmatched one) would raise and abort the whole run —
    so the exclusion has to be broader than the writer's per-row consumed guard.
    GRs are deleted before POs (goods_receipts.po_id is ON DELETE RESTRICT);
    po_line_items / gr_line_items cascade."""
    cur.execute(
        "select id from purchase_orders po where po.source='nc' "
        "and exists (select 1 from invoices i where i.po_id = po.id)")
    protected = [pid for (pid,) in cur.fetchall()]
    if protected:
        cur.execute("delete from goods_receipts where source='nc' "
                    "and not (po_id = any(%s))", (protected,))
        cur.execute("delete from purchase_orders where source='nc' "
                    "and not (id = any(%s))", (protected,))
    else:
        cur.execute("delete from goods_receipts where source='nc'")
        cur.execute("delete from purchase_orders where source='nc'")
    return len(protected)


def start_run(mode: str, started_by, *, fetch=None, pg_dsn: str | None = None,
              run_worker: bool = True):
    """Single-flight gate + run-row insert. Synchronous — the API layer threads
    it. Returns the new run id. Raises SyncAlreadyRunning if a live run exists."""
    if fetch is None:
        fetch = reader.fetch_nc
    dsn = pg_dsn or _pg_dsn()
    with _start_lock:
        con = psycopg2.connect(dsn); con.autocommit = True
        cur = con.cursor()
        # auto-fail stale 'running' rows (crashed container), then check liveness
        cur.execute("update nc_purchase_sync_runs set status='failed', error='abandoned', "
                    "finished_at=now(), updated_at=now() "
                    "where status='running' and updated_at < %s",
                    (datetime.now(timezone.utc) - STALE_AFTER,))
        cur.execute("select id from nc_purchase_sync_runs where status='running'")
        if cur.fetchone():
            con.close()
            raise SyncAlreadyRunning("an NC purchase sync is already running")
        run_id = uuid.uuid4()
        cur.execute("insert into nc_purchase_sync_runs (id, mode, status, started_by, "
                    "started_at, created_at, updated_at) "
                    "values (%s, %s, 'running', %s, now(), now(), now())",
                    (run_id, mode, started_by))
        con.close()
    if run_worker:
        _run_worker(run_id, mode, fetch, dsn)
    return run_id


def _run_worker(run_id, mode: str, fetch, dsn: str) -> None:
    con = None
    try:
        con = psycopg2.connect(dsn); con.autocommit = False
        cur = con.cursor()
        prev_wm = latest_watermark(cur)
        raw = fetch(_cutover(), prev_wm if mode == "incremental" else None)

        vendor_by_erp = writer.load_vendor_map(cur)
        payload = transform(raw, vendor_by_erp)
        system_user_id = writer.ensure_system_user_sync(cur)

        full_skipped = _full_reload_delete(cur) if mode == "full" else 0

        # Heartbeat the run row every ~500 rows so the initial full load (~1680
        # orders / 6011 arrival lines, row-by-row) refreshes updated_at and isn't
        # swept as stale (STALE_AFTER) mid-run.
        counts = writer.upsert(cur, payload, system_user_id,
                               heartbeat=lambda: _mark(dsn, run_id))

        # superseded-run guard: if the sweeper already marked us abandoned, bail
        # without committing so we don't resurrect a dead run.
        cur.execute("select status from nc_purchase_sync_runs where id = %s for update", (run_id,))
        row = cur.fetchone()
        if not row or row[0] != "running":
            con.rollback(); con.close()
            return
        con.commit(); con.close()

        wm_to = raw.get("max_modifiedtime") or prev_wm
        # In full mode the delete-guard count is authoritative for skipped_consumed
        # (upsert would re-count the same POs); in incremental it comes from upsert.
        skipped_consumed = full_skipped if mode == "full" else counts["skipped_consumed"]
        _mark_terminal(dsn, run_id, status="success",
                       finished_at=datetime.now(timezone.utc),
                       pos_upserted=counts["pos_upserted"],
                       po_lines_upserted=counts["po_lines_upserted"],
                       grs_upserted=counts["grs_upserted"],
                       gr_lines_upserted=counts["gr_lines_upserted"],
                       skipped_no_vendor=len(payload.get("skipped_no_vendor", [])),
                       skipped_consumed=skipped_consumed,
                       watermark_from=prev_wm, watermark_to=wm_to)
    except Exception as e:  # noqa: BLE001 — terminal state must always be written
        try:
            if con is not None:
                con.rollback(); con.close()
        except Exception:  # noqa: BLE001
            pass
        _mark_terminal(dsn, run_id, status="failed", error=str(e)[:2000],
                       finished_at=datetime.now(timezone.utc))
