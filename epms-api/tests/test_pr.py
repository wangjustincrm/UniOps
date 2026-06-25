"""Purchase Request endpoint tests."""
import pytest

URL = "/api/v1/pr"

_LINE = {
    "description": "Deep Groove Bearing 6205",
    "material_id": "PART-0001",
    "qty": "2",
    "unit": "EA",
    "unit_price": "12.50",
}


def _payload(**overrides):
    base = {
        "title": "Monthly Spare Parts Replenishment",
        "type": 3,
        "currency": "CAD",
        "notes": "Urgent restock",
        "line_items": [_LINE],
    }
    base.update(overrides)
    return base


async def _create(client, **overrides):
    resp = await client.post(URL, json=_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Basic CRUD ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_pr(admin_client):
    pr = await _create(admin_client)
    assert pr["number"].startswith("PR-")
    assert pr["status"] == "draft"
    assert len(pr["line_items"]) == 1
    assert float(pr["amount"]) == 25.00
    assert pr["approval_step_idx"] == 0


@pytest.mark.asyncio
async def test_list_prs(admin_client):
    await _create(admin_client)
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_get_pr(admin_client):
    pr = await _create(admin_client)
    resp = await admin_client.get(f"{URL}/{pr['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == pr["id"]


@pytest.mark.asyncio
async def test_get_pr_not_found(admin_client):
    resp = await admin_client.get(f"{URL}/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_pr_draft(admin_client):
    pr = await _create(admin_client, title="Old Title")
    resp = await admin_client.patch(f"{URL}/{pr['id']}", json={"title": "New Title"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New Title"


@pytest.mark.asyncio
async def test_update_pr_line_items(admin_client):
    pr = await _create(admin_client)
    new_lines = [
        {**_LINE, "qty": "5", "description": "Updated Bearing"},
        {**_LINE, "description": "Oil Seal 40x60", "qty": "3", "unit_price": "8.00"},
    ]
    resp = await admin_client.patch(f"{URL}/{pr['id']}", json={"line_items": new_lines})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["line_items"]) == 2
    assert float(data["amount"]) == pytest.approx(5 * 12.50 + 3 * 8.00)


@pytest.mark.asyncio
async def test_create_pr_unauthenticated(client):
    resp = await client.post(URL, json=_payload())
    assert resp.status_code == 403


# ── Workflow ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_pr(admin_client):
    pr = await _create(admin_client)
    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "submitted"
    assert data["submitted_at"] is not None


@pytest.mark.asyncio
async def test_cannot_edit_submitted_pr(admin_client):
    pr = await _create(admin_client)
    await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})
    resp = await admin_client.patch(f"{URL}/{pr['id']}", json={"title": "Hacked"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_full_approval_flow(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]

    # submit
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    assert r.json()["status"] == "submitted"

    # approve step 0 → in_review (step 1)
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve", "comment": "LGTM"})
    assert r.json()["status"] == "in_review"
    assert r.json()["approval_step_idx"] == 1

    # approve step 1 → in_review (step 2)
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "in_review"
    assert r.json()["approval_step_idx"] == 2

    # approve step 2 → approved
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve"})
    assert r.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_return_pr(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "return", "comment": "Needs clarification"})
    assert r.json()["status"] == "returned"
    assert r.json()["approval_step_idx"] == 0


@pytest.mark.asyncio
async def test_resubmit_after_return(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "return"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    assert r.json()["status"] == "submitted"


@pytest.mark.asyncio
async def test_reject_pr(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    r = await admin_client.post(f"{URL}/{pid}/action", json={"action": "reject"})
    assert r.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_cancel_draft_pr(admin_client):
    pr = await _create(admin_client)
    r = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "cancel"})
    assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_invalid_action(admin_client):
    pr = await _create(admin_client)
    r = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "fly"})
    assert r.status_code == 409


# ── Approval events ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approval_events(admin_client):
    pr = await _create(admin_client)
    pid = pr["id"]
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "submit"})
    await admin_client.post(f"{URL}/{pid}/action", json={"action": "approve", "comment": "OK"})

    resp = await admin_client.get(f"{URL}/{pid}/events")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 2
    actions = [e["action"] for e in events]
    assert "submit" in actions
    assert "approve" in actions
