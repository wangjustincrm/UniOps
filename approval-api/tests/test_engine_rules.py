"""The engine's rules as stated to people (app/crud/engine_rules.py) — each one
held to the behaviour it describes.

Two kinds of test here. The first checks the statements themselves: every rule
names tests that exist, the placeholders are filled from the engine's own
constants and settings. The second adds the behaviours no other test pinned
yet: no same-approver skip at submission or through a delegation, a System
Admin's approval not cascading, an empty post waiting rather than being
skipped, a recorded backup being powerless, a delegate returning.
"""
import ast
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from jose import jwt as jose_jwt

from app.core.config import settings
from app.crud.engine import (CROSS_DEPT_PA_CONDITIONS, RECALLABLE_STATUSES, _DOC_META,
                             _should_skip_step, execute_action)
from app.crud.engine_rules import ENGINE_RULES, engine_rules
from app.db.base import get_db
from app.main import app
from app.models.config import CompanyConfig
from app.models.delegation import ApprovalDelegation
from app.models.pa import PaymentApplication
from app.models.routing import ApprovalBackup, ApprovalSetting, CROSS_DEPT_GM_OR_OPM, DeptRouting
from app.models.task import Task
from app.models.user import User

_TESTS = Path(__file__).resolve().parent


# ── The statements ───────────────────────────────────────────────────────────

def _defined_tests(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")}


def test_every_rule_is_pinned_by_tests_that_exist():
    """A rule with no test behind it is a description, and descriptions drift."""
    ids = [r["id"] for r in ENGINE_RULES]
    assert len(ids) == len(set(ids))
    for rule in ENGINE_RULES:
        assert rule["area"] in ("approval_engine", "delegation"), rule["id"]
        assert rule["pinned_by"], rule["id"]
        for ref in rule["pinned_by"]:
            file, _, name = ref.partition("::")
            path = _TESTS.parent / file
            assert path.exists(), f"{rule['id']}: {file} does not exist"
            assert name in _defined_tests(path), f"{rule['id']}: no {name} in {file}"


@pytest.mark.asyncio
async def test_served_rules_have_every_placeholder_filled(engine_db_session):
    served = await engine_rules(engine_db_session)
    assert [r["id"] for r in served] == [r["id"] for r in ENGINE_RULES]
    for r in served:
        assert "{" not in r["answer"] and "}" not in r["answer"], r["id"]


@pytest.mark.asyncio
async def test_cancel_statuses_are_the_engines(engine_db_session):
    """who_can_cancel lists _DOC_META's own tuples, and the engine refuses the
    rest — an in_review PR included (the Withdraw button is shown there)."""
    served = {r["id"]: r["answer"] for r in await engine_rules(engine_db_session)}
    text = served["who_can_cancel"]
    assert "a purchase request: draft, returned or submitted" in text
    assert "a payment application: draft or returned" in text
    assert _DOC_META["pr"]["valid_cancel"] == ("draft", "returned", "submitted")
    assert "in_review" not in _DOC_META["pr"]["valid_cancel"]
    assert "submitted or in_review" in served["recall_returns_to_draft"]
    assert RECALLABLE_STATUSES == ("submitted", "in_review")


@pytest.mark.asyncio
async def test_cross_department_post_is_read_from_the_setting(engine_db_session):
    db = engine_db_session
    before = {r["id"]: r["answer"] for r in await engine_rules(db)}
    assert "goes to the GM for every such payment" in before["cross_department_payment"]
    db.add(ApprovalSetting(key=CROSS_DEPT_GM_OR_OPM, value="opm"))
    await db.flush()
    after = {r["id"]: r["answer"] for r in await engine_rules(db)}
    assert "goes to the OPM for every such payment" in after["cross_department_payment"]


def test_pa_department_steps_are_annotated_with_the_cross_department_skip():
    """The read-out's condition for a PA's department steps must be the skip
    _should_skip_step applies to a cross-department payment."""
    class _Doc:
        quality_approver_id = None

    for role in CROSS_DEPT_PA_CONDITIONS:
        skip, reason = _should_skip_step(role, "pa", _Doc(), uuid.uuid4(), uuid.uuid4(),
                                         True, True, cross_dept_pa=True)
        assert skip and "several departments" in reason
        assert CROSS_DEPT_PA_CONDITIONS[role].startswith("Only runs when")
    assert set(CROSS_DEPT_PA_CONDITIONS) == {"dept_manager", "director"}


@pytest.fixture
def _api(engine_db_session):
    async def _get_db_override():
        yield engine_db_session
    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_rules_and_pa_conditions_are_served(engine_db_session, _api):
    db = engine_db_session
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "gm_or_opm", "role": "gm_or_opm", "label": "GM/OPM"}]}, budget_admin_config={}))
    await db.flush()
    token = jose_jwt.encode({"sub": str(uuid.uuid4()), "role": "requester"},
                            settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        rules = (await c.get("/approval/v1/workflows/rules")).json()["rules"]
        pa = (await c.get("/approval/v1/workflows/pa")).json()["steps"]
        pr = (await c.get("/approval/v1/workflows/pr")).json()["steps"]
    assert {r["id"] for r in rules} == {r["id"] for r in ENGINE_RULES}
    assert pa[0]["conditional"] and "several departments" in pa[0]["condition"]
    assert not pa[1]["conditional"]
    # A PR's Department Manager step always runs.
    assert not next(s for s in pr if s["role"] == "dept_manager")["conditional"]


# ── Behaviours no other test pinned ──────────────────────────────────────────

_TWO_STEPS = [
    {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    {"id": "gm_or_opm", "role": "gm_or_opm", "label": "GM / OPM"},
]


async def _user(db, role, dept_id=None):
    u = User(id=uuid.uuid4(), full_name=f"U {role}", role=role,
             department_id=dept_id, is_active=True)
    db.add(u)
    await db.flush()
    return u


async def _config(db, workflow):
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa": workflow},
                         budget_admin_config={}))
    await db.flush()


