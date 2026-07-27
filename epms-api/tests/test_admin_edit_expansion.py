"""Tests for expanded Data Maintenance edit (references, line items, approval state)."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def test_fieldspec_reference_serialization():
    from app.admin.fields import FieldSpec

    f = FieldSpec("vendor_id", "reference", True, label="Vendor",
                  ref_source="vendors", ref_name_field="vendor_name")
    d = f.to_dict()
    assert d["type"] == "reference"
    assert d["ref_source"] == "vendors"
    assert d["ref_name_field"] == "vendor_name"


def test_entityschema_child_serialization():
    from app.admin.fields import EntitySchema, FieldSpec, ChildSchema
    from app.models.pr import PrLineItem

    child = ChildSchema(
        table_label="Line Items", model=PrLineItem, fk_field="pr_id",
        fields=[FieldSpec("description", "string", True), FieldSpec("qty", "decimal", True)],
    )
    schema = EntitySchema(
        key="pr", label="PR", number_field="number",
        list_columns=["number"], search_fields=["number"], order_by="created_at desc",
        fields=[FieldSpec("number", "string", False)], child=child,
    )
    d = schema.to_dict()
    assert d["child"]["table_label"] == "Line Items"
    assert d["child"]["fk_field"] == "pr_id"
    assert [f["name"] for f in d["child"]["fields"]] == ["description", "qty"]


@pytest.mark.asyncio
async def test_resolver_fetch_and_search_vendors(test_engine):
    from app.models.vendor import Vendor
    from app.admin.resolvers import get_resolver

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    vid = uuid.uuid4()
    async with factory() as db:
        db.add(Vendor(id=vid, code="V-ABC", name="Acme Supplies",
                      category="supplier", contact_name="A", contact_email="a@x.com"))
        await db.commit()

    resolver = get_resolver("vendors")
    async with factory() as db:
        got = await resolver.fetch_by_id(db, vid)
        assert got is not None and got.label == "Acme Supplies"
        hits = await resolver.search(db, "acme", limit=10)
        assert any(h.id == vid for h in hits)


def test_registry_pr_has_reference_and_expanded_fields():
    from app.admin.registry import REGISTRY

    schema = REGISTRY["pr"].schema
    names = {f.name for f in schema.fields}
    assert {"created_by", "vendor_id", "cost_center_id", "project_code",
            "delivery_address", "is_prepaid"} <= names
    created_by = schema.field_spec("created_by")
    assert created_by.type == "reference" and created_by.ref_source == "users"
    assert created_by.editable is True
    vendor = schema.field_spec("vendor_id")
    assert vendor.ref_source == "vendors" and vendor.ref_name_field == "vendor_name"


def test_reference_name_fields_serialize_readonly():
    from app.admin.registry import REGISTRY

    for key in ("pr", "po", "pa"):
        schema = REGISTRY[key].schema
        vendor_name = schema.field_spec("vendor_name")
        assert vendor_name is not None, f"{key} missing vendor_name field"
        assert vendor_name.editable is False, f"{key} vendor_name must be read-only"

    pr_schema = REGISTRY["pr"].schema
    cost_center_name = pr_schema.field_spec("cost_center_name")
    assert cost_center_name is not None
    assert cost_center_name.editable is False


@pytest.mark.asyncio
async def test_edit_reference_sets_id_and_syncs_name(test_engine):
    from app.models.user import User
    from app.models.vendor import Vendor
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); new_vendor = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"c-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="Creator", role="requester"))
        db.add(Vendor(id=new_vendor, code="V-NEW", name="New Vendor Inc",
                      category="supplier", contact_name="N", contact_email="n@x.com"))
        await db.commit()
    pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-REF1", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), vendor_name="Old Vendor",
                               created_by=creator))
        await db.commit()

    async with factory() as db:
        await service.edit_record(db, "pr", pr_id, {"vendor_id": str(new_vendor)},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    async with factory() as db:
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert str(pr.vendor_id) == str(new_vendor)
        assert pr.vendor_name == "New Vendor Inc"   # denormalized name synced


@pytest.mark.asyncio
async def test_edit_reference_unknown_id_rejected(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"c2-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C2", role="requester"))
        await db.commit()
    pr_id = uuid.uuid4()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-REF2", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), created_by=creator))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ValueError, match="not found"):
            await service.edit_record(db, "pr", pr_id, {"vendor_id": str(uuid.uuid4())},
                                      actor_id=creator, actor_email="admin@x.com")


def test_recompute_po_header():
    from decimal import Decimal
    from app.admin.recompute import recompute_header

    class _Row:
        tax_rate = Decimal("0.05")
    row = _Row()
    lines = [{"line_total": Decimal("100.00")}, {"line_total": Decimal("50.00")}]
    result = recompute_header("po", row, lines)
    assert result["subtotal"] == Decimal("150.00")
    assert result["tax_amount"] == Decimal("7.50")
    assert result["total"] == Decimal("157.50")


def test_recompute_pa_header_with_charges():
    from decimal import Decimal
    from app.admin.recompute import recompute_header

    class _Row:
        tax_rate = Decimal("0.10")
        shipping_amount = Decimal("20.00")
        other_charges = Decimal("5.00")
        prepayment_applied = None
    row = _Row()
    lines = [{"line_total": Decimal("200.00")}]
    result = recompute_header("pa", row, lines)
    assert result["subtotal"] == Decimal("200.00")
    assert result["tax_amount"] == Decimal("20.00")
    assert result["payment_amount"] == Decimal("245.00")  # 200 + 20 + 20 + 5 - 0


def test_registry_entities_have_child_line_items():
    from app.admin.registry import REGISTRY

    for key, fk in [("pr", "pr_id"), ("po", "po_id"), ("pa", "pa_id")]:
        child = REGISTRY[key].schema.child
        assert child is not None, f"{key} missing child schema"
        assert child.fk_field == fk
        names = {f.name for f in child.fields}
        assert {"description", "qty", "unit", "unit_price"} <= names
        # line_total is server-computed → read-only in the child schema
        lt = child.field_type("line_total")
        assert lt == "decimal"
        assert not any(f.name == "line_total" and f.editable for f in child.fields)


@pytest.mark.asyncio
async def test_edit_line_items_add_update_delete_and_recompute(test_engine):
    from app.models.user import User
    from app.models.po import PurchaseOrder, PoLineItem
    from app.models.vendor import Vendor
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); vid = uuid.uuid4(); po_id = uuid.uuid4(); keep_line = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"po-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        db.add(Vendor(id=vid, code="V-PO", name="V", category="supplier",
                      contact_name="A", contact_email="a@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number="PO-LI1", title="t", type=1, status="draft",
                             currency="CAD", subtotal=Decimal("0"), tax_rate=Decimal("0.05"),
                             tax_amount=Decimal("0"), total=Decimal("0"),
                             vendor_id=vid, vendor_name="V", created_by=creator))
        await db.flush()
        db.add(PoLineItem(id=keep_line, po_id=po_id, description="old", qty=Decimal("1"),
                          unit="ea", unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0))
        await db.commit()

    # keep+update the existing line (qty 1->2), add a new line, (implicitly delete none here)
    line_items = [
        {"id": str(keep_line), "description": "updated", "qty": "2", "unit": "ea", "unit_price": "10"},
        {"description": "brand new", "qty": "3", "unit": "ea", "unit_price": "100"},
    ]
    async with factory() as db:
        await service.edit_record(db, "po", po_id, {"line_items": line_items},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()

    async with factory() as db:
        po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
        lines = (await db.execute(select(PoLineItem).where(PoLineItem.po_id == po_id))).scalars().all()
        assert len(lines) == 2
        # 2*10 + 3*100 = 320 subtotal; tax 5% = 16.00; total 336.00
        assert po.subtotal == Decimal("320.00")
        assert po.tax_amount == Decimal("16.00")
        assert po.total == Decimal("336.00")
        assert {l.line_total for l in lines} == {Decimal("20.00"), Decimal("300.00")}


@pytest.mark.asyncio
async def test_edit_line_items_deletes_dropped_rows(test_engine):
    from app.models.user import User
    from app.models.pr import PurchaseRequest, PrLineItem
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    creator = uuid.uuid4(); pr_id = uuid.uuid4(); l1 = uuid.uuid4(); l2 = uuid.uuid4()
    async with factory() as db:
        db.add(User(id=creator, email=f"pr-{creator.hex[:6]}@x.com", hashed_password="x",
                    full_name="C", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseRequest(id=pr_id, number="PR-LI2", title="t", type=1, status="draft",
                               currency="CAD", amount=Decimal("0"), created_by=creator))
        await db.flush()
        db.add(PrLineItem(id=l1, pr_id=pr_id, description="a", qty=Decimal("1"), unit="ea",
                          unit_price=Decimal("10"), line_total=Decimal("10"), sort_order=0))
        db.add(PrLineItem(id=l2, pr_id=pr_id, description="b", qty=Decimal("1"), unit="ea",
                          unit_price=Decimal("20"), line_total=Decimal("20"), sort_order=1))
        await db.commit()
    async with factory() as db:
        await service.edit_record(db, "pr", pr_id,
                                  {"line_items": [{"id": str(l1), "description": "a", "qty": "1",
                                                   "unit": "ea", "unit_price": "10"}]},
                                  actor_id=creator, actor_email="admin@x.com")
        await db.commit()
    async with factory() as db:
        remaining = (await db.execute(select(PrLineItem).where(PrLineItem.pr_id == pr_id))).scalars().all()
        pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == pr_id))).scalar_one()
        assert {l.id for l in remaining} == {l1}
        assert pr.amount == Decimal("10.00")
