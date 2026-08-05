"""Multi-level BOM explosion service (MRP phase0 task 5).

Fixtures build canonical `boms`/`bom_lines` rows directly (no NC extract
needed — that plumbing is exercised by test_bom_sync_and_effective.py); this
suite is purely about explode_bom()'s own tree-walking, cycle-guard,
missing-bom-detection, version-selection, and depth-truncation logic.
"""
from decimal import Decimal

import pytest
import pytest_asyncio

from app.models.bom import Bom, BomLine


def _bom(product_material_code, bom_type, version, nc_source_pk, status="approved", yield_rate=None):
    kwargs = {} if yield_rate is None else {"yield_rate": yield_rate}
    return Bom(
        product_material_code=product_material_code,
        bom_type=bom_type,
        version=version,
        status=status,
        nc_source_pk=nc_source_pk,
        **kwargs,
    )


def _line(bom_id, line_no, component_material_code, qty_per, nc_source_pk, uom="KGM", scrap_rate=Decimal("0")):
    return BomLine(
        bom_id=bom_id,
        line_no=line_no,
        component_material_code=component_material_code,
        qty_per=Decimal(qty_per),
        uom=uom,
        scrap_rate=scrap_rate,
        nc_source_pk=nc_source_pk,
    )


@pytest_asyncio.fixture
async def seed_cascade(db_session):
    """S0093 (packaging) -> CW0001 (drymix) -> CS0026 (milling) -> CR0031
    (raw, no BOM of its own — a legitimate leaf, not missing_bom), mirroring
    the survey's real 3-layer cascade shape (survey: CF/S finished good ->
    CW semi-finished drymix -> CS milling -> CR raw)."""
    root = _bom("S0093", "packaging", "1.0", "H-S0093")
    db_session.add(root)
    await db_session.flush()
    db_session.add(_line(root.id, 10, "CW0001", "0.5", "L-S0093-1"))

    cw = _bom("CW0001", "drymix", "1.1", "H-CW0001")
    db_session.add(cw)
    await db_session.flush()
    db_session.add(_line(cw.id, 10, "CS0026", "2.0", "L-CW0001-1"))

    cs = _bom("CS0026", "milling", "1.6", "H-CS0026")
    db_session.add(cs)
    await db_session.flush()
    db_session.add(_line(cs.id, 10, "CR0031", "3.0", "L-CS0026-1"))

    await db_session.commit()


@pytest_asyncio.fixture
async def seed_real_s0093_cascade(db_session):
    """The REAL S0093 -> CW0001 -> CS0026 -> CR0024 cascade, with the exact
    already-normalized `qty_per` values a real PATCH 6 sync writes to
    `bom_lines` (live NC65, 2026-08-04 spot-check + full-tree explosion
    smoke re-run). Phase 1C consumes `explode_bom` directly (not just
    /effective's flat per-line qty_per), so this pins the real numbers at
    THIS level too, not only at transform()/`/effective`:
      - S0093 -> CP0115-1 (tin): qty_per=610/420=1.4523809524
      - S0093 -> CW0001 (dry-mix powder): qty_per=420/420=1.0
      - CW0001 -> CS0026: qty_per=999.45/1000=0.99945
      - CS0026 -> CR0024 (raw material): qty_per=270/1000=0.27
    so CR0024's qty_accumulated = 1.0 * 0.99945 * 0.27 = 0.2698515 — a
    plausible per-kg fraction, not the ~113 million the pre-fix code
    produced for this exact real cascade."""
    root = _bom("S0093", "packaging", "1.0", "H-S0093-REAL")
    db_session.add(root)
    await db_session.flush()
    db_session.add(_line(root.id, 10, "CW0001", "1.0", "L-S0093-REAL-1"))
    db_session.add(_line(root.id, 20, "CP0115-1", "1.4523809524", "L-S0093-REAL-2", uom="EA"))

    cw = _bom("CW0001", "drymix", "1.1", "H-CW0001-REAL")
    db_session.add(cw)
    await db_session.flush()
    db_session.add(_line(cw.id, 10, "CS0026", "0.99945", "L-CW0001-REAL-1"))

    cs = _bom("CS0026", "milling", "1.6", "H-CS0026-REAL")
    db_session.add(cs)
    await db_session.flush()
    db_session.add(_line(cs.id, 10, "CR0024", "0.27", "L-CS0026-REAL-1"))

    await db_session.commit()


