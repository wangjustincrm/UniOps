# Purchase Agreement Phase 1A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the backlog of Princess Auto invoices be matched to a Purchase Agreement and paid, without a PO and without a goods receipt.

**Architecture:** A new `purchase_agreements` document in epms-api, approved through its own `agr` action key on the shared approval engine. Invoices gain an agreement route alongside the existing PO route; when an invoice is matched to an agreement it links for traceability with zero variance (the same shape as the existing fee-only branch). Payment Applications accept `agreement_id` in place of `po_id`, and the goods-receipt gate is skipped for that route. Pickup Slip reconciliation is explicitly **out of scope** — 1A ships a `legacy_settlement` flag that records "paid with no slip evidence, reason given".

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic (epms-api, approval-api, identity-api); React 18 + TypeScript + Vite + Tailwind (epms frontend); pytest + httpx ASGITransport.

**Spec:** `docs/superpowers/specs/2026-08-06-purchase-agreement-design.md` — §5.1, §6, §8, §10 (1A only).

## Global Constraints

- **Worktree:** `c:/Project/uniops-agreement`, branch `feature/purchase-agreement`, based on `origin/main` = `d57578d`. Do not work in `c:/Project/uniops` (detached HEAD, other sessions' WIP).
- **epms-api alembic down_revision:** `nc02_nc_cutover` — verified the single head on 2026-08-06 by parsing the full revision graph (53 revisions; `r8m9n0o1p2q3` is NOT a head, it is merged by `s9n0o1p2q3r4`'s tuple `down_revision`). Re-verify before writing each new migration; if another session has landed one, chain onto the new tail instead.
- **identity-api alembic down_revision:** `0005_procurement_officer_pa`. Revision ids must be **≤ 32 characters** — `alembic_version_identity.version_num` is `varchar(32)` and a longer id fails the upgrade.
- **Permissions go through the Access Control Matrix.** Never hardcode a role tuple as a gate. Use `require_permission("<key>")`.
- **All user-facing UI copy is English.** Code comments may be Chinese.
- **Decimals arrive as JSON strings.** Pydantic serialises `Decimal` as a string; every frontend arithmetic or comparison must wrap in `Number()`.
- **Frontend gate:** `cd epms && npx tsc -p tsconfig.app.json` — baseline is **59 errors**. The count must not increase.
- **Test DB is single-writer.** Only one epms-api suite may run at a time; concurrent suites cause `UndefinedTable` races on `drop_all`. Backend test env:
  ```bash
  POSTGRES_SERVER=localhost POSTGRES_PORT=5432 POSTGRES_USER=uniops \
  POSTGRES_PASSWORD=<local docker pw> POSTGRES_DB=epms_test \
  JWT_SECRET_KEY=<any> pytest epms-api/tests/... -v
  ```
  A worktree has no `.env`, so `JWT_SECRET_KEY` must be passed explicitly.
- **Never run alembic or scripts from the host shell** — the host `.env` points at the production DB (10.10.50.20). Run inside the container or override `POSTGRES_*`.
- **Document numbers** use `app.crud._numbering.next_number` (MAX-tail + advisory lock). Never `count(*) + 1`.
- **Overlay UI** (dropdowns, popovers) must `createPortal` to `document.body` with `position: fixed` to escape `overflow` clipping.
- **Commit per task.** Do not push; the user approves pushes separately.

- **Test env (corrected 2026-08-07 — the value written below in older task bodies is wrong).** The local Postgres container `uniops_postgres` runs on `localhost:5432` with user **`epms`**, DB `epms_test` (approval-api: `approval_test`). Any task step showing `POSTGRES_USER=uniops` is stale — the user is `epms`. Read the password from the running container rather than hardcoding it anywhere:
  ```bash
  docker exec uniops_postgres env | grep POSTGRES_PASSWORD
  ```
  Never paste a credential into a tracked file.
- **Baseline confirmed 2026-08-07:** epms-api full suite = **69 failed, 542 passed** (11m08s). approval-api = 53 passed.
- **⚠️ Do NOT use the `seeded_vendor` / `system_user_id` conftest fixtures in these tests.** They insert through `pg_cur`, a **separate psycopg2 connection whose transaction is never committed**, so rows are invisible to the async ORM session and to API calls — you get `ForeignKeyViolationError` or a 404 "Vendor not found". (`seeded_vendor` also returns a **tuple `(id, name)`**, not a dict, so `seeded_vendor["id"]` raises `TypeError`.) Confirmed empirically during Task 1. Every test body in Tasks 2, 5, 6, and 7 that references `seeded_vendor["id"]` or `system_user_id` must instead seed through the async session using this helper, which Task 2 adds to `epms-api/tests/test_agreements.py` and later tasks import:

  ```python
  # epms-api/tests/test_agreements.py — shared by the agreement test modules.
  import uuid

  from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

  from app.crud import user as user_crud
  from app.models.vendor import Vendor
  from app.schemas.auth import RegisterRequest


  async def seed_vendor_and_user(test_engine, vendor_name="Princess Auto"):
      """Seed a vendor + user through the ASYNC session and commit them.

      The conftest `seeded_vendor` / `system_user_id` fixtures write through an
      uncommitted psycopg2 connection that the async engine cannot see; anything
      touching the ORM or the API needs committed rows on the same engine.
      Returns (vendor_id: uuid.UUID, vendor_name: str, user_id: uuid.UUID).
      """
      factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
      async with factory() as db:
          vendor = Vendor(
              code=f"V-{uuid.uuid4().hex[:8]}", name=vendor_name, category="supplier",
              contact_name="AP Contact", contact_email="ap@example.com",
          )
          db.add(vendor)
          user = await user_crud.create(db, RegisterRequest(
              email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
              full_name="Agreement Tester", role="procurement_officer",
          ))
          await db.commit()
          await db.refresh(vendor)
          await db.refresh(user)
          return vendor.id, vendor.name, user.id
  ```

  Read `epms-api/tests/test_backfill_invoice_gr_links.py` for the established precedent. Where a task's test body says `seeded_vendor["id"]`, use `str(vendor_id)` from this helper; where it says `system_user_id`, use `user_id`.

## Out of Scope for 1A

Pickup Slips, slip↔invoice reconciliation, the E2/E3 exception queue, recurring payment schedules, milestone schedules, NTE threshold notifications, and the OCR reference-number auto-resolution. 1A ships **manual route selection only** — the operator picks "Agreements" in the match panel. Auto-resolution is 1B.

## File Structure

**epms-api (new)**
- `app/models/agreement.py` — `PurchaseAgreement` ORM model, one responsibility: the table.
- `app/schemas/agreement.py` — request/response shapes + `AGR_WORKFLOW` constant.
- `app/crud/agreement.py` — number allocation, reads, create/update, status transitions.
- `app/api/v1/agreements.py` — HTTP surface, permission gates.
- `alembic/versions/ag01_purchase_agreements.py` — new table.
- `alembic/versions/ag02_agreement_links.py` — columns on `invoices` and `payment_applications`.
- `tests/test_agreements.py`, `tests/test_agreement_invoice_match.py`, `tests/test_agreement_pa.py`.

**epms-api (modified)**
- `app/models/__init__.py` — register the new model.
- `app/models/invoice.py`, `app/models/pa.py` — new columns.
- `app/schemas/invoice.py` — `InvoiceMatchRequest.agreement_id` / `legacy_settlement_reason`; `InvoiceResponse` new fields.
- `app/crud/invoice.py` — agreement branch inside `match()`.
- `app/api/v1/invoices.py` — `match-candidates` returns agreements.
- `app/crud/pa.py` — widen the list filter.
- `app/api/v1/pa.py` — agreement-sourced PA create + receipt-gate exemption.
- `app/main.py` — mount the agreements router.

**approval-api (modified)**
- `app/models/agreement.py` — read-write mirror (new file).
- `app/crud/engine.py` — `_DOC_META["agr"]`, `_WORKFLOW_DEFAULTS["agr"]`, post-approve hook.

**identity-api (modified)**
- `alembic/versions/0006_agreement_perms.py` — register + grant the two new keys.
- `scripts/seed_phase2_keys.py` — same keys, for fresh DBs.

**epms frontend (new)**
- `src/pages/agreements/AgreementListPage.tsx`
- `src/pages/agreements/AgreementCreatePage.tsx`
- `src/pages/agreements/AgreementDetailPage.tsx`

**epms frontend (modified)**
- `src/App.tsx` (routes), `src/pages/invoices/MatchPanel.tsx` + `InvoiceListPage.tsx` (route group switch), `src/pages/pa/PaCreatePage.tsx` (agreement source).
- `portal/src/components/layout/navConfig.tsx` — nav entry gated on `epms.agreement.read`.

**Permission keys chosen (and why):** `epms.agreement.read` and `epms.agreement.write`, both **phase-2 style** keys registered in `permission_defs` / `role_permissions`. Deliberately NOT a phase-1 `view_agreement` key: phase-1 keys live in `seed_authz.MODULE_BY_KEY` / `DEFAULTS` / `LOCKED`, are re-derived by `verify_gate_parity.py`, and are **hand-copied** into `epms-api/tests/conftest.py` — adding one there means four files must stay in sync. Phase-2 keys need only the migration plus `seed_phase2_keys.py`.

---

### Task 1: Agreement table and ORM model

**Files:**
- Create: `epms-api/app/models/agreement.py`
- Create: `epms-api/alembic/versions/ag01_purchase_agreements.py`
- Modify: `epms-api/app/models/__init__.py`
- Test: `epms-api/tests/test_agreements.py`

**Interfaces:**
- Produces: `app.models.agreement.PurchaseAgreement` with columns `id, number, title, agreement_type, contract_no, contact_email, vendor_id, vendor_name, vendor_reference, valid_from, valid_to, grace_days, not_to_exceed, consumed_amount, currency, tax_code, tax_rate, department_id, owner_id, status, approval_step_idx, notes, created_by, created_at, updated_at`.

- [ ] **Step 1: Re-verify the alembic head**

```bash
cd /c/Project/uniops-agreement/epms-api && python -c "
import re,glob,os
revs={}; downs=set()
for f in glob.glob('alembic/versions/*.py'):
    s=open(f,encoding='utf-8').read()
    m=re.search(r'^revision\s*[:=].*?[\'\"]([^\'\"]+)',s,re.M)
    d=re.search(r'^down_revision\s*[:=]\s*(.*)\$',s,re.M)
    if m: revs[m.group(1)]=os.path.basename(f)
    if d:
        for x in re.findall(r'[\'\"]([^\'\"]+)[\'\"]', d.group(1)): downs.add(x)
print('HEADS:',[(h,revs[h]) for h in revs if h not in downs])
"
```

Expected: exactly one head. If it is still `nc02_nc_cutover`, continue. If it changed, use the new head as `down_revision` below.

- [ ] **Step 2: Write the failing test**

```python
# epms-api/tests/test_agreements.py
"""Purchase Agreement — model, CRUD, approval, and match/PA integration."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agreement import PurchaseAgreement

pytestmark = pytest.mark.asyncio


async def test_agreement_model_roundtrip(test_engine, seeded_vendor, system_user_id):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = PurchaseAgreement(
            number=f"AGR-202608-{uuid.uuid4().hex[:4]}",
            title="Princess Auto house account",
            agreement_type="house_account",
            contract_no="CN-2026-001",
            contact_email="ap@princessauto.example",
            vendor_id=uuid.UUID(seeded_vendor["id"]),
            vendor_name=seeded_vendor["name"],
            vendor_reference="PO-585-2606-01",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            not_to_exceed=Decimal("50000.00"),
            created_by=uuid.UUID(system_user_id),
        )
        db.add(agr)
        await db.commit()
        await db.refresh(agr)

    async with factory() as db:
        got = (await db.execute(
            select(PurchaseAgreement).where(PurchaseAgreement.id == agr.id)
        )).scalar_one()

    assert got.status == "draft"                    # server default
    assert got.grace_days == 30                     # server default
    assert got.consumed_amount == Decimal("0")      # server default
    assert got.currency == "CAD"
    assert got.approval_step_idx == 0
    assert got.vendor_reference == "PO-585-2606-01"
```

`seeded_vendor` and `system_user_id` are existing fixtures in `epms-api/tests/conftest.py` (lines 440 and 455) — do not redefine them.

- [ ] **Step 3: Run the test to verify it fails**

```bash
POSTGRES_SERVER=localhost POSTGRES_PORT=5432 POSTGRES_USER=uniops \
POSTGRES_PASSWORD=<local docker pw> POSTGRES_DB=epms_test JWT_SECRET_KEY=test \
pytest epms-api/tests/test_agreements.py::test_agreement_model_roundtrip -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.agreement'`.

- [ ] **Step 4: Write the model**

```python
# epms-api/app/models/agreement.py
"""ORM model for Purchase Agreement (AGR) — the blanket-PO replacement.

An agreement is the authorisation + price container for spend that must not go
through PR→PO→GR: house accounts (staff pick up at the vendor and the vendor
bills monthly) and contract-driven recurring / milestone payments. It is NOT an
order: it carries no quantities and is never received against.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PurchaseAgreement(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "purchase_agreements"

    number: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # house_account | recurring | milestone — only house_account is wired in 1A.
    agreement_type: Mapped[str] = mapped_column(String(20), nullable=False)

    contract_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_partners.id", ondelete="RESTRICT"),
        nullable=False, index=True
    )
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 供应商侧的账号/引用号。切换时把现有 Open PO 号登记于此,供应商无需改号,
    # 其发票上印的老号仍能解析到本协议(1B 的自动识别用)。
    vendor_reference: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    # 过期后仍可匹配的宽限窗口 —— 月结账单总在期末之后才到(8/31 到期,9/3 来票)。
    grace_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")

    # NTE 只预警不拦截(用户决策):这两列驱动进度条与阈值通知,不阻断任何写路径。
    not_to_exceed: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    consumed_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, server_default="0")

    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="CAD")
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)

    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    budget_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 协议责任人 —— NTE 预警与到期提醒的收件人。
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)

    # draft | in_review | active | expired | closed | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft", index=True)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
```

- [ ] **Step 5: Register the model**

Add to `epms-api/app/models/__init__.py`, in the same style as the neighbouring imports:

```python
from app.models.agreement import PurchaseAgreement  # noqa: F401
```

- [ ] **Step 6: Write the migration**

```python
# epms-api/alembic/versions/ag01_purchase_agreements.py
"""create purchase_agreements

Revision ID: ag01_purchase_agreements
Revises: nc02_nc_cutover
Create Date: 2026-08-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag01_purchase_agreements"
down_revision = "nc02_nc_cutover"   # 实测唯一 head(2026-08-06,53 revisions)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_agreements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("agreement_type", sa.String(20), nullable=False),
        sa.Column("contract_no", sa.String(100), nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("business_partners.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("vendor_reference", sa.String(100), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("grace_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("not_to_exceed", sa.Numeric(15, 2), nullable=True),
        sa.Column("consumed_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("budget_code", sa.String(100), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("approval_step_idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_purchase_agreements_number", "purchase_agreements", ["number"], unique=True)
    op.create_index("ix_purchase_agreements_vendor_id", "purchase_agreements", ["vendor_id"])
    op.create_index("ix_purchase_agreements_vendor_reference", "purchase_agreements", ["vendor_reference"])
    op.create_index("ix_purchase_agreements_status", "purchase_agreements", ["status"])
    op.create_index("ix_purchase_agreements_department_id", "purchase_agreements", ["department_id"])
    op.create_index("ix_purchase_agreements_created_by", "purchase_agreements", ["created_by"])


def downgrade() -> None:
    op.drop_table("purchase_agreements")
```

- [ ] **Step 7: Run the test to verify it passes**

Same command as Step 3. Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add epms-api/app/models/agreement.py epms-api/app/models/__init__.py \
        epms-api/alembic/versions/ag01_purchase_agreements.py \
        epms-api/tests/test_agreements.py
git commit -m "feat(epms): add purchase_agreements table and ORM model"
```

---

### Task 2: Agreement schemas, CRUD, and HTTP endpoints

**Files:**
- Create: `epms-api/app/schemas/agreement.py`
- Create: `epms-api/app/crud/agreement.py`
- Create: `epms-api/app/api/v1/agreements.py`
- Create: `identity-api/alembic/versions/0006_agreement_perms.py`
- Modify: `epms-api/app/main.py`, `identity-api/scripts/seed_phase2_keys.py`
- Test: `epms-api/tests/test_agreements.py`

**Interfaces:**
- Consumes: `PurchaseAgreement` (Task 1); `app.crud._numbering.next_number(db, column, prefix, width)`.
- Produces:
  - `app.schemas.agreement.AgreementCreate / AgreementUpdate / AgreementResponse / AgreementListResponse / AgreementActionRequest`, and `AGR_WORKFLOW: list[dict]`.
  - `app.crud.agreement.create(db, body, *, vendor_name, created_by) -> PurchaseAgreement`
  - `app.crud.agreement.get_by_id(db, agreement_id) -> PurchaseAgreement | None`
  - `app.crud.agreement.get_all(db, *, status=None, vendor_id=None, agreement_type=None, search=None, page=1, page_size=20) -> tuple[list, int]`
  - `app.crud.agreement.update(db, agr, body) -> PurchaseAgreement`
  - Router mounted at `/api/v1/agreements`.
  - Permission keys `epms.agreement.read`, `epms.agreement.write`.

- [ ] **Step 1: Write the failing tests**

Append to `epms-api/tests/test_agreements.py`:

```python
AGR_URL = "/api/v1/agreements"


def _agr_payload(vendor_id, **over):
    body = {
        "title": "Princess Auto house account",
        "agreement_type": "house_account",
        "vendor_id": vendor_id,
        "vendor_reference": "PO-585-2606-01",
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
        "not_to_exceed": "50000.00",
        "currency": "CAD",
        "tax_rate": "0.13",
        "tax_code": "HST13",
    }
    body.update(over)
    return body


async def test_create_agreement_allocates_number_and_defaults(admin_client, seeded_vendor):
    r = await admin_client.post(AGR_URL, json=_agr_payload(seeded_vendor["id"]))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["number"].startswith("AGR-")
    assert len(body["number"]) == len("AGR-202608-0001")
    assert body["status"] == "draft"
    assert body["vendor_name"] == seeded_vendor["name"]
    assert body["consumed_amount"] == "0.00"
    assert body["grace_days"] == 30


async def test_agreement_numbers_are_sequential(admin_client, seeded_vendor):
    a = (await admin_client.post(AGR_URL, json=_agr_payload(seeded_vendor["id"]))).json()
    b = (await admin_client.post(AGR_URL, json=_agr_payload(seeded_vendor["id"]))).json()
    assert int(b["number"].rsplit("-", 1)[1]) == int(a["number"].rsplit("-", 1)[1]) + 1


async def test_create_agreement_rejects_inverted_validity(admin_client, seeded_vendor):
    r = await admin_client.post(AGR_URL, json=_agr_payload(
        seeded_vendor["id"], valid_from="2026-12-31", valid_to="2026-01-01"))
    assert r.status_code == 422
    assert "valid_to" in r.text


async def test_create_agreement_unknown_vendor_404(admin_client):
    r = await admin_client.post(AGR_URL, json=_agr_payload(str(uuid.uuid4())))
    assert r.status_code == 404


async def test_get_and_list_agreement(admin_client, seeded_vendor):
    created = (await admin_client.post(AGR_URL, json=_agr_payload(seeded_vendor["id"]))).json()

    got = await admin_client.get(f"{AGR_URL}/{created['id']}")
    assert got.status_code == 200
    assert got.json()["number"] == created["number"]

    listed = await admin_client.get(AGR_URL, params={"vendor_id": seeded_vendor["id"]})
    assert listed.status_code == 200
    assert created["id"] in [i["id"] for i in listed.json()["items"]]


async def test_patch_agreement_only_in_draft(admin_client, seeded_vendor, test_engine):
    created = (await admin_client.post(AGR_URL, json=_agr_payload(seeded_vendor["id"]))).json()

    ok = await admin_client.patch(f"{AGR_URL}/{created['id']}", json={"title": "Renamed"})
    assert ok.status_code == 200
    assert ok.json()["title"] == "Renamed"

    # Force the agreement past draft, then the same edit must be refused.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == uuid.UUID(created["id"])))).scalar_one()
        agr.status = "active"
        await db.commit()

    blocked = await admin_client.patch(f"{AGR_URL}/{created['id']}", json={"title": "Nope"})
    assert blocked.status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
POSTGRES_SERVER=localhost POSTGRES_PORT=5432 POSTGRES_USER=uniops \
POSTGRES_PASSWORD=<local docker pw> POSTGRES_DB=epms_test JWT_SECRET_KEY=test \
pytest epms-api/tests/test_agreements.py -v
```

Expected: the six new tests FAIL with 404 (router not mounted).

- [ ] **Step 3: Write the schemas**

```python
# epms-api/app/schemas/agreement.py
"""Request/response schemas for Purchase Agreement (AGR)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

# 独立审批流(用户决策:不复用 PO 链)。这是种子默认值 —— 生产以 CompanyConfig
# .workflow_defs["agr"] 为准,管理员可在 Portal Admin 改。
AGR_WORKFLOW = [
    {"step": 0, "role": "dept_manager",        "label": "Department Manager"},
    {"step": 1, "role": "procurement_manager", "label": "Procurement Manager"},
    {"step": 2, "role": "finance_manager",     "label": "Finance Manager"},
]

AGREEMENT_TYPES = ("house_account", "recurring", "milestone")


class AgreementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    agreement_type: str = Field(pattern="^(house_account|recurring|milestone)$")
    vendor_id: uuid.UUID
    contract_no: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=255)
    vendor_reference: str | None = Field(default=None, max_length=100)
    valid_from: date
    valid_to: date
    grace_days: int = Field(default=30, ge=0, le=365)
    not_to_exceed: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="CAD", min_length=1, max_length=10)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    department_id: uuid.UUID | None = None
    budget_code: str | None = Field(default=None, max_length=100)
    owner_id: uuid.UUID | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validity_window_is_ordered(self):
        if self.valid_to < self.valid_from:
            raise ValueError("valid_to must be on or after valid_from")
        return self


class AgreementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    contract_no: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=255)
    vendor_reference: str | None = Field(default=None, max_length=100)
    valid_from: date | None = None
    valid_to: date | None = None
    grace_days: int | None = Field(default=None, ge=0, le=365)
    not_to_exceed: Decimal | None = Field(default=None, ge=0)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    department_id: uuid.UUID | None = None
    budget_code: str | None = Field(default=None, max_length=100)
    owner_id: uuid.UUID | None = None
    notes: str | None = None


class AgreementActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=20)   # submit|approve|return|cancel
    comment: str | None = None


class AgreementResponse(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    agreement_type: str
    contract_no: str | None
    contact_email: str | None
    vendor_id: uuid.UUID
    vendor_name: str
    vendor_reference: str | None
    valid_from: date
    valid_to: date
    grace_days: int
    not_to_exceed: Decimal | None
    consumed_amount: Decimal
    currency: str
    tax_code: str | None
    tax_rate: Decimal | None
    department_id: uuid.UUID | None
    budget_code: str | None
    owner_id: uuid.UUID | None
    status: str
    approval_step_idx: int
    notes: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgreementListResponse(BaseModel):
    items: list[AgreementResponse]
    total: int
```

- [ ] **Step 4: Write the CRUD layer**

```python
# epms-api/app/crud/agreement.py
"""CRUD for Purchase Agreement (AGR)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.agreement import PurchaseAgreement
from app.schemas.agreement import AgreementCreate, AgreementUpdate

# 只有 draft 可编辑 —— 一旦进入审批,改额度/有效期/供应商必须重走审批(spec §6)。
EDITABLE_STATUSES = ("draft", "returned")


async def _next_number(db: AsyncSession) -> str:
    ym = datetime.now(timezone.utc).strftime("%Y%m")
    return await next_number(db, PurchaseAgreement.number, f"AGR-{ym}-", width=4)


async def get_by_id(db: AsyncSession, agreement_id: uuid.UUID) -> PurchaseAgreement | None:
    return (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == agreement_id)
    )).scalar_one_or_none()


