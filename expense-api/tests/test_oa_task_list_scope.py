"""GET /api/v1/tasks — the OA Task Inbox is scoped by the `tasks` table.

This endpoint backs OA's pinned landing tab. It kept selecting every document
parked at a workflow step whose role the caller's JWT carried, long after
`my_actions` (the Portal inbox) and `_can_act_on_claim` (the gate behind the
Approve button) had both moved to the shared `tasks` table. Four consequences,
one per test group below:

  * no department predicate — every dept_manager saw every company claim at
    that step, requester name and amount included;
  * JWT primary role only — additional roles (which is how finance_bp and
    friends are actually granted) counted for nothing;
  * no delegation — a stand-in covering someone saw none of their work;
  * a local `_CAN_PAY` copy that had drifted and lost `payment_officer`, the
    role that executes payments — so the person responsible for paying had an
    empty Record Payment section.

Plus: TRA leaked into both the payment section and the Custom Form bucket.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.base as db_module
from app.core.delegation import local_today
from app.models.expense import ExpenseClaim
from app.models.pa import PaymentApplication
from app.models.task_mirror import TaskMirror
from tests.conftest import _client, _make_token
from tests.test_authz_scope import _restore_workflow_defs, _set_workflow_defs

TASKS = "/api/v1/tasks"


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _claim(test_engine, *, claim_type="EXP", status="in_review",
                 employee_id=None, step_idx=0) -> uuid.UUID:
    cid = uuid.uuid4()
    emp = employee_id or uuid.uuid4()
    async with _factory(test_engine)() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"EC-TL-{cid.hex[:8]}", claim_type=claim_type,
            employee_id=emp, employee_name="Another Department's Employee",
            submission_date=date(2026, 9, 10), status=status,
            approval_step_idx=step_idx, created_by=emp,
            total_amount=Decimal("0.00") if claim_type == "TRA" else Decimal("500.00"),
        ))
        await s.commit()
    return cid


async def _pa(test_engine, *, status="in_review", created_by=None) -> uuid.UUID:
    pid = uuid.uuid4()
    async with _factory(test_engine)() as s:
        s.add(PaymentApplication(
            id=pid, pa_number=f"PA-TL-{pid.hex[:6]}", title="T",
            vendor_id=uuid.uuid4(), vendor_name="V", invoice_ids=[], gr_ids=[],
            pa_type="PA-DIR", subtotal=Decimal("10"), payment_amount=Decimal("10"),
            currency="CAD", status=status, approval_step_idx=0,
            created_by=created_by or uuid.uuid4(),
        ))
        await s.commit()
    return pid


async def _task(doc_id, *, doc_type="exp", role=None, user_id=None, completed=False):
    async with db_module.AsyncSessionLocal() as db:
        db.add(TaskMirror(
            id=uuid.uuid4(), document_id=doc_id, document_type=doc_type,
            type=f"approve_{doc_type}",
            assigned_user_id=uuid.UUID(user_id) if user_id else None,
            assigned_role=role, is_completed=completed,
            created_at=datetime.now(timezone.utc),
        ))
        await db.commit()


async def _grant_role(test_engine, user_id: str, role_code: str):
    async with _factory(test_engine)() as db:
        # role_defs is identity's register of roles; a role absent from it (or
        # marked inactive there) is not held at all — see core/authz_matrix.
        await db.execute(text(
            "INSERT INTO role_defs (code, is_active) VALUES (:c, true) "
            "ON CONFLICT (code) DO NOTHING"), {"c": role_code})
        await db.execute(
            text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r)"),
            {"u": user_id, "r": role_code})
        await db.commit()


async def _delegate(test_engine, *, delegator_id, delegate_id, days=1):
    """An open delegation window. The delegate must also exist and be active —
    core/delegation.py's _ACTIVE joins `users` to check exactly that."""
    today = local_today()
    async with _factory(test_engine)() as db:
        await db.execute(text(
            "INSERT INTO users (id, full_name, email, role, is_active) "
            "VALUES (:id, 'Stand-in', :email, 'requester', true) "
            "ON CONFLICT (id) DO NOTHING"),
            {"id": delegate_id, "email": f"{delegate_id}@example.test"})
        await db.execute(text(
            "INSERT INTO approval_delegations "
            "(id, delegator_user_id, delegate_user_id, start_date, end_date, created_by) "
            "VALUES (:id, :dr, :de, :s, :e, :dr)"),
            {"id": uuid.uuid4(), "dr": delegator_id, "de": delegate_id,
             "s": today - timedelta(days=days), "e": today + timedelta(days=days)})
        await db.commit()


