# Payments Hub and Remittance Advice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Finance a single Payments hub listing every payment the system has made, and email each payee a remittance advice after a payment executes — from a batch run or from a single PA payment.

**Architecture:** Remittance is anchored on `payment_records`, the one artifact both payment entry points produce. A scope (`batch` = records sharing a `batch_id`, `payment` = one record) resolves to a set of records, which group into per-payee emails. Grouping, templates, block rules, sending, logging, and resend are one code path serving both scopes. The Payments hub is a read surface over the same table, and is also the catch-all place to send remittance for payments whose originating screen has no dialog.

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic (finance-api, epms-api, mdm-api), aiosmtplib, React + TypeScript + Vite (finance, epms), pytest + pytest-asyncio + httpx ASGITransport.

**Spec:** `docs/superpowers/specs/2026-07-22-batch-payment-remittance-advice-design.md`

**Worktree:** `C:/Project/uniops-remittance` — branch `feature/batch-payment-remittance`. All paths below are relative to that worktree root.

## Global Constraints

- **Never run alembic or any script from the host shell.** The host `.env` points at the production database `10.10.50.20`. Migrations run inside a container; tests run against the local docker `uniops_postgres`.
- **Test database is shared — never run two finance-api pytest sessions at once.** Two concurrent sessions drop each other's schema and produce a screen of `UndefinedTable` errors that look like regressions but are not.
- **Alembic `down_revision` values, verified against the real chains — do not guess from filenames:**
  - finance-api head: `0026_jv_lines_nc_cc_code`
  - epms-api head: `aa_default_match_tolerance_5`
  - mdm-api head: `0005_units_of_measure`
- **epms-api already has two heads on `main`:** `aa_default_match_tolerance_5` (live chain) and the dangling `r8m9n0o1p2q3`. Attach to `aa_default_match_tolerance_5`. Do **not** merge or otherwise touch the sibling head in this branch — a downgrade would roll it back too.
- **All user-facing frontend strings are English.** Comments may be Chinese. Use standard accounting terminology.
- **Status badges use the shared `StatusBadge`** from `@uniops/shell` (`packages/shell/src/ui/badge.tsx`). Never write page-local `bg-green-50`-style badges.
- **Pydantic serializes `Decimal` as a JSON string.** Every frontend arithmetic on an amount goes through `Number()` first.
- **Any new Vite build argument must be declared as both `ARG` and `ENV` in the Dockerfile.** A missing build arg is silently dropped and shows up as a page that hangs on loading with no error. This plan introduces none — verify before adding one.
- **Every Finance page is wrapped in `PortalChromeLayout`** with an `activeKey` matching its `navConfig` entry. Sidebar entries live in the shared nav config, gated by permission keys — never a page-local `NAV_SECTIONS` copy or a role check.
- **New mirror models must match the physical table column-for-column.** Check `information_schema` before writing the model. `company_config` has no timestamp columns, so no `TimestampMixin`.
- **Commit after every task.** Do not accumulate WIP.

## File Structure

**finance-api (owns the new table, the send logic, and the hub API)**

| File | Responsibility |
| --- | --- |
| `alembic/versions/0027_payment_remittance_notifications.py` | New table + GIN index |
| `app/models/remittance.py` | `RemittanceNotification` model |
| `app/models/mirrors.py` | Add `BusinessPartner` mirror; add `email` to `User`; add `remittance_config` to `CompanyConfig` |
| `app/services/email.py` | SMTP send primitive, ported from epms-api |
| `app/services/remittance_config.py` | Resolve SMTP + sender + CC from `company_config` |
| `app/crud/remittance.py` | Scope → records → payee groups → block reasons |
| `app/services/remittance_template.py` | Vendor and employee HTML rendering |
| `app/crud/remittance_send.py` | Send one group, upsert the notification row |
| `app/api/v1/remittance.py` | Preview / send endpoints for both scopes |
| `app/crud/payment.py` | Hub filters, summary aggregate, export rows |
| `app/schemas/payment.py` | Widen `PaymentResponse`, add hub response models |
| `app/api/v1/payments.py` | Hub query params, `/summary`, `/export`, mount remittance router |
| `tests/test_remittance.py` | Grouping, block reasons, send, endpoints |
| `tests/test_payments_hub.py` | List/filter/summary/export, claim-payment regression |

**mdm-api (owns `business_partners`)**

| File | Responsibility |
| --- | --- |
| `alembic/versions/0006_partner_remittance_email.py` | Add `remittance_email` |
| `app/models/business_partner.py` | New column |
| `app/schemas/business_partner.py` | Expose on create/update/out |

**epms-api (owns `company_config` and the `vendors` view)**

| File | Responsibility |
| --- | --- |
| `alembic/versions/ab_remittance_config_and_vendor_view.py` | Add `remittance_config`; recreate the `vendors` view with the new column |
| `app/models/config.py` | New column |
| `app/schemas/config.py` | `remittance_config` on update/response |
| `app/schemas/vendor.py` | `remittance_email` on create/update/response/CSV |
| `app/api/v1/vendors.py` | Forward `remittance_email` to mdm |

**finance frontend**

| File | Responsibility |
| --- | --- |
| `src/services/remittance.ts` | Preview / send API client |
| `src/components/remittance/RemittancePanel.tsx` | Shared payee list: badges, selection, Refresh, Send/Resend |
| `src/components/remittance/RemittanceDialog.tsx` | Modal wrapper around the panel |
| `src/pages/finance/PaymentsPage.tsx` | The hub |
| `src/pages/finance/PaymentBatchPage.tsx` | Open the dialog after execute; Remittance section |
| `src/app/routes.tsx`, nav config | Route + sidebar entry |

**epms frontend** — maintenance surfaces only; no remittance UI (see Task 14)

| File | Responsibility |
| --- | --- |
| `src/pages/vendors/VendorsPage.tsx` | Remittance email field |
| Company Settings page | Remittance section |

---

### Task 1: Remittance notification table

**Files:**
- Create: `finance-api/alembic/versions/0027_payment_remittance_notifications.py`
- Create: `finance-api/app/models/remittance.py`
- Test: `finance-api/tests/test_remittance.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.models.remittance.RemittanceNotification` with columns `id, scope_kind, scope_id, recipient_kind, party_id, party_name, email, payment_record_ids, amount, currency, status, error, attempts, sent_at, created_by, created_at, updated_at`; constants `SCOPE_BATCH = "batch"`, `SCOPE_PAYMENT = "payment"`, `KIND_VENDOR = "vendor"`, `KIND_EMPLOYEE = "employee"`, `SENT = "sent"`, `FAILED = "failed"`.

- [ ] **Step 1: Write the failing test**

```python
# finance-api/tests/test_remittance.py
"""Remittance advice — notification log, grouping, sending."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.remittance import (
    KIND_VENDOR, SCOPE_BATCH, SENT, RemittanceNotification,
)


def _token(role="finance_manager"):
    return jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="finance_manager"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_notification_row_round_trips(db_session):
    rec_id = uuid.uuid4()
    row = RemittanceNotification(
        scope_kind=SCOPE_BATCH, scope_id=uuid.uuid4(),
        recipient_kind=KIND_VENDOR, party_id=uuid.uuid4(), party_name="ACME",
        email="ap@acme.test", payment_record_ids=[str(rec_id)],
        amount=Decimal("100.00"), currency="CAD", status=SENT,
        attempts=1, sent_at=datetime.now(timezone.utc), created_by=uuid.uuid4(),
    )
    db_session.add(row)
    await db_session.flush()

    got = (await db_session.execute(
        select(RemittanceNotification).where(RemittanceNotification.id == row.id)
    )).scalar_one()
    assert got.payment_record_ids == [str(rec_id)]
    assert got.amount == Decimal("100.00")
    assert got.attempts == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_remittance.py::test_notification_row_round_trips -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.remittance'`

- [ ] **Step 3: Write the model**

```python
# finance-api/app/models/remittance.py
"""Remittance advice send log — finance-api owns this table.

Anchored on payment_records, not on batch lines: a `batch` scope covers every
record sharing a batch_id, a `payment` scope covers one record. Both produce
the same per-payee groups, so one log serves both.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

SCOPE_BATCH = "batch"
SCOPE_PAYMENT = "payment"
KIND_VENDOR = "vendor"
KIND_EMPLOYEE = "employee"
SENT = "sent"
FAILED = "failed"


class RemittanceNotification(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "payment_remittance_notifications"

    scope_kind: Mapped[str] = mapped_column(String(10), nullable=False)      # batch | payment
    # No FK: points at payment_batches or payment_records depending on scope_kind.
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    recipient_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # vendor | employee
    party_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    party_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    payment_record_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)          # sent | failed
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
```

- [ ] **Step 4: Write the migration**

```python
# finance-api/alembic/versions/0027_payment_remittance_notifications.py
"""payment_remittance_notifications — remittance advice send log.

Revision ID: 0027_payment_remittance_notifications
Revises: 0026_jv_lines_nc_cc_code
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_payment_remittance_notifications"
down_revision = "0026_jv_lines_nc_cc_code"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_remittance_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("scope_kind", sa.String(10), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_kind", sa.String(10), nullable=False),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("party_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("payment_record_ids", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("scope_kind", "scope_id", "recipient_kind", "party_id",
                            name="uq_remittance_scope_party"),
    )
    op.create_index("ix_remittance_scope", "payment_remittance_notifications",
                    ["scope_kind", "scope_id"])
    # Backs the hub's "was this payment notified?" containment query.
    op.create_index("ix_remittance_record_ids", "payment_remittance_notifications",
                    ["payment_record_ids"], postgresql_using="gin")


def downgrade():
    op.drop_index("ix_remittance_record_ids", table_name="payment_remittance_notifications")
    op.drop_index("ix_remittance_scope", table_name="payment_remittance_notifications")
    op.drop_table("payment_remittance_notifications")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd finance-api && python -m pytest tests/test_remittance.py::test_notification_row_round_trips -v`
Expected: PASS. The conftest rebuilds the schema by running alembic, so the new migration is exercised on every run.

- [ ] **Step 6: Verify the whole existing suite still migrates**

Run: `cd finance-api && python -m pytest tests/test_payment_batch.py -v`
Expected: PASS, same count as before this task. A failure here means the migration broke the chain.

- [ ] **Step 7: Commit**

```bash
git add finance-api/alembic/versions/0027_payment_remittance_notifications.py finance-api/app/models/remittance.py finance-api/tests/test_remittance.py
git commit -m "feat(finance): add payment_remittance_notifications table"
```

---

### Task 2: `remittance_email` on the vendor master

Adding a column to `business_partners` is not enough. EPMS reads vendors through a compatibility **VIEW** whose column list is written out explicitly in `epms-api/alembic/versions/x5_repoint_vendor_fks.py`, so the view must be recreated, and the epms vendor schemas and the mdm forwarder must both carry the field or the value silently vanishes on save.

**Files:**
- Create: `mdm-api/alembic/versions/0006_partner_remittance_email.py`
- Modify: `mdm-api/app/models/business_partner.py`
- Modify: `mdm-api/app/schemas/business_partner.py`
- Create: `epms-api/alembic/versions/ab_remittance_config_and_vendor_view.py` (view half; the config half lands in Task 3 — same file, written once here and extended there)
- Modify: `epms-api/app/schemas/vendor.py`
- Modify: `epms-api/app/api/v1/vendors.py`
- Test: `mdm-api/tests/test_business_partner.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `business_partners.remittance_email` (nullable `varchar(255)`), exposed as `remittance_email` on mdm `PartnerCreate` / `PartnerUpdate` / `PartnerOut` and on epms `VendorCreate` / `VendorUpdate` / `VendorResponse` / `VendorCsvRow`.

- [ ] **Step 1: Write the failing test**

```python
# mdm-api/tests/test_business_partner.py — append
async def test_partner_remittance_email_round_trips(client):
    created = (await client.post("/mdm/v1/partners", json={
        "code": f"V-{uuid.uuid4().hex[:6]}", "name": "ACME", "category": "goods",
        "contact_name": "AP", "contact_email": "ap@acme.test",
        "remittance_email": "remit@acme.test", "is_supplier": True,
    }, headers=_h())).json()
    assert created["remittance_email"] == "remit@acme.test"

    patched = (await client.patch(f"/mdm/v1/partners/{created['id']}",
                                  json={"remittance_email": "ap2@acme.test"},
                                  headers=_h())).json()
    assert patched["remittance_email"] == "ap2@acme.test"
```

Match the surrounding file's existing fixture names (`client`, `_h`) — if they differ, use the local ones rather than introducing new fixtures.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mdm-api && python -m pytest tests/test_business_partner.py::test_partner_remittance_email_round_trips -v`
Expected: FAIL — the response has no `remittance_email` key.

- [ ] **Step 3: Add the column to the model**