async def get_all(
    db: AsyncSession,
    *,
    status: str | None = None,
    vendor_id: uuid.UUID | None = None,
    agreement_type: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PurchaseAgreement], int]:
    q = select(PurchaseAgreement)
    if status:
        q = q.where(PurchaseAgreement.status == status)
    if vendor_id:
        q = q.where(PurchaseAgreement.vendor_id == vendor_id)
    if agreement_type:
        q = q.where(PurchaseAgreement.agreement_type == agreement_type)
    if search:
        like = f"%{search}%"
        q = q.where(or_(
            PurchaseAgreement.number.ilike(like),
            PurchaseAgreement.title.ilike(like),
            PurchaseAgreement.vendor_name.ilike(like),
            PurchaseAgreement.vendor_reference.ilike(like),
            PurchaseAgreement.contract_no.ilike(like),
        ))

    total = (await db.execute(
        select(func.count()).select_from(q.subquery())
    )).scalar_one()

    rows = (await db.execute(
        q.order_by(PurchaseAgreement.created_at.desc())
         .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return list(rows), total


async def create(
    db: AsyncSession,
    body: AgreementCreate,
    *,
    vendor_name: str,
    created_by: uuid.UUID,
) -> PurchaseAgreement:
    agr = PurchaseAgreement(
        number=await _next_number(db),
        vendor_name=vendor_name,
        created_by=created_by,
        **body.model_dump(),
    )
    db.add(agr)
    await db.commit()
    await db.refresh(agr)
    return agr


async def update(
    db: AsyncSession, agr: PurchaseAgreement, body: AgreementUpdate
) -> PurchaseAgreement:
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(agr, field, value)
    await db.commit()
    await db.refresh(agr)
    return agr
```

- [ ] **Step 5: Write the API endpoints**

```python
# epms-api/app/api/v1/agreements.py
"""Purchase Agreement endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.authz import require_permission
from app.core.deps import CurrentUserPayload, SessionDep
from app.crud import agreement as agr_crud
from app.crud import vendor as vendor_crud
from app.schemas.agreement import (
    AgreementCreate,
    AgreementListResponse,
    AgreementResponse,
    AgreementUpdate,
)

router = APIRouter(prefix="/agreements", tags=["purchase-agreements"])

AgrReadDep = Annotated[dict, Depends(require_permission("epms.agreement.read"))]
AgrWriteDep = Annotated[dict, Depends(require_permission("epms.agreement.write"))]


@router.get("", response_model=AgreementListResponse)
async def list_agreements(
    db: SessionDep,
    user: AgrReadDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    vendor_id: uuid.UUID | None = Query(default=None),
    agreement_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, le=200),
):
    items, total = await agr_crud.get_all(
        db, status=status_filter, vendor_id=vendor_id,
        agreement_type=agreement_type, search=search,
        page=page, page_size=page_size,
    )
    return {"items": items, "total": total}


@router.post("", response_model=AgreementResponse, status_code=status.HTTP_201_CREATED)
async def create_agreement(body: AgreementCreate, db: SessionDep, user: AgrWriteDep):
    vendor = await vendor_crud.get_by_id(db, body.vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return await agr_crud.create(
        db, body, vendor_name=vendor.name, created_by=uuid.UUID(user["sub"]),
    )


@router.get("/{agreement_id}", response_model=AgreementResponse)
async def get_agreement(agreement_id: uuid.UUID, db: SessionDep, user: AgrReadDep):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return agr


@router.patch("/{agreement_id}", response_model=AgreementResponse)
async def update_agreement(
    agreement_id: uuid.UUID, body: AgreementUpdate, db: SessionDep, user: AgrWriteDep
):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if agr.status not in agr_crud.EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Agreement is {agr.status}; only a draft agreement can be edited. "
                   "Changing terms after approval requires a new approval round.",
        )
    return await agr_crud.update(db, agr, body)
```

- [ ] **Step 6: Mount the router**

In `epms-api/app/main.py`, next to the existing `po`/`pa`/`invoices` router includes, add:

```python
from app.api.v1 import agreements as agreements_router
...
app.include_router(agreements_router.router, prefix=settings.API_V1_STR)
```

Match the exact import/include style already used in that file — do not invent a different prefix constant.

- [ ] **Step 7: Register the permission keys (identity-api)**

```python
# identity-api/alembic/versions/0006_agreement_perms.py
"""Register + grant the Purchase Agreement permission keys.

Phase-2 style keys (permission_defs + role_permissions) rather than a phase-1
matrix key: phase-1 keys live in seed_authz.MODULE_BY_KEY/DEFAULTS/LOCKED, are
re-derived by verify_gate_parity.py, and are hand-copied into epms-api's
conftest — four files that must stay in sync. Phase-2 needs only this migration
and seed_phase2_keys.py.

Seeding the grants here rather than leaving them for an admin to tick is
deliberate: the Create-GR cutover shipped a matrix-driven gate with no seeded
grant and 403'd everyone who previously had the button.

Idempotent (ON CONFLICT DO NOTHING); safe on a DB the seed scripts already
touched and self-sufficient on a fresh one.

Revision id is 26 chars — alembic_version_identity.version_num is varchar(32).
"""
from alembic import op

revision = "0006_agreement_perms"
down_revision = "0005_procurement_officer_pa"
branch_labels = None
depends_on = None

_KEYS = {
    "epms.agreement.read":  ("epms", "View Agreements", 104),
    "epms.agreement.write": ("epms", "Create / Edit Agreements", 105),
}

# Read: everyone already in the procurement/AP chain. Write: the roles that
# already hold epms.po.write, plus procurement_manager who owns the terms.
_GRANTS = {
    "epms.agreement.read": (
        "system_admin", "procurement_officer", "procurement_manager",
        "ap_clerk", "finance_bp", "finance_manager", "auditor",
    ),
    "epms.agreement.write": (
        "system_admin", "procurement_officer", "procurement_manager",
    ),
}


def upgrade() -> None:
    for key, (module, label, sort) in _KEYS.items():
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','{module}','{label}',{sort}) ON CONFLICT (key) DO NOTHING")
    for key, roles in _GRANTS.items():
        for role in roles:
            op.execute(
                "INSERT INTO role_permissions(role_code,permission_key) "
                f"VALUES ('{role}','{key}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grants + defs this migration added. Do NOT touch role_defs —
    # every role referenced here pre-exists.
    for key in _KEYS:
        op.execute(f"DELETE FROM role_permissions WHERE permission_key = '{key}'")
        op.execute(f"DELETE FROM permission_defs WHERE key = '{key}'")
```

Then mirror the same two keys into `identity-api/scripts/seed_phase2_keys.py` so a fresh DB seeded by script (not migration) also gets them — add to `PHASE2_KEYS`:

```python
    "epms.agreement.read":   ("epms",    "View Agreements",          104),
    "epms.agreement.write":  ("epms",    "Create / Edit Agreements", 105),
```

and to `PHASE2_DEFAULTS`:

```python
    "epms.agreement.read":  ("system_admin", "procurement_officer", "procurement_manager",
                             "ap_clerk", "finance_bp", "finance_manager", "auditor"),
    "epms.agreement.write": ("system_admin", "procurement_officer", "procurement_manager"),
```

The two lists must stay identical to `_KEYS` / `_GRANTS` above.

- [ ] **Step 8: Run the tests to verify they pass**

Same command as Step 2. Expected: all tests in `test_agreements.py` PASS.

- [ ] **Step 9: Confirm no regression in the rest of the suite**

```bash
POSTGRES_SERVER=localhost POSTGRES_PORT=5432 POSTGRES_USER=uniops \
POSTGRES_PASSWORD=<local docker pw> POSTGRES_DB=epms_test JWT_SECRET_KEY=test \
pytest epms-api/tests -q 2>&1 | tail -5
```

Expected: failure count is **69 or fewer** (the pre-existing baseline). A count above 69 is a regression introduced by this task — do not proceed until it is back at baseline. "No output" is not evidence of success; read the summary line.

- [ ] **Step 10: Commit**

```bash
git add epms-api/app/schemas/agreement.py epms-api/app/crud/agreement.py \
        epms-api/app/api/v1/agreements.py epms-api/app/main.py \
        epms-api/tests/test_agreements.py \
        identity-api/alembic/versions/0006_agreement_perms.py \
        identity-api/scripts/seed_phase2_keys.py
git commit -m "feat(epms): Purchase Agreement CRUD endpoints and permission keys"
```

---

### Task 3: The `agr` approval workflow

**Files:**
- Create: `approval-api/app/models/agreement.py`
- Modify: `approval-api/app/crud/engine.py`
- Modify: `epms-api/app/api/v1/agreements.py`
- Test: `epms-api/tests/test_agreements.py`, `approval-api/tests/test_agr_workflow.py`

**Interfaces:**
- Consumes: `PurchaseAgreement` table (Task 1); `app.services.approval_client.delegate_action(doc_type, doc_id, action, comment, bearer_token)`.
- Produces: action key `"agr"` accepted by the approval engine; `POST /api/v1/agreements/{id}/action` on epms-api; agreement reaches `status="active"` when the last step approves.

- [ ] **Step 1: Verify the physical columns before writing the mirror**

Mirror models have drifted from the physical table three times in this project. Confirm column names and types first:

```bash
docker exec uniops_postgres psql -U uniops -d epms_test -c "\d purchase_agreements"
```

Expected: the columns listed in Task 1. The mirror below must match them exactly.

- [ ] **Step 2: Write the failing test**

```python
# approval-api/tests/test_agr_workflow.py
"""agr action key — the Purchase Agreement approval chain."""
import pytest

from app.crud.engine import _DOC_META, _WORKFLOW_DEFAULTS, _resolve_meta

pytestmark = pytest.mark.asyncio


def test_agr_is_a_known_action_key():
    meta = _resolve_meta("agr")
    assert meta["model"].__tablename__ == "purchase_agreements"
    assert meta["number_attr"] == "number"
    assert meta["task_approve"] == "approve_agr"
    assert meta["task_revise"] == "revise_agr"
    assert "draft" in meta["valid_submit"]


def test_agr_has_seeded_default_workflow():
    steps = _WORKFLOW_DEFAULTS["agr"]
    assert [s["role"] for s in steps] == [
        "dept_manager", "procurement_manager", "finance_manager"]
    assert all("id" in s and "label" in s for s in steps)


def test_agr_amount_attr_points_at_a_real_column():
    # NTE is nullable; the engine must not blow up on an agreement without one.
    meta = _DOC_META["agr"]
    assert hasattr(meta["model"], meta["amount_attr"])
```

And append the end-to-end test to `epms-api/tests/test_agreements.py`:

```python
async def test_agreement_action_endpoint_delegates(admin_client, seeded_vendor, monkeypatch):
    created = (await admin_client.post(AGR_URL, json=_agr_payload(seeded_vendor["id"]))).json()

    calls = []

    async def _fake_delegate(doc_type, doc_id, action, comment, token):
        calls.append((doc_type, doc_id, action))
        return {"status": "in_review", "step_idx": 1}

    monkeypatch.setattr("app.api.v1.agreements.delegate_action", _fake_delegate)

    r = await admin_client.post(f"{AGR_URL}/{created['id']}/action",
                                json={"action": "submit", "comment": None})
    assert r.status_code == 200, r.text
    assert calls == [("agr", created["id"], "submit")]


async def test_agreement_action_unknown_id_404(admin_client):
    r = await admin_client.post(f"{AGR_URL}/{uuid.uuid4()}/action", json={"action": "submit"})
    assert r.status_code == 404
```

- [ ] **Step 3: Run both tests to verify they fail**

```bash
POSTGRES_SERVER=localhost POSTGRES_PORT=5432 POSTGRES_USER=uniops \
POSTGRES_PASSWORD=<local docker pw> POSTGRES_DB=approval_test JWT_SECRET_KEY=test \
pytest approval-api/tests/test_agr_workflow.py -v
```

Expected: FAIL — `KeyError: Unknown action key: agr`.

- [ ] **Step 4: Write the approval-api mirror model**

```python
# approval-api/app/models/agreement.py
"""Read-write mirror of EPMS purchase_agreements — workflow execution only.

Only the columns the engine touches are mirrored. Verified against the physical
table (\\d purchase_agreements) before writing — mirror drift has bitten this
project three times.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PurchaseAgreement(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "purchase_agreements"

    number: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_step_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # NTE is nullable — an agreement may be approved without a ceiling.
    not_to_exceed: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
```

- [ ] **Step 5: Register `agr` in the engine**

In `approval-api/app/crud/engine.py`, import the mirror alongside the other model imports:

```python
from app.models.agreement import PurchaseAgreement
```

Add to `_DOC_META`, immediately after the `"po"` entry (keep it in the EPMS procurement block):

```python
    # ── EPMS purchase agreements ──────────────────────────────────────────────
    # Terminal approve flips status to "active" (not "approved") — an agreement
    # is an authorisation window, and "active" is what the invoice match
    # candidate pool filters on. Handled by _post_approve_agr below.
    "agr": {
        "model":        PurchaseAgreement,
        "number_attr":  "number",
        "amount_attr":  "not_to_exceed",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_agr",
        "task_revise":  "revise_agr",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned", "submitted"),
    },
```

Add to `_WORKFLOW_DEFAULTS` (the dict at ~line 230), keeping the same shape as the `"exp"` entry:

```python
    "agr": [
        {"id": "dept_manager",        "role": "dept_manager",        "label": "Department Manager"},
        {"id": "procurement_manager", "role": "procurement_manager", "label": "Procurement Manager"},
        {"id": "finance_manager",     "role": "finance_manager",     "label": "Finance Manager"},
    ],
```

`app/main.py::seed_default_workflows` copies any missing `_WORKFLOW_DEFAULTS` key into `CompanyConfig.workflow_defs` on boot and never overwrites an admin-configured key, so this needs **no migration**.

- [ ] **Step 6: Add the post-approve hook**

Define it next to the other `_post_approve_*` functions:

```python
async def _post_approve_agr(db, doc, token: str | None = None) -> None:
    """Final approval activates the agreement rather than marking it 'approved'.

    The invoice match candidate pool filters on status == "active" (plus the
    grace window), so leaving it at "approved" would approve an agreement that
    no invoice could ever be matched to.
    """
    doc.status = "active"
```

and register it in the post-approve dispatch dict:

```python
    "agr":    _post_approve_agr,
```

Match the exact signature of the neighbouring hooks (`_post_approve_po` etc.) — copy theirs rather than the sketch above if it differs.

- [ ] **Step 7: Add the epms-api action endpoint**

Append to `epms-api/app/api/v1/agreements.py`:

```python
from app.core.deps import BearerToken
from app.services.approval_client import delegate_action
from app.schemas.agreement import AgreementActionRequest


@router.post("/{agreement_id}/action", response_model=AgreementResponse)
async def agreement_action(
    agreement_id: uuid.UUID,
    body: AgreementActionRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    agr = await agr_crud.get_by_id(db, agreement_id)
    if agr is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    try:
        await delegate_action("agr", str(agreement_id), body.action, body.comment, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    await db.refresh(agr)
    return agr
```

Note the gate: this endpoint uses `CurrentUserPayload`, not `AgrWriteDep` — approval authority comes from the open task in the approval engine, not from the write permission. This mirrors `po_action` (`epms-api/app/api/v1/po.py:191-209`).

- [ ] **Step 8: Run both test files to verify they pass**

Run the approval-api command from Step 3, then the epms-api command from Task 2 Step 2. Expected: PASS in both. approval-api's suite baseline is **53 passed, 0 failed** — it must stay fully green.

- [ ] **Step 9: Commit**

```bash
git add approval-api/app/models/agreement.py approval-api/app/crud/engine.py \
        approval-api/tests/test_agr_workflow.py \
        epms-api/app/api/v1/agreements.py epms-api/tests/test_agreements.py
git commit -m "feat(approval): add agr action key and wire agreement approval"
```

---

### Task 4: Link columns on invoices and payment_applications

**Files:**
- Create: `epms-api/alembic/versions/ag02_agreement_links.py`
- Modify: `epms-api/app/models/invoice.py`, `epms-api/app/models/pa.py`, `epms-api/app/schemas/invoice.py`, `epms-api/app/schemas/pa.py`
- Test: `epms-api/tests/test_agreement_invoice_match.py`

**Interfaces:**
- Produces on `Invoice`: `agreement_id: uuid.UUID | None`, `agreement_number: str | None`, `match_route: str | None` (`"po"` | `"agreement"`), `match_route_auto: bool`, `legacy_settlement: bool`, `legacy_settlement_reason: str | None`.
- Produces on `PaymentApplication`: `agreement_id: uuid.UUID | None`, `agreement_number: str | None`.

- [ ] **Step 1: Count the consumers before adding columns**

`invoices` and `payment_applications` are read by more than one service. Missing one has cost this project a full service tree four times. Enumerate them and confirm which need the mirror updated:

```bash
cd /c/Project/uniops-agreement
grep -rn "payment_applications\|__tablename__ = \"invoices\"" --include=*.py \
  epms-api approval-api expense-api finance-api | grep -v tests | sort
```

Expected finding: epms-api owns both tables; approval-api, expense-api, and finance-api hold mirrors. **The new columns are nullable with defaults, so existing mirrors keep working unread** — no mirror needs updating in 1A. Record that conclusion in the commit message. If the grep shows a mirror that does `SELECT *` into a strict schema, update that mirror too.

- [ ] **Step 2: Write the failing test**

```python
# epms-api/tests/test_agreement_invoice_match.py
"""Invoice ↔ Agreement matching (Phase 1A: manual route, no slips)."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agreement import PurchaseAgreement
from app.models.invoice import Invoice

pytestmark = pytest.mark.asyncio

AGR_URL = "/api/v1/agreements"
INV_URL = "/api/v1/invoices"


async def test_invoice_carries_agreement_link_columns(test_engine, seeded_vendor, system_user_id):
    """The new columns exist, default correctly, and round-trip."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        inv = Invoice(
            internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_invoice_number="PA-STMT-202607",
            vendor_id=uuid.UUID(seeded_vendor["id"]),
            vendor_name=seeded_vendor["name"],
            amount=Decimal("1000.00"),
            tax_amount=Decimal("130.00"),
            total_amount=Decimal("1130.00"),
            invoice_date="2026-07-31",
            due_date="2026-08-30",
            uploaded_by=uuid.UUID(system_user_id),
            line_items=[],
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)

        assert inv.agreement_id is None
        assert inv.agreement_number is None
        assert inv.match_route is None
        assert inv.match_route_auto is False
        assert inv.legacy_settlement is False
        assert inv.legacy_settlement_reason is None
```

- [ ] **Step 3: Run it to verify it fails**

```bash
POSTGRES_SERVER=localhost POSTGRES_PORT=5432 POSTGRES_USER=uniops \
POSTGRES_PASSWORD=<local docker pw> POSTGRES_DB=epms_test JWT_SECRET_KEY=test \
pytest epms-api/tests/test_agreement_invoice_match.py -v
```

Expected: FAIL — `AttributeError: 'Invoice' object has no attribute 'agreement_id'`.

- [ ] **Step 4: Add the model columns**

In `epms-api/app/models/invoice.py`, after the existing `gr_ids` block:

```python
    # ── Agreement route (blanket/house-account/contract spend) ────────────────
    # Mutually exclusive with the PO route in practice, but both columns are kept
    # nullable so a re-match can flip an invoice from one route to the other.
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_agreements.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )
    agreement_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # "po" | "agreement" — which candidate pool this invoice was matched against.
    match_route: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # True only when the system resolved the route itself (1B). A human override
    # sets it False, which doubles as a health signal: a vendor whose route is
    # constantly corrected by hand has a mis-registered vendor_reference.
    match_route_auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    # ⚠️ Backlog escape hatch: paid against an agreement with NO pickup-slip
    # evidence. Opened for the 1A invoice backlog; MUST be narrowed once 1B ships
    # slip reconciliation, or it becomes the standard way to bypass matching.
    legacy_settlement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false")
    legacy_settlement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

`Boolean` must be added to the `sqlalchemy` import line at the top of that file (it currently imports `Date, DateTime, ForeignKey, Numeric, String, Text`).

In `epms-api/app/models/pa.py`, after the `po_number` column:

```python
    # Agreement-sourced PA: po_id is NULL and this is set instead. EPMS's PA list
    # admits a PA when EITHER is non-NULL (OA's Direct PAs have both NULL).
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_agreements.id", ondelete="RESTRICT"),
        nullable=True, index=True
    )
    agreement_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
```

- [ ] **Step 5: Write the migration**

```python
# epms-api/alembic/versions/ag02_agreement_links.py
"""link invoices and payment_applications to purchase_agreements

Revision ID: ag02_agreement_links
Revises: ag01_purchase_agreements
Create Date: 2026-08-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ag02_agreement_links"
down_revision = "ag01_purchase_agreements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("agreement_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("invoices", sa.Column("agreement_number", sa.String(40), nullable=True))
    op.add_column("invoices", sa.Column("match_route", sa.String(20), nullable=True))
    op.add_column("invoices", sa.Column("match_route_auto", sa.Boolean(), nullable=False,
                                        server_default="false"))
    op.add_column("invoices", sa.Column("legacy_settlement", sa.Boolean(), nullable=False,
                                        server_default="false"))
    op.add_column("invoices", sa.Column("legacy_settlement_reason", sa.Text(), nullable=True))
    op.create_foreign_key("fk_invoices_agreement_id", "invoices", "purchase_agreements",
                          ["agreement_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_invoices_agreement_id", "invoices", ["agreement_id"])

    op.add_column("payment_applications",
                  sa.Column("agreement_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("payment_applications", sa.Column("agreement_number", sa.String(40), nullable=True))
    op.create_foreign_key("fk_pa_agreement_id", "payment_applications", "purchase_agreements",
                          ["agreement_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_payment_applications_agreement_id", "payment_applications", ["agreement_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_applications_agreement_id", table_name="payment_applications")
    op.drop_constraint("fk_pa_agreement_id", "payment_applications", type_="foreignkey")
    op.drop_column("payment_applications", "agreement_number")
    op.drop_column("payment_applications", "agreement_id")

    op.drop_index("ix_invoices_agreement_id", table_name="invoices")
    op.drop_constraint("fk_invoices_agreement_id", "invoices", type_="foreignkey")
    op.drop_column("invoices", "legacy_settlement_reason")
    op.drop_column("invoices", "legacy_settlement")
    op.drop_column("invoices", "match_route_auto")
    op.drop_column("invoices", "match_route")
    op.drop_column("invoices", "agreement_number")
    op.drop_column("invoices", "agreement_id")
```

- [ ] **Step 6: Surface the fields in the response schemas**

In `epms-api/app/schemas/invoice.py`, add to `InvoiceResponse`:

```python
    agreement_id: uuid.UUID | None = None
    agreement_number: str | None = None
    match_route: str | None = None
    match_route_auto: bool = False
    legacy_settlement: bool = False
    legacy_settlement_reason: str | None = None
```

In `epms-api/app/schemas/pa.py`, add to the PA response model:

```python
    agreement_id: uuid.UUID | None = None
    agreement_number: str | None = None
```

- [ ] **Step 7: Run the test to verify it passes**

Same command as Step 3. Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add epms-api/alembic/versions/ag02_agreement_links.py \
        epms-api/app/models/invoice.py epms-api/app/models/pa.py \
        epms-api/app/schemas/invoice.py epms-api/app/schemas/pa.py \
        epms-api/tests/test_agreement_invoice_match.py
git commit -m "feat(epms): add agreement link columns to invoices and PAs

Consumers audited: epms-api owns both tables; approval-api / expense-api /
finance-api mirrors read named columns only, and every new column is nullable
with a default, so no mirror needs updating."
```

---

### Task 5: Agreement candidates on the match endpoint

**Files:**
- Modify: `epms-api/app/api/v1/invoices.py`, `epms-api/app/crud/agreement.py`
- Test: `epms-api/tests/test_agreement_invoice_match.py`

**Interfaces:**
- Produces: `GET /api/v1/invoices/{invoice_id}/agreement-candidates` → `{"items": [AgreementResponse], "total": int}`, filtered to the invoice's vendor and to agreements inside the candidate window.
- Produces: `app.crud.agreement.candidates_for_vendor(db, vendor_id, on_date) -> list[PurchaseAgreement]`

**Design note:** a **separate endpoint** rather than widening `match-candidates`. `match-candidates` is typed `response_model=PoListResponse`; returning a union would either break that contract or force a discriminated wrapper that every existing caller must learn. Two endpoints keep both response models honest, and the frontend already fetches per-tab.

- [ ] **Step 1: Write the failing test**

Append to `epms-api/tests/test_agreement_invoice_match.py`:

```python
from datetime import date, timedelta


async def _make_active_agreement(test_engine, vendor_id, system_user_id, **over):
    """Insert an active agreement directly — bypasses the approval chain, which
    needs a running approval-api this suite does not have."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    fields = dict(
        number=f"AGR-202608-{uuid.uuid4().hex[:4]}",
        title="Test house account",
        agreement_type="house_account",
        vendor_id=uuid.UUID(vendor_id),
        vendor_name="Test Vendor",
        valid_from=date.today() - timedelta(days=30),
        valid_to=date.today() + timedelta(days=30),
        status="active",
        created_by=uuid.UUID(system_user_id),
    )
    fields.update(over)
    async with factory() as db:
        agr = PurchaseAgreement(**fields)
        db.add(agr)
        await db.commit()
        await db.refresh(agr)
        return agr


async def _upload_invoice(client, vendor_id, amount="1000.00"):
    r = await client.post(INV_URL, json={
        "vendor_invoice_number": f"STMT-{uuid.uuid4().hex[:6]}",
        "vendor_id": vendor_id,
        "amount": amount,
        "tax_amount": "0.00",
        "total_amount": amount,
        "invoice_date": "2026-07-31",
        "due_date": "2026-08-30",
        "line_items": [{"description": "Monthly statement", "quantity": "1",
                        "unit_price": amount, "line_total": amount}],
    })
    r.raise_for_status()
    return r.json()


async def test_agreement_candidates_returns_active_same_vendor(
    admin_client, test_engine, seeded_vendor, system_user_id
):
    agr = await _make_active_agreement(test_engine, seeded_vendor["id"], system_user_id)
    inv = await _upload_invoice(admin_client, seeded_vendor["id"])

    r = await admin_client.get(f"{INV_URL}/{inv['id']}/agreement-candidates")
    assert r.status_code == 200, r.text
    assert str(agr.id) in [i["id"] for i in r.json()["items"]]


async def test_agreement_candidates_excludes_draft_and_cancelled(
    admin_client, test_engine, seeded_vendor, system_user_id
):
    draft = await _make_active_agreement(test_engine, seeded_vendor["id"], system_user_id,
                                         status="draft")
    cancelled = await _make_active_agreement(test_engine, seeded_vendor["id"], system_user_id,
                                             status="cancelled")
    inv = await _upload_invoice(admin_client, seeded_vendor["id"])

    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(draft.id) not in ids
    assert str(cancelled.id) not in ids


async def test_agreement_candidates_excludes_other_vendors(
    admin_client, test_engine, seeded_vendor, system_user_id, pg_cur
):
    pg_cur.execute(
        "INSERT INTO business_partners(id,code,name,is_active,is_vendor) "
        "VALUES (gen_random_uuid(),%s,%s,true,true) RETURNING id",
        (f"V{uuid.uuid4().hex[:6]}", "Other Vendor"))
    other_vendor_id = str(pg_cur.fetchone()[0])

    other = await _make_active_agreement(test_engine, other_vendor_id, system_user_id)
    inv = await _upload_invoice(admin_client, seeded_vendor["id"])

    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(other.id) not in ids


async def test_expired_agreement_inside_grace_is_a_candidate(
    admin_client, test_engine, seeded_vendor, system_user_id
):
    """8/31 expiry, 9/3 statement — the month's bill always lands after expiry."""
    agr = await _make_active_agreement(
        test_engine, seeded_vendor["id"], system_user_id,
        status="expired",
        valid_from=date.today() - timedelta(days=90),
        valid_to=date.today() - timedelta(days=5),
        grace_days=30,
    )
    inv = await _upload_invoice(admin_client, seeded_vendor["id"])
    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(agr.id) in ids


async def test_expired_agreement_past_grace_is_not_a_candidate(
    admin_client, test_engine, seeded_vendor, system_user_id
):
    agr = await _make_active_agreement(
        test_engine, seeded_vendor["id"], system_user_id,
        status="expired",
        valid_from=date.today() - timedelta(days=200),
        valid_to=date.today() - timedelta(days=100),
        grace_days=30,
    )
    inv = await _upload_invoice(admin_client, seeded_vendor["id"])
    ids = [i["id"] for i in (await admin_client.get(
        f"{INV_URL}/{inv['id']}/agreement-candidates")).json()["items"]]
    assert str(agr.id) not in ids
```

If the invoice-upload payload above does not match the current `InvoiceCreate` schema, copy the exact payload helper from `epms-api/tests/test_invoice_allocations.py::_inv_payload` instead of inventing one.

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL with 404 (endpoint does not exist).

- [ ] **Step 3: Add the candidate query to the CRUD layer**

Append to `epms-api/app/crud/agreement.py`:

```python
from datetime import date

from sqlalchemy import and_, literal


async def candidates_for_vendor(
    db: AsyncSession, vendor_id: uuid.UUID, on_date: date | None = None
) -> list[PurchaseAgreement]:
    """Agreements an invoice from this vendor may be matched against.

    Admission (spec §5.1): status == "active", OR status == "expired" and today
    is still within valid_to + grace_days. The grace branch exists because a
    period's statement always arrives after the period closes — an agreement
    expiring 8/31 still has to absorb the invoice that lands on 9/3.

    The grace predicate is written as an integer day difference rather than an
    interval addition: in Postgres `date - date` yields an integer number of
    days, so `(today - valid_to) <= grace_days` needs no interval construction
    and reads the same as the rule it encodes.
    """
    today = on_date or date.today()
    q = select(PurchaseAgreement).where(
        PurchaseAgreement.vendor_id == vendor_id,
        or_(
            PurchaseAgreement.status == "active",
            and_(
                PurchaseAgreement.status == "expired",
                (literal(today) - PurchaseAgreement.valid_to) <= PurchaseAgreement.grace_days,
            ),
        ),
    ).order_by(PurchaseAgreement.number)
    return list((await db.execute(q)).scalars().all())
```

Add `and_` and `literal` to the existing `sqlalchemy` import in that file.

Both grace tests must pass together — `test_expired_agreement_inside_grace_is_a_candidate` and `test_expired_agreement_past_grace_is_not_a_candidate`. One passing alone does not prove the boundary is right; an always-true predicate passes the first and fails the second, an always-false one does the reverse.

- [ ] **Step 4: Add the endpoint**

In `epms-api/app/api/v1/invoices.py`, next to `list_match_candidates`:

```python
@router.get("/{invoice_id}/agreement-candidates", response_model=AgreementListResponse)
async def list_agreement_candidates(
    invoice_id: uuid.UUID,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """Agreements this invoice may be matched to (same vendor, inside the
    admission window). Authorised exactly like list_match_candidates — by the
    invoice's match permission, NOT by a generic agreement scope, or an assignee
    with no related PR sees zero candidates and deadlocks."""
    inv = await invoice_crud.get_by_id(db, invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    caller_id = uuid.UUID(user["sub"])
    is_uploader = inv.uploaded_by == caller_id
    if (user.get("role") not in _AP_ROLES and not is_uploader
            and not await _has_open_match_task(db, caller_id, invoice_id)):
        raise HTTPException(status_code=403, detail="Not allowed to match this invoice")

    items = await agreement_crud.candidates_for_vendor(db, inv.vendor_id)
    return {"items": items, "total": len(items)}
```

Add the imports `from app.crud import agreement as agreement_crud` and `from app.schemas.agreement import AgreementListResponse`.

- [ ] **Step 5: Run the tests to verify they pass**

Expected: all six candidate tests PASS.

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/crud/agreement.py epms-api/app/api/v1/invoices.py \
        epms-api/tests/test_agreement_invoice_match.py
git commit -m "feat(epms): expose agreement candidates for invoice matching"
```

---

### Task 6: Match an invoice to an agreement

**Files:**
- Modify: `epms-api/app/schemas/invoice.py`, `epms-api/app/crud/invoice.py`, `epms-api/app/api/v1/invoices.py`
- Test: `epms-api/tests/test_agreement_invoice_match.py`

**Interfaces:**
- Consumes: `InvoiceMatchRequest` (existing), `candidates_for_vendor` (Task 5), agreement link columns (Task 4).
- Produces: `InvoiceMatchRequest.agreement_id: uuid.UUID | None` and `.legacy_settlement_reason: str | None`; a new exception `AgreementMatchInvalid` raised as **422**.
- Post-condition of an agreement match: `status="matched"`, `agreement_id`/`agreement_number` set, `match_route="agreement"`, `match_route_auto=False`, `po_id=None`, `variance=0`, `po_total=0`, `exception_reason=None`, and `agreement.consumed_amount` increased by the invoice's `total_amount`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_match_to_agreement_sets_route_and_consumes(
    admin_client, test_engine, seeded_vendor, system_user_id
):
    agr = await _make_active_agreement(test_engine, seeded_vendor["id"], system_user_id,
                                       not_to_exceed=Decimal("50000.00"))
    inv = await _upload_invoice(admin_client, seeded_vendor["id"], amount="1000.00")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id),
        "legacy_settlement_reason": "Backlog statement, paper slips held by Finance",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "matched"
    assert body["agreement_id"] == str(agr.id)
    assert body["agreement_number"] == agr.number
    assert body["match_route"] == "agreement"
    assert body["match_route_auto"] is False
    assert body["po_id"] is None
    assert Decimal(body["variance"]) == Decimal("0")
    assert body["legacy_settlement"] is True

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("1000.00")


async def test_match_to_agreement_requires_a_reason_in_1a(
    admin_client, test_engine, seeded_vendor, system_user_id
):
    """1A has no pickup slips, so every agreement match is a legacy settlement
    and must carry a reason. 1B replaces this with real slip reconciliation."""
    agr = await _make_active_agreement(test_engine, seeded_vendor["id"], system_user_id)
    inv = await _upload_invoice(admin_client, seeded_vendor["id"])

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match",
                                json={"agreement_id": str(agr.id)})
    assert r.status_code == 422
    assert "reason" in r.text.lower()


async def test_match_to_agreement_rejects_vendor_mismatch(
    admin_client, test_engine, seeded_vendor, system_user_id, pg_cur
):
    pg_cur.execute(
        "INSERT INTO business_partners(id,code,name,is_active,is_vendor) "
        "VALUES (gen_random_uuid(),%s,%s,true,true) RETURNING id",
        (f"V{uuid.uuid4().hex[:6]}", "Other Vendor"))
    other_vendor_id = str(pg_cur.fetchone()[0])
    agr = await _make_active_agreement(test_engine, other_vendor_id, system_user_id)
    inv = await _upload_invoice(admin_client, seeded_vendor["id"])

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "x"})
    assert r.status_code == 422
    assert "vendor" in r.text.lower()


