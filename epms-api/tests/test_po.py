"""Purchase Order endpoint tests."""
import pytest

URL = "/api/v1/po"
VENDOR_URL = "/api/v1/vendors"
PR_URL = "/api/v1/pr"

_LINE = {
    "description": "Hydraulic Filter HF-200",
    "qty": "4",
    "unit": "EA",
    "unit_price": "45.00",
}


async def _make_vendor(client, code="VND-PO-01"):
    resp = await client.post(VENDOR_URL, json={
        "code": code, "name": "PO Vendor Corp", "category": "Services",
        "contact_name": "Bob", "contact_email": "bob@vendor.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _po_payload(vendor_id, **overrides):
    base = {
        "title": "Monthly Filter Order",
        "type": 2,
        "vendor_id": vendor_id,
        "currency": "CAD",
        "tax_rate": "0.13",
        "line_items": [_LINE],
    }
    base.update(overrides)
    return base


async def _create_po(client, vendor_id, **overrides):
    resp = await client.post(URL, json=_po_payload(vendor_id, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Basic CRUD ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-CREATE-01")
    po = await _create_po(admin_client, v["id"])
    assert po["number"].startswith("PO-")
    assert po["status"] == "draft"
    assert len(po["line_items"]) == 1
    subtotal = 4 * 45.0
    assert float(po["subtotal"]) == pytest.approx(subtotal)
    assert float(po["tax_amount"]) == pytest.approx(subtotal * 0.13)
    assert float(po["total"]) == pytest.approx(subtotal * 1.13)


@pytest.mark.asyncio
async def test_list_pos(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-LIST-01")
    await _create_po(admin_client, v["id"])
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_get_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-GET-01")
    po = await _create_po(admin_client, v["id"])
    resp = await admin_client.get(f"{URL}/{po['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == po["id"]


@pytest.mark.asyncio
async def test_update_po_lines(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-UPD-01")
    po = await _create_po(admin_client, v["id"])
    new_lines = [
        {**_LINE, "qty": "10", "unit_price": "50.00"},
    ]
    resp = await admin_client.patch(f"{URL}/{po['id']}", json={"line_items": new_lines})
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["subtotal"]) == pytest.approx(500.0)
    assert float(data["total"]) == pytest.approx(500.0 * 1.13)


@pytest.mark.asyncio
async def test_create_po_invalid_vendor(admin_client):
    resp = await admin_client.post(URL, json=_po_payload(
        "00000000-0000-0000-0000-000000000000"
    ))
    assert resp.status_code == 404


# ── Workflow ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-SUB-01")
    po = await _create_po(admin_client, v["id"])
    resp = await admin_client.post(f"{URL}/{po['id']}/action", json={"action": "submit"})
    assert resp.json()["status"] == "submitted"


@pytest.mark.asyncio
async def test_full_po_approval(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-APPR-01")
    po = await _create_po(admin_client, v["id"])
    pid = po["id"]

    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})

    # step 0 → in_review
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "in_review"
    assert r.json()["approval_step_idx"] == 1

    # step 1 → approved
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_issue_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-ISSUE-01")
    po = await _create_po(admin_client, v["id"])
    pid = po["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "issue"})
    assert r.json()["status"] == "issued"


@pytest.mark.asyncio
async def test_return_po(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-RET-01")
    po = await _create_po(admin_client, v["id"])
    pid = po["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "return"})
    assert r.json()["status"] == "returned"


@pytest.mark.asyncio
async def test_po_events(admin_client):
    v = await _make_vendor(admin_client, code="VND-PO-EVT-01")
    po = await _create_po(admin_client, v["id"])
    pid = po["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})

    resp = await admin_client.get(f"{URL}/{pid}/events")
    assert resp.status_code == 200
    assert any(e["action"] == "submit" for e in resp.json())


# ── PR → PO link ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_po_from_pr(admin_client):
    # Create a PR first
    pr_resp = await admin_client.post(PR_URL, json={
        "title": "Linked PR for PO test",
        "type": 2,
        "line_items": [_LINE],
    })
    pr = pr_resp.json()

    v = await _make_vendor(admin_client, code="VND-PO-PR-01")
    po = await _create_po(admin_client, v["id"], pr_id=pr["id"])
    assert po["pr_id"] == pr["id"]
    assert po["pr_number"] == pr["number"]

    # PR should now have po_id set
    pr_resp2 = await admin_client.get(f"{PR_URL}/{pr['id']}")
    assert pr_resp2.json()["po_id"] == po["id"]
    assert pr_resp2.json()["po_number"] == po["number"]


# ── Task inbox ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tasks_created_on_pr_submit(admin_client):
    pr_resp = await admin_client.post(PR_URL, json={
        "title": "Task creation test PR",
        "type": 1,
        "line_items": [_LINE],
    })
    pr = pr_resp.json()
    await admin_client.post(f"{PR_URL}/{pr['id']}/action", json={"action": "submit"})

    resp = await admin_client.get("/api/v1/tasks", params={"include_completed": True})
    assert resp.status_code == 200
    tasks = resp.json()
    pr_tasks = [t for t in tasks if t["document_id"] == pr["id"]]
    assert any(t["type"] == "approve_pr" for t in pr_tasks)
