# OA Travel Application (TRA) + TRV Reimbursement Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Travel Application" (TRA) pre-trip approval form to the OA module, and gate travel-expense reimbursement (TRV) so it must reference an approved TRA on which the reimbursing user is a listed traveler.

**Architecture:** TRA is a new `claim_type` reusing the existing `expense_claims` table + `approval-api` workflow engine + Task Inbox + attachments. TRA carries no money (totals stay 0). It adds a travelers child table, transport-mode checkboxes, and leave dates. The approval chain is `dept_manager → finance_manager → gm`, auto-seeded into `company_config.workflow_defs`. TRV gains a `travel_application_id` reference enforced at create time.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / Alembic / Pydantic v2 (backend); React 18 / TypeScript / TanStack Query / Vite / Tailwind (frontend); ReportLab (new PDF dep in expense-api).

**Spec:** `docs/superpowers/specs/2026-08-03-oa-travel-application-design.md`

## Global Constraints

- **Branch/worktree:** all work in `C:/Project/uniops-travel-app` on `feature/oa-travel-application` (based on `origin/main` = 6beac58). One session, one branch — do not touch other worktrees or `main`.
- **Alembic new migration:** `down_revision = "0011_invoice_line_unit"` (verified current head). New `revision = "0012_travel_application"`. Verify with `cd expense-api && python -m alembic heads` before and after.
- **expense-api tests:** test DB is `expense_test`. Run once: `cd expense-api && python -m scripts.create_test_db`. The host `.env` points at PRODUCTION — override DB env before pytest: `POSTGRES_HOST=localhost POSTGRES_PASSWORD=<local uniops_postgres pw> python -m pytest`. Run **one suite at a time** (suites `drop_all`/`create_all` the shared test DB). Command: `cd expense-api && python -m pytest tests/<file> -v`.
- **approval-api tests:** use the `engine_db_session` fixture (test DB `approval_test`). Command: `cd approval-api && python -m pytest tests/<file> -v`.
- **UI copy:** user-facing text is **English only** (comments may be Chinese). PDF labels English only.
- **oa API calls:** the `api` client does NOT auto-prefix — pass full `/api/v1/...` paths. `Decimal` fields arrive as JSON strings → wrap with `Number()` before math/`.toFixed()`.
- **oa build gate:** oa has no unit-test runner; the verification gate for every frontend task is `cd oa && npm run build` (`tsc -b && vite build`) passing with no new errors. Capture the error count before starting as the baseline.
- **Overlays:** dropdowns/popovers that can be clipped must use `createPortal(..., document.body)` with `position:fixed` (copy the `booking/src/components/AttendeePicker.tsx` pattern).

---

## File Structure

**expense-api (backend)**
- `app/models/expense.py` — MODIFY: add columns to `ExpenseClaim`; new `ExpenseTraveler` model + `travelers` relationship.
- `alembic/versions/0012_travel_application.py` — CREATE: columns + `expense_travelers` table.
- `app/schemas/expense.py` — MODIFY: traveler schemas + new claim fields.
- `app/crud/expense.py` — MODIFY: TRA branch, TRV-gate helper, eligible-list + directory query, `get_by_id` eager-load travelers.
- `app/api/v1/expenses.py` — MODIFY: allow `TRA`, TRV gate, `_action_key`/`_workflow_key`/`_INBOX_STEP_ROLES`.
- `app/api/v1/travel.py` — CREATE: `GET /travel-applications/eligible`, `GET /users/directory`.
- `app/services/pdf_tra.py` — CREATE: English ReportLab PDF for the travel application.
- `app/api/v1/expense_attachments.py` — MODIFY: add `POST /{claim_id}/regenerate-pdf` (or new route in `travel.py`).
- `app/main.py` — MODIFY: register `travel_router`.
- `requirements.txt` — MODIFY: add `reportlab==4.2.5`.

**approval-api (backend)**
- `app/crud/engine.py` — MODIFY: `_DOC_META["tra"]`, `_WORKFLOW_DEFAULTS["tra"]`, `_post_approve_tra`, `_POST_APPROVE["tra"]`, module docstring.

**portal (admin)**
- `src/pages/admin/AdminPanel.tsx` — MODIFY: add `tra` to `ActionKey`, `ACTION_KEYS`, `ACTION_LABELS`, `WORKFLOW_DEFAULTS`.

**oa (frontend)**
- `src/components/TravelerPicker.tsx` — CREATE: multi-select user picker (adapted from `booking/AttendeePicker.tsx`).
- `src/pages/travel/TravelApplicationsListPage.tsx` — CREATE.
- `src/pages/travel/TraCreatePage.tsx` — CREATE.
- `src/pages/expenses/ExpenseDetailPage.tsx` — MODIFY: TRA branch (travelers, transport, leave, linked TRVs, PDF button).
- `src/pages/expenses/TrvCreatePage.tsx` — MODIFY: required Travel Application selector + auto-fill.
- `src/app/routes.tsx` — MODIFY: `/travel`, `/travel/new` routes.

---

## Task 1: Data model + migration (expense-api)

**Files:**
- Modify: `expense-api/app/models/expense.py`
- Create: `expense-api/alembic/versions/0012_travel_application.py`
- Test: `expense-api/tests/test_travel_application_model.py`

**Interfaces:**
- Produces: `ExpenseClaim.transport_modes: list[str]`, `ExpenseClaim.leave_from_date/leave_to_date: date|None`, `ExpenseClaim.travel_application_id: uuid|None`, `ExpenseClaim.travelers: list[ExpenseTraveler]`; `ExpenseTraveler(claim_id, user_id, user_name, seq)`.

- [ ] **Step 1: Write the failing test**

```python
# expense-api/tests/test_travel_application_model.py
import uuid
from datetime import date
import pytest
from sqlalchemy import select
from app.models.expense import ExpenseClaim, ExpenseTraveler


async def test_tra_claim_persists_travelers_and_transport(db_session):
    claim = ExpenseClaim(
        claim_number="TRA-20260803-0001", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="Alice",
        department_name="Ops", submission_date=date(2026, 8, 3),
        transport_modes=["airplane", "accommodation"],
        leave_from_date=date(2026, 8, 4), leave_to_date=date(2026, 8, 6),
        status="draft", created_by=uuid.uuid4(),
    )
    db_session.add(claim)
    await db_session.flush()
    db_session.add(ExpenseTraveler(
        claim_id=claim.id, user_id=uuid.uuid4(), user_name="Bob", seq=0))
    await db_session.flush()
    await db_session.refresh(claim, ["travelers"])
    assert claim.transport_modes == ["airplane", "accommodation"]
    assert len(claim.travelers) == 1
    assert claim.travelers[0].user_name == "Bob"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_application_model.py -v`
Expected: FAIL — `AttributeError: transport_modes` / `ImportError: ExpenseTraveler`.

- [ ] **Step 3: Add columns + model in `app/models/expense.py`**

In the imports, ensure `JSONB` is available:
```python
from sqlalchemy.dialects.postgresql import UUID, JSONB
```
Add to `ExpenseClaim` (after the `# TRV-specific` block, before `# Financials`):
```python
    # TRA-specific (Travel Application)
    transport_modes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    leave_from_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    leave_to_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # TRV → approved TRA reference (self-referential within expense_claims)
    travel_application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_claims.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
```
Add the relationship next to the other relationships on `ExpenseClaim`:
```python
    travelers: Mapped[list["ExpenseTraveler"]] = relationship(
        "ExpenseTraveler", back_populates="claim", cascade="all, delete-orphan",
        order_by="ExpenseTraveler.seq",
    )
```
Add the new model after `ExpenseTripItem`:
```python
class ExpenseTraveler(UUIDPrimaryKey, Base):
    """Traveler roster for TRA (Travel Application). Members may reimburse against it."""
    __tablename__ = "expense_travelers"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_claims.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    claim: Mapped["ExpenseClaim"] = relationship("ExpenseClaim", back_populates="travelers")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_application_model.py -v`
Expected: PASS (conftest `create_all` builds the new table from the model).

- [ ] **Step 5: Write the Alembic migration**

