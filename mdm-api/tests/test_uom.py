"""UnitOfMeasure CRUD — code case preserved, dedup, reference guard."""
import pytest

from app.crud import uom as uom_crud
from app.models.part import Part
from app.schemas.uom import UomCreate, UomUpdate


async def test_create_preserves_case_and_reads_back(db_session):
    created = await uom_crud.create(db_session, UomCreate(code="kg", name="Kilogram", dimension="mass"))
    assert created.code == "kg"  # NOT upper-cased
    assert created.dimension == "mass"

    fetched = await uom_crud.get_by_code(db_session, "kg")
    assert fetched is not None
    assert fetched.id == created.id


async def test_get_all_active_only(db_session):
    await uom_crud.create(db_session, UomCreate(code="pcs", name="Pieces", dimension="count"))
    inactive = await uom_crud.create(db_session, UomCreate(code="box", name="Box", dimension="count"))
    await uom_crud.update(db_session, inactive, UomUpdate(is_active=False))

    codes_all = {u.code for u in await uom_crud.get_all(db_session, active_only=False)}
    codes_active = {u.code for u in await uom_crud.get_all(db_session, active_only=True)}
    assert {"pcs", "box"} <= codes_all
    assert "pcs" in codes_active
    assert "box" not in codes_active


async def test_update_changes_fields(db_session):
    u = await uom_crud.create(db_session, UomCreate(code="m", name="Meter", dimension="length"))
    updated = await uom_crud.update(db_session, u, UomUpdate(name="Metre", is_active=False))
    assert updated.name == "Metre"
    assert updated.is_active is False


async def test_count_references_counts_parts_using_code(db_session):
    await uom_crud.create(db_session, UomCreate(code="roll", name="Roll", dimension="count"))
    db_session.add(Part(
        code="P-UOM-1", category="Misc", name="Tape", description=None,
        supplier="ACME", supplier_part_no="SP1", unit_price=0, unit="roll", is_active=True,
    ))
    await db_session.flush()
    refs = await uom_crud.count_references(db_session, "roll")
    assert refs["parts"] == 1
    assert (await uom_crud.count_references(db_session, "lot"))["parts"] == 0
