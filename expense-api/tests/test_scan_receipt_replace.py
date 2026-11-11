"""OA ATT-OA-005: rescanning on a line replaces that line's earlier scan (ISS-0197).

A receipt scanned on a line was uploaded as a bare claim attachment, with no
line and no origin, and an edit deletes and re-inserts every line with new ids
— so a rescan on a later edit could not find the earlier scan, and the claim
ended up with the same receipt twice (EXP-20261103-0010 / -0013).

Now an attachment records its line and whether it was scanned; an edit that
sends the lines' ids back keeps them; a scan on a line replaces that line's
earlier scan (never a manual attachment); and a scan whose line an edit
removed goes with it.
"""
import uuid

import pytest

from tests.conftest import _client, _make_token

ACCOUNT = {"budget_account_id": str(uuid.uuid4()), "budget_account_code": "CRM00201",
           "budget_account_name": "Training & Development"}


def _line(n, desc, line_id=..., net="20.00", tax="2.60", total="22.60"):
    li = {"line_number": n, "expense_date": "2026-11-03", "description": desc,
          "net_amount": net, "tax_amount": tax, "total_amount": total, **ACCOUNT}
    if line_id is not ...:
        li["id"] = line_id
    return li


@pytest.fixture
def file_server(monkeypatch):
    """Stand-in file-api: records what was stored and deleted."""
    from app.api.v1 import expense_attachments, expenses

    state = {"deleted": []}

    async def _upload(data, **kw):
        return uuid.uuid4()

    async def _delete(key, token):
        state["deleted"].append(str(key))

    monkeypatch.setattr(expense_attachments, "upload_to_file_server", _upload)
    monkeypatch.setattr(expense_attachments, "delete_from_file_server", _delete)
    monkeypatch.setattr(expenses, "delete_from_file_server", _delete)
    return state


async def _upload_file(c, claim_id, name, line_id=None, source=None):
    data = {}
    if line_id:
        data["line_item_id"] = line_id
    if source:
        data["source"] = source
    r = await c.post(f"/api/v1/expenses/{claim_id}/attachments",
                     files={"file": (name, b"receipt-bytes", "image/png")}, data=data)
    assert r.status_code == 201, r.text
    return r.json()


async def _new_claim(c):
    r = await c.post("/api/v1/expenses", json={
        "claim_type": "EXP", "submission_date": "2026-11-03", "currency": "CAD",
        "line_items": [_line(1, "Office paper", net="40.00", tax="5.20", total="45.20"),
                       _line(2, "Lunch set")],
        "trip_items": []})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], [li["id"] for li in sorted(body["line_items"], key=lambda x: x["line_number"])]


async def _attachments(c, claim_id):
    r = await c.get(f"/api/v1/expenses/{claim_id}/attachments")
    assert r.status_code == 200, r.text
    return r.json()


async def test_rescan_on_a_line_after_an_edit_replaces_the_earlier_scan(db_session, file_server):
    async with _client(_make_token("requester", user_id=str(uuid.uuid4()))) as c:
        claim_id, (l1, l2) = await _new_claim(c)
        first = await _upload_file(c, claim_id, "lunch.png", l2, "scan")
        assert first["line_item_id"] == l2 and first["source"] == "scan"

        # Edit and save the lines back with their ids: they keep them.
        r = await c.patch(f"/api/v1/expenses/{claim_id}", json={"line_items": [
            _line(1, "Office paper", l1, net="40.00", tax="5.20", total="45.20"),
            _line(2, "Lunch set", l2)]})
        assert r.status_code == 200, r.text
        assert {li["id"] for li in r.json()["line_items"]} == {l1, l2}

        # Scan the same receipt again on line 2: one copy, the new one.
        second = await _upload_file(c, claim_id, "lunch.png", l2, "scan")
        atts = await _attachments(c, claim_id)
    assert [a["id"] for a in atts] == [second["id"]]
    assert file_server["deleted"] == [first["file_id"]]


