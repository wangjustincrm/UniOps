# Approval Delegation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an administrator name a dated stand-in who can approve on another person's behalf, and stop making one person approve the same document twice when they hold two of its steps.

**Architecture:** A new `approval_delegations` table owned by approval-api is the single source of truth; its date range is evaluated inside every query (predicate widening), so a window expires by itself and the `tasks` table is never rewritten. Authorization widens in approval-api; visibility widens in epms-api and expense-api, which read the table read-only via raw SQL. The notification recipient is the one place that *substitutes* rather than widens.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, Alembic, PostgreSQL 15, pytest/pytest-asyncio, React + TypeScript (Portal).

**Spec:** `docs/superpowers/specs/2026-08-19-approval-delegation-design.md`

## Global Constraints

- Worktree `c:/Project/uniops-delegation`, branch `feature/approval-delegation`, based on `origin/main` @ `454ab4e`. Do not work in the main checkout — other sessions have WIP there.
- **Never run `git stash`.** The stash is repository-wide and shared across worktrees; popping would eject another session's work. To capture a baseline, copy files with `cp`.
- Dates are DATE-only, inclusive on both ends, evaluated against **today in `America/Toronto`**. Never write `CURRENT_DATE` into SQL — the containers set no `TZ`, so a database-side date is the UTC date and expires windows four hours early. Always pass `today` as a bind parameter.
- Delegation applies to **approval tasks only** (`tasks.type LIKE 'approve%'`).
- Delegation **must never** grant `system_admin` powers, and **must never** widen `_effective_role_codes` — that function drives `build_scope`'s visibility decisions, so widening it would hand the delegate the delegator's entire company-wide document scope.
- Delegation is **non-transitive**: every query joins exactly one level.
- All user-facing UI copy is **English**. Code comments may be Chinese.
- Run every command in the foreground. Never background a test run.
- Commit per task.

### Baselines — measure before touching anything

Do not reuse remembered numbers. epms-api has three flaky tests, so compare the failing **set**, not the count. A full epms-api run takes about an hour.

```bash
cd c:/Project/uniops-delegation
# approval-api
TEST_APPROVAL_DB=approval_test python -m pytest approval-api/tests -q 2>&1 | tail -5
# epms-api  (see reference_uniops_epms_test_invocation for env)
# expense-api
```
Record each result in the task-1 commit message.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `approval-api/app/models/delegation.py` | `ApprovalDelegation` ORM model |
| `approval-api/alembic/versions/0002_approval_delegations.py` | table + 3 constraints |
| `approval-api/app/crud/delegation.py` | `local_today`, active-delegation queries (authoritative copy) |
| `approval-api/app/api/v1/delegations.py` | admin REST surface |
| `approval-api/tests/test_delegation_*.py` | model, query, authorization, API tests |
| `epms-api/app/core/delegation.py` | read-only sibling copy for epms |
| `expense-api/app/core/delegation.py` | read-only sibling copy for expense |
| `portal/src/pages/admin/ApprovalDelegation.tsx` | admin UI |

**Modified**

| File | Change |
|---|---|
| `approval-api/app/crud/engine.py` | extract `_actor_is_step_holder`; retire `_holds`; delegation branch; event annotation |
| `approval-api/app/main.py` | mount delegations router |
| `approval-api/tests/conftest.py` | register table + `btree_gist` |
| `epms-api/app/crud/task.py` | inbox predicate |
| `epms-api/app/core/access_scope.py` | `_open_task_doc_ids` takes a user-id set |
| `epms-api/app/services/notification.py` | recipient substitution |
| `epms-api/app/crud/dashboard.py` | two counts |
| `epms-api/app/crud/current_step.py` | approver label |
| `epms-api/app/crud/signatories.py` | PDF annotation |
| `epms-api/tests/conftest.py` | shadow table |
| `expense-api/app/api/v1/expenses.py` | `_can_act_on_claim`, my-actions |
| `expense-api/tests/conftest.py` | shadow table |
| `portal/src/services/adminApi.ts`, Portal nav | wire the page |

**The three copies of the date predicate** (`approval-api/app/crud/delegation.py`, `epms-api/app/core/delegation.py`, `expense-api/app/core/delegation.py`) exist because cross-service imports are impossible here. Each carries a `⚠️ SIBLING COPY` header naming the other two — the same convention `access_scope._mapped_dept_ids` already uses. Each gets the same four boundary tests.

---

# PHASE 0 — Same-approver skip fix (independently shippable)

No migration, no new table. Can ship on its own branch ahead of Phase 1.

### Task 1: Extract the shared step-holder check and fix multi-holder auto-skip

`execute_action` decides "does this actor also hold the next step?" with a local
`_holds()` closure that compares against `rm['<role>_user_id']` — the *first*
post holder by uid sort. `_actor_can_approve` was fixed long ago to test
`post_holder_ids` set membership instead. The two have drifted, so a Department
Manager who also holds GM as an additional role is not recognised at
`gm_or_opm` whenever the real GM sorts first, and must approve twice.

**Files:**
- Modify: `approval-api/app/crud/engine.py` (`_actor_can_approve` ~line 328, `_holds` ~line 985, skip walk ~line 1019)
- Test: `approval-api/tests/test_engine_same_approver_skip.py` (create)

**Interfaces:**
- Consumes: `post_holder_ids` from `app.crud.workflow`; `_get_dept_manager_id`, `_resolve_gm_or_opm`, `_build_role_map` from `engine.py`.
- Produces: `async def _actor_is_step_holder(db, step_role, actor_id, routing_dept_id, rm, dept_gm_opm, finance_bp_ids, doc=None, director_uid=None, supervisor_uid=None) -> bool` — identity only, **no `system_admin` bypass**. Task 4 builds on it.

- [ ] **Step 1: Write the failing test**

Create `approval-api/tests/test_engine_same_approver_skip.py`:

```python
"""The same-approver auto-skip must recognise a multi-holder post.

A Department Manager who ALSO holds GM through an additional user_roles role
is a legitimate holder of the gm_or_opm step, but is not
_post_holders()['gm'][0] when the real GM sorts first by uid. The skip walk
used to compare against that collapsed first holder, so it did not fire and
the same person had to approve the document twice.
"""
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import execute_action
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.routing import DeptRouting
from app.models.user import User

# gm_first sorts before actor by uid string, so gm_first is _post_holders()['gm'][0].
_GM_FIRST_ID = uuid.UUID(int=1)
_ACTOR_ID = uuid.UUID(int=(1 << 128) - 1)


async def _seed(db, dept_id):
    requester = User(id=uuid.uuid4(), role="requester",
                     department_id=dept_id, is_active=True)
    gm_first = User(id=_GM_FIRST_ID, role="gm", is_active=True)
    # The actor is BOTH this department's manager and a GM holder.
    actor = User(id=_ACTOR_ID, role="dept_manager",
                 department_id=dept_id, is_active=True)
    db.add_all([requester, gm_first, actor])
    await db.flush()
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'gm')"),
        {"u": str(actor.id)})
    db.add(DeptRouting(dept_id=dept_id, gm_or_opm="gm", supervisor_enabled=False))
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "gm_or_opm", "role": "gm_or_opm", "label": "GM / OPM"},
    ]}))
    await db.flush()
    return requester, actor


@pytest.mark.asyncio
async def test_second_step_auto_skips_for_non_first_post_holder(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, actor = await _seed(db, dept_id)
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-SK-{uuid.uuid4().hex[:4]}",
        title="Same-approver skip", status="submitted", approval_step_idx=0,
        payment_amount=Decimal("100.00"), vendor_name="V", currency="CAD",
        invoice_ids=[], po_id=None, created_by=requester.id)
    db.add(pa)
    await db.flush()

    await execute_action(db, "pa", pa.id, "approve", actor.id, "dept_manager")

    assert pa.status == "approved", (
        "the gm_or_opm step must auto-skip: the approver holds that post too, "
        "even though another holder sorts first"
    )
    events = (await db.execute(
        sa.select(ApprovalEvent)
        .where(ApprovalEvent.document_id == pa.id)
        .order_by(ApprovalEvent.step_idx))).scalars().all()
    assert events[1].comment == "Auto-approved (same approver holds both roles)"


@pytest.mark.asyncio
async def test_second_step_does_not_skip_for_a_different_person(engine_db_session):
    """Negative guard — the fix must not degrade into 'always skip'."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, actor = await _seed(db, dept_id)
    # Strip the actor's GM role: now the two steps are two different people.
    await db.execute(sa.text(
        "DELETE FROM user_roles WHERE user_id = :u"), {"u": str(actor.id)})
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-NS-{uuid.uuid4().hex[:4]}",
        title="No skip", status="submitted", approval_step_idx=0,
        payment_amount=Decimal("100.00"), vendor_name="V", currency="CAD",
        invoice_ids=[], po_id=None, created_by=requester.id)
    db.add(pa)
    await db.flush()

    await execute_action(db, "pa", pa.id, "approve", actor.id, "dept_manager")

    assert pa.status == "in_review"
    assert pa.approval_step_idx == 1, "the GM step must still be pending"
```

