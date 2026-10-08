"""How many can we make — the arithmetic, and the four ways it goes wrong.

Built after the assistant was asked "现存的原料能生产多少S0102" and answered by
listing seven packaging components' stock and totalling them to 521,257 — a sum
of lids, labels and cartons, which is not a quantity of anything. It could not
do better: controlled_query runs one query, and this needs the recipe AND the
stock AND arithmetic over both.

These fixtures are deliberately small and hand-computable. The point is not that
the code runs; it is that each number can be checked by hand against the
docstring's definition.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.services import producible_view as pv


# ── Unit handling, which needs no database ───────────────────────────────────

def test_the_two_sides_name_the_same_units_differently():
    """The recipe says EA and KGM; the warehouse says PIECES and KG."""
    assert pv._canon_unit("EA") == pv._canon_unit("PIECES") == "EA"
    assert pv._canon_unit("KGM") == pv._canon_unit("KG") == "KG"
    assert pv._canon_unit("LTR") == pv._canon_unit("L") == "L"


def test_genuinely_different_units_do_not_collapse():
    """The denial that matters, with the admission beside it: if KG and L
    canonicalised to the same thing, the check that refuses to divide one by
    the other would pass for the wrong reason."""
    assert pv._canon_unit("KG") != pv._canon_unit("L")
    assert pv._canon_unit("EA") != pv._canon_unit("KG")


def test_an_unknown_unit_is_not_guessed():
    assert pv._canon_unit("FURLONG") is None
    assert pv._canon_unit(None) is None
    assert pv._canon_unit("") is None


# ── The real thing, against fixtures ─────────────────────────────────────────

@pytest.fixture
async def db_session(test_engine):
    """A session on the test database, with the three external tables emptied.

    boms / bom_lines / wms_inventory_lots belong to mdm-api and mrp-api; conftest
    creates them from the ontology's external metadata. They are shared state
    across tests in a way the ORM-backed tables are not, so each test starts
    from empty rather than inheriting whatever the last one seeded.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(test_engine, class_=AsyncSession,
                                 expire_on_commit=False)
    async with factory() as db:
        async def wipe():
            """Only this file's rows.

            Every BOM seeded here carries an nc_source_pk beginning "PK-" and
            every lot sits in warehouse "W1", so the deletes can be scoped to
            them. Saying DELETE FROM boms instead would take whatever the rest
            of the suite had seeded — and these three tables are shared with
            the ontology drift test, which measures real coverage on them.
            """
            await db.execute(sa.text(
                "DELETE FROM bom_lines WHERE bom_id IN"
                " (SELECT id FROM boms WHERE nc_source_pk LIKE 'PK-%')"))
            await db.execute(sa.text("DELETE FROM boms WHERE nc_source_pk LIKE 'PK-%'"))
            await db.execute(sa.text("DELETE FROM nc_bom WHERE nc_source_pk LIKE 'PK-%'"))
            await db.execute(sa.text("DELETE FROM wms_inventory_lots WHERE warehouse_id = 'W1'"))
            await db.commit()

        # Cleared before AND after. Clearing only on the way in leaves this
        # file's fixtures sitting in tables other tests read — harmless in file
        # order, and a failure that appears only under a different one.
        await wipe()
        try:
            yield db
        finally:
            await wipe()


