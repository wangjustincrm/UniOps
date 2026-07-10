# JV Subsystem — Plan 2: Lifecycle (审核/过账/红冲) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the 制单→审核→过账 lifecycle to journal vouchers, plus 弃审 (un-review), 反过账 (un-post), and 红冲 (reversing voucher), with SoD (审核≠制单), fiscal-period gating, batch review/post, and REST endpoints.

**Architecture:** A new `finance-api/app/crud/journal_voucher.py` holds the state-transition functions (review/unreview/post/unpost/reverse + batch), reusing the existing `FiscalPeriod`/`OPEN` period gate and `SodRule` self-review rule. A new `finance-api/app/api/v1/journal_voucher.py` exposes list/get + action endpoints, registered in `api/v1/__init__.py`. Vouchers are created `draft` by Plan 1; only `posted` vouchers count toward the GL (Plan 3 will switch GL reads).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Postgres, pytest. Paths under `finance-api/`.

**Spec:** `docs/superpowers/specs/2026-07-07-finance-jv-subsystem-design.md` §4 (lifecycle). Plan 1 (data model + generation) is already merged on this branch: models `JournalVoucher`/`JournalVoucherLine`/`JvLineDimension` exist with `status`, `prepared_by/at`, `reviewed_by/at`, `posted_by/at`, `reverses_jv_id`, `reversed_by_jv_id`, dual-currency + quantity columns.

**Test command (every task):** `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <tests> -q` (Windows Git Bash; venv python; the DB-password env is REQUIRED). Tests take ~25-30s each (schema rebuilt per test).

---

## File Structure

- Create `finance-api/app/crud/journal_voucher.py` — state transitions: `review`, `unreview`, `post`, `unpost`, `reverse`, `review_batch`, `post_batch`, plus `get`, permission + gate helpers and the `JvStateError` / `JvPermissionError` exceptions.
- Create `finance-api/app/api/v1/journal_voucher.py` — router `/journal-vouchers`: list, get-detail, and action endpoints.
- Modify `finance-api/app/api/v1/__init__.py` — register the new router.
- Create `finance-api/tests/test_jv_lifecycle.py` — lifecycle crud tests.
- Create `finance-api/tests/test_jv_api.py` — endpoint tests.

Reference existing patterns: `app/crud/payment_execute.py` (`_check_period_open`, `_sod_enabled`, role checks), `app/models/fiscal_period.py` (`OPEN`, `FiscalPeriod`), `app/models/mirrors.py` (`SodRule`), `app/api/v1/ap_invoices.py` (router/endpoint style), `app/core/deps.py` (`CurrentUser`).

---

### Task 1: Review + un-review (draft ↔ reviewed) with SoD

**Files:**
- Create: `finance-api/app/crud/journal_voucher.py`
- Test: `finance-api/tests/test_jv_lifecycle.py`

- [ ] **Step 1: Write the failing test**

