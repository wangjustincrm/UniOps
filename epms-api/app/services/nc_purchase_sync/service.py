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
from app.services.nc_purchase_sync import error_tasks, reader, writer
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


def _cutover(cur=None) -> str:
    """Effective cutover. Prefer the admin-set value in company_config (read via
    the given psycopg2 cursor); fall back to the env setting, then the default."""
    if cur is not None:
        try:
            cur.execute("select nc_purchase_cutover from company_config limit 1")
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
    return getattr(settings, "nc_purchase_cutover", None) or _DEFAULT_CUTOVER


def clamp_watermark_for_skipped(orders, skipped_numbers, wm_to, prev_wm) -> str | None:
    """Hold the incremental watermark AT the earliest order this run could not
    import, so the next run reads it again.

    The watermark filter is ``changed_at >= watermark``. Advancing past an order
    the run DROPPED (no vendor, no free document number) makes that order
    invisible to every future incremental run: fixing the cause then changes
    nothing, because the sync never looks at the order again. That is how
    PO-029-2609-01 and four August orders went missing — each was read, skipped
    for a missing vendor, and left behind by a watermark that moved on regardless.

    Holding the position means those orders are re-read (and re-skipped, and the
    Admin task refreshed) every run until somebody fixes the cause — at which
    point the very next run imports them with no manual step. The cost is that
    the window stops shrinking while the problem is open; the Admin task is what
    keeps that from being permanent.

    Never below ``prev_wm``: an order can enter a run through the ARRIVAL branch
    with a change time far older than the watermark, and clamping to that would
    walk the watermark backwards over months of history every run. Standing
    still is enough — the same arrival still qualifies next run.
    """
    if wm_to is None or not skipped_numbers:
        return wm_to
    stamps = [ca for ca in (reader.changed_at(o) for o in orders
                            if o.get("vbillcode") in skipped_numbers) if ca]
    if not stamps:
        return wm_to
    held = min(wm_to, min(stamps))
    if prev_wm is not None and held < prev_wm:
        held = prev_wm
    return held


def pending_refetch_pks(cur) -> list:
    """NC order pks with a standing re-fetch request (see the
    NcPurchaseRefetchRequest model). Read on every run; normally empty."""
    # to_regclass, not try/except: in psycopg2 a failed statement poisons the
    # WHOLE transaction, so probing for the table by querying it would take the
    # mirror write down with it on any deployment where the migration has not
    # run yet (and in the test DB, which is built from create_all).
    cur.execute("select to_regclass('nc_purchase_refetch_requests')")
    if cur.fetchone()[0] is None:
        return []
    cur.execute("select nc_source_pk from nc_purchase_refetch_requests "
                "where fulfilled_at is null")
    return [r[0] for r in cur.fetchall()]