```python
# mdm-api/app/models/business_partner.py — after contact_email
    # Where remittance advice is sent. Falls back to contact_email when empty
    # (finance-api resolves the fallback, not the DB).
    remittance_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

- [ ] **Step 4: Write the mdm migration**

```python
# mdm-api/alembic/versions/0006_partner_remittance_email.py
"""business_partners.remittance_email — where remittance advice is sent.

Revision ID: 0006_partner_remittance_email
Revises: 0005_units_of_measure
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_partner_remittance_email"
down_revision = "0005_units_of_measure"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("business_partners",
                  sa.Column("remittance_email", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("business_partners", "remittance_email")
```

- [ ] **Step 5: Expose it on the mdm schemas**

```python
# mdm-api/app/schemas/business_partner.py
# PartnerBase — after contact_email
    remittance_email: Optional[str] = None
# PartnerUpdate — after contact_email
    remittance_email: Optional[str] = None
```

`PartnerOut` inherits `PartnerBase`, so it picks the field up automatically. Confirm that by reading the class before assuming it.

- [ ] **Step 6: Run the mdm test to verify it passes**

Run: `cd mdm-api && python -m pytest tests/test_business_partner.py -v`
Expected: PASS

- [ ] **Step 7: Recreate the epms `vendors` view with the new column**

```python
# epms-api/alembic/versions/ab_remittance_config_and_vendor_view.py
"""vendors view + company_config.remittance_config.

The `vendors` compatibility view (x5_repoint_vendor_fks) lists its columns
explicitly, so a new business_partners column does NOT appear in it. EPMS
vendor reads go through that view, so it must be recreated here.

Revision ID: ab_remittance_config_and_vendor_view
Revises: aa_default_match_tolerance_5
"""
import sqlalchemy as sa
from alembic import op

revision = "ab_remittance_config_and_vendor_view"
down_revision = "aa_default_match_tolerance_5"
branch_labels = None
depends_on = None

# Verbatim from x5_repoint_vendor_fks._VIEW_COLS.
_VIEW_COLS_OLD = (
    "id, code, erp_id, name, category, contact_name, contact_email, phone, "
    "address, payment_terms, max_prepayment_pct, currency, is_active, notes, "
    "created_at, updated_at"
)
_VIEW_COLS_NEW = _VIEW_COLS_OLD + ", remittance_email"


def upgrade():
    op.execute("DROP VIEW IF EXISTS vendors")
    op.execute(
        f"CREATE VIEW vendors AS SELECT {_VIEW_COLS_NEW} "
        "FROM business_partners WHERE is_supplier"
    )


def downgrade():
    op.execute("DROP VIEW IF EXISTS vendors")
    op.execute(
        f"CREATE VIEW vendors AS SELECT {_VIEW_COLS_OLD} "
        "FROM business_partners WHERE is_supplier"
    )
```

Before running this, open `epms-api/alembic/versions/x5_repoint_vendor_fks.py` and confirm `_VIEW_COLS` still reads exactly as copied above. If that file has changed since 2026-07-22, use its current value — a stale copy silently drops columns from every vendor read.

- [ ] **Step 8: Carry the field through the epms vendor schemas**

```python
# epms-api/app/schemas/vendor.py
# VendorCreate — after contact_email
    remittance_email: str = Field(default="", max_length=255)
# VendorUpdate — after contact_email
    remittance_email: str | None = Field(default=None, max_length=255)
# VendorResponse — after contact_email
    remittance_email: str | None = None
# VendorCsvRow — after contact_email
    remittance_email: str = Field(default="", max_length=255)
```

- [ ] **Step 9: Forward it to mdm**

In `epms-api/app/api/v1/vendors.py`, `_partner_payload` builds the mdm payload — add `remittance_email` to it. The local read path selects explicit columns around line 89; add `v.remittance_email` there in the same position the schema expects. The CSV import path near line 140 reads `raw.get("contactEmail", …)`; add `remittance_email=raw.get("remittanceEmail", "").strip()`.

- [ ] **Step 10: Verify the epms vendor suite**

Run: `cd epms-api && python -m pytest tests/ -k vendor -v`
Expected: PASS. Compare the failure count against the pre-existing baseline — epms has known pre-existing failures; only new ones matter.

- [ ] **Step 11: Commit**

```bash
git add mdm-api/alembic/versions/0006_partner_remittance_email.py mdm-api/app/models/business_partner.py mdm-api/app/schemas/business_partner.py mdm-api/tests/test_business_partner.py epms-api/alembic/versions/ab_remittance_config_and_vendor_view.py epms-api/app/schemas/vendor.py epms-api/app/api/v1/vendors.py
git commit -m "feat(mdm,epms): add vendor remittance_email through model, view, and forwarder"
```

---

### Task 3: `remittance_config` on company config

**Files:**
- Modify: `epms-api/alembic/versions/ab_remittance_config_and_vendor_view.py` (extend Task 2's migration)
- Modify: `epms-api/app/models/config.py`
- Modify: `epms-api/app/schemas/config.py`
- Test: `epms-api/tests/test_config.py`

**Interfaces:**
- Consumes: the migration file from Task 2.
- Produces: `company_config.remittance_config` — `JSONB NOT NULL DEFAULT '{}'` holding `{enabled, from_email, from_name, cc_email, smtp_user, smtp_password}`; readable and patchable through `GET /config` and `PATCH /config`.

- [ ] **Step 1: Write the failing test**

```python
# epms-api/tests/test_config.py — append
async def test_remittance_config_patches_and_defaults_empty(client, admin_headers):
    got = (await client.get("/api/v1/config", headers=admin_headers)).json()
    assert got["remittance_config"] == {}

    patched = (await client.patch("/api/v1/config", headers=admin_headers, json={
        "remittance_config": {
            "enabled": True, "from_email": "ap@crm.test", "from_name": "CRM AP",
            "cc_email": "apbox@crm.test",
        },
    })).json()
    assert patched["remittance_config"]["enabled"] is True
    assert patched["remittance_config"]["from_email"] == "ap@crm.test"
```

Use the fixture names the existing `test_config.py` already defines.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_config.py::test_remittance_config_patches_and_defaults_empty -v`
Expected: FAIL with `KeyError: 'remittance_config'`

- [ ] **Step 3: Add the column to the model**

```python
# epms-api/app/models/config.py — beside the other JSONB config blocks
    # Remittance advice: {enabled, from_email, from_name, cc_email,
    # smtp_user, smtp_password}. Deliberately a JSONB blob defaulting to {}
    # so every consumer reads through .get() with an explicit fallback — a
    # non-null scalar default would shadow the switch the way
    # notification_channel shadowed default_channel.
    remittance_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
```

- [ ] **Step 4: Extend the Task 2 migration**

```python
# epms-api/alembic/versions/ab_remittance_config_and_vendor_view.py — in upgrade(),
# BEFORE the view is recreated
    op.add_column("company_config", sa.Column(
        "remittance_config", sa.dialects.postgresql.JSONB,
        nullable=False, server_default=sa.text("'{}'::jsonb")))

# in downgrade(), AFTER the view is restored
    op.drop_column("company_config", "remittance_config")
```

Add `from sqlalchemy.dialects import postgresql` and use `postgresql.JSONB` if the `sa.dialects` path is not already imported in that file.

- [ ] **Step 5: Expose it on the config schemas**

```python
# epms-api/app/schemas/config.py
# ConfigUpdate — beside collection_config
    remittance_config: dict[str, Any] | None = None
# ConfigResponse — beside collection_config
    remittance_config: dict[str, Any]
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add epms-api/alembic/versions/ab_remittance_config_and_vendor_view.py epms-api/app/models/config.py epms-api/app/schemas/config.py epms-api/tests/test_config.py
git commit -m "feat(epms): add company_config.remittance_config"
```

---

### Task 4: finance mirrors for partner, user email, and config

**Files:**
- Modify: `finance-api/app/models/mirrors.py`
- Modify: `finance-api/tests/conftest.py`
- Test: `finance-api/tests/test_remittance.py`

**Interfaces:**
- Consumes: Tasks 2 and 3's columns.
- Produces: `app.models.mirrors.BusinessPartner` (`id, code, name, contact_email, remittance_email, is_supplier`), `mirrors.User.email`, `mirrors.CompanyConfig.remittance_config`.

- [ ] **Step 1: Verify the physical columns before writing the model**

Run inside the database container, never from the host shell:

```bash
docker exec -i uniops_postgres psql -U epms -d epms -c "\d business_partners"
docker exec -i uniops_postgres psql -U epms -d epms -c "\d company_config"
docker exec -i uniops_postgres psql -U epms -d epms -c "\d users"
```

Confirm the exact type and nullability of every column the mirror will declare. Three previous mirrors were written from convention instead of from the table and were wrong.

- [ ] **Step 2: Write the failing test**

```python
# finance-api/tests/test_remittance.py — append
from app.models.mirrors import BusinessPartner


async def test_partner_mirror_reads_remittance_email(db_session):
    bp = BusinessPartner(
        code=f"V-{uuid.uuid4().hex[:6]}", name="ACME", contact_email="ap@acme.test",
        remittance_email="remit@acme.test", is_supplier=True,
    )
    db_session.add(bp)
    await db_session.flush()

    got = (await db_session.execute(
        select(BusinessPartner).where(BusinessPartner.id == bp.id)
    )).scalar_one()
    assert got.remittance_email == "remit@acme.test"
    assert got.contact_email == "ap@acme.test"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_remittance.py::test_partner_mirror_reads_remittance_email -v`
Expected: FAIL with `ImportError: cannot import name 'BusinessPartner'`

- [ ] **Step 4: Add the mirrors**

```python
# finance-api/app/models/mirrors.py — new class, matching the physical table
class BusinessPartner(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror subset (mdm-api owns schema) — remittance recipients.
    Columns verified against information_schema 2026-07-22."""
    __tablename__ = "business_partners"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    remittance_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_supplier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

# mirrors.User — add
    email: Mapped[str] = mapped_column(String(255), nullable=False)

# mirrors.CompanyConfig — add
    remittance_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
```

`CompanyConfig` still has no `TimestampMixin` — do not add one.

- [ ] **Step 5: Register the mirror in the test schema builder**

In `finance-api/tests/conftest.py`, add `BusinessPartner` to the `from app.models.mirrors import (...)` list and to the `Base.metadata.create_all(eng, tables=[...])` list. Tests build mirror tables from these models, so a mirror missing here fails with `UndefinedTable`.

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add finance-api/app/models/mirrors.py finance-api/tests/conftest.py finance-api/tests/test_remittance.py
git commit -m "feat(finance): mirror business_partners, user email, remittance_config"
```

---

### Task 5: SMTP send primitive and sender resolution

**Files:**
- Create: `finance-api/app/services/email.py`
- Create: `finance-api/app/services/remittance_config.py`
- Test: `finance-api/tests/test_remittance.py`

**Interfaces:**
- Consumes: `mirrors.CompanyConfig`.
- Produces:
  - `services.email.send_email(to, subject, html, *, cc=None, smtp_host, smtp_port, smtp_user, smtp_password, smtp_use_tls, smtp_from) -> None`
  - `services.remittance_config.RemittanceSettings` dataclass with `enabled: bool`, `from_email: str`, `from_name: str`, `cc_email: str | None`, `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `smtp_use_tls`
  - `services.remittance_config.load(db) -> RemittanceSettings | None` (None when unconfigured)

- [ ] **Step 1: Write the failing tests**

```python
# finance-api/tests/test_remittance.py — append
from unittest.mock import AsyncMock, patch

from app.models.mirrors import CompanyConfig
from app.services import remittance_config as rc


async def test_send_email_uses_starttls_on_587():
    from app.services.email import send_email
    with patch("app.services.email.aiosmtplib.send", new=AsyncMock()) as m:
        await send_email("a@b.test", "s", "<p>x</p>", smtp_host="h", smtp_port=587,
                         smtp_user="u", smtp_password="p", smtp_use_tls=True,
                         smtp_from="from@b.test")
    assert m.await_args.kwargs["start_tls"] is True
    assert m.await_args.kwargs["use_tls"] is False


async def test_send_email_uses_implicit_tls_on_465():
    from app.services.email import send_email
    with patch("app.services.email.aiosmtplib.send", new=AsyncMock()) as m:
        await send_email("a@b.test", "s", "<p>x</p>", smtp_host="h", smtp_port=465,
                         smtp_user="u", smtp_password="p", smtp_use_tls=True,
                         smtp_from="from@b.test")
    assert m.await_args.kwargs["use_tls"] is True
    assert m.await_args.kwargs["start_tls"] is False


async def test_settings_none_when_switch_absent(db_session):
    db_session.add(CompanyConfig(role_management={}, remittance_config={}))
    await db_session.flush()
    assert await rc.load(db_session) is None


