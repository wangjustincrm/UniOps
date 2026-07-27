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
