"""Canonical BOM sync: raw NC mirror refresh -> transform -> upsert
boms/bom_lines/bom_substitutes.

Two phases, matching the brief's "先跑 Task 4 原始镜像再转换" (run Task 4's
raw mirror sync first, then transform):
  1. Fetch the live NC extract ONCE (`fetch_nc_bom()`) and feed it to BOTH
     `nc_bom_sync.service.sync_nc_bom()` (refreshes the nc_bom/nc_bom_b/
     nc_bom_repl raw mirror, Task 4) and `transform()` (produces canonical
     row dicts, Task 5) — a single NC round trip serves both; the raw
     mirror's own `sync_nc_bom(db)` would otherwise re-fetch independently
     and lose the `material_codes` this function still needs.
  2. Chunked upsert-by-nc_source_pk into boms/bom_lines/bom_substitutes,
     same idiom as Task 4's `service._upsert_all` (asyncpg's 32767
     bind-parameter-per-statement limit applies here too), except each
     upsert also RETURNs (id, nc_source_pk) so the next table's rows can
     resolve their parent FK (bom_id / bom_line_id) without a second SELECT
     round trip.
"""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Bom, BomLine, BomSubstitute
from app.services.nc_bom_sync.reader import fetch_nc_bom
from app.services.nc_bom_sync.service import sync_nc_bom as sync_raw_mirror
from app.services.nc_bom_sync.transform import transform

_PG_MAX_PARAMS = 32767  # asyncpg's hard per-statement bind-parameter limit


async def _bulk_upsert(db: AsyncSession, model, rows: list[dict]) -> dict:
    """Chunked upsert-by-nc_source_pk. Returns {nc_source_pk: id} for every
    row post-upsert (insert or update, via ON CONFLICT DO UPDATE ...
    RETURNING), so FK-dependent rows in the next table can be resolved in
    memory."""
    id_by_pk: dict[str, object] = {}
    if not rows:
        return id_by_pk
    n_cols = len(rows[0])
    chunk_size = max(1, (_PG_MAX_PARAMS // 2) // n_cols)
    for i in range(0, len(rows), chunk_size):
        batch = rows[i:i + chunk_size]
        stmt = pg_insert(model).values(batch)
        update_cols = {
            c.name: getattr(stmt.excluded, c.name)
            for c in model.__table__.columns
            if c.name not in ("id", "nc_source_pk", "created_at")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["nc_source_pk"], set_=update_cols
        ).returning(model.id, model.nc_source_pk)
        result = await db.execute(stmt)
        for row_id, pk in result.all():
            id_by_pk[pk] = row_id
    return id_by_pk


async def sync_boms(db: AsyncSession) -> dict:
    """Full canonical sync. Returns row counts + skip/warning tallies so a
    caller can see resolution failures without digging through logs:
    {"boms": n, "lines": n, "substitutes": n, "skipped": n, "warnings": n}."""
    extract = fetch_nc_bom()
    await sync_raw_mirror(db, extract=extract)

    t = transform(extract)

    bom_id_by_pk = await _bulk_upsert(db, Bom, t["boms"])

    line_rows = []
    for row in t["lines"]:
        row = dict(row)
        bom_pk = row.pop("bom_nc_source_pk")
        bom_id = bom_id_by_pk.get(bom_pk)
        if bom_id is None:
            continue  # shouldn't happen — transform() already drops orphan lines
        row["bom_id"] = bom_id
        line_rows.append(row)
    line_id_by_pk = await _bulk_upsert(db, BomLine, line_rows)

    sub_rows = []
    for row in t["substitutes"]:
        row = dict(row)
        line_pk = row.pop("bom_line_nc_source_pk")
        line_id = line_id_by_pk.get(line_pk)
        if line_id is None:
            continue  # shouldn't happen — transform() already drops orphan substitutes
        row["bom_line_id"] = line_id
        sub_rows.append(row)
    sub_id_by_pk = await _bulk_upsert(db, BomSubstitute, sub_rows)

    await db.commit()
    return {
        "boms": len(bom_id_by_pk),
        "lines": len(line_id_by_pk),
        "substitutes": len(sub_id_by_pk),
        "skipped": len(t["skipped"]),
        "warnings": len(t["warnings"]),
    }