- [ ] **Step 2: Run the test and confirm the first one fails**

```bash
cd c:/Project/uniops-delegation
python -m pytest approval-api/tests/test_engine_same_approver_skip.py -v
```

Expected: `test_second_step_auto_skips_for_non_first_post_holder` FAILS with
`assert pa.status == 'approved'` (actual `'in_review'`). The negative guard
PASSES already. **If the first test passes before the fix, stop — the premise
is wrong and the rest of this task is invalid.**

- [ ] **Step 3: Extract `_actor_is_step_holder`**

In `approval-api/app/crud/engine.py`, insert immediately **above**
`_actor_can_approve`:

```python
async def _actor_is_step_holder(
    db: AsyncSession,
    step_role: str,
    actor_id: uuid.UUID,
    routing_dept_id: uuid.UUID | None,
    rm: dict,
    dept_gm_opm: dict,
    finance_bp_ids: set[uuid.UUID],
    doc: Any | None = None,
    director_uid: uuid.UUID | None = None,
    supervisor_uid: uuid.UUID | None = None,
) -> bool:
    """Is this actor, in their OWN right, a holder of `step_role` for this document?

    Identity only — deliberately WITHOUT the `system_admin` bypass that
    `_actor_can_approve` layers on top. Two callers depend on that omission:

      * `_actor_can_approve` adds the bypass itself, so approving as an admin
        still works;
      * the same-approver auto-skip walk must NOT treat an admin as the holder
        of every remaining step — that would auto-approve the whole workflow
        off a single admin click.

    This function is the single authority on "does X hold step Y". It replaced
    a second, weaker copy (`_holds`) that compared against the COLLAPSED first
    post holder and therefore missed multi-holder posts.
    """
    if step_role == "quality_manager":
        if doc is None:
            return False
        expected = getattr(doc, "quality_approver_id", None)
        return expected is not None and actor_id == expected
    if step_role == "finance_bp":
        return actor_id in finance_bp_ids
    if step_role == "dept_manager":
        dept_mgr_id = await _get_dept_manager_id(db, routing_dept_id)
        return dept_mgr_id is not None and actor_id == dept_mgr_id
    if step_role == "gm_or_opm":
        resolved_role, resolved_user_id = await _resolve_gm_or_opm(
            db, routing_dept_id, rm, dept_gm_opm)
        if resolved_user_id is not None and actor_id == resolved_user_id:
            return True
        return actor_id in await post_holder_ids(db, resolved_role)
    if step_role == "director":
        return director_uid is not None and actor_id == director_uid
    if step_role == "supervisor":
        return supervisor_uid is not None and actor_id == supervisor_uid
    post_holders = await post_holder_ids(db, step_role)
    if post_holders:
        return actor_id in post_holders
    return False
```

- [ ] **Step 4: Make `_actor_can_approve` delegate to it**

Replace the body of `_actor_can_approve` (keep its signature and docstring)
after the `system_admin` line with:

```python
    if actor_role == "system_admin":
        return True
    if await _actor_is_step_holder(
        db, step_role, actor_id, routing_dept_id, rm, dept_gm_opm,
        finance_bp_ids, doc, director_uid, supervisor_uid,
    ):
        return True
    # Broadcast fallback: an actor whose own JWT role names the step, used for
    # steps with no resolvable holder set.
    return actor_role == step_role and not await post_holder_ids(db, step_role)
```

- [ ] **Step 5: Replace `_holds` with the shared check in the skip walk**

Delete the `def _holds(role: str) -> bool:` closure. In the skip walk, replace
`if _holds(next_role):` with:

```python
            if await _actor_is_step_holder(
                db, next_role, actor_id, routing_dept_id, rm, dept_gm_opm,
                finance_bp_ids, doc, director_uid, supervisor_uid,
            ):
```

`role_map` and `dept_mgr_id` above the closure become unused — delete them if
nothing else in the function reads them, otherwise leave them.

- [ ] **Step 6: Run the new tests**

```bash
python -m pytest approval-api/tests/test_engine_same_approver_skip.py -v
```

Expected: both PASS.

- [ ] **Step 7: Run the whole approval-api suite against the baseline**

```bash
python -m pytest approval-api/tests -q 2>&1 | tail -5
```

Expected: the failing **set** matches the baseline from Global Constraints.
Pay attention to `test_engine_multiholder_approve.py` and
`test_engine_optional_levels.py` — they exercise these exact paths.

- [ ] **Step 8: Commit**

```bash
git add approval-api/app/crud/engine.py approval-api/tests/test_engine_same_approver_skip.py
git commit -m "fix(approval): recognise multi-holder posts in the same-approver skip

_holds() compared against the collapsed first post holder while
_actor_can_approve() had already moved to post_holder_ids set membership.
A Department Manager who also holds GM as an additional role was therefore
not recognised at the gm_or_opm step and had to approve twice.

Extracts the identity check both paths now share. The extracted function
deliberately omits the system_admin bypass: applying it in the skip walk
would auto-approve every remaining step off one admin click.

Baselines: approval-api <N> passed/<M> failed (unchanged from base)."
```

---

# PHASE 1 — Delegation

### Task 2: Delegation table, model, and migration

**Files:**
- Create: `approval-api/app/models/delegation.py`
- Create: `approval-api/alembic/versions/0002_approval_delegations.py`
- Modify: `approval-api/tests/conftest.py`
- Test: `approval-api/tests/test_delegation_model.py` (create)

**Interfaces:**
- Produces: `ApprovalDelegation` with columns `id, delegator_user_id, delegate_user_id, start_date, end_date, note, revoked_at, revoked_by, created_by, created_at, updated_at`. Tasks 3-5 import it.

- [ ] **Step 1: Write the failing test**

Create `approval-api/tests/test_delegation_model.py`:

```python
import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.delegation import ApprovalDelegation


def _row(delegator, delegate, start, end):
    return ApprovalDelegation(
        id=uuid.uuid4(), delegator_user_id=delegator, delegate_user_id=delegate,
        start_date=start, end_date=end, created_by=uuid.uuid4())


@pytest.mark.asyncio
async def test_roundtrip(engine_db_session):
    db = engine_db_session
    a, b = uuid.uuid4(), uuid.uuid4()
    db.add(_row(a, b, date(2026, 8, 20), date(2026, 9, 3)))
    await db.flush()
    got = (await db.execute(sa.select(ApprovalDelegation))).scalars().one()
    assert got.delegator_user_id == a
    assert got.revoked_at is None


@pytest.mark.asyncio
async def test_cannot_delegate_to_self(engine_db_session):
    db = engine_db_session
    a = uuid.uuid4()
    db.add(_row(a, a, date(2026, 8, 20), date(2026, 9, 3)))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_end_before_start_rejected(engine_db_session):
    db = engine_db_session
    db.add(_row(uuid.uuid4(), uuid.uuid4(), date(2026, 9, 3), date(2026, 8, 20)))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_overlapping_live_windows_rejected(engine_db_session):
    db = engine_db_session
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add(_row(a, b, date(2026, 8, 20), date(2026, 9, 3)))
    await db.flush()
    db.add(_row(a, c, date(2026, 9, 1), date(2026, 9, 10)))
    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_revoked_window_does_not_block_a_new_one(engine_db_session):
    """The exclusion constraint is partial: a revoked row must not reserve dates."""
    db = engine_db_session
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    first = _row(a, b, date(2026, 8, 20), date(2026, 9, 3))
    first.revoked_at = sa.func.now()
    db.add(first)
    await db.flush()
    db.add(_row(a, c, date(2026, 9, 1), date(2026, 9, 10)))
    await db.flush()   # must not raise
```

