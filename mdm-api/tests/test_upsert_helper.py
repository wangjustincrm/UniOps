"""app/services/upsert.py — the chunked-upsert helper shared by
nc_bom_sync/service.py (raw mirror) and nc_bom_sync/canonical_sync.py
(canonical tables). Two behaviors under test:

1. Re-syncing an existing row must NOT reset `created_at` (the bug: the old,
   now-removed per-call-site copy in nc_bom_sync/service.py's `_upsert_all`
   excluded only id/nc_source_pk from the ON CONFLICT ... SET clause, so
   `created_at` — never present in the upsert dict itself, relying on the
   column's server_default — got overwritten with a fresh `now()` on every
   re-sync; canonical_sync.py's copy already excluded it correctly).
2. The chunking math actually splits into >1 statement when the configured
   per-statement bind-parameter budget is smaller than the row count would
   need — asserted here by monkeypatching `_PG_MAX_PARAMS` down to a size
   that forces multiple chunks from a handful of fixture rows (the real
   NC volumes, ~10k rows, are impractical to construct in a unit test).
"""
import pytest
from sqlalchemy import func, select

from app.models.nc_bom import NcBom
from app.services import upsert as upsert_module
from app.services.upsert import chunked_upsert_count, chunked_upsert_returning


def _rows(n: int, version: str) -> list[dict]:
    # 2 keys/row (nc_source_pk + cbomid) so a tiny _PG_MAX_PARAMS forces
    # multiple chunks without needing hundreds of fixture rows.
    return [
        {"nc_source_pk": f"PK{i}", "cbomid": f"C{i}", "hversion": version}
        for i in range(n)
    ]


@pytest.mark.anyio
async def test_chunked_upsert_count_splits_into_multiple_chunks_and_preserves_created_at(
    db_session, monkeypatch,
):
    # 5 rows x 3 cols; _PG_MAX_PARAMS=10 -> chunk_size = max(1, 10//2//3) = 1
    # -> 5 chunks for 5 rows, proving the loop actually iterates in batches
    # rather than a single `.values(rows)` call.
    monkeypatch.setattr(upsert_module, "_PG_MAX_PARAMS", 10)

    rows_v1 = _rows(5, version="1.0")
    n1 = await chunked_upsert_count(db_session, NcBom, rows_v1)
    assert n1 == 5

    all_rows = (await db_session.execute(select(NcBom).order_by(NcBom.nc_source_pk))).scalars().all()
    assert [r.nc_source_pk for r in all_rows] == [f"PK{i}" for i in range(5)]
    assert all(r.hversion == "1.0" for r in all_rows)
    created_at_before = {r.nc_source_pk: r.created_at for r in all_rows}

    # Re-sync with changed data — same rows, different hversion. Must
    # update in place (idempotent count) and must NOT reset created_at.
    rows_v2 = _rows(5, version="2.0")
    n2 = await chunked_upsert_count(db_session, NcBom, rows_v2)
    assert n2 == 5

    db_session.expire_all()
    all_rows_2 = (await db_session.execute(select(NcBom).order_by(NcBom.nc_source_pk))).scalars().all()
    assert all(r.hversion == "2.0" for r in all_rows_2), "the upsert must apply the new values"
    for r in all_rows_2:
        assert r.created_at == created_at_before[r.nc_source_pk], (
            f"created_at for {r.nc_source_pk} was reset by a re-sync — "
            "the ON CONFLICT SET clause must exclude created_at"
        )

    total = (await db_session.execute(select(func.count()).select_from(NcBom))).scalar()
    assert total == 5, "re-sync must update in place, not duplicate rows"


@pytest.mark.anyio
async def test_chunked_upsert_returning_splits_into_multiple_chunks_and_maps_ids(
    db_session, monkeypatch,
):
    monkeypatch.setattr(upsert_module, "_PG_MAX_PARAMS", 10)

    rows = _rows(4, version="1.0")
    id_by_pk = await chunked_upsert_returning(db_session, NcBom, rows)
    assert set(id_by_pk) == {f"PK{i}" for i in range(4)}

    db_rows = (await db_session.execute(select(NcBom))).scalars().all()
    assert {r.nc_source_pk: r.id for r in db_rows} == id_by_pk

    created_at_before = {r.nc_source_pk: r.created_at for r in db_rows}

    # Re-run: same ids must come back (upsert, not re-insert) and
    # created_at must not move.
    id_by_pk_2 = await chunked_upsert_returning(db_session, NcBom, _rows(4, version="9.9"))
    assert id_by_pk_2 == id_by_pk

    db_session.expire_all()
    db_rows_2 = (await db_session.execute(select(NcBom))).scalars().all()
    for r in db_rows_2:
        assert r.hversion == "9.9"
        assert r.created_at == created_at_before[r.nc_source_pk]
