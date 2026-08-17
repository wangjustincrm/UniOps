"""Columns on the material master that **mrp-api reads directly**.

MRP mirrors `materials` read-only (`mrp-api/app/models/epms_mirror.py`) rather
than going through this service's HTTP client, for one specific reason worth
knowing before changing anything here: MRP's name lookup
(`mrp-api/app/services/mdm_client.py`) never raises — it degrades every name to
None so a stock screen does not 5xx because master data is slow. That is right
for a NAME and wrong for an EXCLUSION. `erp_class_code` decides whether raw milk
is counted in stock and on-order figures, and a class that degrades to None
silently stops excluding: raw milk reappears in the numbers with nothing on
screen to say so.

The test lives here, in the owning service, because it must fail in the suite of
whoever renames the column. MRP's own tests build their fixtures from MRP's
mirror definitions and would agree with themselves regardless.

If this fails, the fix is not to edit the expectations. Update
`mrp-api/app/models/epms_mirror.py` and `mrp-api/app/services/in_transit.py` in
the same change.
"""
import pytest
import sqlalchemy as sa

# column -> information_schema.data_type. Only what MRP actually reads.
CONSUMED = {
    "code": "character varying",
    "name": "character varying",
    "base_uom": "character varying",
    "shelf_life_months": "integer",
    # ★ The raw-milk exclusion. '0101' = Raw Milk, never counted as stock or as
    # on order (business rule 2026-08-17).
    "erp_class_code": "character varying",
    "erp_class_name": "character varying",
}


@pytest.mark.anyio
async def test_columns_mrp_reads_still_exist(db_session):
    rows = (await db_session.execute(sa.text(
        "select column_name, data_type from information_schema.columns "
        "where table_name = 'materials'"
    ))).all()
    actual = {name: dtype for name, dtype in rows}
    assert actual, "materials does not exist"
    for column, dtype in CONSUMED.items():
        assert column in actual, (
            f"mrp-api reads materials.{column} directly. Dropping or renaming "
            f"erp_class_code in particular does not break anything visibly — it "
            f"silently stops excluding raw milk from MRP's stock and on-order "
            f"figures. Update mrp-api/app/models/epms_mirror.py in this change.")
        assert actual[column] == dtype, (
            f"materials.{column} is now {actual[column]}, was {dtype}; "
            f"mrp-api/app/models/epms_mirror.py maps the old type.")


@pytest.mark.anyio
async def test_the_sync_still_populates_the_class_mrp_filters_on(db_session):
    """A column that exists but stops being filled is the same outage.

    `erp_class_code` is promoted from `erp_materials.accounting_group` by
    material_sync. If that mapping were dropped, the column would survive this
    file's first test, quietly go NULL for every material, and MRP would count
    raw milk again — the exact failure mode the mirror exists to prevent.
    """
    import uuid
    from datetime import UTC, datetime

    from app.models.erp_material import ErpMaterial
    from app.models.material import Material
    from app.services.material_sync import sync_materials

    db_session.add(ErpMaterial(
        id=uuid.uuid4(), erp_part_no="CR0010", description="Raw Cows Milk",
        unit_meas="KGM", accounting_group="0101", accounting_group_name="Raw Milk",
        raw_payload={"part_no": "CR0010", "accounting_group": "0101"},
        synced_at=datetime.now(UTC),
    ))
    await db_session.commit()

    await sync_materials(db_session)

    material = (await db_session.execute(
        sa.select(Material).where(Material.code == "CR0010"))).scalar_one()
    assert material.erp_class_code == "0101"
    assert material.erp_class_name == "Raw Milk"