- [ ] **Step 2: Run and confirm failure**

```bash
python -m pytest approval-api/tests/test_delegation_model.py -v
```

Expected: all FAIL — `ModuleNotFoundError: No module named 'app.models.delegation'`.

- [ ] **Step 3: Create the model**

`approval-api/app/models/delegation.py`:

```python
"""Dated approval delegation (代班) — owned by approval-api.

epms-api and expense-api read this table read-only via raw SQL (the same
cross-service pattern access_scope._mapped_dept_ids uses for
approval_dept_routing). They have no ORM model for it.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Text, func
from sqlalchemy.dialects.postgresql import ExcludeConstraint, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApprovalDelegation(Base):
    __tablename__ = "approval_delegations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    delegator_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    delegate_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)   # inclusive
    end_date: Mapped[date] = mapped_column(Date, nullable=False)     # inclusive
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("delegator_user_id <> delegate_user_id",
                        name="ck_delegation_not_self"),
        CheckConstraint("end_date >= start_date", name="ck_delegation_date_order"),
        # One live window per delegator. Partial: revoked rows free their dates.
        ExcludeConstraint(
            ("delegator_user_id", "="),
            (func.daterange(start_date, end_date, "[]"), "&&"),
            name="ex_delegation_no_overlap",
            using="gist",
            where=revoked_at.is_(None),
        ),
    )
```

- [ ] **Step 4: Create the migration**

`approval-api/alembic/versions/0002_approval_delegations.py`:

```python
"""dated approval delegation (代班)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_approval_delegations"
down_revision = "0001_approval_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # btree_gist is required for an EXCLUDE constraint mixing = and &&.
    # It is trusted in PG 13+, so the database owner can create it, and it is
    # already present in production (booking-api's no_double_booking created
    # it and deliberately never drops it).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "approval_delegations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("delegator_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("delegate_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("delegator_user_id <> delegate_user_id",
                           name="ck_delegation_not_self"),
        sa.CheckConstraint("end_date >= start_date", name="ck_delegation_date_order"),
    )
    op.create_index("ix_approval_delegations_delegator",
                    "approval_delegations", ["delegator_user_id"])
    op.create_index("ix_approval_delegations_delegate",
                    "approval_delegations", ["delegate_user_id"])
    op.execute(
        "ALTER TABLE approval_delegations ADD CONSTRAINT ex_delegation_no_overlap "
        "EXCLUDE USING gist ("
        "  delegator_user_id WITH =,"
        "  daterange(start_date, end_date, '[]') WITH &&"
        ") WHERE (revoked_at IS NULL)"
    )


def downgrade() -> None:
    op.drop_table("approval_delegations")
    # btree_gist is intentionally NOT dropped — booking-api relies on it.
```

- [ ] **Step 5: Register the table in the test schema**

In `approval-api/tests/conftest.py`, add the import next to the other models:

```python
from app.models.delegation import ApprovalDelegation
```

add `ApprovalDelegation.__table__,` to `_ENGINE_TABLES`, and inside
`_build_engine_schema`, **before** `Base.metadata.create_all`, add:

```python
    # The delegation table's EXCLUDE constraint needs btree_gist. Trusted in
    # PG 13+, and this fixture owns the database it just created.
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
```

- [ ] **Step 6: Run the tests**

```bash
python -m pytest approval-api/tests/test_delegation_model.py -v
```

Expected: all 5 PASS. If `CREATE EXTENSION` is denied, the test user does not
own the test database — stop and report, do not weaken the constraint.

- [ ] **Step 7: Commit**

```bash
git add approval-api/app/models/delegation.py approval-api/alembic/versions/0002_approval_delegations.py approval-api/tests/conftest.py approval-api/tests/test_delegation_model.py
git commit -m "feat(approval): approval_delegations table

One live window per delegator, enforced by a partial GiST exclusion
constraint so revoked rows free their dates."
```

---

### Task 3: Active-delegation queries and the local-date helper

**Files:**
- Create: `approval-api/app/crud/delegation.py`
- Test: `approval-api/tests/test_delegation_query.py` (create)

