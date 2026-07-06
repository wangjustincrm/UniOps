"""Invoice list search: GET /invoices?search=... must filter by ref / vendor /
invoice number / PO number.

Regression: the endpoint never declared a `search` query param, so FastAPI
silently dropped it and the invoice list search box did nothing.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.crud import invoice as invoice_crud
from app.models.invoice import Invoice
from app.models.user import User
from app.models.vendor import Vendor

TOK = "srchuniqtoken"  # unique enough that no other seeded row matches


async def _seed(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = User(
            id=uuid.uuid4(), email=f"u-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password=hash_password("x"), full_name="U", role="system_admin",
            is_active=True,
        )
        vendor = Vendor(
            id=uuid.uuid4(), code=f"V{uuid.uuid4().hex[:6]}", name="V",
            category="general", contact_name="N/A",
            contact_email="v@example.com", payment_terms="net30",
        )
        db.add_all([user, vendor])
        await db.flush()
        common = dict(
            vendor_id=vendor.id, amount=Decimal("10"), total_amount=Decimal("10"),
            invoice_date=date(2024, 1, 1), due_date=date(2024, 1, 1), uploaded_by=user.id,
        )
        inv_a = Invoice(id=uuid.uuid4(), internal_ref=f"INV-{TOK}-A",
                        vendor_invoice_number="AAA-111", vendor_name=f"{TOK} Acme",
                        po_number=f"PO-{TOK}-A", **common)
        inv_b = Invoice(id=uuid.uuid4(), internal_ref=f"INV-{TOK}-B",
                        vendor_invoice_number=f"{TOK}-XYZ", vendor_name="Globex",
                        po_number="PO-plain-B", **common)
        db.add_all([inv_a, inv_b])
        await db.commit()
        return user.id, vendor.id


async def _cleanup(test_engine, vendor_id, user_id):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await db.execute(sa_delete(Invoice).where(Invoice.internal_ref.like(f"INV-{TOK}-%")))
        await db.execute(sa_delete(Vendor).where(Vendor.id == vendor_id))
        await db.execute(sa_delete(User).where(User.id == user_id))
        await db.commit()


async def test_get_all_search_filters_by_vendor_invoice_and_po(test_engine):
    user_id, vendor_id = await _seed(test_engine)
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sf() as db:
            # vendor name
            items, _ = await invoice_crud.get_all(db, search=f"{TOK} acme")
            refs = {i.internal_ref for i in items}
            assert f"INV-{TOK}-A" in refs
            assert f"INV-{TOK}-B" not in refs

            # invoice number
            items, _ = await invoice_crud.get_all(db, search=f"{TOK}-XYZ")
            refs = {i.internal_ref for i in items}
            assert refs == {f"INV-{TOK}-B"}

            # PO number
            items, _ = await invoice_crud.get_all(db, search=f"PO-{TOK}-A")
            refs = {i.internal_ref for i in items}
            assert refs == {f"INV-{TOK}-A"}

            # internal ref
            items, _ = await invoice_crud.get_all(db, search=f"INV-{TOK}-B")
            refs = {i.internal_ref for i in items}
            assert refs == {f"INV-{TOK}-B"}
    finally:
        await _cleanup(test_engine, vendor_id, user_id)