async def _seed(db, *, boms, lines, lots):
    """Write the three external tables directly.

    They belong to mdm-api and mrp-api; conftest creates them from the
    ontology's external metadata, so there are no ORM models here to use.
    """
    for b in boms:
        await db.execute(sa.text(
            "INSERT INTO boms (id, nc_source_pk, product_material_code, bom_type,"
            " version, status, batch_output_qty, created_at)"
            " VALUES (CAST(:id AS uuid), :pk, :code, :t, :v, 'approved', :b, now())"), b)
        # conftest creates nc_bom with only the two columns the default-version
        # expression reads — it is not an ORM table.
        await db.execute(sa.text(
            "INSERT INTO nc_bom (nc_source_pk, hbdefault) VALUES (:pk, :d)"),
            {"pk": b["pk"], "d": b["default"]})
    for ln in lines:
        await db.execute(sa.text(
            "INSERT INTO bom_lines (id, bom_id, line_no, component_material_code,"
            " qty_per, uom, created_at)"
            " VALUES (CAST(:id AS uuid), CAST(:bom AS uuid), :n, :code, :q, :u, now())"),
            {**ln, "id": str(uuid.uuid4())})
    for lot in lots:
        await db.execute(sa.text(
            "INSERT INTO wms_inventory_lots (id, warehouse_id, material_code, lot_no,"
            " qty, qty_onhold, uom, mapped_status, expiry_date, created_at)"
            " VALUES (CAST(:id AS uuid), 'W1', :code, :lot, :qty, :hold, :u, :st,"
            " CAST(:exp AS date), now())"),
            {**lot, "id": str(uuid.uuid4())})
    await db.commit()


PRODUCT, MID, RAW_A, RAW_B = "P1", "M1", "RAW-A", "RAW-B"
B_TOP, B_MID = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
async def two_level(db_session):
    """P1 needs 2 M1; M1 needs 3 RAW-A and 1 RAW-B. So one P1 needs 6 RAW-A."""
    await _seed(
        db_session,
        boms=[
            {"id": B_TOP, "pk": "PK-TOP", "code": PRODUCT, "t": "packaging",
             "v": "1.0", "b": Decimal("100"), "default": "Y"},
            {"id": B_MID, "pk": "PK-MID", "code": MID, "t": "milling",
             "v": "1.0", "b": Decimal("1000"), "default": "Y"},
        ],
        lines=[
            {"bom": B_TOP, "n": 10, "code": MID, "q": Decimal("2"), "u": "KGM"},
            {"bom": B_MID, "n": 10, "code": RAW_A, "q": Decimal("3"), "u": "KGM"},
            {"bom": B_MID, "n": 20, "code": RAW_B, "q": Decimal("1"), "u": "KGM"},
        ],
        lots=[
            {"code": RAW_A, "lot": "A1", "qty": Decimal("600"), "hold": Decimal("0"),
             "u": "KG", "st": "available", "exp": None},
            {"code": RAW_B, "lot": "B1", "qty": Decimal("500"), "hold": Decimal("0"),
             "u": "KG", "st": "available", "exp": None},
        ],
    )
    return db_session


@pytest.mark.asyncio
async def test_the_recipe_is_exploded_through_sub_recipes(two_level):
    """M1 is not a leaf. Stopping at it would report stock we do not consume
    and miss the materials that actually constrain production — the mistake the
    single-query answer made."""
    leaves, intermediates, _ = await pv.explode(two_level, PRODUCT)
    by_code = {c.material_code: c for c in leaves}

    assert set(by_code) == {RAW_A, RAW_B}
    assert MID in [i["material_code"] for i in intermediates]
    # 2 M1 per P1 x 3 RAW-A per M1
    assert by_code[RAW_A].qty_per_unit == Decimal("6")
    assert by_code[RAW_B].qty_per_unit == Decimal("2")


@pytest.mark.asyncio
async def test_the_smallest_ratio_wins_and_is_named(two_level):
    """600 RAW-A / 6 = 100. 500 RAW-B / 2 = 250. The answer is 100."""
    r = await pv.how_many_can_we_make(two_level, PRODUCT)

    assert r["can_make"] == "100"
    assert r["limited_by"] == [RAW_A]
    constraint = [m for m in r["materials"] if m["is_the_constraint"]]
    assert [m["material_code"] for m in constraint] == [RAW_A]


@pytest.mark.asyncio
async def test_excluding_a_material_drops_what_is_only_under_it(two_level):
    """'不考虑CR0059' has to take CR0059's subtree with it — the materials
    below it are reachable no other way, and leaving them in would keep
    constraining an answer the asker said to compute without them."""
    r = await pv.how_many_can_we_make(two_level, PRODUCT, exclude={MID})

    assert r["excluded"] == [MID]
    assert r["materials"] == []
    assert r["can_make"] is None