@pytest_asyncio.fixture
async def seed_nontrivial_yield_rate(db_session):
    """PATCH 6 follow-up regression: S_YIELD's header carries a real,
    non-1 `yield_rate` (4.2, S0093's own live HVCHANGERATE-derived value) —
    the accumulation formula must NOT divide by it (see bom_explode.py's
    module docstring, "Accumulation formula"), since `qty_per` is already
    normalized against the same HNPARENTNUM/HNASSPARENTNUM pair at sync
    time. A component with qty_per=1.0 must accumulate to exactly 1.0, not
    1.0/4.2."""
    root = _bom("S_YIELD", "packaging", "1.0", "H-S-YIELD", yield_rate=Decimal("4.2"))
    db_session.add(root)
    await db_session.flush()
    db_session.add(_line(root.id, 10, "CW_YIELD", "1.0", "L-S-YIELD-1"))
    await db_session.commit()


@pytest_asyncio.fixture
async def seed_cyclic_bom(db_session):
    """CYC_A -> CYC_B -> CYC_A: NC data can contain loops (brief hard
    requirement #1); explode_bom must detect and stop, not recurse forever."""
    a = _bom("CYC_A", "milling", "1.0", "H-CYC-A")
    db_session.add(a)
    await db_session.flush()
    db_session.add(_line(a.id, 10, "CYC_B", "1.0", "L-CYC-A-1"))

    b = _bom("CYC_B", "milling", "1.0", "H-CYC-B")
    db_session.add(b)
    await db_session.flush()
    db_session.add(_line(b.id, 10, "CYC_A", "1.0", "L-CYC-B-1"))

    await db_session.commit()


@pytest_asyncio.fixture
async def seed_missing_bom(db_session):
    """S_MISS -> CW0099-R: a CW-prefixed (drymix) component that per its
    code prefix SHOULD have its own approved BOM, but none was synced — the
    real-world shape of an NC `-R` rework variant (survey / brief example).
    Must be flagged missing_bom, never silently treated as a leaf."""
    root = _bom("S_MISS", "packaging", "1.0", "H-S-MISS")
    db_session.add(root)
    await db_session.flush()
    db_session.add(_line(root.id, 10, "CW0099-R", "1.0", "L-S-MISS-1"))

    await db_session.commit()


@pytest_asyncio.fixture
async def seed_multi_version(db_session):
    """TOP_MULTI -> CS_MULTI, which has THREE approved versions: '1.0',
    '1.9', '1.10'. Numeric version ordering (not string) must pick '1.10' as
    highest — this is the exact bug window boms.py's `_version_key` docstring
    warns about ('1.10' > '1.9' as strings sorts the wrong way). Each
    version's line points at a DIFFERENT component code so the test can tell
    which version's line actually got selected, not just which version
    number got reported."""
    root = _bom("TOP_MULTI", "packaging", "1.0", "H-TOP-MULTI")
    db_session.add(root)
    await db_session.flush()
    db_session.add(_line(root.id, 10, "CS_MULTI", "1.0", "L-TOP-MULTI-1"))

    old = _bom("CS_MULTI", "milling", "1.0", "H-CS-MULTI-OLD")
    db_session.add(old)
    await db_session.flush()
    db_session.add(_line(old.id, 10, "CR_OLD", "5.0", "L-CS-MULTI-OLD-1"))

    mid = _bom("CS_MULTI", "milling", "1.9", "H-CS-MULTI-MID")
    db_session.add(mid)
    await db_session.flush()
    db_session.add(_line(mid.id, 10, "CR_MID", "6.0", "L-CS-MULTI-MID-1"))

    newest = _bom("CS_MULTI", "milling", "1.10", "H-CS-MULTI-NEWEST")
    db_session.add(newest)
    await db_session.flush()
    db_session.add(_line(newest.id, 10, "CR_TARGET", "7.0", "L-CS-MULTI-NEWEST-1"))

    await db_session.commit()


