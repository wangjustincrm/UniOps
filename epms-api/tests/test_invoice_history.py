"""Invoice History keeps every step (ISS-0170).

The History tab used to be derived from the invoice's current matched_* and
exception_* fields, so it showed only the latest match, never the assignment,
the AP review (and its note) or an unmatch (and its reason), and an unmatch
wiped the earlier match off it. Each step is now recorded when it happens and
read back by GET /invoices/{id}/history.
"""
import pytest

from tests.test_invoice_assign import (
    INV_URL, _client_for_user, _make_invoice, _make_po, _make_user, _make_vendor,
)
from tests.test_invoice_review import _assigned_variance_match


def _alloc(inv, po, amount):
    return {"allocations": [{
        "invoice_line_id": inv["line_items"][0]["id"], "po_id": po["id"],
        "po_line_id": po["line_items"][0]["id"],
        "allocated_amount": amount, "allocated_tax": "0.00"}]}


async def _history(client, invoice_id):
    r = await client.get(f"{INV_URL}/{invoice_id}/history")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_history_keeps_assign_review_unmatch_and_rematch(admin_client):
    """The tester's sequence on INV-2026-0403, step for step."""
    inv, assignee = await _assigned_variance_match(
        admin_client, code="VND-HIST-01", number="HIST-001")
    po_number = inv["po_number"]
    # AP rejects with the mandatory note.
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review",
                                json={"action": "reject", "note": "测试退回,请重新匹配"})
    assert r.status_code == 200, r.text
    # The assignee matches again (their Re-match task admits them); AP confirms with a note.
    po = (await admin_client.get(f"/api/v1/po/{inv['po_id']}")).json()
    async with await _client_for_user(assignee) as c:
        r = await c.post(f"{INV_URL}/{inv['id']}/match", json=_alloc(inv, po, "900.00"))
        assert r.status_code == 200, r.text
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match-review",
                                json={"action": "approve", "note": "确认匹配(测试备注)"})
    assert r.status_code == 200, r.text
    # Unmatch with the mandatory reason, then match again.
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/unmatch-po",
                                json={"reason": "误匹配,撤销"})
    assert r.status_code == 200, r.text
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json=_alloc(inv, po, "900.00"))
    assert r.status_code == 200, r.text

    h = await _history(admin_client, inv["id"])
    assert [e["action"] for e in h] == [
        "assign_match", "match", "review_reject", "match", "review_approve",
        "unmatch", "match",
    ]
    assign, first_match, reject, _, approve, unmatch, last = h
    assert assign["after"]["assignee_name"] == "Assignee Test"
    assert first_match["actor_id"] == str(assignee)
    assert first_match["after"]["po_number"] == po_number
    assert first_match["after"]["status"] == "match_review"
    assert reject["after"]["note"] == "测试退回,请重新匹配"
    assert reject["before"]["po_number"] == po_number     # what was rejected
    assert approve["after"]["note"] == "确认匹配(测试备注)"
    assert unmatch["after"]["reason"] == "误匹配,撤销"
    assert unmatch["before"]["po_number"] == po_number
    assert last["after"]["status"] == "matched"
    assert all(e["actor_name"] for e in h)


@pytest.mark.asyncio
async def test_history_records_decline_and_exception_resolution(admin_client):
    v = await _make_vendor(admin_client, "VND-HIST-02")
    po = await _make_po(admin_client, v["id"],
                        [{"description": "A", "qty": "1", "unit": "EA", "unit_price": "1000.00"}])
    inv = await _make_invoice(admin_client, v["id"], number="HIST-002", amount="1200.00",
                              lines=[{"description": "L", "quantity": "1",
                                      "unit_price": "1200.00", "line_total": "1200.00"}])
    assignee = await _make_user()
    await admin_client.post(f"{INV_URL}/{inv['id']}/assign-match", json={"user_id": str(assignee)})
    async with await _client_for_user(assignee) as c:
        r = await c.post(f"{INV_URL}/{inv['id']}/decline-match", json={"note": "Not my vendor"})
        assert r.status_code == 200, r.text
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json=_alloc(inv, po, "1200.00"))
    assert r.json()["status"] == "exception"
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/exception",
                                json={"resolution": "accepted", "note": "Price rise agreed"})
    assert r.status_code == 200, r.text

    h = await _history(admin_client, inv["id"])
    assert [e["action"] for e in h] == [
        "assign_match", "decline_match", "match", "resolve_exception"]
    assert h[1]["after"]["note"] == "Not my vendor"
    assert h[2]["after"]["status"] == "exception"
    assert h[3]["after"] == {"resolution": "accepted", "note": "Price rise agreed",
                             "status": "matched"}


@pytest.mark.asyncio
async def test_history_follows_the_invoice_read_gate(admin_client):
    v = await _make_vendor(admin_client, "VND-HIST-03")
    inv = await _make_invoice(admin_client, v["id"], number="HIST-003")
    assert await _history(admin_client, inv["id"]) == []      # admitted, nothing yet
    outsider = await _make_user()
    async with await _client_for_user(outsider) as c:
        r = await c.get(f"{INV_URL}/{inv['id']}/history")
    assert r.status_code == 404
