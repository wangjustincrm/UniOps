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
  2. Chunked upsert-by-nc_source_pk into boms/bom_lines/bom_substitutes via
     the shared `chunked_upsert()` helper (`app/services/upsert.py` — also
     used by Task 4's raw-mirror sync, `nc_bom_sync/service.py`; asyncpg's
     32767 bind-parameter-per-statement limit applies here too), except each
     upsert also RETURNs (id, nc_source_pk) so the next table's rows can
     resolve their parent FK (bom_id / bom_line_id) without a second SELECT
     round trip.
  3. Tombstone: after upserting, delete any canonical row whose
     `nc_source_pk` is absent from THIS sync's resolved set — snapshot
     semantics for the resolved subset, matching mrp-api's WMS sync
     (app/services/wms_sync/service.py). Upsert alone is append/update-only:
     a line removed from a BOM in NC (or a whole header deleted/no-longer-
     resolvable), never disappearing from `nc_bom_sync.service.sync_nc_bom`'s
     re-fetch, would otherwise be planned forever. Runs in the SAME
     transaction as the upserts (one `db.commit()` at the end) so a
     mid-tombstone failure rolls back the whole sync, not just the deletes.
     Per-table empty-set guard: if a table's resolved set is empty this
     sync, its tombstone is SKIPPED (existing rows kept) rather than wiping
     the table — see `_tombstone()`'s docstring for why.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Bom, BomLine, BomSubstitute
from app.services.nc_bom_sync.reader import fetch_nc_bom
from app.services.nc_bom_sync.service import sync_nc_bom as sync_raw_mirror
from app.services.nc_bom_sync.transform import transform
from app.services.upsert import chunked_upsert_returning


async def _tombstone(db: AsyncSession, model, current_pks: set) -> tuple[int, bool]:
    """Delete rows of `model` whose nc_source_pk is NOT in `current_pks`
    (this sync's resolved set) — the canonical-table half of snapshot
    semantics. Returns (rows_deleted, skipped).

    `current_pks` empty is a REFUSAL case, not a "delete everything" case —
    same rationale as mrp-api's WMS empty-extract guard (wms_sync/service.py's
    `run_wms_sync`). An earlier version of this function reasoned that NC's
    BOM tables are always non-trivially populated (survey: 1016/10169/664
    rows) so an empty resolved set could only mean a genuine deletion
    cascade — but that is a diagnostic *expectation* about NC's data, not a
    technical guarantee: a transient/partial NC read for just ONE layer
    (headers, lines, or repl) that returns zero rows WITHOUT raising would
    make that layer's resolved set empty too, and SQLAlchemy's
    `notin_(<empty set>)` compiles to an always-TRUE predicate (`col NOT IN
    (NULL)) OR (1=1)` in practice — the exact wrong behavior: wiping the
    whole table (and cascading via FK to its children) instead of doing
    nothing. So: an empty `current_pks` here means "skip this table's
    tombstone, keep whatever rows already exist," full stop — never issue
    the DELETE at all when the set is empty, regardless of how SQLAlchemy
    would render the predicate.
    """
    if not current_pks:
        return 0, True
    stmt = model.__table__.delete().where(model.nc_source_pk.notin_(current_pks))
    result = await db.execute(stmt)
    return result.rowcount or 0, False


async def sync_boms(db: AsyncSession) -> dict:
    """Full canonical sync. Returns row counts + skip/warning tallies so a
    caller can see resolution failures without digging through logs:
    {"boms": n, "lines": n, "substitutes": n, "skipped": n, "warnings": n,
    "tombstoned": n, "tombstone_skipped": [table_name, ...]} —
    `tombstoned` is the total rows deleted across all three tables for
    having fallen out of the current NC extract; `tombstone_skipped` names
    any table whose tombstone was REFUSED this run because its resolved set
    came back empty (see `_tombstone()`'s docstring) — existing rows in
    that table were left untouched, and a caller/operator should treat a
    non-empty `tombstone_skipped` as a warning worth investigating (did NC
    really return zero rows for that layer, or was the read partial?)."""
    # fetch_nc_bom() is a blocking oracledb call (sync driver, thin mode) — run
    # it off the event loop so a slow/hung NC read doesn't stall every other
    # request this service (also serving EPMS/OA/Finance lookups) is handling
    # concurrently. asyncio.to_thread is the modern equivalent of the
    # run_in_executor(None, ...) idiom epms-api/app/api/v1/nc_purchase_sync.py
    # uses for the same class of call.
    extract = await asyncio.to_thread(fetch_nc_bom)
    await sync_raw_mirror(db, extract=extract)

    t = transform(extract)

    bom_id_by_pk = await chunked_upsert_returning(db, Bom, t["boms"])

    line_rows = []
    for row in t["lines"]:
        row = dict(row)
        bom_pk = row.pop("bom_nc_source_pk")
        bom_id = bom_id_by_pk.get(bom_pk)
        if bom_id is None:
            continue  # shouldn't happen — transform() already drops orphan lines
        row["bom_id"] = bom_id
        line_rows.append(row)
    line_id_by_pk = await chunked_upsert_returning(db, BomLine, line_rows)

    sub_rows = []
    for row in t["substitutes"]:
        row = dict(row)
        line_pk = row.pop("bom_line_nc_source_pk")
        line_id = line_id_by_pk.get(line_pk)
        if line_id is None:
            continue  # shouldn't happen — transform() already drops orphan substitutes
        row["bom_line_id"] = line_id
        sub_rows.append(row)
    sub_id_by_pk = await chunked_upsert_returning(db, BomSubstitute, sub_rows)

    # Tombstone child-to-parent: a bom_substitute/bom_line orphaned by a
    # cascading DB-level ondelete="CASCADE" (see app/models/bom.py) on a
    # parent deleted below is already gone by the time that DELETE runs, so
    # order doesn't affect correctness — child-first just keeps the rowcount
    # tallies meaningful (no double-counting rows the parent delete already
    # removed).
    n_tombstoned = 0
    tombstone_skipped: list[str] = []

    n, skipped = await _tombstone(db, BomSubstitute, set(sub_id_by_pk))
    n_tombstoned += n
    if skipped:
        tombstone_skipped.append("bom_substitutes")

    n, skipped = await _tombstone(db, BomLine, set(line_id_by_pk))
    n_tombstoned += n
    if skipped:
        tombstone_skipped.append("bom_lines")

    n, skipped = await _tombstone(db, Bom, set(bom_id_by_pk))
    n_tombstoned += n
    if skipped:
        tombstone_skipped.append("boms")

    await db.commit()
    return {
        "boms": len(bom_id_by_pk),
        "lines": len(line_id_by_pk),
        "substitutes": len(sub_id_by_pk),
        "skipped": len(t["skipped"]),
        "warnings": len(t["warnings"]),
        "tombstoned": n_tombstoned,
        "tombstone_skipped": tombstone_skipped,
    }