async def _inbox(role: str, user_id: str) -> list[dict]:
    async with _client(_make_token(role, user_id)) as c:
        r = await c.get(TASKS)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _ids(items) -> set[str]:
    return {i["doc_id"] for i in items}


# ── Department scope ──────────────────────────────────────────────────────────

async def test_a_manager_does_not_see_a_claim_pinned_to_another_manager(test_engine):
    """The leak this endpoint shipped with: role match, no assignment check.

    The workflow_defs row matters — it is what the old code matched on. With
    dept_manager at step 0 and the claim sitting at step 0, the old endpoint
    handed this claim (requester name and amount) to EVERY department manager
    in the company; approval-api had already pinned the task to exactly one.
    """
    previous = await _set_workflow_defs(test_engine, {"exp": [
        {"id": "s0", "role": "dept_manager", "label": "Department Manager"},
        {"id": "s1", "role": "finance_bp", "label": "Finance BP"},
    ]})
    try:
        theirs = str(uuid.uuid4())
        claim = await _claim(test_engine, step_idx=0)
        await _task(claim, role=None, user_id=theirs)   # pinned to the OTHER manager

        me = str(uuid.uuid4())
        assert str(claim) not in _ids(await _inbox("dept_manager", me))
    finally:
        await _restore_workflow_defs(test_engine, previous)


async def test_the_assigned_manager_does_see_it(test_engine):
    me = str(uuid.uuid4())
    claim = await _claim(test_engine)
    await _task(claim, role=None, user_id=me)

    items = await _inbox("dept_manager", me)
    assert str(claim) in _ids(items)
    assert next(i for i in items if i["doc_id"] == str(claim))["task_type"] == "approve_expense"


async def test_a_role_pool_task_still_reaches_every_holder(test_engine):
    """Broadcast tasks (assigned_user_id NULL) stay visible by role."""
    claim = await _claim(test_engine)
    await _task(claim, role="finance_bp")

    assert str(claim) in _ids(await _inbox("finance_bp", str(uuid.uuid4())))


async def test_a_completed_task_drops_out_of_the_inbox(test_engine):
    me = str(uuid.uuid4())
    claim = await _claim(test_engine)
    await _task(claim, role=None, user_id=me, completed=True)

    assert str(claim) not in _ids(await _inbox("dept_manager", me))


# ── Role union ────────────────────────────────────────────────────────────────

async def test_an_additional_role_counts(test_engine):
    """finance_bp is granted through user_roles, not through the JWT."""
    me = str(uuid.uuid4())
    await _grant_role(test_engine, me, "finance_bp")
    claim = await _claim(test_engine)
    await _task(claim, role="finance_bp")

    assert str(claim) in _ids(await _inbox("requester", me))


async def test_gm_or_opm_is_expanded(test_engine):
    """approval-api broadcasts singleton-post steps under a synthetic role name."""
    me = str(uuid.uuid4())
    await _grant_role(test_engine, me, "opm")
    claim = await _claim(test_engine)
    await _task(claim, role="gm_or_opm")

    assert str(claim) in _ids(await _inbox("requester", me))


# ── Delegation ────────────────────────────────────────────────────────────────

async def test_a_stand_in_sees_the_delegators_pinned_task(test_engine):
    delegator, delegate = str(uuid.uuid4()), str(uuid.uuid4())
    await _delegate(test_engine, delegator_id=delegator, delegate_id=delegate)
    claim = await _claim(test_engine)
    await _task(claim, role=None, user_id=delegator)

    assert str(claim) in _ids(await _inbox("requester", delegate))


# ── Payment section ───────────────────────────────────────────────────────────

async def test_payment_officer_sees_claims_waiting_to_be_paid(test_engine):
    """The drifted local _CAN_PAY copy omitted the role that does the paying."""
    claim = await _claim(test_engine, status="approved")

    items = await _inbox("payment_officer", str(uuid.uuid4()))
    row = next((i for i in items if i["doc_id"] == str(claim)), None)
    assert row is not None
    assert row["task_type"] == "pay_expense"


async def test_payment_applications_do_not_appear_at_all(test_engine):
    """OA's Direct PA is retired, so this list has no PA section — not for
    payment, not for approval. EPMS's PAs live in EPMS's own task list and in
    the Portal home inbox; OA no longer has a /pa route to send a card to.
    See DIRECT_PA_RETIRED in api/v1/pa.py."""
    approved = await _pa(test_engine, status="approved")
    in_review = await _pa(test_engine, status="in_review")

    ids = _ids(await _inbox("payment_officer", str(uuid.uuid4())))
    assert str(approved) not in ids
    assert str(in_review) not in ids