Confirm head first: `cd expense-api && python -m alembic heads` → expect `0011_invoice_line_unit (head)`.
```python
# expense-api/alembic/versions/0012_travel_application.py
"""travel application (TRA): traveler roster, transport modes, leave dates, TRV ref

Revision ID: 0012_travel_application
Revises: 0011_invoice_line_unit
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0012_travel_application"
down_revision = "0011_invoice_line_unit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expense_claims", sa.Column(
        "transport_modes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("expense_claims", sa.Column("leave_from_date", sa.Date(), nullable=True))
    op.add_column("expense_claims", sa.Column("leave_to_date", sa.Date(), nullable=True))
    op.add_column("expense_claims", sa.Column(
        "travel_application_id", UUID(as_uuid=True),
        sa.ForeignKey("expense_claims.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_expense_claims_travel_application_id",
                    "expense_claims", ["travel_application_id"])
    op.create_table(
        "expense_travelers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("claim_id", UUID(as_uuid=True),
                  sa.ForeignKey("expense_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_expense_travelers_claim_id", "expense_travelers", ["claim_id"])
    op.create_index("ix_expense_travelers_user_id", "expense_travelers", ["user_id"])


def downgrade() -> None:
    op.drop_table("expense_travelers")
    op.drop_index("ix_expense_claims_travel_application_id", "expense_claims")
    op.drop_column("expense_claims", "travel_application_id")
    op.drop_column("expense_claims", "leave_to_date")
    op.drop_column("expense_claims", "leave_from_date")
    op.drop_column("expense_claims", "transport_modes")
```

- [ ] **Step 6: Verify migration applies on a scratch DB**

Run: `cd expense-api && python -m alembic heads` → expect `0012_travel_application (head)`.
Run against the test DB to catch SQL errors: `POSTGRES_HOST=localhost python -m alembic upgrade head` then `python -m alembic downgrade -1` then `python -m alembic upgrade head`.
Expected: no errors; head is `0012_travel_application`.

- [ ] **Step 7: Commit**

```bash
git add expense-api/app/models/expense.py expense-api/alembic/versions/0012_travel_application.py expense-api/tests/test_travel_application_model.py
git commit -m "feat(expense): TRA data model — travelers, transport modes, leave dates, TRV ref"
```

---

## Task 2: Schemas (expense-api)

**Files:**
- Modify: `expense-api/app/schemas/expense.py`
- Test: `expense-api/tests/test_travel_application_schema.py`

**Interfaces:**
- Consumes: models from Task 1.
- Produces: `TravelerCreate{user_id, user_name, seq}`, `TravelerResponse(+id)`; `ExpenseClaimCreate` gains `travelers, transport_modes, leave_from_date, leave_to_date, travel_application_id`; `ExpenseClaimResponse` gains the same + `travelers: list[TravelerResponse]`.

- [ ] **Step 1: Write the failing test**

```python
# expense-api/tests/test_travel_application_schema.py
import uuid
from datetime import date
from app.schemas.expense import ExpenseClaimCreate, ExpenseClaimResponse


def test_tra_create_schema_accepts_travelers_and_transport():
    payload = ExpenseClaimCreate(
        claim_type="TRA", submission_date=date(2026, 8, 3),
        purpose="Client visit", notes="Driver Sam (external) tags along",
        travel_destination="Toronto",
        travel_from_date=date(2026, 8, 4), travel_to_date=date(2026, 8, 6),
        transport_modes=["airplane", "meal"],
        leave_from_date=date(2026, 8, 4), leave_to_date=date(2026, 8, 6),
        travelers=[{"user_id": uuid.uuid4(), "user_name": "Alice", "seq": 0}],
    )
    assert payload.transport_modes == ["airplane", "meal"]
    assert payload.travelers[0].user_name == "Alice"


def test_trv_create_schema_accepts_travel_application_id():
    payload = ExpenseClaimCreate(
        claim_type="TRV", submission_date=date(2026, 8, 7),
        travel_application_id=uuid.uuid4(), line_items=[],
    )
    assert payload.travel_application_id is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_application_schema.py -v`
Expected: FAIL — `ExpenseClaimCreate` has no `travelers`/`transport_modes` fields (Pydantic ignores or errors).

- [ ] **Step 3: Add schemas**

Add before `ExpenseClaimCreate`:
```python
# ── Traveler (TRA) ────────────────────────────────────────────────────────────

class TravelerCreate(BaseModel):
    user_id: uuid.UUID
    user_name: str
    seq: int = 0


class TravelerResponse(TravelerCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
```
Add these fields to `ExpenseClaimCreate` (after `travel_destination`):
```python
    # TRA only
    transport_modes: list[str] = []
    leave_from_date: Optional[date] = None
    leave_to_date: Optional[date] = None
    travelers: list[TravelerCreate] = []
    # TRV only — reference to an approved TRA
    travel_application_id: Optional[uuid.UUID] = None
```
Add the same five fields to `ExpenseClaimUpdate`.
Add to `ExpenseClaimResponse` (after `travel_destination`):
```python
    transport_modes: list[str] = []
    leave_from_date: Optional[date] = None
    leave_to_date: Optional[date] = None
    travel_application_id: Optional[uuid.UUID] = None
    travelers: list[TravelerResponse] = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_application_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add expense-api/app/schemas/expense.py expense-api/tests/test_travel_application_schema.py
git commit -m "feat(expense): TRA/TRV schemas — travelers, transport, leave, travel_application_id"
```

---

## Task 3: create_claim TRA branch + endpoint allow-list (expense-api)

**Files:**
- Modify: `expense-api/app/crud/expense.py`
- Modify: `expense-api/app/api/v1/expenses.py:233` (allowed tuple)
- Test: `expense-api/tests/test_travel_application_create.py`

**Interfaces:**
- Consumes: schemas (Task 2), models (Task 1).
- Produces: creating a claim with `claim_type="TRA"` persists travelers + transport + leave and keeps totals 0; number format `TRA-YYYYMMDD-0001`.

- [ ] **Step 1: Write the failing test**

The suite drives the API. Model on `tests/test_mileage_totals.py` for the authed client fixture (`client` + auth headers). Use the existing fixture names from conftest.
```python
# expense-api/tests/test_travel_application_create.py
import uuid
from datetime import date


async def test_create_tra_persists_travelers_and_zero_totals(client, auth_headers):
    traveler_id = str(uuid.uuid4())
    body = {
        "claim_type": "TRA", "submission_date": "2026-08-03",
        "purpose": "Supplier audit", "travel_destination": "Montreal",
        "travel_from_date": "2026-08-04", "travel_to_date": "2026-08-06",
        "transport_modes": ["airplane", "accommodation"],
        "travelers": [{"user_id": traveler_id, "user_name": "Alice", "seq": 0}],
    }
    r = await client.post("/api/v1/expenses", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["claim_number"].startswith("TRA-")
    assert data["total_amount"] == "0.00"
    assert data["transport_modes"] == ["airplane", "accommodation"]
    assert len(data["travelers"]) == 1
    assert data["travelers"][0]["user_name"] == "Alice"
```
> If `auth_headers`/`client` fixtures have different names, copy the exact fixture usage from `tests/test_pa.py` (top of file).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_application_create.py -v`
Expected: FAIL — endpoint rejects `TRA` with 400 `claim_type must be one of ...`.

- [ ] **Step 3: Allow TRA in the create endpoint**

In `expense-api/app/api/v1/expenses.py` `create_expense`:
```python
    allowed = ("EXP", "MIL", "TRV", "TRA")
```

- [ ] **Step 4: Persist TRA fields + travelers in `create_claim`**

In `expense-api/app/crud/expense.py`, add the new columns to the `ExpenseClaim(...)` constructor (so all types carry them; defaults keep other types unaffected):
```python
        travel_destination=data.travel_destination,
        transport_modes=data.transport_modes,
        leave_from_date=data.leave_from_date,
        leave_to_date=data.leave_to_date,
        travel_application_id=data.travel_application_id,
        status="draft",
```
Add the TRA branch after the `CFM` branch:
```python
    elif data.claim_type == "TRA":
        # Travel Application: no money; persist traveler roster. Totals stay 0.
        for i, tr in enumerate(data.travelers):
            db.add(ExpenseTraveler(
                claim_id=claim.id, user_id=tr.user_id,
                user_name=tr.user_name, seq=tr.seq if tr.seq is not None else i))
        await db.flush()
```
Import `ExpenseTraveler` at the top of the file:
```python
from app.models.expense import (
    ExpenseApprovalEvent, ExpenseAttachment, ExpenseClaim,
    ExpenseLineItem, ExpenseTraveler, ExpenseTripItem,
)
```
Add `"travelers"` to the final refresh so the response includes them:
```python
    await db.flush()
    await db.refresh(claim, ["line_items", "trip_items", "attachments", "approval_events", "travelers"])
    return claim