async def test_match_to_draft_agreement_is_refused(
    admin_client, test_engine, seeded_vendor, system_user_id
):
    agr = await _make_active_agreement(test_engine, seeded_vendor["id"], system_user_id,
                                       status="draft")
    inv = await _upload_invoice(admin_client, seeded_vendor["id"])
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "x"})
    assert r.status_code == 422


async def test_agreement_match_does_not_block_when_over_nte(
    admin_client, test_engine, seeded_vendor, system_user_id
):
    """NTE warns, it does not block (user decision). An invoice that pushes
    consumed_amount past the ceiling must still match."""
    agr = await _make_active_agreement(test_engine, seeded_vendor["id"], system_user_id,
                                       not_to_exceed=Decimal("100.00"))
    inv = await _upload_invoice(admin_client, seeded_vendor["id"], amount="1000.00")

    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    assert r.status_code == 200

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("1000.00")   # over the 100.00 ceiling, allowed


async def test_rematch_from_agreement_to_po_releases_consumption(
    admin_client, test_engine, seeded_vendor, system_user_id
):
    """Re-matching must not leave the agreement's consumed_amount inflated."""
    agr = await _make_active_agreement(test_engine, seeded_vendor["id"], system_user_id)
    inv = await _upload_invoice(admin_client, seeded_vendor["id"], amount="1000.00")

    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "first pass"})
    # Re-match to the SAME agreement — consumption must not double-count.
    await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "corrected"})

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        fresh = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == agr.id))).scalar_one()
    assert fresh.consumed_amount == Decimal("1000.00")
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — the match endpoint rejects the payload (`AllocationImbalance` / `FeeOnlyLinkRequired`) because it has no agreement branch.