async def test_an_approved_travel_application_is_not_a_payment_task(test_engine):
    """A TRA's total is 0.00 and it never enters the payment path."""
    tra = await _claim(test_engine, claim_type="TRA", status="approved")

    assert str(tra) not in _ids(await _inbox("payment_officer", str(uuid.uuid4())))


# ── Document typing ───────────────────────────────────────────────────────────

async def test_a_travel_application_is_typed_tra_not_cfm(test_engine):
    """TRA fell through `.get(ct, "cfm")` and showed up as a Custom Form,
    deep-linking to /expenses/{id} instead of /travel/{id}."""
    me = str(uuid.uuid4())
    tra = await _claim(test_engine, claim_type="TRA")
    await _task(tra, doc_type="tra", role=None, user_id=me)

    row = next(i for i in await _inbox("dept_manager", me) if i["doc_id"] == str(tra))
    assert row["doc_type"] == "tra"


# ── Own submissions ───────────────────────────────────────────────────────────

async def test_my_own_in_flight_and_returned_claims_still_show(test_engine):
    me = str(uuid.uuid4())
    inflight = await _claim(test_engine, employee_id=uuid.UUID(me))
    returned = await _claim(test_engine, employee_id=uuid.UUID(me), status="returned")

    items = await _inbox("requester", me)
    by_id = {i["doc_id"]: i for i in items}
    assert by_id[str(inflight)]["task_type"] == "submitted_expense"
    assert by_id[str(returned)]["task_type"] == "revise_expense"


# ── Only OA's own work belongs in OA's inbox ──────────────────────────────────
#
# `tasks` is shared by every service in the platform. On production data the
# approve-tasks in it are overwhelmingly EPMS's: ~1,378 pa, 534 po, 353 pr, 27
# agr against 4 exp. Two things follow, and both were wrong here:
#   * EPMS Payment Applications were listed in OA's task inbox outright (the
#     endpoint queried payment_applications directly);
#   * even after that section went, the approve-task lookup was unscoped, so
#     every one of an approver's EPMS tasks was read into Python and then
#     discarded against a table of expense claims.


async def _foreign_task(doc_type: str, *, user_id: str) -> uuid.UUID:
    """An approve task for another service's document, pinned to this user."""
    doc_id = uuid.uuid4()
    async with db_module.AsyncSessionLocal() as db:
        db.add(TaskMirror(
            id=uuid.uuid4(), document_id=doc_id, document_type=doc_type,
            type=f"approve_{doc_type}", assigned_user_id=uuid.UUID(user_id),
            assigned_role=None, is_completed=False,
            created_at=datetime.now(timezone.utc),
        ))
        await db.commit()
    return doc_id


@pytest.mark.parametrize("doc_type", ["pr", "po", "pa", "pa_dir", "agr",
                                      "vms_visit", "budget_plan", "posign"])
async def test_another_services_task_never_appears(test_engine, doc_type):
    me = str(uuid.uuid4())
    foreign = await _foreign_task(doc_type, user_id=me)
    # Something of OA's own, so the inbox is not trivially empty.
    mine = await _claim(test_engine, employee_id=uuid.UUID(me))

    ids = _ids(await _inbox("dept_manager", me))

    assert str(mine) in ids, "precondition: the caller's own OA work is listed"
    assert str(foreign) not in ids, f"an EPMS/{doc_type} task leaked into OA's inbox"


@pytest.mark.parametrize("doc_type", ["pr", "po", "pa", "agr"])
async def test_another_services_task_is_not_even_read(test_engine, doc_type):
    """Scoped at the query, not filtered afterwards.

    Filtering in Python would give the same list and still drag every EPMS task
    an approver holds across the wire — the thing that makes this worth doing.
    """
    from app.api.v1.expenses import OA_CLAIM_DOC_TYPES, approvable_document_ids

    me = uuid.uuid4()
    foreign = await _foreign_task(doc_type, user_id=str(me))

    async with db_module.AsyncSessionLocal() as db:
        ids = await approvable_document_ids(
            db, me, "dept_manager", doc_types=OA_CLAIM_DOC_TYPES)

    assert foreign not in ids


async def test_a_custom_form_task_is_still_in_scope(test_engine):
    """OA's custom forms are typed `cfm_<code>`, one per form — matched by
    prefix, so a new form does not have to be added to a list anywhere."""
    from app.api.v1.expenses import OA_CLAIM_DOC_TYPES, approvable_document_ids

    me = uuid.uuid4()
    cfm = await _foreign_task("cfm_travel", user_id=str(me))

    async with db_module.AsyncSessionLocal() as db:
        ids = await approvable_document_ids(
            db, me, "requester", doc_types=OA_CLAIM_DOC_TYPES)

    assert cfm in ids