async def _pa(db, creator, status="submitted"):
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-R-{uuid.uuid4().hex[:6]}", title="Rules",
        status=status, approval_step_idx=0, payment_amount=Decimal("100.00"),
        vendor_name="V", currency="CAD", invoice_ids=[], po_id=None,
        created_by=creator.id)
    db.add(pa)
    await db.flush()
    return pa


@pytest.mark.asyncio
async def test_no_same_approver_skip_at_submission(engine_db_session):
    """A requester who is also the first approver still has to approve."""
    db = engine_db_session
    dept = uuid.uuid4()
    db.add(DeptRouting(dept_id=dept, gm_or_opm="gm", supervisor_enabled=False))
    manager = await _user(db, "dept_manager", dept)
    await _user(db, "gm")
    await _config(db, _TWO_STEPS)
    pa = await _pa(db, manager, status="draft")

    await execute_action(db, "pa", pa.id, "submit", manager.id, "dept_manager")

    assert pa.status == "submitted" and pa.approval_step_idx == 0
    task = (await db.execute(sa.select(Task).where(
        Task.document_id == pa.id, Task.is_completed.is_(False)))).scalar_one()
    assert task.assigned_user_id == manager.id


@pytest.mark.asyncio
async def test_no_same_approver_skip_through_a_delegation(engine_db_session):
    """A delegate covering both the manager and the GM approves only the step
    they acted on; the GM step is not auto-approved."""
    db = engine_db_session
    dept = uuid.uuid4()
    db.add(DeptRouting(dept_id=dept, gm_or_opm="gm", supervisor_enabled=False))
    requester = await _user(db, "requester", dept)
    manager = await _user(db, "dept_manager", dept)
    gm = await _user(db, "gm")
    stand_in = await _user(db, "requester")
    today = date(2026, 8, 25)
    for who in (manager, gm):
        db.add(ApprovalDelegation(id=uuid.uuid4(), delegator_user_id=who.id,
                                  delegate_user_id=stand_in.id,
                                  start_date=date(2026, 8, 20), end_date=date(2026, 9, 3),
                                  created_by=uuid.uuid4()))
    await _config(db, _TWO_STEPS)
    pa = await _pa(db, requester)

    await execute_action(db, "pa", pa.id, "approve", stand_in.id, "requester", today=today)

    assert pa.status == "in_review" and pa.approval_step_idx == 1