```python
"""JV lifecycle crud — Plan 2."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.crud import journal_voucher as jv_crud
from app.models.journal_voucher import JournalVoucher
from app.models.mirrors import SodRule
from app.services.posting import emit_event


def _user(sub=None, role="finance_manager"):
    return {"sub": str(sub or uuid.uuid4()), "role": role}


async def _draft_jv(db, prepared_by):
    """Create a draft JV via the Plan 1 generation path."""
    ev_id = await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        prepared_by=prepared_by,
        lines=[
            {"line_role": "purchase_expense", "account_code": "5000",
             "debit": Decimal("100.00"), "currency": "CAD"},
            {"line_role": "accounts_payable", "account_code": "2000",
             "credit": Decimal("100.00"), "currency": "CAD"},
        ],
    )
    return (await db.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()


async def test_review_moves_draft_to_reviewed(db_session):
    preparer = uuid.uuid4()
    jv = await _draft_jv(db_session, preparer)
    reviewer = _user()  # different person
    out = await jv_crud.review(db_session, jv.id, reviewer)
    assert out.status == "reviewed"
    assert str(out.reviewed_by) == reviewer["sub"]
    assert out.reviewed_at is not None


async def test_review_blocks_self_review_when_sod_enabled(db_session):
    preparer = uuid.uuid4()
    jv = await _draft_jv(db_session, preparer)
    db_session.add(SodRule(rule_code="jv_self_review", name="jv self review", enabled=True))
    await db_session.flush()
    with pytest.raises(jv_crud.JvPermissionError):
        await jv_crud.review(db_session, jv.id, _user(sub=preparer))  # same person


async def test_review_requires_finance_role(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    with pytest.raises(jv_crud.JvPermissionError):
        await jv_crud.review(db_session, jv.id, _user(role="requester"))


async def test_review_rejects_non_draft(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await jv_crud.review(db_session, jv.id, _user())
    with pytest.raises(jv_crud.JvStateError):
        await jv_crud.review(db_session, jv.id, _user())  # already reviewed


async def test_unreview_moves_reviewed_to_draft(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await jv_crud.review(db_session, jv.id, _user())
    out = await jv_crud.unreview(db_session, jv.id, _user())
    assert out.status == "draft"
    assert out.reviewed_by is None
    assert out.reviewed_at is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.crud.journal_voucher'`

- [ ] **Step 3: Implement review/unreview**

Create `finance-api/app/crud/journal_voucher.py`:

```python
"""Journal voucher lifecycle — 制单→审核→过账, 弃审, 反过账, 红冲.

Vouchers are created `draft` by services/journal_voucher.generate_from_event.
This module drives the human/controlled transitions. Only `posted` vouchers
reach the GL (Plan 3 switches GL reads to posted JV lines).
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fiscal_period import OPEN, FiscalPeriod
from app.models.journal_voucher import (
    DRAFT, POSTED, REVERSED, REVIEWED, JournalVoucher, JournalVoucherLine, JvLineDimension,
)
from app.models.mirrors import SodRule
from app.services.journal_voucher import next_jv_number

# Finance authority to review/post vouchers. role_management-assignment gating
# (finance_bp / finance_manager) can layer on later; JWT role is the base gate.
_JV_ROLES = {"finance_manager", "finance_bp", "system_admin"}


class JvStateError(ValueError):
    """Illegal state transition (wrong current status)."""


class JvPermissionError(Exception):
    """Caller lacks the role, or SoD forbids the action."""


def _require_role(user: dict) -> None:
    if user.get("role") not in _JV_ROLES:
        raise JvPermissionError("Insufficient role for journal-voucher action")


async def _sod_self_review_enabled(db: AsyncSession) -> bool:
    rule = (await db.execute(
        select(SodRule).where(SodRule.rule_code == "jv_self_review")
    )).scalar_one_or_none()
    return bool(rule and rule.enabled)


async def get(db: AsyncSession, jv_id: uuid.UUID) -> JournalVoucher | None:
    return (await db.execute(
        select(JournalVoucher).where(JournalVoucher.id == jv_id))).scalar_one_or_none()


async def _require(db: AsyncSession, jv_id: uuid.UUID, expect_status: str) -> JournalVoucher:
    jv = await get(db, jv_id)
    if jv is None:
        raise JvStateError("Journal voucher not found")
    if jv.status != expect_status:
        raise JvStateError(f"Voucher is '{jv.status}', expected '{expect_status}'")
    return jv


async def review(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    jv = await _require(db, jv_id, DRAFT)
    _require_role(user)
    if await _sod_self_review_enabled(db) and str(user["sub"]) == str(jv.prepared_by):
        raise JvPermissionError("SoD (jv_self_review): reviewer cannot be the preparer")
    jv.status = REVIEWED
    jv.reviewed_by = uuid.UUID(user["sub"])
    jv.reviewed_at = datetime.now(timezone.utc)
    await db.flush()
    return jv


async def unreview(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    jv = await _require(db, jv_id, REVIEWED)
    _require_role(user)
    jv.status = DRAFT
    jv.reviewed_by = None
    jv.reviewed_at = None
    await db.flush()
    return jv
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/journal_voucher.py finance-api/tests/test_jv_lifecycle.py
git commit -m "feat(finance): JV review/un-review with SoD"
```

