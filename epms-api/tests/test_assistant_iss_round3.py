"""Assistant fixes, third round (ISS-0013).

1. A list cut at the row cap must carry the real total, so "100 records match"
   is never said of 486.
2. "Matched but not received" must be expressible without counting agreement
   invoices, which never have a goods receipt number.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.crud import po as po_crud
from app.models.agreement import PurchaseAgreement
from app.models.invoice import Invoice
from app.models.po import PoLineItem, PurchaseOrder
from app.models.vendor import Vendor
from app.services import assistant_llm

pytestmark = pytest.mark.asyncio
CHAT = "/api/v1/assistant/chat"


def _user_id(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    return uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                                algorithms=[settings.JWT_ALGORITHM])["sub"])


def _run(monkeypatch, query: dict) -> list:
    seen: list = []

    async def fake_plan(schema, message, context=None, retry_error=None, history=None):
        return {"kind": "query", "query": query}

    async def fake_narrate(message, q, result):
        seen.append(result)
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(assistant_llm, "plan", fake_plan)
    monkeypatch.setattr(assistant_llm, "narrate", fake_narrate)
    return seen


async def _vendor(db):
    v = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Messer", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    return v


async def test_truncated_list_carries_the_real_total(admin_client, test_engine, monkeypatch):
    tag = uuid.uuid4().hex[:6]
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        v = await _vendor(db)
        for i in range(5):
            db.add(PurchaseOrder(number=f"PO-TR-{tag}-{i}", title="t", type=2, vendor_id=v.id,
                                 vendor_name=v.name, status="issued", subtotal=Decimal("1"),
                                 created_by=_user_id(admin_client)))
        await db.commit()
    seen = _run(monkeypatch, {"entity": "purchase_order", "select": ["number"], "limit": 2,
                              "where": [{"field": "number", "op": "like", "value": f"PO-TR-{tag}"}]})
    r = await admin_client.post(CHAT, json={"message": "列出这些采购订单"})
    assert r.status_code == 200, r.text
    assert seen[0]["row_count"] == 2 and seen[0]["truncated"] is True
    assert seen[0]["total_rows"] == 5
    assert r.json()["sources"]["total_rows"] == 5           # the evidence line shows "2 of 5"


async def test_untruncated_list_does_not_pay_for_a_count(admin_client, monkeypatch):
    seen = _run(monkeypatch, {"entity": "purchase_order", "select": ["number"],
                              "where": [{"field": "number", "op": "eq", "value": "PO-NOPE"}]})
    await admin_client.post(CHAT, json={"message": "x"})
    assert seen[0]["truncated"] is False and "total_rows" not in seen[0]


async def test_matched_but_not_received_excludes_agreement_invoices(admin_client, test_engine,
                                                                   monkeypatch):
    tag = uuid.uuid4().hex[:6]
    uid = _user_id(admin_client)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        v = await _vendor(db)

        async def po(received):
            p = PurchaseOrder(number=f"PO-RC-{tag}-{received}", title="t", type=2, vendor_id=v.id,
                              vendor_name=v.name, status="issued", subtotal=Decimal("10"),
                              created_by=uid)
            db.add(p)
            await db.flush()
            db.add(PoLineItem(po_id=p.id, description="x", qty=Decimal("1"), unit="ea",
                              unit_price=Decimal("10"), line_total=Decimal("10"),
                              received_qty=Decimal(received)))
            await db.flush()
            return p

        not_received, received = await po("0"), await po("1")
        agr = PurchaseAgreement(number=f"AGR-RC-{tag}", title="a", agreement_type="house_account",
                                vendor_id=v.id, vendor_name=v.name, valid_from=date(2026, 1, 1),
                                valid_to=date(2026, 12, 31), status="active", created_by=uid)
        db.add(agr)
        await db.flush()

        def inv(ref, **kw):
            return Invoice(internal_ref=f"INV-RC-{tag}-{ref}", vendor_invoice_number=ref,
                           vendor_id=v.id, vendor_name=v.name, amount=Decimal("10"),
                           tax_amount=Decimal("0"), total_amount=Decimal("10"),
                           invoice_date=date(2026, 10, 1), due_date=date(2026, 11, 1),
                           status="matched", line_items=[], uploaded_by=uid, **kw)
        db.add_all([
            inv("po-none", po_id=not_received.id, po_number=not_received.number),
            inv("po-recv", po_id=received.id, po_number=received.number),
            inv("agr", agreement_id=agr.id, agreement_number=agr.number),
        ])
        await db.commit()

    seen = _run(monkeypatch, {
        "entity": "invoice",
        "select": ["vendor_invoice_number", "billed_against", "po_receipt_recorded", "gr_number"],
        "where": [{"field": "internal_ref", "op": "like", "value": f"INV-RC-{tag}"}]})
    r = await admin_client.post(CHAT, json={"message": "有哪些发票已经匹配了但货还没收到？"})
    assert r.status_code == 200, r.text
    rows = {row["vendor_invoice_number"]: row for row in seen[0]["rows"]}
    assert rows["po-none"]["billed_against"] == "purchase_order"
    assert rows["po-none"]["po_receipt_recorded"] is False
    assert rows["po-recv"]["po_receipt_recorded"] is True       # received_qty counts
    assert rows["agr"]["billed_against"] == "agreement"
    assert rows["agr"]["po_receipt_recorded"] is None            # not applicable, never "no"
    # All three lack a GR number — the column that misled the answer.
    assert all(row["gr_number"] is None for row in rows.values())

    # And the filter the label prescribes picks exactly the one.
    seen = _run(monkeypatch, {
        "entity": "invoice", "select": ["vendor_invoice_number"],
        "where": [{"field": "internal_ref", "op": "like", "value": f"INV-RC-{tag}"},
                  {"field": "status", "op": "eq", "value": "matched"},
                  {"field": "billed_against", "op": "eq", "value": "purchase_order"},
                  {"field": "po_receipt_recorded", "op": "eq", "value": False}]})
    await admin_client.post(CHAT, json={"message": "x"})
    assert [row["vendor_invoice_number"] for row in seen[0]["rows"]] == ["po-none"]

    # The PA receipt gate reads the same rule.
    async with factory() as db:
        assert await po_crud.po_has_receipt_evidence(db, not_received.id) is False
        assert await po_crud.po_has_receipt_evidence(db, received.id) is True
