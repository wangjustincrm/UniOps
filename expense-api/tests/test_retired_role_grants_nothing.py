"""A role an admin has deactivated must stop granting anything in OA.

`role_defs` is identity's register of roles and carries the is_active flag the
Portal admin toggles. `core/authz_matrix.user_role_codes` has always honoured
it — but expenses.py, pa.py and invoice_list.py each carried their own copy of
the same lookup that read `user_roles` bare, with no join to role_defs. So the
same user, in the same request, held a retired role for the purposes of
approval visibility and did not hold it for the purposes of the permission
matrix. All three now delegate to the one helper.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.base as db_module
from app.models.expense import ExpenseClaim
from app.models.task_mirror import TaskMirror
from tests.conftest import _client, _make_token


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _register_role(test_engine, code: str, *, active: bool):
    async with _factory(test_engine)() as db:
        await db.execute(text(
            "INSERT INTO role_defs (code, is_active) VALUES (:c, :a) "
            "ON CONFLICT (code) DO UPDATE SET is_active = :a"), {"c": code, "a": active})
        await db.commit()


async def _hold_role(test_engine, user_id: str, code: str):
    async with _factory(test_engine)() as db:
        await db.execute(text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :c) "
            "ON CONFLICT DO NOTHING"), {"u": user_id, "c": code})
        await db.commit()


async def _claim_awaiting(test_engine, role: str) -> uuid.UUID:
    """A claim with an open broadcast approve task for `role`."""
    cid, emp = uuid.uuid4(), uuid.uuid4()
    async with _factory(test_engine)() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"EC-RR-{cid.hex[:8]}", claim_type="EXP",
            employee_id=emp, employee_name="Emp", submission_date=date(2026, 9, 10),
            status="in_review", approval_step_idx=0, created_by=emp,
            total_amount=Decimal("500.00"),
        ))
        await s.commit()
    async with db_module.AsyncSessionLocal() as db:
        db.add(TaskMirror(
            id=uuid.uuid4(), document_id=cid, document_type="exp", type="approve_exp",
            assigned_user_id=None, assigned_role=role, is_completed=False,
            created_at=datetime.now(timezone.utc),
        ))
        await db.commit()
    return cid


async def _sees(user_id: str, claim_id: uuid.UUID) -> bool:
    async with _client(_make_token("requester", user_id)) as c:
        r = await c.get("/api/v1/tasks")
    assert r.status_code == 200, r.text
    return str(claim_id) in {i["doc_id"] for i in r.json()["items"]}


async def test_an_active_additional_role_grants_the_task(test_engine):
    """Control: the same setup, role left active."""
    me = str(uuid.uuid4())
    await _register_role(test_engine, "acting_finance_bp", active=True)
    await _hold_role(test_engine, me, "acting_finance_bp")
    claim = await _claim_awaiting(test_engine, "acting_finance_bp")

    assert await _sees(me, claim) is True


async def test_a_deactivated_additional_role_grants_nothing(test_engine):
    me = str(uuid.uuid4())
    await _register_role(test_engine, "retired_reviewer", active=True)
    await _hold_role(test_engine, me, "retired_reviewer")
    claim = await _claim_awaiting(test_engine, "retired_reviewer")
    assert await _sees(me, claim) is True, "precondition: visible while active"

    await _register_role(test_engine, "retired_reviewer", active=False)

    assert await _sees(me, claim) is False


async def test_deactivating_a_role_also_withdraws_can_pay(test_engine):
    """The same union feeds the payment permission on a claim's detail page."""
    from app.api.v1.expenses import _user_role_codes

    me = uuid.uuid4()
    await _register_role(test_engine, "retired_payer", active=True)
    await _hold_role(test_engine, str(me), "retired_payer")

    async with db_module.AsyncSessionLocal() as db:
        assert "retired_payer" in await _user_role_codes(db, me, "requester")

    await _register_role(test_engine, "retired_payer", active=False)

    async with db_module.AsyncSessionLocal() as db:
        codes = await _user_role_codes(db, me, "requester")
    assert "retired_payer" not in codes
    assert "requester" in codes, "the JWT primary role is not filtered by role_defs"
