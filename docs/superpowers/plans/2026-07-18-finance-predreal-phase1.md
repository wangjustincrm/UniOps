# Finance 预实对比 Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only Budget-vs-Actual grid (成本中心 × 收支项目, per 5 expense categories) whose actuals come from posted NC-synced JV lines, mapped account-aware via an imported table, with P/D tie-out rows, budget joined from budget-api, and an exceptions panel for unmapped lines — replacing finance's monthly Excel predreal.

**Architecture:** A new finance-api table `budget_actual_cc_map` (imported from `Budget vs Actual Mapping.xlsx`) becomes the single source for NC→UniOps cost-center resolution, resolved account-aware by `app/services/cc_map.py`. `nc_sync.py` uses it (replacing the hardcoded `CC_BY_*` dicts) and additionally stores the raw NC cost-center code on each JV line. A new `budget_actual_grid` CRUD in `account_balance.py` builds the grid over posted JV lines; budget figures come from a new budget-api internal read endpoint via `budget_client`. The frontend reworks `BudgetActualPage.tsx` from an actuals-only view into the full grid.

**Tech Stack:** Python 3.11, SQLAlchemy async, FastAPI, Alembic, openpyxl, pytest; React + TypeScript (finance frontend), TanStack Query.

## Global Constraints

- Branch: `feature/finance-predreal`, worktree `c:/Project/uniops/.worktrees/finance-predreal`.
- Run pytest INSIDE the finance-api container/venv, from `finance-api/`. Only ONE finance/epms pytest suite at a time (shared test DB). Override POSTGRES_* to the local docker `uniops_postgres` if running against local (see `feedback_uniops_admin_test_db_env`).
- **NEVER run alembic/scripts from the host** — host `.env` points at prod. Run inside the finance-api container (its `DATABASE_URL` is correct).
- New migrations: run `alembic heads` FIRST and set `down_revision` to the real chain tip — do NOT guess from filenames.
- Mapping authoritative values live in `C:\Project\Budget\Budget vs Actual Mapping.xlsx`; the import script reads it, code does not hardcode the 21 rows.
- All frontend user-facing strings in English (`feedback_uniops_ui_english_only`).
- Decimal columns serialize as JSON strings — frontend must `Number()`-coerce before arithmetic/`toFixed` (`feedback_uniops_decimal_as_string`).
- `income_expense_item_id` (JV line) == budget-api `BudgetPlanLine.account_id` == `budget_accounts.id` (shared master); `cost_center_id` likewise shared — so grid↔budget join is on these ids directly.

---

### Task 1: Mapping table — model + migration + import script

**Files:**
- Create: `finance-api/app/models/cc_map.py`
- Create: `finance-api/alembic/versions/<next>_budget_actual_cc_map.py`
- Create: `finance-api/scripts/import_cc_map.py`
- Test: `finance-api/tests/test_cc_map.py`

**Interfaces produced:**
- `BudgetActualCcMap` model: `id, account_code, dept_code, nc_cc_code, uniops_cc_code`.
- `scripts/import_cc_map.py <xlsx_path>` — truncate + reload the table from the mapping xlsx.

- [ ] **Step 1: Write the model**

Create `finance-api/app/models/cc_map.py`:

```python
"""NC → UniOps cost-center mapping (account-aware). Imported from finance's
`Budget vs Actual Mapping.xlsx`; replaces the hardcoded CC_BY_CODE/CC_BY_DEPT
dicts in nc_sync. Key = (account_code, dept_code, nc_cc_code) with 'ALL' wildcard."""
import uuid

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKey


class BudgetActualCcMap(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "budget_actual_cc_map"
    __table_args__ = (
        UniqueConstraint("account_code", "dept_code", "nc_cc_code",
                         name="uq_ba_cc_map"),
    )

    account_code: Mapped[str] = mapped_column(String(10), nullable=False)
    dept_code: Mapped[str] = mapped_column(String(20), nullable=False)   # 'ALL' wildcard
    nc_cc_code: Mapped[str] = mapped_column(String(20), nullable=False)  # 'ALL' wildcard
    uniops_cc_code: Mapped[str] = mapped_column(String(50), nullable=False)
```

> Verify the mixin import path matches the repo: check `finance-api/app/models/mirrors.py` top imports for `UUIDPrimaryKey, TimestampMixin` and copy that exact import line.

- [ ] **Step 2: Create the migration**

First, inside the finance-api container, find the chain tip:

Run: `docker exec uniops_finance_api sh -c "cd /app && alembic heads"`
Record the single head id (e.g. `0025_xxx`). Use it as `down_revision` below.

Create `finance-api/alembic/versions/<next>_budget_actual_cc_map.py` (name the file with the next sequence number):