- [ ] **Step 3: Extend the request schema**

In `epms-api/app/schemas/invoice.py`, add to `InvoiceMatchRequest`:

```python
    # Agreement route: takes priority over every PO field when set. The invoice
    # is linked to the agreement for traceability and paid in full from the AP
    # header — there is no line reference to measure a variance against.
    agreement_id: uuid.UUID | None = None
    # 1A only: no pickup slips exist yet, so an agreement match is by definition
    # settled without receipt evidence and must record why.
    legacy_settlement_reason: str | None = None
```

- [ ] **Step 4: Implement the agreement branch**

In `epms-api/app/crud/invoice.py`, define the exception next to `FeeOnlyLinkRequired`:

```python
class AgreementMatchInvalid(Exception):
    """The invoice cannot be matched to the requested agreement."""
```

Then add the branch at the **top** of `match()`, before `_normalize_allocations` — the agreement route shares none of the allocation machinery, and running the balance check first would reject a perfectly valid statement:

```python
async def _recompute_consumed(db: AsyncSession, agreement_id: uuid.UUID) -> None:
    """consumed_amount is derived, never incremented.

    Deriving it from the invoice set makes re-match idempotent: incrementing
    would double-count every correction, and the number drives the NTE warning
    banner AP looks at.
    """
    total = (await db.execute(
        select(func.coalesce(func.sum(Invoice.total_amount), Decimal("0")))
        .where(Invoice.agreement_id == agreement_id)
    )).scalar_one()
    agr = (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == agreement_id)
    )).scalar_one()
    agr.consumed_amount = total


async def _match_to_agreement(
    db: AsyncSession, invoice: Invoice, req: InvoiceMatchRequest, matched_by: uuid.UUID
) -> Invoice:
    from datetime import date, timedelta

    agr = (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == req.agreement_id)
    )).scalar_one_or_none()
    if agr is None:
        raise ValueError(f"Agreement {req.agreement_id} not found")
    if agr.vendor_id != invoice.vendor_id:
        raise AgreementMatchInvalid(
            "Agreement must belong to the same vendor as the invoice")

    today = date.today()
    in_window = agr.status == "active" or (
        agr.status == "expired"
        and agr.valid_to + timedelta(days=agr.grace_days or 0) >= today
    )
    if not in_window:
        raise AgreementMatchInvalid(
            f"Agreement {agr.number} is {agr.status} and outside its grace window; "
            "renew it before matching invoices to it.")

    # 1A: no pickup slips exist, so every agreement match settles without receipt
    # evidence. Force the operator to say why, and flag the row — this path MUST
    # be narrowed once 1B ships slip reconciliation.
    reason = (req.legacy_settlement_reason or "").strip()
    if not reason:
        raise AgreementMatchInvalid(
            "A reason is required to settle an agreement invoice without receipt evidence")

    previous_agreement_id = invoice.agreement_id

    # Clear any PO-route state so a re-routed invoice doesn't carry stale links.
    await db.execute(sa_delete(InvoicePoAllocation).where(
        InvoicePoAllocation.invoice_id == invoice.id))
    invoice.po_id = None
    invoice.po_number = None
    invoice.matched_po_line_ids = None
    invoice.matched_reference_total = None
    invoice.gr_ids = None
    invoice.gr_id = None
    invoice.gr_number = None

    invoice.agreement_id = agr.id
    invoice.agreement_number = agr.number
    invoice.match_route = "agreement"
    invoice.match_route_auto = False        # 1A is manual selection only
    invoice.legacy_settlement = True
    invoice.legacy_settlement_reason = reason
    invoice.po_total = Decimal("0")
    invoice.gr_value = None
    invoice.variance = Decimal("0")
    invoice.variance_pct = Decimal("0")
    invoice.exception_reason = None
    invoice.matched_at = datetime.now(timezone.utc)
    invoice.matched_by = matched_by
    invoice.matched_by_name = (await db.execute(
        select(User.full_name).where(User.id == matched_by)
    )).scalar_one_or_none()
    invoice.status = "matched"

    await db.flush()
    await _recompute_consumed(db, agr.id)
    if previous_agreement_id and previous_agreement_id != agr.id:
        await _recompute_consumed(db, previous_agreement_id)
    await db.commit()
    await db.refresh(invoice)
    return invoice
```

