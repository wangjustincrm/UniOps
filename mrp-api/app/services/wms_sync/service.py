"""WMS inventory lot sync — full-extract SNAPSHOT semantics.

`run_wms_sync` fetches the entire live extract, transforms every row (status
mapping loaded from the `mrp_status_mapping` table, never hardcoded here),
then replaces `wms_inventory_lots` wholesale: DELETE all rows + INSERT the
new set, in ONE transaction tagged with `sync_batch_id` (this run's UTC
timestamp string). This is intentionally NOT an upsert-by-key like the NC
mirrors (mdm-api's nc_bom_sync) — WMS lots that fully deplete (qty drops to 0)
simply vanish from the source query and must vanish from the mirror too, so
a snapshot replace is the correct semantics, not a merge that would leave
stale rows behind.

`fetch_inventory` is imported as a bare name (not accessed via the `reader`
module) so tests can `monkeypatch.setattr(service, "fetch_inventory", ...)`
the way the sibling nc_bom_sync tests do (see mdm-api/app/services/
nc_bom_sync/service.py's docstring for the same idiom).

Bind-parameter chunking: asyncpg refuses a single statement with more than
32767 bind parameters. A plain multi-row `insert().values(rows)` is one
statement with `len(rows) * n_cols` params, so at WMS's real volume (~3.5k
lots x 15 cols ~= 52k params) an unchunked insert would blow up — chunk to
stay safely under the limit, same idiom as mdm-api/app/services/
nc_bom_sync/canonical_sync.py's `_bulk_upsert`. The whole chunked delete+
insert still runs inside the ONE caller-managed transaction (no
intermediate commits) so a mid-loop failure leaves the previous snapshot
untouched — see the `except` branch below, which rolls back before writing
`last_error`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.status_mapping import MrpStatusMapping
from app.models.sync_state import MrpSyncState
from app.models.wms_inventory import WmsInventoryLot
from app.models.wms_lot_location import WmsLotLocation
from app.services.wms_sync.reader import (  # noqa: F401 (re-exported)
    fetch_inventory,
    fetch_lot_locations,
    wms_configured,
)
from app.services.wms_sync.transform import transform_lot, transform_lot_location

_SOURCE = "wms"
_PG_MAX_PARAMS = 32767  # asyncpg's hard per-statement bind-parameter limit


async def _load_mapping(db: AsyncSession) -> dict[str, str]:
    rows = (await db.execute(
        select(MrpStatusMapping.wms_code, MrpStatusMapping.mapped_status)
    )).all()
    return {code: status for code, status in rows}


async def _write_sync_state(
    db: AsyncSession, *, status: str, row_count: int, last_error: str | None,
) -> datetime:
    """Record the outcome of one attempt.

    `last_synced_at` moves on every attempt; `last_success_at` moves only when
    the mirror was actually replaced. Anything that wants to know how old the
    data on screen is must read the latter — see migration
    mrp16_sync_state_last_success. 'empty_extract' counts as an attempt, not a
    success: it kept the previous snapshot, so that snapshot's age is unchanged.
    """
    now = datetime.now(timezone.utc)
    fields = dict(status=status, last_error=last_error, row_count=row_count,
                  last_synced_at=now, updated_at=now)
    if status == "success":
        fields["last_success_at"] = now
    stmt = pg_insert(MrpSyncState).values(source=_SOURCE, **fields).on_conflict_do_update(
        index_elements=["source"], set_=fields,
    )
    await db.execute(stmt)
    return now


async def run_wms_sync(db: AsyncSession) -> dict:
    """Full snapshot sync. Returns {"lots": n, "synced_at": iso-ts} — or, if
    the live extract came back with zero rows, {"lots": <kept count>,
    "synced_at": ..., "skipped": True} WITHOUT touching wms_inventory_lots
    (see the empty-extract guard below).

    On any failure (reader/transform/DB), rolls back so the previous
    snapshot is left intact, records `last_error` on `mrp_sync_state`
    (source='wms') in a fresh transaction, and re-raises.
    """
    try:
        # fetch_inventory() is a blocking oracledb call (sync driver, thin
        # mode) — run it off the event loop so a slow/hung WMS read doesn't
        # stall every other request this service is handling concurrently.
        # Same idiom as mdm-api/app/services/nc_bom_sync/canonical_sync.py's
        # fetch_nc_bom() wrapping (modern equivalent of epms-api/app/api/v1/
        # nc_purchase_sync.py's run_in_executor(None, ...)).
        raw_rows = await asyncio.to_thread(fetch_inventory)
        # Locations come from a second extract, read in the same run and
        # written in the same transaction, so the two can never describe
        # different moments -- a lot present in one and absent from the other
        # would show stock sitting nowhere, or a location holding a lot that
        # no longer exists.
        raw_locations = await asyncio.to_thread(fetch_lot_locations)
        mapping = await _load_mapping(db)
        # UTC, not container-local time — which lots flip to 'expired' must
        # not depend on the host/container TZ (see transform_lot's expiry
        # override, which compares expiry_date < today).
        today = datetime.now(timezone.utc).date()
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")

        rows = [transform_lot(raw, mapping, today) for raw in raw_rows]
        location_rows = [transform_lot_location(raw) for raw in raw_locations]

        if not rows:
            # Refuse to replace a real snapshot with an empty one. A
            # delete-all + insert-0 here would silently write
            # status='success' row_count=0 to mrp_sync_state, which Phase
            # 1's purchase-suggestion logic would read as "zero stock
            # everywhere" and propose buying material that is actually
            # sitting in the warehouse. Record the refusal (status=
            # 'empty_extract') and leave wms_inventory_lots untouched —
            # this is a distinct, non-exception early return (not routed
            # through the `except` branch below) so it doesn't get
            # re-recorded as a generic status='failed'/row_count=0 error.
            kept = (await db.execute(
                select(func.count()).select_from(WmsInventoryLot))).scalar()
            synced_at = await _write_sync_state(
                db, status="empty_extract", row_count=kept,
                last_error=(
                    "WMS extract returned 0 rows; refused to replace the "
                    f"snapshot (previous snapshot of {kept} lot(s) kept)."
                ),
            )
            await db.commit()
            return {"lots": kept, "synced_at": synced_at.isoformat(), "skipped": True}

        for row in rows:
            row["sync_batch_id"] = batch_id
        for row in location_rows:
            row["sync_batch_id"] = batch_id

        await db.execute(delete(WmsInventoryLot))
        await db.execute(delete(WmsLotLocation))

        if rows:
            n_cols = len(rows[0])
            chunk_size = max(1, (_PG_MAX_PARAMS // 2) // n_cols)
            for i in range(0, len(rows), chunk_size):
                batch = rows[i:i + chunk_size]
                await db.execute(insert(WmsInventoryLot).values(batch))

        if location_rows:
            n_cols = len(location_rows[0])
            chunk_size = max(1, (_PG_MAX_PARAMS // 2) // n_cols)
            for i in range(0, len(location_rows), chunk_size):
                await db.execute(
                    insert(WmsLotLocation).values(location_rows[i:i + chunk_size]))

        synced_at = await _write_sync_state(db, status="success", row_count=len(rows), last_error=None)
        await db.commit()
        # row_count on mrp_sync_state stays the LOT count: it is what the
        # empty-extract guard compares against and what every existing reader
        # means by "how much stock is mirrored". Locations are reported
        # alongside rather than folded in.
        return {"lots": len(rows), "locations": len(location_rows),
                "synced_at": synced_at.isoformat()}
    except Exception as exc:
        await db.rollback()
        await _write_sync_state(db, status="failed", row_count=0, last_error=str(exc))
        await db.commit()
        raise