```python
"""budget_actual_cc_map

Revision ID: <next>
Revises: <REAL_HEAD_FROM_ALEMBIC_HEADS>
"""
import sqlalchemy as sa
from alembic import op

revision = "<next>"
down_revision = "<REAL_HEAD_FROM_ALEMBIC_HEADS>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_actual_cc_map",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_code", sa.String(10), nullable=False),
        sa.Column("dept_code", sa.String(20), nullable=False),
        sa.Column("nc_cc_code", sa.String(20), nullable=False),
        sa.Column("uniops_cc_code", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_code", "dept_code", "nc_cc_code", name="uq_ba_cc_map"),
    )


def downgrade() -> None:
    op.drop_table("budget_actual_cc_map")
```

> Match the `created_at/updated_at` column defs to what `TimestampMixin` actually emits (check an existing finance migration that creates a mixin table, e.g. `0017_journal_vouchers.py`).

- [ ] **Step 3: Apply the migration (dev container)**

Run: `docker exec uniops_finance_api sh -c "cd /app && alembic upgrade head"`
Expected: `Running upgrade ... -> <next>, budget_actual_cc_map`.

- [ ] **Step 4: Write the import script**

Create `finance-api/scripts/import_cc_map.py`:

```python
"""Load budget_actual_cc_map from finance's `Budget vs Actual Mapping.xlsx`.

Truncate + reload (idempotent). Run INSIDE the finance-api container.
Sheet columns (row 1 header): 费用类别 | Sheet Name | Account in ERP | 部门 | 成本中心(NC) | 成本中心(UniOps)
- 'Account in ERP' only appears on each category's first row -> forward-fill.
- account_code from 'Account in ERP'; dept from 部门; nc_cc from 成本中心(NC);
  uniops_cc from 成本中心(UniOps). Blank/None -> skipped.

Usage: docker exec uniops_finance_api python scripts/import_cc_map.py "/path/to/Budget vs Actual Mapping.xlsx"
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db.base import AsyncSessionLocal  # noqa: E402
from app.models.cc_map import BudgetActualCcMap  # noqa: E402


def _rows_from_xlsx(path: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Sheet1"]
    out, account = [], None
    for i, row in enumerate(ws.iter_rows(values_only=True), 1):
        if i == 1:
            continue  # header
        _cat, _sheet, acct, dept, nc_cc, uni_cc = (list(row) + [None] * 6)[:6]
        if acct not in (None, ""):
            account = str(int(acct)) if isinstance(acct, float) else str(acct).strip()
        if dept in (None, "") or uni_cc in (None, ""):
            continue
        out.append({
            "account_code": account,
            "dept_code": str(dept).strip(),
            "nc_cc_code": str(nc_cc).strip() if nc_cc not in (None, "") else "ALL",
            "uniops_cc_code": str(uni_cc).strip(),
        })
    return out


async def main(path: str):
    rows = _rows_from_xlsx(path)
    async with AsyncSessionLocal() as s:
        await s.execute(delete(BudgetActualCcMap))
        for r in rows:
            s.add(BudgetActualCcMap(**r))
        await s.commit()
    print(f"import_cc_map: loaded {len(rows)} rows")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/import_cc_map.py <xlsx_path>")
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1]))
```

- [ ] **Step 5: Write the parser test**

Create `finance-api/tests/test_cc_map.py`:

```python
import openpyxl
from app.scripts_cc_map_helper import _rows_from_xlsx  # see note below


def _make_xlsx(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["费用类别", "Sheet Name", "Account in ERP", "部门", "成本中心(NC)", "成本中心(UniOps)"])
    ws.append(["制造费用", "ENG", 5101, "0106", "ALL", "MOH-0106-E01"])
    ws.append([None, "P02", None, "0104", "P02", "MOH-0104-P02"])
    ws.append(["研发费用", "R&D", 5301, "ALL", "ALL", "RD-0109"])
    ws.append([None, "blankrow", None, None, None, None])  # skipped (no dept/uni)
    p = tmp_path / "map.xlsx"
    wb.save(p)
    return str(p)


def test_rows_forward_fill_account_and_skip_blank(tmp_path):
    rows = _rows_from_xlsx(_make_xlsx(tmp_path))
    assert {r["account_code"] for r in rows} == {"5101", "5301"}
    p02 = next(r for r in rows if r["nc_cc_code"] == "P02")
    assert p02 == {"account_code": "5101", "dept_code": "0104",
                   "nc_cc_code": "P02", "uniops_cc_code": "MOH-0104-P02"}
    assert all(r["dept_code"] and r["uniops_cc_code"] for r in rows)
```

> To keep `_rows_from_xlsx` importable from tests without running the async `main`, put it in a small module `finance-api/app/services/cc_map_import.py` and have `scripts/import_cc_map.py` import it from there. Adjust the script's import and the test import to `from app.services.cc_map_import import _rows_from_xlsx`.

- [ ] **Step 6: Run the test**

Run: `docker exec uniops_finance_api sh -c "cd /app && pytest tests/test_cc_map.py -v"`
Expected: PASS (1 test).

