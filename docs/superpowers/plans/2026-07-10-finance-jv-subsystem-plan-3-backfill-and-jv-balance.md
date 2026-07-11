# JV Subsystem — Plan 3 (additive): Backfill + JV-based Account Balance

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
>
> **⚠️ SURGICAL GIT:** the working tree has unrelated uncommitted WIP (invoice-tax: `epms-api/`, `expense-api/`, `epms/`, `portal/`). Every commit must `git add` ONLY the exact files named — never `git add -A`/`.`/`git commit -a`. Never `git stash`/`reset`/`checkout`.

**Goal:** (1) Backfill: give every existing `posting_event` a **posted** journal voucher (so JV data mirrors current GL). (2) Add a **new** JV-based account-balance aggregation (reads posted JV lines, local CAD) that the account-balance report will consume — **without touching the live `gl.py`** (which keeps reading `posting_lines` until the voucher-center UI + workflow are adopted).

**Architecture:** Backfill lives in `crud/journal_voucher.py` (`backfill_posted_jvs`): for each posting_event, ensure a JV exists (via Plan-1 `generate_from_event`) and set it `posted` directly (bypassing the period gate — this is a data migration, not the human post flow). A new `crud/account_balance.py` computes opening/period/closing per account from `journal_vouchers(status='posted')` + `journal_voucher_lines.local_debit/credit`, mirroring `gl.trial_balance`'s shape. A new `api/v1/account_balance.py` exposes `GET /gl/account-balance`. The existing `gl.py`/`gl` router are left unchanged.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Postgres, pytest. Paths under `finance-api/`.

**Spec:** `docs/superpowers/specs/2026-07-07-finance-account-balance-report-design.md` §3 (能力① 科目余额表) + §2 (data source = posted JV lines). Aux expansion (②), voucher drill-down (③), Budget Actual (④) are a later plan. Plan 1+2 are done on this branch: `generate_from_event`, JV models with `local_debit/credit`, lifecycle crud.

**Test command (every task):** `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <tests> -q` (Windows Git Bash; venv python; DB-password env REQUIRED). ~25-30s per test.

---

## File Structure

- Modify (APPEND) `finance-api/app/crud/journal_voucher.py` — add `backfill_posted_jvs`.
- Create `finance-api/app/crud/account_balance.py` — `account_balance` aggregation over posted JV lines.
- Create `finance-api/app/api/v1/account_balance.py` — `GET /gl/account-balance` router.
- Modify `finance-api/app/api/v1/__init__.py` — register the new router (1 import + 1 include line).
- Create `finance-api/tests/test_jv_backfill.py` — backfill tests.
- Create `finance-api/tests/test_account_balance.py` — aggregation + endpoint tests.

Reference: `app/crud/gl.py` (`trial_balance`, `_coa_map`, `_s`), `app/models/coa.py` (`ChartOfAccount`: `.code`, `.name`, `.account_type`), `app/services/journal_voucher.py` (`generate_from_event`).

---

### Task 1: Backfill — every posting_event gets a posted JV

**Files:**
- Modify (APPEND): `finance-api/app/crud/journal_voucher.py`
- Test: `finance-api/tests/test_jv_backfill.py`

- [ ] **Step 1: Write the failing test** — create `finance-api/tests/test_jv_backfill.py`:

