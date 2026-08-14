"""Task 5: `InvoiceResponse.claimed_receipts` — a read-only projection of the
receipts an invoice has claimed (Task 7's `PUT /invoices/{id}/receipts`),
exposed on the invoice's own read permission (view_invoice) instead of the
dedicated receipts route.

Why this exists at all (see the docstring on schemas/invoice.py::ClaimedReceipt
and crud/invoice.py::attach_claimed_receipts for the full story): the existing
`GET /invoices/{id}/agreements/{aid}/receipts` route is gated by
`_require_invoice_match_access` — AP staff, the invoice's uploader, or a
match-task holder — and 403s for the PA approvers (GM, Finance Manager,
department managers) a later task needs to show this data to. It also only
searches `candidates_for_vendor`, so a CLOSED agreement 404s outright. Riding
the data on the invoice response instead sidesteps both problems: whoever can
read the invoice sees what it claims.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_agreement_invoice_match import _make_active_agreement
from tests.test_agreements import seed_vendor_and_user
from tests.test_invoice_receipts import _create_receipt, _matched_invoice

pytestmark = pytest.mark.asyncio

INV_URL = "/api/v1/invoices"


async def test_claimed_receipts_returned_on_detail_with_amount_less_receipt_preserved(
    admin_client, test_engine,
):
    """The critical assertion (brief): a priced receipt's total_amount comes
    back as the string "120.00"; an amount-less delivery receipt comes back
    as None — NEVER 0. Coercing it to 0 would make an invoice whose only
    evidence is a delivery note report a variance equal to the entire
    invoice to the PA approver reading this field."""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="120.00")
    inv_id = inv["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        priced = await _create_receipt(
            db, agr, user_id, amount="100.00", tax_amount="20.00",
            receipt_ref="SLIP-1", receipt_type="counter_slip",
        )
        amount_less = await _create_receipt(
            db, agr, user_id, amount=None, tax_amount=None, total_amount=None,
            receipt_ref="DN-1", receipt_type="delivery",
        )

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts", json={
        "receipt_ids": [str(priced.id), str(amount_less.id)],
    })
    assert res.status_code == 200, res.text

    res = await admin_client.get(f"{INV_URL}/{inv_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["claimed_receipts"] is not None
    assert {r["id"] for r in body["claimed_receipts"]} == {str(priced.id), str(amount_less.id)}

    amounts = {r["receipt_ref"]: r["total_amount"] for r in body["claimed_receipts"]}
    assert amounts["SLIP-1"] == "120.00"
    assert amounts["DN-1"] is None, (
        "送货单没有金额,必须原样返回 None —— 折成 0 会让前端把它算进汇总")

    types = {r["receipt_ref"]: r["receipt_type"] for r in body["claimed_receipts"]}
    assert types["SLIP-1"] == "counter_slip"
    assert types["DN-1"] == "delivery"


async def test_claimed_receipts_absent_returns_none_or_empty_consistently(
    admin_client, test_engine,
):
    """An invoice with no claimed receipts: whether the field lands as `None`
    or `[]`, it must be the SAME shape on every read of the same invoice —
    not one on the list response and the other on detail."""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="100.00")
    inv_id = inv["id"]

    res = await admin_client.get(f"{INV_URL}/{inv_id}")
    assert res.status_code == 200, res.text
    detail_value = res.json()["claimed_receipts"]
    assert detail_value in (None, []), detail_value

    res = await admin_client.get(INV_URL, params={"vendor_id": vendor_id})
    assert res.status_code == 200, res.text
    [list_item] = [it for it in res.json()["items"] if it["id"] == inv_id]
    assert list_item["claimed_receipts"] == detail_value, (
        "list and detail must not drift apart on the same invoice")


async def test_claimed_receipts_match_between_list_and_detail_when_present(
    admin_client, test_engine,
):
    """Same invoice, same claimed receipt, read through both routes: the
    field must not drift apart between `GET /invoices` and
    `GET /invoices/{id}` — that's the whole point of attaching it at every
    call site `_attach_match_assignees` already uses."""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)
    inv = await _matched_invoice(admin_client, vendor_id, agr, amount="100.00")
    inv_id = inv["id"]

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        receipt = await _create_receipt(db, agr, user_id, amount="100.00", receipt_ref="R-1")

    res = await admin_client.put(f"{INV_URL}/{inv_id}/receipts",
                                  json={"receipt_ids": [str(receipt.id)]})
    assert res.status_code == 200, res.text

    detail = (await admin_client.get(f"{INV_URL}/{inv_id}")).json()
    listing = (await admin_client.get(INV_URL, params={"vendor_id": vendor_id})).json()
    [list_item] = [it for it in listing["items"] if it["id"] == inv_id]

    assert detail["claimed_receipts"] == list_item["claimed_receipts"]
    assert detail["claimed_receipts"][0]["id"] == str(receipt.id)
    assert detail["claimed_receipts"][0]["total_amount"] == "100.00"
    assert detail["claimed_receipts"][0]["vendor_name"] is None


async def test_claimed_receipts_batch_load_is_a_single_query_across_a_page(
    admin_client, test_engine,
):
    """★ N+1 guard: serialise a page of 5 house-account invoices, each with a
    claimed receipt, and count how many SELECTs hit `agreement_receipts`
    while the list request is served. Must be exactly 1 — one query per
    invoice would defeat the entire point of batching in
    crud/invoice.py::attach_claimed_receipts."""
    vendor_id, _name, user_id = await seed_vendor_and_user(test_engine)
    agr = await _make_active_agreement(test_engine, vendor_id, user_id)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    for i in range(5):
        inv = await _matched_invoice(admin_client, vendor_id, agr, amount="100.00")
        async with factory() as db:
            receipt = await _create_receipt(
                db, agr, user_id, amount="100.00", receipt_ref=f"BATCH-{i}")
        res = await admin_client.put(f"{INV_URL}/{inv['id']}/receipts",
                                      json={"receipt_ids": [str(receipt.id)]})
        assert res.status_code == 200, res.text

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", _capture)
    try:
        res = await admin_client.get(
            INV_URL, params={"vendor_id": vendor_id, "page_size": 200})
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _capture)

    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 5

    receipt_queries = [
        s for s in statements
        if "agreement_receipts" in s and s.strip().upper().startswith("SELECT")
    ]
    assert len(receipt_queries) == 1, (
        f"expected exactly 1 SELECT against agreement_receipts for the whole "
        f"page, got {len(receipt_queries)}:\n" + "\n---\n".join(receipt_queries)
    )