and dispatch to it as the first thing `match()` does:

```python
async def match(
    db: AsyncSession,
    invoice: Invoice,
    req: InvoiceMatchRequest,
    matched_by: uuid.UUID,
    require_review: bool = False,
    auto_link_grs: bool = False,
) -> Invoice:
    # Agreement route short-circuits: it shares none of the PO allocation
    # machinery (no lines, no GRs, no balance check), and running that first
    # would reject a valid monthly statement.
    if req.agreement_id is not None:
        return await _match_to_agreement(db, invoice, req, matched_by)

    now = datetime.now(timezone.utc)
    ...
```

Import `PurchaseAgreement` at the top of `crud/invoice.py`.

- [ ] **Step 5: Register the new exception on the endpoint**

In `epms-api/app/api/v1/invoices.py`, add `AgreementMatchInvalid` to the import from `app.crud.invoice` and to the 422 `except` tuple (currently `(AllocationImbalance, LegacyMatchUnsupported, FeeOnlyLinkRequired)` at line ~370). An unregistered exception escapes as a 500.

- [ ] **Step 6: Suppress the create_pa requester broadcast for agreement matches**

The match endpoint calls `_notify_requester_create_pa` after a successful match. That helper resolves a requester through `po.pr_id`; an agreement-matched invoice has `po_id = None`, so confirm it short-circuits. Read the helper and verify. If it does not short-circuit on a NULL `po_id`, add an explicit guard:

```python
    if result.match_route == "agreement":
        pass   # agreement invoices have no PR requester to notify
    else:
        await _notify_requester_create_pa(...)
```

This is not hypothetical: a PO with no PR previously broadcast an acknowledgement task to 59 people twice. Prove the short-circuit with a test that asserts no `create_pa` task row exists after an agreement match.

- [ ] **Step 7: Run the tests to verify they pass**

Expected: all agreement-match tests PASS.

- [ ] **Step 8: Run the full suite for regressions**

Expected: failure count ≤ 69.

- [ ] **Step 9: Commit**

```bash
git add epms-api/app/schemas/invoice.py epms-api/app/crud/invoice.py \
        epms-api/app/api/v1/invoices.py epms-api/tests/test_agreement_invoice_match.py
git commit -m "feat(epms): match invoices to a purchase agreement (2-way, no GR)"
```

---

### Task 7: Pay an agreement invoice

**Files:**
- Modify: `epms-api/app/schemas/pa.py`, `epms-api/app/crud/pa.py`, `epms-api/app/api/v1/pa.py`
- Test: `epms-api/tests/test_agreement_pa.py`

**Interfaces:**
- Consumes: `Invoice.agreement_id` / `match_route` (Task 6); `PaymentApplication.agreement_id` (Task 4).
- Produces: `PaCreate.agreement_id: uuid.UUID | None`; `po_id` becomes optional on `PaCreate` when `agreement_id` is present; EPMS PA list admits `po_id IS NOT NULL OR agreement_id IS NOT NULL`.

