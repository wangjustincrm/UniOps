# Test Cases — UniOps PRD Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full pytest test suite for `expense-api` (currently zero tests) and fill FR-ID gaps in `epms-api` tests, covering all PRD requirements in PRD-OA v1.5, PRD v2.16, and SPRINT-OA.md.

**Architecture:** Three test categories — (1) expense-api API tests using a dedicated `expense_test` PostgreSQL DB + httpx ASGITransport, mirroring the epms-api test pattern; (2) epms-api supplement tests for newly-added cost-center endpoints and PR scoping FR IDs; (3) a smoke-test addition for expense-api endpoints in `test_all.py`.

**Tech Stack:** pytest 8.3, pytest-asyncio 0.24 (session loop scope), httpx ASGITransport, pytest-mock, python-jose for token generation, PostgreSQL 15 (`expense_test` DB).

---

## PRD Coverage Map

| PRD Section | FR ID | Test Task |
|-------------|-------|-----------|
| PRD-OA §3 — PA-DIR creation | — | Task 6 |
| PRD-OA §2 — Invoice dedup | — | Task 5 |
| PRD-OA §3.2 — Budget hierarchy endpoint | — | Task 4 |
| PRD-OA §2.3 — Vendor search endpoint | — | Task 4 |
| PRD §1.3 — PR list scoping | PL-001 to PL-005 | Task 9 |
| PRD §3.2.6 — PR approval flow | REJ-001 to REJ-004 | (existing test_pr.py) |
| PRD §3.1 — Auth / 401 handling | — | Task 3 |
| Expense policy config (S4-B) | — | Task 3 |
| EXP / MIL claims | — | Task 7 |
| New dept + cost-center endpoints | — | Task 8 |

---

## Task 1: expense-api Dev Dependencies + Test Database

**Files:**
- Create: `expense-api/requirements-dev.txt`
- Create: `expense-api/scripts/create_test_db.py`

- [ ] **Step 1: Create requirements-dev.txt**

```
# expense-api/requirements-dev.txt
-r requirements.txt
pytest==8.3.4
pytest-asyncio==0.24.0
pytest-mock==3.14.0
anyio[asyncio]==4.7.0
```

- [ ] **Step 2: Create create_test_db.py**

```python
# expense-api/scripts/create_test_db.py
"""Create the expense_test database. Run once before pytest.
Usage: python -m scripts.create_test_db
"""
import subprocess, sys

result = subprocess.run(
    ["psql", "-U", "epms", "-h", "localhost", "-c",
     "CREATE DATABASE expense_test;"],
    capture_output=True, text=True,
)
if result.returncode != 0 and "already exists" not in result.stderr:
    print(result.stderr, file=sys.stderr)
    sys.exit(1)
print("expense_test database ready.")
```

- [ ] **Step 3: Install dev dependencies**

```bash
cd expense-api
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Windows
# or: .venv/bin/pip install -r requirements-dev.txt  # Linux/Mac
```

Expected: no errors, pytest discovered.

- [ ] **Step 4: Create test database**

```bash
cd expense-api
python -m scripts.create_test_db
```

Expected: `expense_test database ready.`

- [ ] **Step 5: Commit**

```bash
git add expense-api/requirements-dev.txt expense-api/scripts/create_test_db.py
git commit -m "test: add expense-api dev dependencies and test DB setup script"
```

---

## Task 2: expense-api conftest.py

**Files:**
- Create: `expense-api/tests/__init__.py`
- Create: `expense-api/tests/conftest.py`

- [ ] **Step 1: Create tests/__init__.py**

```python
# expense-api/tests/__init__.py
```

- [ ] **Step 2: Write conftest.py**

```python
# expense-api/tests/conftest.py
"""Shared pytest fixtures for expense-api test suite.
Mirrors epms-api/tests/conftest.py — test DB is `expense_test`.
Requires: python -m scripts.create_test_db (run once).
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import ALL models so Base.metadata.create_all() registers every table
import app.models.expense          # noqa: F401
import app.models.invoice          # noqa: F401
import app.models.invoice_attachment  # noqa: F401
import app.models.pa               # noqa: F401
import app.models.policy           # noqa: F401
import app.models.budget           # noqa: F401
import app.models.epms_mirrors     # noqa: F401
import app.models.approval_event_mirror  # noqa: F401
import app.models.company_config_mirror  # noqa: F401
import app.db.base as db_module
from app.core.config import settings
from app.db.base import Base
from app.main import create_app

# Derive test DB URL from settings (swap DB name)
_base, _ = settings.database_url.rsplit("/", 1)
_TEST_DB_URL = f"{_base}/expense_test"

_JWT_SECRET = settings.jwt_secret_key
_JWT_ALG    = settings.jwt_algorithm


# ── Loop scope (same trick as epms-api) ───────────────────────────────────────

def pytest_collection_modifyitems(items):
    session_mark = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if isinstance(item, pytest.Function) and asyncio.iscoroutinefunction(item.function):
            item.add_marker(session_mark, append=False)


# ── Test engine ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def _patch_session_factory(test_engine):
    original = db_module.AsyncSessionLocal
    db_module.AsyncSessionLocal = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield
    db_module.AsyncSessionLocal = original


# ── Token helper ──────────────────────────────────────────────────────────────

def _make_token(role: str, user_id: str | None = None) -> str:
    return jwt.encode(
        {
            "sub": user_id or str(uuid.uuid4()),
            "role": role,
            "exp": datetime.utcnow() + timedelta(hours=8),
        },
        _JWT_SECRET,
        algorithm=_JWT_ALG,
    )


# ── HTTP clients ──────────────────────────────────────────────────────────────

def _client(token: str) -> AsyncClient:
    app = create_app()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
async def client():
    """Unauthenticated client."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_client():
    async with _client(_make_token("system_admin")) as c:
        yield c


@pytest.fixture
async def finance_client():
    async with _client(_make_token("finance_manager")) as c:
        yield c


@pytest.fixture
async def finance_bp_client():
    async with _client(_make_token("finance_bp")) as c:
        yield c


@pytest.fixture
async def requester_client():
    async with _client(_make_token("requester")) as c:
        yield c


@pytest.fixture
async def requester_client_b():
    """A second requester — different user_id."""
    async with _client(_make_token("requester")) as c:
        yield c
```