---

### Task 2: Post + un-post (reviewed ↔ posted) with period gate

**Files:**
- Modify: `finance-api/app/crud/journal_voucher.py` (append)
- Test: `finance-api/tests/test_jv_lifecycle.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
async def _open_period(db, period="2026-07"):
    # A row with status != OPEN would block posting; absence of a row means open.
    # Insert an explicit OPEN row to be deterministic regardless of today's date.
    from app.models.fiscal_period import OPEN, FiscalPeriod
    existing = (await db.execute(select(FiscalPeriod).where(FiscalPeriod.period == period))).scalar_one_or_none()
    if existing is None:
        db.add(FiscalPeriod(period=period, status=OPEN))
        await db.flush()


async def test_post_moves_reviewed_to_posted(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await _open_period(db_session, jv.fiscal_period)
    await jv_crud.review(db_session, jv.id, _user())
    out = await jv_crud.post(db_session, jv.id, _user())
    assert out.status == "posted"
    assert out.posted_at is not None
    assert out.posted_by is not None


async def test_post_rejects_non_reviewed(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    with pytest.raises(jv_crud.JvStateError):
        await jv_crud.post(db_session, jv.id, _user())  # still draft


async def test_post_blocked_in_closed_period(db_session):
    from app.models.fiscal_period import FiscalPeriod
    jv = await _draft_jv(db_session, uuid.uuid4())
    await jv_crud.review(db_session, jv.id, _user())
    db_session.add(FiscalPeriod(period=jv.fiscal_period, status="hard_closed"))
    await db_session.flush()
    with pytest.raises(jv_crud.JvStateError):
        await jv_crud.post(db_session, jv.id, _user())


async def test_unpost_moves_posted_to_reviewed(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await _open_period(db_session, jv.fiscal_period)
    await jv_crud.review(db_session, jv.id, _user())
    await jv_crud.post(db_session, jv.id, _user())
    out = await jv_crud.unpost(db_session, jv.id, _user())
    assert out.status == "reviewed"
    assert out.posted_by is None and out.posted_at is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py -k "post" -q`
Expected: FAIL — `AttributeError: module 'app.crud.journal_voucher' has no attribute 'post'`

- [ ] **Step 3: Implement post/unpost (append to crud)**

```python
async def _require_period_open(db: AsyncSession, period: str) -> None:
    row = (await db.execute(
        select(FiscalPeriod).where(FiscalPeriod.period == period)
    )).scalar_one_or_none()
    if row is not None and row.status != OPEN:
        raise JvStateError(f"Fiscal period {period} is closed ({row.status})")


async def post(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    jv = await _require(db, jv_id, REVIEWED)
    _require_role(user)
    await _require_period_open(db, jv.fiscal_period)
    jv.status = POSTED
    jv.posted_by = uuid.UUID(user["sub"])
    jv.posted_at = datetime.now(timezone.utc)
    await db.flush()
    return jv


async def unpost(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    jv = await _require(db, jv_id, POSTED)
    _require_role(user)
    await _require_period_open(db, jv.fiscal_period)
    jv.status = REVIEWED
    jv.posted_by = None
    jv.posted_at = None
    await db.flush()
    return jv
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/journal_voucher.py finance-api/tests/test_jv_lifecycle.py
git commit -m "feat(finance): JV post/un-post with fiscal-period gate"
```

---

### Task 3: 红冲 (reverse a posted voucher)

