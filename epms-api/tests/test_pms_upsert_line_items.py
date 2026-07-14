"""Incremental PMS import must sync line-item edits, not just header fields.

Regression (2026-07-13, PO-833-2607-01 / PR-20260713-0006): discount rows were
entered positive in PMS, imported, then corrected to negative in PMS. Later
incremental runs hit _upsert_pr/_upsert_po/_upsert_pa, which updated headers
only — the stale positive line items stayed in EPMS forever ("negatives turned
positive"). Line items are matched by sort_order (PMS item lists are
append-ordered and the initial import assigned sort_order from that same
enumeration), so UUIDs stay stable for GR/PA/invoice-allocation references.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.pa import PaLineItem, PaymentApplication
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PrLineItem, PurchaseRequest
from app.models.user import User
from app.models.vendor import Vendor
from scripts.import_pms import load
from tests.conftest import _TEST_DB_URL

PR_NUM = "PR-UPSLI-1"
PO_NUM = "PO-UPSLI-1"
PA_NUM = "PA-UPSLI-1"
OLD = "2024-01-01T00:00:00Z"
# Staged Modified must beat the EPMS rows' updated_at (defaulted to now() at
# seed time) or _is_local_edit() skips the doc entirely.
NEW = "2999-01-01T00:00:00Z"


def _staging(pr_item_rows, po_item_rows, pa_item_rows):
    return {
        "pr.json": [{
            "ID": "1", "PR_x0020_No": PR_NUM, "Title": "t", "PONo": PO_NUM,
            "Status": "Approved", "Currency": "CAD", "TotalPrice": "90",
            "Created": OLD, "Modified": NEW,
        }],
        "po.json": [{
            "ID": "1", "Title": PO_NUM, "Status": "Approved", "Currency": "CAD",
            "TotalPrice": "90", "Created": OLD, "Modified": NEW,
        }],
        "pa.json": [{
            "ID": "1", "Title": PA_NUM, "PONO": PO_NUM, "Status": "Paid",
            "Currency": "CAD", "TotalPrice": "90", "ItemsTotal": "90",
            "Created": OLD, "Modified": NEW,
        }],
        "pr_item.json": pr_item_rows,
        "po_item.json": po_item_rows,
        "pa_item.json": pa_item_rows,
    }


def _pr_item(i, unit, total, qty="1"):
    return {"ID": str(100 + i), "Title": PR_NUM, "Description": f"line {i}",
            "Qty": qty, "UOM": "EA", "UnitPrice": unit, "Total_x0020_Price": total}


def _po_item(i, unit, total, qty="1"):
    return {"ID": str(200 + i), "Title": PO_NUM, "Description": f"line {i}",
            "QTY": qty, "UOM": "EA", "UnitPrice": unit, "TotalPrice": total,
            "ReceivedQTY": "0"}


def _pa_item(i, unit, total, qty="1"):
    return {"ID": str(300 + i), "Title": PA_NUM, "Description": f"line {i}",
            "QTY": qty, "UOM": "EA", "UnitPrice": unit, "TotalPrice": total}


async def _seed(test_engine):
    """User/vendor + already-imported PR/PO/PA, each with 2 positive lines
    (the stale first-import snapshot)."""
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        user = User(
            id=uuid.uuid4(), email=f"upsli-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password=hash_password("x"), full_name="Owner",
            role="requester", is_active=True,
        )
        vendor = Vendor(
            id=uuid.uuid4(), code=f"V{uuid.uuid4().hex[:6]}", name="Acme",
            category="general", contact_name="N/A",
            contact_email="v@example.com", payment_terms="net30",
        )
        db.add_all([user, vendor])
        await db.flush()
        pr = PurchaseRequest(
            id=uuid.uuid4(), number=PR_NUM, title="t", type=2, status="approved",
            currency="CAD", amount=Decimal("300"), created_by=user.id,
        )
        po = PurchaseOrder(
            id=uuid.uuid4(), number=PO_NUM, title=PO_NUM, type=2,
            vendor_id=vendor.id, vendor_name=vendor.name, created_by=user.id,
            subtotal=Decimal("300"), total=Decimal("300"),
        )
        db.add_all([pr, po])
        await db.flush()
        pa = PaymentApplication(
            id=uuid.uuid4(), pa_number=PA_NUM, title="t", po_id=po.id,
            po_number=PO_NUM, vendor_id=vendor.id, vendor_name=vendor.name,
            invoice_ids=[], gr_ids=[], pa_type="regular",
            subtotal=Decimal("300"), tax_amount=Decimal("0"),
            shipping_amount=Decimal("0"), other_charges=Decimal("0"),
            payment_amount=Decimal("300"), currency="CAD", status="paid",
            created_by=user.id,
        )
        db.add(pa)
        await db.flush()
        for idx in (0, 1):
            db.add(PrLineItem(
                id=uuid.uuid4(), pr_id=pr.id, description=f"line {idx}",
                qty=Decimal("1"), unit="EA", unit_price=Decimal("100"),
                line_total=Decimal("100"), sort_order=idx,
            ))
            db.add(PoLineItem(
                id=uuid.uuid4(), po_id=po.id, description=f"line {idx}",
                qty=Decimal("1"), unit="EA", unit_price=Decimal("100"),
                line_total=Decimal("100"), received_qty=Decimal("0"),
                sort_order=idx,
            ))
            db.add(PaLineItem(
                id=uuid.uuid4(), pa_id=pa.id, description=f"line {idx}",
                qty=Decimal("1"), unit="EA", unit_price=Decimal("100"),
                line_total=Decimal("100"), sort_order=idx,
            ))
        await db.commit()
        return user.id, vendor.id, pr.id, po.id, pa.id


async def _cleanup(test_engine, user_id, vendor_id):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await db.execute(sa_delete(PaymentApplication).where(PaymentApplication.pa_number == PA_NUM))
        await db.execute(sa_delete(PurchaseOrder).where(PurchaseOrder.number == PO_NUM))
        await db.execute(sa_delete(PurchaseRequest).where(PurchaseRequest.number == PR_NUM))
        await db.execute(sa_delete(User).where(User.id == user_id))
        await db.execute(sa_delete(Vendor).where(Vendor.id == vendor_id))
        await db.commit()


async def _lines(test_engine, model, doc_col, doc_id):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        return (await db.execute(
            select(model).where(doc_col == doc_id).order_by(model.sort_order)
        )).scalars().all()


async def test_upsert_syncs_line_item_values_and_appends(test_engine, monkeypatch):
    """PMS corrected line 2 to negative and appended line 3 → upsert must apply
    both to PR, PO and PA; PO subtotal follows the lines; UUIDs stay stable."""
    user_id, vendor_id, pr_id, po_id, pa_id = await _seed(test_engine)
    before_po_ids = [li.id for li in await _lines(test_engine, PoLineItem, PoLineItem.po_id, po_id)]
    staging = _staging(
        [_pr_item(0, "100", "100"), _pr_item(1, "-5891", "-5891"), _pr_item(2, "50", "50")],
        [_po_item(0, "100", "100"), _po_item(1, "-5891", "-5891"), _po_item(2, "50", "50")],
        [_pa_item(0, "100", "100"), _pa_item(1, "-5891", "-5891"), _pa_item(2, "50", "50")],
    )
    monkeypatch.setattr(load, "load_staging", lambda fn: [dict(x) for x in staging.get(fn, [])])
    try:
        await load.run_load(dry_run=False, mode="upsert", db_url=_TEST_DB_URL,
                            reconstruct=False, dedup_invoices=False)

        for model, col, doc in (
            (PrLineItem, PrLineItem.pr_id, pr_id),
            (PoLineItem, PoLineItem.po_id, po_id),
            (PaLineItem, PaLineItem.pa_id, pa_id),
        ):
            lines = await _lines(test_engine, model, col, doc)
            assert len(lines) == 3, f"{model.__name__}: appended row must be inserted"
            assert lines[0].unit_price == Decimal("100")
            assert lines[1].unit_price == Decimal("-5891"), \
                f"{model.__name__}: negative correction must sync"
            assert lines[1].line_total == Decimal("-5891")
            assert lines[2].unit_price == Decimal("50")

        po_lines = await _lines(test_engine, PoLineItem, PoLineItem.po_id, po_id)
        assert [li.id for li in po_lines[:2]] == before_po_ids, \
            "existing PO line UUIDs must not change (GR/PA/allocations reference them)"

        sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            po = await db.get(PurchaseOrder, po_id)
            assert po.subtotal == Decimal("-5741"), "PO subtotal must follow synced lines"
    finally:
        await _cleanup(test_engine, user_id, vendor_id)


async def test_upsert_skips_items_of_locally_edited_doc(test_engine, monkeypatch):
    """A doc edited in EPMS after the PMS change keeps its local line items."""
    user_id, vendor_id, pr_id, po_id, pa_id = await _seed(test_engine)
    staging = _staging(
        [_pr_item(0, "100", "100"), _pr_item(1, "-1", "-1")],
        [_po_item(0, "100", "100"), _po_item(1, "-1", "-1")],
        [_pa_item(0, "100", "100"), _pa_item(1, "-1", "-1")],
    )
    # Staged Modified older than the EPMS rows' updated_at (seeded with now()).
    for key in ("pr.json", "po.json", "pa.json"):
        staging[key][0]["Modified"] = OLD
    monkeypatch.setattr(load, "load_staging", lambda fn: [dict(x) for x in staging.get(fn, [])])
    try:
        await load.run_load(dry_run=False, mode="upsert", db_url=_TEST_DB_URL,
                            reconstruct=False, dedup_invoices=False)
        lines = await _lines(test_engine, PoLineItem, PoLineItem.po_id, po_id)
        assert [li.unit_price for li in lines] == [Decimal("100"), Decimal("100")], \
            "locally-edited doc must not have its items clobbered"
    finally:
        await _cleanup(test_engine, user_id, vendor_id)


async def test_upsert_empty_staged_items_never_wipes(test_engine, monkeypatch):
    """No staged item rows for the doc (e.g. failed/partial extract) → items kept."""
    user_id, vendor_id, pr_id, po_id, pa_id = await _seed(test_engine)
    staging = _staging([], [], [])
    monkeypatch.setattr(load, "load_staging", lambda fn: [dict(x) for x in staging.get(fn, [])])
    try:
        await load.run_load(dry_run=False, mode="upsert", db_url=_TEST_DB_URL,
                            reconstruct=False, dedup_invoices=False)
        for model, col, doc in (
            (PrLineItem, PrLineItem.pr_id, pr_id),
            (PoLineItem, PoLineItem.po_id, po_id),
            (PaLineItem, PaLineItem.pa_id, pa_id),
        ):
            assert len(await _lines(test_engine, model, col, doc)) == 2, \
                f"{model.__name__}: empty staging must never delete items"
    finally:
        await _cleanup(test_engine, user_id, vendor_id)


async def test_upsert_removes_surplus_unreferenced_rows(test_engine, monkeypatch):
    """A line deleted in PMS is removed in EPMS when nothing references it."""
    user_id, vendor_id, pr_id, po_id, pa_id = await _seed(test_engine)
    staging = _staging(
        [_pr_item(0, "100", "100")],
        [_po_item(0, "100", "100")],
        [_pa_item(0, "100", "100")],
    )
    monkeypatch.setattr(load, "load_staging", lambda fn: [dict(x) for x in staging.get(fn, [])])
    try:
        await load.run_load(dry_run=False, mode="upsert", db_url=_TEST_DB_URL,
                            reconstruct=False, dedup_invoices=False)
        for model, col, doc in (
            (PrLineItem, PrLineItem.pr_id, pr_id),
            (PoLineItem, PoLineItem.po_id, po_id),
            (PaLineItem, PaLineItem.pa_id, pa_id),
        ):
            assert len(await _lines(test_engine, model, col, doc)) == 1, \
                f"{model.__name__}: surplus unreferenced row must be deleted"
    finally:
        await _cleanup(test_engine, user_id, vendor_id)