async def test_rescan_never_replaces_a_manual_file_or_another_lines_scan(db_session, file_server):
    async with _client(_make_token("requester", user_id=str(uuid.uuid4()))) as c:
        claim_id, (l1, l2) = await _new_claim(c)
        manual = await _upload_file(c, claim_id, "invoice.pdf", l2, "manual")
        other = await _upload_file(c, claim_id, "paper.png", l1, "scan")
        claim_page = await _upload_file(c, claim_id, "notes.pdf")   # Upload file on the claim page
        scan = await _upload_file(c, claim_id, "lunch.png", l2, "scan")
        atts = await _attachments(c, claim_id)
    assert {a["id"] for a in atts} == {manual["id"], other["id"], claim_page["id"], scan["id"]}
    assert claim_page["source"] == "manual" and claim_page["line_item_id"] is None
    assert file_server["deleted"] == []


async def test_removing_a_line_on_edit_drops_its_scan_but_keeps_manual_files(db_session, file_server):
    async with _client(_make_token("requester", user_id=str(uuid.uuid4()))) as c:
        claim_id, (l1, l2) = await _new_claim(c)
        scan = await _upload_file(c, claim_id, "lunch.png", l2, "scan")
        manual = await _upload_file(c, claim_id, "invoice.pdf", l2, "manual")
        kept = await _upload_file(c, claim_id, "paper.png", l1, "scan")

        r = await c.patch(f"/api/v1/expenses/{claim_id}", json={"line_items": [
            _line(1, "Office paper", l1, net="40.00", tax="5.20", total="45.20")]})
        assert r.status_code == 200, r.text
        atts = await _attachments(c, claim_id)
    assert {a["id"] for a in atts} == {manual["id"], kept["id"]}
    assert file_server["deleted"] == [scan["file_id"]]
    assert {a["id"] for a in r.json()["attachments"]} == {manual["id"], kept["id"]}


async def test_an_edit_from_a_client_that_sends_no_line_ids_removes_nothing(db_session, file_server):
    """A form that predates line ids makes every line look new; reading that as
    'every line was removed' would delete every scanned receipt."""
    async with _client(_make_token("requester", user_id=str(uuid.uuid4()))) as c:
        claim_id, (l1, l2) = await _new_claim(c)
        scan = await _upload_file(c, claim_id, "lunch.png", l2, "scan")
        r = await c.patch(f"/api/v1/expenses/{claim_id}", json={"line_items": [
            _line(1, "Office paper", net="40.00", tax="5.20", total="45.20"),
            _line(2, "Lunch set")]})
        assert r.status_code == 200, r.text
        atts = await _attachments(c, claim_id)
    assert [a["id"] for a in atts] == [scan["id"]]
    assert file_server["deleted"] == []


async def test_a_line_id_from_another_claim_is_refused_and_not_reused(db_session, file_server):
    async with _client(_make_token("requester", user_id=str(uuid.uuid4()))) as c:
        claim_a, (a1, _) = await _new_claim(c)
        claim_b, (b1, b2) = await _new_claim(c)
        r = await c.post(f"/api/v1/expenses/{claim_b}/attachments",
                         files={"file": ("x.png", b"x", "image/png")},
                         data={"line_item_id": a1, "source": "scan"})
        assert r.status_code == 422, r.text
        # Sending another claim's line id on an edit gets a fresh id.
        r = await c.patch(f"/api/v1/expenses/{claim_b}", json={"line_items": [
            _line(1, "Office paper", a1, net="40.00", tax="5.20", total="45.20"),
            _line(2, "Lunch set", b2)]})
        assert r.status_code == 200, r.text
    ids = {li["id"] for li in r.json()["line_items"]}
    assert a1 not in ids and b2 in ids and len(ids) == 2


async def test_someone_who_may_not_delete_attachments_adds_but_never_replaces(db_session, file_server):
    """Delete rights are unchanged (ATT-OA-002: claimant, filer or System Admin).
    The upload itself is not limited (OI-13, a known defect outside this fix),
    so a scan by anyone else is added next to the earlier one, not over it."""
    owner = str(uuid.uuid4())
    async with _client(_make_token("requester", user_id=owner)) as c:
        claim_id, (_, l2) = await _new_claim(c)
        first = await _upload_file(c, claim_id, "lunch.png", l2, "scan")
    async with _client(_make_token("requester")) as other:
        second = await _upload_file(other, claim_id, "lunch.png", l2, "scan")
    async with _client(_make_token("requester", user_id=owner)) as c:
        atts = await _attachments(c, claim_id)
    assert {a["id"] for a in atts} == {first["id"], second["id"]}
    assert file_server["deleted"] == []