@pytest.mark.anyio
async def test_explode_pins_real_s0093_normalized_numbers(db_session, seed_real_s0093_cascade):
    """PATCH 6 regression at the explode level (not just transform()/
    /effective — Phase 1C consumes `explode_bom` directly): the real S0093
    numbers must come out of the FULL walk-and-accumulate path correctly,
    not just survive a single division."""
    from datetime import date

    from app.services.bom_explode import explode_bom

    root = await explode_bom(db_session, "S0093", date(2026, 8, 4))

    tin = next(c for c in root.children if c.material_code == "CP0115-1")
    assert tin.qty_accumulated == Decimal("1.4523809524")

    powder = next(c for c in root.children if c.material_code == "CW0001")
    assert powder.qty_accumulated == Decimal("1.0")

    silo = next(c for c in powder.children if c.material_code == "CS0026")
    assert silo.qty_accumulated == Decimal("0.99945")

    raw = next(c for c in silo.children if c.material_code == "CR0024")
    assert raw.qty_accumulated == Decimal("0.2698515")  # NOT ~113 million


@pytest.mark.anyio
async def test_explode_walks_three_levels_and_accumulates(db_session, seed_cascade):
    from datetime import date

    from app.services.bom_explode import explode_bom

    root = await explode_bom(db_session, "S0093", date(2026, 8, 4))
    assert root.material_code == "S0093" and root.level == 0

    powder = next(c for c in root.children if c.material_code == "CW0001")
    assert powder.bom_type == "drymix" and powder.level == 1

    silo = next(c for c in powder.children if c.material_code == "CS0026")
    assert silo.level == 2

    raw = silo.children[0]
    assert raw.material_code == "CR0031"
    assert raw.qty_accumulated == pytest.approx(
        Decimal(powder.qty_per) * Decimal(silo.qty_per) * Decimal(raw.qty_per), rel=1e-9
    )
    # Leaf with no BOM of its own is NOT missing_bom (CR prefix is not a
    # manufactured-type prefix — a legitimate purchased raw material).
    assert raw.missing_bom is False
    assert raw.children == []


@pytest.mark.anyio
async def test_accumulation_does_not_divide_by_yield_rate(db_session, seed_nontrivial_yield_rate):
    from datetime import date

    from app.services.bom_explode import explode_bom

    root = await explode_bom(db_session, "S_YIELD", date(2026, 8, 4))
    child = root.children[0]
    assert child.material_code == "CW_YIELD"
    # qty_per=1.0 must accumulate to exactly 1.0 — NOT 1.0/4.2 — even though
    # the parent BOM's yield_rate is a real, non-1 value.
    assert child.qty_accumulated == Decimal("1.0")


@pytest.mark.anyio
async def test_max_nodes_truncates_independently_of_max_depth(db_session, seed_cascade):
    """`max_nodes` is a second, independent hard stop on top of `max_depth`
    (guards diamond-heavy graphs that fan out combinatorially well before
    hitting a depth limit — the cycle guard alone doesn't bound that). Using
    the existing linear seed_cascade (S0093 -> CW0001 -> CS0026 -> CR0031)
    with max_nodes=2 (root + one child): the walk must stop materializing
    further children right after the budget is spent, flagging the first
    over-budget node `node_limit_reached=True` rather than silently
    expanding it or crashing."""
    from datetime import date

    from app.services.bom_explode import explode_bom

    root = await explode_bom(db_session, "S0093", date(2026, 8, 4), max_nodes=2)
    assert root.node_limit_reached is False

    powder = root.children[0]
    assert powder.material_code == "CW0001"
    assert powder.node_limit_reached is False  # 2nd node — still within budget

    silo = powder.children[0]
    assert silo.material_code == "CS0026"
    # 3rd node — budget (2) already spent by [root, CW0001]: reported with
    # its correct qty_per/qty_accumulated but never expanded.
    assert silo.node_limit_reached is True
    assert silo.children == []
    assert silo.cycle_detected is False
    assert silo.missing_bom is False


