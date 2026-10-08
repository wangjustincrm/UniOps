"""A payment role held as an ADDITIONAL role must work end to end, not just in
the inbox.

The OA task list resolves `can_pay` against the user's full role union. The
expense list and the claim detail endpoint were still testing the JWT's primary
role alone. So someone whose payment_officer / finance_bp / finance_manager is
an assignment rather than their primary role got a "Record Payment" card in
their inbox, and a 403 when they opened it — the exact shape of failure the
task-list fix was meant to end (a row you can see but cannot act on), just
moved one click later.

Payment roles are assignments far more often than they are primary roles here,
which is why _user_role_codes exists at all.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.expense import ExpenseClaim
from tests.conftest import _client, _make_token

# Every role in _CAN_PAY that a real person here is likely to hold as an
# assignment rather than as their primary role.
PAY_ROLES = ["payment_officer", "finance_bp", "finance_manager", "ap_clerk"]


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _approved_claim(test_engine) -> uuid.UUID:
    cid = uuid.uuid4()
    async with _factory(test_engine)() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"EC-PAY-{cid.hex[:8]}", claim_type="EXP",
            employee_id=uuid.uuid4(), employee_name="Someone Else",
            department_name="Ops", submission_date=date(2026, 9, 11),
            status="approved", approval_step_idx=0, created_by=uuid.uuid4(),
            total_amount=Decimal("880.00"),
        ))
        await s.commit()
    return cid


async def _grant(test_engine, user_id: uuid.UUID, role_code: str):
    async with _factory(test_engine)() as db:
        await db.execute(text(
            "INSERT INTO role_defs (code, is_active) VALUES (:c, true) "
            "ON CONFLICT (code) DO NOTHING"), {"c": role_code})
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :c) "
            "ON CONFLICT DO NOTHING"), {"u": str(user_id), "c": role_code})
        await db.commit()


@pytest.mark.parametrize("pay_role", PAY_ROLES)
async def test_an_assigned_payment_role_can_reach_the_claim_it_is_offered(
    test_engine, pay_role,
):
    """Inbox card, list row and detail page must agree."""
    me = uuid.uuid4()
    await _grant(test_engine, me, pay_role)
    claim = await _approved_claim(test_engine)

    async with _client(_make_token("requester", str(me))) as c:
        inbox = await c.get("/api/v1/tasks")
        listing = await c.get("/api/v1/expenses")
        detail = await c.get(f"/api/v1/expenses/{claim}")

    card = next((i for i in inbox.json()["items"] if i["doc_id"] == str(claim)), None)
    assert card is not None and card["task_type"] == "pay_expense", (
        "precondition: the task list offers this claim for payment")

    assert str(claim) in {i["id"] for i in listing.json()["items"]}, (
        f"{pay_role} held as an additional role sees the payment card but not "
        "the list row")
    assert detail.status_code == 200, (
        f"{pay_role} held as an additional role can see the payment card but "
        f"gets {detail.status_code} opening the claim")


async def test_someone_with_no_payment_role_still_sees_nothing(test_engine):
    """The widening must not turn into "everyone sees approved claims"."""
    claim = await _approved_claim(test_engine)

    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        listing = await c.get("/api/v1/expenses")
        detail = await c.get(f"/api/v1/expenses/{claim}")

    assert str(claim) not in {i["id"] for i in listing.json()["items"]}
    assert detail.status_code == 403


async def test_a_primary_payment_role_still_works(test_engine):
    """Regression guard on the path that was already fine."""
    claim = await _approved_claim(test_engine)

    async with _client(_make_token("finance_bp", str(uuid.uuid4()))) as c:
        listing = await c.get("/api/v1/expenses")
        detail = await c.get(f"/api/v1/expenses/{claim}")

    assert str(claim) in {i["id"] for i in listing.json()["items"]}
    assert detail.status_code == 200
