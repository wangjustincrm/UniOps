"""NC65 BOM raw mirror sync tests.

Field names below are the real NC65 columns confirmed by the 2026-08-03
survey (docs/superpowers/specs/2026-08-03-nc-bom-survey.md) — NOT the
task-4-brief's guessed pk_bom/pk_bom_b/hstate names, which predate the
survey. Real header PK = CBOMID, line PK = CBOM_BID, substitute PK =
CBOM_REPLACEID; approval status = FBILLSTATUS (1=approved, -1=draft);
watermark = TS (see reader.py docstring for why, not MODIFIEDTIME).
"""
import pytest
from sqlalchemy import func, select

FAKE_HEADERS = [{
    "cbomid": "PKHDR1", "hcmaterialid": "MATCF1", "hcmaterialvid": "~",
    "hversion": "1.0", "fbillstatus": 1, "fbomtype": 1, "approver": "USER1",
    "pk_org": "ORG1", "hvchangerate": "1/1", "ts": "2026-08-01 00:00:00",
    "vbillcode": "Test Co CF0001 生产BOM 1.0",
}]
FAKE_LINES = [{
    "cbom_bid": "PKLN1", "cbomid": "PKHDR1", "cmaterialid": "MATCS1",
    "ibasenum": 1, "nitemnum": "1.05", "nassitemnum": "1.05",
    "vrowno": "10", "bcanreplace": "N", "bmainmaterial": "N",
    "cbeginperiod": "2019-09-01 00:00:00", "cendperiod": "2999-12-31 23:59:59",
    "ts": "2026-08-01 00:00:00",
}]
FAKE_REPL = [{
    "cbom_replaceid": "PKR1", "cbom_bid": "PKLN1", "creplmaterialoid": "MATCS2",
    "vrowno": "10", "vreplaceindex": "1.00/1.00", "ts": "2026-08-01 00:00:00",
}]


def _fake_extract():
    return {"headers": FAKE_HEADERS, "lines": FAKE_LINES, "repl": FAKE_REPL,
            "material_codes": {"MATCF1": "CF0001", "MATCS1": "CS0001"}}


@pytest.mark.anyio
async def test_nc_bom_sync_idempotent(db_session, monkeypatch):
    from app.services.nc_bom_sync import service
    monkeypatch.setattr(service, "fetch_nc_bom", _fake_extract)

    r1 = await service.sync_nc_bom(db_session)
    assert r1 == {"headers": 1, "lines": 1, "repl": 1}

    r2 = await service.sync_nc_bom(db_session)  # re-run must not duplicate rows
    assert r2 == {"headers": 1, "lines": 1, "repl": 1}

    from app.models.nc_bom import NcBom, NcBomB, NcBomRepl
    n_headers = (await db_session.execute(select(func.count()).select_from(NcBom))).scalar()
    n_lines = (await db_session.execute(select(func.count()).select_from(NcBomB))).scalar()
    n_repl = (await db_session.execute(select(func.count()).select_from(NcBomRepl))).scalar()
    assert n_headers == 1
    assert n_lines == 1
    assert n_repl == 1


@pytest.mark.anyio
async def test_nc_bom_sync_maps_columns_and_upserts_changes(db_session, monkeypatch):
    """Fields land on the right columns, and a changed value on re-sync
    overwrites the existing row (upsert, not insert-and-ignore)."""
    from app.services.nc_bom_sync import service
    from app.models.nc_bom import NcBom, NcBomB, NcBomRepl

    monkeypatch.setattr(service, "fetch_nc_bom", _fake_extract)
    await service.sync_nc_bom(db_session)

    header = (await db_session.execute(select(NcBom))).scalar_one()
    assert header.nc_source_pk == "PKHDR1"
    assert header.cbomid == "PKHDR1"
    assert header.hcmaterialid == "MATCF1"
    assert header.hversion == "1.0"
    assert int(header.fbillstatus) == 1

    line = (await db_session.execute(select(NcBomB))).scalar_one()
    assert line.nc_source_pk == "PKLN1"
    assert line.cbomid == "PKHDR1"
    assert line.cmaterialid == "MATCS1"
    assert line.vrowno == "10"

    repl = (await db_session.execute(select(NcBomRepl))).scalar_one()
    assert repl.nc_source_pk == "PKR1"
    assert repl.cbom_bid == "PKLN1"
    assert repl.creplmaterialoid == "MATCS2"

    # Re-sync with an updated version string -> same row, new value (upsert).
    updated_headers = [dict(FAKE_HEADERS[0], hversion="1.1")]
    monkeypatch.setattr(
        service, "fetch_nc_bom",
        lambda: {"headers": updated_headers, "lines": FAKE_LINES, "repl": FAKE_REPL},
    )
    r2 = await service.sync_nc_bom(db_session)
    assert r2 == {"headers": 1, "lines": 1, "repl": 1}

    # db_session fixture uses expire_on_commit=False, so the identity map still
    # holds `header`'s pre-update attributes — force a fresh read from Postgres.
    db_session.expire_all()
    header2 = (await db_session.execute(select(NcBom))).scalar_one()
    assert header2.id == header.id
    assert header2.hversion == "1.1"


@pytest.mark.anyio
async def test_nc_bom_sync_skips_rows_with_blank_pk(db_session, monkeypatch):
    """A row missing its NC PK column can't be upserted (no nc_source_pk to
    key on) — sync must skip it, not crash or insert a broken row."""
    from app.services.nc_bom_sync import service
    from app.models.nc_bom import NcBom

    bad_headers = [dict(FAKE_HEADERS[0]), {"cbomid": None, "hversion": "9.9"}]
    monkeypatch.setattr(
        service, "fetch_nc_bom",
        lambda: {"headers": bad_headers, "lines": [], "repl": []},
    )
    result = await service.sync_nc_bom(db_session)
    assert result["headers"] == 1

    n = (await db_session.execute(select(func.count()).select_from(NcBom))).scalar()
    assert n == 1


def test_nc_configured_false_when_env_missing(monkeypatch):
    from app.core.config import settings
    from app.services.nc_bom_sync import reader

    monkeypatch.setattr(settings, "nc_host", None)
    monkeypatch.setattr(settings, "nc_service", None)
    monkeypatch.setattr(settings, "nc_user", None)
    monkeypatch.setattr(settings, "nc_password", None)
    assert reader.nc_configured() is False


def test_nc_configured_true_when_env_present(monkeypatch):
    from app.core.config import settings
    from app.services.nc_bom_sync import reader

    monkeypatch.setattr(settings, "nc_host", "10.10.95.67")
    monkeypatch.setattr(settings, "nc_service", "ORCL")
    monkeypatch.setattr(settings, "nc_user", "ncsc")
    monkeypatch.setattr(settings, "nc_password", "secret")
    assert reader.nc_configured() is True