```python
"""JV backfill — Plan 3 Task 1."""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.crud import journal_voucher as jv_crud
from app.models.journal_voucher import JournalVoucher
from app.services.posting import emit_event


async def _event(db, prepared=True):
    return await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        prepared_by=uuid.uuid4() if prepared else None,
        lines=[{"line_role": "purchase_expense", "account_code": "5000",
                "debit": Decimal("100.00"), "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "2000",
                "credit": Decimal("100.00"), "currency": "CAD"}])


async def test_backfill_posts_existing_draft_jvs(db_session):
    await _event(db_session)
    await _event(db_session)
    res = await jv_crud.backfill_posted_jvs(db_session)
    assert res["posted"] == 2
    posted = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.status == "posted"))).scalars().all()
    assert len(posted) == 2


async def test_backfill_generates_jv_for_event_without_one(db_session):
    ev_id = await _event(db_session)
    # simulate a pre-Plan-1 event: delete its auto-generated JV
    await db_session.execute(text("delete from journal_vouchers where posting_event_id = :e"),
                             {"e": str(ev_id)})
    await db_session.flush()
    res = await jv_crud.backfill_posted_jvs(db_session)
    assert res["generated"] == 1
    assert res["posted"] == 1
    jv = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()
    assert jv.status == "posted"


async def test_backfill_is_idempotent(db_session):
    await _event(db_session)
    await jv_crud.backfill_posted_jvs(db_session)
    res2 = await jv_crud.backfill_posted_jvs(db_session)
    assert res2["generated"] == 0 and res2["posted"] == 0
    posted = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.status == "posted"))).scalars().all()
    assert len(posted) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_backfill.py -q`
Expected: FAIL — `AttributeError: module 'app.crud.journal_voucher' has no attribute 'backfill_posted_jvs'`

- [ ] **Step 3: Append to `finance-api/app/crud/journal_voucher.py`:**

```python
async def backfill_posted_jvs(db: AsyncSession) -> dict:
    """One-time migration: ensure every posting_event has a JV (generate via the
    Plan-1 path if missing) and mark it `posted` directly — bypassing the human
    post flow + period gate, because this reflects already-live GL data. Idempotent
    (skips events whose JV is already posted/reversed). Returns counts."""
    from app.models.posting import PostingEvent
    from app.services.journal_voucher import generate_from_event

    event_ids = (await db.execute(select(PostingEvent.id))).scalars().all()
    generated = posted = 0
    now = datetime.now(timezone.utc)
    for eid in event_ids:
        jv = (await db.execute(
            select(JournalVoucher).where(JournalVoucher.posting_event_id == eid)
        )).scalar_one_or_none()
        if jv is None:
            jv = await generate_from_event(db, eid, None)
            generated += 1
        if jv is not None and jv.status == DRAFT:
            jv.status = POSTED
            jv.posted_at = now
            posted += 1
    await db.flush()
    return {"generated": generated, "posted": posted}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_backfill.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit (surgical — ONLY these two files)**

```bash
git add finance-api/app/crud/journal_voucher.py finance-api/tests/test_jv_backfill.py
git commit -m "feat(finance): backfill existing posting events into posted JVs"
```

---

### Task 2: JV-based account-balance aggregation

**Files:**
- Create: `finance-api/app/crud/account_balance.py`
- Test: `finance-api/tests/test_account_balance.py`

- [ ] **Step 1: Write the failing test** — create `finance-api/tests/test_account_balance.py`:

```python
"""JV-based account balance — Plan 3 Task 2."""
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.crud import account_balance as ab
from app.crud import journal_voucher as jv_crud
from app.services.posting import emit_event


async def _posted_event(db, period_month, debit_acct="5000", amount="100.00"):
    """Emit an accrual then backfill-post it so it counts toward the JV balance.
    period_month like '2026-06' controls the JV fiscal_period via occurred_at."""
    from datetime import datetime, timezone
    occurred = datetime(int(period_month[:4]), int(period_month[5:7]), 15, tzinfo=timezone.utc)
    await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        occurred_at=occurred, prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": debit_acct,
                "debit": Decimal(amount), "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "2000",
                "credit": Decimal(amount), "currency": "CAD"}])


