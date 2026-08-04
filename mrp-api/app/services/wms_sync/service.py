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

from datetime import date, datetime, timezone

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.status_mapping import MrpStatusMapping
from app.models.sync_state import MrpSyncState
from app.models.wms_inventory import WmsInventoryLot
from app.services.wms_sync.reader import fetch_inventory, wms_configured  # noqa: F401 (re-exported)
from app.services.wms_sync.transform import transform_lot

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
    now = datetime.now(timezone.utc)
    stmt = pg_insert(MrpSyncState).values(
        source=_SOURCE, status=status, last_error=last_error,
        row_count=row_count, last_synced_at=now, updated_at=now,
    ).on_conflict_do_update(
        index_elements=["source"],
        set_=dict(status=status, last_error=last_error, row_count=row_count,
                   last_synced_at=now, updated_at=now),
    )
    await db.execute(stmt)
    return now


async def run_wms_sync(db: AsyncSession) -> dict:
    """Full snapshot sync. Returns {"lots": n, "synced_at": iso-ts}.

    On any failure (reader/transform/DB), rolls back so the previous
    snapshot is left intact, records `last_error` on `mrp_sync_state`
    (source='wms') in a fresh transaction, and re-raises.
    """
    try:
        raw_rows = fetch_inventory()
        mapping = await _load_mapping(db)
        today = date.today()
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")

        rows = [transform_lot(raw, mapping, today) for raw in raw_rows]
        for row in rows:
            row["sync_batch_id"] = batch_id

        await db.execute(delete(WmsInventoryLot))

        if rows:
            n_cols = len(rows[0])
            chunk_size = max(1, (_PG_MAX_PARAMS // 2) // n_cols)
            for i in range(0, len(rows), chunk_size):
                batch = rows[i:i + chunk_size]
                await db.execute(insert(WmsInventoryLot).values(batch))

        synced_at = await _write_sync_state(db, status="success", row_count=len(rows), last_error=None)
        await db.commit()
        return {"lots": len(rows), "synced_at": synced_at.isoformat()}
    except Exception as exc:
        await db.rollback()
        await _write_sync_state(db, status="failed", row_count=0, last_error=str(exc))
        await db.commit()
        raise