- [ ] **Step 1: Write the failing tests**

```python
# epms-api/tests/test_agreement_pa.py
"""PA created from an agreement-matched invoice (no PO, no GR)."""
import uuid
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio

PA_URL = "/api/v1/pa"


async def test_create_pa_from_agreement(admin_client, agreement_matched_invoice):
    inv, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "Princess Auto July statement",
        "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00",
        "tax_amount": "130.00",
        "payment_amount": "1130.00",
        "line_items": [{"description": "July statement", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["agreement_id"] == str(agr.id)
    assert body["agreement_number"] == agr.number
    assert body["po_id"] is None
    assert body["status"] == "draft"


async def test_agreement_pa_skips_the_goods_receipt_gate(admin_client, agreement_matched_invoice):
    """No GR exists and none ever will for this route — the gate must not fire,
    and no receipt_override reason should be demanded."""
    inv, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "No GR here",
        "agreement_id": str(agr.id),
        "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })
    assert r.status_code == 201, r.text
    assert r.json()["receipt_override"] is False


async def test_agreement_pa_appears_in_the_epms_list(admin_client, agreement_matched_invoice):
    """crud.pa.get_all filters on po_id IS NOT NULL to keep OA's Direct PAs out;
    that filter must not also hide agreement PAs."""
    inv, agr = agreement_matched_invoice
    created = (await admin_client.post(PA_URL, json={
        "title": "Listed?", "agreement_id": str(agr.id), "invoice_ids": [inv["id"]],
        "subtotal": "1000.00", "tax_amount": "0.00", "payment_amount": "1000.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1000.00", "line_total": "1000.00"}],
    })).json()

    listed = await admin_client.get(PA_URL)
    assert listed.status_code == 200
    assert created["id"] in [i["id"] for i in listed.json()["items"]]


async def test_pa_requires_exactly_one_source(admin_client, agreement_matched_invoice):
    inv, agr = agreement_matched_invoice
    neither = await admin_client.post(PA_URL, json={
        "title": "Neither", "invoice_ids": [inv["id"]],
        "subtotal": "1.00", "tax_amount": "0.00", "payment_amount": "1.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1.00", "line_total": "1.00"}],
    })
    assert neither.status_code == 422
    assert "po_id" in neither.text or "agreement_id" in neither.text


async def test_invoice_not_on_agreement_is_refused(admin_client, agreement_matched_invoice,
                                                   unmatched_invoice):
    _, agr = agreement_matched_invoice
    r = await admin_client.post(PA_URL, json={
        "title": "Wrong invoice", "agreement_id": str(agr.id),
        "invoice_ids": [unmatched_invoice["id"]],
        "subtotal": "1.00", "tax_amount": "0.00", "payment_amount": "1.00",
        "line_items": [{"description": "x", "qty": "1", "unit": "EA",
                        "unit_price": "1.00", "line_total": "1.00"}],
    })
    assert r.status_code == 422
```

Add the two fixtures at the top of the same file, reusing the helpers from `test_agreement_invoice_match.py` (import them rather than copying):

```python
from tests.test_agreement_invoice_match import _make_active_agreement, _upload_invoice


@pytest.fixture
async def agreement_matched_invoice(admin_client, test_engine, seeded_vendor, system_user_id):
    agr = await _make_active_agreement(test_engine, seeded_vendor["id"], system_user_id)
    inv = await _upload_invoice(admin_client, seeded_vendor["id"], amount="1000.00")
    await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={
        "agreement_id": str(agr.id), "legacy_settlement_reason": "backlog"})
    return inv, agr


@pytest.fixture
async def unmatched_invoice(admin_client, seeded_vendor):
    return await _upload_invoice(admin_client, seeded_vendor["id"], amount="50.00")
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — `PaCreate` rejects the unknown `agreement_id` field / requires `po_id`.

- [ ] **Step 3: Make `po_id` optional and add `agreement_id` to `PaCreate`**

In `epms-api/app/schemas/pa.py`:

```python
    po_id: uuid.UUID | None = None
    # Agreement-sourced PA (no PO, no GR). Exactly one of po_id / agreement_id
    # must be set — a PA with neither is OA's Direct PA, which EPMS does not own.
    agreement_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self):
        if (self.po_id is None) == (self.agreement_id is None):
            raise ValueError("Provide exactly one of po_id or agreement_id")
        return self
```

Import `model_validator` from pydantic if it is not already imported in that file.

- [ ] **Step 4: Branch the create endpoint**

In `epms-api/app/api/v1/pa.py::create_pa`, wrap the PO-specific work. The existing body loads the PO, validates invoices against it, and runs the receipt gate — all three are PO-route concerns:

```python
    if body.agreement_id is not None:
        agr = await agr_crud.get_by_id(db, body.agreement_id)
        if agr is None:
            raise HTTPException(status_code=404, detail="Agreement not found")
        # Every listed invoice must actually be matched to THIS agreement —
        # otherwise a PA could pay an unrelated invoice off an approved ceiling.
        if body.invoice_ids:
            rows = (await db.execute(
                select(Invoice.id, Invoice.agreement_id)
                .where(Invoice.id.in_(body.invoice_ids))
            )).all()
            if len(rows) != len(set(body.invoice_ids)):
                raise HTTPException(status_code=422, detail="One or more invoices not found")
            stray = [str(r.id) for r in rows if r.agreement_id != body.agreement_id]
            if stray:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invoice(s) not matched to agreement {agr.number}: {', '.join(stray)}")
        # 收货闸门不适用:协议路线定义上就没有 GR(1A 无 pickup slip,1B 才有)。
        created = await pa_crud.create(
            db, body,
            po_number=None,
            agreement_number=agr.number,
            vendor_id=agr.vendor_id,
            vendor_name=agr.vendor_name,
            created_by=uuid.UUID(user["sub"]),
        )
        return created

    # ── existing PO route below, unchanged ────────────────────────────────────
```

Place this block **before** the existing PO lookup so no PO-route code runs for an agreement PA. Extend `pa_crud.create`'s signature with `agreement_number: str | None = None` and have it persist `agreement_id=payload.agreement_id, agreement_number=agreement_number`. Import `agreement as agr_crud`.

Note: the existing PO path calls `_complete_create_pa_tasks(db, payload.po_id)` after creation. Do **not** call it on the agreement path — there is no PO whose `create_pa` task could be closed, and passing `None` would match tasks with a NULL `document_id`.

- [ ] **Step 5: Widen the EPMS PA list filter**

`epms-api/app/crud/pa.py:83` currently reads:

```python
    q = select(PaymentApplication).where(PaymentApplication.po_id.is_not(None))
```

Replace with:

```python
    # EPMS owns PO-based AND agreement-based PAs; OA's Direct PAs have BOTH
    # columns NULL, which is what keeps them out of this list.
    q = select(PaymentApplication).where(or_(
        PaymentApplication.po_id.is_not(None),
        PaymentApplication.agreement_id.is_not(None),
    ))
```

Add `or_` to the `sqlalchemy` import in that file. Check the `po_ids_subq` scope block immediately below (lines 84-91) — it further constrains on `po_id`, which would filter every agreement PA back out. Agreement PAs must bypass the PO scope subquery:

```python
    if po_ids_subq is not None:
        q = q.where(or_(
            PaymentApplication.po_id.in_(po_ids_subq),
            PaymentApplication.agreement_id.is_not(None),
        ))
```

Read the surrounding lines before editing; the block has two branches and both need the same treatment.

- [ ] **Step 6: Run the tests to verify they pass**

Expected: all five PA tests PASS.

- [ ] **Step 7: Run the full suite for regressions**

Expected: failure count ≤ 69. Pay particular attention to existing PA tests — Step 3 changed `PaCreate`'s required fields, and any test posting a PA without `po_id` now behaves differently.

- [ ] **Step 8: Commit**

```bash
git add epms-api/app/schemas/pa.py epms-api/app/crud/pa.py \
        epms-api/app/api/v1/pa.py epms-api/tests/test_agreement_pa.py