@pytest.mark.asyncio
async def test_on_hold_stock_does_not_count(db_session):
    await _seed(
        db_session,
        boms=[{"id": B_TOP, "pk": "PK-H", "code": PRODUCT, "t": "packaging",
               "v": "1.0", "b": Decimal("10"), "default": "Y"}],
        lines=[{"bom": B_TOP, "n": 10, "code": RAW_A, "q": Decimal("1"), "u": "KGM"}],
        lots=[
            {"code": RAW_A, "lot": "OK", "qty": Decimal("50"), "hold": Decimal("20"),
             "u": "KG", "st": "available", "exp": None},
            {"code": RAW_A, "lot": "HELD", "qty": Decimal("999"), "hold": Decimal("0"),
             "u": "KG", "st": "hold", "exp": None},
        ],
    )
    r = await pv.how_many_can_we_make(db_session, PRODUCT)
    # 50 - 20 on the available lot; the hold lot contributes nothing at all.
    assert r["can_make"] == "30"


@pytest.mark.asyncio
async def test_allocated_stock_DOES_count(db_session):
    """mrp-api's definition subtracts qty_onhold and NOT qty_allocated, on the
    grounds that allocated stock is still physically on the shelf. Copying the
    formula means copying that choice; this test exists so nobody 'fixes' it
    into disagreeing with net_requirement.py."""
    await _seed(
        db_session,
        boms=[{"id": B_TOP, "pk": "PK-AL", "code": PRODUCT, "t": "packaging",
               "v": "1.0", "b": Decimal("10"), "default": "Y"}],
        lines=[{"bom": B_TOP, "n": 10, "code": RAW_A, "q": Decimal("1"), "u": "KGM"}],
        lots=[{"code": RAW_A, "lot": "A", "qty": Decimal("40"), "hold": Decimal("0"),
               "u": "KG", "st": "available", "exp": None}],
    )
    await db_session.execute(sa.text(
        "UPDATE wms_inventory_lots SET qty_allocated = 35 WHERE material_code = :c"),
        {"c": RAW_A})
    await db_session.commit()

    r = await pv.how_many_can_we_make(db_session, PRODUCT)
    assert r["can_make"] == "40"


@pytest.mark.asyncio
async def test_stock_that_exists_but_cannot_be_used_says_so(db_session):
    """Nothing usable and nothing at all are different problems: one is a
    quality or expiry issue, the other is a purchasing one."""
    await _seed(
        db_session,
        boms=[{"id": B_TOP, "pk": "PK-EXP", "code": PRODUCT, "t": "packaging",
               "v": "1.0", "b": Decimal("10"), "default": "Y"}],
        lines=[
            {"bom": B_TOP, "n": 10, "code": RAW_A, "q": Decimal("1"), "u": "KGM"},
            {"bom": B_TOP, "n": 20, "code": RAW_B, "q": Decimal("1"), "u": "KGM"},
        ],
        lots=[{"code": RAW_A, "lot": "OLD", "qty": Decimal("999"), "hold": Decimal("0"),
               "u": "KG", "st": "available", "exp": date(2000, 1, 1)}],
    )
    r = await pv.how_many_can_we_make(db_session, PRODUCT)
    rows = {m["material_code"]: m for m in r["materials"]}

    assert r["can_make"] == "0"
    assert "none of it is usable" in rows[RAW_A]["problem"]
    assert "no stock records at all" in rows[RAW_B]["problem"]


@pytest.mark.asyncio
async def test_mismatched_units_are_refused_not_divided(db_session):
    """The failure that would look like an ordinary number."""
    await _seed(
        db_session,
        boms=[{"id": B_TOP, "pk": "PK-U", "code": PRODUCT, "t": "packaging",
               "v": "1.0", "b": Decimal("10"), "default": "Y"}],
        lines=[{"bom": B_TOP, "n": 10, "code": RAW_A, "q": Decimal("1"), "u": "LTR"}],
        lots=[{"code": RAW_A, "lot": "A", "qty": Decimal("500"), "hold": Decimal("0"),
               "u": "KG", "st": "available", "exp": None}],
    )
    r = await pv.how_many_can_we_make(db_session, PRODUCT)

    assert r["can_make"] is None
    assert r["could_not_compute"] == [RAW_A]
    assert "cannot compare" in r["materials"][0]["problem"]