**Interfaces:**
- Produces:
  - `def local_today() -> date` — today in `America/Toronto`.
  - `async def active_delegator_ids(db, delegate_user_id: uuid.UUID, today: date | None = None) -> set[uuid.UUID]` — people whose approvals this user may act on today.
  - `async def active_delegate_id(db, delegator_user_id: uuid.UUID, today: date | None = None) -> uuid.UUID | None` — who is standing in for this person today (used by the notification substitution in Task 8's sibling copy).
  - Both filter on `revoked_at IS NULL`, the inclusive date range, and an **active delegate account**.

- [ ] **Step 1: Write the failing test**

Create `approval-api/tests/test_delegation_query.py`:

```python
"""Boundary behaviour of the active-delegation predicate.

⚠️ SIBLING COPIES: epms-api/tests/test_delegation_query.py and
expense-api/tests/test_delegation_query.py assert the SAME four boundaries
against their own copy of this predicate. Change one, change all three.
"""
import uuid
from datetime import date

import pytest

from app.crud.delegation import active_delegate_id, active_delegator_ids
from app.models.delegation import ApprovalDelegation
from app.models.user import User

WINDOW_START = date(2026, 8, 20)
WINDOW_END = date(2026, 9, 3)


async def _seed(db, *, delegate_active=True):
    delegator = User(id=uuid.uuid4(), role="dept_manager", is_active=True)
    delegate = User(id=uuid.uuid4(), role="dept_manager", is_active=delegate_active)
    db.add_all([delegator, delegate])
    await db.flush()
    db.add(ApprovalDelegation(
        id=uuid.uuid4(), delegator_user_id=delegator.id,
        delegate_user_id=delegate.id, start_date=WINDOW_START,
        end_date=WINDOW_END, created_by=uuid.uuid4()))
    await db.flush()
    return delegator, delegate


@pytest.mark.asyncio
@pytest.mark.parametrize("today,expected", [
    (date(2026, 8, 19), False),   # day before start
    (WINDOW_START, True),         # first day, inclusive
    (WINDOW_END, True),           # last day, inclusive
    (date(2026, 9, 4), False),    # day after end
])
async def test_window_boundaries(engine_db_session, today, expected):
    db = engine_db_session
    delegator, delegate = await _seed(db)
    got = await active_delegator_ids(db, delegate.id, today=today)
    assert (delegator.id in got) is expected


@pytest.mark.asyncio
async def test_inactive_delegate_is_ignored(engine_db_session):
    """A deactivated stand-in must stop matching, so approval falls back to the
    delegator — who never lost the right. Nothing can strand."""
    db = engine_db_session
    delegator, delegate = await _seed(db, delegate_active=False)
    got = await active_delegator_ids(db, delegate.id, today=WINDOW_START)
    assert got == set()


@pytest.mark.asyncio
async def test_revoked_is_ignored_immediately(engine_db_session):
    db = engine_db_session
    delegator, delegate = await _seed(db)
    from sqlalchemy import text
    await db.execute(text(
        "UPDATE approval_delegations SET revoked_at = now() "
        "WHERE delegator_user_id = :d"), {"d": str(delegator.id)})
    got = await active_delegator_ids(db, delegate.id, today=WINDOW_START)
    assert got == set()


@pytest.mark.asyncio
async def test_not_transitive(engine_db_session):
    """A -> B and B -> C must not give C anything of A's."""
    db = engine_db_session
    a = User(id=uuid.uuid4(), role="dept_manager", is_active=True)
    b = User(id=uuid.uuid4(), role="dept_manager", is_active=True)
    c = User(id=uuid.uuid4(), role="dept_manager", is_active=True)
    db.add_all([a, b, c])
    await db.flush()
    db.add_all([
        ApprovalDelegation(id=uuid.uuid4(), delegator_user_id=a.id,
                           delegate_user_id=b.id, start_date=WINDOW_START,
                           end_date=WINDOW_END, created_by=uuid.uuid4()),
        ApprovalDelegation(id=uuid.uuid4(), delegator_user_id=b.id,
                           delegate_user_id=c.id, start_date=WINDOW_START,
                           end_date=WINDOW_END, created_by=uuid.uuid4()),
    ])
    await db.flush()
    got = await active_delegator_ids(db, c.id, today=WINDOW_START)
    assert got == {b.id}, "C acts for B only — never for A"


@pytest.mark.asyncio
async def test_reverse_lookup(engine_db_session):
    db = engine_db_session
    delegator, delegate = await _seed(db)
    assert await active_delegate_id(db, delegator.id, today=WINDOW_START) == delegate.id
    assert await active_delegate_id(db, delegator.id, today=date(2026, 9, 4)) is None
```

- [ ] **Step 2: Run and confirm failure**

```bash
python -m pytest approval-api/tests/test_delegation_query.py -v
```

Expected: all FAIL — `No module named 'app.crud.delegation'`.

- [ ] **Step 3: Implement**

`approval-api/app/crud/delegation.py`:

```python
"""Active approval delegations (代班) — the authoritative copy.

⚠️ SIBLING COPIES: epms-api/app/core/delegation.py and
expense-api/app/core/delegation.py carry the same predicate, because those
services cannot import from this one. If the semantics here change, change
them too — each has its own boundary tests that will go red.

WHY `today` IS A BIND PARAMETER, NOT `CURRENT_DATE`:
the containers set no TZ, so a database-side date is the UTC date. Between
20:00 and midnight in Toronto that is already tomorrow, which would expire a
window four hours early on its last day. Passing the plant-local date in also
lets tests assert the boundaries without touching a clock.
"""
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PLANT_TIMEZONE = "America/Toronto"

_ACTIVE = """
      revoked_at IS NULL
  AND start_date <= :today
  AND end_date   >= :today
  AND EXISTS (SELECT 1 FROM users u
              WHERE u.id = d.delegate_user_id AND u.is_active)
"""


def local_today() -> date:
    """Today's calendar date at the plant, not in UTC."""
    return datetime.now(ZoneInfo(PLANT_TIMEZONE)).date()


async def active_delegator_ids(
    db: AsyncSession, delegate_user_id: uuid.UUID, today: date | None = None,
) -> set[uuid.UUID]:
    """People whose approvals `delegate_user_id` may act on today.

    One level only — delegation is not transitive. A delegate may legitimately
    cover several delegators at once (the exclusion constraint is per
    delegator, not per delegate), so this returns a set.
    """
    rows = (await db.execute(text(
        f"SELECT d.delegator_user_id FROM approval_delegations d "
        f"WHERE d.delegate_user_id = :me AND {_ACTIVE}"),
        {"me": str(delegate_user_id), "today": today or local_today()},
    )).scalars().all()
    return set(rows)


async def active_delegate_id(
    db: AsyncSession, delegator_user_id: uuid.UUID, today: date | None = None,
) -> uuid.UUID | None:
    """Who is standing in for `delegator_user_id` today, if anyone.

    At most one row can match: the exclusion constraint forbids overlapping
    live windows for one delegator.
    """
    return (await db.execute(text(
        f"SELECT d.delegate_user_id FROM approval_delegations d "
        f"WHERE d.delegator_user_id = :who AND {_ACTIVE}"),
        {"who": str(delegator_user_id), "today": today or local_today()},
    )).scalar_one_or_none()
```

- [ ] **Step 4: Run the tests**

```bash
python -m pytest approval-api/tests/test_delegation_query.py -v
```

Expected: all PASS (9 cases including the 4 parametrised boundaries).

- [ ] **Step 5: Commit**

```bash
git add approval-api/app/crud/delegation.py approval-api/tests/test_delegation_query.py
git commit -m "feat(approval): active-delegation queries with plant-local dates

today is a bind parameter, never CURRENT_DATE: the containers set no TZ, so a
database-side date would expire a window four hours early on its last day."
```

---

### Task 4: Authorize the delegate and annotate the approval event

**Files:**
- Modify: `approval-api/app/crud/engine.py`
- Test: `approval-api/tests/test_delegation_authz.py` (create)

**Interfaces:**
- Consumes: `_actor_is_step_holder` (Task 1), `active_delegator_ids` (Task 3).
- Produces: `async def _acting_on_behalf_of(...) -> uuid.UUID | None` — the delegator whose identity the actor borrowed, or `None` when the actor holds the step themselves.

**Deliberate scope decision — read before implementing.** The same-approver
auto-skip walk keeps using `_actor_is_step_holder` (own identity only). A
delegate does **not** auto-skip a later step they only reach through someone
else's delegation; they get the next task and click again. The primary case —
one person holding two posts — is fully covered, and compounding delegated
skips would collapse several people's accountability into one click.

- [ ] **Step 1: Write the failing test**

Create `approval-api/tests/test_delegation_authz.py`:

```python
"""A delegate may approve in the delegator's place; both retain the right."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.crud.engine import _actor_can_approve, execute_action
from app.crud.workflow import get_dept_gm_opm_mapping, get_role_management
from app.models.config import CompanyConfig
from app.models.delegation import ApprovalDelegation
from app.models.event import ApprovalEvent
from app.models.pa import PaymentApplication
from app.models.routing import DeptRouting
from app.models.user import User

TODAY = date(2026, 8, 25)
START, END = date(2026, 8, 20), date(2026, 9, 3)


async def _seed(db, dept_id, *, delegate_start=START, delegate_end=END):
    requester = User(id=uuid.uuid4(), role="requester", department_id=dept_id, is_active=True)
    manager = User(id=uuid.uuid4(), role="dept_manager", department_id=dept_id, is_active=True)
    stand_in = User(id=uuid.uuid4(), role="dept_manager", is_active=True)
    db.add_all([requester, manager, stand_in])
    await db.flush()
    db.add(DeptRouting(dept_id=dept_id, gm_or_opm="gm", supervisor_enabled=False))
    db.add(CompanyConfig(id=uuid.uuid4(), workflow_defs={"pa": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"}]}))
    db.add(ApprovalDelegation(
        id=uuid.uuid4(), delegator_user_id=manager.id, delegate_user_id=stand_in.id,
        start_date=delegate_start, end_date=delegate_end, created_by=uuid.uuid4()))
    await db.flush()
    return requester, manager, stand_in


async def _ctx(db):
    rm = await get_role_management(db)
    return rm, await get_dept_gm_opm_mapping(db), {
        uuid.UUID(u) for u in rm.get("finance_bp_user_ids", [])}


@pytest.mark.asyncio
async def test_delegate_is_authorized_inside_the_window(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    _, manager, stand_in = await _seed(db, dept_id)
    rm, mapping, fbp = await _ctx(db)
    assert await _actor_can_approve(
        db, "dept_manager", stand_in.id, "dept_manager", dept_id, rm, mapping, fbp,
        today=TODAY)


@pytest.mark.asyncio
async def test_delegate_is_denied_outside_the_window(engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    _, manager, stand_in = await _seed(db, dept_id)
    rm, mapping, fbp = await _ctx(db)
    assert not await _actor_can_approve(
        db, "dept_manager", stand_in.id, "dept_manager", dept_id, rm, mapping, fbp,
        today=date(2026, 9, 4))


@pytest.mark.asyncio
async def test_delegator_keeps_the_right(engine_db_session):
    """Both can approve — the decision was 'first click wins', not a transfer."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    _, manager, stand_in = await _seed(db, dept_id)
    rm, mapping, fbp = await _ctx(db)
    assert await _actor_can_approve(
        db, "dept_manager", manager.id, "dept_manager", dept_id, rm, mapping, fbp,
        today=TODAY)


@pytest.mark.asyncio
async def test_system_admin_is_never_inherited(engine_db_session):
    """Delegating FROM an admin must not hand over the unconditional bypass."""
    db = engine_db_session
    dept_id = uuid.uuid4()
    admin = User(id=uuid.uuid4(), role="system_admin", is_active=True)
    nobody = User(id=uuid.uuid4(), role="requester", is_active=True)
    db.add_all([admin, nobody])
    await db.flush()
    db.add(ApprovalDelegation(
        id=uuid.uuid4(), delegator_user_id=admin.id, delegate_user_id=nobody.id,
        start_date=START, end_date=END, created_by=uuid.uuid4()))
    await db.flush()
    rm, mapping, fbp = await _ctx(db)
    assert not await _actor_can_approve(
        db, "dept_manager", nobody.id, "requester", dept_id, rm, mapping, fbp,
        today=TODAY), "an admin's delegate must not inherit the admin bypass"


@pytest.mark.asyncio
async def test_approval_event_records_the_delegate_and_names_the_delegator(
        engine_db_session):
    db = engine_db_session
    dept_id = uuid.uuid4()
    requester, manager, stand_in = await _seed(db, dept_id)
    manager.full_name = "Sivers"
    pa = PaymentApplication(
        id=uuid.uuid4(), pa_number=f"PA-DG-{uuid.uuid4().hex[:4]}",
        title="Delegated approval", status="submitted", approval_step_idx=0,
        payment_amount=Decimal("100.00"), vendor_name="V", currency="CAD",
        invoice_ids=[], po_id=None, created_by=requester.id)
    db.add(pa)
    await db.flush()

    await execute_action(db, "pa", pa.id, "approve", stand_in.id, "dept_manager",
                         today=TODAY)

    ev = (await db.execute(sa.select(ApprovalEvent).where(
        ApprovalEvent.document_id == pa.id))).scalars().one()
    assert ev.actor_id == stand_in.id, "the person who clicked is the actor"
    assert "on behalf of Sivers" in (ev.comment or "")
```

- [ ] **Step 2: Run and confirm failure**

```bash
python -m pytest approval-api/tests/test_delegation_authz.py -v
```

Expected: FAIL — `_actor_can_approve() got an unexpected keyword argument 'today'`.

- [ ] **Step 3: Thread `today` through and add the delegation branch**

In `engine.py`, import at the top:

```python
from app.crud.delegation import active_delegator_ids, local_today
```

Add `today: date | None = None` as the last keyword parameter of
`_actor_can_approve` and of `execute_action`. In `_actor_can_approve`, after
the own-identity check added in Task 1 and **before** the broadcast fallback:

```python
    # Delegation: act for anyone who has named this actor their stand-in today.
    # Deliberately placed AFTER the own-identity check and BELOW the
    # system_admin bypass — _actor_is_step_holder has no bypass, so an admin's
    # delegate inherits nothing.
    for delegator_id in await active_delegator_ids(db, actor_id, today=today):
        if await _actor_is_step_holder(
            db, step_role, delegator_id, routing_dept_id, rm, dept_gm_opm,
            finance_bp_ids, doc, director_uid, supervisor_uid,
        ):
            return True
```

- [ ] **Step 4: Add the on-behalf-of resolver**

Immediately below `_actor_can_approve`:

```python
async def _acting_on_behalf_of(
    db: AsyncSession,
    step_role: str,
    actor_id: uuid.UUID,
    routing_dept_id: uuid.UUID | None,
    rm: dict,
    dept_gm_opm: dict,
    finance_bp_ids: set[uuid.UUID],
    doc: Any | None = None,
    director_uid: uuid.UUID | None = None,
    supervisor_uid: uuid.UUID | None = None,
    today: date | None = None,
) -> uuid.UUID | None:
    """The delegator whose identity the actor borrowed, or None when the actor
    holds this step in their own right.

    A delegate may cover several people at once, so more than one delegator can
    satisfy the step. Sorting makes the annotation deterministic rather than
    dependent on row order.
    """
    if await _actor_is_step_holder(
        db, step_role, actor_id, routing_dept_id, rm, dept_gm_opm,
        finance_bp_ids, doc, director_uid, supervisor_uid,
    ):
        return None
    for delegator_id in sorted(await active_delegator_ids(db, actor_id, today=today)):
        if await _actor_is_step_holder(
            db, step_role, delegator_id, routing_dept_id, rm, dept_gm_opm,
            finance_bp_ids, doc, director_uid, supervisor_uid,
        ):
            return delegator_id
    return None
```

- [ ] **Step 5: Annotate the approval event**

In `execute_action`'s approve branch, where the `ApprovalEvent` for the actor's
own decision is created, resolve and append the annotation:

```python
        on_behalf_of = await _acting_on_behalf_of(
            db, recorded_role, actor_id, routing_dept_id, rm, dept_gm_opm,
            finance_bp_ids, doc, director_uid, supervisor_uid, today=today)
        if on_behalf_of is not None:
            delegator_name = (await db.execute(
                select(User.full_name).where(User.id == on_behalf_of))
            ).scalar_one_or_none() or "another approver"
            suffix = f"on behalf of {delegator_name}"
            comment = f"{comment} — {suffix}" if comment else suffix
```

Place this immediately before the `ApprovalEvent(...)` construction so the
computed `comment` is the one stored. Pass `today=today` down to the two
`_actor_can_approve` call sites (~lines 963 and 1065).

- [ ] **Step 6: Run the tests**

```bash
python -m pytest approval-api/tests/test_delegation_authz.py -v
```

Expected: all 5 PASS.

- [ ] **Step 7: Run the approval-api suite**

```bash
python -m pytest approval-api/tests -q 2>&1 | tail -5
```

Expected: the failing set matches the Task 1 baseline.

- [ ] **Step 8: Commit**

```bash
git add approval-api/app/crud/engine.py approval-api/tests/test_delegation_authz.py
git commit -m "feat(approval): authorize an active delegate, annotate the event

The delegation branch sits below the system_admin bypass and calls
_actor_is_step_holder, which has no bypass — so delegating from an admin
hands over nothing. Both parties keep the right to approve; the event records
whoever clicked, annotated 'on behalf of <delegator>'."
```

---

### Task 5: Admin REST surface for delegations

**Files:**
- Create: `approval-api/app/api/v1/delegations.py`
- Modify: `approval-api/app/main.py`
- Test: `approval-api/tests/test_delegation_api.py` (create)

**Interfaces:**
- Produces: `GET /approval/v1/delegations`, `POST /approval/v1/delegations`, `PATCH /approval/v1/delegations/{id}`, `POST /approval/v1/delegations/{id}/revoke`. All `system_admin` only. Task 12's UI consumes them.

- [ ] **Step 1: Write the failing test**

Create `approval-api/tests/test_delegation_api.py`. Follow the existing
`approval-api/tests/test_resync_document_endpoint.py` for how this service
builds an authenticated client; assert:

```python
# 1. A non-admin gets 403 on every verb.
# 2. POST with delegator == delegate  -> 422
# 3. POST with end_date < start_date  -> 422
# 4. POST overlapping a live window   -> 409 with a readable message,
#    NOT a 500 leaking the constraint name.
# 5. POST after the first was revoked -> 201
# 6. GET returns the rows with delegator/delegate full names resolved.
# 7. revoke sets revoked_at and revoked_by, and a second revoke is a no-op 200.
```

Write each as a real test function with concrete request bodies and status
assertions before implementing.

- [ ] **Step 2: Run and confirm failure**

```bash
python -m pytest approval-api/tests/test_delegation_api.py -v
```

Expected: 404s (router not mounted).

- [ ] **Step 3: Implement the router**

`approval-api/app/api/v1/delegations.py`, mirroring
`approval-api/app/api/v1/routing.py`'s shape (`APIRouter(prefix="/delegations",
tags=["delegations"])`, `CurrentUser`, `system_admin` guard). The overlap
translation is the part that must not be skipped:

```python
from asyncpg.exceptions import ExclusionViolationError
from sqlalchemy.exc import IntegrityError

