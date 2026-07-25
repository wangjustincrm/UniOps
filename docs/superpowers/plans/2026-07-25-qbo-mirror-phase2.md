# QBO Mirror — Phase 2 (AR, remaining transactions, long-tail masters, attachments)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the QBO mirror by adding every remaining entity — AR
(Invoice/Payment/CreditMemo), the other transactions (Purchase/Deposit/Transfer/
JournalEntry), the 11 long-tail masters (via a generic `qbo_raw` table), and
attachments — reusing the Phase-1 engine so the entire QuickBooks company is
mirrored inside UniOps.

**Architecture:** Phase 1 built the engine (registry → extract → load →
orchestrator → migration 0027) with typed header + line tables and a `raw` JSONB
fallback. Phase 2 registers more entities (mostly a model + mapper + registry line
each), adds ONE new load path for raw-only long-tail entities, and adds
attachments. All new schema goes in migration `0028`. Read-only mirror — still no
association, no posting.

**Tech Stack:** Python 3.12, async SQLAlchemy 2.0, Postgres (asyncpg), Alembic,
httpx, pytest.

---

## Decisions locked for this plan (flagged for review)

1. **`posting_type` is added to the shared `_TxnLineMixin`** (nullable String(10)),
   populated only for JournalEntry lines (Debit/Credit). GL debit/credit is the
   meaningful field for a journal mirror, so it's a column, not raw-only. Migration
   0028 ALTERs the three existing Phase-1 line tables to add it.
2. **Attachment binaries are stored in a `qbo_attachments.content` BYTEA column**,
   not file-api — the mirror stays self-contained (matches the "independent tables"
   philosophy), and there are only 3 tiny (~16 KB) PDFs. Downloaded via the
   `TempDownloadUri` signed URL during import.
3. **Entities without a party** (Deposit/Transfer/JournalEntry) pass
   `counterparty=None` to `txn_header` (which already yields `None` cleanly).
   Entity-specific columns (from/to account, payment_type, entity_type,
   deposit_to_account) live on the specific model, like Phase-1's `pay_type` —
   never on the shared mixin.

## Conventions (same as Phase 1)

- Models in `app/models/qbo.py`; engine in `scripts/qbo_import/`.
- Latest alembic head after Phase 1 is `0027_qbo_mirror_ap_core`; migration 0028
  chains off it. (Verify: `grep -h "^revision" alembic/versions/0027_qbo_mirror_ap_core.py`.)
- Tests: synthetic payloads (real structure, fake values) — never commit real QBO
  data. Env for pytest:
  ```
  export TEST_PG_PASSWORD=$(docker exec uniops_postgres env | grep '^POSTGRES_PASSWORD=' | cut -d= -f2- | tr -d '\r')
  export DATABASE_URL="postgresql+asyncpg://epms:epms_dev@localhost:5432/finance_test"
  export JWT_SECRET_KEY="test-secret"
  ```
  Run from `finance-api/`; conftest `db_session` runs `alembic upgrade head`
  (so 0028 must apply cleanly). One finance-api pytest at a time.
- `qbo_id` is the upsert key. Every model has a `raw` JSONB column.
- Commit author `Claude <noreply@anthropic.com>`; body ends with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Add `posting_type` to the shared line mixin + migration 0028 skeleton

**Files:**
- Modify: `app/models/qbo.py` (`_TxnLineMixin`)
- Modify: `scripts/qbo_import/mappers.py` (`txn_line`)
- Create: `alembic/versions/0028_qbo_mirror_phase2.py`
- Test: `tests/test_qbo_mappers.py` (extend), `tests/test_qbo_load.py` (extend)

- [ ] **Step 1: Write the failing mapper test** (append to `tests/test_qbo_mappers.py`)

```python
def test_txn_line_captures_posting_type_for_journal_lines():
    je_line = {
        "Id": "0", "Amount": 100.0, "DetailType": "JournalEntryLineDetail",
        "JournalEntryLineDetail": {
            "PostingType": "Credit",
            "AccountRef": {"value": "285", "name": "A/P"},
        },
    }
    line = txn_line(je_line, parent_qbo_id="9")
    assert line["posting_type"] == "Credit"
    assert line["account_id"] == "285"


def test_txn_line_posting_type_none_for_non_journal():
    normal = {
        "Id": "1", "Amount": 10.0, "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "1"}},
    }
    assert txn_line(normal, parent_qbo_id="1")["posting_type"] is None
```

- [ ] **Step 2: Run → FAIL** (`KeyError: 'posting_type'`).

Run: `pytest tests/test_qbo_mappers.py -k posting_type -v`

- [ ] **Step 3: Add `posting_type` to `txn_line`** (in `scripts/qbo_import/mappers.py`, inside the returned dict of `txn_line`, after `tax_code_ref`)

```python
        "posting_type": detail.get("PostingType"),
```

- [ ] **Step 4: Add `posting_type` column to `_TxnLineMixin`** (in `app/models/qbo.py`, after `tax_code_ref`)

```python
    posting_type: Mapped[str | None] = mapped_column(String(10))
```

- [ ] **Step 5: Create migration 0028 with the posting_type ALTERs**

```python
# alembic/versions/0028_qbo_mirror_phase2.py
"""qbo mirror phase 2 — AR, more transactions, long-tail raw, attachments

Revision ID: 0028_qbo_mirror_phase2
Revises: 0027_qbo_mirror_ap_core
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0028_qbo_mirror_phase2"
down_revision = "0027_qbo_mirror_ap_core"
branch_labels = None
depends_on = None

# Phase-1 line tables that gain posting_type.
_EXISTING_LINE_TABLES = ("qbo_bill_lines", "qbo_bill_payment_lines", "qbo_vendor_credit_lines")


def upgrade() -> None:
    for t in _EXISTING_LINE_TABLES:
        op.add_column(t, sa.Column("posting_type", sa.String(10)))
    # New tables are added by later steps of this migration (Tasks 2-10).


def downgrade() -> None:
    for t in _EXISTING_LINE_TABLES:
        op.drop_column(t, "posting_type")
```

