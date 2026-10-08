"""Type 5 needs a Fixed Asset ID and type 6 a Project No. (2026-09-14).

Both were in the state the service completion date used to be in: the Create PR
form rendered them with a required asterisk, the zod rule was `.optional()`, and
nothing server-side checked. The fixed asset id had it worse — it was never in
the submit payload and had no column, so every value typed into that box was
discarded. PRD §2 has listed both as requirements since the types were defined.

Each denial here is paired with an admission: "should be rejected" is satisfied
just as well by rejecting everything, so a gate is only proved by also showing
what it lets through.
"""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.cost_center import CostCenter
from app.models.department import Department
from app.services.doc_preflight import field_checks_for

URL = "/api/v1/pr"
VENDOR_URL = "/api/v1/vendors"

_LINE = {"description": "Filler pump", "qty": "1", "unit": "EA", "unit_price": "1200.00"}


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
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        dept = Department(code=f"D-{uuid.uuid4().hex[:6]}", name="Asset Gate Dept")
        db.add(dept)
        await db.flush()
        cc = CostCenter(code=f"CC-{uuid.uuid4().hex[:6]}", name="Asset Gate CC",
                        department_id=dept.id)
        db.add(cc)
        await db.commit()
        return str(cc.id)


async def _submittable(client, test_engine, pr_type: int, **extra) -> dict:
    """A PR of `pr_type` that clears every gate except the one under test."""
    body = {
        "title": "Homogeniser replacement",
        "type": pr_type,
        "currency": "CAD",
        "vendor_id": await _make_vendor(client),
        "cost_center_id": await _make_cost_center(test_engine),
        "budget_code": "6100-01",
        "line_items": [_LINE],
    }
    body.update(extra)
    resp = await client.post(URL, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def delegations(monkeypatch):
    """Stub the approval-api hop and record what reached it.

    An empty list proves the gate fired; a recorded call proves it let the
    action through. Without this, a passing gate and a 401 from the local
    approval-api look the same.
    """
    calls: list[tuple[str, str, str]] = []

    async def _fake(doc_type, doc_id, action, comment, token):  # noqa: ANN001
        calls.append((doc_type, doc_id, action))

    import app.api.v1.pr as pr_api
    monkeypatch.setattr(pr_api, "delegate_action", _fake)
    return calls


# ── Type 5 — Fixed Asset ID ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_type5_without_fixed_asset_id_cannot_be_submitted(
        admin_client, test_engine, delegations):
    pr = await _submittable(admin_client, test_engine, 5)

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 409, resp.text
    assert "fixed asset" in resp.json()["detail"].lower()
    assert delegations == []


@pytest.mark.asyncio
async def test_type5_with_fixed_asset_id_is_submitted(
        admin_client, test_engine, delegations):
    pr = await _submittable(admin_client, test_engine, 5, fixed_asset_id="FA-2026-0001")

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 200, resp.text
    assert [c[2] for c in delegations] == ["submit"]


@pytest.mark.asyncio
async def test_whitespace_is_not_a_fixed_asset_id(admin_client, test_engine, delegations):
    pr = await _submittable(admin_client, test_engine, 5, fixed_asset_id="   ")

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 409, resp.text
    assert delegations == []


@pytest.mark.asyncio
async def test_fixed_asset_id_survives_the_round_trip(admin_client, test_engine):
    """The bug was not that the value failed validation — it was never stored.

    Reading it back off the API is the assertion that matters: the create form
    posted a payload without the field, so this would have returned nothing no
    matter what the requester typed.
    """
    pr = await _submittable(admin_client, test_engine, 5, fixed_asset_id="FA-2026-0042")
    assert pr["fixed_asset_id"] == "FA-2026-0042"

    resp = await admin_client.get(f"{URL}/{pr['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["fixed_asset_id"] == "FA-2026-0042"


@pytest.mark.asyncio
async def test_fixed_asset_id_can_be_set_from_the_edit_form(admin_client, test_engine):
    """The edit form is where a PR blocked by the gate gets unblocked.

    The field only ever existed on the create form, so a type 5 PR that reached
    the gate with no asset id had nowhere to acquire one.
    """
    pr = await _submittable(admin_client, test_engine, 5)
    assert pr["fixed_asset_id"] is None

    resp = await admin_client.patch(f"{URL}/{pr['id']}",
                                    json={"fixed_asset_id": "FA-2026-0099"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["fixed_asset_id"] == "FA-2026-0099"


# ── Type 6 — Project No. ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_type6_without_project_code_cannot_be_submitted(
        admin_client, test_engine, delegations):
    pr = await _submittable(admin_client, test_engine, 6,
                            service_completion_date="2026-12-31")

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 409, resp.text
    assert "project" in resp.json()["detail"].lower()
    assert delegations == []


@pytest.mark.asyncio
async def test_type6_with_project_code_is_submitted(
        admin_client, test_engine, delegations):
    pr = await _submittable(admin_client, test_engine, 6,
                            service_completion_date="2026-12-31",
                            project_code="PROJ-2026-001")

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 200, resp.text
    assert [c[2] for c in delegations] == ["submit"]


# ── The gates as a whole ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_type_only_gets_its_own_identifier_gate(admin_client, test_engine,
                                                        delegations):
    """The new gates must not leak onto the types they do not belong to.

    A type 4 service PR has a completion date but no asset id and no project
    code, and must still submit — otherwise the fix for two types would have
    broken a third.
    """
    pr = await _submittable(admin_client, test_engine, 4,
                            service_completion_date="2026-12-31")

    resp = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "submit"})

    assert resp.status_code == 200, resp.text
    assert [c[2] for c in delegations] == ["submit"]


class _StubPr:
    """Just enough PR for the pure gate functions."""
    def __init__(self, pr_type: int):
        self.id = uuid.uuid4()
        self.type = pr_type
        self.vendor_id = None
        self.budget_code = None
        self.cost_center_id = None
        self.service_completion_date = None
        self.project_code = None
        self.fixed_asset_id = None


# What each type has to satisfy at submit. This is the table the Create PR form,
# PRD §2 and the assistant's answer to "what differs between the types" all have
# to agree with — assert it in one place so a change to the gates shows up as a
# failure here rather than as three descriptions quietly drifting apart.
EXPECTED_GATES = {
    1: {"vendor_required"},
    2: {"vendor_required", "budget_account_required"},
    3: {"vendor_required", "budget_account_required"},
    4: {"vendor_required", "budget_account_required", "service_completion_date_required"},
    5: {"vendor_required", "budget_account_required", "fixed_asset_id_required"},
    6: {"vendor_required", "budget_account_required", "service_completion_date_required",
        "project_code_required"},
}


@pytest.mark.parametrize("pr_type", sorted(EXPECTED_GATES))
def test_each_type_declares_exactly_its_own_gates(pr_type):
    ids = {c.id for c in field_checks_for("pr", _StubPr(pr_type))}
    assert ids == EXPECTED_GATES[pr_type]