async def test_account_balance_opening_movement_closing(db_session):
    await _posted_event(db_session, "2026-06", amount="100.00")   # prior period
    await _posted_event(db_session, "2026-07", amount="40.00")    # current period
    await jv_crud.backfill_posted_jvs(db_session)                 # post them all

    bal = await ab.account_balance(db_session, "2026-07")
    by_code = {r["account_code"]: r for r in bal["rows"]}
    # 5000 expense: opening 100 (debit), movement +40 debit, closing 140
    assert by_code["5000"]["opening"] == "100.00"
    assert by_code["5000"]["period_debit"] == "40.00"
    assert by_code["5000"]["closing"] == "140.00"
    # 2000 AP: opening -100 (credit), movement -40, closing -140
    assert by_code["2000"]["closing"] == "-140.00"
    assert bal["balanced"] is True


async def test_account_balance_excludes_draft(db_session):
    await _posted_event(db_session, "2026-07")   # left as draft (no backfill)
    bal = await ab.account_balance(db_session, "2026-07")
    assert bal["rows"] == []                     # draft JVs don't count
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.crud.account_balance'`

- [ ] **Step 3: Create `finance-api/app/crud/account_balance.py`:**

```python
"""Account balance (科目余额表 能力①) over POSTED journal vouchers.

Additive GL read that aggregates `journal_voucher_lines.local_debit/credit`
(functional currency, CAD) by account + period into opening / period-movement /
closing. Only `posted` vouchers count. Does NOT touch the live gl.py (which still
reads posting_lines) — this is the foundation the account-balance report consumes.
"""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coa import ChartOfAccount
from app.models.journal_voucher import POSTED, JournalVoucher, JournalVoucherLine

_ZERO = Decimal("0")


def _s(v: Decimal) -> str:
    return str(Decimal(v).quantize(Decimal("0.01")))


async def _coa_map(db: AsyncSession) -> dict:
    rows = (await db.execute(select(ChartOfAccount))).scalars().all()
    return {a.code: a for a in rows}


async def account_balance(db: AsyncSession, period: str) -> dict:
    """Opening (cumulative posted before `period`) + period movement + closing,
    per account, in local (CAD) amounts. Mirrors gl.trial_balance's shape but
    sourced from posted JV lines."""
    coa = await _coa_map(db)

    async def sums(where):
        q = (select(JournalVoucherLine.account_code,
                    func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                    func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .where(JournalVoucher.status == POSTED).where(where)
             .group_by(JournalVoucherLine.account_code))
        return {code: (Decimal(d), Decimal(c)) for code, d, c in (await db.execute(q)).all()}

    opening = await sums(JournalVoucher.fiscal_period < period)
    movement = await sums(JournalVoucher.fiscal_period == period)

    codes = sorted(set(opening) | set(movement), key=lambda c: (c is None, c or ""))
    rows = []
    tot_dr = tot_cr = tot_close = _ZERO
    for code in codes:
        od, oc = opening.get(code, (_ZERO, _ZERO))
        md, mc = movement.get(code, (_ZERO, _ZERO))
        open_bal = od - oc
        close_bal = open_bal + md - mc
        acct = coa.get(code)
        rows.append({
            "account_code": code or "(unmapped)",
            "account_name": acct.name if acct else "(unmapped)",
            "account_type": acct.account_type if acct else None,
            "opening": _s(open_bal),
            "period_debit": _s(md), "period_credit": _s(mc),
            "closing": _s(close_bal),
        })
        tot_dr += md; tot_cr += mc; tot_close += close_bal
    return {
        "period": period, "rows": rows,
        "totals": {"period_debit": _s(tot_dr), "period_credit": _s(tot_cr),
                   "closing": _s(tot_close)},
        "balanced": tot_dr == tot_cr and tot_close == _ZERO,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit (surgical — ONLY these two files)**

```bash
git add finance-api/app/crud/account_balance.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): JV-based account balance aggregation"
```

---

### Task 3: `GET /gl/account-balance` endpoint

**Files:**
- Create: `finance-api/app/api/v1/account_balance.py`
- Modify: `finance-api/app/api/v1/__init__.py`
- Test (APPEND): `finance-api/tests/test_account_balance.py`

- [ ] **Step 1: Append the endpoint test to `finance-api/tests/test_account_balance.py`:**

```python
from datetime import datetime, timezone, timedelta
from httpx import ASGITransport, AsyncClient
from jose import jwt
from app.core.config import settings
from app.db.base import get_db
from app.main import app


