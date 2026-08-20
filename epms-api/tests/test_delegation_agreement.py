"""I2 (final whole-branch review, feature/approval-delegation, 2026-08-19):
delegated agreement approvals must actually be reachable.

`approve_agr` is pinned to a single user (see approval-api's engine.py
~L665 for dept_manager), and access_scope.py's `own_agr_tasks` block is what
matches doc_type="agr" tasks — `_task_chain_agreement_ids` only walks PA
tasks, so it never covers a task pinned directly on the agreement. Before
this fix `own_agr_tasks` matched `Task.assigned_user_id == user_id` only,
never widened with the `task_user_ids` set (viewer + active delegators) the
rest of build_scope already threads through. A delegate covering a
dept_manager's approve_agr task passed the task-assignment check and saw the
task in their inbox, but the agreement itself 404s on both list and detail —
they could not open what they were being asked to approve.
"""
import uuid
from datetime import date, timedelta

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.delegation import local_today
from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.agreement import PurchaseAgreement
from app.models.task import Task
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

AGR_URL = "/api/v1/agreements"


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _make_user(test_engine, role: str) -> str:
    factory = _factory(test_engine)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:6]}@deleg-agr-test.com",
                password="TestPass1!",
                full_name=f"Deleg {role}",
                role=role,
            ),
        )
        await db.commit()
        return str(user.id)


def _authed_client(user_id: str, role: str) -> AsyncClient:
    token = create_access_token(user_id, role)
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _seed_delegation(test_engine, *, delegator_id, delegate_id, start_date, end_date):
    factory = _factory(test_engine)
    async with factory() as db:
        await db.execute(sa.text(
            "INSERT INTO approval_delegations "
            "(id, delegator_user_id, delegate_user_id, start_date, end_date, created_by) "
            "VALUES (:id, :delegator, :delegate, :start, :end, :delegator)"),
            {
                "id": str(uuid.uuid4()), "delegator": delegator_id, "delegate": delegate_id,
                "start": start_date, "end": end_date,
            })
        await db.commit()


async def _make_agreement(test_engine, *, created_by: str) -> str:
    """A vendor + an agreement created by a THIRD party, unrelated to both
    the delegator and the delegate — nothing but the task/delegation should
    make it visible to the delegate."""
    factory = _factory(test_engine)
    vid = uuid.uuid4()
    aid = uuid.uuid4()
    async with factory() as db:
        # Committed separately from the agreement below: SQLAlchemy's flush
        # ordering only sorts by declared relationship() dependencies, not
        # bare FK columns, so inserting both in one flush can send the
        # agreement's INSERT before the vendor's and 23503 on vendor_id.
        db.add(Vendor(id=vid, code=f"V-{vid.hex[:6]}", name="Liftow Limited",
                       category="supplier", contact_name="N", contact_email="n@x.com"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseAgreement(
            id=aid, number=f"AGR-DELEG-{aid.hex[:8]}", title="Delegation Agreement Test",
            agreement_type="house_account", vendor_id=vid, vendor_name="Liftow Limited",
            valid_from=date(2026, 1, 1), valid_to=date(2027, 1, 1),
            status="in_review", created_by=uuid.UUID(created_by),
        ))
        await db.commit()
    return str(aid)


async def _make_agr_task(test_engine, *, agreement_id, assigned_user_id, assigned_role="dept_manager"):
    factory = _factory(test_engine)
    async with factory() as db:
        db.add(Task(
            id=uuid.uuid4(), type="approve_agr", document_type="agr",
            document_id=uuid.UUID(agreement_id), document_number="AGR-DELEG-TEST",
            assigned_role=assigned_role, assigned_user_id=uuid.UUID(assigned_user_id),
            title="Approve", is_completed=False,
        ))
        await db.commit()


async def _grant_agreement_read(test_engine, role: str) -> None:
    """epms.agreement.read is a phase-2 permission conftest's default matrix
    grants to NO role (see test_agreement_receipt_api.py's block comment) —
    it must be seeded per-test, same pattern as that file and
    test_gr_create_authz.py's _grant_gr_receive."""
    factory = _factory(test_engine)
    async with factory() as db:
        await db.execute(sa.text(
            "INSERT INTO permission_defs(key,module,label,sort) "
            "VALUES ('epms.agreement.read','epms','View Agreements',104) "
            "ON CONFLICT (key) DO NOTHING"))
        await db.execute(sa.text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES (:role,'epms.agreement.read') ON CONFLICT DO NOTHING"),
            {"role": role})
        await db.commit()


@pytest.mark.asyncio
async def test_delegate_can_open_delegators_agreement_inside_window_only(test_engine):
    await _grant_agreement_read(test_engine, "requester")
    creator_id = await _make_user(test_engine, "requester")
    delegator_id = await _make_user(test_engine, "dept_manager")
    delegate_id = await _make_user(test_engine, "requester")
    delegate_outside_id = await _make_user(test_engine, "requester")

    agreement_id = await _make_agreement(test_engine, created_by=creator_id)

    # Baseline: without a task or delegation, an unrelated requester cannot
    # open this agreement (not its creator/owner, no department overlap).
    async with _authed_client(delegate_id, "requester") as c:
        resp = await c.get(f"{AGR_URL}/{agreement_id}")
        assert resp.status_code == 404, (
            "test setup invalid: delegate could already see the agreement "
            "before any task/delegation existed"
        )

    # approve_agr is pinned to a single user (dept_manager step) — mirrors
    # approval-api engine.py's routing for this doc type.
    await _make_agr_task(test_engine, agreement_id=agreement_id, assigned_user_id=delegator_id)

    today = local_today()
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_id,
        start_date=today, end_date=today,
    )
    # A second delegate whose window has already expired.
    await _seed_delegation(
        test_engine, delegator_id=delegator_id, delegate_id=delegate_outside_id,
        start_date=today - timedelta(days=10), end_date=today - timedelta(days=1),
    )

    async with _authed_client(delegate_id, "requester") as c:
        resp = await c.get(f"{AGR_URL}/{agreement_id}")
        assert resp.status_code == 200, (
            f"delegate could not open the delegator's agreement inside the "
            f"delegation window (status={resp.status_code}) — approve_agr "
            f"was reachable in the inbox but the document itself was not"
        )

    async with _authed_client(delegate_outside_id, "requester") as c:
        resp = await c.get(f"{AGR_URL}/{agreement_id}")
        assert resp.status_code == 404, (
            f"delegate (expired window) could open the delegator's agreement "
            f"(status={resp.status_code})"
        )
