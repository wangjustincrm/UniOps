"""Canonical BOM sync (POST /boms/sync) + effective-version query
(GET /boms/effective), MRP phase0 task 5.

Fixture mirrors the survey's real 3-layer cascade shape (CF finished good ->
CW semi-finished drymix -> CS milling -> CR raw material) with synthetic
NC pks, so these tests exercise the same header/line/substitute resolution
path the real-data smoke test walks, without needing a live NC connection.
"""
from decimal import Decimal

import pytest


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
    assert body == {"boms": 4, "lines": 5, "substitutes": 1, "skipped": 0, "warnings": 0}

    # Re-run must not duplicate rows (idempotent upsert by nc_source_pk).
    resp2 = await client.post("/mdm/v1/boms/sync")
    assert resp2.json() == {"boms": 4, "lines": 5, "substitutes": 1, "skipped": 0, "warnings": 0}


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