- [ ] **Step 6: Run mapper tests → PASS**, and confirm the full Phase-1 suite still applies the migration cleanly.

Run: `pytest tests/test_qbo_mappers.py tests/test_qbo_load.py -v`
Expected: all pass (existing line tables now have posting_type; existing tests unaffected).

- [ ] **Step 7: Commit**

```bash
git add app/models/qbo.py scripts/qbo_import/mappers.py \
        alembic/versions/0028_qbo_mirror_phase2.py tests/test_qbo_mappers.py
git commit -m "feat(qbo): posting_type on line mixin + migration 0028 skeleton"
```

---

### Task 2: Invoice (AR) mirror — header CustomerRef + lines

**Files:** Modify `app/models/qbo.py`, `alembic/versions/0028_qbo_mirror_phase2.py`, `scripts/qbo_import/registry.py`, `tests/test_qbo_load.py`.

Reuses `_TxnHeaderMixin` + `_TxnLineMixin` (proven in Phase 1). Only difference from
Bill: `counterparty="CustomerRef"`.

- [ ] **Step 1: Failing test** (append to `tests/test_qbo_load.py`)

```python
from app.models.qbo import QboInvoice, QboInvoiceLine

INVOICES = [
    {"Id": "9", "SyncToken": "0", "DocNumber": "1001", "TxnDate": "2019-05-26",
     "DueDate": "2019-06-25", "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1,
     "TotalAmt": 108.0, "HomeTotalAmt": 108.0, "Balance": 0,
     "CustomerRef": {"value": "1", "name": "Bird Sanctuary"},
     "MetaData": {"LastUpdatedTime": "2019-05-27T10:00:00-07:00"},
     "Line": [
        {"Id": "1", "LineNum": 1, "Amount": 100.0, "Description": "Service",
         "DetailType": "SalesItemLineDetail",
         "SalesItemLineDetail": {"ItemAccountRef": {"value": "45", "name": "Sales"},
                                 "TaxCodeRef": {"value": "TAX"}}},
     ]},
]


@pytest.mark.asyncio
async def test_load_invoice_customer_and_lines(db_session):
    await load_entity(db_session, "Invoice", INVOICES)
    inv = (await db_session.execute(select(QboInvoice))).scalar_one()
    assert inv.qbo_id == "9"
    assert inv.counterparty_id == "1"
    assert inv.counterparty_name == "Bird Sanctuary"
    lines = (await db_session.execute(select(QboInvoiceLine))).scalars().all()
    assert len(lines) == 1
    assert lines[0].detail_type == "SalesItemLineDetail"
```

Note: `SalesItemLineDetail` uses `ItemAccountRef` (not `AccountRef`). `txn_line`
scans `_LINE_DETAIL_KEYS` for the detail block but reads `AccountRef` from it — so
`account_id` will be None for sales lines (they carry `ItemAccountRef`). That is
acceptable for the mirror (the full line is in `raw`); the test asserts
`detail_type`, not `account_id`, for the sales line.

- [ ] **Step 2: Run → FAIL** (`cannot import name 'QboInvoice'`).

- [ ] **Step 3: Add models** (append to `app/models/qbo.py`)

```python
class QboInvoice(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_invoices"


class QboInvoiceLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_invoice_lines"
```

- [ ] **Step 4: Register** (append to REGISTRY)

```python
REGISTRY.append(Entity(
    name="Invoice", model=m.QboInvoice,
    header=lambda o: mappers.txn_header(o, counterparty="CustomerRef"),
    line_model=m.QboInvoiceLine, line=mappers.txn_line,
))
```

- [ ] **Step 5: Add tables to migration 0028** — `qbo_invoices` (all `_TxnHeaderMixin`
columns, same shape as `qbo_bills` in 0027) and `qbo_invoice_lines` (same as
`qbo_bill_lines` PLUS the new `posting_type sa.String(10)` column). Add indexes
`ix_qbo_invoices_txn_date`, `ix_qbo_invoices_counterparty_id`,
`ix_qbo_invoices_doc_number`, `ix_qbo_invoice_lines_parent`. Drop in `downgrade()`
children-first, before the posting_type drops.

Use this exact column list for `qbo_invoices` (copy for each later header table too):
```python
op.create_table(
    "qbo_invoices",
    sa.Column("qbo_id", sa.String(20), primary_key=True),
    sa.Column("sync_token", sa.String(10)),
    sa.Column("doc_number", sa.String(64)),
    sa.Column("txn_date", sa.String(10)),
    sa.Column("due_date", sa.String(10)),
    sa.Column("currency", sa.String(10)),
    sa.Column("exchange_rate", sa.Numeric(20, 8)),
    sa.Column("total_amt", sa.Numeric(20, 2)),
    sa.Column("home_total_amt", sa.Numeric(20, 2)),
    sa.Column("balance", sa.Numeric(20, 2)),
    sa.Column("home_balance", sa.Numeric(20, 2)),
    sa.Column("global_tax_calc", sa.String(20)),
    sa.Column("private_note", sa.Text),
    sa.Column("counterparty_id", sa.String(20)),
    sa.Column("counterparty_name", sa.String(255)),
    sa.Column("last_updated_time", sa.DateTime(timezone=True)),
    sa.Column("deleted_at", sa.DateTime(timezone=True)),
    sa.Column("raw", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)
op.create_index("ix_qbo_invoices_txn_date", "qbo_invoices", ["txn_date"])
op.create_index("ix_qbo_invoices_counterparty_id", "qbo_invoices", ["counterparty_id"])
op.create_index("ix_qbo_invoices_doc_number", "qbo_invoices", ["doc_number"])
```
And the line table (copy this shape for every later line table, changing the name):
```python
op.create_table(
    "qbo_invoice_lines",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("parent_qbo_id", sa.String(20), nullable=False),
    sa.Column("line_num", sa.Integer),
    sa.Column("amount", sa.Numeric(20, 2)),
    sa.Column("detail_type", sa.String(48)),
    sa.Column("account_id", sa.String(20)),
    sa.Column("account_name", sa.String(255)),
    sa.Column("tax_code_ref", sa.String(20)),
    sa.Column("posting_type", sa.String(10)),
    sa.Column("description", sa.Text),
    sa.Column("linked_txn_id", sa.String(20)),
    sa.Column("linked_txn_type", sa.String(32)),
    sa.Column("raw", JSONB, nullable=False),
)
op.create_index("ix_qbo_invoice_lines_parent", "qbo_invoice_lines", ["parent_qbo_id"])
```