**Files:**
- Modify: `finance-api/app/crud/journal_voucher.py` (append)
- Test: `finance-api/tests/test_jv_lifecycle.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
async def test_reverse_creates_red_voucher_and_marks_original(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    await _open_period(db_session, jv.fiscal_period)
    await jv_crud.review(db_session, jv.id, _user())
    await jv_crud.post(db_session, jv.id, _user())

    actor = _user()
    red = await jv_crud.reverse(db_session, jv.id, actor)

    # red voucher: posted, links to original, negated amounts
    assert red.id != jv.id
    assert red.status == "posted"
    assert red.reverses_jv_id == jv.id
    assert red.total_debit == Decimal("-100.00")
    assert red.total_credit == Decimal("-100.00")
    red_lines = (await db_session.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == red.id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()
    assert len(red_lines) == 2
    assert red_lines[0].orig_debit == Decimal("-100.00")
    assert red_lines[0].local_debit == Decimal("-100.00")
    assert red_lines[0].account_code == "5000"

    # original marked reversed + back-link
    original = await jv_crud.get(db_session, jv.id)
    assert original.status == "reversed"
    assert original.reversed_by_jv_id == red.id


async def test_reverse_rejects_non_posted(db_session):
    jv = await _draft_jv(db_session, uuid.uuid4())
    with pytest.raises(jv_crud.JvStateError):
        await jv_crud.reverse(db_session, jv.id, _user())  # draft, not posted
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py -k reverse -q`
Expected: FAIL — `AttributeError: ... has no attribute 'reverse'`

- [ ] **Step 3: Implement reverse (append to crud)**

```python
def _neg(v: Decimal | None) -> Decimal | None:
    return None if v is None else -v


async def reverse(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    """红冲: create a posted red (negated) voucher that offsets the original, and
    mark the original `reversed`. Both stay for audit. Period must be open."""
    jv = await _require(db, jv_id, POSTED)
    _require_role(user)
    await _require_period_open(db, jv.fiscal_period)

    now = datetime.now(timezone.utc)
    red = JournalVoucher(
        jv_number=await next_jv_number(db, jv.fiscal_period, jv.voucher_word),
        voucher_word=jv.voucher_word,
        voucher_date=jv.voucher_date,
        fiscal_period=jv.fiscal_period,
        summary=f"红冲: {jv.summary or jv.jv_number}",
        status=POSTED,
        source_service=jv.source_service, source_doc_type=jv.source_doc_type,
        source_doc_id=jv.source_doc_id, source_doc_number=jv.source_doc_number,
        prepared_by=uuid.UUID(user["sub"]), prepared_at=now,
        reviewed_by=uuid.UUID(user["sub"]), reviewed_at=now,
        posted_by=uuid.UUID(user["sub"]), posted_at=now,
        reverses_jv_id=jv.id,
        total_debit=_neg(jv.total_debit), total_credit=_neg(jv.total_credit),
        total_local_debit=_neg(jv.total_local_debit),
        total_local_credit=_neg(jv.total_local_credit),
        entity_id=jv.entity_id,
    )
    db.add(red)
    await db.flush()

    src_lines = (await db.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv.id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()
    line_map: list[tuple[JournalVoucherLine, uuid.UUID]] = []
    for sl in src_lines:
        rl = JournalVoucherLine(
            jv_id=red.id, line_no=sl.line_no, account_code=sl.account_code,
            summary=sl.summary,
            orig_debit=_neg(sl.orig_debit), orig_credit=_neg(sl.orig_credit),
            local_debit=_neg(sl.local_debit), local_credit=_neg(sl.local_credit),
            currency=sl.currency, fx_rate=sl.fx_rate,
            quantity=_neg(sl.quantity), unit=sl.unit, price=sl.price,
            cost_center_id=sl.cost_center_id, department_id=sl.department_id,
            partner_id=sl.partner_id, partner_name=sl.partner_name,
            tax_code=sl.tax_code, project_id=sl.project_id, item_id=sl.item_id,
        )
        db.add(rl)
        line_map.append((rl, sl.id))
    await db.flush()

    for rl, src_id in line_map:
        dims = (await db.execute(
            select(JvLineDimension).where(JvLineDimension.jv_line_id == src_id)
        )).scalars().all()
        for d in dims:
            db.add(JvLineDimension(jv_line_id=rl.id, dim_code=d.dim_code,
                                   value_id=d.value_id, value_text=d.value_text))

    jv.status = REVERSED
    jv.reversed_by_jv_id = red.id
    await db.flush()
    return red
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/journal_voucher.py finance-api/tests/test_jv_lifecycle.py
git commit -m "feat(finance): JV 红冲 reversing voucher"
```