- [ ] **Step 7: Commit**

```bash
git add finance-api/app/models/cc_map.py finance-api/app/services/cc_map_import.py \
        finance-api/scripts/import_cc_map.py finance-api/alembic/versions/*budget_actual_cc_map.py \
        finance-api/tests/test_cc_map.py
git commit -m "feat(finance): budget_actual_cc_map table + xlsx import (account-aware NC->UniOps CC map)"
```

---

### Task 2: `resolve_uniops_cc` — account-aware 3-tier resolution

**Files:**
- Modify: `finance-api/app/services/cc_map_import.py` (add `resolve_uniops_cc`) — or a new `app/services/cc_map.py`; keep resolver + parser together.
- Test: `finance-api/tests/test_cc_map.py` (append)

**Interfaces produced:**
- `resolve_uniops_cc(rows: list[dict], account_code: str, dept_code: str | None, nc_cc_code: str | None) -> str | None`
- `load_cc_map(db) -> list[dict]` (rows as plain dicts for caching)

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_cc_map.py`:

```python
from app.services.cc_map_import import resolve_uniops_cc

_ROWS = [
    {"account_code": "5101", "dept_code": "0106", "nc_cc_code": "ALL", "uniops_cc_code": "MOH-0106-E01"},
    {"account_code": "5101", "dept_code": "0104", "nc_cc_code": "P02", "uniops_cc_code": "MOH-0104-P02"},
    {"account_code": "6602", "dept_code": "0108", "nc_cc_code": "ALL", "uniops_cc_code": "GA-0100"},
    {"account_code": "5301", "dept_code": "ALL", "nc_cc_code": "ALL", "uniops_cc_code": "RD-0109"},
    {"account_code": "6603", "dept_code": "ALL", "nc_cc_code": "ALL", "uniops_cc_code": "FN-0103"},
]


def test_exact_code_wins():
    assert resolve_uniops_cc(_ROWS, "5101", "0104", "P02") == "MOH-0104-P02"

def test_dept_all_fallback():
    assert resolve_uniops_cc(_ROWS, "5101", "0106", "SOMECODE") == "MOH-0106-E01"

def test_account_all_all():
    assert resolve_uniops_cc(_ROWS, "5301", "0999", "X") == "RD-0109"
    assert resolve_uniops_cc(_ROWS, "6603", "0103", None) == "FN-0103"

def test_unmapped_returns_none():
    # 6602 + 0106 intentionally absent -> engineering-in-management is an exception
    assert resolve_uniops_cc(_ROWS, "6602", "0106", "ALL") is None

def test_account_aware_same_dept_differs_by_account():
    assert resolve_uniops_cc(_ROWS, "6602", "0108", "ALL") == "GA-0100"
    assert resolve_uniops_cc(_ROWS, "5101", "0108", "ALL") is None  # no 5101/0108 row
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec uniops_finance_api sh -c "cd /app && pytest tests/test_cc_map.py -k resolve or exact or dept_all or account_all or unmapped or same_dept -v"`
Expected: FAIL — `ImportError: cannot import name 'resolve_uniops_cc'`.

- [ ] **Step 3: Implement**

Append to `finance-api/app/services/cc_map_import.py`:

```python
async def load_cc_map(db) -> list[dict]:
    """All mapping rows as plain dicts (small table; caller caches per run)."""
    from sqlalchemy import select
    from app.models.cc_map import BudgetActualCcMap
    rows = (await db.execute(select(BudgetActualCcMap))).scalars().all()
    return [{"account_code": r.account_code, "dept_code": r.dept_code,
             "nc_cc_code": r.nc_cc_code, "uniops_cc_code": r.uniops_cc_code}
            for r in rows]


def resolve_uniops_cc(rows, account_code, dept_code, nc_cc_code):
    """(account, dept, nc_cc) exact -> (account, dept, ALL) -> (account, ALL, ALL) -> None.
    None = unmapped: the (account, dept, cc) combo is not a valid budget bucket
    (e.g. engineering dept in 6602) -> surfaces as an exception downstream."""
    dept = dept_code or ""
    cc = nc_cc_code or ""
    idx = {(r["account_code"], r["dept_code"], r["nc_cc_code"]): r["uniops_cc_code"]
           for r in rows}
    for key in ((account_code, dept, cc),
                (account_code, dept, "ALL"),
                (account_code, "ALL", "ALL")):
        hit = idx.get(key)
        if hit:
            return hit
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec uniops_finance_api sh -c "cd /app && pytest tests/test_cc_map.py -v"`
Expected: PASS (all cc_map tests).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/services/cc_map_import.py finance-api/tests/test_cc_map.py
git commit -m "feat(finance): account-aware 3-tier resolve_uniops_cc + load_cc_map"
```

---

### Task 3: sync account-aware + store `nc_cc_code` on JV lines