```
Also add `selectinload(ExpenseClaim.travelers)` to `get_by_id`:
```python
        .options(
            selectinload(ExpenseClaim.line_items),
            selectinload(ExpenseClaim.trip_items),
            selectinload(ExpenseClaim.attachments),
            selectinload(ExpenseClaim.approval_events),
            selectinload(ExpenseClaim.travelers),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_application_create.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add expense-api/app/crud/expense.py expense-api/app/api/v1/expenses.py expense-api/tests/test_travel_application_create.py
git commit -m "feat(expense): create TRA claims — persist travelers, transport, leave dates"
```

---

## Task 4: TRV reimbursement gate (expense-api)

**Files:**
- Modify: `expense-api/app/api/v1/expenses.py` (`create_expense`)
- Test: `expense-api/tests/test_trv_travel_application_gate.py`

**Interfaces:**
- Consumes: `expense_crud.get_by_id` (loads `travelers`), TRA claims from Task 3.
- Produces: creating a `TRV` claim requires `travel_application_id` → an approved TRA on which the current user is a traveler; otherwise 400/404/403.

- [ ] **Step 1: Write the failing test**

```python
# expense-api/tests/test_trv_travel_application_gate.py
import uuid


async def _make_approved_tra(db_session, traveler_id):
    from datetime import date
    from app.models.expense import ExpenseClaim, ExpenseTraveler
    tra = ExpenseClaim(
        claim_number=f"TRA-TEST-{uuid.uuid4().hex[:4]}", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="Creator", department_name="Ops",
        submission_date=date(2026, 8, 3), status="approved", created_by=uuid.uuid4())
    db_session.add(tra)
    await db_session.flush()
    db_session.add(ExpenseTraveler(claim_id=tra.id, user_id=traveler_id, user_name="Me", seq=0))
    await db_session.flush()
    return tra


def _trv_body(app_id=None):
    return {"claim_type": "TRV", "submission_date": "2026-08-07",
            "travel_application_id": str(app_id) if app_id else None, "line_items": []}


async def test_trv_without_application_rejected(client, auth_headers):
    r = await client.post("/api/v1/expenses", json=_trv_body(None), headers=auth_headers)
    assert r.status_code == 400


async def test_trv_with_unapproved_application_rejected(client, auth_headers, db_session, current_user_id):
    from datetime import date
    from app.models.expense import ExpenseClaim, ExpenseTraveler
    tra = ExpenseClaim(claim_number=f"TRA-TEST-{uuid.uuid4().hex[:4]}", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="C", department_name="Ops",
        submission_date=date(2026, 8, 3), status="submitted", created_by=uuid.uuid4())
    db_session.add(tra); await db_session.flush()
    db_session.add(ExpenseTraveler(claim_id=tra.id, user_id=current_user_id, user_name="Me", seq=0))
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_trv_body(tra.id), headers=auth_headers)
    assert r.status_code == 400


async def test_trv_when_user_not_traveler_forbidden(client, auth_headers, db_session):
    tra = await _make_approved_tra(db_session, traveler_id=uuid.uuid4())  # someone else
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_trv_body(tra.id), headers=auth_headers)
    assert r.status_code == 403