async def test_settings_prefer_po_smtp_and_own_from(db_session):
    cfg = CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test", "from_name": "CRM AP",
        "cc_email": "apbox@crm.test",
    })
    db_session.add(cfg)
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_port=587,"
        " po_smtp_user='po_user', po_smtp_password='pw', po_smtp_use_tls=true,"
        " smtp_host='int.host', smtp_port=25 WHERE id = :i"), {"i": str(cfg.id)})

    s = await rc.load(db_session)
    assert s is not None
    assert s.smtp_host == "po.host"          # outbound sender wins
    assert s.from_email == "ap@crm.test"     # never the shared smtp_from
    assert s.cc_email == "apbox@crm.test"
    assert s.smtp_user == "po_user"          # no override configured
```

The last test writes SMTP columns with raw SQL because the finance mirror deliberately does not map them all; add `import sqlalchemy as sa` at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -k "send_email or settings" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.email'`

- [ ] **Step 3: Port the send primitive**

```python
# finance-api/app/services/email.py
"""Async email sending via aiosmtplib.

Ported from epms-api/app/services/email.py (2026-07-22) — the send primitive
only, no MFA or PO template code. Keep the TLS mode decision below in sync
with that file; getting it wrong yields [SSL: WRONG_VERSION_NUMBER] at
runtime and nothing at test time.
"""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, html: str, *, cc: str | None = None,
                     smtp_host: str, smtp_port: int, smtp_user: str | None,
                     smtp_password: str | None, smtp_use_tls: bool,
                     smtp_from: str) -> None:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg.attach(MIMEText(html, "html"))

    # use_tls   → handshake on connect (implicit TLS / SMTPS, port 465)
    # start_tls → connect plaintext, upgrade via STARTTLS (submission, 587)
    implicit_tls = bool(smtp_use_tls) and smtp_port == 465
    start_tls = bool(smtp_use_tls) and smtp_port != 465

    try:
        await aiosmtplib.send(
            msg, hostname=smtp_host, port=smtp_port,
            username=smtp_user or None, password=smtp_password or None,
            use_tls=implicit_tls, start_tls=start_tls,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send remittance email to %s: %s", to, exc)
        raise
```

- [ ] **Step 4: Write the settings resolver**

```python
# finance-api/app/services/remittance_config.py
"""Resolve remittance sender settings from company_config.

Server parameters are shared with the rest of the system (outbound po_smtp_*
preferred, internal smtp_* as fallback). The From address is always the
remittance-specific one — never the shared smtp_from — with optional
credential overrides for servers that reject a mismatched From.
"""
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RemittanceSettings:
    enabled: bool
    from_email: str
    from_name: str
    cc_email: str | None
    smtp_host: str
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_use_tls: bool


async def load(db: AsyncSession) -> RemittanceSettings | None:
    """None when remittance is switched off or not configured well enough to
    send (no from address, or no SMTP host anywhere)."""
    row = (await db.execute(sa.text(
        "SELECT remittance_config, po_smtp_host, po_smtp_port, po_smtp_user,"
        " po_smtp_password, po_smtp_use_tls, smtp_host, smtp_port, smtp_user,"
        " smtp_password, smtp_use_tls FROM company_config LIMIT 1"
    ))).mappings().first()
    if row is None:
        return None

    cfg = row["remittance_config"] or {}
    if not cfg.get("enabled"):
        return None
    from_email = (cfg.get("from_email") or "").strip()
    if not from_email:
        return None

    use_po = bool(row["po_smtp_host"])
    host = (row["po_smtp_host"] if use_po else row["smtp_host"]) or ""
    if not host:
        return None
    port = (row["po_smtp_port"] if use_po else row["smtp_port"]) or 587
    user = row["po_smtp_user"] if use_po else row["smtp_user"]
    password = row["po_smtp_password"] if use_po else row["smtp_password"]
    use_tls = row["po_smtp_use_tls"] if use_po else row["smtp_use_tls"]

    return RemittanceSettings(
        enabled=True,
        from_email=from_email,
        from_name=(cfg.get("from_name") or "").strip(),
        cc_email=(cfg.get("cc_email") or "").strip() or None,
        smtp_host=host, smtp_port=int(port),
        smtp_user=cfg.get("smtp_user") or user,
        smtp_password=cfg.get("smtp_password") or password,
        smtp_use_tls=bool(use_tls),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -k "send_email or settings" -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add finance-api/app/services/email.py finance-api/app/services/remittance_config.py finance-api/tests/test_remittance.py
git commit -m "feat(finance): SMTP send primitive and remittance sender resolution"
```

---

### Task 6: Payee grouping and block reasons

**Files:**
- Create: `finance-api/app/crud/remittance.py`
- Test: `finance-api/tests/test_remittance.py`

**Interfaces:**
- Consumes: `mirrors.BusinessPartner`, `mirrors.User`, `mirrors.Invoice`, `models.pa.PaymentApplication`, `models.payment.PaymentRecord`, `models.remittance.RemittanceNotification`, and `crud.payment_batch._vendor_inv_no_map`.
- Produces:
  - `crud.remittance.resolve_scope(db, scope_kind, scope_id) -> list[PaymentRecord]`
  - `crud.remittance.build_groups(db, records) -> list[PayeeGroup]`
  - `PayeeGroup` dataclass: `recipient_kind, party_id, party_name, email, lines: list[GroupLine], total: Decimal, currency: str, block_reasons: list[str], payment_record_ids: list[uuid.UUID]`
  - `GroupLine` dataclass: `vendor_inv_no: str, doc_number: str, payment_date: date, amount: Decimal`
  - Constants `BLOCK_MISSING_EMAIL = "missing_email"`, `BLOCK_MISSING_INVOICE_NO = "missing_invoice_no"`

- [ ] **Step 1: Write the failing tests**

```python
# finance-api/tests/test_remittance.py — append
from app.crud import remittance as rem
from app.models.mirrors import ExpenseClaim, Invoice
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord


def _pa(vendor_id, amount="100.00", invoice_ids=None, po_id=None):
    return PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Widgets", pa_type="regular",
        status="approved", po_id=po_id, po_number="PO-1" if po_id else None,
        vendor_id=vendor_id, vendor_name="ACME", invoice_ids=invoice_ids or [],
        payment_amount=Decimal(amount), currency="CAD", created_by=uuid.uuid4(),
    )


def _record(pa, batch_id=None, status="completed"):
    return PaymentRecord(
        doc_kind="pa" if pa.po_id else "pa_dir", doc_id=pa.id, doc_number=pa.pa_number,
        pa_id=pa.id, pa_number=pa.pa_number, vendor_id=pa.vendor_id,
        vendor_name=pa.vendor_name, payment_date=date(2026, 7, 22),
        payment_method="bank_transfer", amount=pa.payment_amount, currency="CAD",
        recorded_by=uuid.uuid4(), status=status, batch_id=batch_id,
    )


async def _vendor(db, email="ap@acme.test", remit=None):
    bp = BusinessPartner(code=f"V-{uuid.uuid4().hex[:6]}", name="ACME",
                         contact_email=email, remittance_email=remit, is_supplier=True)
    db.add(bp)
    await db.flush()
    return bp


async def _invoice(db, number="VINV-1"):
    inv = Invoice(vendor_invoice_number=number)
    db.add(inv)
    await db.flush()
    return inv


async def test_two_pas_for_one_vendor_collapse_into_one_group(db_session):
    bp = await _vendor(db_session, remit="remit@acme.test")
    i1, i2 = await _invoice(db_session, "VINV-1"), await _invoice(db_session, "VINV-2")
    pas = [_pa(bp.id, "100.00", [str(i1.id)]), _pa(bp.id, "50.00", [str(i2.id)])]
    db_session.add_all(pas)
    await db_session.flush()
    recs = [_record(p) for p in pas]
    db_session.add_all(recs)
    await db_session.flush()

    groups = await rem.build_groups(db_session, recs)
    assert len(groups) == 1
    g = groups[0]
    assert g.email == "remit@acme.test"
    assert g.total == Decimal("150.00")
    assert sorted(l.vendor_inv_no for l in g.lines) == ["VINV-1", "VINV-2"]
    assert g.block_reasons == []


async def test_missing_email_blocks_group(db_session):
    bp = await _vendor(db_session, email="", remit=None)
    inv = await _invoice(db_session, "VINV-3")
    pa = _pa(bp.id, "10.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert rem.BLOCK_MISSING_EMAIL in g.block_reasons


async def test_direct_pa_without_invoice_no_blocks_group(db_session):
    bp = await _vendor(db_session, remit="remit@acme.test")
    pa = _pa(bp.id, "10.00", invoice_ids=[])          # Direct PA, no invoice
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert rem.BLOCK_MISSING_INVOICE_NO in g.block_reasons


async def test_employee_group_never_blocked_for_invoice_no(db_session):
    from app.models.mirrors import User
    emp_id = uuid.uuid4()
    db_session.add(User(id=emp_id, email="jane@crm.test"))
    claim = ExpenseClaim(claim_number="EXP-1", claim_type="EXP", status="approved",
                         employee_id=emp_id, employee_name="Jane Doe", currency="CAD",
                         total_amount=Decimal("20.00"), tax_amount=Decimal("0"),
                         net_amount=Decimal("20.00"))
    db_session.add(claim)
    await db_session.flush()
    rec = PaymentRecord(
        doc_kind="expense_claim", doc_id=claim.id, doc_number=claim.claim_number,
        payment_date=date(2026, 7, 22), payment_method="bank_transfer",
        amount=Decimal("20.00"), currency="CAD", recorded_by=uuid.uuid4(),
        status="completed",
    )
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert g.recipient_kind == "employee"
    assert g.email == "jane@crm.test"
    assert g.block_reasons == []


async def test_scope_batch_and_scope_payment_agree_for_one_record(db_session):
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-9")
    pa = _pa(bp.id, "75.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch_id = uuid.uuid4()
    rec = _record(pa, batch_id=batch_id)
    db_session.add(rec)
    await db_session.flush()

    by_batch = await rem.build_groups(db_session, await rem.resolve_scope(db_session, "batch", batch_id))
    by_payment = await rem.build_groups(db_session, await rem.resolve_scope(db_session, "payment", rec.id))
    assert [g.total for g in by_batch] == [g.total for g in by_payment] == [Decimal("75.00")]


async def test_failed_record_is_excluded(db_session):
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-10")
    pa = _pa(bp.id, "5.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch_id = uuid.uuid4()
    db_session.add(_record(pa, batch_id=batch_id, status="cancelled"))
    await db_session.flush()

    assert await rem.resolve_scope(db_session, "batch", batch_id) == []
```

The `Invoice` mirror may require more non-null columns than shown; read `app/models/mirrors.py` and fill in whatever the table demands rather than guessing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -k "group or scope or blocked or excluded" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.crud.remittance'`

- [ ] **Step 3: Write the grouping module**