...
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if isinstance(getattr(exc, "orig", None).__cause__, ExclusionViolationError):
            raise HTTPException(
                status_code=409,
                detail="This person already has a delegation covering part of "
                       "that date range. Revoke or shorten it first.",
            )
        raise
```

Guard every endpoint with the same check `routing.py` uses:

```python
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
```

- [ ] **Step 4: Mount it**

In `approval-api/app/main.py`, next to the routing router:

```python
from app.api.v1.delegations import router as delegations_router
...
app.include_router(delegations_router, prefix="/approval/v1")
```

- [ ] **Step 5: Run the tests**

```bash
python -m pytest approval-api/tests/test_delegation_api.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add approval-api/app/api/v1/delegations.py approval-api/app/main.py approval-api/tests/test_delegation_api.py
git commit -m "feat(approval): admin REST surface for delegations

Overlapping windows return 409 with an actionable message instead of a 500
leaking the exclusion constraint name."
```

---

### Task 6: epms-api read-only sibling copy

**Files:**
- Create: `epms-api/app/core/delegation.py`
- Modify: `epms-api/tests/conftest.py`
- Test: `epms-api/tests/test_delegation_query.py` (create)

**Interfaces:**
- Produces (same names as Task 3, so the two read alike):
  - `local_today() -> date`
  - `async def active_delegator_ids(db, delegate_user_id, today=None) -> set[uuid.UUID]`
  - `async def active_delegate_id(db, delegator_user_id, today=None) -> uuid.UUID | None`
  - `async def delegated_broadcast_roles(db, delegator_ids: set[uuid.UUID]) -> set[str]` — the union of those delegators' role codes, minus `_PERSONAL_APPROVAL_ROLES`, for matching role-pool tasks. **Used only inside task queries — never fed into `_effective_role_codes`.**

- [ ] **Step 1: Add the shadow table to the test schema**

epms-api does not own `approval_delegations`; it reads it. Follow the existing
`approval_dept_routing` shadow-table idiom already in
`epms-api/tests/conftest.py` (~line 271):

```python
        await conn.execute(text("DROP TABLE IF EXISTS approval_delegations CASCADE"))
        await conn.execute(text(
            "CREATE TABLE approval_delegations ("
            "  id uuid PRIMARY KEY,"
            "  delegator_user_id uuid NOT NULL,"
            "  delegate_user_id uuid NOT NULL,"
            "  start_date date NOT NULL,"
            "  end_date date NOT NULL,"
            "  note text NULL,"
            "  revoked_at timestamptz NULL,"
            "  revoked_by uuid NULL,"
            "  created_by uuid NOT NULL,"
            "  created_at timestamptz NOT NULL DEFAULT now(),"
            "  updated_at timestamptz NOT NULL DEFAULT now())"))
