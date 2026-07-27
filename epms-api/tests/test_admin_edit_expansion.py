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
