"""Shared chunked-upsert-by-key helper.

Both NC65 BOM sync call sites upsert at real NC volumes and need the same
asyncpg 32767-bind-parameter-per-statement chunking math:
  - `nc_bom_sync/service.py` (raw mirror, Task 4) — nc_bom_b alone is ~58
    cols x 10169 rows (~590k params) in one INSERT.
  - `nc_bom_sync/canonical_sync.py` (canonical tables, Task 5) — same idiom,
    plus it needs each upsert to RETURN (id, key) so the next table's rows
    can resolve their parent FK without a second SELECT round trip.

This module is the single place that builds the `ON CONFLICT (key) DO
UPDATE` column set, so both call sites share one bug surface instead of two
independently-drifting copies. The column set always excludes `id` (the
surrogate PK, never written) and `created_at` (a re-sync must NOT reset when
a row was first created — the bug this module fixes: canonical_sync.py's
copy already excluded it, nc_bom_sync/service.py's copy didn't) in addition
to the conflict key itself.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

_PG_MAX_PARAMS = 32767  # asyncpg's hard per-statement bind-parameter limit
_ALWAYS_EXCLUDED = {"id", "created_at"}


def _chunk_size(n_cols: int) -> int:
    return max(1, (_PG_MAX_PARAMS // 2) // n_cols)


async def _upsert_chunks(
    db: AsyncSession, model, rows: list[dict], conflict_key: str, *, returning: bool,
) -> dict:
    """Runs the chunked `ON CONFLICT DO UPDATE` loop. Returns {key: id} when
    `returning` (empty dict otherwise) — always dict-shaped so both public
    wrappers below can share this one implementation."""
    id_by_key: dict = {}
    if not rows:
        return id_by_key
    n_cols = len(rows[0])
    chunk_size = _chunk_size(n_cols)
    excluded_cols = _ALWAYS_EXCLUDED | {conflict_key}
    for i in range(0, len(rows), chunk_size):
        batch = rows[i:i + chunk_size]
        stmt = pg_insert(model).values(batch)
        update_cols = {
            c.name: getattr(stmt.excluded, c.name)
            for c in model.__table__.columns
            if c.name not in excluded_cols
        }
        stmt = stmt.on_conflict_do_update(index_elements=[conflict_key], set_=update_cols)
        if returning:
            key_col = getattr(model, conflict_key)
            stmt = stmt.returning(model.id, key_col)
            result = await db.execute(stmt)
            for row_id, key_val in result.all():
                id_by_key[key_val] = row_id
        else:
            await db.execute(stmt)
    return id_by_key


async def chunked_upsert_returning(
    db: AsyncSession, model, rows: list[dict], conflict_key: str = "nc_source_pk",
) -> dict:
    """Chunked upsert; returns {conflict_key value: id} for every row
    post-upsert (insert or update) so a caller can resolve FK-dependent rows
    in the next table without a second SELECT round trip. Used by
    canonical_sync.py."""
    return await _upsert_chunks(db, model, rows, conflict_key, returning=True)


async def chunked_upsert_count(
    db: AsyncSession, model, rows: list[dict], conflict_key: str = "nc_source_pk",
) -> int:
    """Chunked upsert; returns the table's total row count afterward
    (idempotent — reruns with the same rows report the same count). Used by
    nc_bom_sync/service.py's raw-mirror sync."""
    await _upsert_chunks(db, model, rows, conflict_key, returning=False)
    return (await db.execute(select(func.count()).select_from(model))).scalar()