- [ ] **Step 6: Run → PASS** (`pytest tests/test_qbo_load.py -k invoice -v`).

- [ ] **Step 7: Commit**

```bash
git add app/models/qbo.py alembic/versions/0028_qbo_mirror_phase2.py \
        scripts/qbo_import/registry.py tests/test_qbo_load.py
git commit -m "feat(qbo): Invoice (AR) mirror + lines"
```

---

### Task 3: Payment (AR) mirror — header CustomerRef + deposit account + reconciliation lines

Payment settles invoices (line `LinkedTxn` → Invoice) and records the deposit
account. Adds a `deposit_to_account_id`/`_name` column pair (entity-specific,
like BillPayment's `pay_type`).

**Files:** Modify `app/models/qbo.py`, `alembic/versions/0028_qbo_mirror_phase2.py`, `scripts/qbo_import/mappers.py`, `scripts/qbo_import/registry.py`, `tests/test_qbo_load.py`.

- [ ] **Step 1: Failing test**

```python
from app.models.qbo import QboPayment, QboPaymentLine

PAYMENTS = [
    {"Id": "31", "SyncToken": "0", "TxnDate": "2019-05-27",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 108.0,
     "CustomerRef": {"value": "1", "name": "Bird Sanctuary"},
     "DepositToAccountRef": {"value": "35", "name": "Chequing"},
     "MetaData": {"LastUpdatedTime": "2019-05-28T10:00:00-07:00"},
     "Line": [{"Amount": 108.0, "LinkedTxn": [{"TxnId": "9", "TxnType": "Invoice"}]}]},
]


@pytest.mark.asyncio
async def test_load_payment_reconciliation_and_deposit(db_session):
    await load_entity(db_session, "Payment", PAYMENTS)
    p = (await db_session.execute(select(QboPayment))).scalar_one()
    assert p.counterparty_id == "1"
    assert p.deposit_to_account_id == "35"
    line = (await db_session.execute(select(QboPaymentLine))).scalar_one()
    assert line.linked_txn_id == "9"
    assert line.linked_txn_type == "Invoice"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Add models**

```python
class QboPayment(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_payments"
    deposit_to_account_id: Mapped[str | None] = mapped_column(String(20))
    deposit_to_account_name: Mapped[str | None] = mapped_column(String(255))


class QboPaymentLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_payment_lines"
```

- [ ] **Step 4: Header mapper + register**

```python
# append to scripts/qbo_import/mappers.py
def payment_header(obj: dict) -> dict:
    h = txn_header(obj, counterparty="CustomerRef")
    h["deposit_to_account_id"] = ref_id(obj, "DepositToAccountRef")
    h["deposit_to_account_name"] = ref_name(obj, "DepositToAccountRef")
    return h
```
```python
# append to REGISTRY
REGISTRY.append(Entity(
    name="Payment", model=m.QboPayment, header=mappers.payment_header,
    line_model=m.QboPaymentLine, line=mappers.txn_line,
))
```

- [ ] **Step 5: Migration** — `qbo_payments` (all `_TxnHeaderMixin` columns +
`deposit_to_account_id sa.String(20)` + `deposit_to_account_name sa.String(255)`)
and `qbo_payment_lines` (the standard line shape with posting_type). Indexes on
txn_date, counterparty_id, and parent. Drops in downgrade before posting_type drops.

- [ ] **Step 6: Run → PASS** (`pytest tests/test_qbo_load.py -k payment -v`).

- [ ] **Step 7: Commit** `feat(qbo): Payment (AR) mirror + reconciliation + deposit account`.

---

### Task 4: CreditMemo (AR) mirror — header CustomerRef + lines

Pure reuse (like Invoice, CustomerRef).

**Files:** Modify `app/models/qbo.py`, migration, `scripts/qbo_import/registry.py`, `tests/test_qbo_load.py`.

- [ ] **Step 1: Failing test**

```python
from app.models.qbo import QboCreditMemo, QboCreditMemoLine

CREDITMEMOS = [
    {"Id": "77", "SyncToken": "0", "DocNumber": "CM-1", "TxnDate": "2019-06-01",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 20.0,
     "CustomerRef": {"value": "1", "name": "Bird Sanctuary"},
     "MetaData": {"LastUpdatedTime": "2019-06-02T10:00:00-07:00"},
     "Line": [{"Id": "1", "LineNum": 1, "Amount": 20.0,
               "DetailType": "SalesItemLineDetail",
               "SalesItemLineDetail": {"ItemRef": {"value": "6"}}}]},
]


@pytest.mark.asyncio
async def test_load_credit_memo(db_session):
    await load_entity(db_session, "CreditMemo", CREDITMEMOS)
    cm = (await db_session.execute(select(QboCreditMemo))).scalar_one()
    assert cm.qbo_id == "77"
    assert cm.counterparty_id == "1"
    assert (await db_session.execute(select(QboCreditMemoLine))).scalar_one().amount == 20
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Models**

```python
class QboCreditMemo(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_credit_memos"


class QboCreditMemoLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_credit_memo_lines"
```

- [ ] **Step 4: Register**

```python
REGISTRY.append(Entity(
    name="CreditMemo", model=m.QboCreditMemo,
    header=lambda o: mappers.txn_header(o, counterparty="CustomerRef"),
    line_model=m.QboCreditMemoLine, line=mappers.txn_line,
))
```

- [ ] **Step 5: Migration** — `qbo_credit_memos` + `qbo_credit_memo_lines` (standard shapes) + indexes + downgrade drops.

- [ ] **Step 6: Run → PASS.**

- [ ] **Step 7: Commit** `feat(qbo): CreditMemo (AR) mirror + lines`.

---

### Task 5: Purchase mirror — EntityRef (polymorphic) + payment_type + account_ref + lines

Purchase = a spend not via a Bill (cheque/CC/cash). `EntityRef` is polymorphic
(Vendor/Customer/Employee, and can be null); `AccountRef` is the funding account;
`PaymentType` is Check/CreditCard/Cash; `Credit` (bool) marks a CC credit.

**Files:** Modify `app/models/qbo.py`, migration, `scripts/qbo_import/mappers.py`, `scripts/qbo_import/registry.py`, `tests/test_qbo_load.py`.

- [ ] **Step 1: Failing test**

```python
from app.models.qbo import QboPurchase, QboPurchaseLine

PURCHASES = [
    {"Id": "500", "SyncToken": "0", "TxnDate": "2019-05-01",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 75.0,
     "PaymentType": "Check", "Credit": False,
     "AccountRef": {"value": "224", "name": "Bank"},
     "EntityRef": {"value": "64", "name": "Acme Inc", "type": "Vendor"},
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"},
     "Line": [{"Id": "1", "LineNum": 1, "Amount": 75.0,
               "DetailType": "AccountBasedExpenseLineDetail",
               "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "141"}}}]},
]


@pytest.mark.asyncio
async def test_load_purchase_entity_and_payment(db_session):
    await load_entity(db_session, "Purchase", PURCHASES)
    p = (await db_session.execute(select(QboPurchase))).scalar_one()
    assert p.payment_type == "Check"
    assert p.account_id == "224"
    assert p.entity_id == "64"
    assert p.entity_type == "Vendor"
    assert (await db_session.execute(select(QboPurchaseLine))).scalar_one().account_id == "141"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Add models** (Purchase uses the header mixin plus its own columns; the
counterparty columns stay null since the party is in `entity_*`)

```python
class QboPurchase(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_purchases"
    payment_type: Mapped[str | None] = mapped_column(String(20))
    is_credit: Mapped[bool | None] = mapped_column(Boolean)
    account_id: Mapped[str | None] = mapped_column(String(20))
    account_name: Mapped[str | None] = mapped_column(String(255))
    entity_id: Mapped[str | None] = mapped_column(String(20))
    entity_name: Mapped[str | None] = mapped_column(String(255))
    entity_type: Mapped[str | None] = mapped_column(String(20))


class QboPurchaseLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_purchase_lines"
```

- [ ] **Step 4: Header mapper + register**

```python
# append to scripts/qbo_import/mappers.py
def purchase_header(obj: dict) -> dict:
    h = txn_header(obj, counterparty=None)
    h["payment_type"] = obj.get("PaymentType")
    h["is_credit"] = obj.get("Credit")
    h["account_id"] = ref_id(obj, "AccountRef")
    h["account_name"] = ref_name(obj, "AccountRef")
    ent = obj.get("EntityRef") or {}
    h["entity_id"] = ent.get("value")
    h["entity_name"] = ent.get("name")
    h["entity_type"] = ent.get("type")
    return h
```
Note: `txn_header(obj, counterparty=None)` yields `counterparty_id/name = None`
(since `ref_id(obj, None)` → `obj.get(None)` → None). Confirm this holds.
```python
# append to REGISTRY
REGISTRY.append(Entity(
    name="Purchase", model=m.QboPurchase, header=mappers.purchase_header,
    line_model=m.QboPurchaseLine, line=mappers.txn_line,
))
```

- [ ] **Step 5: Migration** — `qbo_purchases` (all `_TxnHeaderMixin` columns + `payment_type` String(20) + `is_credit` Boolean + `account_id` String(20) + `account_name` String(255) + `entity_id` String(20) + `entity_name` String(255) + `entity_type` String(20)) and `qbo_purchase_lines` (standard). Indexes on txn_date + parent. Drops before posting_type drops.

- [ ] **Step 6: Run → PASS.**

- [ ] **Step 7: Commit** `feat(qbo): Purchase mirror (entity/account/payment) + lines`.

---

### Task 6: Deposit mirror — deposit account + lines

Deposit groups received funds into an account; its lines link back to Payments.
No party header ref.

**Files:** Modify `app/models/qbo.py`, migration, `scripts/qbo_import/mappers.py`, `scripts/qbo_import/registry.py`, `tests/test_qbo_load.py`.

- [ ] **Step 1: Failing test**

```python
from app.models.qbo import QboDeposit, QboDepositLine

DEPOSITS = [
    {"Id": "600", "SyncToken": "0", "TxnDate": "2019-05-30",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 108.0,
     "HomeTotalAmt": 108.0, "DepositToAccountRef": {"value": "35", "name": "Chequing"},
     "MetaData": {"LastUpdatedTime": "2019-05-31T10:00:00-07:00"},
     "Line": [{"Amount": 108.0, "LinkedTxn": [{"TxnId": "31", "TxnType": "Payment"}]}]},
]


@pytest.mark.asyncio
async def test_load_deposit(db_session):
    await load_entity(db_session, "Deposit", DEPOSITS)
    d = (await db_session.execute(select(QboDeposit))).scalar_one()
    assert d.deposit_to_account_id == "35"
    line = (await db_session.execute(select(QboDepositLine))).scalar_one()
    assert line.linked_txn_id == "31"
    assert line.linked_txn_type == "Payment"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Add models**

```python
class QboDeposit(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_deposits"
    deposit_to_account_id: Mapped[str | None] = mapped_column(String(20))
    deposit_to_account_name: Mapped[str | None] = mapped_column(String(255))


class QboDepositLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_deposit_lines"
```

- [ ] **Step 4: Header mapper + register**

```python
# append to scripts/qbo_import/mappers.py
def deposit_header(obj: dict) -> dict:
    h = txn_header(obj, counterparty=None)
    h["deposit_to_account_id"] = ref_id(obj, "DepositToAccountRef")
    h["deposit_to_account_name"] = ref_name(obj, "DepositToAccountRef")
    return h
```
```python
# append to REGISTRY
REGISTRY.append(Entity(
    name="Deposit", model=m.QboDeposit, header=mappers.deposit_header,
    line_model=m.QboDepositLine, line=mappers.txn_line,
))
```

- [ ] **Step 5: Migration** — `qbo_deposits` (header mixin columns + deposit_to_account_id/name) + `qbo_deposit_lines` (standard) + indexes + downgrade drops.

- [ ] **Step 6: Run → PASS.**

- [ ] **Step 7: Commit** `feat(qbo): Deposit mirror + lines`.

---

### Task 7: Transfer mirror — from/to account, no lines

Transfer has NO lines and NO party — just `FromAccountRef`, `ToAccountRef`,
`Amount`, currency. It reuses `_TxnHeaderMixin` (party/doc_number stay null) with a
custom mapper that maps `Amount → total_amt` and adds the two account columns; no
line_model.

**Files:** Modify `app/models/qbo.py`, migration, `scripts/qbo_import/mappers.py`, `scripts/qbo_import/registry.py`, `tests/test_qbo_load.py`.

- [ ] **Step 1: Failing test**

```python
from app.models.qbo import QboTransfer

TRANSFERS = [
    {"Id": "1", "SyncToken": "0", "TxnDate": "2017-01-09",
     "CurrencyRef": {"value": "USD"}, "ExchangeRate": 1.3, "Amount": 1200000.0,
     "FromAccountRef": {"value": "67", "name": "CAD Chequing"},
     "ToAccountRef": {"value": "66", "name": "USD Chequing"},
     "MetaData": {"LastUpdatedTime": "2017-01-09T00:00:00-08:00"}},
]


@pytest.mark.asyncio
async def test_load_transfer_no_lines(db_session):
    await load_entity(db_session, "Transfer", TRANSFERS)
    t = (await db_session.execute(select(QboTransfer))).scalar_one()
    assert t.from_account_id == "67"
    assert t.to_account_id == "66"
    assert float(t.total_amt) == 1200000.0
    assert t.currency == "USD"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Add model** (no line model)

```python
class QboTransfer(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_transfers"
    from_account_id: Mapped[str | None] = mapped_column(String(20))
    from_account_name: Mapped[str | None] = mapped_column(String(255))
    to_account_id: Mapped[str | None] = mapped_column(String(20))
    to_account_name: Mapped[str | None] = mapped_column(String(255))
```

- [ ] **Step 4: Header mapper + register** (Transfer's amount is `Amount`, not `TotalAmt`)

```python
# append to scripts/qbo_import/mappers.py
def transfer_header(obj: dict) -> dict:
    h = txn_header(obj, counterparty=None)
    h["total_amt"] = obj.get("Amount")   # Transfer uses Amount, not TotalAmt
    h["from_account_id"] = ref_id(obj, "FromAccountRef")
    h["from_account_name"] = ref_name(obj, "FromAccountRef")
    h["to_account_id"] = ref_id(obj, "ToAccountRef")
    h["to_account_name"] = ref_name(obj, "ToAccountRef")
    return h
```
```python
# append to REGISTRY  (no line_model / line)
REGISTRY.append(Entity(name="Transfer", model=m.QboTransfer, header=mappers.transfer_header))
```

- [ ] **Step 5: Migration** — `qbo_transfers` (header mixin columns + from/to account id/name), index on txn_date. No line table. Drop in downgrade before posting_type drops.

- [ ] **Step 6: Run → PASS.**

- [ ] **Step 7: Commit** `feat(qbo): Transfer mirror (from/to account, no lines)`.

---

### Task 8: JournalEntry mirror — no party + debit/credit lines

JournalEntry has no header party; its lines carry `PostingType` (Debit/Credit) —
now captured by `txn_line` (Task 1) into the `posting_type` column.

**Files:** Modify `app/models/qbo.py`, migration, `scripts/qbo_import/registry.py`, `tests/test_qbo_load.py`.

- [ ] **Step 1: Failing test**

```python
from app.models.qbo import QboJournalEntry, QboJournalEntryLine

JOURNALS = [
    {"Id": "6", "SyncToken": "0", "DocNumber": "JE-1", "TxnDate": "2019-05-13",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 100.0,
     "MetaData": {"LastUpdatedTime": "2019-05-14T10:00:00-07:00"},
     "Line": [
        {"Id": "0", "Amount": 100.0, "DetailType": "JournalEntryLineDetail",
         "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": "38", "name": "Truck"}}},
        {"Id": "1", "Amount": 100.0, "DetailType": "JournalEntryLineDetail",
         "JournalEntryLineDetail": {"PostingType": "Credit", "AccountRef": {"value": "34", "name": "Equity"}}},
     ]},
]


@pytest.mark.asyncio
async def test_load_journal_entry_debit_credit(db_session):
    await load_entity(db_session, "JournalEntry", JOURNALS)
    je = (await db_session.execute(select(QboJournalEntry))).scalar_one()
    assert je.qbo_id == "6"
    lines = (await db_session.execute(
        select(QboJournalEntryLine).order_by(QboJournalEntryLine.id))).scalars().all()
    assert [l.posting_type for l in lines] == ["Debit", "Credit"]
    assert [l.account_id for l in lines] == ["38", "34"]
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Models**

```python
class QboJournalEntry(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_journal_entries"


class QboJournalEntryLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_journal_entry_lines"
```

- [ ] **Step 4: Register** (no party)

```python
REGISTRY.append(Entity(
    name="JournalEntry", model=m.QboJournalEntry,
    header=lambda o: mappers.txn_header(o, counterparty=None),
    line_model=m.QboJournalEntryLine, line=mappers.txn_line,
))
```

- [ ] **Step 5: Migration** — `qbo_journal_entries` (standard header mixin) + `qbo_journal_entry_lines` (standard line, includes posting_type) + indexes (txn_date, doc_number, parent) + downgrade drops.

- [ ] **Step 6: Run → PASS.**

- [ ] **Step 7: Commit** `feat(qbo): JournalEntry mirror + debit/credit lines`.

---

### Task 9: Long-tail masters via a generic `qbo_raw` table + raw-only load path

The 11 low-value masters (Term, TaxCode, TaxRate, TaxAgency, PaymentMethod, Class,
Item, CustomerType, CompanyCurrency, Employee, Department) land raw-only in one
generic table. This adds a second load path and a registry flag.

**Files:** Modify `app/models/qbo.py`, migration, `scripts/qbo_import/registry.py`, `scripts/qbo_import/load.py`, `scripts/qbo_import/orchestrator.py`, `tests/test_qbo_load.py`.

- [ ] **Step 1: Failing test**

```python
from app.models.qbo import QboRaw

TAXCODES = [
    {"Id": "2", "Name": "GST", "MetaData": {"LastUpdatedTime": "2019-01-01T00:00:00-08:00"}},
    {"Id": "3", "Name": "HST", "MetaData": {"LastUpdatedTime": "2019-01-02T00:00:00-08:00"}},
]


@pytest.mark.asyncio
async def test_load_raw_entity(db_session):
    n = await load_entity(db_session, "TaxCode", TAXCODES)
    assert n["inserted"] == 2
    rows = (await db_session.execute(
        select(QboRaw).where(QboRaw.entity_type == "TaxCode").order_by(QboRaw.qbo_id))).scalars().all()
    assert [r.qbo_id for r in rows] == ["2", "3"]
    assert rows[0].payload["Name"] == "GST"

    # Re-load with a change → update, not duplicate.
    TAXCODES[0]["Name"] = "GST-13"
    n2 = await load_entity(db_session, "TaxCode", TAXCODES)
    assert n2["updated"] == 2
    row = (await db_session.execute(
        select(QboRaw).where(QboRaw.entity_type == "TaxCode", QboRaw.qbo_id == "2"))).scalar_one()
    assert row.payload["Name"] == "GST-13"
```

- [ ] **Step 2: Run → FAIL** (`cannot import name 'QboRaw'`).

- [ ] **Step 3: Add the `QboRaw` model** (composite PK on entity_type+qbo_id)

```python
class QboRaw(TimestampMixin, Base):
    """Raw-only mirror for long-tail masters that don't warrant typed columns."""
    __tablename__ = "qbo_raw"

    entity_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    qbo_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    last_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
```

- [ ] **Step 4: Extend the registry to mark raw entities**

Add an optional `raw_only: bool = False` field to the `Entity` dataclass in
`scripts/qbo_import/registry.py`:
```python
@dataclass(frozen=True)
class Entity:
    name: str
    model: type | None = None
    header: Callable[[dict], dict] | None = None
    line_model: type | None = None
    line: Callable[[dict, str], dict] | None = None
    raw_only: bool = False
```
Then register the 11 long-tail entities:
```python
for _name in ("Term", "TaxCode", "TaxRate", "TaxAgency", "PaymentMethod", "Class",
              "Item", "CustomerType", "CompanyCurrency", "Employee", "Department"):
    REGISTRY.append(Entity(name=_name, raw_only=True))
```

- [ ] **Step 5: Add the raw-load branch to `load_entity`** (in `scripts/qbo_import/load.py`)

At the top of `load_entity`, before the typed path:
```python
async def load_entity(db: AsyncSession, name: str, objects: list[dict]) -> dict:
    ent = by_name(name)
    if ent.raw_only:
        return await _load_raw(db, name, objects)
    ...
```
And add `_load_raw`:
```python
from app.models.qbo import QboRaw
from scripts.qbo_import.mappers import last_updated


async def _load_raw(db: AsyncSession, entity_type: str, objects: list[dict]) -> dict:
    if not objects:
        return {"inserted": 0, "updated": 0}
    existing = set((await db.execute(
        select(QboRaw.qbo_id).where(QboRaw.entity_type == entity_type)
    )).scalars().all())
    ins = updated = 0
    for o in objects:
        row = {"entity_type": entity_type, "qbo_id": o["Id"],
               "last_updated_time": last_updated(o), "payload": o}
        stmt = insert(QboRaw).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["entity_type", "qbo_id"],
            set_={"payload": stmt.excluded.payload,
                  "last_updated_time": stmt.excluded.last_updated_time})
        await db.execute(stmt)
        (updated_key := o["Id"]) in existing and (updated := updated + 1) or (ins := ins + 1)
    await db.commit()
    db.expire_all()
    return {"inserted": ins, "updated": updated}
```
Note: prefer a clear if/else over the walrus one-liner — write:
```python
        if o["Id"] in existing:
            updated += 1
        else:
            ins += 1
```

- [ ] **Step 6: Make the orchestrator's soft-delete raw-aware** — `_soft_delete_extras`
in `orchestrator.py` reads `ent.model.qbo_id`; for raw entities `ent.model` is None.
Guard it:
```python
async def _soft_delete_extras(db, name, seen_ids):
    ent = by_name(name)
    if ent.raw_only:
        model = QboRaw
        local = set((await db.execute(
            select(model.qbo_id).where(model.entity_type == name, model.deleted_at.is_(None))
        )).scalars().all())
        gone = local - seen_ids
        if gone:
            await db.execute(update(model).where(
                model.entity_type == name, model.qbo_id.in_(gone)
            ).values(deleted_at=datetime.now(timezone.utc)))
            await db.commit()
        db.expire_all()
        return len(gone)
    model = ent.model
    ...
```
Add `from app.models.qbo import QboRaw` to orchestrator imports.

- [ ] **Step 7: Add `qbo_raw` to migration 0028**

```python
op.create_table(
    "qbo_raw",
    sa.Column("entity_type", sa.String(40), primary_key=True),
    sa.Column("qbo_id", sa.String(20), primary_key=True),
    sa.Column("last_updated_time", sa.DateTime(timezone=True)),
    sa.Column("deleted_at", sa.DateTime(timezone=True)),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)
```
Drop in downgrade before the posting_type drops.

- [ ] **Step 8: Run → PASS** (`pytest tests/test_qbo_load.py -k raw -v`), then full suite regression `pytest tests/test_qbo_load.py tests/test_qbo_orchestrator.py -v`.

- [ ] **Step 9: Commit** `feat(qbo): qbo_raw long-tail masters + raw-only load path`.

---

### Task 10: Attachments — metadata, txn links, and binary download

`Attachable` carries `FileName`, `ContentType`, `Size`, a signed `TempDownloadUri`,
and `AttachableRef[]` (each `{EntityRef:{value,type}}`) linking one file to one or
more transactions. Store metadata + a link row per ref + the downloaded bytes.

**Files:** Modify `app/models/qbo.py`, migration; Create `scripts/qbo_import/attachments.py`; Test `tests/test_qbo_attachments.py`.

- [ ] **Step 1: Failing test** (fake downloader — no network)

```python
# tests/test_qbo_attachments.py
import pytest
from sqlalchemy import select
from app.models.qbo import QboAttachment, QboAttachmentLink
from scripts.qbo_import.attachments import load_attachments

ATTACHABLES = [
    {"Id": "900", "FileName": "Invoice_1.pdf", "ContentType": "application/pdf",
     "Size": 16416, "TempDownloadUri": "https://example.test/doc/900",
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"},
     "AttachableRef": [
        {"EntityRef": {"value": "48751", "type": "Bill"}},
        {"EntityRef": {"value": "49832", "type": "Bill"}},
     ]},
]


class FakeDownloader:
    def __init__(self, blob): self.blob = blob; self.urls = []
    def get_bytes(self, url): self.urls.append(url); return self.blob


@pytest.mark.asyncio
async def test_load_attachments_metadata_links_and_bytes(db_session):
    dl = FakeDownloader(b"%PDF-1.4 fake")
    n = await load_attachments(db_session, ATTACHABLES, downloader=dl)
    assert n["inserted"] == 1
    a = (await db_session.execute(select(QboAttachment))).scalar_one()
    assert a.file_name == "Invoice_1.pdf"
    assert a.content_type == "application/pdf"
    assert a.content == b"%PDF-1.4 fake"
    assert dl.urls == ["https://example.test/doc/900"]
    links = (await db_session.execute(
        select(QboAttachmentLink).order_by(QboAttachmentLink.txn_id))).scalars().all()
    assert [(l.txn_id, l.txn_type) for l in links] == [("48751", "Bill"), ("49832", "Bill")]
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Add models**

```python
from sqlalchemy import LargeBinary  # add to imports

class QboAttachment(TimestampMixin, Base):
    __tablename__ = "qbo_attachments"
    qbo_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    file_name: Mapped[str | None] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    last_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)


class QboAttachmentLink(Base):
    __tablename__ = "qbo_attachment_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attachment_qbo_id: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    txn_id: Mapped[str] = mapped_column(String(20), nullable=False)
    txn_type: Mapped[str | None] = mapped_column(String(32))
```

- [ ] **Step 4: Write `attachments.py`**

```python
# scripts/qbo_import/attachments.py
"""Load QBO Attachable metadata, txn links, and downloaded bytes.

The binary is fetched from the signed TempDownloadUri (no auth, expires — so it
must be pulled during the run). A `downloader` with `.get_bytes(url) -> bytes` is
injected so tests run without network. Links are delete-then-insert per attachment.
"""
import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qbo import QboAttachment, QboAttachmentLink
from scripts.qbo_import.mappers import last_updated


class HttpDownloader:
    def get_bytes(self, url: str) -> bytes:
        r = httpx.get(url, timeout=120.0)
        r.raise_for_status()
        return r.content


async def load_attachments(db: AsyncSession, objects: list[dict], downloader=None) -> dict:
    downloader = downloader or HttpDownloader()
    if not objects:
        return {"inserted": 0, "updated": 0}
    existing = set((await db.execute(select(QboAttachment.qbo_id))).scalars().all())
    ins = updated = 0
    for o in objects:
        qid = o["Id"]
        content = None
        uri = o.get("TempDownloadUri")
        if uri:
            content = downloader.get_bytes(uri)
        row = {
            "qbo_id": qid, "file_name": o.get("FileName"),
            "content_type": o.get("ContentType"), "size": o.get("Size"),
            "content": content, "last_updated_time": last_updated(o), "raw": o,
        }
        stmt = insert(QboAttachment).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["qbo_id"],
            set_={k: stmt.excluded[k] for k in row if k != "qbo_id"})
        await db.execute(stmt)
        (updated := updated + 1) if qid in existing else (ins := ins + 1)

        await db.execute(delete(QboAttachmentLink).where(QboAttachmentLink.attachment_qbo_id == qid))
        for ref in (o.get("AttachableRef") or []):
            er = ref.get("EntityRef") or {}
            if er.get("value"):
                await db.execute(insert(QboAttachmentLink).values(
                    attachment_qbo_id=qid, txn_id=er["value"], txn_type=er.get("type")))
    await db.commit()
    db.expire_all()
    return {"inserted": ins, "updated": updated}