---

### Task 4: Batch review + batch post

**Files:**
- Modify: `finance-api/app/crud/journal_voucher.py` (append)
- Test: `finance-api/tests/test_jv_lifecycle.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
async def test_review_batch_reports_per_id(db_session):
    good = await _draft_jv(db_session, uuid.uuid4())
    already = await _draft_jv(db_session, uuid.uuid4())
    await jv_crud.review(db_session, already.id, _user())  # already reviewed → error in batch
    missing = uuid.uuid4()

    res = await jv_crud.review_batch(db_session, [good.id, already.id, missing], _user())
    by_id = {r["id"]: r for r in res}
    assert by_id[str(good.id)]["ok"] is True
    assert by_id[str(already.id)]["ok"] is False
    assert by_id[str(missing)]["ok"] is False
    assert (await jv_crud.get(db_session, good.id)).status == "reviewed"


async def test_post_batch_posts_reviewed_only(db_session):
    a = await _draft_jv(db_session, uuid.uuid4())
    b = await _draft_jv(db_session, uuid.uuid4())
    await _open_period(db_session, a.fiscal_period)
    await jv_crud.review(db_session, a.id, _user())
    # b left as draft → should fail in batch
    res = await jv_crud.post_batch(db_session, [a.id, b.id], _user())
    by_id = {r["id"]: r for r in res}
    assert by_id[str(a.id)]["ok"] is True
    assert by_id[str(b.id)]["ok"] is False
    assert (await jv_crud.get(db_session, a.id)).status == "posted"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py -k batch -q`
Expected: FAIL — `AttributeError: ... has no attribute 'review_batch'`

- [ ] **Step 3: Implement batch helpers (append to crud)**

```python
async def _batch(db, ids, user, fn) -> list[dict]:
    """Apply a single-voucher transition to each id, collecting per-id outcome.
    Each failure is caught and reported; successes stay in the shared txn."""
    out: list[dict] = []
    for jid in ids:
        try:
            await fn(db, jid, user)
            out.append({"id": str(jid), "ok": True, "error": None})
        except (JvStateError, JvPermissionError) as e:
            out.append({"id": str(jid), "ok": False, "error": str(e)})
    return out


async def review_batch(db: AsyncSession, ids: list[uuid.UUID], user: dict) -> list[dict]:
    return await _batch(db, ids, user, review)


async def post_batch(db: AsyncSession, ids: list[uuid.UUID], user: dict) -> list[dict]:
    return await _batch(db, ids, user, post)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_lifecycle.py -q`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/journal_voucher.py finance-api/tests/test_jv_lifecycle.py