async def test_trv_valid_application_allows_create(client, auth_headers, db_session, current_user_id):
    tra = await _make_approved_tra(db_session, traveler_id=current_user_id)
    await db_session.commit()
    r = await client.post("/api/v1/expenses", json=_trv_body(tra.id), headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["travel_application_id"] == str(tra.id)
```
> `current_user_id` must equal the `sub` in `auth_headers`. If conftest lacks a `current_user_id` fixture, add one that returns the same UUID used to mint the test JWT (see conftest's token creation), or read it from the decoded token.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_trv_travel_application_gate.py -v`
Expected: FAIL — no gate; `test_trv_without_application_rejected` gets 201 instead of 400.

- [ ] **Step 3: Add the gate to `create_expense`**

In `expense-api/app/api/v1/expenses.py`, inside `create_expense`, after computing `user_id` and before calling `create_claim`:
```python
    # TRV reimbursement gate: must reference an APPROVED TRA the user travels on.
    if body.claim_type == "TRV":
        if not body.travel_application_id:
            raise HTTPException(status_code=400,
                detail="A Travel Application is required for travel expense claims")
        tra = await expense_crud.get_by_id(db, body.travel_application_id)
        if not tra or tra.claim_type != "TRA":
            raise HTTPException(status_code=404, detail="Travel Application not found")
        if tra.status != "approved":
            raise HTTPException(status_code=400,
                detail="The selected Travel Application is not approved yet")
        if user_id not in {t.user_id for t in tra.travelers}:
            raise HTTPException(status_code=403,
                detail="You are not listed as a traveler on this Travel Application")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_trv_travel_application_gate.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add expense-api/app/api/v1/expenses.py expense-api/tests/test_trv_travel_application_gate.py
git commit -m "feat(expense): gate TRV creation behind an approved Travel Application"
```

---

## Task 5: Read endpoints — eligible TRAs + user directory (expense-api)

**Files:**
- Create: `expense-api/app/api/v1/travel.py`
- Modify: `expense-api/app/main.py` (register router)
- Modify: `expense-api/app/crud/expense.py` (add `list_eligible_travel_apps`)
- Test: `expense-api/tests/test_travel_read_endpoints.py`

**Interfaces:**
- Produces: `GET /api/v1/travel-applications/eligible` → `list[{id, claim_number, travel_destination, travel_from_date, travel_to_date, purpose}]` (approved TRAs where current user is a traveler). `GET /api/v1/users/directory?q=<str>` → `list[{id, full_name, email}]` (active users matching name/email, limit 20).

- [ ] **Step 1: Write the failing test**

```python
# expense-api/tests/test_travel_read_endpoints.py
import uuid
from datetime import date
from app.models.expense import ExpenseClaim, ExpenseTraveler


async def test_eligible_lists_only_approved_tra_where_user_travels(
        client, auth_headers, db_session, current_user_id):
    approved = ExpenseClaim(claim_number="TRA-E-1", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="C", department_name="Ops",
        submission_date=date(2026, 8, 3), travel_destination="Calgary",
        status="approved", created_by=uuid.uuid4())
    other = ExpenseClaim(claim_number="TRA-E-2", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="C", department_name="Ops",
        submission_date=date(2026, 8, 3), status="approved", created_by=uuid.uuid4())
    db_session.add_all([approved, other]); await db_session.flush()
    db_session.add(ExpenseTraveler(claim_id=approved.id, user_id=current_user_id, user_name="Me", seq=0))
    db_session.add(ExpenseTraveler(claim_id=other.id, user_id=uuid.uuid4(), user_name="X", seq=0))
    await db_session.commit()
    r = await client.get("/api/v1/travel-applications/eligible", headers=auth_headers)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert str(approved.id) in ids and str(other.id) not in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_read_endpoints.py -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Add `list_eligible_travel_apps` to crud**

In `expense-api/app/crud/expense.py`:
```python
async def list_eligible_travel_apps(db: AsyncSession, user_id: uuid.UUID) -> list[ExpenseClaim]:
    """Approved TRAs on which `user_id` is a listed traveler (for the TRV picker)."""
    q = (
        select(ExpenseClaim)
        .join(ExpenseTraveler, ExpenseTraveler.claim_id == ExpenseClaim.id)
        .where(ExpenseClaim.claim_type == "TRA",
               ExpenseClaim.status == "approved",
               ExpenseTraveler.user_id == user_id)
        .order_by(ExpenseClaim.travel_from_date.desc().nullslast(),
                  ExpenseClaim.created_at.desc())
    )
    return list((await db.execute(q)).scalars().unique().all())
```

- [ ] **Step 4: Create the router `app/api/v1/travel.py`**

```python
"""Travel-application read endpoints: eligible TRAs for the TRV picker, user directory."""
import uuid
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.core.deps import CurrentUserDep, SessionDep
from app.crud import expense as expense_crud

router = APIRouter(tags=["travel"])


class EligibleTravelApp(BaseModel):
    id: uuid.UUID
    claim_number: str
    travel_destination: str | None = None
    travel_from_date: str | None = None
    travel_to_date: str | None = None
    purpose: str | None = None


@router.get("/travel-applications/eligible", response_model=list[EligibleTravelApp])
async def eligible_travel_apps(db: SessionDep, user: CurrentUserDep):
    user_id = uuid.UUID(user["sub"])
    rows = await expense_crud.list_eligible_travel_apps(db, user_id)
    return [EligibleTravelApp(
        id=c.id, claim_number=c.claim_number, travel_destination=c.travel_destination,
        travel_from_date=c.travel_from_date.isoformat() if c.travel_from_date else None,
        travel_to_date=c.travel_to_date.isoformat() if c.travel_to_date else None,
        purpose=c.purpose) for c in rows]


class DirectoryUser(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None = None


@router.get("/users/directory", response_model=list[DirectoryUser])
async def user_directory(db: SessionDep, _: CurrentUserDep, q: str = ""):
    """Search active users by name/email (shared users table). Limit 20."""
    like = f"%{q.strip()}%"
    rows = (await db.execute(text(
        "SELECT id, full_name, email FROM users "
        "WHERE is_active = true AND (full_name ILIKE :like OR email ILIKE :like) "
        "ORDER BY full_name LIMIT 20"), {"like": like})).all()
    return [DirectoryUser(id=r[0], full_name=r[1] or "", email=r[2]) for r in rows]
```
> If the `users` table has no `email` column, drop it from the SELECT and set `email=None`. Verify with `\d users` on the dev DB before implementing.

- [ ] **Step 5: Register the router in `app/main.py`**

Add near the other imports and includes:
```python
from app.api.v1.travel import router as travel_router
...
    app.include_router(travel_router, prefix="/api/v1")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_read_endpoints.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add expense-api/app/api/v1/travel.py expense-api/app/main.py expense-api/app/crud/expense.py expense-api/tests/test_travel_read_endpoints.py
git commit -m "feat(expense): eligible-TRA + user-directory read endpoints"
```

---

## Task 6: action_key / workflow mappings for TRA (expense-api)

**Files:**
- Modify: `expense-api/app/api/v1/expenses.py` (`_action_key`, `_BASE_WF_KEY`, `_INBOX_STEP_ROLES`)
- Test: `expense-api/tests/test_travel_action_key.py`

**Interfaces:**
- Produces: `_action_key("TRA") == "tra"`; `_workflow_key("TRA") == "tra"`; `_INBOX_STEP_ROLES` includes `gm` at the TRA GM step.

- [ ] **Step 1: Write the failing test**

```python
# expense-api/tests/test_travel_action_key.py
from app.api.v1.expenses import _action_key, _workflow_key


def test_tra_maps_to_tra_action_and_workflow():
    assert _action_key("TRA") == "tra"
    assert _workflow_key("TRA") == "tra"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_action_key.py -v`
Expected: FAIL — `_action_key("TRA")` falls through to `"cfm"`.

- [ ] **Step 3: Add the mappings**

In `expense-api/app/api/v1/expenses.py`:
```python
    mapping = {"EXP": "exp", "MIL": "mil", "TRV": "trv", "TRA": "tra"}
```
```python
_BASE_WF_KEY = {"EXP": "exp", "MIL": "mil", "TRV": "trv", "TRA": "tra"}
```
Extend `_INBOX_STEP_ROLES` so the GM step surfaces in `/my-actions` (TRA chain is dept_manager→finance_manager→gm):
```python
_INBOX_STEP_ROLES: dict[int, set[str]] = {
    0: {"dept_manager", "system_admin"},
    1: {"finance_bp", "finance_manager", "system_admin"},
    2: {"finance_manager", "gm", "system_admin"},
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_travel_action_key.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add expense-api/app/api/v1/expenses.py expense-api/tests/test_travel_action_key.py
git commit -m "feat(expense): route TRA claims to the 'tra' approval workflow"
```

---

## Task 7: Approval engine wiring for TRA (approval-api)

**Files:**
- Modify: `approval-api/app/crud/engine.py`
- Test: `approval-api/tests/test_engine_tra.py`

**Interfaces:**
- Consumes: `_expense_meta`, `ExpenseClaim`, `Task` (already in engine.py).
- Produces: `_DOC_META["tra"]`, `_WORKFLOW_DEFAULTS["tra"] = [dept_manager, finance_manager, gm]`, `_post_approve_tra` (no-op — no reimbursement task), `_POST_APPROVE["tra"] = _post_approve_tra`.

- [ ] **Step 1: Write the failing test**

```python
# approval-api/tests/test_engine_tra.py
from app.crud import engine


def test_tra_registered_with_gm_chain_and_noop_post_approve():
    assert "tra" in engine._DOC_META
    assert engine._DOC_META["tra"]["task_approve"] == "approve_tra"
    roles = [s["role"] for s in engine._WORKFLOW_DEFAULTS["tra"]]
    assert roles == ["dept_manager", "finance_manager", "gm"]
    # TRA must NOT reuse the expense reimbursement post-approve (would spawn a
    # bogus finance_bp "process reimbursement" task for a non-financial doc).
    assert engine._POST_APPROVE["tra"] is engine._post_approve_tra
    assert engine._POST_APPROVE["tra"] is not engine._post_approve_exp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd approval-api && python -m pytest tests/test_engine_tra.py -v`
Expected: FAIL — `KeyError: 'tra'` / `_post_approve_tra` undefined.

- [ ] **Step 3: Wire TRA into the engine**

In `approval-api/app/crud/engine.py`:

Add to `_DOC_META` next to the other expense claims:
```python
    "trv": _expense_meta("TRV"),
    "tra": _expense_meta("TRA"),
    "cfm": _expense_meta("CFM"),
```
Add to `_WORKFLOW_DEFAULTS` (after the `trv` entry):
```python
    "tra": [
        {"id": "dept_manager", "role": "dept_manager",    "label": "Department Manager"},
        {"id": "finance_mgr",  "role": "finance_manager", "label": "Finance Manager"},
        {"id": "gm",           "role": "gm",              "label": "General Manager"},
    ],
```
Add the no-op post-approve callback next to `_post_approve_exp`:
```python
async def _post_approve_tra(db: AsyncSession, claim: ExpenseClaim) -> None:
    """Travel Application approved: no reimbursement, no money, no task. The
    approval itself unlocks TRV creation for the roster (enforced in expense-api).
    Deliberately a no-op — do NOT reuse _post_approve_exp, which would create a
    finance_bp reimbursement task for a non-financial document."""
    return None
```
Register it in `_POST_APPROVE`:
```python
    "trv":    _post_approve_exp,
    "tra":    _post_approve_tra,
    "cfm":    _post_approve_exp,
```
Update the module docstring action-key list (line ~3) to include `tra`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd approval-api && python -m pytest tests/test_engine_tra.py -v`
Expected: PASS.

- [ ] **Step 5: Sanity-check the seed picks up `tra`**

The startup `seed_default_workflows()` fills missing keys from `_WORKFLOW_DEFAULTS`, so `company_config.workflow_defs["tra"]` is created automatically on next approval-api boot. No code change needed — just confirm `"tra"` is now in `_WORKFLOW_DEFAULTS`.

- [ ] **Step 6: Commit**

```bash
git add approval-api/app/crud/engine.py approval-api/tests/test_engine_tra.py
git commit -m "feat(approval): register TRA workflow (dept_manager->finance_manager->gm), no-op post-approve"
```

---

## Task 8: Travel Application PDF + regenerate endpoint (expense-api)

**Files:**
- Modify: `expense-api/requirements.txt`
- Create: `expense-api/app/services/pdf_tra.py`
- Modify: `expense-api/app/api/v1/travel.py` (add regenerate route)
- Test: `expense-api/tests/test_pdf_tra.py`

**Interfaces:**
- Consumes: `ExpenseClaim` with `travelers`, `approval_events`.
- Produces: `build_travel_application_pdf(claim) -> bytes` (starts with `%PDF`); `POST /api/v1/travel-applications/{claim_id}/pdf` regenerates + attaches.

- [ ] **Step 1: Add the dependency**

Append to `expense-api/requirements.txt`:
```
reportlab==4.2.5
```
Install: `cd expense-api && pip install reportlab==4.2.5`.

- [ ] **Step 2: Write the failing test**

```python
# expense-api/tests/test_pdf_tra.py
import uuid
from datetime import date
from app.models.expense import ExpenseClaim, ExpenseTraveler
from app.services.pdf_tra import build_travel_application_pdf


def test_build_travel_application_pdf_returns_pdf_bytes():
    claim = ExpenseClaim(
        id=uuid.uuid4(), claim_number="TRA-20260803-0001", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="Alice", department_name="Operations",
        submission_date=date(2026, 8, 3), travel_destination="Toronto",
        travel_from_date=date(2026, 8, 4), travel_to_date=date(2026, 8, 6),
        purpose="Supplier audit", notes="Driver Sam (external) accompanies",
        transport_modes=["airplane", "accommodation", "meal"],
        leave_from_date=date(2026, 8, 4), leave_to_date=date(2026, 8, 6),
        status="approved", created_by=uuid.uuid4())
    claim.travelers = [ExpenseTraveler(user_id=uuid.uuid4(), user_name="Alice", seq=0),
                       ExpenseTraveler(user_id=uuid.uuid4(), user_name="Bob", seq=1)]
    claim.approval_events = []
    data = build_travel_application_pdf(claim)
    assert isinstance(data, (bytes, bytearray))
    assert data[:4] == b"%PDF"
    assert len(data) > 800
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_pdf_tra.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.pdf_tra`.

- [ ] **Step 4: Implement `app/services/pdf_tra.py`**

English labels; Helvetica for labels; register ReportLab's built-in CID font `STSong-Light` and use it for user-entered data fields so Chinese data doesn't render as tofu boxes.
```python
"""English-only Travel Application PDF (ReportLab). Labels Helvetica; data fields
use the built-in CID font STSong-Light so any Chinese in user data still renders."""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)

_CJK = "STSong-Light"
try:
    pdfmetrics.registerFont(UnicodeCIDFont(_CJK))
except Exception:  # font already registered / unavailable → fall back to Helvetica
    _CJK = "Helvetica"

_LABEL = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#334155"))
_DATA = ParagraphStyle("data", fontName=_CJK, fontSize=9, textColor=colors.black, leading=12)
_H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, spaceAfter=2)
_MODES = [("airplane", "Airplane"), ("train", "Train"), ("ship", "Ship"),
          ("car", "Car"), ("accommodation", "Accommodation"), ("meal", "Meal"), ("other", "Other")]


def _row(label, value):
    return [Paragraph(label, _LABEL), Paragraph(value or "—", _DATA)]


def build_travel_application_pdf(claim) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    els = [Paragraph("Application of Travel", _H1),
           Paragraph(f"No. {claim.claim_number}", _DATA), Spacer(1, 8)]

    travelers = ", ".join(t.user_name for t in (claim.travelers or []))
    dates = ""
    if claim.travel_from_date and claim.travel_to_date:
        dates = f"{claim.travel_from_date.isoformat()} → {claim.travel_to_date.isoformat()}"
    leave = ""
    if claim.leave_from_date and claim.leave_to_date:
        leave = f"{claim.leave_from_date.isoformat()} → {claim.leave_to_date.isoformat()}"
    chosen = set(claim.transport_modes or [])
    transport = "  ".join(f"[{'X' if k in chosen else ' '}] {lbl}" for k, lbl in _MODES)

    info = Table([
        _row("Department", claim.department_name),
        _row("Time of Application", claim.submission_date.isoformat() if claim.submission_date else ""),
        _row("Staff", travelers),
        _row("Number of Persons", str(len(claim.travelers or []))),
        _row("Location", claim.travel_destination),
        _row("Time", dates),
        _row("Reasons and Explanation", claim.purpose),
        _row("Period off", leave),
        _row("Transportation and Accommodation", transport),
        _row("Remarks", claim.notes),
    ], colWidths=[55 * mm, 119 * mm])
    info.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    els += [info, Spacer(1, 14)]

    # Signature rows from approval events (approve actions only), in step order.
    approvals = {e.step_idx: e for e in (claim.approval_events or []) if e.action == "approve"}
    sig_rows = [[Paragraph("Approval", _LABEL), Paragraph("Approver", _LABEL), Paragraph("Date", _LABEL)]]
    for idx, title in enumerate(["Head of Department", "Finance", "General Manager"]):
        ev = approvals.get(idx)
        who = ev.actor_name if ev else ""
        when = ev.created_at.date().isoformat() if ev else ""
        sig_rows.append([Paragraph(title, _DATA), Paragraph(who or "________", _DATA),
                         Paragraph(when or "________", _DATA)])
    sig = Table(sig_rows, colWidths=[58 * mm, 58 * mm, 58 * mm])
    sig.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    els.append(sig)
    doc.build(els)
    return buf.getvalue()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest tests/test_pdf_tra.py -v`
Expected: PASS.

- [ ] **Step 6: Add the regenerate endpoint to `app/api/v1/travel.py`**

Model the upload on `expense_attachments.py` (`upload_to_file_server` → `ExpenseAttachment`). Add:
```python
from fastapi import HTTPException
from app.core.deps import BearerTokenDep
from app.models.expense import ExpenseAttachment
from app.services.attachment_helper import upload_to_file_server
from app.services.pdf_tra import build_travel_application_pdf


@router.post("/travel-applications/{claim_id}/pdf")
async def regenerate_tra_pdf(claim_id: uuid.UUID, db: SessionDep,
                             user: CurrentUserDep, token: BearerTokenDep):
    claim = await expense_crud.get_by_id(db, claim_id)
    if not claim or claim.claim_type != "TRA":
        raise HTTPException(status_code=404, detail="Travel Application not found")
    data = build_travel_application_pdf(claim)
    filename = f"{claim.claim_number}.pdf"
    storage_key = await upload_to_file_server(
        data, filename, "application/pdf", "tra", claim.id, token)
    att = ExpenseAttachment(claim_id=claim.id, file_id=str(storage_key),
                            file_name=filename, file_size_bytes=len(data),
                            mime_type="application/pdf")
    db.add(att)
    await db.commit()
    return {"file_name": filename, "file_id": str(storage_key)}
```

- [ ] **Step 7: Commit**

```bash
git add expense-api/requirements.txt expense-api/app/services/pdf_tra.py expense-api/app/api/v1/travel.py expense-api/tests/test_pdf_tra.py
git commit -m "feat(expense): English Travel Application PDF + regenerate endpoint"
```

---

## Task 9: Admin workflow editor — register `tra` (portal)

**Files:**
- Modify: `portal/src/pages/admin/AdminPanel.tsx`

**Interfaces:**
- Consumes: nothing new. Produces: the "Travel Application" workflow becomes visible/editable in Admin → Approval Workflows.

- [ ] **Step 1: Capture the baseline build state**

Run: `cd portal && npm run build` and note any pre-existing error count (baseline).

- [ ] **Step 2: Add `tra` to the four places**

In `portal/src/pages/admin/AdminPanel.tsx`:
- `type ActionKey` (line ~25): add `| 'tra'`:
```tsx
type ActionKey = 'pr' | 'po' | 'pa' | 'pa_dir' | 'exp' | 'mil' | 'trv' | 'tra' | 'cfm' | 'budget_plan' | 'vms_visit'
```
- `ACTION_KEYS` (line ~1335): insert `'tra'` after `'trv'`.
- `ACTION_LABELS` map (near line ~1344): add:
```tsx
  tra:         'Travel Application',
```
- `WORKFLOW_DEFAULTS` (line ~1367): add an entry mirroring the engine default:
```tsx
  tra: [
    { role: 'dept_manager', label: 'Department Manager' },
    { role: 'finance_manager', label: 'Finance Manager' },
    { role: 'gm', label: 'General Manager' },
  ],
```
> Match the exact `WorkflowNodeDef` shape already used in that object (check whether it includes an `id` field and replicate it).

- [ ] **Step 3: Verify the build**

Run: `cd portal && npm run build`
Expected: build succeeds; error count == baseline (no new errors).

- [ ] **Step 4: Commit**

```bash
git add portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(portal): expose Travel Application (tra) workflow in Admin editor"
```

---

## Task 10: TravelerPicker component (oa)

**Files:**
- Create: `oa/src/components/TravelerPicker.tsx`
- Reference: `booking/src/components/AttendeePicker.tsx`

**Interfaces:**
- Produces: `<TravelerPicker value={Traveler[]} onChange={(t:Traveler[])=>void} />` where `Traveler = { user_id: string; user_name: string }`. Fetches from `GET /api/v1/users/directory?q=` via the `api` client.

- [ ] **Step 1: Capture baseline build**

Run: `cd oa && npm run build` and note the pre-existing error count (baseline).

- [ ] **Step 2: Create `oa/src/components/TravelerPicker.tsx`**

Adapt the `booking/AttendeePicker.tsx` portal-dropdown pattern (debounced search, `createPortal` fixed dropdown, outside-click close excluding trigger+overlay, `onMouseDown preventDefault` on options). Wire search to the OA `api` client:
```tsx
import { useState, useRef, useEffect, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { X, Search } from 'lucide-react'
import { api } from '@/lib/api'

export interface Traveler { user_id: string; user_name: string }
interface DirectoryUser { id: string; full_name: string; email?: string }

export function TravelerPicker({ value, onChange }: {
  value: Traveler[]; onChange: (t: Traveler[]) => void
}) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [results, setResults] = useState<DirectoryUser[]>([])
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null)
  const triggerRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)

  // debounced directory search (q >= 2 chars)
  useEffect(() => {
    if (q.trim().length < 2) { setResults([]); return }
    const h = setTimeout(async () => {
      const rows = await api.get<DirectoryUser[]>(`/api/v1/users/directory?q=${encodeURIComponent(q.trim())}`)
      const chosen = new Set(value.map(v => v.user_id))
      setResults(rows.filter(r => !chosen.has(r.id)))
    }, 300)
    return () => clearTimeout(h)
  }, [q, value])

  const reposition = () => {
    const r = triggerRef.current?.getBoundingClientRect()
    if (r) setPos({ top: r.bottom + 4, left: r.left, width: r.width })
  }
  useEffect(() => {
    if (!open) return
    reposition()
    const onScroll = () => reposition(); const onResize = () => reposition()
    window.addEventListener('scroll', onScroll, true); window.addEventListener('resize', onResize)
    const onDoc = (e: MouseEvent) => {
      if (triggerRef.current?.contains(e.target as Node)) return
      if (overlayRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => {
      window.removeEventListener('scroll', onScroll, true); window.removeEventListener('resize', onResize)
      document.removeEventListener('mousedown', onDoc)
    }
  }, [open])

  const add = (u: DirectoryUser) => {
    onChange([...value, { user_id: u.id, user_name: u.full_name }]); setQ(''); setResults([])
  }
  const remove = (id: string) => onChange(value.filter(v => v.user_id !== id))

  const dropdown = open && pos && results.length > 0 ? createPortal(
    <div ref={overlayRef} className="z-[9999] rounded-lg border border-neutral-200 bg-white shadow-lg"
      style={{ position: 'fixed', top: pos.top, left: pos.left, width: pos.width, maxHeight: 240, overflowY: 'auto' }}>
      {results.map(u => (
        <button type="button" key={u.id} onMouseDown={e => e.preventDefault()} onClick={() => add(u)}
          className="flex w-full flex-col items-start px-3 py-2 text-left text-sm hover:bg-primary-50">
          <span className="font-medium text-neutral-800">{u.full_name}</span>
          {u.email && <span className="text-xs text-neutral-400">{u.email}</span>}
        </button>
      ))}
    </div>, document.body) : null

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {value.map(t => (
          <span key={t.user_id} className="inline-flex items-center gap-1 rounded-full bg-primary-100 px-2.5 py-1 text-xs font-medium text-primary-700">
            {t.user_name}
            <button type="button" onClick={() => remove(t.user_id)} className="text-primary-400 hover:text-danger-500">
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
      <div ref={triggerRef} className="relative">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-neutral-400" />
        <input value={q} onFocus={() => setOpen(true)} onChange={e => { setQ(e.target.value); setOpen(true) }}
          placeholder="Search employees by name or email…"
          className="w-full rounded-lg border border-neutral-200 py-2 pl-9 pr-3 text-sm focus:outline-none focus:border-primary-400" />
      </div>
      {dropdown}
    </div>
  )
}
```

- [ ] **Step 3: Verify the build**

Run: `cd oa && npm run build`
Expected: build succeeds; error count == baseline.

- [ ] **Step 4: Commit**

```bash
git add oa/src/components/TravelerPicker.tsx
git commit -m "feat(oa): TravelerPicker — portal-dropdown employee multi-select"
```

---

## Task 11: TraCreatePage + routes (oa)

**Files:**
- Create: `oa/src/pages/travel/TraCreatePage.tsx`
- Modify: `oa/src/app/routes.tsx`
- Test: build gate.

**Interfaces:**
- Consumes: `TravelerPicker` (Task 10), `api` client. Produces: `/travel/new` route posting a `TRA` claim to `POST /api/v1/expenses`, then navigating to `/travel/:id`.

- [ ] **Step 1: Create `oa/src/pages/travel/TraCreatePage.tsx`**

Fields: department (default from claim after create — omit editable dept for v1; server fills from the user's department), application date, travelers (TravelerPicker), destination, from/to dates, reason (purpose), leave from/to (optional), transport modes (checkbox group), remarks (notes). Number-of-persons is derived (`travelers.length`, read-only). Submit posts and replaces the tab with the detail page.
```tsx
import { useState } from 'react'
import { useReplaceTab } from '@uniops/shell'
import { useMutation } from '@tanstack/react-query'
import { ArrowLeft, AlertTriangle } from 'lucide-react'
import { oaRoutes } from '@/app/routes'
import { api } from '@/lib/api'
import { TravelerPicker, type Traveler } from '@/components/TravelerPicker'

const TRANSPORT = [
  ['airplane', 'Airplane'], ['train', 'Train'], ['ship', 'Ship'], ['car', 'Car'],
  ['accommodation', 'Accommodation'], ['meal', 'Meal'], ['other', 'Other'],
] as const

const today = () => new Date().toISOString().slice(0, 10)

export default function TraCreatePage() {
  const replaceTab = useReplaceTab(oaRoutes)
  const [appDate, setAppDate] = useState(today())
  const [travelers, setTravelers] = useState<Traveler[]>([])
  const [destination, setDestination] = useState('')
  const [fromDate, setFromDate] = useState(today())
  const [toDate, setToDate] = useState(today())
  const [reason, setReason] = useState('')
  const [leaveFrom, setLeaveFrom] = useState('')
  const [leaveTo, setLeaveTo] = useState('')
  const [modes, setModes] = useState<string[]>([])
  const [remarks, setRemarks] = useState('')
  const [error, setError] = useState('')

  const toggleMode = (k: string) =>
    setModes(prev => prev.includes(k) ? prev.filter(m => m !== k) : [...prev, k])

  const mutation = useMutation({
    mutationFn: () => api.post<{ id: string }>('/api/v1/expenses', {
      claim_type: 'TRA', submission_date: appDate, currency: 'CAD',
      purpose: reason, notes: remarks || null,
      travel_destination: destination, travel_from_date: fromDate, travel_to_date: toDate,
      transport_modes: modes,
      leave_from_date: leaveFrom || null, leave_to_date: leaveTo || null,
      travelers: travelers.map((t, i) => ({ user_id: t.user_id, user_name: t.user_name, seq: i })),
    }),
    onSuccess: (d) => replaceTab(`/travel/${d.id}`),
    onError: (e: any) => setError(e.message || 'Failed to create travel application'),
  })

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (travelers.length === 0) { setError('Add at least one traveler'); return }
    if (!destination.trim()) { setError('Destination is required'); return }
    if (!reason.trim()) { setError('Reason is required'); return }
    mutation.mutate()
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-6 max-w-3xl">
      <div>
        <a href="/travel" className="inline-flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-700 mb-4">
          <ArrowLeft className="h-4 w-4" />Back to Travel Applications
        </a>
        <h1 className="text-2xl font-bold text-neutral-900">Travel Application</h1>
        <p className="mt-0.5 text-sm text-neutral-500">Apply for a business trip before claiming expenses</p>
      </div>

      <div className="rounded-xl border border-neutral-200 bg-white p-5 flex flex-col gap-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Application Date *</label>
            <input type="date" required value={appDate} onChange={e => setAppDate(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Number of Persons</label>
            <input readOnly value={travelers.length}
              className="w-full rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-500" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Travelers (Staff) *</label>
          <TravelerPicker value={travelers} onChange={setTravelers} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="sm:col-span-1">
            <label className="mb-1 block text-xs font-medium text-neutral-600">Destination *</label>
            <input required value={destination} onChange={e => setDestination(e.target.value)}
              placeholder="e.g. Toronto, ON" className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">From *</label>
            <input type="date" required value={fromDate} onChange={e => setFromDate(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">To *</label>
            <input type="date" required value={toDate} min={fromDate} onChange={e => setToDate(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Reasons and Explanation *</label>
          <textarea required value={reason} onChange={e => setReason(e.target.value)} rows={3}
            className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm resize-none" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Transportation & Accommodation</label>
          <div className="flex flex-wrap gap-3">
            {TRANSPORT.map(([k, lbl]) => (
              <label key={k} className="inline-flex items-center gap-1.5 text-sm text-neutral-700">
                <input type="checkbox" checked={modes.includes(k)} onChange={() => toggleMode(k)} />{lbl}
              </label>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Leave From (optional)</label>
            <input type="date" value={leaveFrom} onChange={e => setLeaveFrom(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Leave To (optional)</label>
            <input type="date" value={leaveTo} onChange={e => setLeaveTo(e.target.value)}
              className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm" />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Remarks (external companions, notes)</label>
          <textarea value={remarks} onChange={e => setRemarks(e.target.value)} rows={2}
            className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm resize-none" />
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-danger-50 border border-danger-200 px-3 py-2 text-sm text-danger-700">
          <AlertTriangle className="h-4 w-4 shrink-0" />{error}
        </div>
      )}
      <button type="submit" disabled={mutation.isPending}
        className="flex items-center justify-center gap-2 rounded-lg bg-primary-700 px-6 py-2.5 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-50 self-start">
        {mutation.isPending ? 'Saving…' : 'Save Draft'}
      </button>
    </form>
  )
}
```

- [ ] **Step 2: Add routes in `oa/src/app/routes.tsx`**

Import and register (list route `TravelApplicationsListPage` is created in Task 12; add its import now and both routes):
```tsx
import TravelApplicationsListPage from '@/pages/travel/TravelApplicationsListPage'
import TraCreatePage from '@/pages/travel/TraCreatePage'
```
```tsx
  { path: '/travel', element: <TravelApplicationsListPage />, tab: { title: 'Travel Applications', icon: 'Plane', keyStrategy: 'static' } },
  { path: '/travel/new', element: <TraCreatePage />, tab: { title: 'New Travel Application', icon: 'Plus', keyStrategy: 'static' } },
```
> The `/travel/:id` detail route reuses `ExpenseDetailPage` — add it in Task 13 after the detail page learns the `travel` path. For now, TRA detail is reachable via `/expenses/:id` (same claim table). The dedicated `/travel/:id` route is added in Task 13.

- [ ] **Step 3: Verify the build**

Run: `cd oa && npm run build`
Expected: build succeeds (note: `TravelApplicationsListPage` import will fail until Task 12 — do Task 12 immediately, or temporarily stub the import). To keep this task self-contained, create a minimal placeholder `TravelApplicationsListPage.tsx` returning `null` now and flesh it out in Task 12.

- [ ] **Step 4: Commit**

```bash
git add oa/src/pages/travel/TraCreatePage.tsx oa/src/app/routes.tsx oa/src/pages/travel/TravelApplicationsListPage.tsx
git commit -m "feat(oa): Travel Application create page + routes"
```

---

## Task 12: TravelApplicationsListPage (oa)

**Files:**
- Modify: `oa/src/pages/travel/TravelApplicationsListPage.tsx` (flesh out the Task 11 placeholder)
- Test: build gate.

**Interfaces:**
- Consumes: `GET /api/v1/expenses?type=TRA&status=&page=` (existing list endpoint). Produces: the `/travel` list page.

- [ ] **Step 1: Implement the list page**

Model on `ExpenseListPage.tsx`: React Query, status tabs, `StatusBadge`, `Pagination`, row click → `/travel/:id`, a "New Travel Application" button → `/travel/new`. Filter is hard-set to `type=TRA`.
```tsx
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useNavigate } from '@uniops/shell'
import { useQuery } from '@tanstack/react-query'
import { Plus, Plane } from 'lucide-react'
import { api } from '@/lib/api'
import { StatusBadge } from '@/components/ui/badge'
import { Pagination } from '@/components/ui/Pagination'

interface TravelApp {
  id: string; claim_number: string; employee_name: string; department_name: string
  submission_date: string; travel_destination?: string | null; status: string
}
interface ListResp { items: TravelApp[]; total: number }
const TABS = ['all', 'draft', 'submitted', 'in_review', 'approved'] as const

export default function TravelApplicationsListPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const status = params.get('status') || 'all'
  const [page, setPage] = useState(1)
  const pageSize = 20

  const { data, isLoading } = useQuery<ListResp>({
    queryKey: ['travel-list', status, page],
    queryFn: () => {
      const qs = new URLSearchParams({ type: 'TRA', page: String(page), page_size: String(pageSize) })
      if (status !== 'all') qs.set('status', status)
      return api.get<ListResp>(`/api/v1/expenses?${qs}`)
    },
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-neutral-900 flex items-center gap-2">
          <Plane className="h-6 w-6 text-primary-700" />Travel Applications
        </h1>
        <button onClick={() => navigate('/travel/new')}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-primary-800">
          <Plus className="h-4 w-4" />New Travel Application
        </button>
      </div>

      <div className="flex gap-1 border-b border-neutral-200">
        {TABS.map(t => (
          <button key={t} onClick={() => { setParams(t === 'all' ? {} : { status: t }); setPage(1) }}
            className={`px-3 py-2 text-sm font-medium capitalize ${status === t ? 'border-b-2 border-primary-600 text-primary-700' : 'text-neutral-500 hover:text-neutral-700'}`}>
            {t.replace('_', ' ')}
          </button>
        ))}
      </div>

      {isLoading ? <div className="py-12 text-center text-neutral-400">Loading…</div> : (
        <div className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
              <tr>
                <th className="px-4 py-3">Application #</th><th className="px-4 py-3">Applicant</th>
                <th className="px-4 py-3">Department</th><th className="px-4 py-3">Destination</th>
                <th className="px-4 py-3">Date</th><th className="px-4 py-3">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {(data?.items ?? []).map(a => (
                <tr key={a.id} onClick={() => navigate(`/travel/${a.id}`)}
                  className="cursor-pointer hover:bg-neutral-50">
                  <td className="px-4 py-3 font-mono text-neutral-800">{a.claim_number}</td>
                  <td className="px-4 py-3">{a.employee_name}</td>
                  <td className="px-4 py-3 text-neutral-500">{a.department_name}</td>
                  <td className="px-4 py-3">{a.travel_destination || '—'}</td>
                  <td className="px-4 py-3 text-neutral-500">{a.submission_date}</td>
                  <td className="px-4 py-3"><StatusBadge status={a.status} /></td>
                </tr>
              ))}
              {data && data.items.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-12 text-center text-neutral-400">No travel applications</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {data && data.total > pageSize && (
        <Pagination page={page} pageSize={pageSize} total={data.total} onPageChange={setPage} />
      )}
    </div>
  )
}
```
> Confirm the exact import paths/props for `StatusBadge`, `Pagination`, and `useNavigate` against `ExpenseListPage.tsx` and adjust if they differ.

- [ ] **Step 2: Verify the build**

Run: `cd oa && npm run build`
Expected: build succeeds; error count == baseline.

- [ ] **Step 3: Commit**

```bash
git add oa/src/pages/travel/TravelApplicationsListPage.tsx
git commit -m "feat(oa): Travel Applications list page"
```

---

## Task 13: ExpenseDetailPage TRA branch + `/travel/:id` route (oa)

**Files:**
- Modify: `oa/src/pages/expenses/ExpenseDetailPage.tsx`
- Modify: `oa/src/app/routes.tsx`
- Test: build gate.

**Interfaces:**
- Consumes: `GET /api/v1/expenses/:id` (now returns `travelers`, `transport_modes`, `leave_*`), `POST /api/v1/travel-applications/:id/pdf`. Produces: TRA-specific detail rendering + a "Regenerate PDF" button; `/travel/:id` opens the detail page.

- [ ] **Step 1: Add the `/travel/:id` route**

In `oa/src/app/routes.tsx`, reuse `ExpenseDetailPage`:
```tsx
  { path: '/travel/:id', element: <ExpenseDetailPage />, tab: { title: (p) => `Travel ${short(p.id)}`, icon: 'Plane', keyStrategy: 'param', paramName: 'id' } },
```

- [ ] **Step 2: Render the TRA sections in `ExpenseDetailPage.tsx`**

Where the page branches on `claim_type` (line-items are rendered when `claim_type !== 'MIL'`), add a `claim.claim_type === 'TRA'` branch that:
- Skips the money/line-items table entirely.
- Renders a travelers list, destination, from/to, reason (`purpose`), transport modes (as checkmark chips), leave dates, remarks (`notes`).
- Adds a "Regenerate PDF" button calling the new endpoint and invalidating the attachments query.
```tsx
{claim.claim_type === 'TRA' && (
  <div className="rounded-xl border border-neutral-200 bg-white p-5 flex flex-col gap-3">
    <div className="flex items-center justify-between">
      <h2 className="text-sm font-semibold text-neutral-700">Travel Application</h2>
      <button onClick={() => regenPdf.mutate()} disabled={regenPdf.isPending}
        className="text-xs font-medium text-primary-700 hover:text-primary-900 disabled:opacity-50">
        {regenPdf.isPending ? 'Generating…' : 'Regenerate PDF'}
      </button>
    </div>
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
      <div><dt className="text-neutral-500">Travelers</dt>
        <dd>{(claim.travelers ?? []).map((t: any) => t.user_name).join(', ') || '—'}</dd></div>
      <div><dt className="text-neutral-500">Number of Persons</dt>
        <dd>{(claim.travelers ?? []).length}</dd></div>
      <div><dt className="text-neutral-500">Destination</dt><dd>{claim.travel_destination || '—'}</dd></div>
      <div><dt className="text-neutral-500">Dates</dt>
        <dd>{claim.travel_from_date} → {claim.travel_to_date}</dd></div>
      <div className="sm:col-span-2"><dt className="text-neutral-500">Reasons</dt><dd>{claim.purpose || '—'}</dd></div>
      <div className="sm:col-span-2"><dt className="text-neutral-500">Transportation & Accommodation</dt>
        <dd>{(claim.transport_modes ?? []).join(', ') || '—'}</dd></div>
      <div><dt className="text-neutral-500">Leave</dt>
        <dd>{claim.leave_from_date ? `${claim.leave_from_date} → ${claim.leave_to_date}` : '—'}</dd></div>
      <div className="sm:col-span-2"><dt className="text-neutral-500">Remarks</dt><dd>{claim.notes || '—'}</dd></div>
    </dl>
  </div>
)}
```
Add the mutation near the other mutations in the component:
```tsx
const regenPdf = useMutation({
  mutationFn: () => api.post(`/api/v1/travel-applications/${id}/pdf`, {}),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ['expense-attachments', id] }),
})
```
Also add `'TRA': 'Travel Application'` to the claim-type label map used in the header, and ensure the existing line-items table is guarded so it does not render for `TRA` (e.g. `claim.claim_type !== 'MIL' && claim.claim_type !== 'TRA'`). Attachments card, approval timeline, and submit/approve buttons are reused as-is (TRA flows through the same `/action` endpoint).

- [ ] **Step 3: Verify the build**

Run: `cd oa && npm run build`
Expected: build succeeds; error count == baseline.

- [ ] **Step 4: Commit**

```bash
git add oa/src/pages/expenses/ExpenseDetailPage.tsx oa/src/app/routes.tsx
git commit -m "feat(oa): Travel Application detail view + PDF regenerate + /travel/:id route"
```

---

## Task 14: TrvCreatePage — required Travel Application selector (oa)

**Files:**
- Modify: `oa/src/pages/expenses/TrvCreatePage.tsx`
- Test: build gate.

**Interfaces:**
- Consumes: `GET /api/v1/travel-applications/eligible`. Produces: a required selector at the top of the TRV form; selection auto-fills destination/from/to/purpose and adds `travel_application_id` to the submit body.

- [ ] **Step 1: Add the eligible-apps query + selector state**

Near the top of `TrvCreatePage()`:
```tsx
interface EligibleApp {
  id: string; claim_number: string; travel_destination?: string | null
  travel_from_date?: string | null; travel_to_date?: string | null; purpose?: string | null
}
const [travelApplicationId, setTravelApplicationId] = useState('')
const { data: eligibleApps = [] } = useQuery<EligibleApp[]>({
  queryKey: ['eligible-travel-apps'],
  queryFn: () => api.get<EligibleApp[]>('/api/v1/travel-applications/eligible'),
})
```

- [ ] **Step 2: Render the selector (auto-fill on change) above "Trip Details"**

```tsx
<div className="rounded-xl border border-neutral-200 bg-white p-5">
  <label className="mb-1 block text-xs font-medium text-neutral-600">Travel Application *</label>
  <select required value={travelApplicationId}
    onChange={e => {
      const id = e.target.value; setTravelApplicationId(id)
      const app = eligibleApps.find(a => a.id === id)
      if (app) {
        if (app.travel_destination) setDestination(app.travel_destination)
        if (app.travel_from_date) setFromDate(app.travel_from_date)
        if (app.travel_to_date) setToDate(app.travel_to_date)
        if (app.purpose) setPurpose(app.purpose)
      }
    }}
    className="w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm">
    <option value="">— Select an approved travel application —</option>
    {eligibleApps.map(a => (
      <option key={a.id} value={a.id}>
        {a.claim_number}{a.travel_destination ? ` — ${a.travel_destination}` : ''}
      </option>
    ))}
  </select>
  {eligibleApps.length === 0 && (
    <p className="mt-1 text-xs text-warning-700">
      No approved travel applications available. Submit and get a Travel Application approved first.
    </p>
  )}
</div>
```

- [ ] **Step 3: Enforce selection + include it in the payload**

In `handleSubmit`, before `mutation.mutate(...)`:
```tsx
    if (!travelApplicationId) { setError('A Travel Application is required'); return }
```
Add to the `mutation.mutate({ ... })` body object:
```tsx
      claim_type: 'TRV',
      travel_application_id: travelApplicationId,
```

- [ ] **Step 4: Verify the build**

Run: `cd oa && npm run build`
Expected: build succeeds; error count == baseline.

- [ ] **Step 5: Commit**

```bash
git add oa/src/pages/expenses/TrvCreatePage.tsx
git commit -m "feat(oa): require an approved Travel Application when creating a TRV claim"
```

---

## Task 15: Full-suite regression + manual smoke (integration)

**Files:** none (verification only).

- [ ] **Step 1: Run the expense-api suite**

Run: `cd expense-api && POSTGRES_HOST=localhost python -m pytest -q`
Expected: new travel tests pass; **no NEW failures vs the pre-existing baseline** (record the baseline failure count before starting — see Global Constraints; some legacy failures may pre-exist).

- [ ] **Step 2: Run the approval-api suite**

Run: `cd approval-api && python -m pytest -q`
Expected: `test_engine_tra.py` passes; no new failures.

- [ ] **Step 3: Build all touched frontends**

Run: `cd oa && npm run build` and `cd portal && npm run build`
Expected: both succeed at baseline error counts.

- [ ] **Step 4: Manual smoke (dev stack) — happy path**

1. Create a Travel Application with 2 travelers → Submit → approve through dept_manager → finance_manager → gm → status `approved`.
2. As a listed traveler, create a TRV → the Travel Application selector lists the approved TRA → select it, destination/dates/purpose auto-fill → submit succeeds.
3. As a non-traveler, attempt a TRV against that TRA → blocked (403 surfaced as an error).
4. Open the TRA detail → Regenerate PDF → the PDF attachment appears and downloads with English labels and signature rows filled from the approvals.

- [ ] **Step 5: Commit any smoke-fix adjustments, then finish the branch**

Use `superpowers:finishing-a-development-branch` to decide merge/PR. **Do not deploy** — release is a separate, user-approved step per the multi-session discipline (base off current production TAG, build+push all 15 images, run `migrate-prod.sh` because this ships migration `0012`).

---

## Self-Review

**Spec coverage:**
- §2/§3 data model → Task 1 (columns, travelers, TRV ref) ✅
- §3 schemas → Task 2 ✅
- §3/§4 TRA create + approval chain → Task 3 (create) + Task 7 (engine) ✅
- §5 TRV gate → Task 4 ✅
- §5.2 eligible endpoint + directory search → Task 5 ✅
- §4 action_key/inbox mappings → Task 6 ✅
- §7 English PDF (+ STSong-Light data fallback, reportlab dep) → Task 8 ✅
- §6.1 list page → Task 12; create page → Task 11; traveler picker → Task 10 ✅
- §6.2 detail branch + linked view + PDF button → Task 13 ✅
- §6.3 TRV selector → Task 14 ✅
- §6.4 routes → Tasks 11/13; nav → not needed (OA is one portal module; sub-pages are OA-internal routes) — confirmed by frontend exploration ✅
- Admin editability of the TRA chain → Task 9 ✅
- §10 open question (self-ref FK) → resolved: `travel_application_id` FK to `expense_claims.id` `ON DELETE SET NULL` (Task 1) ✅
- §10 open question (traveler notification on approval) → out of scope for v1: `_post_approve_tra` is a no-op (Task 7); notification can be added later ✅

**Placeholder scan:** No TBD/TODO; every code step has real code. Two explicit "confirm against existing file" notes (fixture names in Task 3/4, badge/pagination imports in Task 12, WorkflowNodeDef shape in Task 9) are verification instructions, not deferred work — the surrounding code is complete.

**Type consistency:** `Traveler = {user_id, user_name}` used consistently in Tasks 10/11. `travel_application_id` (snake_case) consistent across schema/endpoint/frontend payloads. `transport_modes: list[str]` consistent. `_post_approve_tra` name consistent (Task 7). Eligible endpoint returns `{id, claim_number, travel_destination, travel_from_date, travel_to_date, purpose}` consumed identically in Task 14.

**Linked-TRV list on TRA detail:** the spec §6.2 mentions showing linked TRVs on the TRA detail. This is display-only polish; not blocking. If desired, add a follow-up query `GET /api/v1/expenses?type=TRV` filtered client-side by `travel_application_id` — noted as optional, not a task, to keep v1 focused (YAGNI).