**Files:**
- Create: `finance-api/alembic/versions/<next>_jv_lines_nc_cc_code.py`
- Modify: `finance-api/app/models/journal_voucher.py` (JournalVoucherLine — add `nc_cc_code`)
- Modify: `finance-api/app/services/nc_sync.py` (`_resolve_dims`, `transform`, `_run_worker`; delete `CC_BY_CODE`/`CC_BY_DEPT`)
- Test: `finance-api/tests/test_nc_sync.py` (append)

**Interfaces:** `_resolve_dims(assid, aux, cc_map_rows, account_code, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust)` returns `(cc_id, dept_id, io_code, ba_id, partner_id, partner_name, nc_cc_code, had_hint)`. `transform` threads `account_code` + `nc_cc_code` into the line tuple.

- [ ] **Step 1: Migration for the new column**

`alembic heads` (as Task 1 Step 2), then create `finance-api/alembic/versions/<next>_jv_lines_nc_cc_code.py`:

```python
"""journal_voucher_lines.nc_cc_code

Revision ID: <next>
Revises: <budget_actual_cc_map revision from Task 1>
"""
import sqlalchemy as sa
from alembic import op

revision = "<next>"
down_revision = "<TASK1_REVISION>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("journal_voucher_lines",
                  sa.Column("nc_cc_code", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("journal_voucher_lines", "nc_cc_code")
```

Apply: `docker exec uniops_finance_api sh -c "cd /app && alembic upgrade head"`

- [ ] **Step 2: Add the column to the model**

In `finance-api/app/models/journal_voucher.py`, in `JournalVoucherLine`, add near `cost_center_id`:

```python
    nc_cc_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

(Ensure `String` is imported in that file.)

- [ ] **Step 3: Write the failing sync test**

Append to `finance-api/tests/test_nc_sync.py` (mirror the existing `transform` test setup in that file — reuse its `NcExtract` fixture builder; below is the assertion shape):

```python
def test_transform_resolves_cc_account_aware_and_stores_nc_cc_code():
    # aux: assid 'A1' -> dept 0104, cc P02 (manufacturing); 'A2' -> dept 0106, cc '' under 6602 (unmapped)
    cc_map_rows = [
        {"account_code": "5101", "dept_code": "0104", "nc_cc_code": "P02", "uniops_cc_code": "MOH-0104-P02"},
    ]
    uni_cc = {"MOH-0104-P02": _UUID_CC}   # code -> id
    extract = _extract_with_details([
        # (pk, idx, acct, dr, cr, ldr, lcr, curr, rate, expl, assid)
        ("V1", 1, "5101", "100", "0", "100", "0", "CAD", "1", "x", "A1"),
        ("V1", 2, "6602", "50", "0", "50", "0", "CAD", "1", "y", "A2"),
    ], aux={"A1": ("0104", "P02", "", "", ""), "A2": ("0106", "", "", "", "")})

    vouchers, lines, dims, unmapped = transform(
        extract, cc_map_rows, uni_cc, {"0104": _UUID_D1, "0106": _UUID_D2},
        {}, {}, {}, skip_pks=set())
    by_acct = {l[3]: l for l in lines}   # l[3] = account_code column in line tuple
    assert by_acct["5101"][/*cost_center_id idx*/ CC_IDX] == _UUID_CC
    assert by_acct["5101"][NC_CC_IDX] == "P02"
    assert by_acct["6602"][CC_IDX] is None      # 6602+0106 unmapped
    assert unmapped == 1
```

> Anchor `CC_IDX`/`NC_CC_IDX` to the actual line-tuple column order after Step 4 (the tuple gains `nc_cc_code`). Prefer asserting via a dict built in the test from named columns if the existing test file already does so.

- [ ] **Step 4: Rewrite `_resolve_dims` + thread account_code/nc_cc_code**

In `finance-api/app/services/nc_sync.py`:

(a) Delete the `CC_BY_CODE` and `CC_BY_DEPT` dicts (lines ~24-48).

(b) Replace `_resolve_dims` with:

```python
def _resolve_dims(assid, aux, cc_map_rows, account_code,
                  uni_cc, uni_dept, uni_ba, uni_sup, uni_cust):
    """-> (cc_id, dept_id, io_code, ba_id, partner_id, partner_name, nc_cc_code, had_hint).
    Cost center is resolved ACCOUNT-AWARE via budget_actual_cc_map (account+dept+
    nc_cc, ALL wildcard). Unmapped combos (e.g. engineering dept in 6602) -> cc_id
    None + had_hint True so they count as unmapped and surface as exceptions."""
    from app.services.cc_map_import import resolve_uniops_cc
    d, c, io, sup, cust = aux.get(assid, ("", "", "", "", ""))
    uni_code = resolve_uniops_cc(cc_map_rows, account_code, d, c)
    partner_id = partner_name = None
    code = sup or cust
    if code:
        hit = (uni_sup.get(sup) if sup else None) or (uni_cust.get(cust) if cust else None)
        if hit:
            partner_id, partner_name = hit
        else:
            partner_name = code
    return (uni_cc.get(uni_code) if uni_code else None,
            uni_dept.get(d) if d else None,
            io or None,
            uni_ba.get(io) if io else None,
            partner_id, partner_name,
            c or None,                 # nc_cc_code: raw NC cost-center code
            bool(c or d))
