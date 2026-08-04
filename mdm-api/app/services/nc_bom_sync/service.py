"""NC65 BOM raw mirror sync — upserts nc_bom / nc_bom_b / nc_bom_repl.

Single-transaction, full-scan (see reader.py for why incremental isn't done
yet). `fetch_nc_bom()` is called and fully materialized in memory BEFORE any
DB write starts, so a reader failure (bad NC creds, network, query error)
raises straight out of `sync_nc_bom` with nothing written — no half-synced
state to clean up. `fetch_nc_bom` is imported as a bare name (not accessed via
the `reader` module) specifically so tests can `monkeypatch.setattr(service,
"fetch_nc_bom", ...)` the way the sibling ERP sync tests do.

Upsert key: the synthetic `nc_source_pk` column each model carries (see
app/models/nc_bom.py docstring) — populated here from the row's real NC PK
column (cbomid / cbom_bid / cbom_replaceid) so one upsert helper covers all
three tables regardless of their differing native PK column names.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.nc_bom import NcBom, NcBomB, NcBomRepl
from app.services.nc_bom_sync.reader import fetch_nc_bom, nc_configured  # noqa: F401 (re-exported)

_RESERVED = {"id", "nc_source_pk", "created_at", "updated_at"}


def _allowed_cols(model) -> set[str]:
    return {c.name for c in model.__table__.columns} - _RESERVED


_HEADER_COLS = _allowed_cols(NcBom)
_LINE_COLS = _allowed_cols(NcBomB)
_REPL_COLS = _allowed_cols(NcBomRepl)


def _row(rec: dict, cols: set[str], pk_field: str) -> dict | None:
    """Raw NC record -> model-shaped upsert dict, or None if its PK is blank.

    Drops any key not present on the model (the VDEF*/VFREE* custom-field
    slots the model intentionally omits, see nc_bom.py's skip list) and stamps
    `nc_source_pk` from the record's real NC PK column.
    """
    pk = rec.get(pk_field)
    if not pk:
        return None
    row = {k: v for k, v in rec.items() if k in cols}
    row["nc_source_pk"] = str(pk)
    return row


_PG_MAX_PARAMS = 32767  # asyncpg's hard per-statement bind-parameter limit


async def _upsert_all(db: AsyncSession, model, rows: list[dict]) -> int:
    """Chunked upsert-by-nc_source_pk. nc_bom_b alone is ~58 cols x 10169 rows
    (~590k parameters) in one INSERT — asyncpg refuses anything over 32767
    bind params per statement, so a single `.values(rows)` call blows up
    against the real NC volumes (only fake-fixture tests, which use 1-row
    batches, would ever pass without this). Chunk to a row count that keeps
    every batch safely under the limit regardless of the model's column count.
    """
    if rows:
        n_cols = len(rows[0])
        chunk_size = max(1, (_PG_MAX_PARAMS // 2) // n_cols)
        for i in range(0, len(rows), chunk_size):
            batch = rows[i:i + chunk_size]
            stmt = pg_insert(model).values(batch)
            update_cols = {
                c.name: getattr(stmt.excluded, c.name)
                for c in model.__table__.columns if c.name not in ("id", "nc_source_pk")
            }
            stmt = stmt.on_conflict_do_update(index_elements=["nc_source_pk"], set_=update_cols)
            await db.execute(stmt)
    return (await db.execute(select(func.count()).select_from(model))).scalar()


async def sync_nc_bom(db: AsyncSession, extract: dict | None = None) -> dict:
    """Full mirror sync. Returns {"headers": n, "lines": n, "repl": n} — total
    row counts in each mirror table after the sync (idempotent: re-running
    with the same extract upserts in place, counts stay the same).

    `extract` lets a caller pass an already-fetched `fetch_nc_bom()` result
    instead of triggering a second live NC round trip — used by Task 5's
    canonical_sync.sync_boms(), which needs the same extract's
    `material_codes` for the transform step right after refreshing this raw
    mirror."""
    if extract is None:
        extract = fetch_nc_bom()

    headers = [row for rec in extract.get("headers", [])
               if (row := _row(rec, _HEADER_COLS, "cbomid")) is not None]
    lines = [row for rec in extract.get("lines", [])
             if (row := _row(rec, _LINE_COLS, "cbom_bid")) is not None]
    repl = [row for rec in extract.get("repl", [])
            if (row := _row(rec, _REPL_COLS, "cbom_replaceid")) is not None]

    n_headers = await _upsert_all(db, NcBom, headers)
    n_lines = await _upsert_all(db, NcBomB, lines)
    n_repl = await _upsert_all(db, NcBomRepl, repl)

    await db.commit()
    return {"headers": n_headers, "lines": n_lines, "repl": n_repl}