```python
# finance-api/app/crud/remittance.py
"""Remittance payee grouping.

Anchored on payment_records so a batch run and a single payment share one
code path: a scope resolves to a set of records, records group into payees.
Block reasons are computed live on every call — never cached — so filling in
a vendor email or attaching an invoice number unblocks Send immediately.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.payment_batch import _vendor_inv_no_map
from app.models.mirrors import BusinessPartner, ExpenseClaim, User
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.models.remittance import KIND_EMPLOYEE, KIND_VENDOR, SCOPE_BATCH, SCOPE_PAYMENT

BLOCK_MISSING_EMAIL = "missing_email"
BLOCK_MISSING_INVOICE_NO = "missing_invoice_no"

_VENDOR_KINDS = ("pa", "pa_dir")
_COMPLETED = "completed"


@dataclass
class GroupLine:
    vendor_inv_no: str
    doc_number: str
    payment_date: date
    amount: Decimal


@dataclass
class PayeeGroup:
    recipient_kind: str
    party_id: uuid.UUID
    party_name: str
    email: str
    currency: str
    lines: list[GroupLine] = field(default_factory=list)
    total: Decimal = Decimal("0")
    block_reasons: list[str] = field(default_factory=list)
    payment_record_ids: list[uuid.UUID] = field(default_factory=list)


async def resolve_scope(db: AsyncSession, scope_kind: str,
                        scope_id: uuid.UUID) -> list[PaymentRecord]:
    """Completed payment records covered by a scope. `batch` = every record
    tagged with the batch; `payment` = that one record."""
    q = select(PaymentRecord).where(PaymentRecord.status == _COMPLETED)
    if scope_kind == SCOPE_BATCH:
        q = q.where(PaymentRecord.batch_id == scope_id)
    elif scope_kind == SCOPE_PAYMENT:
        q = q.where(PaymentRecord.id == scope_id)
    else:
        raise ValueError(f"Unknown scope kind '{scope_kind}'")
    return list((await db.execute(q.order_by(PaymentRecord.created_at))).scalars().all())


async def build_groups(db: AsyncSession,
                       records: list[PaymentRecord]) -> list[PayeeGroup]:
    if not records:
        return []
    vendor_recs = [r for r in records if r.doc_kind in _VENDOR_KINDS]
    claim_recs = [r for r in records if r.doc_kind == "expense_claim"]
    groups = await _vendor_groups(db, vendor_recs)
    groups += await _employee_groups(db, claim_recs)
    return groups


async def _vendor_groups(db: AsyncSession,
                         records: list[PaymentRecord]) -> list[PayeeGroup]:
    if not records:
        return []
    pa_ids = [r.doc_id for r in records if r.doc_id]
    pas = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.id.in_(pa_ids))
    )).scalars().all()
    pa_by_id = {p.id: p for p in pas}
    inv_no = await _vendor_inv_no_map(db, list(pas))

    vendor_ids = {p.vendor_id for p in pas}
    partners = (await db.execute(
        select(BusinessPartner).where(BusinessPartner.id.in_(vendor_ids))
    )).scalars().all() if vendor_ids else []
    partner_by_id = {p.id: p for p in partners}

    out: dict[uuid.UUID, PayeeGroup] = {}
    for r in records:
        pa = pa_by_id.get(r.doc_id)
        if pa is None:
            continue
        bp = partner_by_id.get(pa.vendor_id)
        email = ""
        if bp is not None:
            email = (bp.remittance_email or "").strip() or (bp.contact_email or "").strip()
        g = out.get(pa.vendor_id)
        if g is None:
            g = PayeeGroup(recipient_kind=KIND_VENDOR, party_id=pa.vendor_id,
                           party_name=pa.vendor_name, email=email, currency=r.currency)
            out[pa.vendor_id] = g
        number = inv_no.get(pa.id, "")
        g.lines.append(GroupLine(vendor_inv_no=number, doc_number=pa.pa_number,
                                 payment_date=r.payment_date, amount=r.amount))
        g.total += r.amount
        g.payment_record_ids.append(r.id)

    for g in out.values():
        if not g.email:
            g.block_reasons.append(BLOCK_MISSING_EMAIL)
        # A vendor cannot reconcile a line without its own invoice number, so
        # a missing one blocks the payee rather than rendering a placeholder.
        if any(not l.vendor_inv_no for l in g.lines):
            g.block_reasons.append(BLOCK_MISSING_INVOICE_NO)
    return list(out.values())


async def _employee_groups(db: AsyncSession,
                           records: list[PaymentRecord]) -> list[PayeeGroup]:
    if not records:
        return []
    claim_ids = [r.doc_id for r in records if r.doc_id]
    claims = (await db.execute(
        select(ExpenseClaim).where(ExpenseClaim.id.in_(claim_ids))
    )).scalars().all()
    claim_by_id = {c.id: c for c in claims}

    emp_ids = {c.employee_id for c in claims if c.employee_id}
    users = (await db.execute(
        select(User).where(User.id.in_(emp_ids))
    )).scalars().all() if emp_ids else []
    email_by_id = {u.id: (u.email or "").strip() for u in users}

    out: dict[uuid.UUID, PayeeGroup] = {}
    for r in records:
        claim = claim_by_id.get(r.doc_id)
        if claim is None or claim.employee_id is None:
            continue
        g = out.get(claim.employee_id)
        if g is None:
            g = PayeeGroup(recipient_kind=KIND_EMPLOYEE, party_id=claim.employee_id,
                           party_name=claim.employee_name,
                           email=email_by_id.get(claim.employee_id, ""),
                           currency=r.currency)
            out[claim.employee_id] = g
        # Claim number is the meaningful reference internally — no invoice number.
        g.lines.append(GroupLine(vendor_inv_no="", doc_number=claim.claim_number,
                                 payment_date=r.payment_date, amount=r.amount))
        g.total += r.amount
        g.payment_record_ids.append(r.id)

    for g in out.values():
        if not g.email:
            g.block_reasons.append(BLOCK_MISSING_EMAIL)
    return list(out.values())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -v`
Expected: PASS, all tests written so far

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/remittance.py finance-api/tests/test_remittance.py
git commit -m "feat(finance): payee grouping and live block reasons for remittance"
```

---

### Task 7: Email templates

**Files:**
- Create: `finance-api/app/services/remittance_template.py`
- Test: `finance-api/tests/test_remittance.py`

**Interfaces:**
- Consumes: `crud.remittance.PayeeGroup`.
- Produces: `services.remittance_template.render(group, *, company_name, reference, payment_method) -> tuple[str, str]` returning `(subject, html)`.

- [ ] **Step 1: Write the failing tests**

```python
# finance-api/tests/test_remittance.py — append
from app.services import remittance_template as tpl


def _group(kind="vendor", inv="VINV-1", doc="PA-0001"):
    return rem.PayeeGroup(
        recipient_kind=kind, party_id=uuid.uuid4(), party_name="ACME",
        email="ap@acme.test", currency="CAD",
        lines=[rem.GroupLine(vendor_inv_no=inv, doc_number=doc,
                             payment_date=date(2026, 7, 22), amount=Decimal("100.00"))],
        total=Decimal("100.00"),
    )


def test_vendor_template_shows_invoice_no_and_hides_pa_no():
    subject, html = tpl.render(_group(), company_name="Canada Royal Milk",
                               reference="BP-20260722-0001", payment_method="bank_transfer")
    assert "VINV-1" in html
    assert "PA-0001" not in html          # internal document number, not the vendor's concern
    assert "100.00" in html
    assert "2026-07-22" in html
    assert "Remittance Advice" in subject


def test_employee_template_shows_claim_no():
    _, html = tpl.render(_group(kind="employee", inv="", doc="EXP-0007"),
                         company_name="Canada Royal Milk",
                         reference="BP-20260722-0001", payment_method="bank_transfer")
    assert "EXP-0007" in html
    assert "Invoice" not in html


def test_template_escapes_payee_name():
    g = _group()
    g.party_name = "<script>x</script>"
    _, html = tpl.render(g, company_name="C", reference="R", payment_method="bank_transfer")
    assert "<script>" not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -k template -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.remittance_template'`

- [ ] **Step 3: Write the templates**

```python
# finance-api/app/services/remittance_template.py
"""Remittance advice HTML.

Vendor rows deliberately omit the PA number: it means nothing to the vendor
and leaks internal numbering. Employee rows show the claim number, which is
the reference an employee actually recognises.
"""
from html import escape

from app.crud.remittance import PayeeGroup
from app.models.remittance import KIND_VENDOR

_METHOD_LABEL = {
    "bank_transfer": "Bank Transfer",
    "eft": "EFT",
    "cheque": "Cheque",
    "wire": "Wire",
    "other": "Other",
}


def _money(amount, currency: str) -> str:
    return f"{amount:,.2f} {escape(currency)}"


def render(group: PayeeGroup, *, company_name: str, reference: str,
           payment_method: str) -> tuple[str, str]:
    is_vendor = group.recipient_kind == KIND_VENDOR
    ref_header = "Invoice No" if is_vendor else "Claim No"
    subject = f"Remittance Advice — {company_name} — {reference}"

    rows = "".join(
        "<tr>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>"
        f"{escape(l.vendor_inv_no if is_vendor else l.doc_number)}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee'>{l.payment_date}</td>"
        f"<td style='padding:8px;border-bottom:1px solid #eee;text-align:right'>"
        f"{_money(l.amount, group.currency)}</td>"
        "</tr>"
        for l in group.lines
    )

    intro = (
        "The following invoices have been paid." if is_vendor
        else "The following expense claims have been paid."
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:640px;margin:auto;color:#222">
      <h2 style="color:#085E5E">Remittance Advice</h2>
      <p>Dear {escape(group.party_name)},</p>
      <p>{intro}</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <thead>
          <tr style="background:#f4f4f4">
            <th style="padding:8px;text-align:left">{ref_header}</th>
            <th style="padding:8px;text-align:left">Payment Date</th>
            <th style="padding:8px;text-align:right">Amount</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
        <tfoot>
          <tr>
            <td colspan="2" style="padding:8px;font-weight:bold;text-align:right">Total</td>
            <td style="padding:8px;font-weight:bold;text-align:right">
              {_money(group.total, group.currency)}</td>
          </tr>
        </tfoot>
      </table>
      <p style="color:#666;font-size:13px">
        Reference: {escape(reference)}<br>
        Payment method: {escape(_METHOD_LABEL.get(payment_method, payment_method))}
      </p>
      <p style="color:#999;font-size:12px">
        This is an automated notification from {escape(company_name)}. Please do not reply
        to this message; contact your accounts payable representative with any questions.
      </p>
    </div>
    """
    return subject, html
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -k template -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/services/remittance_template.py finance-api/tests/test_remittance.py
git commit -m "feat(finance): remittance advice email templates"
```

---

### Task 8: Sending and the send log

**Files:**
- Create: `finance-api/app/crud/remittance_send.py`
- Test: `finance-api/tests/test_remittance.py`

**Interfaces:**
- Consumes: Tasks 5, 6, 7.
- Produces: `crud.remittance_send.send_groups(db, *, scope_kind, scope_id, groups, reference, payment_method, company_name, sender, actor_id) -> list[dict]`, each `{"recipient_kind", "party_id", "party_name", "status": "sent"|"failed"|"skipped", "error"}`.

- [ ] **Step 1: Write the failing tests**

```python
# finance-api/tests/test_remittance.py — append
from app.crud import remittance_send as rsend


def _sender():
    return rc.RemittanceSettings(
        enabled=True, from_email="ap@crm.test", from_name="CRM AP",
        cc_email="apbox@crm.test", smtp_host="h", smtp_port=587,
        smtp_user="u", smtp_password="p", smtp_use_tls=True,
    )


async def _send(db, groups, scope_id, side_effect=None):
    with patch("app.crud.remittance_send.send_email",
               new=AsyncMock(side_effect=side_effect)) as m:
        results = await rsend.send_groups(
            db, scope_kind=SCOPE_BATCH, scope_id=scope_id, groups=groups,
            reference="BP-20260722-0001", payment_method="bank_transfer",
            company_name="Canada Royal Milk", sender=_sender(),
            actor_id=uuid.uuid4(),
        )
    return results, m


async def test_send_writes_log_and_uses_cc(db_session):
    g = _group()
    scope_id = uuid.uuid4()
    results, m = await _send(db_session, [g], scope_id)
    assert [r["status"] for r in results] == ["sent"]
    assert m.await_args.args[0] == "ap@acme.test"
    assert m.await_args.kwargs["cc"] == "apbox@crm.test"

    row = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalar_one()
    assert row.status == SENT
    assert row.attempts == 1
    assert row.sent_at is not None


async def test_resend_upserts_and_increments_attempts(db_session):
    g = _group()
    scope_id = uuid.uuid4()
    await _send(db_session, [g], scope_id)
    await _send(db_session, [g], scope_id)

    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].attempts == 2


async def test_one_failure_does_not_stop_the_others(db_session):
    g1, g2 = _group(), _group()
    g1.email = "first@acme.test"
    g2.party_id = uuid.uuid4()
    g2.email = "second@acme.test"
    scope_id = uuid.uuid4()
    calls = {"n": 0}

    async def _boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("smtp down")

    with patch("app.crud.remittance_send.send_email", new=AsyncMock(side_effect=_boom)):
        results = await rsend.send_groups(
            db_session, scope_kind=SCOPE_BATCH, scope_id=scope_id, groups=[g1, g2],
            reference="R", payment_method="bank_transfer",
            company_name="C", sender=_sender(), actor_id=uuid.uuid4())

    assert sorted(r["status"] for r in results) == ["failed", "sent"]
    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    assert len(rows) == 2


async def test_blocked_group_is_skipped_not_sent(db_session):
    g = _group()
    g.block_reasons = [rem.BLOCK_MISSING_EMAIL]
    g.email = ""
    scope_id = uuid.uuid4()
    results, m = await _send(db_session, [g], scope_id)
    assert [r["status"] for r in results] == ["skipped"]
    assert m.await_count == 0
    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -k "send_writes or resend or one_failure or blocked_group" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.crud.remittance_send'`

- [ ] **Step 3: Write the send module**