```

(c) In `transform`, change its signature to accept `cc_map_rows` (add as first param after `extract`), and in the details loop pass `acct` + `cc_map_rows` into `_resolve_dims`, unpack the extra `nc_cc_code`, and append it to the line tuple:

```python
        cc_id, dept_id, io_code, ba_id, partner_id, partner_name, nc_cc_code, had_hint = _resolve_dims(
            assid, extract.aux, cc_map_rows, acct, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust)
        if had_hint and cc_id is None:
            unmapped += 1
        ...
        lines.append((lid, jid, idx, acct, (expl or "")[:255], odr, ocr, ldr_, lcr_,
                      ccy_code, _d(rate) if rate else Decimal("1"),
                      cc_id, dept_id, ba_id, partner_id, partner_name, nc_cc_code))
```

(d) In `_run_worker`: load the map and pass to transform; add `nc_cc_code` to the line INSERT.

```python
        cur.execute("select account_code, dept_code, nc_cc_code, uniops_cc_code from budget_actual_cc_map")
        cc_map_rows = [dict(zip(("account_code","dept_code","nc_cc_code","uniops_cc_code"), r))
                       for r in cur.fetchall()]
        ...
        vouchers, lines, dims, unmapped = transform(extract, cc_map_rows, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust, skip_pks=...)