```

The shadow omits the constraints on purpose: epms only reads, and approval-api
owns correctness of what gets written.

- [ ] **Step 2: Write the failing test**

Create `epms-api/tests/test_delegation_query.py` with the **same four boundary
assertions** as Task 3 (`start − 1` False, `start` True, `end` True, `end + 1`
False), plus the inactive-delegate and revoked cases, written against
`app.core.delegation`. Copy the structure; use epms's own fixtures and `User`
model.

- [ ] **Step 3: Run and confirm failure**

```bash
python -m pytest epms-api/tests/test_delegation_query.py -v
```

Expected: FAIL — module missing.

- [ ] **Step 4: Implement**

Create `epms-api/app/core/delegation.py` as a copy of Task 3's module (same
`_ACTIVE` SQL, same `PLANT_TIMEZONE`, same function names), with the header:

```python
"""Active approval delegations — READ-ONLY sibling copy.

⚠️ SIBLING COPY of approval-api/app/crud/delegation.py (authoritative) and
expense-api/app/core/delegation.py. epms never writes this table.
"""
```

and add:

```python
async def delegated_broadcast_roles(
    db: AsyncSession, delegator_ids: set[uuid.UUID],
) -> set[str]:
    """Role codes the given delegators hold, for matching ROLE-POOL tasks
    (assigned_user_id IS NULL).

    ⚠️ Never feed this into _effective_role_codes. That function decides
    document VISIBILITY SCOPE in access_scope.build_scope — unioning a
    delegator's roles into it would hand the delegate the delegator's entire
    company-wide scope, far beyond approving their tasks.
    """
    if not delegator_ids:
        return set()
    from app.crud.task import _PERSONAL_APPROVAL_ROLES
    ids = [str(i) for i in delegator_ids]
    rows = (await db.execute(sa.text(
        "SELECT role FROM users WHERE id = ANY(:ids) AND is_active "
        "UNION SELECT role_code FROM user_roles WHERE user_id = ANY(:ids)"),
        {"ids": ids})).scalars().all()
    return {r for r in rows if r} - _PERSONAL_APPROVAL_ROLES
```

- [ ] **Step 5: Run the tests**

```bash
python -m pytest epms-api/tests/test_delegation_query.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/core/delegation.py epms-api/tests/conftest.py epms-api/tests/test_delegation_query.py
git commit -m "feat(epms): read-only delegation predicate + shadow test table"
```

---

### Task 7: Widen the EPMS task inbox and document visibility

**Files:**
- Modify: `epms-api/app/crud/task.py` (`get_for_role`, ~line 493)
- Modify: `epms-api/app/core/access_scope.py` (`_open_task_doc_ids` ~line 33 and its three chain helpers)
- Test: `epms-api/tests/test_delegation_inbox.py` (create)

**Interfaces:**
- Consumes: `active_delegator_ids`, `delegated_broadcast_roles` (Task 6).
- Produces: `_open_task_doc_ids(user_ids: set[uuid.UUID], doc_type: str)` — **signature change**: it now takes a SET of user ids (the viewer plus their delegators) instead of one. `_task_chain_pr_ids`, `_task_chain_po_ids` and `_task_chain_agreement_ids` take the same set and pass it down. `build_scope` resolves the set once and threads it through.

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_delegation_inbox.py` asserting:

```python
# 1. Inside the window, the delegate's inbox contains the delegator's PINNED
#    approve task (assigned_user_id = delegator).
# 2. Inside the window, the delegate's inbox contains a ROLE-POOL approve task
#    (assigned_user_id IS NULL, assigned_role = a role the delegator holds).
# 3. Outside the window, neither appears.
# 4. NON-approval tasks (type 'create_pa') never appear, in or out of window.
# 5. The delegate can OPEN the delegator's PR (build_scope includes it) inside
#    the window, and cannot outside it.
# 6. The delegate's own visibility scope is otherwise UNCHANGED: delegating
#    from a gm (unrestricted) must NOT make the delegate unrestricted.
```

Case 6 is the security regression guard — write it as a real assertion that the
delegate still cannot see an unrelated department's PR.

- [ ] **Step 2: Run and confirm failure**

```bash
python -m pytest epms-api/tests/test_delegation_inbox.py -v
```

- [ ] **Step 3: Widen `get_for_role`**

In `epms-api/app/crud/task.py`, inside `get_for_role`, after `broadcast_roles`
is computed:

```python
        from app.core.delegation import active_delegator_ids, delegated_broadcast_roles
        delegator_ids = await active_delegator_ids(db, user_id)
        deleg_roles = await delegated_broadcast_roles(db, delegator_ids)
        clauses = [
            and_(Task.assigned_user_id.is_(None), Task.assigned_role.in_(broadcast_roles)),
            Task.assigned_user_id == user_id,
        ]
        if delegator_ids:
            # Delegation covers APPROVAL tasks only — never create_po / create_pa
            # / GR acknowledgement, which are role pools that do not strand and
            # whose actions are gated by the Access Control matrix.
            clauses.append(and_(
                Task.type.like("approve%"),
                Task.assigned_user_id.in_(delegator_ids)))
            if deleg_roles:
                clauses.append(and_(
                    Task.type.like("approve%"),
                    Task.assigned_user_id.is_(None),
                    Task.assigned_role.in_(deleg_roles)))
        q = q.where(or_(*clauses))
```

- [ ] **Step 4: Widen `_open_task_doc_ids` and thread the set through**

Change the signature and the predicate:

```python
def _open_task_doc_ids(user_ids: set[uuid.UUID], doc_type: str) -> Select:
    """Doc ids any of `user_ids` has an OPEN task for.

    `user_ids` is the viewer PLUS anyone currently delegating to them: if you
    are asked to approve a document you can open it, and that must hold when
    the ask reached you through a delegation.
    """
    return select(Task.document_id).where(
        Task.assigned_user_id.in_(user_ids),
        Task.document_type == doc_type,
        Task.is_completed.is_(False),
    )
```

Update `_task_chain_pr_ids`, `_task_chain_po_ids` and
`_task_chain_agreement_ids` to take `user_ids: set[uuid.UUID]` and pass it
down. In `build_scope` (and any other caller), resolve once:

```python
    task_user_ids = {user_id} | await active_delegator_ids(db, user_id)
```

and pass `task_user_ids` wherever `user_id` was previously handed to those
helpers. Grep for every call site before running:

```bash
grep -n "_open_task_doc_ids\|_task_chain_" epms-api/app/core/access_scope.py
```

- [ ] **Step 5: Run the tests**