@pytest.mark.asyncio
async def test_a_system_admin_approval_does_not_cascade(engine_db_session):
    """A System Admin may approve a step they do not hold; only that step."""
    db = engine_db_session
    dept = uuid.uuid4()
    db.add(DeptRouting(dept_id=dept, gm_or_opm="gm", supervisor_enabled=False))
    requester = await _user(db, "requester", dept)
    await _user(db, "dept_manager", dept)
    await _user(db, "gm")
    admin = await _user(db, "system_admin")
    await _config(db, _TWO_STEPS)
    pa = await _pa(db, requester)

    await execute_action(db, "pa", pa.id, "approve", admin.id, "system_admin")

    assert pa.status == "in_review" and pa.approval_step_idx == 1


@pytest.mark.asyncio
async def test_an_empty_post_step_waits_and_its_new_holder_can_approve(engine_db_session):
    db = engine_db_session
    requester = await _user(db, "requester")
    await _config(db, [{"id": "fm", "role": "finance_manager", "label": "Finance Manager"}])
    pa = await _pa(db, requester, status="draft")

    await execute_action(db, "pa", pa.id, "submit", requester.id, "requester")

    # Not skipped: the document waits at the step, addressed to the post.
    assert pa.status == "submitted" and pa.approval_step_idx == 0
    task = (await db.execute(sa.select(Task).where(
        Task.document_id == pa.id, Task.is_completed.is_(False)))).scalar_one()
    assert task.assigned_role == "finance_manager" and task.assigned_user_id is None

    # Someone is given the post; the same task is now theirs to act on.
    holder = await _user(db, "finance_manager")
    await execute_action(db, "pa", pa.id, "approve", holder.id, "finance_manager")
    assert pa.status == "approved"


@pytest.mark.asyncio
async def test_a_recorded_backup_cannot_approve(engine_db_session):
    db = engine_db_session
    dept = uuid.uuid4()
    db.add(DeptRouting(dept_id=dept, gm_or_opm="gm", supervisor_enabled=False))
    requester = await _user(db, "requester", dept)
    await _user(db, "gm")
    backup = await _user(db, "requester")
    db.add(ApprovalBackup(role_code="gm", backup_user_id=backup.id))
    await _config(db, [{"id": "gm_or_opm", "role": "gm_or_opm", "label": "GM / OPM"}])
    pa = await _pa(db, requester)

    with pytest.raises(ValueError, match="Not authorized"):
        await execute_action(db, "pa", pa.id, "approve", backup.id, "requester")


@pytest.mark.asyncio
async def test_a_delegate_can_return(engine_db_session):
    db = engine_db_session
    dept = uuid.uuid4()
    db.add(DeptRouting(dept_id=dept, gm_or_opm="gm", supervisor_enabled=False))
    requester = await _user(db, "requester", dept)
    manager = await _user(db, "dept_manager", dept)
    stand_in = await _user(db, "requester")
    db.add(ApprovalDelegation(id=uuid.uuid4(), delegator_user_id=manager.id,
                              delegate_user_id=stand_in.id,
                              start_date=date(2026, 8, 20), end_date=date(2026, 9, 3),
                              created_by=uuid.uuid4()))
    await _config(db, _TWO_STEPS)
    pa = await _pa(db, requester)

    await execute_action(db, "pa", pa.id, "return", stand_in.id, "requester",
                         today=date(2026, 8, 25))

    assert pa.status == "returned"
