"""An invoice's Activity (user 2026-11-24): its own audit rows, newest first, in people's words — the shared timeline."""
from datetime import date
from decimal import Decimal

import pytest

from app.models.orders import Order
from app.platform.authorization import PermissionDenied
from app.schemas.invoices import InvoiceEdit
from app.services.invoice_views import InvoiceService
from app.services.record_files import TIMELINES
from tests.test_attachments import READER
from tests.test_invoicing import OP, confirm, p6, send, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_returned import set_returned


async def _edited(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-ACT")
    await p6.inv.advance()
    inv = await confirm(p6, o.id, when=date(2026, 11, 5))
    async with p6.factory(OP) as u:
        d = await InvoiceService(u).detail(inv.id)
        out = await InvoiceService(u).edit(p6.inv, inv.id, InvoiceEdit(reason="agreed price", delivery_number="POD-7", lines=[
            {"id": d.lines[0].id, "unit_price": "1.00"}]), d.version)
        await u.commit()
    return o, inv.id, out


async def test_it_says_who_changed_what_and_why_in_words_newest_first(p6):  # noqa: F811
    _, id, out = await _edited(p6)
    newest = next(e for e in out.timeline if e.entity == "sales_invoices")
    first = [e for e in out.timeline if e.entity == "sales_invoices"][-1]
    assert (newest.action, newest.reason, newest.user_name) == ("update", "invoice edited: agreed price", OP.name)
    assert newest.after["Delivery no."] == "POD-7" and set(newest.after) == {"Delivery no.", "Subtotal", "Total"}
    assert set(newest.before) == set(newest.after) and first.action == "create"
    names = {k for e in out.timeline for k in (*(e.before or {}), *(e.after or {}))}
    assert not [n for n in names if n.endswith("_sql") or "_" in n], names  # no field names, nothing computed from another record
    assert not {"id", "order_id"} & names and "Invoice" in first.after
    rows = await p6.q("SELECT count(*) n FROM sales_audit WHERE entity = 'sales_invoices' AND entity_id = :i", i=str(id))
    assert len([e for e in out.timeline if e.entity == "sales_invoices"]) == rows[0]["n"]  # every row shown, none rewritten
    kept = await p6.q("SELECT after FROM sales_audit WHERE entity = 'sales_invoices' AND entity_id = :i AND action = 'create'", i=str(id))
    assert "order_ref_sql" in kept[0]["after"]  # the audit itself is as it was


async def test_every_column_of_the_invoice_has_its_word():
    k = TIMELINES["invoice"]
    kept = {c.key for c in k.model.__table__.columns} - k.internal - {
        "created_at", "created_by", "created_by_name", "updated_at", "updated_by", "updated_by_name", "version"}  # never in an audit row
    assert kept == set(k.words)


async def test_reading_is_enough_and_without_it_nothing_is_read(p6, monkeypatch):  # noqa: F811
    _, id, _ = await _edited(p6)

    def only(*keys):
        async def check(session, actor, key):  # noqa: ARG001
            return key in keys
        return check
    monkeypatch.setattr(p6.factory, "permissions", only("sales.read"))
    async with p6.factory(READER) as u:
        assert (await InvoiceService(u).detail(id)).timeline
        assert (await InvoiceService(u).meta())["capabilities"]["attachments"] is None  # an Activity, not attachments
    monkeypatch.setattr(p6.factory, "permissions", only("sales.invoice.manage"))
    async with p6.factory(READER) as u:
        with pytest.raises(PermissionDenied):
            await InvoiceService(u).detail(id)


async def _lines(p6, id):  # noqa: F811
    async with p6.factory(OP) as u:
        return [e for e in (await InvoiceService(u).detail(id)).timeline if e.entity == "sales_invoice_lines"]


async def test_an_edited_line_says_which_line_what_changed_and_why(p6):  # noqa: F811
    _, id, out = await _edited(p6)
    (e,) = [e for e in out.timeline if e.entity == "sales_invoice_lines"]
    assert (e.action, e.about, e.user_name, e.reason) == ("update", "Line 1 · CF0092", OP.name, "invoice edited: agreed price")
    assert e.before == {"Unit price": "50.00000", "Amount": "500.00"} and {k: Decimal(v) for k, v in e.after.items()} == {
        "Unit price": Decimal(1), "Amount": Decimal(10)}  # 10 cases; the quantity did not change, so it is not said


async def test_a_quantity_edited_is_there_and_an_edit_of_nothing_is_not(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-ACT2")
    await p6.inv.advance()
    inv = await confirm(p6, o.id, when=date(2026, 11, 5))
    assert await _lines(p6, inv.id) == []  # the lines' making is the invoice's own row
    for qty in (9, 9):
        async with p6.factory(OP) as u:
            d = await InvoiceService(u).detail(inv.id)
            await InvoiceService(u).edit(p6.inv, inv.id, InvoiceEdit(lines=[{"id": d.lines[0].id, "qty": qty}]), d.version)
            await u.commit()
    (e,) = await _lines(p6, inv.id)
    assert (e.before["Quantity"], e.after["Quantity"], e.after["Amount"]) == (10, 9, "450.00") and e.reason == "invoice edited"


async def test_a_return_saved_on_a_draft_is_there_on_its_line_with_its_reason(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-ACT3")
    await p6.inv.advance()
    inv = await confirm(p6, o.id, when=date(2026, 11, 5))
    await set_returned(p6, o.id, (await p6.q("SELECT id FROM sales_order_lines WHERE order_id = :i", i=o.id))[0]["id"], 12, "2 cases refused")
    (e,) = await _lines(p6, inv.id)
    assert (e.about, e.reason, e.before["Quantity"], e.after["Quantity"]) == ("Line 1 · CF0092", "returned: 2 cases refused", 10, 8)


async def test_the_status_is_there_from_the_order_s_audit_and_nothing_else_of_the_order(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-ACT4")
    await p6.inv.advance()
    inv = await confirm(p6, o.id, when=date(2026, 11, 5))
    async with p6.factory(OP) as u:  # another field of the order, alone and beside the status
        row = await u.session.get(Order, o.id)
        row.notes = "a note of the order"
        u.set_reason("order noted")
        await u.commit()
    await send(p6, inv.id)
    await p6.worker.run_once()
    async with p6.factory(OP) as u:
        timeline = (await InvoiceService(u).detail(inv.id)).timeline
    status = [e for e in reversed(timeline) if e.entity == "sales_orders"]
    assert [(e.before["Status"], e.after["Status"]) for e in status] == [
        ("Pending", "Waiting for Delivery Confirmation"), ("Waiting for Delivery Confirmation", "Ready"), ("Ready", "Queued"), ("Queued", "Sent")]
    assert all(set(e.before) == set(e.after) == {"Status"} for e in status)  # no other field of the order
    assert [e.reason for e in status][1:3] == ["delivery confirmed", "invoice send"] and status[2].user_name == OP.name
    assert "order noted" not in [e.reason for e in timeline]
    kept = await p6.q("SELECT count(*) n FROM sales_audit WHERE entity = 'sales_orders' AND action = 'update' AND entity_id = :i "
                      "AND after ? 'invoice_status'", i=str(o.id))
    assert len(status) == kept[0]["n"]
    docs = [e for e in timeline if "Document" in (e.after or {}) and e.after["Document"]]
    name = (await p6.q("SELECT d.remote_name FROM sales_documents d JOIN sales_invoices i ON i.document_id = d.id WHERE i.id = :i", i=inv.id))[0]
    assert [e.after["Document"] for e in docs] == [name["remote_name"]] and docs[0].before == {"Document": None}  # the file, no internal id


async def test_another_invoice_s_lines_and_status_stay_out(p6):  # noqa: F811
    _, first, _ = await _edited(p6)
    o = await shipped_850(p6, "PO-ACT5")
    await p6.inv.advance()
    other = await confirm(p6, o.id, when=date(2026, 11, 5))
    assert await _lines(p6, other.id) == [] and len(await _lines(p6, first)) == 1
    async with p6.factory(OP) as u:
        mine = (await InvoiceService(u).detail(other.id)).timeline
    ids = {r["entity_id"] for r in await p6.q("SELECT entity_id FROM sales_audit WHERE entity = 'sales_orders' AND after ? 'invoice_status' "
                                             "AND action = 'update' AND at = ANY(:t)", t=[e.at for e in mine if e.entity == "sales_orders"])}
    assert ids == {str(o.id)}


async def test_an_order_s_and_a_transfer_s_timeline_stay_as_they_were():
    for kind in ("order", "transfer"):
        k = TIMELINES[kind]
        assert (k.words, k.line_words, k.files, k.related) == (None, None, frozenset(), ())