@pytest.mark.anyio
async def test_cycle_is_detected_not_infinite(db_session, seed_cyclic_bom):
    from datetime import date

    from app.services.bom_explode import explode_bom

    root = await explode_bom(db_session, "CYC_A", date(2026, 8, 4))
    node = root.children[0].children[0]  # A -> B -> A
    assert node.material_code == "CYC_A"
    assert node.cycle_detected is True and node.children == []


@pytest.mark.anyio
async def test_component_without_bom_is_flagged_missing(db_session, seed_missing_bom):
    from datetime import date

    from app.services.bom_explode import explode_bom

    root = await explode_bom(db_session, "S_MISS", date(2026, 8, 4))
    child = root.children[0]
    assert child.material_code == "CW0099-R"
    assert child.missing_bom is True
    assert child.children == []


@pytest.mark.anyio
async def test_max_depth_truncates_the_tree(db_session, seed_cascade):
    """max_depth=1: root (level 0) expands to CW0001 (level 1), but CW0001
    itself is never explored further — its own BOM lookup is never even
    attempted, so it reports as an un-flagged, un-expanded stub (distinct
    from missing_bom/cycle_detected, which both mean 'we looked')."""
    from datetime import date

    from app.services.bom_explode import explode_bom

    root = await explode_bom(db_session, "S0093", date(2026, 8, 4), max_depth=1)
    assert root.level == 0
    assert len(root.children) == 1

    powder = root.children[0]
    assert powder.material_code == "CW0001" and powder.level == 1
    assert powder.children == []
    assert powder.missing_bom is False
    assert powder.cycle_detected is False
    assert powder.bom_type is None  # never resolved — truncated before lookup
    assert powder.version_candidates_count == 0


@pytest.mark.anyio
async def test_multiple_versions_report_count_and_pick_highest_numeric_version(db_session, seed_multi_version):
    from datetime import date

    from app.services.bom_explode import explode_bom

    root = await explode_bom(db_session, "TOP_MULTI", date(2026, 8, 4))
    node = root.children[0]
    assert node.material_code == "CS_MULTI"
    assert node.version_candidates_count == 3
    # '1.10' > '1.9' numerically; a naive string compare would pick '1.9'.
    assert node.version == "1.10"
    assert [c.material_code for c in node.children] == ["CR_TARGET"]


@pytest.mark.anyio
async def test_explode_endpoint_returns_tree(client, db_session, seed_cascade):
    """Endpoint wiring: GET /mdm/v1/boms/explode serializes the same tree
    explode_bom() builds, gated the same as /effective (any authenticated
    role — the `client` fixture's default system_admin override covers
    that; no separate 403 test needed here, mirroring /effective's own
    endpoint tests which don't re-test the shared read gate either)."""
    resp = await client.get(
        "/mdm/v1/boms/explode",
        params={"product": "S0093", "date": "2026-08-04", "max_depth": 5},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["material_code"] == "S0093" and body["level"] == 0
    powder = next(c for c in body["children"] if c["material_code"] == "CW0001")
    assert powder["bom_type"] == "drymix"
    silo = next(c for c in powder["children"] if c["material_code"] == "CS0026")
    raw = silo["children"][0]
    assert raw["material_code"] == "CR0031"
    assert Decimal(str(raw["qty_accumulated"])) == pytest.approx(
        Decimal("0.5") * Decimal("2.0") * Decimal("3.0"), rel=1e-9
    )