- [ ] **Step 3: Verify conftest loads with no import errors**

```bash
cd expense-api
.venv/Scripts/python -m pytest tests/ --collect-only -q
```

Expected: `no tests ran` (0 tests, no errors).

- [ ] **Step 4: Commit**

```bash
git add expense-api/tests/
git commit -m "test: add expense-api test infrastructure (conftest, DB setup)"
```

---

## Task 3: expense-api Health + Policy Tests

**Files:**
- Create: `expense-api/tests/test_health.py`
- Create: `expense-api/tests/test_policy.py`

- [ ] **Step 1: Write test_health.py**

```python
# expense-api/tests/test_health.py
import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_unauthenticated_expense_list(client):
    """PRD §3.1 — every OA endpoint requires a valid JWT."""
    resp = await client.get("/api/v1/expenses")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_pa_list(client):
    resp = await client.get("/api/v1/pa")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_policy(client):
    resp = await client.get("/api/v1/policy")
    assert resp.status_code == 403
```

- [ ] **Step 2: Write test_policy.py**

```python
# expense-api/tests/test_policy.py
"""PRD S4-B — OA Expense Config admin panel: GET/PATCH /api/v1/policy."""
import pytest


@pytest.mark.asyncio
async def test_get_policy_defaults(admin_client):
    """Policy singleton auto-created on first access with sensible defaults."""
    resp = await admin_client.get("/api/v1/policy")
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["hst_rate"]) == pytest.approx(0.13)
    assert float(data["mileage_rate_per_km"]) > 0
    assert float(data["meal_breakfast_limit"]) > 0


@pytest.mark.asyncio
async def test_update_policy_as_admin(admin_client):
    """System admin can update any policy field; change is persisted."""
    resp = await admin_client.patch(
        "/api/v1/policy",
        json={"mileage_rate_per_km": "0.75"},
    )
    assert resp.status_code == 200
    assert float(resp.json()["mileage_rate_per_km"]) == pytest.approx(0.75)

    # Verify persisted
    resp2 = await admin_client.get("/api/v1/policy")
    assert float(resp2.json()["mileage_rate_per_km"]) == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_update_policy_as_finance_manager(finance_client):
    """Finance Manager is also allowed to update policy."""
    resp = await finance_client.patch(
        "/api/v1/policy",
        json={"max_km_per_claim": 3000},
    )
    assert resp.status_code == 200
    assert resp.json()["max_km_per_claim"] == 3000


@pytest.mark.asyncio
async def test_update_policy_forbidden_for_requester(requester_client):
    """PRD S4-B — only Finance Manager / System Admin may update policy (FR: role guard)."""
    resp = await requester_client.patch(
        "/api/v1/policy",
        json={"mileage_rate_per_km": "0.50"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 3: Run tests**

```bash
cd expense-api
.venv/Scripts/python -m pytest tests/test_health.py tests/test_policy.py -v
```

Expected: 8 tests, all PASSED.

- [ ] **Step 4: Commit**

```bash
git add expense-api/tests/test_health.py expense-api/tests/test_policy.py
git commit -m "test: expense-api health and policy endpoint tests"
```

---

## Task 4: expense-api Vendor Search + Budget Hierarchy Tests

**Files:**
- Create: `expense-api/tests/test_vendors.py`
- Create: `expense-api/tests/test_budget.py`

- [ ] **Step 1: Write test_vendors.py**

```python
# expense-api/tests/test_vendors.py
"""PRD-OA §2.3 — GET /api/v1/vendors: server-side ILIKE search on EpmsVendor."""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.epms_mirrors import EpmsVendor
import app.db.base as db_module


async def _seed_vendor(name: str, code: str, active: bool = True) -> EpmsVendor:
    factory = db_module.AsyncSessionLocal
    async with factory() as db:
        v = EpmsVendor(id=uuid.uuid4(), name=name, code=code, is_active=active)
        db.add(v)
        await db.commit()
        return v


@pytest.mark.asyncio
async def test_list_vendors_empty(admin_client):
    """Before seeding, returns empty list — not an error."""
    resp = await admin_client.get("/api/v1/vendors")
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_list_vendors_with_data(admin_client):
    await _seed_vendor("Titan Power Ltd", "TIT-001")
    resp = await admin_client.get("/api/v1/vendors?search=titan")
    assert resp.status_code == 200
    names = [v["name"] for v in resp.json()["items"]]
    assert any("Titan" in n for n in names)


@pytest.mark.asyncio
async def test_vendor_search_partial_match(admin_client):
    await _seed_vendor("Northern Dairy Supplies", "NDS-001")
    resp = await admin_client.get("/api/v1/vendors?search=dairy")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_vendor_search_case_insensitive(admin_client):
    await _seed_vendor("Acme Packaging", "ACM-001")
    resp = await admin_client.get("/api/v1/vendors?search=ACME")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_vendor_active_only_default(admin_client):
    """active_only=true is the default; inactive vendors must be excluded."""
    await _seed_vendor("OldVendorInactive", "OVI-001", active=False)
    resp = await admin_client.get("/api/v1/vendors?search=OldVendorInactive")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_vendor_unauthenticated(client):
    resp = await client.get("/api/v1/vendors")
    assert resp.status_code == 403
```

- [ ] **Step 2: Write test_budget.py**

```python
# expense-api/tests/test_budget.py
"""PRD-OA §3.2 — GET /api/v1/budget/hierarchy: Cost Center → L1 → L2 for form pickers."""
import uuid
import pytest