def settle_refetch_requests(cur, requested, upserted_pks, in_scope_pks) -> None:
    """Close the standing requests this run answered.

    'mirrored' — the order was written back, so the delete has healed.
    'gone_from_nc' — NC no longer lists the order at all, so there is nothing
    left to re-read and retrying every run forever would be noise.
    Anything else (the order reached the batch but was skipped for a missing
    vendor or a taken number) stays PENDING on purpose: the request is the only
    thing that will bring it back once the cause is fixed.
    """
    if not requested:
        return
    upserted = [pk for pk in requested if pk in upserted_pks]
    gone = ([pk for pk in requested if pk not in in_scope_pks]
            if in_scope_pks is not None else [])
    for pks, outcome in ((upserted, "mirrored"), (gone, "gone_from_nc")):
        if not pks:
            continue
        cur.execute("update nc_purchase_refetch_requests set fulfilled_at=now(), "
                    "outcome=%s, updated_at=now() "
                    "where nc_source_pk = any(%s) and fulfilled_at is null",
                    (outcome, list(pks)))


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
        "and (exists (select 1 from invoices i where i.po_id = po.id) "
        # A buyer-added line (nc_source_pk IS NULL on a mirrored PO) exists in
        # no ERP and cannot be rebuilt by the reload that is about to run: a
        # one-off mould/tooling charge NC has no way to carry. Deleting the PO
        # cascades it away for good, along with the money it puts on the header.
        # The reload's job is to rebuild what NC owns, not to discard what it
        # never held.
        "     or exists (select 1 from po_line_items l "
        "                where l.po_id = po.id and l.nc_source_pk is null))")
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
        refetch = pending_refetch_pks(cur) if mode == "incremental" else []
        if refetch:
            logger.info("nc purchase sync %s: %d standing re-fetch request(s) join "
                        "this run regardless of the watermark", run_id, len(refetch))
        raw = fetch(_cutover(cur), prev_wm if mode == "incremental" else None,
                    refetch_pks=refetch)

        vendor_by_erp = writer.load_vendor_map(cur)
        payload = transform(raw, vendor_by_erp)
        system_user_id = writer.ensure_system_user_sync(cur)

        full_skipped = _full_reload_delete(cur) if mode == "full" else 0

        # Withdraw first, upsert second: an order NC approved between runs is
        # still in scope, so it survives this pass and the upsert below gives it
        # its real status. Skipped entirely when the reader did not report a
        # scope set (a stubbed fetch in tests), because an empty set means
        # "NC lists nothing" and would cancel every mirrored pending PO.
        in_scope_pks = raw.get("in_scope_pks")
        if in_scope_pks is not None:
            withdrawn = writer.reconcile_pending(cur, in_scope_pks)
            if withdrawn:
                logger.info("nc purchase sync %s: cancelled %d pending PO(s) NC "
                            "no longer lists", run_id, withdrawn)

        # Heartbeat the run row every ~500 rows so the initial full load (~1680
        # orders / 6011 arrival lines, row-by-row) refreshes updated_at and isn't
        # swept as stale (STALE_AFTER) mid-run.
        counts = writer.upsert(cur, payload, system_user_id,
                               heartbeat=lambda: _mark(dsn, run_id))

        # What this run could NOT import goes to the Admin Task Inbox, naming the
        # order and the reason. Behind a SAVEPOINT: the mirror write is the job,
        # and a failure in the reporting must not abort the transaction carrying
        # it (in psycopg2 any error poisons the whole transaction, so catching
        # the exception without the savepoint would still lose the run).
        # Same transaction as the mirror write: a request must not read as
        # settled unless the rows that settle it committed with it.
        settle_refetch_requests(cur, refetch,
                                set(counts.get("upserted_po_pks") or []),
                                in_scope_pks)

        cur.execute("savepoint nc_error_tasks")
        try:
            error_tasks.record_import_errors(
                cur, run_id,
                vendor_gaps=payload.get("vendor_gaps"),
                collision_numbers=counts.get("collision_numbers"),
                in_scope_numbers=raw.get("in_scope_numbers"),
                created_by=system_user_id)
            error_tasks.clear_run_failure(cur)
        except Exception:  # noqa: BLE001 — reporting never breaks the mirror
            cur.execute("rollback to savepoint nc_error_tasks")
            logger.exception("nc purchase sync %s: error tasks not written", run_id)

        # superseded-run guard: if the sweeper already marked us abandoned, bail
        # without committing so we don't resurrect a dead run.
        cur.execute("select status from nc_purchase_sync_runs where id = %s for update", (run_id,))
        row = cur.fetchone()
        if not row or row[0] != "running":
            con.rollback(); con.close()
            return
        con.commit(); con.close()

        wm_to = raw.get("max_modifiedtime") or prev_wm
        # Do not step over what this run could not import — see
        # clamp_watermark_for_skipped. Only the fixable skips count: a PO skipped
        # because a human already consumed it downstream (skipped_consumed) is
        # skipped forever, and holding for it would freeze the watermark for good.
        skipped_numbers = {g["number"] for g in (payload.get("vendor_gaps") or [])}
        skipped_numbers |= set(counts.get("collision_numbers") or [])
        held_wm = clamp_watermark_for_skipped(
            raw.get("orders") or [], skipped_numbers, wm_to, prev_wm)
        if held_wm != wm_to:
            logger.info("nc purchase sync %s: holding watermark at %s (was going to "
                        "advance to %s) — %d order(s) could not be imported: %s",
                        run_id, held_wm, wm_to, len(skipped_numbers),
                        sorted(skipped_numbers)[:20])
        wm_to = held_wm
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
                       renamed_number_collision=counts["renamed_number_collision"],
                       skipped_number_collision=counts["skipped_number_collision"],
                       watermark_from=prev_wm, watermark_to=wm_to)
    except Exception as e:  # noqa: BLE001 — terminal state must always be written
        try:
            if con is not None:
                con.rollback(); con.close()
        except Exception:  # noqa: BLE001
            pass
        _mark_terminal(dsn, run_id, status="failed", error=str(e)[:2000],
                       finished_at=datetime.now(timezone.utc))
        _record_failure_task(dsn, run_id, str(e))


def _record_failure_task(dsn: str, run_id, message: str) -> None:
    """Put a failed run in the Admin inbox, over its OWN connection — the
    worker's is rolled back and closed by the time this runs. Never raises: a
    sync that failed must still record its terminal state, and this is the
    reporting on top of that."""
    con = None
    try:
        con = psycopg2.connect(dsn); con.autocommit = False
        cur = con.cursor()
        error_tasks.record_run_failure(
            cur, run_id, message[:1000], writer.ensure_system_user_sync(cur))
        con.commit()
    except Exception:  # noqa: BLE001
        logger.exception("nc purchase sync %s: failure task not written", run_id)
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