def _h():
    tok = jwt.encode({"sub": str(uuid.uuid4()), "role": "finance_manager",
                      "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                     settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override():
        yield db_session
    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_account_balance_endpoint(client, db_session):
    await _posted_event(db_session, "2026-07", amount="55.00")
    await jv_crud.backfill_posted_jvs(db_session)
    r = await client.get("/finance/v1/gl/account-balance?period=2026-07", headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    by_code = {row["account_code"]: row for row in body["rows"]}
    assert by_code["5000"]["closing"] == "55.00"
    assert body["balanced"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -k endpoint -q`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Create `finance-api/app/api/v1/account_balance.py`:**

```python
"""Account balance report API (科目余额表 能力①) — reads posted JV lines."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud import account_balance as crud
from app.db.base import get_db

router = APIRouter(prefix="/gl", tags=["account-balance"])


@router.get("/account-balance")
async def account_balance(_: CurrentUser, db: AsyncSession = Depends(get_db),
                          period: str = Query(...)):
    return await crud.account_balance(db, period)
```

- [ ] **Step 4: Register in `finance-api/app/api/v1/__init__.py`**

Add this import right after the existing `from app.api.v1.journal_voucher import router as journal_voucher_router` line:
```python
from app.api.v1.account_balance import router as account_balance_router
```
And this include right after `api_router.include_router(journal_voucher_router)`:
```python
api_router.include_router(account_balance_router)
```
Change nothing else. (Two routers sharing the `/gl` prefix is fine in FastAPI — the existing `gl_router` keeps its routes; this adds `/gl/account-balance`.)

- [ ] **Step 5: Run to verify it passes**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Regression — JV suite + this + existing gl unaffected**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_journal_voucher.py tests/test_jv_lifecycle.py tests/test_jv_api.py tests/test_jv_backfill.py tests/test_account_balance.py -q`
Expected: ALL PASS (8 + 13 + 4 + 3 + 3 = 31 passed).

- [ ] **Step 7: Commit (surgical — ONLY these three files)**

```bash
git add finance-api/app/api/v1/account_balance.py finance-api/app/api/v1/__init__.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): GET /gl/account-balance endpoint"
```

---

## Self-Review

**Spec coverage (report §3 能力① + §2 source + JV spec §6 backfill):**
- Backfill existing posting_events → posted JVs (zero-diff foundation) — Task 1. ✓
- Account balance = opening/period/closing per account from **posted JV lines, local CAD** — Task 2. ✓
- Draft JVs excluded from balance — Task 2 test. ✓
- Endpoint — Task 3. ✓
- Live `gl.py` untouched (additive) — new modules only. ✓

**Not in this plan (by design):** aux-accounting expansion (②), voucher drill-down (③), Budget Actual view (④) — next report plan; the actual gl.py cutover to posted JVs — deferred until the voucher-center UI (Plan 4) + workflow adoption.

**Placeholder scan:** none — every step has complete code/commands.

**Type consistency:** `backfill_posted_jvs(db) -> {"generated": int, "posted": int}`; `account_balance(db, period) -> {"period", "rows":[{account_code, account_name, account_type, opening, period_debit, period_credit, closing}], "totals", "balanced"}` reused by crud + endpoint + tests. Field names (`local_debit`, `local_credit`, `status`, `fiscal_period`, `posting_event_id`) match Plan-1 models; `POSTED`/`DRAFT` constants from `app.models.journal_voucher`. `ChartOfAccount.code/.name/.account_type` match `gl.py` usage.