```

And extend the `journal_voucher_lines` INSERT column list + template to include `nc_cc_code` (append after `partner_name`), matching the extra tuple element.

- [ ] **Step 5: Run tests**

Run: `docker exec uniops_finance_api sh -c "cd /app && pytest tests/test_nc_sync.py -v"`
Expected: PASS — new account-aware test + existing transform/sync tests (update any existing `_resolve_dims`/`transform` call sites in the test file for the new signature).

- [ ] **Step 6: Commit**

```bash
git add finance-api/app/services/nc_sync.py finance-api/app/models/journal_voucher.py \
        finance-api/alembic/versions/*nc_cc_code.py finance-api/tests/test_nc_sync.py
git commit -m "feat(finance): NC sync resolves cost center account-aware via cc_map; store nc_cc_code on JV lines"
```

---

### Task 4: `budget_actual_grid` — actual side + P/D tie-out rows + 6603

**Files:**
- Modify: `finance-api/app/crud/account_balance.py` (`BUDGET_ACTUAL_ACCOUNTS` add 6603; add `budget_actual_grid`)
- Test: `finance-api/tests/test_account_balance.py` (append)

**Interfaces:** `budget_actual_grid(db, period, budget_lookup: dict) -> dict` — `budget_lookup` keyed `(cost_center_id, income_expense_item_id)` -> Decimal (Task 5 supplies; pass `{}` here). CRM004/CRM007 excluded from detail, summed into `payroll_actual`/`depreciation_actual` per category; `tie_ok` asserts detail+P+D == account period-debit.

- [ ] **Step 1: Write the failing test**

Append to `finance-api/tests/test_account_balance.py` (reuse existing `_posted_cc_event`/`_cc` helpers; add an income-expense-item helper if none — the file's `_posted_dim_event` already sets dims):

```python
async def test_budget_actual_grid_excludes_pd_and_ties_out(db_session):
    cc = await _cc(db_session, "MOH-0106-E01", "ENG")
    ba_it = await _budget_account(db_session, "CRM003", "IT General Fee")
    ba_pay = await _budget_account(db_session, "CRM007", "Payroll")
    ba_dep = await _budget_account(db_session, "CRM004", "Depreciation")
    # 5101: detail CRM003=100, Payroll(CRM007)=300, Depreciation(CRM004)=50
    await _posted_line(db_session, "5101", cc, ba_it, debit="100.00", period="2026-06")
    await _posted_line(db_session, "5101", cc, ba_pay, debit="300.00", period="2026-06")
    await _posted_line(db_session, "5101", cc, ba_dep, debit="50.00", period="2026-06")
    await jv_crud.backfill_posted_jvs(db_session)

    grid = await ab.budget_actual_grid(db_session, "2026-06", {})
    moh = next(c for c in grid["categories"] if c["account_code"] == "5101")
    detail = {(d["cost_center_code"], d["income_expense_code"]): d for d in moh["detail"]}
    assert (("MOH-0106-E01", "CRM003")) in detail
    assert detail[("MOH-0106-E01", "CRM003")]["actual"] == "100.00"
    assert all(d["income_expense_code"] not in ("CRM004", "CRM007") for d in moh["detail"])
    assert moh["payroll_actual"] == "300.00"
    assert moh["depreciation_actual"] == "50.00"
    assert moh["category_actual_total"] == "450.00"   # 100 + 300 + 50 == account debit
    assert moh["tie_ok"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker exec uniops_finance_api sh -c "cd /app && pytest tests/test_account_balance.py -k budget_actual_grid -v"`
Expected: FAIL — `budget_actual_grid` undefined (and helpers `_budget_account`/`_posted_line` if not present — add them mirroring existing `_posted_cc_event`).

- [ ] **Step 3: Implement `budget_actual_grid`**

In `finance-api/app/crud/account_balance.py`: add `"6603": "FN"` to `BUDGET_ACTUAL_ACCOUNTS`, then add:

```python
_PAYROLL_PREFIX = "CRM007"
_DEPREC_PREFIX = "CRM004"


async def _ba_lines(db: AsyncSession, account_code: str, period: str):
    """Posted lines for a category account's subtree in `period`, joined to
    budget_accounts for the income-expense (CRM) code/name. One row per
    (cost_center_id, income_expense_item_id)."""
    from app.models.mirrors import BudgetAccount, CostCenter
    subtree = await _subtree_codes(db, account_code)
    q = (select(JournalVoucherLine.cost_center_id,
                JournalVoucherLine.income_expense_item_id,
                CostCenter.code, CostCenter.name,
                BudgetAccount.code, BudgetAccount.name,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .outerjoin(CostCenter, JournalVoucherLine.cost_center_id == CostCenter.id)
         .outerjoin(BudgetAccount, JournalVoucherLine.income_expense_item_id == BudgetAccount.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code.in_(subtree))
         .group_by(JournalVoucherLine.cost_center_id, JournalVoucherLine.income_expense_item_id,
                   CostCenter.code, CostCenter.name, BudgetAccount.code, BudgetAccount.name))
    return (await db.execute(q)).all()


async def budget_actual_grid(db: AsyncSession, period: str, budget_lookup: dict) -> dict:
    """④ Budget-vs-Actual grid. For each of the 5 expense categories: detail rows
    per (cost center × income-expense item) with budget/actual/variance, EXCLUDING
    Payroll(CRM007)/Depreciation(CRM004) which roll into category-level tie-out
    rows. actual = period DEBIT (expense accounts net to ~0 via 结转)."""
    categories = []
    for acct, category in BUDGET_ACTUAL_ACCOUNTS.items():
        detail, payroll, deprec, total = [], _ZERO, _ZERO, _ZERO
        for cc_id, ie_id, cc_code, cc_name, ie_code, ie_name, dr in await _ba_lines(db, acct, period):
            dr = Decimal(dr)
            total += dr
            code = ie_code or ""
            if code.startswith(_PAYROLL_PREFIX):
                payroll += dr; continue
            if code.startswith(_DEPREC_PREFIX):
                deprec += dr; continue
            budget = Decimal(budget_lookup.get((cc_id, ie_id), _ZERO))
            detail.append({
                "cost_center_id": str(cc_id) if cc_id else None,
                "cost_center_code": cc_code, "cost_center_name": cc_name,
                "income_expense_code": ie_code, "income_expense_name": ie_name,
                "budget": _s(budget), "actual": _s(dr), "variance": _s(budget - dr),
            })
        detail.sort(key=lambda d: (d["cost_center_code"] or "￿", d["income_expense_code"] or "￿"))
        detail_total = sum((Decimal(d["actual"]) for d in detail), _ZERO)
        categories.append({
            "account_code": acct, "category": category, "detail": detail,
            "payroll_actual": _s(payroll), "depreciation_actual": _s(deprec),
            "detail_actual_total": _s(detail_total),
            "category_actual_total": _s(total),
            "tie_ok": (detail_total + payroll + deprec) == total,
        })
    return {"period": period, "categories": categories,
            "unmapped": await _ba_unmapped(db, period)}
```

(Leave `_ba_unmapped` as a stub returning `[]` for now — Task 6 implements it. Add `async def _ba_unmapped(db, period): return []` above `budget_actual_grid`.)

- [ ] **Step 4: Run to verify it passes**

Run: `docker exec uniops_finance_api sh -c "cd /app && pytest tests/test_account_balance.py -k budget_actual_grid -v"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/account_balance.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): budget_actual_grid (5 categories, cost-center×item detail, P/D tie-out rows)"
```

---

### Task 5: Budget join — budget-api internal endpoint + finance fetch + merge

**Files:**
- Create/Modify: `budget-api/app/api/v1/...` (add `GET /internal/plan-lines`)
- Modify: `finance-api/app/services/budget_client.py` (add `fetch_plan_lines`)
- Modify: `finance-api/app/crud/account_balance.py` (nothing — grid already takes `budget_lookup`)
- Test: `budget-api/tests/...` (endpoint) + `finance-api/tests/test_account_balance.py` (budget merged variance)

**Interfaces:** budget-api `GET /internal/plan-lines?fiscal_year=&month=` -> `[{cost_center_id, account_id, amount}]` (current approved plans only). finance `fetch_plan_lines(fiscal_year, month) -> dict[(uuid,uuid)] -> Decimal`.

- [ ] **Step 1: budget-api endpoint test (write first)**

In budget-api tests, assert: given a current approved plan for (cc, 2026) with a line (account, month=6, amount=1000), `GET /internal/plan-lines?fiscal_year=2026&month=6` returns `[{cost_center_id: cc, account_id: account, amount: "1000.00"}]`; a draft (non-current) plan's lines are excluded.

- [ ] **Step 2: Implement the endpoint**

Add to budget-api (mirror existing plan read routes). Query: join `BudgetPlan` (is_current AND status='approved', fiscal_year) → `BudgetPlanLine` (month) → return cc_id/account_id/amount. Register the router.

```python
@router.get("/internal/plan-lines")
async def internal_plan_lines(fiscal_year: int, month: int, db: AsyncSession = Depends(get_db)):
    q = (select(BudgetPlan.cost_center_id, BudgetPlanLine.account_id, BudgetPlanLine.amount)
         .join(BudgetPlanLine, BudgetPlanLine.plan_id == BudgetPlan.id)
         .where(BudgetPlan.fiscal_year == fiscal_year, BudgetPlan.is_current.is_(True),
                BudgetPlan.status == "approved", BudgetPlanLine.month == month))
    return [{"cost_center_id": str(cc), "account_id": str(a), "amount": str(amt)}
            for cc, a, amt in (await db.execute(q)).all()]
```

- [ ] **Step 3: finance budget_client.fetch_plan_lines**

Add to `finance-api/app/services/budget_client.py` (mirror existing httpx calls, base URL from settings):

```python
async def fetch_plan_lines(fiscal_year: int, month: int) -> dict:
    """(cost_center_id, account_id) -> Decimal budget for current approved plans."""
    from decimal import Decimal
    async with httpx.AsyncClient(base_url=_BUDGET_BASE, timeout=15) as c:
        r = await c.get("/internal/plan-lines", params={"fiscal_year": fiscal_year, "month": month})
        r.raise_for_status()
    import uuid
    return {(uuid.UUID(x["cost_center_id"]), uuid.UUID(x["account_id"])): Decimal(x["amount"])
            for x in r.json()}
```

- [ ] **Step 4: finance grid variance test with budget**

Append to `finance-api/tests/test_account_balance.py`: pass a `budget_lookup={(cc_id, ie_id): Decimal("1000")}` into `budget_actual_grid`; assert the CRM003 detail row's `budget=="1000.00"`, `variance=="900.00"` (1000-100).

- [ ] **Step 5: Run tests**

Run (finance): `docker exec uniops_finance_api sh -c "cd /app && pytest tests/test_account_balance.py -k budget_actual_grid -v"`
Run (budget): `docker exec uniops_budget_api sh -c "cd /app && pytest tests/ -k plan_lines -v"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add budget-api/app/api finance-api/app/services/budget_client.py finance-api/tests/test_account_balance.py budget-api/tests
git commit -m "feat(budget/finance): internal plan-lines endpoint + finance fetch for predreal budget column"
```

> **待确认①**: this assumes budget-api holds the 2026 approved plans matching finance's Excel budget. Verify in dev: `GET /internal/plan-lines?fiscal_year=2026&month=6` returns non-empty. If EMPTY (budget only lives in the Excel), add a one-off step: export the Excel budget to the plan CSV format and load via budget-api `import_plan_csv` before this column is meaningful. Flag to the user either way.

---

### Task 6: Exceptions panel data (`_ba_unmapped`)

**Files:**
- Modify: `finance-api/app/crud/account_balance.py` (`_ba_unmapped`)
- Test: `finance-api/tests/test_account_balance.py` (append)

- [ ] **Step 1: Failing test**

Post a JV line under 6602 whose (account, dept, nc_cc) is unmapped so sync-equivalent `cost_center_id IS NULL` but `nc_cc_code` present (in the test, insert a JV line directly with `cost_center_id=None, nc_cc_code="X", account_code="6602"`, an income-expense item, debit 77). Assert `budget_actual_grid(...)["unmapped"]` contains a row with `account_code="6602"`, `nc_cc_code="X"`, `actual=="77.00"`.

- [ ] **Step 2: Implement**

Replace the `_ba_unmapped` stub:

```python
async def _ba_unmapped(db: AsyncSession, period: str) -> list:
    """Posted lines in the 5 categories that resolved to NO cost center
    (account-aware map miss) but carry an NC cost-center code — the exceptions
    a human must fix in NC or add to the map."""
    from app.models.mirrors import BudgetAccount
    accts = set()
    for a in BUDGET_ACTUAL_ACCOUNTS:
        accts |= await _subtree_codes(db, a)
    q = (select(JournalVoucherLine.account_code, JournalVoucherLine.nc_cc_code,
                BudgetAccount.code, BudgetAccount.name,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                func.count())
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .outerjoin(BudgetAccount, JournalVoucherLine.income_expense_item_id == BudgetAccount.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code.in_(accts),
                JournalVoucherLine.cost_center_id.is_(None),
                JournalVoucherLine.nc_cc_code.isnot(None))
         .group_by(JournalVoucherLine.account_code, JournalVoucherLine.nc_cc_code,
                   BudgetAccount.code, BudgetAccount.name))
    return [{"account_code": ac, "nc_cc_code": nc, "income_expense_code": iec,
             "income_expense_name": ien, "actual": _s(Decimal(dr)), "line_count": int(n)}
            for ac, nc, iec, ien, dr, n in (await db.execute(q)).all()]
```

- [ ] **Step 3: Run / Step 4: Commit** (as prior tasks) — commit msg: `feat(finance): predreal exceptions panel (unmapped posted lines)`.

---

### Task 7: API endpoint `GET /gl/budget-actual-grid`

**Files:**
- Modify: `finance-api/app/api/v1/account_balance.py` (add route)
- Test: `finance-api/tests/test_account_balance.py` (endpoint smoke)

- [ ] **Step 1: Failing endpoint test** — call the route for a period, assert 200 + `categories` has 5 entries + `unmapped` key present.

- [ ] **Step 2: Implement** — mirror the existing `/gl/budget-actual` route; fetch budget via `budget_client.fetch_plan_lines(int(period[:4]), int(period[5:7]))` (guard budget-api errors → empty lookup + log, so the grid still renders actuals if budget-api is down), then `return await budget_actual_grid(db, period, budget_lookup)`.

```python
@router.get("/gl/budget-actual-grid")
async def budget_actual_grid_ep(period: str, db: AsyncSession = Depends(get_db), user=Depends(...)):
    try:
        budget = await budget_client.fetch_plan_lines(int(period[:4]), int(period[5:7]))
    except Exception:
        logger.exception("budget-api plan-lines fetch failed; rendering actuals only")
        budget = {}
    return await ab.budget_actual_grid(db, period, budget)
```

- [ ] **Step 3: Run / Step 4: Commit** — `feat(finance): GET /gl/budget-actual-grid endpoint`.

---

### Task 8: Frontend — rework `BudgetActualPage` into the full grid

**Files:**
- Modify: `finance/src/pages/finance/BudgetActualPage.tsx`
- Test: frontend typecheck baseline (no unit test infra for pages)

- [ ] **Step 1: Capture tsc baseline** — `cd finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -c "error TS"`. Record; must not increase.

- [ ] **Step 2: Rewrite the page** — fetch `GET /gl/budget-actual-grid?period=`; render per category block a table with columns `Cost Center | Income/Expense | Budget | Actual | Variance | ⋯`(Number()-coerce all decimals). Append two grey category-level rows `Payroll` / `Depreciation` (actual only). Category footer: total + a tie badge (`tie_ok ? "Tied" : "OFF by …"`, red when off). Below all categories, an "Exceptions" section listing `unmapped[]` with a Vouchers drill button (reuse `AccountVouchersModal` with `dimsValues={cost_center:'none'}` scoped by account) and a note "fix in NC or add to mapping". Keep `AccountVouchersModal`/`JvDetailModal` wiring. Read-only. `PortalChromeLayout activeKey="portal:/finance/budget-actual"`, permission gate unchanged (`view_budget_dashboard`).

- [ ] **Step 3: tsc** — recount errors == baseline. Fix new ones.

- [ ] **Step 4: Commit** — `feat(finance): rework Budget Actual page into full predreal grid (budget/actual/variance + P/D + exceptions)`.

---

## Verification (post-implementation, dev)

1. Apply migrations + `docker exec uniops_finance_api python scripts/import_cc_map.py "<path>/Budget vs Actual Mapping.xlsx"`.
2. Full NC reload (dev) so JV lines get account-aware `cost_center_id` + `nc_cc_code`.
3. Restart finance-api + budget-api; open Budget Actual for a closed month (e.g. 2026-06).
4. Confirm: 5 category blocks; each detail row cost-center×item with budget/actual/variance; Payroll/Depreciation rows present; category tie badge = Tied (detail+P+D == NC account debit); Exceptions section lists any unmapped lines and drills to their vouchers.
5. Cross-check one category's numbers against the Excel tool's output for the same month on the same data (the tool is the validated oracle) — they should match row-for-row (modulo the P/D rows the tool omits).
6. **待确认①**: verify `GET /internal/plan-lines?fiscal_year=2026&month=6` non-empty; if empty, budget column is 0 everywhere → import the Excel budget into budget-api first and report to user.
