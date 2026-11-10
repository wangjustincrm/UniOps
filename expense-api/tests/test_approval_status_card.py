"""Claim detail: the Approval Status card and the Approval Timeline.

ISS-0194: approval-api resets approval_step_idx to 0 when a claim is returned,
so the card cannot read the returning step off the index — it has to come from
the last return event (same as epms approvalTimeline.ts).

The workflow is merged into the shared company_config row under a key of its
own (`cfm_t194`) and removed afterwards; other modules read the same row.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.approval_event_mirror import ApprovalEventMirror
from app.models.company_config_mirror import EpmsCompanyConfig
from app.models.expense import ExpenseClaim
from tests.conftest import _client, _make_token

CLAIM_TYPE = "CFM_T194"
WF_KEY = "cfm_t194"
STEPS = [
    {"id": "s1", "role": "dept_manager", "label": "Department Manager"},
    {"id": "s2", "role": "gm_or_opm", "label": "GM"},
    {"id": "s3", "role": "finance_director", "label": "Finance Director"},
    {"id": "s4", "role": "finance_manager", "label": "Finance Manager"},
]
T0 = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def workflow(test_engine):
    async with _factory(test_engine)() as s:
        cfg = (await s.execute(select(EpmsCompanyConfig))).scalars().first()
        if cfg is None:
            cfg = EpmsCompanyConfig(id=uuid.uuid4(), dept_gm_opm_mapping={},
                                    role_management={}, workflow_defs={})
            s.add(cfg)
        cfg.workflow_defs = {**(cfg.workflow_defs or {}), WF_KEY: STEPS}
        await s.commit()
    yield
    async with _factory(test_engine)() as s:
        cfg = (await s.execute(select(EpmsCompanyConfig))).scalars().first()
        cfg.workflow_defs = {k: v for k, v in (cfg.workflow_defs or {}).items() if k != WF_KEY}
        await s.commit()


async def _seed(test_engine, *, status: str, step_idx: int,
                events: list[tuple[str, int, uuid.UUID, str | None, datetime]]):
    """A claim plus its approval_events rows (action, step, actor, comment, at)."""
    owner = uuid.uuid4()
    cid = uuid.uuid4()
    async with _factory(test_engine)() as s:
        s.add(ExpenseClaim(
            id=cid, claim_number=f"{CLAIM_TYPE}-{cid.hex[:6]}", claim_type=CLAIM_TYPE,
            employee_id=owner, employee_name="TEST Requester", department_name="E-COM",
            submission_date=date(2026, 11, 3), status=status, approval_step_idx=step_idx,
            created_by=owner,
        ))
        for action, step, actor, comment, at in events:
            s.add(ApprovalEventMirror(
                id=uuid.uuid4(), document_type=WF_KEY, document_id=cid,
                document_number="X", step_idx=step, action=action,
                actor_id=actor, actor_role="system_admin", comment=comment, created_at=at,
            ))
        await s.commit()
    return cid, owner


async def _add_user(test_engine, uid: uuid.UUID, name: str) -> None:
    async with _factory(test_engine)() as s:
        await s.execute(text("INSERT INTO users (id, full_name) VALUES (:i, :n)"),
                        {"i": uid, "n": name})
        await s.commit()


async def _status(cid, owner) -> list[dict]:
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.get(f"/api/v1/expenses/{cid}/approval-status")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_returned_step_comes_from_the_return_event(test_engine, workflow):
    """EXP-20261103-0008: approved at steps 1-2, returned at step 3 (Finance
    Director). The engine left approval_step_idx at 0."""
    dm, admin = uuid.uuid4(), uuid.uuid4()
    await _add_user(test_engine, admin, "System Administrator")
    cid, owner = await _seed(test_engine, status="returned", step_idx=0, events=[
        ("submit", 0, uuid.uuid4(), None, T0),
        ("approve", 0, dm, "ok", T0 + timedelta(minutes=1)),
        ("approve", 1, admin, "ok", T0 + timedelta(minutes=2)),
        ("return", 2, admin, "please explain", T0 + timedelta(minutes=3)),
    ])
    steps = await _status(cid, owner)
    assert [s["state"] for s in steps] == ["approved", "approved", "returned", "waiting"]
    ret = steps[2]
    assert ret["actor_name"] == "System Administrator"
    assert ret["action"] == "return"
    assert ret["comment"] == "please explain"
    assert ret["acted_at"] is not None


@pytest.mark.asyncio
async def test_unapproved_first_step_is_not_marked_returned(test_engine, workflow):
    """TRA-20261103-0002 shape: returned at step 2. Index 0 is only where the
    engine parks a returned claim, so step 1 must not be called "returned";
    and of two returns the latest one decides."""
    admin = uuid.uuid4()
    cid, owner = await _seed(test_engine, status="returned", step_idx=0, events=[
        ("submit", 0, uuid.uuid4(), None, T0),
        ("return", 0, admin, "wrong account", T0 + timedelta(minutes=1)),
        ("submit", 0, uuid.uuid4(), None, T0 + timedelta(minutes=2)),
        ("approve", 0, admin, "ok", T0 + timedelta(minutes=3)),
        ("return", 1, admin, "again", T0 + timedelta(minutes=4)),
    ])
    steps = await _status(cid, owner)
    # The latest return (step 2, GM) wins; step 1 is approved, not "returned".
    assert [s["state"] for s in steps] == ["approved", "returned", "waiting", "waiting"]
    assert steps[1]["comment"] == "again"


@pytest.mark.asyncio
async def test_timeline_puts_submit_before_steps_skipped_in_the_same_transaction(
        test_engine, workflow):
    """ISS-0216 (TRA-20261104-0001): approval-api writes the submission and the
    auto-skipped Department Manager step in one transaction — same now(), to
    the microsecond. The timeline must still read Submitted first. The rows
    are inserted skip-first so that time alone would put the skip on top."""
    owner_actor = uuid.uuid4()
    cid, owner = await _seed(test_engine, status="in_review", step_idx=2, events=[
        ("approve", 1, owner_actor, "Auto-approved (same approver holds both roles)", T0),
        ("approve", 0, owner_actor,
         "Auto-skipped (claimant holds this step — cannot approve own claim)", T0),
        ("submit", 0, owner_actor, "trip to plant", T0),
    ])
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.get(f"/api/v1/expenses/{cid}")
    assert r.status_code == 200, r.text
    events = r.json()["approval_events"]
    assert [(e["action"], e["step_idx"]) for e in events] == [
        ("submit", 0), ("approve", 0), ("approve", 1)]
