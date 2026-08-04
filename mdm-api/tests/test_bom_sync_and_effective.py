"""Canonical BOM sync (POST /boms/sync) + effective-version query
(GET /boms/effective), MRP phase0 task 5.

Fixture mirrors the survey's real 3-layer cascade shape (CF finished good ->
CW semi-finished drymix -> CS milling -> CR raw material) with synthetic
NC pks, so these tests exercise the same header/line/substitute resolution
path the real-data smoke test walks, without needing a live NC connection.
"""
from decimal import Decimal

import pytest


@pytest.fixture(autouse=True)
def _nc_configured_by_default(monkeypatch):
    """Every test in this file exercises sync logic against a fixture
    extract, not a live NC connection — default nc_configured() to True so
    the endpoint's guard (added alongside the fixture: nc_configured() was
    defined but never enforced at POST /boms/sync) doesn't 503 them. The one
    test that exercises the guard itself (test_bom_sync_503_when_nc_not_
    configured) overrides this back to False."""
    from app.api.v1 import boms as boms_module

    monkeypatch.setattr(boms_module, "nc_configured", lambda: True)


def _extract():
    return {
        "headers": [
            {"cbomid": "H-CF", "hcmaterialid": "M-CF", "hversion": "1.2",
             "fbillstatus": 1, "pk_org": "ORG1", "hvchangerate": "1/1"},
            {"cbomid": "H-CW", "hcmaterialid": "M-CW", "hversion": "1.1",
             "fbillstatus": 1, "pk_org": "ORG1", "hvchangerate": "1/1"},
            # Two coexisting APPROVED versions of the same product (CS0026),
            # like the survey's real CS0026 (7 approved versions 1.0-1.6) —
            # /effective must pick the max version.
            {"cbomid": "H-CS-OLD", "hcmaterialid": "M-CS", "hversion": "1.0",
             "fbillstatus": 1, "pk_org": "ORG1", "hvchangerate": "1/1"},
            {"cbomid": "H-CS-NEW", "hcmaterialid": "M-CS", "hversion": "1.6",
             "fbillstatus": 1, "pk_org": "ORG1", "hvchangerate": "1/1"},
        ],
        "lines": [
            {"cbom_bid": "L-CF-1", "cbomid": "H-CF", "cmaterialid": "M-CW",
             "nitemnum": 420, "vrowno": "10",
             "cbeginperiod": "2019-01-01 00:00:00", "cendperiod": "2999-12-31 23:59:59"},
            {"cbom_bid": "L-CF-2", "cbomid": "H-CF", "cmaterialid": "M-CP",
             "nitemnum": 610, "vrowno": "20",
             "cbeginperiod": "2019-01-01 00:00:00", "cendperiod": "2999-12-31 23:59:59"},
            {"cbom_bid": "L-CW-1", "cbomid": "H-CW", "cmaterialid": "M-CS",
             "nitemnum": Decimal("999.45"), "vrowno": "10",
             "cbeginperiod": "2019-01-01 00:00:00", "cendperiod": "2999-12-31 23:59:59"},
            {"cbom_bid": "L-CSOLD-1", "cbomid": "H-CS-OLD", "cmaterialid": "M-CR",
             "nitemnum": 100, "vrowno": "10",
             "cbeginperiod": "2010-01-01 00:00:00", "cendperiod": "2999-12-31 23:59:59"},
            {"cbom_bid": "L-CSNEW-1", "cbomid": "H-CS-NEW", "cmaterialid": "M-CR",
             "nitemnum": 1911, "vrowno": "10",
             "cbeginperiod": "2019-01-01 00:00:00", "cendperiod": "2999-12-31 23:59:59"},
        ],
        "repl": [
            {"cbom_replaceid": "R-1", "cbom_bid": "L-CW-1", "creplmaterialoid": "M-CS-ALT", "vrowno": "10"},
        ],
        "material_codes": {
            "M-CF": "CF0092", "M-CW": "CW0001", "M-CP": "CP0115", "M-CS": "CS0026",
            "M-CR": "CR0059", "M-CS-ALT": "CS0099",
        },
    }