```python
# finance-api/app/crud/remittance_send.py
"""Send remittance advice and record the outcome.

Never called inside the payment transaction: the caller commits the payment
first, then sends. One payee's SMTP failure is isolated and logged; the rest
still go out. Blocked payees are refused here as well as in the UI — a client
that posts one anyway gets `skipped`, never `sent`.
"""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.remittance import PayeeGroup
from app.models.remittance import FAILED, SENT, RemittanceNotification
from app.services.email import send_email
from app.services.remittance_config import RemittanceSettings
from app.services.remittance_template import render

logger = logging.getLogger(__name__)


async def _upsert(db: AsyncSession, *, scope_kind: str, scope_id: uuid.UUID,
                  group: PayeeGroup, status: str, error: str | None,
                  actor_id: uuid.UUID) -> None:
    row = (await db.execute(
        select(RemittanceNotification).where(
            RemittanceNotification.scope_kind == scope_kind,
            RemittanceNotification.scope_id == scope_id,
            RemittanceNotification.recipient_kind == group.recipient_kind,
            RemittanceNotification.party_id == group.party_id,
        )
    )).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = RemittanceNotification(
            scope_kind=scope_kind, scope_id=scope_id,
            recipient_kind=group.recipient_kind, party_id=group.party_id,
            party_name=group.party_name, email=group.email,
            payment_record_ids=[str(i) for i in group.payment_record_ids],
            amount=group.total, currency=group.currency,
            status=status, error=error, attempts=1,
            sent_at=now if status == SENT else None, created_by=actor_id,
        )
        db.add(row)
    else:
        row.party_name = group.party_name
        row.email = group.email
        row.payment_record_ids = [str(i) for i in group.payment_record_ids]
        row.amount = group.total
        row.currency = group.currency
        row.status = status
        row.error = error
        row.attempts = (row.attempts or 0) + 1
        if status == SENT:
            row.sent_at = now
    await db.flush()


async def send_groups(db: AsyncSession, *, scope_kind: str, scope_id: uuid.UUID,
                      groups: list[PayeeGroup], reference: str,
                      payment_method: str, company_name: str,
                      sender: RemittanceSettings,
                      actor_id: uuid.UUID) -> list[dict]:
    results: list[dict] = []
    for g in groups:
        base = {"recipient_kind": g.recipient_kind, "party_id": str(g.party_id),
                "party_name": g.party_name}
        if g.block_reasons or not g.email:
            results.append({**base, "status": "skipped",
                            "error": ", ".join(g.block_reasons) or "missing_email"})
            continue
        subject, html = render(g, company_name=company_name, reference=reference,
                               payment_method=payment_method)
        try:
            await send_email(
                g.email, subject, html, cc=sender.cc_email,
                smtp_host=sender.smtp_host, smtp_port=sender.smtp_port,
                smtp_user=sender.smtp_user, smtp_password=sender.smtp_password,
                smtp_use_tls=sender.smtp_use_tls,
                smtp_from=(f"{sender.from_name} <{sender.from_email}>"
                           if sender.from_name else sender.from_email),
            )
        except Exception as exc:  # noqa: BLE001 — isolate one payee's failure
            logger.error("Remittance send failed for %s: %s", g.email, exc)
            await _upsert(db, scope_kind=scope_kind, scope_id=scope_id, group=g,
                          status=FAILED, error=str(exc)[:500], actor_id=actor_id)
            results.append({**base, "status": "failed", "error": str(exc)[:500]})
            continue
        await _upsert(db, scope_kind=scope_kind, scope_id=scope_id, group=g,
                      status=SENT, error=None, actor_id=actor_id)
        results.append({**base, "status": "sent", "error": None})
    return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/remittance_send.py finance-api/tests/test_remittance.py
git commit -m "feat(finance): send remittance advice with per-payee isolation and resend"
```

---

### Task 9: Preview and send endpoints

**Files:**
- Create: `finance-api/app/api/v1/remittance.py`
- Modify: `finance-api/app/api/v1/payments.py`
- Test: `finance-api/tests/test_remittance.py`

**Interfaces:**
- Consumes: Tasks 5–8.
- Produces, all under the existing `/payments` prefix:
  - `GET /payments/batches/{batch_id}/remittance/preview`
  - `POST /payments/batches/{batch_id}/remittance/send`
  - `GET /payments/{payment_id}/remittance/preview`
  - `POST /payments/{payment_id}/remittance/send`

  Preview response: `{"enabled": bool, "reference": str, "payment_method": str, "groups": [{"recipient_kind", "party_id", "party_name", "email", "currency", "total": str, "block_reasons": [str], "lines": [{"reference", "payment_date", "amount": str}], "last_send": {"status", "error", "sent_at", "attempts"} | null}]}`.
  Send request: `{"recipients": [{"recipient_kind", "party_id"}] | null}`. Send response: `{"sent": int, "failed": int, "skipped": int, "results": [...]}`.

- [ ] **Step 1: Write the failing tests**

```python
# finance-api/tests/test_remittance.py — append
from app.models.payment_batch import EXECUTED, PaymentBatch


async def _configured(db):
    db.add(CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test", "cc_email": "apbox@crm.test"}))
    await db.flush()
    await db.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_port=587,"
        " po_smtp_use_tls=true"))


async def test_preview_requires_payment_authority(client, db_session):
    batch = PaymentBatch(batch_number="BP-1", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("0"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    r = await client.get(f"/finance/v1/payments/batches/{batch.id}/remittance/preview",
                         headers=_h("requester"))
    assert r.status_code == 403


async def test_preview_409_when_batch_not_executed(client, db_session):
    batch = PaymentBatch(batch_number="BP-2", batch_date=date(2026, 7, 22),
                         status="draft", currency="CAD", total=Decimal("0"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    r = await client.get(f"/finance/v1/payments/batches/{batch.id}/remittance/preview",
                         headers=_h())
    assert r.status_code == 409


async def test_preview_lists_group_with_block_reasons(client, db_session):
    await _configured(db_session)
    bp = await _vendor(db_session, email="", remit=None)
    inv = await _invoice(db_session, "VINV-20")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch = PaymentBatch(batch_number="BP-3", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("42.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    db_session.add(_record(pa, batch_id=batch.id))
    await db_session.flush()

    body = (await client.get(
        f"/finance/v1/payments/batches/{batch.id}/remittance/preview",
        headers=_h())).json()
    assert body["enabled"] is True
    assert body["reference"] == "BP-3"
    assert len(body["groups"]) == 1
    assert body["groups"][0]["block_reasons"] == ["missing_email"]
    assert body["groups"][0]["total"] == "42.00"      # Decimal serialized as string


async def test_send_endpoint_sends_and_reports(client, db_session):
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-21")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                              json={"recipients": None}, headers=_h())
    assert r.status_code == 200
    assert r.json()["sent"] == 1


async def test_send_endpoint_refuses_blocked_payee(client, db_session):
    await _configured(db_session)
    bp = await _vendor(db_session, email="", remit=None)
    inv = await _invoice(db_session, "VINV-22")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                              json={"recipients": [
                                  {"recipient_kind": "vendor", "party_id": str(bp.id)}]},
                              headers=_h())
    assert r.json()["skipped"] == 1
    assert r.json()["sent"] == 0
    assert m.await_count == 0
```

Check the API prefix the existing tests use (`test_payment_batch.py` shows the real one) and match it; the paths above assume `/finance/v1`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -k "preview or send_endpoint" -v`
Expected: FAIL with 404 — the routes do not exist.

- [ ] **Step 3: Write the endpoints**

```python
# finance-api/app/api/v1/remittance.py
"""Remittance preview / send — two scopes, one implementation.

Mounted onto the payments router. Route order matters: the payment-scoped
paths must be registered before payments.py's catch-all GET /{payment_id}.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud import payment_execute, remittance as rem, remittance_send as rsend
from app.crud.payment_execute import PaymentPermissionError
from app.db.base import get_db
from app.models.mirrors import CompanyConfig
from app.models.payment import PaymentRecord
from app.models.payment_batch import EXECUTED, PaymentBatch
from app.models.remittance import SCOPE_BATCH, SCOPE_PAYMENT, RemittanceNotification
from app.services import remittance_config as rc

router = APIRouter()


class RecipientRef(BaseModel):
    recipient_kind: str
    party_id: uuid.UUID


class SendRequest(BaseModel):
    recipients: list[RecipientRef] | None = None


async def _authorize(db: AsyncSession, user: dict) -> None:
    try:
        await payment_execute._check_can_pay(db, user)
    except PaymentPermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


async def _scope_context(db: AsyncSession, *, scope_kind: str,
                         scope_id: uuid.UUID) -> tuple[str, str, date]:
    """(reference, payment_method, payment_date) for the scope, or 404/409."""
    if scope_kind == SCOPE_BATCH:
        batch = (await db.execute(
            select(PaymentBatch).where(PaymentBatch.id == scope_id))).scalar_one_or_none()
        if batch is None:
            raise HTTPException(status_code=404, detail="Batch not found")
        if batch.status != EXECUTED:
            raise HTTPException(status_code=409, detail=f"Batch is {batch.status}")
        return batch.batch_number, batch.payment_method, batch.batch_date
    rec = (await db.execute(
        select(PaymentRecord).where(PaymentRecord.id == scope_id))).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="Payment record not found")
    if rec.status != "completed":
        raise HTTPException(status_code=409, detail=f"Payment is {rec.status}")
    return (rec.doc_number or rec.pa_number or str(rec.id)), rec.payment_method, rec.payment_date


async def _company_name(db: AsyncSession) -> str:
    name = (await db.execute(
        select(CompanyConfig.__table__.c.name).limit(1))).scalar_one_or_none()
    return name or "UniOps"


async def _last_sends(db: AsyncSession, scope_kind: str,
                      scope_id: uuid.UUID) -> dict[tuple[str, uuid.UUID], dict]:
    rows = (await db.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_kind == scope_kind,
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    return {(r.recipient_kind, r.party_id): {
        "status": r.status, "error": r.error, "attempts": r.attempts,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None} for r in rows}


async def _preview(db: AsyncSession, user: dict, *, scope_kind: str,
                   scope_id: uuid.UUID) -> dict:
    await _authorize(db, user)
    reference, method, _ = await _scope_context(db, scope_kind=scope_kind, scope_id=scope_id)
    records = await rem.resolve_scope(db, scope_kind, scope_id)
    groups = await rem.build_groups(db, records)
    last = await _last_sends(db, scope_kind, scope_id)
    settings_ = await rc.load(db)
    return {
        "enabled": settings_ is not None,
        "reference": reference,
        "payment_method": method,
        "groups": [{
            "recipient_kind": g.recipient_kind,
            "party_id": str(g.party_id),
            "party_name": g.party_name,
            "email": g.email,
            "currency": g.currency,
            "total": str(g.total),
            "block_reasons": g.block_reasons,
            "lines": [{
                "reference": (l.vendor_inv_no if g.recipient_kind == "vendor"
                              else l.doc_number),
                "payment_date": l.payment_date.isoformat(),
                "amount": str(l.amount),
            } for l in g.lines],
            "last_send": last.get((g.recipient_kind, g.party_id)),
        } for g in groups],
    }


async def _send(db: AsyncSession, user: dict, body: SendRequest, *,
                scope_kind: str, scope_id: uuid.UUID) -> dict:
    await _authorize(db, user)
    reference, method, _ = await _scope_context(db, scope_kind=scope_kind, scope_id=scope_id)
    sender = await rc.load(db)
    if sender is None:
        raise HTTPException(status_code=409,
                            detail="Remittance email is not configured or is switched off")
    groups = await rem.build_groups(db, await rem.resolve_scope(db, scope_kind, scope_id))
    if body.recipients is not None:
        wanted = {(r.recipient_kind, r.party_id) for r in body.recipients}
        groups = [g for g in groups if (g.recipient_kind, g.party_id) in wanted]

    results = await rsend.send_groups(
        db, scope_kind=scope_kind, scope_id=scope_id, groups=groups,
        reference=reference, payment_method=method,
        company_name=await _company_name(db), sender=sender,
        actor_id=uuid.UUID(user["sub"]),
    )
    await db.commit()
    return {
        "sent": sum(1 for r in results if r["status"] == "sent"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "results": results,
    }


@router.get("/batches/{batch_id}/remittance/preview")
async def preview_batch(batch_id: uuid.UUID, user: CurrentUser,
                        db: AsyncSession = Depends(get_db)):
    return await _preview(db, user, scope_kind=SCOPE_BATCH, scope_id=batch_id)


@router.post("/batches/{batch_id}/remittance/send")
async def send_batch(batch_id: uuid.UUID, user: CurrentUser,
                     body: SendRequest = SendRequest(),
                     db: AsyncSession = Depends(get_db)):
    return await _send(db, user, body, scope_kind=SCOPE_BATCH, scope_id=batch_id)


@router.get("/{payment_id}/remittance/preview")
async def preview_payment(payment_id: uuid.UUID, user: CurrentUser,
                          db: AsyncSession = Depends(get_db)):
    return await _preview(db, user, scope_kind=SCOPE_PAYMENT, scope_id=payment_id)


@router.post("/{payment_id}/remittance/send")
async def send_payment(payment_id: uuid.UUID, user: CurrentUser,
                       body: SendRequest = SendRequest(),
                       db: AsyncSession = Depends(get_db)):
    return await _send(db, user, body, scope_kind=SCOPE_PAYMENT, scope_id=payment_id)
```

- [ ] **Step 4: Mount the router before the catch-all**

In `finance-api/app/api/v1/payments.py`, immediately after the `router = APIRouter(prefix="/payments", tags=["payments"])` line, add:

```python
from app.api.v1.remittance import router as remittance_router

router.include_router(remittance_router)
```

Placing the include at the top of the module registers the remittance routes before `GET /{payment_id}` is declared at the bottom, so `/{payment_id}/remittance/preview` is matched first. Verify with the test in Step 5 — if a preview call returns the payment detail payload instead, the ordering is wrong.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd finance-api && python -m pytest tests/test_remittance.py -v`
Expected: PASS

- [ ] **Step 6: Verify nothing else broke**

Run: `cd finance-api && python -m pytest tests/test_payment_batch.py tests/test_payment_execute.py -v`
Expected: PASS at the same counts as before.

- [ ] **Step 7: Commit**

```bash
git add finance-api/app/api/v1/remittance.py finance-api/app/api/v1/payments.py finance-api/tests/test_remittance.py
git commit -m "feat(finance): remittance preview and send endpoints for batch and payment scopes"
```

---

### Task 10: Payments hub API

**Files:**
- Modify: `finance-api/app/schemas/payment.py`
- Modify: `finance-api/app/crud/payment.py`
- Modify: `finance-api/app/api/v1/payments.py`
- Test: `finance-api/tests/test_payments_hub.py`

**Interfaces:**
- Consumes: Task 1's table (for the remittance status column).
- Produces:
  - `PaymentResponse` with `pa_id`, `pa_number`, `vendor_id`, `vendor_name` **optional**, plus `doc_kind`, `doc_number`, `batch_id`, `bank_account_id`, `payment_date`, `entity_id`, `payee_name`, `remittance_status`.
  - `crud.payment.get_all(db, *, filters…) -> tuple[list[PaymentRecord], int]`
  - `crud.payment.summary(db, *, filters…) -> list[dict]` — `[{"currency", "count", "total"}]`
  - `GET /payments/summary`, `GET /payments/export`

- [ ] **Step 1: Write the failing tests**

```python
# finance-api/tests/test_payments_hub.py
"""Payments hub — list, filters, summary, export."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.payment import PaymentRecord