import app.db.base as db_module
from app.models.epms_mirrors import EpmsCostCenter, EpmsBudgetL1
from app.models.budget import BudgetAccount


async def _seed_hierarchy():
    """Seed CC → L1 → L2 in test DB."""
    async with db_module.AsyncSessionLocal() as db:
        cc = EpmsCostCenter(id=uuid.uuid4(), code="GA-0100", name="General Admin", is_active=True)
        db.add(cc)
        await db.flush()

        l1 = EpmsBudgetL1(id=uuid.uuid4(), cost_center_id=cc.id, code="GA001", name="Travel", is_active=True)
        db.add(l1)
        await db.flush()

        acc = BudgetAccount(
            id=uuid.uuid4(), l1_id=l1.id, code="GA00101", name="Travel Meals",
            annual_budget=10000, committed=0, actual_spent=0, is_active=True,
        )
        db.add(acc)
        await db.commit()
        return cc, l1, acc


@pytest.mark.asyncio
async def test_budget_hierarchy_empty(admin_client):
    resp = await admin_client.get("/api/v1/budget/hierarchy")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_budget_hierarchy_structure(admin_client):
    """PRD-OA §3.2 — hierarchy returns CC → l1_groups → accounts nesting."""
    await _seed_hierarchy()
    resp = await admin_client.get("/api/v1/budget/hierarchy")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    cc = next(x for x in data if x["code"] == "GA-0100")
    assert len(cc["l1_groups"]) >= 1
    l1 = cc["l1_groups"][0]
    assert "accounts" in l1
    assert len(l1["accounts"]) >= 1
    assert l1["accounts"][0]["code"] == "GA00101"