git commit -m "feat(epms): create and list payment applications from an agreement"
```

---

### Task 8: Agreement pages in the EPMS frontend

**Files:**
- Create: `epms/src/pages/agreements/AgreementListPage.tsx`, `AgreementCreatePage.tsx`, `AgreementDetailPage.tsx`
- Modify: `epms/src/App.tsx`, `portal/src/components/layout/navConfig.tsx`

**Interfaces:**
- Consumes: `GET/POST /api/v1/agreements`, `GET/PATCH /api/v1/agreements/{id}`, `POST /api/v1/agreements/{id}/action`.
- Produces: routes `/agreements`, `/agreements/new`, `/agreements/:id`.

- [ ] **Step 1: Read the pages you are copying**

Read `epms/src/pages/po/PoListPage.tsx`, `PoCreatePage.tsx`, and `PoDetailPage.tsx` in full before writing anything. Match their structure, their use of the shared `Button` from the shell package (never a bare `<button>`), their table markup, and their status-badge usage (use the app-level `StatusBadge`; do not hand-roll colour classes).

- [ ] **Step 2: Build the list page**

`AgreementListPage.tsx` — columns: Number, Title, Vendor, Type, Valid From, Valid To, Consumed / NTE, Status. Filters: status, vendor, type, search.

The consumed/NTE cell shows a progress bar. **`consumed_amount` and `not_to_exceed` arrive as JSON strings** — every comparison and width calculation must wrap in `Number()`:

```tsx
const consumed = Number(agreement.consumed_amount)
const ceiling  = agreement.not_to_exceed ? Number(agreement.not_to_exceed) : null
const pct      = ceiling && ceiling > 0 ? Math.min((consumed / ceiling) * 100, 100) : null
const overCeiling = ceiling !== null && consumed > ceiling
```

`overCeiling` styles the bar as a warning. It must **not** disable any action — NTE warns, it never blocks.

All copy is English: "Agreements", "New Agreement", "Not to Exceed", "Consumed", "Valid Until", "Over ceiling".

- [ ] **Step 3: Build the create page**

`AgreementCreatePage.tsx` — fields: Title, Type (select: House Account / Recurring / Milestone), Vendor (searchable select), Contract No., Contact Email, Vendor Reference, Valid From, Valid To, Grace Days (default 30), Not to Exceed, Currency, Tax Code, Department, Owner, Notes.

Put helper text under Vendor Reference: *"The account or reference number the vendor prints on their invoices. Enter the existing open PO number here so the vendor does not need to change anything."*

Any dropdown must `createPortal` into `document.body` with `position: fixed`, or it will be clipped by the form's `overflow` container.

- [ ] **Step 4: Build the detail page**

`AgreementDetailPage.tsx` — header (number, status badge, vendor), the terms block, the consumed/NTE bar, an approval-step timeline copied from `PoDetailPage`, and Submit / Approve / Return / Cancel buttons wired to `POST /agreements/{id}/action`.

Also list the invoices matched to this agreement (`GET /api/v1/invoices?agreement_id=...` — add that filter to the invoice list endpoint if it does not exist, and note it as a small backend addition in this task's commit). Show a **"Settled without receipt"** badge on any row with `legacy_settlement === true`, and a count of such rows in the header. That count is the health metric for the escape hatch.

- [ ] **Step 5: Register the routes and the nav entry**

Add the three routes to `epms/src/App.tsx` following the existing PO route block.

In `portal/src/components/layout/navConfig.tsx`, add the EPMS nav item gated with `anyPermission: ['epms.agreement.read']`. Do not build a page-local nav section — `navConfig` is the single source.

- [ ] **Step 6: Verify the TypeScript gate**

```bash
cd epms && npx tsc -p tsconfig.app.json 2>&1 | tail -3
```

Expected: **59 errors or fewer**. A higher count is a regression from this task.

- [ ] **Step 7: Commit**

```bash
git add epms/src/pages/agreements epms/src/App.tsx portal/src/components/layout/navConfig.tsx
git commit -m "feat(epms-ui): agreement list, create, and detail pages"
```

---

### Task 9: Route switch in the invoice match panel

**Files:**
- Modify: `epms/src/pages/invoices/MatchPanel.tsx`, `epms/src/pages/invoices/InvoiceListPage.tsx`, `epms/src/pages/invoices/InvoiceDetailPage.tsx`

**Interfaces:**
- Consumes: `GET /invoices/{id}/agreement-candidates` (Task 5); `POST /invoices/{id}/match` with `{agreement_id, legacy_settlement_reason}` (Task 6).

- [ ] **Step 1: Read the existing panel**

Read `MatchPanel.tsx` and `InvoiceAllocationPanel.tsx` in full. The submit payload currently flows `InvoiceAllocationPanel.onSubmit({allocations, nonPoLines})` → `MatchPanel` → `InvoiceListPage`. The agreement route adds a third shape; thread it through the **same** two call sites (`MatchPanel` and `InvoiceListPage`) — missing one is how earlier invoice work shipped half-wired.

- [ ] **Step 2: Add the route tabs**

At the top of the panel:

```tsx
<div className="flex gap-2 border-b">
  <button type="button" onClick={() => setRoute('po')}
          className={route === 'po' ? ACTIVE_TAB : IDLE_TAB}>
    Purchase Orders ({poCandidates.length})
  </button>
  <button type="button" onClick={() => setRoute('agreement')}
          className={route === 'agreement' ? ACTIVE_TAB : IDLE_TAB}>
    Agreements ({agreementCandidates.length})
  </button>
</div>
```

Use the shell `Button` component if it supports a tab-like variant; otherwise match the styling already used for tabs elsewhere in EPMS rather than inventing new classes.

Default the selected tab to `'agreement'` **only** when there are zero PO candidates and at least one agreement candidate. Otherwise default to `'po'` — 1A does no auto-resolution, and silently preferring agreements would mis-route ordinary PO invoices.

- [ ] **Step 3: Build the agreement tab body**

A radio list of candidates, each row showing number, title, validity window, and `Consumed X / NTE Y` (remember `Number()`). Below it, a **required** textarea:

> **Reason for settling without receipt evidence** *(required)*
> This invoice will be paid against the agreement with no pickup slip to reconcile against. Explain why — this is recorded for audit.

Submit is disabled until an agreement is selected **and** the reason is non-empty after trimming.

- [ ] **Step 4: Wire the submit payload**

```ts
const payload = route === 'agreement'
  ? { agreement_id: selectedAgreementId, legacy_settlement_reason: reason.trim() }
  : { allocations, non_po_lines: nonPoLines, reference_po_id: referencePoId }
```

Update the `MatchInvoiceBody` type to make `agreement_id` and `legacy_settlement_reason` optional members, and pass them through both call sites.

- [ ] **Step 5: Show the route on the detail page**

On `InvoiceDetailPage.tsx`, when `match_route === 'agreement'`, show the agreement number (linking to `/agreements/:id`) where the PO number normally sits, plus a **"Settled without receipt"** badge and the recorded reason when `legacy_settlement` is true.

- [ ] **Step 6: Verify the TypeScript gate**

```bash
cd epms && npx tsc -p tsconfig.app.json 2>&1 | tail -3
```

Expected: 59 errors or fewer.

- [ ] **Step 7: Commit**

```bash
git add epms/src/pages/invoices
git commit -m "feat(epms-ui): PO / Agreement route switch in the invoice match panel"
```

---

### Task 10: PA creation from an agreement, and the release checklist

**Files:**
- Modify: `epms/src/pages/pa/PaCreatePage.tsx`
- Create: `docs/release-notes/2026-08-06-purchase-agreement-phase1a.md`

- [ ] **Step 1: Add the agreement source to the PA create page**

Read `PaCreatePage.tsx` first. It currently prefills from a PO. Add an agreement mode: when navigated with `?agreement_id=...` (from the agreement detail page or an agreement-matched invoice), load the agreement instead of a PO, list its matched-but-unpaid invoices for selection, and post `agreement_id` instead of `po_id`.

Hide every PO-specific control in this mode — line selection from PO lines, the goods-receipt picker, and the receipt-override reason field. None of them apply, and leaving the override field visible invites someone to fill it in on a route where it is meaningless.

- [ ] **Step 2: Verify the TypeScript gate**

```bash
cd epms && npx tsc -p tsconfig.app.json 2>&1 | tail -3
```

Expected: 59 errors or fewer.

- [ ] **Step 3: Write the release checklist**

`docs/release-notes/2026-08-06-purchase-agreement-phase1a.md`:

```markdown
# Purchase Agreement Phase 1A — release checklist

## Migrations (both required)
- epms-api: `ag01_purchase_agreements`, `ag02_agreement_links` (chained onto `nc02_nc_cutover`)
- identity-api: `0006_agreement_perms`

Run via `migrate-prod.sh`. Re-verify the epms head before deploying — another
branch may have landed a migration since 2026-08-06.

## Post-deploy verification
1. Portal → Access Control shows **View Agreements** and **Create / Edit Agreements**
   under EPMS. Confirm the seeded grants landed (procurement_officer and
   procurement_manager should hold both).
2. approval-api boot seeded `workflow_defs["agr"]` — check
   `SELECT workflow_defs->'agr' FROM company_config;` returns three steps.
   Portal Admin → Approval Workflows should list the Agreement chain; if it does
   not, its key list is hardcoded and needs a line adding.
3. Create the Princess Auto agreement:
   - `agreement_type` = `house_account`
   - `vendor_reference` = **the existing open PO number**, so the vendor keeps
     printing what it already prints
   - `not_to_exceed` = last year's actual spend
4. Take it through the `agr` approval chain to `active`.
5. Match one backlog invoice to it, with a reason. Confirm the PA is created,
   appears in the EPMS PA list, and needs no goods receipt.
6. Only after the backlog is clear: close the old open PO
   (`status = 'closed'`), which removes it from the invoice match candidate pool.

## Known limitations shipped deliberately
- **No pickup slips.** Every agreement match is flagged `legacy_settlement` with
  a reason. This is the 1A escape hatch for the backlog.
- **No auto-routing.** The operator picks the Agreements tab manually. Reference
  number resolution ships in 1B.
- **NTE warns only.** Passing the ceiling is recorded and shown, never blocked.

## ⚠️ Follow-up that must not be dropped
Once 1B ships slip reconciliation, narrow `legacy_settlement`: restrict it to a
named role and surface its count per agreement. Left open, it becomes the normal
way to skip reconciliation entirely.
```

- [ ] **Step 4: Commit**

```bash
git add epms/src/pages/pa/PaCreatePage.tsx docs/release-notes/2026-08-06-purchase-agreement-phase1a.md
git commit -m "feat(epms-ui): create PA from an agreement; add 1A release checklist"
```

---

## Self-Review Notes

Checked against the spec, 1A scope only:

- **§5.1 Agreement model** → Task 1. All fields present except `agreement_price_lines`, which §13-8 defers.
- **§6 `agr` approval workflow** → Task 3, including the "no migration" property and the `active` (not `approved`) terminal status.
- **§7 control #2 (NTE warns, never blocks)** → asserted directly by `test_agreement_match_does_not_block_when_over_nte` in Task 6.
- **§7 control #3 (validity + grace)** → Task 5, with tests on both sides of the grace boundary.
- **§8.1 candidate pool** → Task 5 (separate endpoint; rationale recorded in the task).
- **§8.3 manual route switch** → Task 9. Auto-resolution (§8.2) is explicitly 1B.
- **§8.5 PA route** → Task 7, covering the list filter and the receipt gate.
- **§10 1A `legacy_settlement`** → Tasks 4, 6, 9, with the narrowing follow-up recorded in the release checklist.

Open items carried from the spec that this plan does **not** resolve, by design: §13-1 (CRA Notice 199, needs the accountant), §13-2 (Portal Admin workflow key enumeration — Task 10 Step 3 turns it into a post-deploy check), §13-6 (which role may use `legacy_settlement` — currently any user who can match invoices; narrow it in 1B).
