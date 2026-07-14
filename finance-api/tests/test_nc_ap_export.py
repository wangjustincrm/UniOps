"""NC AP export (parallel-run) — batches, assembly, xlsx, API."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select


async def test_export_batch_and_ap_columns(db_session):
    from app.models.ap_invoice import ApInvoice
    from app.models.nc_export import NcExportBatch
    b = NcExportBatch(exported_by=uuid.uuid4(),
                      exported_at=datetime.now(timezone.utc), ap_count=2,
                      filename="NC-AP-x.xlsx")
    db_session.add(b)
    inv = ApInvoice(ap_invoice_number="AP-2026-0001", source="epms",
                    source_invoice_id=uuid.uuid4(), amount=Decimal("100"),
                    tax_amount=Decimal("13"), total_amount=Decimal("113"),
                    currency="CAD", invoice_date=date(2026, 7, 1), status="posted")
    db_session.add(inv)
    await db_session.flush()
    inv.nc_exported_at = datetime.now(timezone.utc)
    inv.nc_export_batch_id = b.id
    await db_session.flush()
    got = (await db_session.execute(select(ApInvoice).where(
        ApInvoice.id == inv.id))).scalar_one()
    assert got.nc_export_batch_id == b.id


async def test_new_mirror_subsets_readable(db_session):
    from app.models.mirrors import ExpenseInvoice, InvoicePoAllocation, PurchaseRequest
    po = uuid.uuid4()
    db_session.add(PurchaseRequest(id=uuid.uuid4(), po_id=po,
                                   cost_center_id=uuid.uuid4(), budget_code="CRM004",
                                   department_name="Engineering", created_by=uuid.uuid4()))
    db_session.add(InvoicePoAllocation(id=uuid.uuid4(), invoice_id=uuid.uuid4(),
                                       po_id=po, allocated_amount=Decimal("50"),
                                       allocated_tax=Decimal("6.50")))
    db_session.add(ExpenseInvoice(id=uuid.uuid4(), pa_id=uuid.uuid4()))
    await db_session.flush()
    got = (await db_session.execute(select(PurchaseRequest).where(
        PurchaseRequest.po_id == po))).scalar_one()
    assert got.department_name == "Engineering"