def _h(role="finance_manager"):
    token = jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _rec(*, doc_kind="pa", amount="100.00", currency="CAD", batch_id=None,
         payment_date=date(2026, 7, 22), method="bank_transfer"):
    return PaymentRecord(
        doc_kind=doc_kind, doc_id=uuid.uuid4(), doc_number="DOC-1",
        pa_id=None if doc_kind == "expense_claim" else uuid.uuid4(),
        pa_number=None if doc_kind == "expense_claim" else "PA-1",
        vendor_id=None if doc_kind == "expense_claim" else uuid.uuid4(),
        vendor_name=None if doc_kind == "expense_claim" else "ACME",
        payment_date=payment_date, payment_method=method, amount=Decimal(amount),
        currency=currency, recorded_by=uuid.uuid4(), status="completed",
        batch_id=batch_id,
    )


async def test_list_serializes_expense_claim_payment(client, db_session):
    """Regression: PaymentResponse used to require pa_id / pa_number / vendor_id /
    vendor_name, all NULL for a claim payment, so this raised."""
    db_session.add(_rec(doc_kind="expense_claim"))
    await db_session.flush()

    r = await client.get("/finance/v1/payments", headers=_h())
    assert r.status_code == 200
    assert r.json()["items"][0]["pa_id"] is None


async def test_claim_payment_shows_the_employee_as_payee(client, db_session):
    from app.models.mirrors import ExpenseClaim
    claim = ExpenseClaim(claim_number="EXP-9", claim_type="EXP", status="paid",
                         employee_id=uuid.uuid4(), employee_name="Jane Doe",
                         currency="CAD", total_amount=Decimal("20.00"),
                         tax_amount=Decimal("0"), net_amount=Decimal("20.00"))
    db_session.add(claim)
    await db_session.flush()
    rec = _rec(doc_kind="expense_claim", amount="20.00")
    rec.doc_id = claim.id
    db_session.add(rec)
    await db_session.flush()

    body = (await client.get("/finance/v1/payments", headers=_h())).json()
    assert body["items"][0]["payee_name"] == "Jane Doe"


async def test_filters_compose(client, db_session):
    batch_id = uuid.uuid4()
    db_session.add_all([
        _rec(amount="10.00", currency="CAD", batch_id=batch_id,
             payment_date=date(2026, 7, 1)),
        _rec(amount="20.00", currency="USD", payment_date=date(2026, 7, 20)),
        _rec(amount="30.00", currency="CAD", payment_date=date(2026, 7, 20)),
    ])
    await db_session.flush()

    r = (await client.get(
        "/finance/v1/payments?date_from=2026-07-10&date_to=2026-07-31"
        "&currency=CAD&source=single", headers=_h())).json()
    assert r["total"] == 1
    assert r["items"][0]["amount"] == "30.00"


async def test_summary_covers_whole_filter_not_page(client, db_session):
    db_session.add_all([_rec(amount="10.00") for _ in range(3)]
                       + [_rec(amount="5.00", currency="USD")])
    await db_session.flush()

    rows = (await client.get("/finance/v1/payments/summary?page_size=1",
                             headers=_h())).json()
    by_ccy = {r["currency"]: r for r in rows}
    assert by_ccy["CAD"]["count"] == 3
    assert by_ccy["CAD"]["total"] == "30.00"
    assert by_ccy["USD"]["count"] == 1


async def test_export_respects_filters_and_ignores_pagination(client, db_session):
    db_session.add_all([_rec(amount="10.00") for _ in range(3)]
                       + [_rec(amount="5.00", currency="USD")])
    await db_session.flush()

    r = await client.get("/finance/v1/payments/export?currency=CAD&page_size=1",
                         headers=_h())
    assert r.status_code == 200
    body = r.text.strip().splitlines()
    assert len(body) == 4                       # header + 3 rows
    assert body[0].startswith("payment_date,")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd finance-api && python -m pytest tests/test_payments_hub.py -v`
Expected: FAIL — the first test raises a `ResponseValidationError`, the rest 404 or return unfiltered data.

- [ ] **Step 3: Widen the response schema**

```python
# finance-api/app/schemas/payment.py — replace PaymentResponse
class PaymentResponse(BaseModel):
    id: uuid.UUID
    # Nullable on the table: expense-claim payments carry none of these.
    pa_id: uuid.UUID | None = None
    pa_number: str | None = None
    vendor_id: uuid.UUID | None = None
    vendor_name: str | None = None
    doc_kind: str | None = None
    doc_number: str | None = None
    payee_name: str | None = None
    payment_date: date
    payment_method: str
    reference: str | None = None
    amount: Decimal
    currency: str
    status: str
    batch_id: uuid.UUID | None = None
    bank_account_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    recorded_by: uuid.UUID
    notes: str | None = None
    remittance_status: str | None = None     # sent | not_sent, filled by the API layer
    created_at: datetime
    model_config = {"from_attributes": True}


class PaymentSummaryRow(BaseModel):
    currency: str
    count: int
    total: Decimal
```

- [ ] **Step 4: Add filters, summary, and export rows to the crud**

```python
# finance-api/app/crud/payment.py — replace get_all, add summary
import csv
import io
from datetime import date

from sqlalchemy import Select, and_, func, or_, select

from app.models.remittance import SENT, RemittanceNotification


def _filtered(*, pa_id=None, vendor_id=None, date_from=None, date_to=None,
              doc_kind=None, currency=None, payment_method=None, status=None,
              bank_account_id=None, batch_id=None, source=None, remittance=None,
              q=None) -> Select:
    stmt = select(PaymentRecord)
    if pa_id:
        stmt = stmt.where(PaymentRecord.pa_id == pa_id)
    if vendor_id:
        stmt = stmt.where(PaymentRecord.vendor_id == vendor_id)
    if date_from:
        stmt = stmt.where(PaymentRecord.payment_date >= date_from)
    if date_to:
        stmt = stmt.where(PaymentRecord.payment_date <= date_to)
    if doc_kind:
        stmt = stmt.where(PaymentRecord.doc_kind == doc_kind)
    if currency:
        stmt = stmt.where(PaymentRecord.currency == currency)
    if payment_method:
        stmt = stmt.where(PaymentRecord.payment_method == payment_method)
    if status:
        stmt = stmt.where(PaymentRecord.status == status)
    if bank_account_id:
        stmt = stmt.where(PaymentRecord.bank_account_id == bank_account_id)
    if batch_id:
        stmt = stmt.where(PaymentRecord.batch_id == batch_id)
    if source == "batch":
        stmt = stmt.where(PaymentRecord.batch_id.isnot(None))
    elif source == "single":
        stmt = stmt.where(PaymentRecord.batch_id.is_(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            PaymentRecord.doc_number.ilike(like),
            PaymentRecord.pa_number.ilike(like),
            PaymentRecord.vendor_name.ilike(like),
            PaymentRecord.reference.ilike(like),
        ))
    if remittance in ("sent", "not_sent"):
        # JSONB containment against the record id, GIN-indexed. Written as raw
        # SQL because the ORM `contains` form needs a literal on the right and
        # the right-hand side here is a correlated column.
        notified = select(RemittanceNotification.id).where(sa.and_(
            RemittanceNotification.status == SENT,
            sa.text("payment_remittance_notifications.payment_record_ids @> "
                    "to_jsonb(payment_records.id::text)"),
        ))
        stmt = stmt.where(notified.exists() if remittance == "sent"
                          else ~notified.exists())
    return stmt