```bash
python -m pytest epms-api/tests/test_delegation_inbox.py epms-api/tests/test_authz_scope.py epms-api/tests/test_pr_scoping.py -v
```

Expected: new tests PASS, existing scope tests unchanged.

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/crud/task.py epms-api/app/core/access_scope.py epms-api/tests/test_delegation_inbox.py
git commit -m "feat(epms): delegated approval tasks reach the delegate's inbox

Approval tasks only. The delegate's own visibility scope is untouched —
_effective_role_codes is deliberately not widened, because it decides
build_scope's unrestricted/restricted split."
```

---

### Task 8: Send the notification to the delegate instead

**Files:**
- Modify: `epms-api/app/services/notification.py` (~line 206)
- Test: `epms-api/tests/test_delegation_notification.py` (create)

**Interfaces:**
- Consumes: `active_delegate_id` (Task 6).

- [ ] **Step 1: Write the failing test**

```python
# 1. Inside the window, a pinned approve task addressed to the delegator sends
#    to the DELEGATE's address, and NOT to the delegator's.
# 2. Outside the window it sends to the delegator.
# 3. If the delegate is deactivated, it falls back to the delegator — the mail
#    must not vanish into a disabled account.
# 4. A NON-approval task addressed to the delegator still mails the delegator.
```

- [ ] **Step 2: Run and confirm failure**

```bash
python -m pytest epms-api/tests/test_delegation_notification.py -v
```

- [ ] **Step 3: Implement the substitution**

In `notification.py`, where the pinned recipient is resolved:

```python
        if task.assigned_user_id:
            recipient_id = task.assigned_user_id
            # Substitution, not widening: the delegator is away, so mailing
            # them is noise. active_delegate_id already returns None when the
            # stand-in is deactivated, which falls back to the delegator.
            if (task.type or "").startswith("approve"):
                from app.core.delegation import active_delegate_id
                stand_in = await active_delegate_id(db, task.assigned_user_id)
                if stand_in is not None:
                    recipient_id = stand_in
            user = await db.get(User, recipient_id)
            if user and user.is_active:
                recipients.append(user)
```

- [ ] **Step 4: Run the tests**

```bash
python -m pytest epms-api/tests/test_delegation_notification.py epms-api/tests/test_notification_dispatch.py -v
```

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/services/notification.py epms-api/tests/test_delegation_notification.py
git commit -m "feat(epms): mail the delegate, not the person on leave

The one place delegation substitutes rather than widens. Falls back to the
delegator when the stand-in's account is deactivated."
```

---

### Task 9: Dashboard counts and the current-step label

**Files:**
- Modify: `epms-api/app/crud/dashboard.py` (~lines 116 and 347)
- Modify: `epms-api/app/crud/current_step.py` (~lines 69-86)
- Test: `epms-api/tests/test_delegation_dashboard.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# 1. Inside the window, the delegate's pending-approval count includes the
#    delegator's approve tasks.
# 2. Inside the window, the delegate's overdue-task count includes the
#    delegator's overdue APPROVE tasks (and not their other overdue tasks).
# 3. A list row whose open task belongs to a delegator renders
#    current_step.approver_name as "Sivers (delegated: Mohammadi)".
# 4. With no delegation, the label is unchanged: "Sivers".
```

- [ ] **Step 2: Run and confirm failure**

- [ ] **Step 3: Widen the two dashboard counts**

Both use `Task.assigned_user_id == user_id`. Resolve the set once per request
and widen, restricting the delegated half to approval tasks:

```python
    from app.core.delegation import active_delegator_ids
    delegator_ids = await active_delegator_ids(db, user_id)
    own = Task.assigned_user_id == user_id
    if delegator_ids:
        own = or_(own, and_(Task.type.like("approve%"),
                            Task.assigned_user_id.in_(delegator_ids)))
```

then use `own` in place of the old equality in both queries.

- [ ] **Step 4: Annotate the current-step label**

In `current_step.py`, after `name_by_user` is built, resolve stand-ins for the
same ids and render:

```python
    from app.core.delegation import active_delegate_id
    stand_in_names: dict = {}
    for uid in user_ids:
        stand_in_id = await active_delegate_id(db, uid)
        if stand_in_id is not None:
            stand_in_names[uid] = (await resolve_user_names(db, [stand_in_id])).get(stand_in_id)
```

and where `approver_name` is set:

```python
            "approver_name": _approver_label(
                name_by_user.get(t.assigned_user_id),
                stand_in_names.get(t.assigned_user_id),
            ) if t.assigned_user_id else None,
```

with the helper:

```python
def _approver_label(name: str | None, stand_in: str | None) -> str | None:
    """"Sivers" normally; "Sivers (delegated: Mohammadi)" while she is away, so
    a reader can tell why someone else is expected to act."""
    if name and stand_in:
        return f"{name} (delegated: {stand_in})"
    return name
```

- [ ] **Step 5: Run the tests**

```bash
python -m pytest epms-api/tests/test_delegation_dashboard.py epms-api/tests/test_current_step_enrich.py -v
```

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/crud/dashboard.py epms-api/app/crud/current_step.py epms-api/tests/test_delegation_dashboard.py
git commit -m "feat(epms): delegated tasks count on the dashboard; label the stand-in"
```

---

### Task 10: Name the delegation on generated PDFs

**Files:**
- Modify: `epms-api/app/crud/signatories.py` (`approval_signatories`)
- Test: `epms-api/tests/test_delegation_signatories.py` (create)

One change point covers all four generators (`pdf_pr`, `pdf_po`, `pdf_pa`,
`pdf_gr`) — they all call this helper.

- [ ] **Step 1: Write the failing test**

```python
# 1. An approval event whose comment ends "on behalf of Sivers" renders the
#    signatory name as "Mohammadi (on behalf of Sivers)".
# 2. A plain approval renders just "Mohammadi".
# 3. Machine events are still filtered out, and
#    "Auto-approved (same approver holds both roles)" is still KEPT — it is a
#    real person holding two posts.
```

Assertion 3 guards the existing `_MACHINE_APPROVAL_MARKERS` behaviour, which
this change must not disturb.

- [ ] **Step 2: Run and confirm failure**

- [ ] **Step 3: Implement**

The engine already wrote the delegator's name into the event comment (Task 4),
so this reads it back rather than re-querying:

```python
_ON_BEHALF_MARKER = "on behalf of "


def _signatory_name(name: str | None, comment: str | None) -> str | None:
    """"Mohammadi (on behalf of Sivers)" when a stand-in approved.

    The delegator's name is read back out of the event comment the engine
    wrote, so a PDF regenerated years later still shows who the signature was
    really for — even if the delegation row is long gone.
    """
    if not name or not comment or _ON_BEHALF_MARKER not in comment:
        return name
    delegator = comment.split(_ON_BEHALF_MARKER, 1)[1].strip()
    return f"{name} ({_ON_BEHALF_MARKER}{delegator})" if delegator else name
```

and in the list comprehension replace `"name": name` with
`"name": _signatory_name(name, ev.comment)`.

Note the interaction with `_is_machine_approval`: `"Auto-approved on behalf of
<role> — PMS migration"` is a machine marker and is filtered out *before* this
runs, so a PMS-migrated row cannot be mistaken for a delegated approval.

- [ ] **Step 4: Run the tests**