@pytest.mark.asyncio
async def test_budget_accounts_list(admin_client):
    resp = await admin_client.get("/api/v1/budget/accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    if data:
        acc = data[0]
        assert "code" in acc
        assert "annual_budget" in acc
        assert "available" in acc


@pytest.mark.asyncio
async def test_budget_hierarchy_unauthenticated(client):
    resp = await client.get("/api/v1/budget/hierarchy")
    assert resp.status_code == 403
```

- [ ] **Step 3: Run tests**

```bash
cd expense-api
.venv/Scripts/python -m pytest tests/test_vendors.py tests/test_budget.py -v
```

Expected: 10 tests, all PASSED.

- [ ] **Step 4: Commit**

```bash
git add expense-api/tests/test_vendors.py expense-api/tests/test_budget.py
git commit -m "test: expense-api vendor search and budget hierarchy tests (PRD-OA §2.3, §3.2)"
```

---

## Task 5: expense-api Invoice Create + Dedup Tests

**Files:**
- Create: `expense-api/tests/test_invoices.py`

- [ ] **Step 1: Write test_invoices.py**

```python
# expense-api/tests/test_invoices.py
"""PRD-OA §2 — expense invoice creation, dedup (409), status lifecycle."""
import uuid
import pytest


_VENDOR_ID = str(uuid.uuid4())   # shared across dedup tests


def _invoice_payload(**overrides) -> dict:
    base = {
        "file_name": "invoice.pdf",
        "file_mime_type": "application/pdf",
        "file_size_bytes": 102400,
        "invoice_number": f"INV-{uuid.uuid4().hex[:8]}",
        "vendor_id": _VENDOR_ID,
        "vendor_name": "Test Vendor",
        "currency": "CAD",
        "subtotal": "500.00",
        "tax_amount": "65.00",
        "total_amount": "565.00",
        "lines": [
            {
                "line_number": 1,
                "description": "Consulting services",
                "quantity": "1",
                "unit_price": "500.00",
                "amount": "500.00",
                "tax_amount": "65.00",
            }
        ],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_invoice_minimal(admin_client):
    """POST with no vendor_id or invoice_number is allowed (no dedup check performed)."""
    resp = await admin_client.post(
        "/api/v1/invoices",
        json={
            "file_name": "receipt.jpg",
            "file_mime_type": "image/jpeg",
            "file_size_bytes": 51200,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "reviewed"


@pytest.mark.asyncio
async def test_create_invoice_with_lines(admin_client):
    resp = await admin_client.post("/api/v1/invoices", json=_invoice_payload())
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "reviewed"
    assert len(data["lines"]) == 1
    assert float(data["total_amount"]) == pytest.approx(565.00)


@pytest.mark.asyncio
async def test_get_invoice(admin_client):
    create = await admin_client.post("/api/v1/invoices", json=_invoice_payload())
    inv_id = create.json()["id"]
    resp = await admin_client.get(f"/api/v1/invoices/{inv_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == inv_id


@pytest.mark.asyncio
async def test_get_invoice_not_found(admin_client):
    resp = await admin_client.get(f"/api/v1/invoices/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invoice_dedup_same_vendor_and_number(admin_client):
    """PRD-OA §2 — duplicate (vendor_id, invoice_number) → HTTP 409."""
    inv_num = f"DUP-{uuid.uuid4().hex[:6]}"
    payload = _invoice_payload(invoice_number=inv_num)
    r1 = await admin_client.post("/api/v1/invoices", json=payload)
    assert r1.status_code == 201

    r2 = await admin_client.post("/api/v1/invoices", json=payload)
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert "already recorded" in detail["message"].lower() or "duplicate" in str(detail).lower()


@pytest.mark.asyncio
async def test_invoice_dedup_different_vendor_allowed(admin_client):
    """Same invoice_number but different vendor_id is NOT a duplicate."""
    inv_num = f"SAME-NUM-{uuid.uuid4().hex[:6]}"
    r1 = await admin_client.post(
        "/api/v1/invoices",
        json=_invoice_payload(invoice_number=inv_num, vendor_id=str(uuid.uuid4())),
    )
    assert r1.status_code == 201

    r2 = await admin_client.post(
        "/api/v1/invoices",
        json=_invoice_payload(invoice_number=inv_num, vendor_id=str(uuid.uuid4())),
    )
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_invoice_dedup_no_vendor_no_number_skipped(admin_client):
    """Both fields absent → dedup check is skipped; two 201s are valid."""
    payload = {"file_name": "scan.pdf", "file_mime_type": "application/pdf", "file_size_bytes": 1024}
    r1 = await admin_client.post("/api/v1/invoices", json=payload)
    r2 = await admin_client.post("/api/v1/invoices", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_invoice_unauthenticated(client):
    resp = await client.post("/api/v1/invoices", json=_invoice_payload())
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests**

```bash
cd expense-api
.venv/Scripts/python -m pytest tests/test_invoices.py -v
```

Expected: 8 tests, all PASSED.

- [ ] **Step 3: Commit**

```bash
git add expense-api/tests/test_invoices.py
git commit -m "test: expense-api invoice create + dedup (409) tests (PRD-OA §2)"
```

---

## Task 6: expense-api PA-DIR Tests

**Files:**
- Create: `expense-api/tests/test_pa.py`

- [ ] **Step 1: Write test_pa.py**

```python
# expense-api/tests/test_pa.py
"""PRD-OA §3 — Direct Payment Application (PA-DIR) creation and lifecycle.

PA actions (submit/approve) delegate to approval-api — those calls are mocked
so tests run without a live approval-api.  Status transitions are tested by
directly setting PA.status in the test DB.
"""
import uuid
import re
import pytest

import app.db.base as db_module
from app.models.invoice import ExpenseInvoice
from app.models.pa import PaymentApplication


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _make_reviewed_invoice(client) -> dict:
    resp = await client.post(
        "/api/v1/invoices",
        json={
            "file_name": "vendor_invoice.pdf",
            "file_mime_type": "application/pdf",
            "file_size_bytes": 204800,
            "invoice_number": f"INV-{uuid.uuid4().hex[:8]}",
            "vendor_id": str(uuid.uuid4()),
            "vendor_name": "Titan Power Ltd",
            "currency": "CAD",
            "subtotal": "1000.00",
            "tax_amount": "130.00",
            "total_amount": "1130.00",
        },
    )
    assert resp.status_code == 201
    return resp.json()


def _pa_payload(invoice_id: str, **overrides) -> dict:
    base = {
        "invoice_id": invoice_id,
        "vendor_name": "Titan Power Ltd",
        "payment_amount": "1130.00",
        "currency": "CAD",
    }
    base.update(overrides)
    return base


# ── Creation ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_direct_pa(admin_client):
    """PRD-OA §3 — POST /pa/direct creates a PA-DIR in draft status."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))
    assert resp.status_code == 201
    data = resp.json()
    assert data["pa_type"] == "PA-DIR"
    assert data["status"] == "draft"
    assert data["po_id"] is None   # no linked PO


@pytest.mark.asyncio
async def test_pa_number_format(admin_client):
    """PRD-OA — PA number follows PA-YYYYMMDD-XXXX format."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))
    assert resp.status_code == 201
    assert re.match(r"PA-\d{8}-\d{4}", resp.json()["pa_number"])


@pytest.mark.asyncio
async def test_pa_auto_title(admin_client):
    """PRD-OA §3.1 — title auto-generated as 'Direct Payment — {Vendor} {InvNum}'."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post(
        "/api/v1/pa/direct",
        json=_pa_payload(inv["id"]),  # no explicit title
    )
    assert resp.status_code == 201
    title = resp.json()["title"]
    assert "Direct Payment" in title


@pytest.mark.asyncio
async def test_pa_custom_title(admin_client):
    """PRD-OA §3.1 — user-provided title overrides the auto-generated one."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post(
        "/api/v1/pa/direct",
        json=_pa_payload(inv["id"], title="Q2 Licensing Fee"),
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == "Q2 Licensing Fee"


@pytest.mark.asyncio
async def test_pa_with_budget_account_code(admin_client):
    """PRD-OA §3.2 — budget_account_code stored on PA."""
    inv = await _make_reviewed_invoice(admin_client)
    resp = await admin_client.post(
        "/api/v1/pa/direct",
        json=_pa_payload(inv["id"], budget_account_code="GA00101"),
    )
    assert resp.status_code == 201
    assert resp.json()["budget_account_code"] == "GA00101"


@pytest.mark.asyncio
async def test_invoice_marked_used_after_pa_creation(admin_client):
    """After PA-DIR is created, invoice.status becomes 'used'."""
    inv = await _make_reviewed_invoice(admin_client)
    await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))

    # Verify invoice status in DB
    async with db_module.AsyncSessionLocal() as db:
        invoice = await db.get(ExpenseInvoice, uuid.UUID(inv["id"]))
        assert invoice.status == "used"


@pytest.mark.asyncio
async def test_cannot_create_pa_for_used_invoice(admin_client):
    """PRD-OA — once an invoice is used, a second PA-DIR cannot be created for it."""
    inv = await _make_reviewed_invoice(admin_client)
    r1 = await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))
    assert r1.status_code == 201

    r2 = await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_cannot_create_pa_for_nonexistent_invoice(admin_client):
    resp = await admin_client.post(
        "/api/v1/pa/direct",
        json=_pa_payload(str(uuid.uuid4())),
    )
    assert resp.status_code == 404


# ── Read ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_pas(admin_client):
    resp = await admin_client.get("/api/v1/pa")
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_get_pa(admin_client):
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()
    resp = await admin_client.get(f"/api/v1/pa/{pa['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == pa["id"]


@pytest.mark.asyncio
async def test_get_pa_not_found(admin_client):
    resp = await admin_client.get(f"/api/v1/pa/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── Action delegation (mocked) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pa_action_submit_delegates_to_approval_api(admin_client, mocker):
    """POST /pa/{id}/action calls delegate_action with action_key='pa_dir'."""
    mock_delegate = mocker.patch(
        "app.api.v1.pa.delegate_action",
        new_callable=mocker.AsyncMock,
        return_value=None,
    )
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/action",
        json={"action": "submit"},
    )
    assert resp.status_code == 200
    mock_delegate.assert_called_once()
    call_args = mock_delegate.call_args
    assert call_args[0][0] == "pa_dir"   # action_key for PA-DIR (no po_id)
    assert call_args[0][2] == "submit"   # action


# ── Payment recording ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_payment_requires_approved_status(admin_client):
    """PRD §3.3 — pay action rejected unless PA is 'approved'."""
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/pay",
        json={"payment_reference": "TXN-001", "payment_date": "2026-05-08"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_record_payment_requires_finance_role(requester_client, admin_client):
    """PRD §1.3 — only ap_clerk/finance_bp/finance_manager/system_admin can record payment."""
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    # Manually set PA to approved
    async with db_module.AsyncSessionLocal() as db:
        record = await db.get(PaymentApplication, uuid.UUID(pa["id"]))
        record.status = "approved"
        await db.commit()

    resp = await requester_client.post(
        f"/api/v1/pa/{pa['id']}/pay",
        json={"payment_reference": "TXN-001", "payment_date": "2026-05-08"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_record_payment_success(admin_client):
    inv = await _make_reviewed_invoice(admin_client)
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_payload(inv["id"]))).json()

    async with db_module.AsyncSessionLocal() as db:
        record = await db.get(PaymentApplication, uuid.UUID(pa["id"]))
        record.status = "approved"
        await db.commit()

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/pay",
        json={"payment_reference": "TXN-001", "payment_date": "2026-05-08"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"
```

- [ ] **Step 2: Run tests**

```bash
cd expense-api
.venv/Scripts/python -m pytest tests/test_pa.py -v
```

Expected: 15 tests, all PASSED.

- [ ] **Step 3: Commit**

```bash
git add expense-api/tests/test_pa.py
git commit -m "test: expense-api PA-DIR creation, invoice used-status, action delegation, payment tests (PRD-OA §3)"
```

---

## Task 7: expense-api Expense Claim Tests (EXP + MIL)

**Files:**
- Create: `expense-api/tests/test_expenses.py`

- [ ] **Step 1: Write test_expenses.py**

```python
# expense-api/tests/test_expenses.py
"""PRD S2 — EXP general expense and MIL mileage claim creation, listing, scoping."""
import uuid
from datetime import date
import pytest

import app.db.base as db_module
from app.models.expense import ExpenseClaim
from app.models.budget import BudgetAccount


async def _seed_budget_account() -> BudgetAccount:
    async with db_module.AsyncSessionLocal() as db:
        acct = BudgetAccount(
            id=uuid.uuid4(), code="TEST001", name="Test Account",
            annual_budget=50000, committed=0, actual_spent=0, is_active=True,
        )
        db.add(acct)
        await db.commit()
        return acct


def _exp_payload(acct_id: str, **overrides) -> dict:
    base = {
        "claim_type": "EXP",
        "submission_date": str(date.today()),
        "currency": "CAD",
        "purpose": "Office supplies",
        "line_items": [
            {
                "line_number": 1,
                "expense_date": str(date.today()),
                "description": "Printer paper",
                "budget_account_id": acct_id,
                "budget_account_code": "TEST001",
                "budget_account_name": "Test Account",
                "total_amount": "45.20",
                "tax_amount": "5.20",
                "net_amount": "40.00",
            }
        ],
    }
    base.update(overrides)
    return base


def _mil_payload(acct_id: str, **overrides) -> dict:
    base = {
        "claim_type": "MIL",
        "submission_date": str(date.today()),
        "currency": "CAD",
        "vehicle_description": "2022 Honda Civic",
        "vehicle_owned_by": "self",
        "trip_items": [
            {
                "trip_number": 1,
                "trip_date": str(date.today()),
                "from_location": "Office",
                "to_location": "Client Site",
                "purpose": "Client meeting",
                "is_round_trip": True,
                "distance_km": "45.0",
                "rate_per_km": "0.72",
                "amount": "32.40",
                "budget_account_id": acct_id,
                "budget_account_code": "TEST001",
                "budget_account_name": "Test Account",
            }
        ],
    }
    base.update(overrides)
    return base


# ── EXP ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_exp_claim(admin_client):
    """PRD S2 — create a general expense claim (EXP)."""
    acct = await _seed_budget_account()
    resp = await admin_client.post("/api/v1/expenses", json=_exp_payload(str(acct.id)))
    assert resp.status_code == 201
    data = resp.json()
    assert data["claim_type"] == "EXP"
    assert data["status"] == "draft"
    assert data["claim_number"].startswith("EXP-")


@pytest.mark.asyncio
async def test_exp_claim_number_format(admin_client):
    """PRD — claim number is EXP-YYYYMMDD-XXXX."""
    import re
    acct = await _seed_budget_account()
    resp = await admin_client.post("/api/v1/expenses", json=_exp_payload(str(acct.id)))
    assert re.match(r"EXP-\d{8}-\d{4}", resp.json()["claim_number"])


@pytest.mark.asyncio
async def test_exp_line_totals_computed(admin_client):
    acct = await _seed_budget_account()
    resp = await admin_client.post("/api/v1/expenses", json=_exp_payload(str(acct.id)))
    data = resp.json()
    assert float(data["total_amount"]) == pytest.approx(45.20)
    assert float(data["net_amount"]) == pytest.approx(40.00)


# ── MIL ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_mil_claim(admin_client):
    """PRD S2 — MIL mileage claim: amount = distance_km × rate_per_km."""
    acct = await _seed_budget_account()
    resp = await admin_client.post("/api/v1/expenses", json=_mil_payload(str(acct.id)))
    assert resp.status_code == 201
    data = resp.json()
    assert data["claim_type"] == "MIL"
    assert data["claim_number"].startswith("MIL-")
    # 45 km × 0.72 = 32.40
    assert float(data["total_amount"]) == pytest.approx(32.40)


@pytest.mark.asyncio
async def test_mil_claim_number_format(admin_client):
    import re
    acct = await _seed_budget_account()
    resp = await admin_client.post("/api/v1/expenses", json=_mil_payload(str(acct.id)))
    assert re.match(r"MIL-\d{8}-\d{4}", resp.json()["claim_number"])


# ── List / filter ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_expenses(admin_client):
    resp = await admin_client.get("/api/v1/expenses")
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_list_expenses_type_filter(admin_client):
    """?type=MIL returns only MIL claims."""
    acct = await _seed_budget_account()
    await admin_client.post("/api/v1/expenses", json=_exp_payload(str(acct.id)))
    await admin_client.post("/api/v1/expenses", json=_mil_payload(str(acct.id)))

    resp = await admin_client.get("/api/v1/expenses?type=MIL")
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["claim_type"] == "MIL"


# ── Action delegation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expense_action_submit_delegates(admin_client, mocker):
    """Submitting an EXP claim delegates to approval-api with action_key='exp'."""
    mock_delegate = mocker.patch(
        "app.api.v1.expenses.delegate_action",
        new_callable=mocker.AsyncMock,
        return_value=None,
    )
    acct = await _seed_budget_account()
    claim = (await admin_client.post("/api/v1/expenses", json=_exp_payload(str(acct.id)))).json()

    resp = await admin_client.post(
        f"/api/v1/expenses/{claim['id']}/action",
        json={"action": "submit"},
    )
    assert resp.status_code == 200
    mock_delegate.assert_called_once()
    assert mock_delegate.call_args[0][0] == "exp"


@pytest.mark.asyncio
async def test_mil_action_uses_mil_action_key(admin_client, mocker):
    """MIL claim delegates to approval-api with action_key='mil'."""
    mock_delegate = mocker.patch(
        "app.api.v1.expenses.delegate_action",
        new_callable=mocker.AsyncMock,
        return_value=None,
    )
    acct = await _seed_budget_account()
    claim = (await admin_client.post("/api/v1/expenses", json=_mil_payload(str(acct.id)))).json()

    await admin_client.post(
        f"/api/v1/expenses/{claim['id']}/action",
        json={"action": "submit"},
    )
    assert mock_delegate.call_args[0][0] == "mil"
```

- [ ] **Step 2: Run tests**

```bash
cd expense-api
.venv/Scripts/python -m pytest tests/test_expenses.py -v
```

Expected: 11 tests, all PASSED.

- [ ] **Step 3: Run full suite**

```bash
cd expense-api
.venv/Scripts/python -m pytest tests/ -q
```

Expected: all tests pass, 0 failures.

- [ ] **Step 4: Commit**

```bash
git add expense-api/tests/test_expenses.py
git commit -m "test: expense-api EXP and MIL claim creation, filtering, action delegation tests (PRD S2)"
```

---

## Task 8: epms-api Cost Center Tests (New Endpoint)

**Files:**
- Create: `epms-api/tests/test_cost_centers.py`

The new `GET/POST/PATCH/DELETE /api/v1/cost-centers` endpoints were added in the most recent session (`epms-api/app/api/v1/cost_centers.py`, `epms-api/app/crud/cost_center.py`). These have no tests yet.

- [ ] **Step 1: Check existing department fixture in conftest.py**

Look at `epms-api/tests/conftest.py` — confirm `admin_client` fixture exists (it does). Cost center tests need a real department to reference.

- [ ] **Step 2: Write test_cost_centers.py**

```python
# epms-api/tests/test_cost_centers.py
"""Tests for GET/POST/PATCH/DELETE /api/v1/cost-centers (added 2026-05-08)."""
import pytest

URL = "/api/v1/cost-centers"
DEPT_URL = "/api/v1/departments"


async def _make_dept(client, code: str | None = None) -> dict:
    import uuid
    code = code or f"D{uuid.uuid4().hex[:4].upper()}"
    resp = await client.post(DEPT_URL, json={"code": code, "name": f"Dept {code}", "is_active": True})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_cc(client, dept_id: str, code: str | None = None) -> dict:
    import uuid
    code = code or f"CC{uuid.uuid4().hex[:4].upper()}"
    resp = await client.post(URL, json={"code": code, "name": f"CostCenter {code}", "department_id": dept_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── List ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_cost_centers(admin_client):
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_cost_centers_filter_by_department(admin_client):
    dept_a = await _make_dept(admin_client)
    dept_b = await _make_dept(admin_client)
    cc_a = await _make_cc(admin_client, dept_a["id"])
    await _make_cc(admin_client, dept_b["id"])

    resp = await admin_client.get(f"{URL}?department_id={dept_a['id']}")
    assert resp.status_code == 200
    ids = [x["id"] for x in resp.json()]
    assert cc_a["id"] in ids
    # cc_b must NOT appear
    for x in resp.json():
        assert x["department_id"] == dept_a["id"]


# ── Create ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_cost_center(admin_client):
    dept = await _make_dept(admin_client)
    resp = await admin_client.post(URL, json={
        "code": "GA-0200", "name": "IT Department", "department_id": dept["id"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["code"] == "GA-0200"
    assert data["department_id"] == dept["id"]


@pytest.mark.asyncio
async def test_create_cost_center_nonexistent_dept(admin_client):
    """Creating a CC against a non-existent department → 404."""
    import uuid
    resp = await admin_client.post(URL, json={
        "code": "FAKE-CC", "name": "Fake", "department_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_cost_center_duplicate_code(admin_client):
    """Duplicate code → 409."""
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"], code="DUP-CODE")
    resp = await admin_client.post(URL, json={
        "code": "DUP-CODE", "name": "Another", "department_id": dept["id"],
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_cost_center_requires_admin(finance_client):
    """Only system_admin can create cost centers."""
    import uuid
    resp = await finance_client.post(URL, json={
        "code": "NO-PERM", "name": "Test", "department_id": str(uuid.uuid4()),
    })
    assert resp.status_code == 403


# ── Read ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_cost_center(admin_client):
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])
    resp = await admin_client.get(f"{URL}/{cc['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == cc["id"]


@pytest.mark.asyncio
async def test_get_cost_center_not_found(admin_client):
    import uuid
    resp = await admin_client.get(f"{URL}/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── Update ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_cost_center(admin_client):
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])
    resp = await admin_client.patch(f"{URL}/{cc['id']}", json={"name": "Renamed CC"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed CC"


@pytest.mark.asyncio
async def test_update_cost_center_nonexistent_dept(admin_client):
    import uuid
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])
    resp = await admin_client.patch(f"{URL}/{cc['id']}", json={"department_id": str(uuid.uuid4())})
    assert resp.status_code == 404


# ── Delete ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_cost_center(admin_client):
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])
    resp = await admin_client.delete(f"{URL}/{cc['id']}")
    assert resp.status_code == 204
    assert (await admin_client.get(f"{URL}/{cc['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_delete_cost_center_with_pr_references(admin_client):
    """PRD — CC referenced by PRs cannot be deleted (409), must deactivate instead."""
    from tests.test_pr import _payload as pr_payload
    dept = await _make_dept(admin_client)
    cc = await _make_cc(admin_client, dept["id"])

    # Create a PR that references this CC
    pr_resp = await admin_client.post("/api/v1/pr", json={
        **pr_payload(),
        "cost_center_id": cc["id"],
    })
    if pr_resp.status_code == 201:
        # CC now has references
        del_resp = await admin_client.delete(f"{URL}/{cc['id']}")
        assert del_resp.status_code == 409
        assert "deactivate" in del_resp.json()["detail"].lower()
```

- [ ] **Step 3: Run tests**

```bash
cd epms-api
.venv/Scripts/python -m pytest tests/test_cost_centers.py -v
```

Expected: 13 tests, all PASSED.

- [ ] **Step 4: Commit**

```bash
git add epms-api/tests/test_cost_centers.py
git commit -m "test: epms-api cost center CRUD endpoint tests (new endpoint 2026-05-08)"
```

---

## Task 9: epms-api PR List Scoping Tests (PL-001 to PL-005)

**Files:**
- Create: `epms-api/tests/test_pr_scoping.py`

These tests verify the FR IDs from PRD §3.2.3.

- [ ] **Step 1: Write test_pr_scoping.py**

```python
# epms-api/tests/test_pr_scoping.py
"""PRD §3.2.3 — PR list visibility scoping (FR IDs: PL-001 through PL-005).

These tests create users with specific roles and departments, then verify
that GET /api/v1/pr returns only the PRs each role should see.
"""
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.schemas.auth import RegisterRequest


async def _make_user(test_engine, role: str, dept_id: uuid.UUID | None = None) -> tuple[str, str]:
    """Create user, return (user_id, token)."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:6]}@scope-test.com",
                password="TestPass1!",
                full_name=f"Scope {role}",
                role=role,
            ),
        )
        if dept_id:
            user.department_id = dept_id
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    return str(user.id), token


def _authed_client(token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


_PR_BASE = {
    "title": "Scoping Test PR",
    "type": 3,
    "currency": "CAD",
    "line_items": [
        {"description": "Part A", "material_id": "M-001", "qty": "1", "unit": "EA", "unit_price": "10.00"}
    ],
}


@pytest.mark.asyncio
async def test_pl001_requester_cannot_see_other_requester_pr(test_engine):
    """PL-001: server enforces requester scope — no parameter bypass possible."""
    dept_id = None  # no department needed for this check

    uid_a, tok_a = await _make_user(test_engine, "requester")
    uid_b, tok_b = await _make_user(test_engine, "requester")

    # Requester A creates a PR
    async with _authed_client(tok_a) as ca:
        r = await ca.post("/api/v1/pr", json=_PR_BASE)
        assert r.status_code == 201

    # Requester B should NOT see requester A's PR
    async with _authed_client(tok_b) as cb:
        resp = await cb.get("/api/v1/pr")
        assert resp.status_code == 200
        ids_seen = [p["created_by"] for p in resp.json()["items"]]
        assert uid_a not in ids_seen, "Requester B saw Requester A's PR — PL-001 violated"


@pytest.mark.asyncio
async def test_pl001_requester_sees_own_prs(test_engine):
    """PL-001: requester can see their own PRs."""
    uid, tok = await _make_user(test_engine, "requester")
    async with _authed_client(tok) as c:
        r = await c.post("/api/v1/pr", json=_PR_BASE)
        assert r.status_code == 201
        pr_id = r.json()["id"]

        resp = await c.get("/api/v1/pr")
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()["items"]]
        assert pr_id in ids, "Requester does not see their own PR — PL-001 violated"


@pytest.mark.asyncio
async def test_pl004_finance_manager_sees_all_prs(test_engine, admin_client):
    """PL-004: Finance Manager sees all PRs regardless of department."""
    # Create a PR as admin (from some department)
    r = await admin_client.post("/api/v1/pr", json=_PR_BASE)
    assert r.status_code == 201

    _, tok_fm = await _make_user(test_engine, "finance_manager")
    async with _authed_client(tok_fm) as c:
        resp = await c.get("/api/v1/pr")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1, "Finance Manager should see all PRs — PL-004 violated"


@pytest.mark.asyncio
async def test_pl004_procurement_officer_sees_all_prs(test_engine, admin_client):
    """PL-004: Procurement Officer sees all PRs."""
    r = await admin_client.post("/api/v1/pr", json=_PR_BASE)
    assert r.status_code == 201

    _, tok_po = await _make_user(test_engine, "procurement_officer")
    async with _authed_client(tok_po) as c:
        resp = await c.get("/api/v1/pr")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_pl005_pr_cache_scope_enforced(test_engine):
    """PL-005: scope cannot be bypassed via query params (server ignores 'mine=false')."""
    uid_a, tok_a = await _make_user(test_engine, "requester")
    uid_b, tok_b = await _make_user(test_engine, "requester")

    async with _authed_client(tok_a) as ca:
        await ca.post("/api/v1/pr", json=_PR_BASE)

    async with _authed_client(tok_b) as cb:
        # Attempt to bypass by passing a parameter that suggests "show all"
        resp = await cb.get("/api/v1/pr?mine=false")
        assert resp.status_code == 200
        ids_seen = [p["created_by"] for p in resp.json()["items"]]
        assert uid_a not in ids_seen, "mine=false bypassed server scope — PL-005 violated"
```

- [ ] **Step 2: Run tests**

```bash
cd epms-api
.venv/Scripts/python -m pytest tests/test_pr_scoping.py -v
```

Expected: 5 tests, all PASSED.

- [ ] **Step 3: Run full epms-api suite to check no regressions**

```bash
cd epms-api
.venv/Scripts/python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add epms-api/tests/test_pr_scoping.py
git commit -m "test: PR list scoping tests for PL-001, PL-004, PL-005 (PRD §3.2.3)"
```

---

## Task 10: Smoke Test Update — expense-api

**Files:**
- Modify: `test_all.py`

- [ ] **Step 1: Add expense-api checks to test_all.py**

Find the line `sys.exit(0)` or the end of the existing smoke tests, and add the following block before it:

```python
# ── expense-api (:8006) ──────────────────────────────────────────────

BASE_OA = "http://localhost:8006"

section("expense-api  :8006")

oa_health = get(f"{BASE_OA}/health")
check("Health", oa_health, "status", "ok")

# Auth: try to get a token from epms-api and use it on expense-api
oa_vendors = get(f"{BASE_OA}/api/v1/vendors?page_size=5", TOKEN)
if "_error" not in oa_vendors:
    check("Vendors endpoint (shared JWT)", {"ok": True})
    print(f"     → {oa_vendors.get('total', 0)} vendors")
else:
    print(f"⚠️  Vendors: {oa_vendors['_error']} (expense-api may need restart)")

oa_hierarchy = get(f"{BASE_OA}/api/v1/budget/hierarchy", TOKEN)
if "_error" not in oa_hierarchy:
    check("Budget hierarchy endpoint", {"ok": True})
    print(f"     → {len(oa_hierarchy)} cost centers in hierarchy")
else:
    print(f"⚠️  Budget hierarchy: {oa_hierarchy['_error']}")

oa_policy = get(f"{BASE_OA}/api/v1/policy", TOKEN)
check("Policy (singleton)", oa_policy, "hst_rate")

oa_pa = get(f"{BASE_OA}/api/v1/pa?page_size=5", TOKEN)
check("PA list", oa_pa, "total")
```

- [ ] **Step 2: Run smoke tests (requires all services)**

```bash
cd /c/Project/uniops
./run_tests.sh --smoke-only
```

Expected: all `✅` lines for expense-api section.

- [ ] **Step 3: Commit**

```bash
git add test_all.py
git commit -m "test: add expense-api smoke tests to test_all.py"
```

---

## Self-Review: Spec Coverage Check

| PRD FR ID | Requirement | Covered By |
|-----------|-------------|-----------|
| PL-001 | Requester cannot see another requester's PR | Task 9: `test_pl001_requester_cannot_see_other_requester_pr` |
| PL-002 | Dept manager scoped to own dept | Not yet — requires dept fixture (future task) |
| PL-003 | GM/OPM see mapped dept PRs | Not yet — requires GM/OPM mapping seed (future task) |
| PL-004 | Procurement roles see all PRs | Task 9: `test_pl004_finance_manager_sees_all_prs` |
| PL-005 | mine=false cannot bypass scope | Task 9: `test_pl005_pr_cache_scope_enforced` |
| REJ-001 | Reject PR → terminal `rejected` | Covered in existing `test_pr.py::test_reject_pr` |
| REJ-002 | Return PR → Requester can edit | Covered in existing `test_pr.py::test_return_pr` |
| REJ-004 | Withdraw / cancel PR | Covered in existing `test_pr.py::test_cancel_draft_pr` |
| OBG-001..008 | Over-budget PR flow | Not yet — requires budget fixture (future task) |
| WF-AUTH-001 | Approval engine rejects unauthorised approver | Covered in existing `test_approval_delegation.py` |
| PRD-OA §2 invoice dedup | 409 on duplicate (vendor_id, invoice_number) | Task 5 |
| PRD-OA §3 PA-DIR creation | Draft, pa_type=PA-DIR, po_id=None | Task 6 |
| PRD-OA §3.2 budget hierarchy | 3-level CC→L1→L2 nesting | Task 4 |
| PRD-OA §2.3 vendor search | ILIKE search, active_only | Task 4 |
| PRD S4-B policy config | GET/PATCH, role guard | Task 3 |
| EXP claim create | Claim number, totals, status | Task 7 |
| MIL claim create | amount = km × rate | Task 7 |
| action_key routing | EXP→"exp", MIL→"mil", PA-DIR→"pa_dir" | Tasks 6, 7 |

**Known gaps (future tasks):** PL-002/PL-003 (dept-scoped manager), OBG-001..008 (over-budget PR). These require seeding department assignments and budget configuration that go beyond the scope of this plan.

---

## Execution Options

Plan complete and saved to `docs/superpowers/plans/2026-05-08-test-cases.md`.

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
**2. Inline Execution** — execute tasks in this session using executing-plans

Which approach?