```
Note: replace the walrus one-liner counting with an explicit if/else:
```python
        if qid in existing:
            updated += 1
        else:
            ins += 1
```

- [ ] **Step 5: Add tables to migration 0028** — `qbo_attachments` (qbo_id PK,
file_name String(512), content_type String(128), size Integer, content
`sa.LargeBinary`, last_updated_time, deleted_at, raw JSONB, created_at, updated_at)
and `qbo_attachment_links` (id autoincrement PK, attachment_qbo_id String(20) NOT
NULL, txn_id String(20) NOT NULL, txn_type String(32)) + index
`ix_qbo_attachment_links_attachment` on attachment_qbo_id. Drops in downgrade
before the posting_type drops.

- [ ] **Step 6: Run → PASS** (`pytest tests/test_qbo_attachments.py -v`).

- [ ] **Step 7: Wire attachments into `run_import.py`** — after the entity sync, if
the run mode is full (or always), pull `Attachable` via `client.query_all` and call
`load_attachments`. Keep it simple and out of `run_sync` (attachments aren't a
registry entity):
```python
# in run_import.py _main, after run_sync, guarded by a --attachments flag defaulting on:
from scripts.qbo_import.attachments import load_attachments
from scripts.qbo_import.extract import extract_entity
...
    if with_attachments:
        async with AsyncSessionLocal() as db:
            objs = extract_entity(client, "Attachable", since=None)
            n = await load_attachments(db, objs)
            print(f"  Attachable: {n}")
