"""Found by the code review of 2026-11-06: one bad unit stops nothing else, a delivered send is
recorded, a failed record is seen, invoice quantities are on the record."""
from datetime import date


from app.models.records import OutboxRecord
from app.platform.actors import SYSTEM_ACTOR
from app.platform.outbox import AcceptedThenFailed, Outbox, OutboxKind, OutboxWorker
from app.services.invoice_views import InvoiceService
from app.schemas.invoices import InvoiceEdit
from tests.test_invoicing import confirm, p4_edi, p6, shipped_850  # noqa: F401 — fixtures
from tests.test_orders import OP, env  # noqa: F401 — a fixture


async def test_an_order_that_cannot_advance_stops_neither_the_others_nor_the_report(p6, monkeypatch):  # noqa: F811
    bad = await shipped_850(p6, "PO-BAD")
    good = await shipped_850(p6, "PO-GOOD")
    real = p6.inv._advance

    async def poison(uow, order_id):
        if order_id == bad.id:
            raise RuntimeError("poison")
        return await real(uow, order_id)
    monkeypatch.setattr(p6.inv, "_advance", poison)
    r = await p6.inv.advance()
    assert r.failed == 1 and r.waiting == 1
    [row] = await p6.q("SELECT invoice_status FROM sales_orders WHERE id = :i", i=good.id)
    assert row["invoice_status"] == "Waiting for Delivery Confirmation"


async def _row(t, kind):
    async with t.factory(SYSTEM_ACTOR) as u:
        Outbox(OutboxRecord).add(u.session, kind, {"x": 1})
        await u.commit()


async def test_a_send_accepted_then_failed_is_recorded_as_done(p6):  # noqa: F811
    seen = []

    async def effect(payload, ctx):
        raise AcceptedThenFailed("the server hung up after the message")

    async def done(row, exc, uow):
        seen.append(row.kind)
    await _row(p6, "review_accepted")
    w = OutboxWorker(Outbox(OutboxRecord), {"review_accepted": OutboxKind(effect, on_done=done)}, p6.factory)
    assert (await w.run_once()).done == 1 and seen == ["review_accepted"]


async def test_a_result_that_cannot_be_recorded_is_reported_not_only_logged(p6):  # noqa: F811
    reported = []

    async def effect(payload, ctx):
        return None

    async def broken(row, exc, uow):
        raise RuntimeError("could not write")

    async def report(row, exc, uow):
        reported.append(str(exc))
    await _row(p6, "review_hook")
    w = OutboxWorker(Outbox(OutboxRecord), {"review_hook": OutboxKind(effect, on_done=broken)}, p6.factory, on_hook_failed=report)
    assert (await w.run_once()).done == 1 and reported == ["could not write"]
    [row] = await p6.q("SELECT last_error FROM sales_outbox WHERE kind = 'review_hook'")
    assert "result not recorded" in row["last_error"]


async def test_an_invoice_quantity_change_is_on_the_record(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-Q")
    await p6.inv.advance()
    inv = await confirm(p6, o.id, when=date(2026, 11, 5))
    [line] = await p6.q("SELECT id FROM sales_invoice_lines")
    async with p6.factory(OP) as u:
        await p6.inv.edit(u, inv.id, version=None, invoice_date=None, lines=[{"id": line["id"], "qty": 9}], reason="short count")
        await u.commit()
    [log] = await p6.q("SELECT message FROM sales_log WHERE message LIKE '%quantity%'")
    assert "quantity 10 → 9" in log["message"]
