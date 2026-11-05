"""Over-receipt is allowed, but only with a reason (ISS-0098/0169)."""
import uuid as _uuid

import pytest
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_gr import GR_URL, PO_URL, _gr_payload, _make_issued_po

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet_notification_email(monkeypatch):
    async def _noop(to, subject, html, **kwargs):
        return None

    monkeypatch.setattr("app.services.email.send_email", _noop)


async def _po_line_id(client, po_id):
    r = await client.get(f"{PO_URL}/{po_id}")
    r.raise_for_status()
    return r.json()["line_items"][0]["id"]   # Hydraulic Pump × 2


def _line(po_line_id, qty, **kw):
    line = {
        "po_line_id": po_line_id, "description": "Hydraulic Pump",
        "qty_ordered": "2", "qty_received": str(qty), "unit": "EA",
        "unit_price": "350.00", "condition": "good",
    }
    line.update(kw)
    return line


async def _post_gr(client, po_id, lines, **kw):
    return await client.post(GR_URL, json=_gr_payload(po_id, line_items=lines, **kw))


async def _ack_and_collect(client, gr_id, **collect_kw):
    r = await client.post(f"{GR_URL}/{gr_id}/action", json={"action": "acknowledge"})
    assert r.status_code == 200, r.text
    return await client.post(f"{GR_URL}/{gr_id}/action", json={"action": "collect", **collect_kw})


async def _received_qty(client, po_id):
    r = await client.get(f"{PO_URL}/{po_id}")
    return float(r.json()["line_items"][0]["received_qty"])


async def test_over_receipt_needs_a_reason_on_create(admin_client, test_engine):
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-OVR-01")
    pl = await _po_line_id(admin_client, po["id"])

    r = await _post_gr(admin_client, po["id"], [_line(pl, 5)])
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("Over-receipt")

    r = await _post_gr(admin_client, po["id"], [_line(pl, 5)],
                       over_receipt_reason="Vendor shipped a full case")
    assert r.status_code == 201, r.text
    assert r.json()["over_receipt_reason"] == "Vendor shipped a full case"


async def test_receipt_within_open_qty_needs_no_reason_and_stores_none(admin_client, test_engine):
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-OVR-02")
    pl = await _po_line_id(admin_client, po["id"])

    r = await _post_gr(admin_client, po["id"], [_line(pl, 2)], over_receipt_reason="not needed")
    assert r.status_code == 201, r.text
    assert r.json()["over_receipt_reason"] is None


async def test_pending_grs_count_against_the_open_quantity(admin_client, test_engine):
    """ISS-0169: GRs not yet collected used to be invisible, so each could take the full remainder."""
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-OVR-03")
    pl = await _po_line_id(admin_client, po["id"])

    r = await _post_gr(admin_client, po["id"], [_line(pl, 2)])
    assert r.status_code == 201, r.text          # pending_ack, nothing posted yet

    r = await _post_gr(admin_client, po["id"], [_line(pl, 1)])
    assert r.status_code == 422, r.text

    r = await admin_client.get(f"{GR_URL}/open-quantities", params={"po_id": po["id"]})
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert (float(row["ordered"]), float(row["received"]), float(row["pending"]), float(row["open"])) \
        == (2.0, 0.0, 2.0, 0.0)


async def test_collect_asks_for_a_reason_when_it_would_exceed_the_order(admin_client, test_engine):
    """A GR created without a reason (e.g. before the check existed) is asked
    for one when its quantity would push the PO line past the order."""
    from app.models.gr import GoodsReceipt

    po, _ = await _make_issued_po(admin_client, test_engine, "VND-OVR-04")
    pl = await _po_line_id(admin_client, po["id"])

    first = (await _post_gr(admin_client, po["id"], [_line(pl, 2)])).json()
    second = await _post_gr(admin_client, po["id"], [_line(pl, 1)], over_receipt_reason="x")
    assert second.status_code == 201, second.text
    second = second.json()
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:   # as if it predates the check
        row = (await db.execute(_select(GoodsReceipt).where(
            GoodsReceipt.id == _uuid.UUID(second["id"])))).scalar_one()
        row.over_receipt_reason = None
        await db.commit()

    r = await _ack_and_collect(admin_client, first["id"])
    assert r.status_code == 200, r.text
    assert await _received_qty(admin_client, po["id"]) == 2.0

    r = await _ack_and_collect(admin_client, second["id"])
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("Over-receipt")
    assert await _received_qty(admin_client, po["id"]) == 2.0   # nothing posted

    r = await admin_client.post(f"{GR_URL}/{second['id']}/action", json={
        "action": "collect", "over_receipt_reason": "Extra unit kept as spare"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "collected"
    assert r.json()["over_receipt_reason"] == "Extra unit kept as spare"
    assert await _received_qty(admin_client, po["id"]) == 3.0


async def test_double_submit_creates_one_gr(admin_client, test_engine, monkeypatch):
    """ISS-0152: a double-click sent two creates 1 ms apart. Both read the open
    quantity before either was saved, so both passed and the line was received
    twice. One GR per PO is created at a time; the second needs a reason."""
    import asyncio

    import app.crud.gr as gr_crud

    # Hold each request between its open-quantity read and its save, so the two
    # overlap the way the double-click did instead of by luck of scheduling.
    original = gr_crud._over_receipt_on_create

    async def _slow(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(0.3)
        return result

    monkeypatch.setattr(gr_crud, "_over_receipt_on_create", _slow)
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-OVR-05")
    pl = await _po_line_id(admin_client, po["id"])

    first, second = await asyncio.gather(
        _post_gr(admin_client, po["id"], [_line(pl, 2)]),
        _post_gr(admin_client, po["id"], [_line(pl, 2)]),
    )

    assert sorted([first.status_code, second.status_code]) == [201, 422], (first.text, second.text)
    r = await admin_client.get(GR_URL, params={"po_id": po["id"]})
    assert r.json()["total"] == 1