```
Add a `--no-attachments` argparse flag to skip. (This wrapper isn't unit-tested;
verify it imports and `--help` works.)

- [ ] **Step 8: Commit** `feat(qbo): attachments — metadata, txn links, binary download`.

---

### Task 11: End-to-end smoke — full mirror against production (read-only), local DB

Manual verification (no automated test, no commit). Confirms every Phase-2 entity
loads from real QBO. Run inside the container or with an explicit local
`DATABASE_URL` — never the production finance DB.

- [ ] **Step 1: Pull a small AR + raw + attachment sample against a LOCAL DB**

Point a throwaway session at a local DB (e.g. finance_test after
`alembic upgrade head`), then run scoped pulls read-only from production QBO:
```
python scripts/qbo_import/run_import.py --full --entities Invoice,Payment,CreditMemo,Transfer,TaxCode
```
Expected: prints `full sync success` with per-entity counts matching the sample
sweep (Invoice ~724, Payment ~247, CreditMemo ~13, Transfer ~754, TaxCode ~25).

- [ ] **Step 2: Verify attachments download**

Run the attachments path (3 files expected):
```
python scripts/qbo_import/run_import.py --full --entities Account --  # then attachments run
```
Confirm `qbo_attachments` has 3 rows with non-null `content`, and
`qbo_attachment_links` rows point at Bills.

- [ ] **Step 3: Record counts** in the PR/summary as evidence. No commit.

---

## Self-review notes

- **Spec coverage (Phase-2 scope):** AR — Invoice (T2), Payment (T3), CreditMemo
  (T4); other transactions — Purchase (T5), Deposit (T6), Transfer (T7),
  JournalEntry (T8); long-tail 11 masters via qbo_raw (T9); attachments (T10);
  posting_type for GL lines (T1); live smoke (T11). Everything the spec deferred
  from Phase 1 (except the API and UI, which are Phase 3) is covered.
- **No placeholders:** every model, mapper, migration column, and test body is
  written out. Header/line table column lists are given once (Task 2) and each
  later table states its exact delta (extra columns) rather than re-pasting — the
  standard shape is explicit and unambiguous.
- **Type consistency:** `load_entity(db, name, objects)` unchanged signature; the
  new `raw_only` branch and `_load_raw` reuse it. `Entity` gains `raw_only` with a
  default so all Phase-1 registrations still construct. `txn_header(obj,
  counterparty=None)` is used by Purchase/Deposit/Transfer/JournalEntry — confirm
  `ref_id(obj, None)` returns None (it does: `dict.get(None)` → None). `posting_type`
  key added to `txn_line` output matches the new `_TxnLineMixin.posting_type` column
  and every line table's migration column.
- **Migration integrity:** one migration `0028` accumulates all Phase-2 tables +
  the three posting_type ALTERs; `down_revision = 0027_qbo_mirror_ap_core`;
  `downgrade()` drops new tables children-first, then the posting_type columns.
  Watch: keep every table/index name unique across the file.

## Out of scope (Phase 3)

- finance-api API endpoints and the Finance UI (sync button, progress, browse).
- Production deployment of migrations 0027 + 0028 and the real full import run.
- Any association/reconciliation between QBO data and NC65 / finance AP.
