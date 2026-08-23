"""Submitting a budget-bearing PR requires a budget account (2026-08-21).

Type 1 carries no budget (the Create PR form hides the Budget Account block for
it); every other type must have both a cost center and a budget code before it
can leave draft. Without both, `compute_budget_check` short-circuits to
over_budget=False, so a PR with no budget code silently bypasses the whole
budget check — including a `hard_block` Budget Config.

The guard lives in pr_action next to the vendor guard: epms-api owns PR
field-level rules, approval-api only owns the state machine.
"""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.cost_center import CostCenter
from app.models.department import Department

URL = "/api/v1/pr"
VENDOR_URL = "/api/v1/vendors"

_LINE = {"description": "Pool noodle", "qty": "1", "unit": "EA", "unit_price": "34.98"}


async def _make_vendor(client) -> str:
    code = f"V{uuid.uuid4().hex[:6].upper()}"
    resp = await client.post(VENDOR_URL, json={
        "code": code, "name": f"Vendor {code}", "category": "Parts",
        "contact_name": "A", "contact_email": "a@a.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_cost_center(test_engine) -> str:
    """Seed a cost center through the ORM — departments are mdm-owned now, so
    /api/v1/departments is read-only and there is no HTTP path to create one."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        dept = Department(code=f"D-{uuid.uuid4().hex[:6]}", name="Budget Guard Dept")
        db.add(dept)
        await db.flush()
        cc = CostCenter(code=f"CC-{uuid.uuid4().hex[:6]}", name="Budget Guard CC",
                        department_id=dept.id)
        db.add(cc)
        await db.commit()
        return str(cc.id)


async def _make_pr(client, **overrides) -> dict:
    body = {
        "title": "Pool noodles for milk receiving doors",
        "type": 2,
        "currency": "CAD",
        "line_items": [_LINE],
    }
    body.update(overrides)
    resp = await client.post(URL, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def delegations(monkeypatch):
    """Stub the approval-api hop and record what reached it.

    The local approval-api rejects test JWTs (401), so guard-passes cases would
    be indistinguishable from guard-blocks without this. An empty list proves the
    guard fired; a recorded call proves it let the action through.
    """
    calls: list[tuple[str, str, str]] = []

    async def _fake(doc_type, doc_id, action, comment, token):  # noqa: ANN001
        calls.append((doc_type, doc_id, action))

    import app.api.v1.pr as pr_api
    monkeypatch.setattr(pr_api, "delegate_action", _fake)
    return calls


@pytest.mark.asyncio
async def test_submit_type2_pr_without_budget_code_is_rejected(admin_client, test_engine, delegations):
    vendor_id = await _make_vendor(admin_client)
    cc_id = await _make_cost_center(test_engine)
    pr = await _make_pr(admin_client, vendor_id=vendor_id, cost_center_id=cc_id)

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 409, resp.text
    assert "budget" in resp.json()["detail"].lower()
    assert delegations == []


@pytest.mark.asyncio
async def test_submit_type2_pr_without_cost_center_is_rejected(admin_client, delegations):
    vendor_id = await _make_vendor(admin_client)
    pr = await _make_pr(admin_client, vendor_id=vendor_id, budget_code="6100-01")

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 409, resp.text
    assert "budget" in resp.json()["detail"].lower()
    assert delegations == []


@pytest.mark.asyncio
async def test_submit_type2_pr_with_budget_account_is_allowed(admin_client, test_engine, delegations):
    vendor_id = await _make_vendor(admin_client)
    cc_id = await _make_cost_center(test_engine)
    pr = await _make_pr(
        admin_client, vendor_id=vendor_id, cost_center_id=cc_id, budget_code="6100-01",
    )

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 200, resp.text
    assert [c[2] for c in delegations] == ["submit"]


@pytest.mark.asyncio
async def test_submit_type1_pr_without_budget_code_is_allowed(admin_client, delegations):
    """Type 1 has no budget block in the UI — the guard must not touch it."""
    vendor_id = await _make_vendor(admin_client)
    pr = await _make_pr(admin_client, type=1, vendor_id=vendor_id)

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 200, resp.text
    assert [c[2] for c in delegations] == ["submit"]


@pytest.mark.asyncio
async def test_cancel_without_budget_code_is_not_blocked(admin_client, delegations):
    """Only submit is gated. Legacy PRs with no budget code must stay actionable
    (cancel / approve / return), otherwise in-flight documents get stranded."""
    vendor_id = await _make_vendor(admin_client)
    pr = await _make_pr(admin_client, vendor_id=vendor_id)

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "cancel"})

    assert resp.status_code == 200, resp.text
    assert [c[2] for c in delegations] == ["cancel"]