@pytest.mark.asyncio
async def test_a_material_reached_twice_has_its_needs_added(db_session):
    """A raw material used by two different sub-recipes is needed for both.
    Taking either branch alone understates it and overstates the answer."""
    await _seed(
        db_session,
        boms=[
            {"id": B_TOP, "pk": "PK-D1", "code": PRODUCT, "t": "packaging",
             "v": "1.0", "b": Decimal("10"), "default": "Y"},
            {"id": B_MID, "pk": "PK-D2", "code": MID, "t": "milling",
             "v": "1.0", "b": Decimal("10"), "default": "Y"},
        ],
        lines=[
            {"bom": B_TOP, "n": 10, "code": RAW_A, "q": Decimal("1"), "u": "KGM"},
            {"bom": B_TOP, "n": 20, "code": MID, "q": Decimal("1"), "u": "KGM"},
            {"bom": B_MID, "n": 10, "code": RAW_A, "q": Decimal("4"), "u": "KGM"},
        ],
        lots=[{"code": RAW_A, "lot": "A", "qty": Decimal("100"), "hold": Decimal("0"),
               "u": "KG", "st": "available", "exp": None}],
    )
    r = await pv.how_many_can_we_make(db_session, PRODUCT)
    # 1 direct + (1 x 4) through M1 = 5 per unit; 100 / 5 = 20.
    assert r["materials"][0]["needed_per_unit"] == "5"
    assert r["can_make"] == "20"


@pytest.mark.asyncio
async def test_a_product_with_two_default_recipes_is_refused(db_session):
    """Five real products carry more than one default BOM. Picking one gives a
    confident answer about a recipe nobody asked for."""
    await _seed(
        db_session,
        boms=[
            {"id": B_TOP, "pk": "PK-X1", "code": PRODUCT, "t": "milling",
             "v": "1.1", "b": Decimal("10"), "default": "Y"},
            {"id": B_MID, "pk": "PK-X2", "code": PRODUCT, "t": "milling",
             "v": "1.2", "b": Decimal("10"), "default": "Y"},
        ],
        lines=[{"bom": B_TOP, "n": 10, "code": RAW_A, "q": Decimal("1"), "u": "KGM"}],
        lots=[],
    )
    with pytest.raises(pv.AmbiguousRecipe) as exc:
        await pv.how_many_can_we_make(db_session, PRODUCT)
    assert "1.1" in str(exc.value) and "1.2" in str(exc.value)


@pytest.mark.asyncio
async def test_a_recipe_that_loops_does_not_hang(db_session):
    """Defensive: no real BOM does this, and one that did would recurse until
    the process died rather than returning a wrong answer."""
    await _seed(
        db_session,
        boms=[
            {"id": B_TOP, "pk": "PK-L1", "code": PRODUCT, "t": "packaging",
             "v": "1.0", "b": Decimal("10"), "default": "Y"},
            {"id": B_MID, "pk": "PK-L2", "code": MID, "t": "milling",
             "v": "1.0", "b": Decimal("10"), "default": "Y"},
        ],
        lines=[
            {"bom": B_TOP, "n": 10, "code": MID, "q": Decimal("1"), "u": "KGM"},
            {"bom": B_MID, "n": 10, "code": PRODUCT, "q": Decimal("1"), "u": "KGM"},
        ],
        lots=[],
    )
    leaves, _, _ = await pv.explode(db_session, PRODUCT)
    assert any("loops back" in (c.problem or "") for c in leaves)


@pytest.mark.asyncio
async def test_no_recipe_is_none_not_zero(db_session):
    """A raw material has no recipe. Zero would read as "we can make none of
    it", which is a different and wrong claim."""
    assert await pv.how_many_can_we_make(db_session, "NOT-A-PRODUCT") is None
