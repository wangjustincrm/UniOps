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
    assert body == {
        "boms": 4, "lines": 5, "substitutes": 1, "skipped": 0, "warnings": 0,
        "tombstoned": 0, "tombstone_skipped": [],
    }

    # Re-run must not duplicate rows (idempotent upsert by nc_source_pk),
    # must not tombstone anything (same extract -> same resolved set), and
    # must not skip any table's tombstone (every layer resolves non-empty
    # both times in this fixture — see the dedicated empty-layer tests below
    # for the skip-on-empty guard itself).
    resp2 = await client.post("/mdm/v1/boms/sync")
    assert resp2.json() == {
        "boms": 4, "lines": 5, "substitutes": 1, "skipped": 0, "warnings": 0,
        "tombstoned": 0, "tombstone_skipped": [],
    }


@pytest.mark.anyio
async def test_bom_sync_tombstones_rows_dropped_from_nc_extract(client, db_session, monkeypatch):
    """Upsert alone is append/update-only — a header deleted in NC, a line
    removed from a still-live header, and a substitute removed from a
    still-live line must all actually disappear from boms/bom_lines/
    bom_substitutes on the next sync, not be planned forever. Sync once with
    three headers (H1/H2/H3, one line each, two of the lines carrying a
    substitute), then re-sync with H1 entirely gone, L1's cascade gone with
    it, and S2 (one of the two substitutes) dropped — while H2/L2/S1 and
    H3/L3 all still resolve normally (every layer's resolved set stays
    NON-empty both syncs, so this exercises real partial tombstoning, not
    the empty-layer skip guard — see the dedicated tests below for that)."""
    from sqlalchemy import func, select

    from app.models.bom import Bom, BomLine, BomSubstitute
    from app.services.nc_bom_sync import canonical_sync

    def extract_v1():
        return {
            "headers": [
                {"cbomid": "H1", "hcmaterialid": "MA", "hversion": "1.0", "fbillstatus": 1},
                {"cbomid": "H2", "hcmaterialid": "MB", "hversion": "1.0", "fbillstatus": 1},
                {"cbomid": "H3", "hcmaterialid": "ME", "hversion": "1.0", "fbillstatus": 1},
            ],
            "lines": [
                {"cbom_bid": "L1", "cbomid": "H1", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"},
                {"cbom_bid": "L2", "cbomid": "H2", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"},
                {"cbom_bid": "L3", "cbomid": "H3", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"},
            ],
            "repl": [
                {"cbom_replaceid": "S1", "cbom_bid": "L2", "creplmaterialoid": "MD", "vrowno": "10"},
                {"cbom_replaceid": "S2", "cbom_bid": "L3", "creplmaterialoid": "MD", "vrowno": "10"},
            ],
            "material_codes": {
                "MA": "CS0001", "MB": "CS0002", "ME": "CS0003", "MC": "CR0001", "MD": "CR0002",
            },
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract_v1)
    r1 = await client.post("/mdm/v1/boms/sync")
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["boms"] == 3 and body1["lines"] == 3 and body1["substitutes"] == 2
    assert body1["tombstoned"] == 0  # nothing to tombstone from an empty DB
    assert body1["tombstone_skipped"] == []  # every layer resolved non-empty

    n_boms = (await db_session.execute(select(func.count()).select_from(Bom))).scalar()
    assert n_boms == 3

    # v2: H1 (and its line L1) dropped entirely; H2/L2/S1 and H3/L3 survive
    # unchanged, EXCEPT S2 (L3's substitute) is gone. Every layer's resolved
    # set is still non-empty this round (boms={H2,H3}, lines={L2,L3},
    # substitutes={S1}), so real tombstoning must still fire everywhere.
    def extract_v2():
        return {
            "headers": [
                {"cbomid": "H2", "hcmaterialid": "MB", "hversion": "1.0", "fbillstatus": 1},
                {"cbomid": "H3", "hcmaterialid": "ME", "hversion": "1.0", "fbillstatus": 1},
            ],
            "lines": [
                {"cbom_bid": "L2", "cbomid": "H2", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"},
                {"cbom_bid": "L3", "cbomid": "H3", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"},
            ],
            "repl": [
                {"cbom_replaceid": "S1", "cbom_bid": "L2", "creplmaterialoid": "MD", "vrowno": "10"},
            ],
            "material_codes": {
                "MA": "CS0001", "MB": "CS0002", "ME": "CS0003", "MC": "CR0001", "MD": "CR0002",
            },
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract_v2)
    r2 = await client.post("/mdm/v1/boms/sync")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["boms"] == 2 and body2["lines"] == 2 and body2["substitutes"] == 1
    assert body2["tombstoned"] > 0
    assert body2["tombstone_skipped"] == []  # real tombstoning, nothing refused

    remaining_boms = (await db_session.execute(select(Bom.nc_source_pk))).scalars().all()
    assert set(remaining_boms) == {"H2", "H3"}

    remaining_lines = (await db_session.execute(select(BomLine.nc_source_pk))).scalars().all()
    assert set(remaining_lines) == {"L2", "L3"}

    remaining_subs = (await db_session.execute(select(BomSubstitute.nc_source_pk))).scalars().all()
    assert set(remaining_subs) == {"S1"}


@pytest.mark.anyio
async def test_bom_sync_skips_tombstone_when_one_layer_extract_is_empty(client, db_session, monkeypatch):
    """CRITICAL regression: a transient/partial NC read that returns zero
    rows for exactly ONE layer (here: `repl`, i.e. no substitutes at all)
    WITHOUT raising must NOT wipe that whole canonical table.
    SQLAlchemy's `notin_(<empty set>)` compiles to an always-TRUE predicate,
    so `_tombstone()` must refuse to even issue the DELETE when the
    resolved set is empty, rather than trusting notin_'s semantics — this
    test is exactly what would have caught the bug (an unguarded version
    deletes every bom_substitutes row here, cascaded or not, even though
    headers/lines still fully resolve and report zero real changes)."""
    from sqlalchemy import func, select

    from app.models.bom import BomSubstitute
    from app.services.nc_bom_sync import canonical_sync

    def extract_with_substitutes():
        return {
            "headers": [{"cbomid": "H1", "hcmaterialid": "MA", "hversion": "1.0", "fbillstatus": 1}],
            "lines": [{"cbom_bid": "L1", "cbomid": "H1", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"}],
            "repl": [{"cbom_replaceid": "S1", "cbom_bid": "L1", "creplmaterialoid": "MD", "vrowno": "10"}],
            "material_codes": {"MA": "CS0001", "MC": "CR0001", "MD": "CR0002"},
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract_with_substitutes)
    r1 = await client.post("/mdm/v1/boms/sync")
    assert r1.status_code == 200
    assert r1.json()["substitutes"] == 1

    def extract_empty_repl_only():
        # headers/lines identical and fully resolving; repl came back empty
        # this round (e.g. a partial NC read) — NOT a real "all substitutes
        # deleted" signal.
        return {
            "headers": [{"cbomid": "H1", "hcmaterialid": "MA", "hversion": "1.0", "fbillstatus": 1}],
            "lines": [{"cbom_bid": "L1", "cbomid": "H1", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"}],
            "repl": [],
            "material_codes": {"MA": "CS0001", "MC": "CR0001", "MD": "CR0002"},
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract_empty_repl_only)
    r2 = await client.post("/mdm/v1/boms/sync")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["substitutes"] == 0  # nothing resolved THIS sync...
    assert body2["tombstone_skipped"] == ["bom_substitutes"]  # ...but refused to wipe

    # The pre-existing substitute row must still be there.
    remaining = (await db_session.execute(select(BomSubstitute.nc_source_pk))).scalars().all()
    assert remaining == ["S1"]
    n = (await db_session.execute(select(func.count()).select_from(BomSubstitute))).scalar()
    assert n == 1


@pytest.mark.anyio
async def test_bom_sync_skips_all_tombstones_when_headers_extract_is_empty(client, db_session, monkeypatch):
    """The most severe form of the same bug: an entirely empty `headers`
    layer (NC read timeout/partial failure that doesn't raise) cascades to
    empty `lines`/`substitutes` resolved sets too (transform() can't resolve
    lines/substitutes without a resolved parent header) — an unguarded
    tombstone would wipe boms, bom_lines, AND bom_substitutes in one sync.
    Every table must be left untouched and every table name must be
    reported in `tombstone_skipped`."""
    from sqlalchemy import func, select

    from app.models.bom import Bom, BomLine, BomSubstitute
    from app.services.nc_bom_sync import canonical_sync

    def extract_v1():
        return {
            "headers": [{"cbomid": "H1", "hcmaterialid": "MA", "hversion": "1.0", "fbillstatus": 1}],
            "lines": [{"cbom_bid": "L1", "cbomid": "H1", "cmaterialid": "MC", "nitemnum": 1, "vrowno": "10"}],
            "repl": [{"cbom_replaceid": "S1", "cbom_bid": "L1", "creplmaterialoid": "MD", "vrowno": "10"}],
            "material_codes": {"MA": "CS0001", "MC": "CR0001", "MD": "CR0002"},
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract_v1)
    r1 = await client.post("/mdm/v1/boms/sync")
    assert r1.status_code == 200
    assert r1.json() == {
        "boms": 1, "lines": 1, "substitutes": 1, "skipped": 0, "warnings": 0,
        "tombstoned": 0, "tombstone_skipped": [],
    }

    def extract_empty():
        return {"headers": [], "lines": [], "repl": [], "material_codes": {}}

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract_empty)
    r2 = await client.post("/mdm/v1/boms/sync")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["boms"] == 0 and body2["lines"] == 0 and body2["substitutes"] == 0
    assert body2["tombstoned"] == 0
    assert set(body2["tombstone_skipped"]) == {"boms", "bom_lines", "bom_substitutes"}

    n_boms = (await db_session.execute(select(func.count()).select_from(Bom))).scalar()
    n_lines = (await db_session.execute(select(func.count()).select_from(BomLine))).scalar()
    n_subs = (await db_session.execute(select(func.count()).select_from(BomSubstitute))).scalar()
    assert (n_boms, n_lines, n_subs) == (1, 1, 1), "an empty headers extract must not delete anything"


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
async def test_effective_exposes_secondary_uom_and_cm_exclusions(client, db_session, monkeypatch):
    """End-to-end coverage for PATCH 2/3/4/5: a two-unit S-prefixed finished
    good line resolves both units (with EA/PIECES normalized), a CM-prefixed
    parent header never syncs, and a CM-prefixed component line is dropped
    but visibly counted in `warnings`."""
    from app.services.nc_bom_sync import canonical_sync

    def extract():
        return {
            "headers": [
                {"cbomid": "H-S", "hcmaterialid": "M-S", "hversion": "1.0",
                 "fbillstatus": 1, "pk_org": "ORG1", "hvchangerate": "1/1"},
                {"cbomid": "H-CM", "hcmaterialid": "M-CM", "hversion": "1.0",
                 "fbillstatus": 1, "pk_org": "ORG1", "hvchangerate": "1/1"},
            ],
            "lines": [
                {"cbom_bid": "L-S-1", "cbomid": "H-S", "cmaterialid": "M-CP",
                 "nitemnum": 610, "vrowno": "10", "cmeasureid": "PK-KGM",
                 "nassitemnum": 610, "cassmeasureid": "PK-PIECES",
                 "cbeginperiod": "2019-01-01 00:00:00", "cendperiod": "2999-12-31 23:59:59"},
                {"cbom_bid": "L-S-2", "cbomid": "H-S", "cmaterialid": "M-CM",
                 "nitemnum": 5, "vrowno": "20", "cmeasureid": None, "nassitemnum": None, "cassmeasureid": None,
                 "cbeginperiod": "2019-01-01 00:00:00", "cendperiod": "2999-12-31 23:59:59"},
                # Line under the excluded CM header — must never surface.
                {"cbom_bid": "L-CM-1", "cbomid": "H-CM", "cmaterialid": "M-CR",
                 "nitemnum": 1, "vrowno": "10", "cmeasureid": None, "nassitemnum": None, "cassmeasureid": None,
                 "cbeginperiod": None, "cendperiod": None},
            ],
            "repl": [],
            "material_codes": {
                "M-S": "S0093", "M-CP": "CP0115", "M-CM": "CM0001", "M-CR": "CR0001",
            },
            "uoms": {"PK-KGM": "KGM", "PK-PIECES": "PIECES"},
        }

    monkeypatch.setattr(canonical_sync, "fetch_nc_bom", extract)
    sync_resp = await client.post("/mdm/v1/boms/sync")
    assert sync_resp.status_code == 200
    body = sync_resp.json()
    assert body["boms"] == 1  # H-CM excluded entirely
    assert body["lines"] == 1  # L-S-2 (CM component) dropped; L-CM-1 orphaned by header exclusion
    assert body["skipped"] >= 1  # H-CM (+ its cascaded line L-CM-1)
    assert body["warnings"] >= 1  # L-S-2's cm_component_excluded warning

    resp = await client.get("/mdm/v1/boms/effective", params={"product": "S0093", "date": "2026-08-04"})
    assert resp.status_code == 200
    eff = resp.json()
    assert eff["bom_type"] == "packaging"
    assert {ln["component_material_code"] for ln in eff["lines"]} == {"CP0115"}
    line = eff["lines"][0]
    assert line["uom"] == "KGM"
    assert Decimal(str(line["qty_per_secondary"])) == Decimal("610")
    assert line["uom_secondary"] == "EA"  # PIECES normalized to canonical EA


@pytest.mark.anyio
async def test_bom_sync_503_when_nc_not_configured(client, db_session, monkeypatch):
    """POST /boms/sync must refuse (503) rather than fall through to
    fetch_nc_bom() and surface an opaque oracledb/DSN error as a 500 —
    nc_configured() existed but was never enforced at the endpoint."""
    from app.api.v1 import boms as boms_module

    monkeypatch.setattr(boms_module, "nc_configured", lambda: False)
    resp = await client.post("/mdm/v1/boms/sync")
    assert resp.status_code == 503
