"""Preflight merges both halves, and says the same thing submit says.

The gates live in two services and neither half is the answer alone. What these
tests pin down is that the merged list (a) reports every applicable gate rather
than stopping at the first, (b) omits gates that do not apply to this document
rather than claiming they passed, and (c) uses the exact wording the submit
route rejects with — the last one is what keeps the two from drifting.
"""
import uuid
from decimal import Decimal

import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.services.doc_preflight import (MSG_PR_BUDGET_REQUIRED,
                                        MSG_PR_COMPLETION_DATE_REQUIRED,
                                        MSG_PR_VENDOR_REQUIRED)

pytestmark = pytest.mark.asyncio

# 4 is a service type (SERVICE_TYPES = {4, 6}); 1 carries no budget; 2 is the
# plain goods case that needs a budget account but no completion date.
TYPE_NO_BUDGET = 1
TYPE_GOODS = 2
TYPE_SERVICE = 4


def _user_id(client) -> uuid.UUID:
    token = client.headers["Authorization"].split()[1]
    payload = jwt.decode(token, settings.JWT_SECRET_KEY,
                         algorithms=[settings.JWT_ALGORITHM])
    return uuid.UUID(payload["sub"])


async def _seed_refs(test_engine):
    """A real vendor and cost centre — both are foreign keys on purchase_requests,
    so the "everything filled in" cases need rows that actually exist."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        tag = uuid.uuid4().hex[:6]
        vendor = Vendor(code=f"V-{tag}", name="Preflight Vendor", category="Services",
                        contact_name="P", contact_email="p@t.test")
        dept = Department(code=f"D-{tag}", name="Preflight Dept")
        db.add_all([vendor, dept])
        await db.commit()
        await db.refresh(vendor)
        await db.refresh(dept)
        cc = CostCenter(code=f"CC-{tag}", name="Preflight CC", department_id=dept.id)
        db.add(cc)
        await db.commit()
        await db.refresh(cc)
        return vendor.id, cc.id


async def _seed_pr(test_engine, creator, *, pr_type=TYPE_GOODS, vendor_id=None,
                   budget_code=None, cost_center_id=None,
                   service_completion_date=None, status="draft"):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        pr = PurchaseRequest(
            number=f"PR-PF-{uuid.uuid4().hex[:6]}", title="Preflight test",
            type=pr_type, status=status, amount=Decimal("100.00"),
            created_by=creator, vendor_id=vendor_id, budget_code=budget_code,
            cost_center_id=cost_center_id,
            service_completion_date=service_completion_date,
        )
        db.add(pr)
        await db.commit()
        return pr.id


def _ids(body, *, failed_only=False):
    return [c["id"] for c in body["checks"] if not (failed_only and c["passed"])]


def _get(body, check_id):
    return next(c for c in body["checks"] if c["id"] == check_id)


# ── every applicable gate, not just the first ─────────────────────────────────


async def test_all_three_field_gates_reported_at_once(test_engine, admin_client):
    """A service PR missing everything gets told everything, once."""
    pr_id = await _seed_pr(test_engine, _user_id(admin_client), pr_type=TYPE_SERVICE)

    r = await admin_client.get(f"/api/v1/assistant/preflight/pr/{pr_id}")
    assert r.status_code == 200
    body = r.json()

    assert body["allowed"] is False
    failed = _ids(body, failed_only=True)
    assert "vendor_required" in failed
    assert "budget_account_required" in failed
    assert "service_completion_date_required" in failed


async def test_gates_that_do_not_apply_are_omitted_not_passed(test_engine, admin_client):
    """Type 1 carries no budget; a non-service PR has no completion date.

    Reporting those as `passed` would read as "we checked and it's fine", which
    is a different claim from "this rule has nothing to do with this document".
    """
    pr_id = await _seed_pr(test_engine, _user_id(admin_client), pr_type=TYPE_NO_BUDGET)

    body = (await admin_client.get(f"/api/v1/assistant/preflight/pr/{pr_id}")).json()
    ids = _ids(body)

    assert "vendor_required" in ids
    assert "budget_account_required" not in ids
    assert "service_completion_date_required" not in ids


async def test_satisfied_field_gates_report_passed(test_engine, admin_client):
    vendor_id, cc_id = await _seed_refs(test_engine)
    pr_id = await _seed_pr(
        test_engine, _user_id(admin_client), pr_type=TYPE_GOODS,
        vendor_id=vendor_id, budget_code="BC-1", cost_center_id=cc_id)

    body = (await admin_client.get(f"/api/v1/assistant/preflight/pr/{pr_id}")).json()

    assert _get(body, "vendor_required")["passed"] is True
    assert _get(body, "budget_account_required")["passed"] is True


async def test_failed_gate_carries_a_route_to_fix_it(test_engine, admin_client):
    pr_id = await _seed_pr(test_engine, _user_id(admin_client), pr_type=TYPE_GOODS)

    body = (await admin_client.get(f"/api/v1/assistant/preflight/pr/{pr_id}")).json()
    vendor = _get(body, "vendor_required")

    assert vendor["fixable_by_user"] is True
    assert vendor["fix_route"] == f"/pr/{pr_id}/edit#vendor"


# ── same wording as the submit route ──────────────────────────────────────────


@pytest.mark.parametrize("pr_type,missing,expected", [
    (TYPE_GOODS, "vendor_required", MSG_PR_VENDOR_REQUIRED),
    (TYPE_GOODS, "budget_account_required", MSG_PR_BUDGET_REQUIRED),
    (TYPE_SERVICE, "service_completion_date_required", MSG_PR_COMPLETION_DATE_REQUIRED),
])
async def test_message_is_what_submit_rejects_with(
    test_engine, admin_client, pr_type, missing, expected
):
    """Predicted wording == the 409 detail the submit route returns.

    Both read the same constant; this asserts nobody quietly forked one of them.
    """
    pr_id = await _seed_pr(test_engine, _user_id(admin_client), pr_type=pr_type)

    body = (await admin_client.get(f"/api/v1/assistant/preflight/pr/{pr_id}")).json()
    assert _get(body, missing)["message"] == expected

    submit = await admin_client.post(f"/api/v1/pr/{pr_id}/action", json={"action": "submit"})
    assert submit.status_code == 409
    # Submission short-circuits, so it reports whichever gate comes first — the
    # point is that when it reports THIS one, it says exactly this.
    assert submit.json()["detail"] in (
        MSG_PR_VENDOR_REQUIRED, MSG_PR_BUDGET_REQUIRED, MSG_PR_COMPLETION_DATE_REQUIRED)


async def test_submit_rejects_with_the_first_gate_preflight_listed(test_engine, admin_client):
    pr_id = await _seed_pr(test_engine, _user_id(admin_client), pr_type=TYPE_SERVICE)

    body = (await admin_client.get(f"/api/v1/assistant/preflight/pr/{pr_id}")).json()
    first_failed = next(c for c in body["checks"]
                        if c["layer"] == "field" and not c["passed"])

    submit = await admin_client.post(f"/api/v1/pr/{pr_id}/action", json={"action": "submit"})
    assert submit.status_code == 409
    assert submit.json()["detail"] == first_failed["message"]


# ── degradation and access ────────────────────────────────────────────────────


async def test_unreachable_engine_is_reported_not_hidden(test_engine, admin_client):
    """approval-api is not running in this suite — the honest answer is
    "I could not check", never a confident allowed=true."""
    vendor_id, cc_id = await _seed_refs(test_engine)
    pr_id = await _seed_pr(
        test_engine, _user_id(admin_client), pr_type=TYPE_GOODS,
        vendor_id=vendor_id, budget_code="BC-1", cost_center_id=cc_id)

    body = (await admin_client.get(f"/api/v1/assistant/preflight/pr/{pr_id}")).json()

    assert body["complete"] is False
    assert body["allowed"] is False, "unknown must not read as allowed"
    engine_check = _get(body, "approval_engine_reachable")
    assert engine_check["passed"] is False
    assert engine_check["fixable_by_user"] is False


async def test_engine_checks_are_merged_in(test_engine, admin_client, monkeypatch):
    async def _fake(doc_type, doc_id, action, token):
        return {"checks": [{
            "id": "approver_routing", "layer": "config", "passed": False,
            "message": "Cannot route approval: no active Department Manager…",
            "fixable_by_user": False, "owner": "admin",
        }]}

    monkeypatch.setattr(
        "app.api.v1.assistant_preflight.approval_client.get_preflight", _fake)

    vendor_id, cc_id = await _seed_refs(test_engine)
    pr_id = await _seed_pr(
        test_engine, _user_id(admin_client), pr_type=TYPE_GOODS,
        vendor_id=vendor_id, budget_code="BC-1", cost_center_id=cc_id)

    body = (await admin_client.get(f"/api/v1/assistant/preflight/pr/{pr_id}")).json()

    assert body["complete"] is True
    assert body["allowed"] is False
    routing = _get(body, "approver_routing")
    assert routing["owner"] == "admin"
    assert routing["fixable_by_user"] is False
    # Field gates sort before config ones — fix your own first.
    layers = [c["layer"] for c in body["checks"]]
    assert layers.index("field") < layers.index("config")


async def test_invisible_pr_is_a_404_not_a_denial(test_engine, admin_client,
                                                  requester_client):
    """Telling someone why a PR they cannot see is blocked would leak that it
    exists, and what state it is in."""
    pr_id = await _seed_pr(test_engine, _user_id(admin_client), pr_type=TYPE_GOODS)

    r = await requester_client.get(f"/api/v1/assistant/preflight/pr/{pr_id}")
    assert r.status_code == 404


async def test_unsupported_doc_type_and_action_are_refused(admin_client):
    some_id = uuid.uuid4()
    r = await admin_client.get(f"/api/v1/assistant/preflight/po/{some_id}")
    assert r.status_code == 400
    assert "pr" in r.json()["detail"]

    r = await admin_client.get(
        f"/api/v1/assistant/preflight/pr/{some_id}?action=approve")
    assert r.status_code == 400