git commit -m "feat(finance): JV batch review/post"
```

---

### Task 5: REST endpoints + router registration

**Files:**
- Create: `finance-api/app/api/v1/journal_voucher.py`
- Modify: `finance-api/app/api/v1/__init__.py`
- Test: `finance-api/tests/test_jv_api.py`

- [ ] **Step 1: Write the failing test**

```python
"""JV lifecycle endpoints — Plan 2."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.journal_voucher import JournalVoucher
from app.services.posting import emit_event


def _token(role="finance_manager", sub=None):
    return jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="finance_manager", sub=None):
    return {"Authorization": f"Bearer {_token(role, sub)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _draft_jv(db):
    ev_id = await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": "5000",
                "debit": Decimal("100.00"), "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "2000",
                "credit": Decimal("100.00"), "currency": "CAD"}])
    return (await db.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()


async def test_list_and_get_detail(client, db_session):
    jv = await _draft_jv(db_session)
    r = await client.get("/finance/v1/journal-vouchers", headers=_h())
    assert r.status_code == 200
    assert any(row["id"] == str(jv.id) for row in r.json())

    r2 = await client.get(f"/finance/v1/journal-vouchers/{jv.id}", headers=_h())
    assert r2.status_code == 200
    body = r2.json()
    assert body["voucher"]["jv_number"].startswith("JV-")
    assert len(body["lines"]) == 2


async def test_review_then_post_endpoints(client, db_session):
    jv = await _draft_jv(db_session)
    r = await client.post(f"/finance/v1/journal-vouchers/{jv.id}/review", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reviewed"
    r2 = await client.post(f"/finance/v1/journal-vouchers/{jv.id}/post", headers=_h())
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "posted"


async def test_review_endpoint_forbidden_for_non_finance(client, db_session):
    jv = await _draft_jv(db_session)
    r = await client.post(f"/finance/v1/journal-vouchers/{jv.id}/review",
                          headers=_h(role="requester"))
    assert r.status_code == 403


async def test_post_batch_endpoint(client, db_session):
    jv = await _draft_jv(db_session)
    await client.post(f"/finance/v1/journal-vouchers/{jv.id}/review", headers=_h())
    r = await client.post("/finance/v1/journal-vouchers/post-batch",
                          json={"ids": [str(jv.id)]}, headers=_h())
    assert r.status_code == 200
    assert r.json()[0]["ok"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_api.py -q`
Expected: FAIL — 404s (router not registered) / import error.

- [ ] **Step 3: Implement the router**

Create `finance-api/app/api/v1/journal_voucher.py`:

```python
"""Journal voucher API — list/detail + lifecycle actions (Plan 2)."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud import journal_voucher as crud
from app.db.base import get_db
from app.models.journal_voucher import JournalVoucher, JournalVoucherLine

router = APIRouter(prefix="/journal-vouchers", tags=["journal-vouchers"])


class IdsIn(BaseModel):
    ids: list[uuid.UUID]


def _hdr(jv: JournalVoucher) -> dict:
    return {
        "id": str(jv.id), "jv_number": jv.jv_number, "voucher_word": jv.voucher_word,
        "voucher_date": jv.voucher_date.isoformat(), "fiscal_period": jv.fiscal_period,
        "summary": jv.summary, "status": jv.status,
        "source_doc_type": jv.source_doc_type,
        "source_doc_id": str(jv.source_doc_id) if jv.source_doc_id else None,
        "source_doc_number": jv.source_doc_number,
        "total_debit": str(jv.total_debit), "total_credit": str(jv.total_credit),
        "total_local_debit": str(jv.total_local_debit),
        "total_local_credit": str(jv.total_local_credit),
        "reverses_jv_id": str(jv.reverses_jv_id) if jv.reverses_jv_id else None,
        "reversed_by_jv_id": str(jv.reversed_by_jv_id) if jv.reversed_by_jv_id else None,
    }


@router.get("")
async def list_vouchers(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        period: str | None = Query(default=None),
                        status: str | None = Query(default=None),
                        source_doc_type: str | None = Query(default=None),
                        limit: int = Query(default=200, le=1000)):
    q = select(JournalVoucher).order_by(JournalVoucher.voucher_date.desc()).limit(limit)
    if period:
        q = q.where(JournalVoucher.fiscal_period == period)
    if status:
        q = q.where(JournalVoucher.status == status)
    if source_doc_type:
        q = q.where(JournalVoucher.source_doc_type == source_doc_type)
    rows = (await db.execute(q)).scalars().all()
    return [_hdr(jv) for jv in rows]