async def get_all(db: AsyncSession, *, page: int = 1, page_size: int = 50,
                  **filters) -> tuple[list[PaymentRecord], int]:
    q = _filtered(**filters)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = list((await db.execute(
        q.order_by(PaymentRecord.payment_date.desc(), PaymentRecord.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return items, total


async def summary(db: AsyncSession, **filters) -> list[dict]:
    """Per-currency count and total across the WHOLE filter, not the page."""
    base = _filtered(**filters).subquery()
    rows = (await db.execute(
        select(base.c.currency, func.count(), func.coalesce(func.sum(base.c.amount), 0))
        .group_by(base.c.currency).order_by(base.c.currency)
    )).all()
    return [{"currency": c, "count": n, "total": t} for c, n, t in rows]


async def export_rows(db: AsyncSession, **filters) -> list[PaymentRecord]:
    """Every matching record, pagination deliberately ignored."""
    q = _filtered(**filters)
    return list((await db.execute(
        q.order_by(PaymentRecord.payment_date.desc(), PaymentRecord.created_at.desc())
    )).scalars().all())
```

Add `import sqlalchemy as sa` at the top. Prove the filter works with the
`remittance=sent` / `remittance=not_sent` test in Step 6 — both directions must be
asserted. "The query ran without error" is not evidence that it filtered.

- [ ] **Step 5: Wire the endpoints**

```python
# finance-api/app/api/v1/payments.py — replace list_payments, add summary + export
import csv
import io

from fastapi.responses import StreamingResponse

from app.models.remittance import SENT, RemittanceNotification

_FILTER_QUERY = dict(
    pa_id=Query(default=None), vendor_id=Query(default=None),
    date_from=Query(default=None), date_to=Query(default=None),
    doc_kind=Query(default=None), currency=Query(default=None),
    payment_method=Query(default=None), status=Query(default=None),
    bank_account_id=Query(default=None), batch_id=Query(default=None),
    source=Query(default=None), remittance=Query(default=None), q=Query(default=None),
)


class PaymentFilters(BaseModel):
    pa_id: uuid.UUID | None = None
    vendor_id: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    doc_kind: str | None = None
    currency: str | None = None
    payment_method: str | None = None
    status: str | None = None
    bank_account_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    source: str | None = None          # batch | single
    remittance: str | None = None      # sent | not_sent
    q: str | None = None


async def _remittance_status(db: AsyncSession,
                             records: list) -> dict[uuid.UUID, str]:
    """'sent' / 'not_sent' for the CURRENT PAGE only — block reasons are live
    and too costly to evaluate across an unbounded result set."""
    if not records:
        return {}
    ids = [str(r.id) for r in records]
    sent = set((await db.execute(sa.text(
        "SELECT DISTINCT jsonb_array_elements_text(payment_record_ids) AS rid"
        " FROM payment_remittance_notifications"
        " WHERE status = :s AND payment_record_ids ?| :ids"
    ), {"s": SENT, "ids": ids})).scalars().all())
    return {r.id: ("sent" if str(r.id) in sent else "not_sent") for r in records}


async def _payee_names(db: AsyncSession, records: list) -> dict[uuid.UUID, str]:
    """Vendor name for vendor payments; the claimant's name for claim payments,
    which carry no vendor columns at all."""
    out = {r.id: (r.vendor_name or "") for r in records}
    claim_ids = [r.doc_id for r in records
                 if r.doc_kind == "expense_claim" and r.doc_id]
    if claim_ids:
        rows = (await db.execute(
            select(ExpenseClaim.id, ExpenseClaim.employee_name)
            .where(ExpenseClaim.id.in_(claim_ids))
        )).all()
        name_by_claim = dict(rows)
        for r in records:
            if r.doc_kind == "expense_claim":
                out[r.id] = name_by_claim.get(r.doc_id, "")
    return out


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    filters: PaymentFilters = Depends(),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    items, total = await payment_crud.get_all(
        db, page=page, page_size=page_size, **filters.model_dump())
    status_by_id = await _remittance_status(db, items)
    payee_by_id = await _payee_names(db, items)
    out = []
    for r in items:
        d = PaymentResponse.model_validate(r).model_dump()
        d["payee_name"] = payee_by_id.get(r.id) or None
        d["remittance_status"] = status_by_id.get(r.id)
        out.append(d)
    return PaymentListResponse(items=out, total=total)


@router.get("/summary", response_model=list[PaymentSummaryRow])
async def payments_summary(filters: PaymentFilters = Depends(),
                           db: AsyncSession = Depends(get_db),
                           _: CurrentUser = ...):
    return await payment_crud.summary(db, **filters.model_dump())


@router.get("/export")
async def export_payments(filters: PaymentFilters = Depends(),
                          db: AsyncSession = Depends(get_db),
                          _: CurrentUser = ...):
    rows = await payment_crud.export_rows(db, **filters.model_dump())

    def _iter():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["payment_date", "doc_kind", "doc_number", "payee", "amount",
                    "currency", "payment_method", "source", "status"])
        yield buf.getvalue()
        for r in rows:
            buf.seek(0), buf.truncate(0)
            w.writerow([r.payment_date, r.doc_kind or "", r.doc_number or "",
                        r.vendor_name or "", r.amount, r.currency,
                        r.payment_method, "batch" if r.batch_id else "single",
                        r.status])
            yield buf.getvalue()

    return StreamingResponse(_iter(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="payments.csv"'})
```

Add `from app.models.mirrors import ExpenseClaim` for `_payee_names`.

`/summary` and `/export` are literal paths and must be declared before the catch-all `GET /{payment_id}` at the bottom of the file, exactly as `/due` and `/batches` already are.

- [ ] **Step 6: Add the remittance-filter test**

```python
# finance-api/tests/test_payments_hub.py — append
from app.models.remittance import KIND_VENDOR, SCOPE_PAYMENT, SENT, RemittanceNotification


async def test_remittance_filter_splits_sent_from_not_sent(client, db_session):
    r1, r2 = _rec(amount="10.00"), _rec(amount="20.00")
    db_session.add_all([r1, r2])
    await db_session.flush()
    db_session.add(RemittanceNotification(
        scope_kind=SCOPE_PAYMENT, scope_id=r1.id, recipient_kind=KIND_VENDOR,
        party_id=uuid.uuid4(), party_name="ACME", email="ap@acme.test",
        payment_record_ids=[str(r1.id)], amount=Decimal("10.00"), currency="CAD",
        status=SENT, attempts=1, created_by=uuid.uuid4()))
    await db_session.flush()

    sent = (await client.get("/finance/v1/payments?remittance=sent", headers=_h())).json()
    assert [i["amount"] for i in sent["items"]] == ["10.00"]
    not_sent = (await client.get("/finance/v1/payments?remittance=not_sent",
                                 headers=_h())).json()
    assert [i["amount"] for i in not_sent["items"]] == ["20.00"]
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd finance-api && python -m pytest tests/test_payments_hub.py -v`
Expected: PASS (7 tests)

- [ ] **Step 8: Verify no caller of the old schema broke**

Run: `cd finance-api && python -m pytest tests/ -v`
Expected: PASS at the pre-existing baseline. `test_ap_payment_writeback.py` and `test_payment_execute.py` consume `PaymentResponse` — check them specifically.

- [ ] **Step 9: Commit**

```bash
git add finance-api/app/schemas/payment.py finance-api/app/crud/payment.py finance-api/app/api/v1/payments.py finance-api/tests/test_payments_hub.py
git commit -m "feat(finance): payments hub API with filters, summary, export; fix claim-payment serialization"
```

---

### Task 11: Remittance panel and dialog (finance frontend)

**Files:**
- Create: `finance/src/services/remittance.ts`
- Create: `finance/src/components/remittance/RemittancePanel.tsx`
- Create: `finance/src/components/remittance/RemittanceDialog.tsx`

**Interfaces:**
- Consumes: Task 9's endpoints.
- Produces:
  - `services/remittance.ts`: `type RemittanceScope = { kind: 'batch' | 'payment'; id: string }`, `type PayeeGroup`, `type RemittancePreview`, `fetchPreview(scope): Promise<RemittancePreview>`, `sendRemittance(scope, recipients: {recipient_kind: string; party_id: string}[] | null): Promise<SendResult>`.
  - `RemittancePanel({ scope, onSent })` — the payee list with badges, selection, Refresh, Send.
  - `RemittanceDialog({ scope, open, onClose })` — modal wrapper.

- [ ] **Step 1: Write the API client**

```typescript
// finance/src/services/remittance.ts
import { financeApi } from '@/lib/api'

export type RemittanceScope = { kind: 'batch' | 'payment'; id: string }

export type PayeeGroupLine = {
  reference: string
  payment_date: string
  amount: string          // Decimal serialized as a string — Number() before arithmetic
}

export type PayeeGroup = {
  recipient_kind: 'vendor' | 'employee'
  party_id: string
  party_name: string
  email: string
  currency: string
  total: string
  block_reasons: string[]
  lines: PayeeGroupLine[]
  last_send: { status: string; error: string | null; attempts: number; sent_at: string | null } | null
}

export type RemittancePreview = {
  enabled: boolean
  reference: string
  payment_method: string
  groups: PayeeGroup[]
}

export type SendResult = {
  sent: number
  failed: number
  skipped: number
  results: { recipient_kind: string; party_id: string; party_name: string; status: string; error: string | null }[]
}

function base(scope: RemittanceScope): string {
  return scope.kind === 'batch'
    ? `/payments/batches/${scope.id}/remittance`
    : `/payments/${scope.id}/remittance`
}

export function fetchPreview(scope: RemittanceScope): Promise<RemittancePreview> {
  return financeApi.get<RemittancePreview>(`${base(scope)}/preview`)
}

export function sendRemittance(
  scope: RemittanceScope,
  recipients: { recipient_kind: string; party_id: string }[] | null,
): Promise<SendResult> {
  return financeApi.post<SendResult>(`${base(scope)}/send`, { recipients })
}
```

Open `finance/src/lib/api.ts` first and use whatever the finance app's own client is called and whatever base path it prepends — the import above assumes a `financeApi` with `get`/`post`. Adapt rather than inventing a new client.

- [ ] **Step 2: Write the panel**

```tsx
// finance/src/components/remittance/RemittancePanel.tsx
import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Send } from 'lucide-react'
import { StatusBadge } from '@uniops/shell'
import {
  fetchPreview, sendRemittance,
  type PayeeGroup, type RemittancePreview, type RemittanceScope,
} from '@/services/remittance'

const BLOCK_LABEL: Record<string, string> = {
  missing_email: 'Missing email',
  missing_invoice_no: 'Missing invoice no',
}

function readiness(g: PayeeGroup): { label: string; tone: string } {
  if (g.block_reasons.length > 0) {
    return { label: BLOCK_LABEL[g.block_reasons[0]] ?? g.block_reasons[0], tone: 'warning' }
  }
  if (g.last_send?.status === 'sent') return { label: 'Sent', tone: 'success' }
  if (g.last_send?.status === 'failed') return { label: 'Failed', tone: 'danger' }
  return { label: 'Ready', tone: 'neutral' }
}

export function RemittancePanel({ scope, onSent }: {
  scope: RemittanceScope
  onSent?: (r: { sent: number; failed: number; skipped: number }) => void
}) {
  const [preview, setPreview] = useState<RemittancePreview | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const p = await fetchPreview(scope)
      setPreview(p)
      setSelected(new Set(
        p.groups.filter(g => g.block_reasons.length === 0)
          .map(g => `${g.recipient_kind}:${g.party_id}`),
      ))
    } catch (e: any) {
      setError(e?.message ?? 'Failed to load remittance preview')
    }
  }, [scope.kind, scope.id])

  useEffect(() => { void load() }, [load])

  async function send() {
    if (!preview) return
    setBusy(true)
    setError(null)
    try {
      const recipients = preview.groups
        .filter(g => selected.has(`${g.recipient_kind}:${g.party_id}`))
        .map(g => ({ recipient_kind: g.recipient_kind, party_id: g.party_id }))
      const result = await sendRemittance(scope, recipients)
      onSent?.(result)
      await load()
    } catch (e: any) {
      setError(e?.message ?? 'Failed to send remittance advice')
    } finally {
      setBusy(false)
    }
  }

  if (!preview) {
    return <p className="text-sm text-neutral-500">{error ?? 'Loading…'}</p>
  }
  if (!preview.enabled) {
    return (
      <p className="text-sm text-neutral-500">
        Remittance email is not configured. Ask an administrator to enable it in
        Company Settings.
      </p>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-neutral-600">Reference: {preview.reference}</p>
        <button type="button" onClick={() => void load()}
                className="inline-flex items-center gap-1 text-sm text-neutral-600 hover:text-neutral-900">
          <RefreshCw className="h-4 w-4" />Refresh
        </button>
      </div>

      {error && <p className="text-sm text-danger-600">{error}</p>}

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-neutral-500">
            <th className="py-2 w-8" />
            <th className="py-2">Payee</th>
            <th className="py-2">Email</th>
            <th className="py-2 text-right">Amount</th>
            <th className="py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {preview.groups.map(g => {
            const key = `${g.recipient_kind}:${g.party_id}`
            const blocked = g.block_reasons.length > 0
            const r = readiness(g)
            return (
              <tr key={key} className={blocked ? 'text-neutral-400' : ''}>
                <td className="py-2">
                  <input type="checkbox" disabled={blocked} checked={selected.has(key)}
                         onChange={e => setSelected(prev => {
                           const next = new Set(prev)
                           e.target.checked ? next.add(key) : next.delete(key)
                           return next
                         })} />
                </td>
                <td className="py-2">{g.party_name}</td>
                <td className="py-2">{g.email || '—'}</td>
                <td className="py-2 text-right font-mono">
                  {Number(g.total).toLocaleString(undefined, { minimumFractionDigits: 2 })} {g.currency}
                </td>
                <td className="py-2"><StatusBadge tone={r.tone as any} label={r.label} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <div className="flex justify-end">
        <button type="button" disabled={busy || selected.size === 0} onClick={() => void send()}
                className="inline-flex items-center gap-2 rounded bg-primary-600 px-4 py-2 text-white disabled:opacity-50">
          <Send className="h-4 w-4" />
          {busy ? 'Sending…' : `Send (${selected.size})`}
        </button>
      </div>
    </div>
  )
}
```

Read `packages/shell/src/ui/badge.tsx` for `StatusBadge`'s actual prop names and pass those — the `tone` / `label` pair above is a guess and must be corrected to match.

- [ ] **Step 3: Write the dialog wrapper**

```tsx
// finance/src/components/remittance/RemittanceDialog.tsx
import { X } from 'lucide-react'
import { RemittancePanel } from './RemittancePanel'
import type { RemittanceScope } from '@/services/remittance'

export function RemittanceDialog({ scope, open, onClose }: {
  scope: RemittanceScope
  open: boolean
  onClose: () => void
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-3xl rounded-lg bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h3 className="text-lg font-semibold text-neutral-900">Send Remittance Advice</h3>
            <p className="text-sm text-neutral-500">
              Payment completed. Review the recipients below and send.
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>
        <RemittancePanel scope={scope} />
        <div className="mt-4 flex justify-end">
          <button type="button" onClick={onClose}
                  className="rounded border border-neutral-300 px-4 py-2 text-sm">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Typecheck**

Run: `cd finance && npx tsc -p tsconfig.app.json --noEmit`
Expected: no new errors. Compare against the count from `git stash && npx tsc … ; git stash pop` if the app already has pre-existing errors.

- [ ] **Step 5: Commit**

```bash
git add finance/src/services/remittance.ts finance/src/components/remittance/
git commit -m "feat(finance-ui): remittance panel and confirm dialog"
```

---

### Task 12: Payments hub page

**Files:**
- Create: `finance/src/pages/finance/PaymentsPage.tsx`
- Modify: `finance/src/app/routes.tsx`
- Modify: the shared nav config that `finance/src/components/layout/AppLayout.tsx` reads (line ~35 holds the Payment Batches entry)

**Interfaces:**
- Consumes: Task 10's endpoints, Task 11's `RemittancePanel`.
- Produces: route `/finance/payments`, nav entry `Payments`.

- [ ] **Step 1: Write the page**

```tsx
// finance/src/pages/finance/PaymentsPage.tsx
import { useEffect, useMemo, useState } from 'react'
import { Download } from 'lucide-react'
import { StatusBadge } from '@uniops/shell'
import { financeApi } from '@/lib/api'
import { RemittancePanel } from '@/components/remittance/RemittancePanel'

type PaymentRow = {
  id: string
  payment_date: string
  doc_kind: string | null
  doc_number: string | null
  payee_name: string | null
  amount: string
  currency: string
  payment_method: string
  batch_id: string | null
  status: string
  remittance_status: string | null
}

type SummaryRow = { currency: string; count: number; total: string }

type Filters = {
  date_from: string; date_to: string; q: string
  currency: string; source: string; payment_method: string; remittance: string
}

const EMPTY: Filters = {
  date_from: '', date_to: '', q: '',
  currency: '', source: '', payment_method: '', remittance: '',
}

function toQuery(f: Filters, extra: Record<string, string> = {}): string {
  const p = new URLSearchParams()
  Object.entries({ ...f, ...extra }).forEach(([k, v]) => { if (v) p.set(k, v) })
  return p.toString()
}

export default function PaymentsPage() {
  const [filters, setFilters] = useState<Filters>(EMPTY)
  const [rows, setRows] = useState<PaymentRow[]>([])
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState<SummaryRow[]>([])
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<string | null>(null)
  const qs = useMemo(() => toQuery(filters), [filters])

  useEffect(() => {
    void (async () => {
      const list = await financeApi.get<{ items: PaymentRow[]; total: number }>(
        `/payments?${toQuery(filters, { page: String(page), page_size: '50' })}`)
      setRows(list.items)
      setTotal(list.total)
      setSummary(await financeApi.get<SummaryRow[]>(`/payments/summary?${qs}`))
    })()
  }, [qs, page])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-4">
        {summary.map(s => (
          <div key={s.currency} className="rounded-lg border border-neutral-200 px-5 py-4">
            <p className="text-xs uppercase tracking-wide text-neutral-500">
              Total Paid ({s.currency})
            </p>
            <p className="mt-1 font-mono text-2xl font-bold text-neutral-900">
              {Number(s.total).toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </p>
            <p className="text-xs text-neutral-500">{s.count} payments</p>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-neutral-600">From
          <input type="date" value={filters.date_from} className="block rounded border px-2 py-1"
                 onChange={e => { setPage(1); setFilters(f => ({ ...f, date_from: e.target.value })) }} />
        </label>
        <label className="text-xs text-neutral-600">To
          <input type="date" value={filters.date_to} className="block rounded border px-2 py-1"
                 onChange={e => { setPage(1); setFilters(f => ({ ...f, date_to: e.target.value })) }} />
        </label>
        <label className="text-xs text-neutral-600">Search
          <input value={filters.q} placeholder="Document, payee, reference"
                 className="block rounded border px-2 py-1"
                 onChange={e => { setPage(1); setFilters(f => ({ ...f, q: e.target.value })) }} />
        </label>
        <label className="text-xs text-neutral-600">Source
          <select value={filters.source} className="block rounded border px-2 py-1"
                  onChange={e => { setPage(1); setFilters(f => ({ ...f, source: e.target.value })) }}>
            <option value="">All</option>
            <option value="batch">Batch</option>
            <option value="single">Single</option>
          </select>
        </label>
        <label className="text-xs text-neutral-600">Remittance
          <select value={filters.remittance} className="block rounded border px-2 py-1"
                  onChange={e => { setPage(1); setFilters(f => ({ ...f, remittance: e.target.value })) }}>
            <option value="">All</option>
            <option value="sent">Sent</option>
            <option value="not_sent">Not sent</option>
          </select>
        </label>
        <a href={`/payments/export?${qs}`} onClick={e => {
          e.preventDefault()
          void financeApi.download(`/payments/export?${qs}`, 'payments.csv')
        }} className="inline-flex items-center gap-2 rounded border px-3 py-2 text-sm">
          <Download className="h-4 w-4" />Export CSV
        </a>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase text-neutral-500">
            <th className="py-2">Date</th><th className="py-2">Document</th>
            <th className="py-2">Payee</th><th className="py-2 text-right">Amount</th>
            <th className="py-2">Method</th><th className="py-2">Source</th>
            <th className="py-2">Status</th><th className="py-2">Remittance</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.id} className="cursor-pointer hover:bg-neutral-50"
                onClick={() => setOpen(r.id)}>
              <td className="py-2">{r.payment_date}</td>
              <td className="py-2">{r.doc_number ?? '—'}</td>
              <td className="py-2">{r.payee_name ?? '—'}</td>
              <td className="py-2 text-right font-mono">
                {Number(r.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })} {r.currency}
              </td>
              <td className="py-2">{r.payment_method}</td>
              <td className="py-2">{r.batch_id ? 'Batch' : 'Single'}</td>
              <td className="py-2"><StatusBadge label={r.status} /></td>
              <td className="py-2">
                <StatusBadge label={r.remittance_status === 'sent' ? 'Sent' : 'Not sent'} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="text-xs text-neutral-500">{total} payments</p>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
             onClick={() => setOpen(null)}>
          <div className="w-full max-w-3xl rounded-lg bg-white p-6" onClick={e => e.stopPropagation()}>
            <h3 className="mb-4 text-lg font-semibold">Remittance</h3>
            <RemittancePanel scope={{ kind: 'payment', id: open }} />
          </div>
        </div>
      )}
    </div>
  )
}
```

`financeApi.download` may not exist — check `finance/src/lib/api.ts` and use whatever blob-download helper the app already has (epms has one; finance may too). If none exists, add one rather than opening a raw URL, which would drop the auth header.

- [ ] **Step 2: Wrap it in the page chrome**

Open `finance/src/pages/finance/PaymentBatchPage.tsx` and copy exactly how it wraps its content in `PortalChromeLayout` with an `activeKey`. Apply the same wrapper to `PaymentsPage`, with the `activeKey` matching the nav entry added in Step 3. A page that renders a bare `div` is wrong regardless of how it looks.

- [ ] **Step 3: Add the route and the nav entry**

```tsx
// finance/src/app/routes.tsx
import PaymentsPage from '@/pages/finance/PaymentsPage'
// beside the payment-batches entry
  { path: '/finance/payments', element: <PaymentsPage />,
    tab: { title: 'Payments', icon: 'Wallet', keyStrategy: 'static' } },
```

```tsx
// the nav config read by finance/src/components/layout/AppLayout.tsx (~line 35)
      { label: 'Payments', href: '/finance/payments', icon: Wallet, permission: 'view_finance' },
```

Place it immediately before the Payment Batches entry. Import `Wallet` from `lucide-react` alongside `Banknote`.

- [ ] **Step 4: Typecheck**

Run: `cd finance && npx tsc -p tsconfig.app.json --noEmit`
Expected: no new errors.

- [ ] **Step 5: Verify in the running dev stack**

Start the finance frontend and log in as a user holding `view_finance`. Confirm: the Payments entry appears in the sidebar; the page lists payments; a currency filter changes both the table and the summary cards; Export CSV downloads a file with the filtered rows; clicking a row opens the remittance panel.

- [ ] **Step 6: Commit**

```bash
git add finance/src/pages/finance/PaymentsPage.tsx finance/src/app/routes.tsx finance/src/components/layout/AppLayout.tsx
git commit -m "feat(finance-ui): payments hub page"
```

---

### Task 13: Batch page integration

**Files:**
- Modify: `finance/src/pages/finance/PaymentBatchPage.tsx`

**Interfaces:**
- Consumes: Task 11's `RemittanceDialog` and `RemittancePanel`.
- Produces: no new exports.

- [ ] **Step 1: Open the dialog after a successful execute**

In the handler that calls the execute endpoint, after the response resolves and the batch state is refreshed, set a piece of state that opens `RemittanceDialog` with `scope={{ kind: 'batch', id: batchId }}`. The dialog itself renders nothing when remittance is switched off (the preview returns `enabled: false`), so no extra conditional is needed on the page — but do not open it when the execute call reported zero paid lines.

- [ ] **Step 2: Add a Remittance section to the batch detail view**

Below the existing batch lines table, render `<RemittancePanel scope={{ kind: 'batch', id: batchId }} />` under a `Remittance` heading, for executed batches only.

- [ ] **Step 3: Typecheck**

Run: `cd finance && npx tsc -p tsconfig.app.json --noEmit`
Expected: no new errors.

- [ ] **Step 4: Verify against the dev stack**

Execute a draft batch containing one vendor PA with a vendor invoice number and a vendor remittance email. Confirm the dialog opens with that payee selected, Send reports `sent: 1`, and the Remittance section then shows `Sent`. Clear the vendor's emails, press Refresh, and confirm the row flips to `Missing email` with Send disabled.

- [ ] **Step 5: Commit**

```bash
git add finance/src/pages/finance/PaymentBatchPage.tsx
git commit -m "feat(finance-ui): open remittance dialog after batch execute"
```

---

### Task 14: DROPPED — EPMS PA detail integration

**Do not implement this task.** Decided 2026-07-22, before execution began.

The task would have copied the remittance client, panel, and dialog into the EPMS app so a
single PA payment could open the dialog on its own screen. That is a second copy of every
piece of this feature in a second app, and it would still leave OA Direct PA payments
without an entry point.

Instead, every non-batch payment is sent from the Payments hub (Task 12), which lists it
with `Single` as its source and opens the remittance panel in its detail drawer. EPMS keeps
no remittance UI. `PaDetailPage.tsx` is not modified.

Task numbering is preserved so the ledger and task briefs stay aligned. Skip straight from
Task 13 to Task 15.

---

### Task 15: Settings and vendor maintenance UI

**Files:**
- Modify: the epms Company Settings page (the one that already edits the `smtp_*` / `po_smtp_*` blocks)
- Modify: `epms/src/pages/vendors/VendorsPage.tsx`
- Modify: `epms/src/services/vendors.ts`

**Interfaces:**
- Consumes: Tasks 2 and 3's fields.
- Produces: no new exports.

- [ ] **Step 1: Add the Remittance section to Company Settings**

Find the page that renders the existing SMTP settings (search `epms/src` for `po_smtp_host`). Add a Remittance section editing `remittance_config` as a whole object: an Enabled switch, From Email, From Name, CC Email, and an optional SMTP User / SMTP Password override pair with the helper text "Leave blank to use the shared SMTP credentials." Patch it through the existing config PATCH call as `remittance_config: {...}`.

All copy is English.

- [ ] **Step 2: Add the field to vendor maintenance**

In `epms/src/services/vendors.ts`, add `remittance_email` to the vendor type and to the create/update payloads. In `VendorsPage.tsx`, add a Remittance Email input beside Contact Email, with the helper text "Where remittance advice is sent. Defaults to the contact email."

- [ ] **Step 3: Typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit`
Expected: no new errors against the baseline.

- [ ] **Step 4: Verify end to end against the dev stack**

Set a vendor's remittance email through the Vendors page, then confirm `GET /payments/batches/{id}/remittance/preview` for a batch paying that vendor returns the new address. This proves the whole chain — mdm column, epms view, forwarder, finance mirror — is connected. If the address does not appear, the `vendors` view from Task 2 Step 7 is the first thing to check.

- [ ] **Step 5: Commit**

```bash
git add epms/src/pages/vendors/VendorsPage.tsx epms/src/services/vendors.ts
git commit -m "feat(epms-ui): remittance settings and vendor remittance email"
```

---

### Task 16: Full-suite verification

**Files:** none modified.

- [ ] **Step 1: Run the finance suite alone**

Run: `cd finance-api && python -m pytest tests/ -v`
Expected: PASS. Never start a second finance-api pytest session while this one runs — concurrent runs drop each other's schema.

- [ ] **Step 2: Run the epms and mdm suites**

Run: `cd epms-api && python -m pytest tests/ -q` then `cd mdm-api && python -m pytest tests/ -q`
Expected: epms at its known pre-existing failure baseline and no new failures; mdm green.

- [ ] **Step 3: Typecheck both frontends**

Run: `cd finance && npx tsc -p tsconfig.app.json --noEmit` and `cd epms && npx tsc -p tsconfig.app.json --noEmit`
Expected: no new errors against each app's baseline.

- [ ] **Step 4: Record the results**

Write the actual counts into the branch's final commit message or the PR body — pass/fail numbers against the baseline, not "tests pass".

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "test: verify payments hub and remittance advice across services"
```

---

## Deployment Notes

Not part of the plan's tasks — for whoever releases this branch.

- Three services carry migrations: **mdm-api**, **epms-api**, **finance-api**. Run them in that order; the epms `vendors` view recreation depends on the mdm column existing.
- The release builds and pushes **all fifteen** service images at the same sha, not only the changed ones — the app server's global pull fails on a missing tag otherwise.
- Verify the production tag currently deployed before releasing; do not assume it equals `origin/main`.
- The feature stays inert until `remittance_config.enabled` is set, so the images can ship ahead of the switch. The Payments hub is live immediately and is worth announcing on its own.