```bash
python -m pytest epms-api/tests/test_delegation_signatories.py epms-api/tests/test_signatories.py epms-api/tests/test_pdf_signatories.py -v
```

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/crud/signatories.py epms-api/tests/test_delegation_signatories.py
git commit -m "feat(epms): PDFs name the delegation on the signature line"
```

---

### Task 11: expense-api — OA inbox and approve gate

**Files:**
- Create: `expense-api/app/core/delegation.py`
- Modify: `expense-api/tests/conftest.py`
- Modify: `expense-api/app/api/v1/expenses.py` (`_can_act_on_claim` ~line 99, my-actions ~line 390)
- Test: `expense-api/tests/test_delegation_query.py`, `expense-api/tests/test_delegation_my_actions.py` (create)

`_can_act_on_claim` is imported by `pa.py` as well — its docstring flags it
load-bearing for both expense claims and PAs. Both paths must keep working.

- [ ] **Step 1: Add the shadow table**

Add `approval_delegations` to `expense-api/tests/conftest.py` using the same
raw DDL as Task 6, following this file's existing shadow-table block.

- [ ] **Step 2: Write the failing tests**

`test_delegation_query.py` — the same four boundary assertions as Tasks 3 and 6.

`test_delegation_my_actions.py`:

```python
# 1. Inside the window, the delegator's pinned approve task appears in the
#    delegate's my-actions.
# 2. A role-pool approve task for a role the delegator holds appears too.
# 3. Outside the window, neither appears.
# 4. _can_act_on_claim is True for the delegate inside the window and False
#    outside it.
# 5. _can_act_on_claim still behaves identically for a PA (the pa.py caller).
```

- [ ] **Step 3: Run and confirm failure**

- [ ] **Step 4: Create the sibling module**

`expense-api/app/core/delegation.py` — a copy of Task 3's module with the
sibling header naming the other two, plus a local `delegated_broadcast_roles`
mirroring Task 6's `delegated_broadcast_roles` (expense-api has no
`_PERSONAL_APPROVAL_ROLES`; use the literal `{"director", "supervisor"}` with a
comment pointing at `epms-api/app/crud/task.py` as the source of that set).

- [ ] **Step 5: Widen `_can_act_on_claim`**

After the existing own-identity and role-pool checks, before `return False`:

```python
    from app.core.delegation import active_delegator_ids, delegated_broadcast_roles
    delegator_ids = await active_delegator_ids(db, user_id)
    if delegator_ids:
        deleg_roles = await delegated_broadcast_roles(db, delegator_ids)
        for t in open_tasks:
            if not (t.type or "").startswith("approve"):
                continue
            if t.assigned_user_id is not None and t.assigned_user_id in delegator_ids:
                return True
            if t.assigned_user_id is None and t.assigned_role:
                assigned = t.assigned_role.lower()
                held = {r.lower() for r in deleg_roles}
                if "gm" in held or "opm" in held:
                    held.add("gm_or_opm")
                if assigned in held:
                    return True
```

- [ ] **Step 6: Widen my-actions**

In the `open_task_for_me` subquery, add the delegated arms:

```python
    delegator_ids = await active_delegator_ids(db, user_id)
    deleg_roles = {r.lower() for r in await delegated_broadcast_roles(db, delegator_ids)}
    if "gm" in deleg_roles or "opm" in deleg_roles:
        deleg_roles.add("gm_or_opm")
    arms = [
        TM.assigned_user_id == user_id,
        and_(TM.assigned_user_id.is_(None),
             func.lower(TM.assigned_role).in_(assigned_roles)),
    ]
    if delegator_ids:
        arms.append(TM.assigned_user_id.in_(delegator_ids))
        if deleg_roles:
            arms.append(and_(TM.assigned_user_id.is_(None),
                             func.lower(TM.assigned_role).in_(deleg_roles)))
    open_task_for_me = select(TM.document_id).where(
        TM.is_completed.is_(False), TM.type.like("approve_%"), or_(*arms))
```

The existing `TM.type.like("approve_%")` filter already restricts this query to
approval tasks, so no extra guard is needed here.

- [ ] **Step 7: Run the tests**

```bash
python -m pytest expense-api/tests/test_delegation_query.py expense-api/tests/test_delegation_my_actions.py expense-api/tests/test_my_actions_task_scope.py expense-api/tests/test_pa_permissions.py -v
```

- [ ] **Step 8: Commit**

```bash
git add expense-api/app/core/delegation.py expense-api/app/api/v1/expenses.py expense-api/tests/conftest.py expense-api/tests/test_delegation_query.py expense-api/tests/test_delegation_my_actions.py
git commit -m "feat(oa): delegated approval tasks reach the delegate in OA"
```

---

### Task 12: Portal Admin UI

**Files:**
- Create: `portal/src/pages/admin/ApprovalDelegation.tsx`
- Modify: `portal/src/services/adminApi.ts`, the Portal admin route table, and `navConfig`

**Interfaces:**
- Consumes: the Task 5 endpoints.

- [ ] **Step 1: Add the API client functions**

In `portal/src/services/adminApi.ts`, add typed wrappers for list / create /
update / revoke against `/approval/v1/delegations`. Follow how
`ApprovalRouting.tsx`'s client calls are declared in the same file.

- [ ] **Step 2: Build the page**

`portal/src/pages/admin/ApprovalDelegation.tsx`, modelled on
`portal/src/pages/admin/ApprovalRouting.tsx`:

- A table of current and future delegations: Delegator, Delegate, From, To, Note, Status, actions.
- "Add delegation" opens a form: delegator picker, delegate picker, start date, end date, note.
- A 409 response renders the server's message inline on the form rather than a toast, since the fix is to change the dates.
- Status is derived from the dates and `revoked_at`: `Scheduled` / `Active` / `Ended` / `Revoked`.
- Revoke asks for confirmation and explains it takes effect immediately.

Requirements to honour:

- **All copy in English.**
- Wrap the page in `PortalChromeLayout` like every other Portal page — no bare `div`.
- Use the shell `Button` component, not a raw `<button>`.
- Any dropdown or date popover must render through `createPortal` to `body` with fixed positioning, so it escapes `overflow` containers.
- Use the shared `formatDate` for display. **Never pass a date-only string to `new Date()`** — in UTC−4 that renders the previous day, and this page is entirely about dates.
- The user pickers must page through all users, not the default first 20 (`GET /users` defaults to `page_size=20`, max 200) — use the `listAll` helper the other admin pages use.

- [ ] **Step 3: Wire the route and nav entry**

Register the page in the Portal admin route table and add its `navConfig` entry
next to Approval Routing, gated on the same admin permission. Do not build a
second nav source — `navConfig` is the single source of truth.

- [ ] **Step 4: Typecheck and build**

```bash
cd c:/Project/uniops-delegation/portal
npx tsc --noEmit --listFiles | wc -l    # confirm it actually compiled files
npx tsc --noEmit
npm run build
```

`tsc` reporting zero errors while compiling zero files is a known false green
in this repo. Confirm the `--listFiles` count is in the thousands and that the
local `typescript` version is used, not a global fallback.

- [ ] **Step 5: Verify the page is reachable in the running app**

Building it is not evidence a user can get to it. Load Portal, sign in as an
admin, navigate to the new page from the nav, create a delegation, and see it
listed. Three times on this codebase a feature was "built correctly" but
unreachable.

- [ ] **Step 6: Commit**

```bash
git add portal/src/pages/admin/ApprovalDelegation.tsx portal/src/services/adminApi.ts
git commit -m "feat(portal): Approval Delegation admin page"
```

---

## Deployment notes

- One migration: approval-api `0002_approval_delegations`. Run `migrate-prod.sh` before rolling containers. It creates `btree_gist` if absent (already present in production).
- Rebuild and push: `approval-api`, `epms-api`, `expense-api`, `portal-web`. Retag the rest to the same sha and push all of them — the release convention is that every service image carries the same tag.
- Before building `portal-web`, source `.env.prod.example` and assert every `VITE_*` is non-empty, then verify the built image has the right domain baked in. A missing build arg silently falls back to `localhost`; this has caused a site-wide outage four times.
- Nothing to backfill: an empty `approval_delegations` table makes every widened predicate behave exactly as it does today.
- **Pushing images is not deploying.** After deploying, curl the live
  `/assets/index-*.js` fingerprint to confirm the running version actually changed.

## Self-review notes

- Spec coverage: every change point 1-13 in the spec maps to a task (1 → Task 1; 2-3 → Task 4; 4 → Task 5; 5-6 → Task 7; 7 → Task 8; 8-9 → Task 9; 10 → Task 10; 11-12 → Task 11; 13 → Task 12). The data model maps to Task 2, the shared predicate to Tasks 3/6/11.
- The spec's security boundaries each have a named test: admin-not-inherited (Task 4), no non-approval permissions (Tasks 7 and 11 restrict to `approve%`), non-transitive (Task 3).
- One spec statement is refined here: the spec said epms/expense "read it read-only", and this plan pins that to raw SQL plus a conftest shadow table, matching how those services already read `approval_dept_routing`.
- One decision was made while planning and is flagged in Task 4: the auto-skip walk uses own identity only, so a delegate does not auto-skip steps reached solely through delegation.
