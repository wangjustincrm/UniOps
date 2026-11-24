"""An invoice's Activity (user 2026-11-24): its own audit rows, newest first, in people's words — the shared timeline."""
from datetime import date

import pytest

from app.platform.authorization import PermissionDenied
from app.schemas.invoices import InvoiceEdit
from app.services.invoice_views import InvoiceService
from app.services.record_files import TIMELINES
from tests.test_attachments import READER
from tests.test_invoicing import OP, confirm, p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture


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
    newest, *_, first = out.timeline
    assert (newest.action, newest.reason, newest.user_name) == ("update", "invoice edited: agreed price", OP.name)
    assert newest.after["Delivery no."] == "POD-7" and set(newest.after) == {"Delivery no.", "Subtotal", "Total"}
    assert set(newest.before) == set(newest.after) and first.action == "create"
    names = {k for e in out.timeline for k in (*(e.before or {}), *(e.after or {}))}
    assert not [n for n in names if n.endswith("_sql") or "_" in n], names  # no field names, nothing computed from another record
    assert not {"id", "order_id"} & names and "Invoice" in first.after
    rows = await p6.q("SELECT count(*) n FROM sales_audit WHERE entity = 'sales_invoices' AND entity_id = :i", i=str(id))
    assert len(out.timeline) == rows[0]["n"]  # every row shown, none rewritten
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
