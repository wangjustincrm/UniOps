"""Business Partner CRUD + role filters (Phase 0-B3)."""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models.business_partner import BusinessPartner


def _partner(**over) -> BusinessPartner:
    kw = dict(
        code=f"BP-{uuid.uuid4().hex[:8]}", name="ACME Dairy Supplies",
        category="Packaging", contact_name="Amy", contact_email="amy@acme.ca",
        payment_terms="net30", currency="CAD", is_active=True,
        is_supplier=True, is_customer=False,
    )
    kw.update(over)
    return BusinessPartner(**kw)


async def test_partner_roundtrip_with_fin_md_006_fields(db_session):
    p = _partner(
        is_customer=True, tax_number="123456789RT0001",
        customer_type="business", province="ON", credit_limit=Decimal("50000.00"),
    )
    db_session.add(p)
    await db_session.flush()

    row = (await db_session.execute(
        select(BusinessPartner).where(BusinessPartner.id == p.id)
    )).scalar_one()
    assert row.is_supplier and row.is_customer
    assert row.tax_number == "123456789RT0001"
    assert row.customer_type == "business"
    assert row.province == "ON"
    assert row.credit_limit == Decimal("50000.00")


async def test_role_filters(db_session):
    sup = _partner()
    cust = _partner(is_supplier=False, is_customer=True)
    both = _partner(is_customer=True)
    db_session.add_all([sup, cust, both])
    await db_session.flush()

    suppliers = (await db_session.execute(
        select(BusinessPartner).where(BusinessPartner.is_supplier.is_(True),
                                      BusinessPartner.id.in_([sup.id, cust.id, both.id]))
    )).scalars().all()
    customers = (await db_session.execute(
        select(BusinessPartner).where(BusinessPartner.is_customer.is_(True),
                                      BusinessPartner.id.in_([sup.id, cust.id, both.id]))
    )).scalars().all()
    assert {p.id for p in suppliers} == {sup.id, both.id}
    assert {p.id for p in customers} == {cust.id, both.id}


def test_partner_update_schema_accepts_code():
    """EPMS Vendor list edits POID via PATCH /partners; the schema must not drop `code`."""
    from app.schemas.business_partner import PartnerUpdate
    body = PartnerUpdate(code="NEW-01")
    assert body.model_dump(exclude_unset=True) == {"code": "NEW-01"}


async def test_vendor_alias_is_business_partner(db_session):
    """Legacy mdm imports keep working: Vendor IS BusinessPartner now."""
    from app.models.vendor import Vendor
    assert Vendor is BusinessPartner