@router.get("/{jv_id}")
async def get_voucher(jv_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    jv = await crud.get(db, jv_id)
    if jv is None:
        raise HTTPException(status_code=404, detail="Journal voucher not found")
    lines = (await db.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv_id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()
    return {"voucher": _hdr(jv), "lines": [
        {"line_no": ln.line_no, "account_code": ln.account_code, "summary": ln.summary,
         "orig_debit": str(ln.orig_debit), "orig_credit": str(ln.orig_credit),
         "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
         "currency": ln.currency, "fx_rate": str(ln.fx_rate),
         "partner_name": ln.partner_name, "tax_code": ln.tax_code}
        for ln in lines]}


def _err(e: Exception):
    from app.crud.journal_voucher import JvPermissionError, JvStateError
    if isinstance(e, JvPermissionError):
        return HTTPException(status_code=403, detail=str(e))
    if isinstance(e, JvStateError):
        return HTTPException(status_code=409, detail=str(e))
    raise e


async def _act(db, jv_id, user, fn):
    from app.crud.journal_voucher import JvPermissionError, JvStateError
    try:
        jv = await fn(db, jv_id, user)
        await db.commit()
        return _hdr(jv)
    except (JvPermissionError, JvStateError) as e:
        await db.rollback()
        raise _err(e)


@router.post("/{jv_id}/review")
async def review(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.review)


@router.post("/{jv_id}/unreview")
async def unreview(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.unreview)


@router.post("/{jv_id}/post")
async def post(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.post)


@router.post("/{jv_id}/unpost")
async def unpost(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.unpost)


@router.post("/{jv_id}/reverse")
async def reverse(jv_id: uuid.UUID, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    return await _act(db, jv_id, user, crud.reverse)


@router.post("/review-batch")
async def review_batch(body: IdsIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    res = await crud.review_batch(db, body.ids, user)
    await db.commit()
    return res


@router.post("/post-batch")
async def post_batch(body: IdsIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    res = await crud.post_batch(db, body.ids, user)
    await db.commit()
    return res
```

- [ ] **Step 4: Register the router**

In `finance-api/app/api/v1/__init__.py`, add after the `gl_router` import line:
```python
from app.api.v1.journal_voucher import router as journal_voucher_router
```
and after `api_router.include_router(gl_router)`:
```python
api_router.include_router(journal_voucher_router)
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_api.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Run the JV suite together (regression)**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_journal_voucher.py tests/test_jv_lifecycle.py tests/test_jv_api.py -q`
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add finance-api/app/api/v1/journal_voucher.py finance-api/app/api/v1/__init__.py finance-api/tests/test_jv_api.py
git commit -m "feat(finance): JV lifecycle REST endpoints"
```

---

## Self-Review

**Spec coverage (§4 lifecycle):**
- draft→reviewed (审核) + SoD 审核≠制单 — Task 1. ✓
- reviewed→draft (弃审) — Task 1. ✓
- reviewed→posted (过账) + period gate — Task 2. ✓
- posted→reviewed (反过账) — Task 2. ✓
- 红冲 reversing voucher + mark original reversed — Task 3. ✓
- Batch review/post — Task 4. ✓
- Roles (finance_manager/finance_bp/system_admin base gate; role_management assignment deferred, noted) — Tasks 1-2. ✓
- Endpoints — Task 5. ✓

**Not in this plan:** system-auto-post for import/opening (those set `posted` directly, not via this human flow); GL read switch to posted JV (Plan 3); UI (Plan 4). `_JV_ROLES` is the base gate; layering role_management-assignment gating (like `payment_execute._check_can_pay`) is a later refinement.

**Placeholder scan:** none — all steps have complete code/commands.

**Type consistency:** `review/unreview/post/unpost/reverse(db, jv_id, user) -> JournalVoucher`; `review_batch/post_batch(db, ids, user) -> list[dict]` with `{id, ok, error}`; `JvStateError`/`JvPermissionError` raised in crud, mapped to 409/403 in the API `_err`. `_hdr()` shape reused by list/detail/actions. Field names (`reviewed_by`, `posted_by`, `reverses_jv_id`, `total_local_debit`) match the Plan-1 model. `FiscalPeriod.status`/`OPEN` and `SodRule.rule_code`/`enabled` match existing models.