@pytest.mark.anyio
async def test_bom_sync_upserts_canonical_tables(client, db_session, monkeypatch):
    from app.services.nc_bom_sync import canonical_sync

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", _extract)

    resp = await client.post("/mdm/v1/boms/sync")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"boms": 4, "lines": 5, "substitutes": 1, "skipped": 0, "warnings": 0, "tombstoned": 0}

    # Re-run must not duplicate rows (idempotent upsert by nc_source_pk) and
    # must not tombstone anything (same extract -> same resolved set).
    resp2 = await client.post("/mdm/v1/boms/sync")
    assert resp2.json() == {"boms": 4, "lines": 5, "substitutes": 1, "skipped": 0, "warnings": 0, "tombstoned": 0}


@pytest.mark.anyio
async def test_bom_sync_tombstones_rows_dropped_from_nc_extract(client, db_session, monkeypatch):
    """Upsert alone is append/update-only — a header deleted in NC, or a
    line/substitute removed from a still-live header, must actually
    disappear from boms/bom_lines/bom_substitutes on the next sync, not be
    planned forever. Sync once with header H1 (one line) + header H2 (one
    line + one substitute), then re-sync with H1 entirely gone and H2's
    line/substitute gone too — every dropped row must vanish, H2 itself
    must survive (with zero lines), and the response must report the
    tombstoned count."""
    from sqlalchemy import func, select

    from app.models.bom import Bom, BomLine, BomSubstitute
    from app.services.nc_bom_sync import canonical_sync

    def extract_v1():
        return {
            "headers": [
                {"cbomid": "H1", "hcmaterialid": "MA", "hversion": "1.0", "fbillstatus": 1},
                {"cbomid": "H2", "hcmaterialid": "MB", "hversion": "1.0", "fbillstatus": 1},
            ],
            "lines": [
                {"cbom_bid": "L1", "cbomid": "H1", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"},
                {"cbom_bid": "L2", "cbomid": "H2", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"},
            ],
            "repl": [
                {"cbom_replaceid": "S1", "cbom_bid": "L2", "creplmaterialoid": "MD", "vrowno": "10"},
            ],
            "material_codes": {"MA": "CS0001", "MB": "CS0002", "MC": "CR0001", "MD": "CR0002"},
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract_v1)
    r1 = await client.post("/mdm/v1/boms/sync")
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["boms"] == 2 and body1["lines"] == 2 and body1["substitutes"] == 1
    assert body1["tombstoned"] == 0  # nothing to tombstone from an empty DB

    n_boms = (await db_session.execute(select(func.count()).select_from(Bom))).scalar()
    assert n_boms == 2

    # v2: H1 dropped entirely; H2 survives but its line (and therefore its
    # substitute) is gone from the NC extract.
    def extract_v2():
        return {
            "headers": [
                {"cbomid": "H2", "hcmaterialid": "MB", "hversion": "1.0", "fbillstatus": 1},
            ],
            "lines": [],
            "repl": [],
            "material_codes": {"MA": "CS0001", "MB": "CS0002", "MC": "CR0001", "MD": "CR0002"},
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract_v2)
    r2 = await client.post("/mdm/v1/boms/sync")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["boms"] == 1 and body2["lines"] == 0 and body2["substitutes"] == 0
    assert body2["tombstoned"] > 0

    remaining_boms = (await db_session.execute(select(Bom.nc_source_pk))).scalars().all()
    assert set(remaining_boms) == {"H2"}

    n_lines = (await db_session.execute(select(func.count()).select_from(BomLine))).scalar()
    assert n_lines == 0

    n_subs = (await db_session.execute(select(func.count()).select_from(BomSubstitute))).scalar()
    assert n_subs == 0


@pytest.mark.anyio
async def test_effective_cascade_three_layers(client, db_session, monkeypatch):
    """Walks the survey's real cascade shape through the canonical tables:
    CF0092 (packaging) -> CW0001 (drymix) -> CS0026 (milling), asserting the
    exact component lists at each layer and that substitutes are nested."""
    from app.services.nc_bom_sync import canonical_sync

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", _extract)
    await client.post("/mdm/v1/boms/sync")

    r1 = await client.get("/mdm/v1/boms/effective", params={"product": "CF0092", "date": "2026-08-04"})
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["bom_type"] == "packaging"
    assert b1["version"] == "1.2"
    assert {ln["component_material_code"] for ln in b1["lines"]} == {"CW0001", "CP0115"}

    r2 = await client.get("/mdm/v1/boms/effective", params={"product": "CW0001", "date": "2026-08-04"})
    b2 = r2.json()
    assert b2["bom_type"] == "drymix"
    assert len(b2["lines"]) == 1
    assert b2["lines"][0]["component_material_code"] == "CS0026"
    assert Decimal(str(b2["lines"][0]["qty_per"])) == Decimal("999.45")
    assert [s["substitute_material_code"] for s in b2["lines"][0]["substitutes"]] == ["CS0099"]

    # Multiple approved versions coexist (1.0 and 1.6) -> must pick max version 1.6,
    # whose line carries qty_per=1911 (the NEW header's line, not OLD's qty=100).
    r3 = await client.get("/mdm/v1/boms/effective", params={"product": "CS0026", "date": "2026-08-04"})
    b3 = r3.json()
    assert b3["bom_type"] == "milling"
    assert b3["version"] == "1.6"
    assert len(b3["lines"]) == 1
    assert b3["lines"][0]["component_material_code"] == "CR0059"
    assert Decimal(str(b3["lines"][0]["qty_per"])) == Decimal("1911")


@pytest.mark.anyio
async def test_effective_respects_line_level_date_window(client, db_session, monkeypatch):
    """A version whose lines don't cover the query date must be skipped in
    favor of a lower version whose lines do — proving the effective query
    checks line-level CBEGINPERIOD/CENDPERIOD, not just max HVERSION."""
    from app.services.nc_bom_sync import canonical_sync

    def extract():
        return {
            "headers": [
                {"cbomid": "H-NEW", "hcmaterialid": "M-X", "hversion": "2.0", "fbillstatus": 1},
                {"cbomid": "H-OLD", "hcmaterialid": "M-X", "hversion": "1.0", "fbillstatus": 1},
            ],
            "lines": [
                # The higher version isn't effective until 2027 — must be skipped for a 2026 query.
                {"cbom_bid": "L-NEW", "cbomid": "H-NEW", "cmaterialid": "M-Y", "nitemnum": 5,
                 "vrowno": "10", "cbeginperiod": "2027-01-01 00:00:00", "cendperiod": "2999-12-31 23:59:59"},
                {"cbom_bid": "L-OLD", "cbomid": "H-OLD", "cmaterialid": "M-Y", "nitemnum": 3,
                 "vrowno": "10", "cbeginperiod": "2019-01-01 00:00:00", "cendperiod": "2999-12-31 23:59:59"},
            ],
            "repl": [],
            "material_codes": {"M-X": "CS9999", "M-Y": "CR9999"},
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract)
    await client.post("/mdm/v1/boms/sync")

    resp = await client.get("/mdm/v1/boms/effective", params={"product": "CS9999", "date": "2026-08-04"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "1.0"
    assert Decimal(str(body["lines"][0]["qty_per"])) == Decimal("3")


@pytest.mark.anyio
async def test_effective_404_when_no_match(client, db_session):
    resp = await client.get("/mdm/v1/boms/effective", params={"product": "NOPE", "date": "2026-08-04"})
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_effective_factory_code_filter_and_deterministic_tiebreak(client, db_session, monkeypatch):
    """Two BOMs for the SAME product code under different NC orgs (PK_ORG):
    without `factory_code`, the higher version wins regardless of org;
    with `factory_code`, the query is scoped to that org's own candidates.
    Also: two BOMs tied on the exact same version key must resolve to the
    SAME winner every time (smallest nc_source_pk), not whatever order
    Postgres happens to return rows in."""
    from app.services.nc_bom_sync import canonical_sync

    def extract():
        return {
            "headers": [
                # ORG1's only version is 1.0; ORG2 has a higher 2.0 version.
                {"cbomid": "H-ORG1", "hcmaterialid": "M1", "hversion": "1.0",
                 "fbillstatus": 1, "pk_org": "ORG1"},
                {"cbomid": "H-ORG2", "hcmaterialid": "M1", "hversion": "2.0",
                 "fbillstatus": 1, "pk_org": "ORG2"},
                # Two more headers, same product, SAME version '1.0' -> a tie
                # the version-only sort can't break; nc_source_pk must.
                {"cbomid": "H-TIE-B", "hcmaterialid": "M2", "hversion": "1.0",
                 "fbillstatus": 1, "pk_org": "ORG1"},
                {"cbomid": "H-TIE-A", "hcmaterialid": "M2", "hversion": "1.0",
                 "fbillstatus": 1, "pk_org": "ORG1"},
            ],
            "lines": [
                {"cbom_bid": "L-ORG1", "cbomid": "H-ORG1", "cmaterialid": "M3", "nitemnum": 1, "vrowno": "10"},
                {"cbom_bid": "L-ORG2", "cbomid": "H-ORG2", "cmaterialid": "M3", "nitemnum": 2, "vrowno": "10"},
                {"cbom_bid": "L-TIE-B", "cbomid": "H-TIE-B", "cmaterialid": "M3", "nitemnum": 3, "vrowno": "10"},
                {"cbom_bid": "L-TIE-A", "cbomid": "H-TIE-A", "cmaterialid": "M3", "nitemnum": 4, "vrowno": "10"},
            ],
            "repl": [],
            "material_codes": {"M1": "CS7001", "M2": "CS7002", "M3": "CR7001"},
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract)
    await client.post("/mdm/v1/boms/sync")

    # No factory_code -> highest version overall (ORG2's 2.0) wins.
    r_all = await client.get("/mdm/v1/boms/effective", params={"product": "CS7001", "date": "2026-08-04"})
    assert r_all.status_code == 200
    assert r_all.json()["nc_source_pk"] == "H-ORG2"

    # factory_code=ORG1 -> scoped to ORG1's own (lower) version.
    r_org1 = await client.get(
        "/mdm/v1/boms/effective",
        params={"product": "CS7001", "date": "2026-08-04", "factory_code": "ORG1"},
    )
    assert r_org1.status_code == 200
    assert r_org1.json()["nc_source_pk"] == "H-ORG1"

    # factory_code with no matching org -> 404, not a silent wrong answer.
    r_none = await client.get(
        "/mdm/v1/boms/effective",
        params={"product": "CS7001", "date": "2026-08-04", "factory_code": "ORG-NOPE"},
    )
    assert r_none.status_code == 404

    # Tie on version '1.0' -> smallest nc_source_pk (H-TIE-A < H-TIE-B) wins,
    # deterministically, every call.
    for _ in range(3):
        r_tie = await client.get("/mdm/v1/boms/effective", params={"product": "CS7002", "date": "2026-08-04"})
        assert r_tie.status_code == 200
        assert r_tie.json()["nc_source_pk"] == "H-TIE-A"


@pytest.mark.anyio
async def test_bom_sync_503_when_nc_not_configured(client, db_session, monkeypatch):
    """POST /boms/sync must refuse (503) rather than fall through to
    fetch_nc_bom() and surface an opaque oracledb/DSN error as a 500 —
    nc_configured() existed but was never enforced at the endpoint."""
    from app.api.v1 import boms as boms_module

    monkeypatch.setattr(boms_module, "nc_configured", lambda: False)
    resp = await client.post("/mdm/v1/boms/sync")
    assert resp.status_code == 503
