# QBO Mirror — Phase 3 (finance-api endpoints + Finance UI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the completed QBO mirror to Finance users: a `/qbo` API in
finance-api (trigger sync + browse) and a Finance page (sync panel + entity tabs +
detail), gated on `view_finance`.

**Architecture:** Backend router `app/api/v1/qbo.py` modeled on `nc_sync.py`
(sync trigger runs the async orchestrator on a worker thread; status polling;
paged browse; detail; attachment stream). Frontend `QboMirrorPage.tsx` + a
`qboApi` client, wired into `financeRoutes` and the finance nav. Read-only over the
mirror. Spec: `docs/superpowers/specs/2026-07-26-qbo-mirror-phase3-ui-design.md`.

**Tech Stack:** FastAPI, async SQLAlchemy, pytest (backend); React + TypeScript
6.0.3 + @tanstack/react-query + @uniops/shell tabs + Vite (frontend).

---

## Grounding facts (verified in-repo)

- Routers register in `app/api/v1/__init__.py` (`include_router`). Base path is
  `/finance/v1` + router prefix. Use prefix `/qbo`.
- Permission: **server-side is auth-only (`CurrentUser`)**, matching every other
  finance-api read endpoint (ap_invoices/ar/gl reads use just `CurrentUser`).
  `view_finance` is enforced CLIENT-SIDE via the nav gate — it is an EPMS matrix
  key not seeded in finance-api's test permission tables, so do NOT server-gate on
  it. `CurrentUser = Annotated[dict, Depends(get_token_payload)]` from
  `app.core.deps`. (Decision confirmed with the user: finance users view AND
  trigger; server requires only authentication.)
- Sync precedent: `app/api/v1/nc_sync.py` + `app/services/nc_sync.py`
  (`start_run`, `_run_worker`, `SSTALE_AFTER`, stale sweep). QBO's orchestrator
  `scripts/qbo_import/orchestrator.py:run_sync(db, client, mode, entities,
  started_by)` is ASYNC and uses an AsyncSession; the worker thread must create
  its own async engine + session and `asyncio.run` it.
- Frontend api client: `financeApi.get/post` in `finance/src/lib/api.ts` →
  `${VITE_FINANCE_API_URL}/finance/v1<path>`; `financeDownload(path, filename)`
  for file downloads. Polling: react-query `refetchInterval`.
- Routes: `finance/src/app/routes.ts` (`financeRoutes`), consumed by
  `@uniops/shell` `TabHost`/`TabRouterSync` in `AppLayout.tsx`. Nav: the `NAV`
  array in `AppLayout.tsx` (`{label, href, icon, permission}`).
- Frontend typecheck: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
  (TS 6.0.3; `npm run build` fails on baseUrl deprecation — use tsc directly).
- Backend test env (one finance-api pytest at a time):
  ```
  export TEST_PG_PASSWORD=$(docker exec uniops_postgres env | grep '^POSTGRES_PASSWORD=' | cut -d= -f2- | tr -d '\r')
  export DATABASE_URL="postgresql+asyncpg://epms:epms_dev@localhost:5432/finance_test"
  export JWT_SECRET_KEY="test-secret"
  ```
- Commit author `Claude <noreply@anthropic.com>`; body ends with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Entity whitelist (URL slug → header model): `vendors`→QboVendor,
  `accounts`→QboAccount, `bills`→QboBill, `bill-payments`→QboBillPayment,
  `vendor-credits`→QboVendorCredit, `purchases`→QboPurchase, `invoices`→QboInvoice,
  `payments`→QboPayment, `credit-memos`→QboCreditMemo, `deposits`→QboDeposit,
  `transfers`→QboTransfer, `journal-entries`→QboJournalEntry. Line-model map for
  detail: bills→QboBillLine, etc. (Transfer/Vendor/Account have no lines.)

---

### Task 1: `qbo_web` service helper — thread-dispatched async sync

**Files:**
- Create: `finance-api/app/services/qbo_sync.py`
- Test: `finance-api/tests/test_qbo_sync_service.py`

Wraps the scripts-layer orchestrator for the API: a "configured?" check, a
stale-run constant, and a thread launcher that runs the async orchestrator with
its own engine/session so it never touches the request session or blocks the loop.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qbo_sync_service.py
from datetime import timedelta
from app.services import qbo_sync


def test_configured_reflects_env(monkeypatch, tmp_path):
    # Missing file → not configured.
    monkeypatch.setattr(qbo_sync, "ENV_PATH", tmp_path / "absent.env")
    assert qbo_sync.qbo_configured() is False
    # Present with the required keys → configured.
    p = tmp_path / "qbo_conn.env"
    p.write_text("CLIENT_ID=x\nCLIENT_SECRET=y\nREALM_ID=z\nREFRESH_TOKEN=t\n", encoding="utf-8")
    monkeypatch.setattr(qbo_sync, "ENV_PATH", p)
    assert qbo_sync.qbo_configured() is True


def test_stale_after_is_a_timedelta():
    assert isinstance(qbo_sync.STALE_AFTER, timedelta)
```

- [ ] **Step 2: Run → FAIL** (`No module named 'app.services.qbo_sync'`).

Run: `pytest tests/test_qbo_sync_service.py -v`

- [ ] **Step 3: Write the service**

```python
# app/services/qbo_sync.py
"""API-facing wrapper over the scripts-layer QBO import orchestrator.

The orchestrator is async and owns DB writes; to run it from a request without
blocking the event loop or borrowing the request session, we launch it on a
worker thread that builds its own async engine + session and asyncio.run()s it.
"""
import asyncio
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from scripts.qbo_import.client import ENV_PATH, load_cfg
from scripts.qbo_import.orchestrator import DEFAULT_ENTITIES, run_sync

# A run whose row hasn't updated in this long is considered abandoned.
STALE_AFTER = timedelta(minutes=30)
FULL_CONFIRM = "RELOAD"
_REQUIRED = ("CLIENT_ID", "CLIENT_SECRET", "REALM_ID", "REFRESH_TOKEN")


def qbo_configured() -> bool:
    if not ENV_PATH.exists():
        return False
    try:
        cfg = load_cfg(ENV_PATH)
    except Exception:  # noqa: BLE001
        return False
    return all(cfg.get(k) for k in _REQUIRED)


def launch_sync(mode: str, entities: list[str] | None, with_attachments: bool) -> None:
    """Run the orchestrator (and optional attachments) on a worker thread.

    Builds a thread-local async engine/session; the caller has already inserted
    the RUNNING QboSyncRun row, so a failure here is recorded on that row by
    run_sync's own try/except.
    """
    def _worker() -> None:
        async def _go() -> None:
            engine = create_async_engine(settings.database_url, connect_args={"ssl": False})
            Session = async_sessionmaker(engine, expire_on_commit=False)
            # Imports here to avoid a heavy import at module load.
            from scripts.qbo_import.attachments import load_attachments
            from scripts.qbo_import.client import QboClient
            from scripts.qbo_import.extract import extract_entity
            client = QboClient()
            async with Session() as db:
                await run_sync(db, client, mode=mode, entities=entities or DEFAULT_ENTITIES)
            if with_attachments:
                async with Session() as db:
                    objs = extract_entity(client, "Attachable", since=None)
                    await load_attachments(db, objs)
            await engine.dispose()
        asyncio.run(_go())

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _worker)
```

- [ ] **Step 4: Run → PASS** (`pytest tests/test_qbo_sync_service.py -v`).

- [ ] **Step 5: Commit**

```bash
git add app/services/qbo_sync.py tests/test_qbo_sync_service.py
git commit -m "feat(qbo): api sync service (thread-dispatched async orchestrator)"
```

---

### Task 2: `/qbo/sync` trigger + status + runs endpoints

**Files:**
- Create: `finance-api/app/api/v1/qbo.py`
- Modify: `finance-api/app/api/v1/__init__.py` (register router)
- Test: `finance-api/tests/test_qbo_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qbo_api.py
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.deps import get_token_payload
from app.services import qbo_sync


@pytest.mark.asyncio
async def test_status_shape(db_session, monkeypatch):
    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: True)
    app.dependency_overrides[get_token_payload] = lambda: {
        "sub": "00000000-0000-0000-0000-000000000001", "role": "system_admin"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/finance/v1/qbo/sync/status", headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"can_sync", "configured", "current_run", "last_run"}
    assert body["configured"] is True


@pytest.mark.asyncio
async def test_full_sync_requires_confirm(db_session, monkeypatch):
    monkeypatch.setattr(qbo_sync, "qbo_configured", lambda: True)
    launched = {}
    monkeypatch.setattr(qbo_sync, "launch_sync",
                        lambda **kw: launched.update(kw))
    app.dependency_overrides[get_token_payload] = lambda: {
        "sub": "00000000-0000-0000-0000-000000000001", "role": "system_admin"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        bad = await c.post("/finance/v1/qbo/sync",
                           json={"mode": "full"}, headers={"Authorization": "Bearer x"})
        good = await c.post("/finance/v1/qbo/sync",
                            json={"mode": "full", "confirm": "RELOAD"},
                            headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    assert bad.status_code == 422
    assert good.status_code == 202
    assert launched.get("mode") == "full"
```

Note: the router is auth-only (no permission gate), so overriding
`get_token_payload` to return a payload dict is sufficient — no permission
seeding needed. If `app.main:app` import needs env (JWT_SECRET_KEY/DATABASE_URL),
those are already set in the test env block above. Confirm the `db_session`
fixture name and how existing API tests (e.g. `test_coa.py`) build the ASGI
client / override `get_db`, and follow that.

- [ ] **Step 2: Run → FAIL** (404 / import error).

- [ ] **Step 3: Write the router** (trigger + status + runs)

```python
# app/api/v1/qbo.py
"""QuickBooks Online mirror API — sync trigger/status + browse. Gated view_finance."""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.qbo import RUNNING, QboSyncRun
from app.services import qbo_sync as svc

# Server-side auth only (CurrentUser); view_finance is the client-side nav gate,
# matching every other finance-api read endpoint.
router = APIRouter(prefix="/qbo", tags=["qbo"])


class SyncIn(BaseModel):
    mode: Literal["full", "incremental"]
    entities: list[str] | None = None
    with_attachments: bool = True
    confirm: str | None = None


def _run_out(r: QboSyncRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": str(r.id), "mode": r.mode, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "counters": r.counters, "watermarks": r.watermarks, "error": r.error,
    }


@router.get("/sync/status")
async def sync_status(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await db.execute(text(
        "update qbo_sync_runs set status='failed', error='abandoned', "
        "finished_at=now(), updated_at=now() "
        "where status='running' and updated_at < :cutoff"),
        {"cutoff": datetime.now(timezone.utc) - svc.STALE_AFTER})
    await db.commit()
    current = (await db.execute(select(QboSyncRun).where(QboSyncRun.status == RUNNING)
               .order_by(QboSyncRun.started_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(select(QboSyncRun).where(QboSyncRun.status != RUNNING)
            .order_by(QboSyncRun.started_at.desc()).limit(1))).scalars().first()
    return {"can_sync": True, "configured": svc.qbo_configured(),
            "current_run": _run_out(current), "last_run": _run_out(last)}


@router.get("/sync/runs")
async def sync_runs(user: CurrentUser, db: AsyncSession = Depends(get_db), limit: int = 20):
    rows = (await db.execute(select(QboSyncRun)
            .order_by(QboSyncRun.started_at.desc()).limit(min(limit, 100)))).scalars().all()
    return {"items": [_run_out(r) for r in rows]}


@router.post("/sync", status_code=202)
async def trigger_sync(body: SyncIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    if not svc.qbo_configured():
        raise HTTPException(status_code=503, detail="QBO connection is not configured")
    if body.mode == "full" and body.confirm != svc.FULL_CONFIRM:
        raise HTTPException(status_code=422, detail=f'full reload requires confirm="{svc.FULL_CONFIRM}"')
    running = (await db.execute(select(QboSyncRun).where(QboSyncRun.status == RUNNING).limit(1))).scalars().first()
    if running:
        raise HTTPException(status_code=409, detail="a sync is already running")
    run = QboSyncRun(mode=body.mode, status=RUNNING, started_at=datetime.now(timezone.utc),
                     started_by=uuid.UUID(user["sub"]), counters={}, watermarks={})
    db.add(run)
    await db.commit()
    svc.launch_sync(mode=body.mode, entities=body.entities, with_attachments=body.with_attachments)
    return {"run_id": str(run.id)}
```

IMPORTANT: `launch_sync` inserts NOTHING — the router already created the RUNNING
row. But `run_sync` (in the orchestrator) ALSO creates its own QboSyncRun row.
This double-creates. RESOLVE by having `launch_sync`/the worker NOT call the
full `run_sync` (which opens its own run) — instead the trigger owns the run row.
Simplest correct approach: change the worker to call a run-agnostic path. Since
`run_sync` manages its own row, do NOT pre-create the row in the router; instead
have the router return the run created by the worker. But the worker is async/
detached. **Chosen resolution:** the router does NOT create a QboSyncRun; it only
gates (config/confirm/already-running) and calls `svc.launch_sync(...)`, and
`run_sync` inside the worker creates+owns the row. The response returns
`{"status": "started"}` (no run_id, since it's created on the thread). The UI
polls `/sync/status` for the new current_run. Update the test accordingly:
`good.status_code == 202` and assert `launched["mode"] == "full"` (no run_id
assertion). Adjust the router `trigger_sync` to drop the row creation and return
`{"status": "started"}`.

- [ ] **Step 4: Register the router** (in `app/api/v1/__init__.py`)

```python
from app.api.v1.qbo import router as qbo_router
# ...
api_router.include_router(qbo_router)
```

- [ ] **Step 5: Run → PASS** (`pytest tests/test_qbo_api.py -v`). Fix the test per
the resolution note (no run_id assertion; status 202; launch_sync monkeypatched).

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/qbo.py app/api/v1/__init__.py tests/test_qbo_api.py
git commit -m "feat(qbo): sync trigger + status + runs endpoints (view_finance)"
```

---

### Task 3: `/qbo/{entity}` paged browse + `/qbo/{entity}/{qbo_id}` detail

**Files:**
- Modify: `finance-api/app/api/v1/qbo.py`
- Test: `finance-api/tests/test_qbo_api.py`

- [ ] **Step 1: Write the failing test** (seed rows, then browse + detail + soft-delete exclusion)

```python
# add to tests/test_qbo_api.py
from app.models.qbo import QboBill, QboBillLine


@pytest.mark.asyncio
async def test_browse_and_detail_and_soft_delete_exclusion(db_session, monkeypatch):
    # seed two bills, one soft-deleted, with a line on the visible one
    from datetime import datetime, timezone
    db_session.add_all([
        QboBill(qbo_id="b1", txn_date="2019-05-01", doc_number="INV-1",
                counterparty_name="Acme", currency="CAD", total_amt=100, raw={}),
        QboBill(qbo_id="b2", txn_date="2019-05-02", counterparty_name="Beta",
                currency="CAD", total_amt=200, raw={}, deleted_at=datetime.now(timezone.utc)),
        QboBillLine(parent_qbo_id="b1", line_num=1, amount=100, account_id="141", raw={}),
    ])
    await db_session.commit()
    app.dependency_overrides[get_token_payload] = lambda: {
        "sub": "00000000-0000-0000-0000-000000000001", "role": "system_admin"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        lst = await c.get("/finance/v1/qbo/bills", headers={"Authorization": "Bearer x"})
        detail = await c.get("/finance/v1/qbo/bills/b1", headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    body = lst.json()
    assert body["total"] == 1                       # soft-deleted b2 excluded
    assert body["items"][0]["qbo_id"] == "b1"
    d = detail.json()
    assert d["header"]["qbo_id"] == "b1"
    assert len(d["lines"]) == 1
    assert d["lines"][0]["account_id"] == "141"
```

- [ ] **Step 2: Run → FAIL** (404 on the browse route).

- [ ] **Step 3: Add browse + detail to the router**

```python
# add near the top of app/api/v1/qbo.py
from app.models.qbo import (
    QboAccount, QboBillLine, QboBillPayment, QboBillPaymentLine, QboCreditMemo,
    QboCreditMemoLine, QboDeposit, QboDepositLine, QboInvoice, QboInvoiceLine,
    QboJournalEntry, QboJournalEntryLine, QboPayment, QboPaymentLine, QboPurchase,
    QboPurchaseLine, QboTransfer, QboVendor, QboVendorCredit, QboVendorCreditLine,
    QboAttachmentLink,
)

# slug -> (header_model, line_model|None)
_ENTITIES = {
    "vendors": (QboVendor, None), "accounts": (QboAccount, None),
    "bills": (QboBill, QboBillLine), "bill-payments": (QboBillPayment, QboBillPaymentLine),
    "vendor-credits": (QboVendorCredit, QboVendorCreditLine),
    "purchases": (QboPurchase, QboPurchaseLine), "invoices": (QboInvoice, QboInvoiceLine),
    "payments": (QboPayment, QboPaymentLine), "credit-memos": (QboCreditMemo, QboCreditMemoLine),
    "deposits": (QboDeposit, QboDepositLine), "transfers": (QboTransfer, None),
    "journal-entries": (QboJournalEntry, QboJournalEntryLine),
}


def _row_dict(obj) -> dict:
    """Serialize a mirror ORM row to JSON-safe primitives (Decimals→float)."""
    from decimal import Decimal
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.name)
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, datetime):
            v = v.isoformat()
        out[col.name] = v
    return out


def _resolve(entity: str):
    if entity not in _ENTITIES:
        raise HTTPException(status_code=404, detail=f"unknown entity: {entity}")
    return _ENTITIES[entity]


@router.get("/{entity}")
async def browse(entity: str, user: CurrentUser, db: AsyncSession = Depends(get_db),
                 page: int = 1, page_size: int = 50,
                 date_from: str | None = None, date_to: str | None = None,
                 q: str | None = None):
    model, _ = _resolve(entity)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conds = []
    if hasattr(model, "deleted_at"):
        conds.append(model.deleted_at.is_(None))
    if date_from and hasattr(model, "txn_date"):
        conds.append(model.txn_date >= date_from)
    if date_to and hasattr(model, "txn_date"):
        conds.append(model.txn_date <= date_to)
    if q:
        like = f"%{q}%"
        ors = []
        if hasattr(model, "counterparty_name"):
            ors.append(model.counterparty_name.ilike(like))
        if hasattr(model, "doc_number"):
            ors.append(model.doc_number.ilike(like))
        if hasattr(model, "display_name"):
            ors.append(model.display_name.ilike(like))
        if hasattr(model, "name"):
            ors.append(model.name.ilike(like))
        if ors:
            from sqlalchemy import or_
            conds.append(or_(*ors))
    from sqlalchemy import func
    total = (await db.execute(select(func.count()).select_from(model).where(*conds))).scalar()
    order = model.txn_date.desc() if hasattr(model, "txn_date") else model.qbo_id
    rows = (await db.execute(select(model).where(*conds).order_by(order)
            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [_row_dict(r) for r in rows], "total": total,
            "page": page, "page_size": page_size}


@router.get("/{entity}/{qbo_id}")
async def detail(entity: str, qbo_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    model, line_model = _resolve(entity)
    header = (await db.execute(select(model).where(model.qbo_id == qbo_id))).scalars().first()
    if not header:
        raise HTTPException(status_code=404, detail="not found")
    lines = []
    if line_model is not None:
        lrows = (await db.execute(select(line_model)
                 .where(line_model.parent_qbo_id == qbo_id)
                 .order_by(line_model.id))).scalars().all()
        lines = [_row_dict(r) for r in lrows]
    att = (await db.execute(select(QboAttachmentLink)
           .where(QboAttachmentLink.txn_id == qbo_id))).scalars().all()
    return {"header": _row_dict(header), "lines": lines,
            "attachments": [{"attachment_qbo_id": a.attachment_qbo_id,
                             "txn_type": a.txn_type} for a in att]}
```

Note: place `/sync/status`, `/sync/runs`, `/sync` BEFORE the `/{entity}` routes so
FastAPI doesn't match "sync" as an entity. In FastAPI, fixed paths take priority
over path-params regardless of order, but keep sync routes above for clarity — and
`_resolve` rejects "sync" with 404 anyway (it's not in `_ENTITIES`).

- [ ] **Step 4: Run → PASS** (`pytest tests/test_qbo_api.py -v`).

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/qbo.py tests/test_qbo_api.py
git commit -m "feat(qbo): paged entity browse + detail (soft-delete excluded)"
```

---

### Task 4: `/qbo/attachments/{qbo_id}/file` stream

**Files:**
- Modify: `finance-api/app/api/v1/qbo.py`
- Test: `finance-api/tests/test_qbo_api.py`

- [ ] **Step 1: Failing test**

```python
# add to tests/test_qbo_api.py
from app.models.qbo import QboAttachment


@pytest.mark.asyncio
async def test_attachment_file_stream(db_session):
    db_session.add(QboAttachment(qbo_id="a1", file_name="x.pdf",
                   content_type="application/pdf", size=5, content=b"%PDF!", raw={}))
    await db_session.commit()
    app.dependency_overrides[get_token_payload] = lambda: {
        "sub": "00000000-0000-0000-0000-000000000001", "role": "system_admin"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/finance/v1/qbo/attachments/a1/file", headers={"Authorization": "Bearer x"})
    app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content == b"%PDF!"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Add the endpoint**

```python
# add to app/api/v1/qbo.py
from fastapi import Response
from app.models.qbo import QboAttachment


@router.get("/attachments/{qbo_id}/file")
async def attachment_file(qbo_id: str, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    a = (await db.execute(select(QboAttachment).where(QboAttachment.qbo_id == qbo_id))).scalars().first()
    if not a or a.content is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    return Response(content=a.content, media_type=a.content_type or "application/octet-stream",
                    headers={"Content-Disposition": f'inline; filename="{a.file_name or qbo_id}"'})
```
Ensure this route is registered before `/{entity}/{qbo_id}` won't capture it —
`attachments` is not in `_ENTITIES`, and this fixed 2-segment path is fine.

- [ ] **Step 4: Run → PASS**, then full backend qbo suite:
`pytest tests/test_qbo_api.py tests/test_qbo_sync_service.py -v`.

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/qbo.py tests/test_qbo_api.py
git commit -m "feat(qbo): attachment file stream endpoint"
```

---

### Task 5: Frontend `qboApi` service + types

**Files:**
- Create: `finance/src/services/qboApi.ts`

- [ ] **Step 1: Write the service** (no test; typecheck is the gate)

```typescript
// finance/src/services/qboApi.ts
import { financeApi } from '@/lib/api'

export interface QboRun {
  id: string; mode: string; status: string
  started_at: string | null; finished_at: string | null
  counters: Record<string, { inserted: number; updated: number; deleted?: number }>
  watermarks: Record<string, string | null>; error: string | null
}
export interface QboStatus {
  can_sync: boolean; configured: boolean
  current_run: QboRun | null; last_run: QboRun | null
}
export interface QboPage<T = Record<string, unknown>> {
  items: T[]; total: number; page: number; page_size: number
}
export interface QboDetail {
  header: Record<string, unknown>
  lines: Record<string, unknown>[]
  attachments: { attachment_qbo_id: string; txn_type: string | null }[]
}

export const ENTITY_TABS: { slug: string; label: string }[] = [
  { slug: 'bills', label: 'Bills' },
  { slug: 'bill-payments', label: 'Bill Payments' },
  { slug: 'vendor-credits', label: 'Vendor Credits' },
  { slug: 'purchases', label: 'Purchases' },
  { slug: 'invoices', label: 'Invoices' },
  { slug: 'payments', label: 'Payments' },
  { slug: 'credit-memos', label: 'Credit Memos' },
  { slug: 'deposits', label: 'Deposits' },
  { slug: 'transfers', label: 'Transfers' },
  { slug: 'journal-entries', label: 'Journal Entries' },
  { slug: 'vendors', label: 'Vendors' },
  { slug: 'accounts', label: 'Accounts' },
]

export const qboApi = {
  status: () => financeApi.get<QboStatus>('/qbo/sync/status'),
  triggerSync: (mode: 'full' | 'incremental', confirm?: string) =>
    financeApi.post<{ status: string }>('/qbo/sync', { mode, confirm }),
  browse: (entity: string, params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') qs.set(k, String(v)) })
    return financeApi.get<QboPage>(`/qbo/${entity}?${qs.toString()}`)
  },
  detail: (entity: string, id: string) => financeApi.get<QboDetail>(`/qbo/${entity}/${id}`),
  fileUrl: (attachmentId: string) => `/qbo/attachments/${attachmentId}/file`,
}
```

- [ ] **Step 2: Typecheck**

Run: `cd finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no new errors from this file (baseline may have pre-existing errors; the
new file must not add any).

- [ ] **Step 3: Commit**

```bash
git add finance/src/services/qboApi.ts
git commit -m "feat(qbo): frontend qboApi service + entity tab config"
```

---

### Task 6: Sync panel + page shell + nav/route wiring

**Files:**
- Create: `finance/src/pages/finance/QboMirrorPage.tsx`
- Modify: `finance/src/app/routes.ts` (register `/finance/qbo`)
- Modify: `finance/src/components/layout/AppLayout.tsx` (nav item)

- [ ] **Step 1: Add the nav item** — in `AppLayout.tsx`'s `NAV` finance section,
add (pick an existing imported Lucide icon, e.g. `RefreshCw` — import it if needed):
```typescript
      { label: 'QuickBooks', href: '/finance/qbo', icon: RefreshCw, permission: 'view_finance' },
```

- [ ] **Step 2: Register the route** — in `finance/src/app/routes.ts`, follow the
existing `financeRoutes` entry pattern (read a peer entry like AccountsPayablePage)
and add an entry mapping `/finance/qbo` to a lazy import of `QboMirrorPage`. Match
the exact shape the other entries use (path, element/lazy, tab meta).

- [ ] **Step 3: Write the page with the sync panel** (tabs added in Task 7)

```tsx
// finance/src/pages/finance/QboMirrorPage.tsx
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { qboApi, ENTITY_TABS } from '@/services/qboApi'

export default function QboMirrorPage() {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const { data: status } = useQuery({
    queryKey: ['qbo-status'],
    queryFn: qboApi.status,
    refetchInterval: (q) => (q.state.data?.current_run ? 2000 : false),
  })
  const running = status?.current_run ?? null

  async function trigger(mode: 'full' | 'incremental') {
    await qboApi.triggerSync(mode, mode === 'full' ? 'RELOAD' : undefined)
    await qc.invalidateQueries({ queryKey: ['qbo-status'] })
    setConfirming(false)
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-xl font-semibold">QuickBooks Mirror</h1>
        <p className="text-sm text-gray-500">
          Read-only copy of the QuickBooks Online company, synced into UniOps.
        </p>
      </div>

      <section className="rounded border p-4 space-y-3">
        <div className="flex items-center gap-3">
          <button
            className="rounded bg-blue-600 px-3 py-1.5 text-white disabled:opacity-50"
            disabled={!!running || !status?.configured}
            onClick={() => trigger('incremental')}
          >Incremental sync</button>
          <button
            className="rounded border px-3 py-1.5 disabled:opacity-50"
            disabled={!!running || !status?.configured}
            onClick={() => setConfirming(true)}
          >Full reload…</button>
          {!status?.configured && <span className="text-sm text-red-600">QBO not configured</span>}
        </div>

        {running && (
          <div className="text-sm">
            <span className="font-medium">Running ({running.mode})…</span>
            <ul className="mt-1 grid grid-cols-2 gap-x-6 gap-y-0.5 md:grid-cols-3">
              {Object.entries(running.counters).map(([k, v]) => (
                <li key={k} className="text-gray-600">
                  {k}: +{v.inserted} ~{v.updated}{v.deleted ? ` -${v.deleted}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
        {!running && status?.last_run && (
          <div className="text-sm text-gray-600">
            Last sync: {status.last_run.mode} · {status.last_run.status}
            {status.last_run.finished_at ? ` · ${new Date(status.last_run.finished_at).toLocaleString()}` : ''}
            {status.last_run.error ? <span className="text-red-600"> · {status.last_run.error}</span> : null}
          </div>
        )}
      </section>

      {confirming && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-96 space-y-3 rounded bg-white p-4">
            <h2 className="font-semibold">Full reload</h2>
            <p className="text-sm text-gray-600">
              Re-pulls every entity from QuickBooks. This can take many minutes. Continue?
            </p>
            <div className="flex justify-end gap-2">
              <button className="rounded border px-3 py-1.5" onClick={() => setConfirming(false)}>Cancel</button>
              <button className="rounded bg-red-600 px-3 py-1.5 text-white" onClick={() => trigger('full')}>Full reload</button>
            </div>
          </div>
        </div>
      )}

      {/* Entity tabs added in Task 7 */}
      <QboTabs />
    </div>
  )
}

function QboTabs() {
  return null  // replaced in Task 7
}
```

- [ ] **Step 4: Typecheck + commit**

Run: `cd finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
```bash
git add finance/src/pages/finance/QboMirrorPage.tsx finance/src/app/routes.ts \
        finance/src/components/layout/AppLayout.tsx
git commit -m "feat(qbo): Finance QuickBooks page — sync panel + nav/route"
```

---

### Task 7: Entity tabs (paged table) + detail modal + attachments

**Files:**
- Modify: `finance/src/pages/finance/QboMirrorPage.tsx`

- [ ] **Step 1: Replace `QboTabs` with the real tabbed browser + detail modal**

```tsx
// replace the QboTabs stub in QboMirrorPage.tsx
import { createPortal } from 'react-dom'
import type { QboDetail } from '@/services/qboApi'

function num(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  return Number.isNaN(n) ? String(v) : n.toFixed(2)
}

function QboTabs() {
  const [tab, setTab] = useState(ENTITY_TABS[0].slug)
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [openId, setOpenId] = useState<string | null>(null)

  const { data } = useQuery({
    queryKey: ['qbo-browse', tab, q, page],
    queryFn: () => qboApi.browse(tab, { q, page, page_size: 50 }),
  })
  const items = data?.items ?? []
  const cols = items[0] ? Object.keys(items[0]).filter((c) => c !== 'raw') : []

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap gap-1 border-b">
        {ENTITY_TABS.map((t) => (
          <button key={t.slug}
            className={`px-3 py-1.5 text-sm ${tab === t.slug ? 'border-b-2 border-blue-600 font-medium' : 'text-gray-500'}`}
            onClick={() => { setTab(t.slug); setPage(1); setOpenId(null) }}>{t.label}</button>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <input className="rounded border px-2 py-1 text-sm" placeholder="Search name / doc #"
          value={q} onChange={(e) => { setQ(e.target.value); setPage(1) }} />
        <span className="text-sm text-gray-500">{data?.total ?? 0} rows</span>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead><tr className="border-b text-left text-gray-500">
            {cols.map((c) => <th key={c} className="px-2 py-1 font-medium">{c}</th>)}
          </tr></thead>
          <tbody>
            {items.map((row) => (
              <tr key={String(row.qbo_id)} className="cursor-pointer border-b hover:bg-gray-50"
                onClick={() => setOpenId(String(row.qbo_id))}>
                {cols.map((c) => (
                  <td key={c} className="px-2 py-1">
                    {c.includes('amt') || c.includes('balance') || c === 'exchange_rate'
                      ? num(row[c]) : String(row[c] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex gap-2 text-sm">
        <button className="rounded border px-2 py-1 disabled:opacity-50" disabled={page <= 1}
          onClick={() => setPage((p) => p - 1)}>Prev</button>
        <span className="px-2 py-1">Page {page}</span>
        <button className="rounded border px-2 py-1 disabled:opacity-50"
          disabled={(data?.total ?? 0) <= page * 50} onClick={() => setPage((p) => p + 1)}>Next</button>
      </div>

      {openId && <DetailModal entity={tab} id={openId} onClose={() => setOpenId(null)} />}
    </section>
  )
}

function DetailModal({ entity, id, onClose }: { entity: string; id: string; onClose: () => void }) {
  const { data } = useQuery<QboDetail>({
    queryKey: ['qbo-detail', entity, id],
    queryFn: () => qboApi.detail(entity, id),
  })
  const FINANCE_API = (import.meta.env.VITE_FINANCE_API_URL as string | undefined) || 'http://localhost:8004'
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="max-h-[85vh] w-[52rem] overflow-auto rounded bg-white p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold">{entity} · {id}</h2>
          <button className="text-gray-500" onClick={onClose}>✕</button>
        </div>
        {data && (
          <>
            <table className="mb-4 text-sm">
              <tbody>
                {Object.entries(data.header).filter(([k]) => k !== 'raw').map(([k, v]) => (
                  <tr key={k}><td className="pr-4 text-gray-500">{k}</td><td>{String(v ?? '')}</td></tr>
                ))}
              </tbody>
            </table>
            {data.lines.length > 0 && (
              <>
                <h3 className="mb-1 font-medium">Lines</h3>
                <table className="mb-4 min-w-full text-sm">
                  <thead><tr className="border-b text-left text-gray-500">
                    {Object.keys(data.lines[0]).filter((c) => c !== 'raw').map((c) => (
                      <th key={c} className="px-2 py-1 font-medium">{c}</th>))}
                  </tr></thead>
                  <tbody>
                    {data.lines.map((ln, i) => (
                      <tr key={i} className="border-b">
                        {Object.keys(data.lines[0]).filter((c) => c !== 'raw').map((c) => (
                          <td key={c} className="px-2 py-1">{String(ln[c] ?? '')}</td>))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            {data.attachments.length > 0 && (
              <>
                <h3 className="mb-1 font-medium">Attachments</h3>
                <ul className="text-sm">
                  {data.attachments.map((a) => (
                    <li key={a.attachment_qbo_id}>
                      <a className="text-blue-600 underline"
                        href={`${FINANCE_API}/finance/v1${qboApi.fileUrl(a.attachment_qbo_id)}`}
                        target="_blank" rel="noreferrer">{a.attachment_qbo_id}</a>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
    </div>, document.body)
}
```

- [ ] **Step 2: Typecheck**

Run: `cd finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no NEW errors (compare to the pre-existing baseline count).

- [ ] **Step 3: Commit**

```bash
git add finance/src/pages/finance/QboMirrorPage.tsx
git commit -m "feat(qbo): entity tabs, paged table, detail modal, attachments"
```

---

### Task 8: Dev import + manual UI verification

Manual (no automated test). Populates a dev DB and eyeballs the page.

- [ ] **Step 1: Load real-shaped data into the finance DEV DB (read-only QBO)**

Run the importer against the dev finance DB (NOT production; the dev compose DB or
finance_test with migrations applied), e.g. inside the dev finance-api container or
with an explicit `DATABASE_URL` for the local dev DB:
```
python scripts/qbo_import/run_import.py --full --entities Vendor,Bill,BillPayment,Invoice,Payment,JournalEntry
```
Expected: `full sync success` with per-entity counts.

- [ ] **Step 2: Start the finance frontend against that DB and verify**

With the dev stack up, open `/finance/qbo`: confirm the sync panel shows "Last
sync", the Bills/Invoices/etc. tabs list rows, a row opens the detail modal with
lines, and (if any attachments loaded) the download link streams the file.

- [ ] **Step 3: Record the verification** in the PR/summary. No commit.

---

## Self-review notes

- **Spec coverage:** sync trigger/status/runs (T2), browse+detail (T3), attachment
  stream (T4), qboApi (T5), nav+route+sync panel (T6), tabs+detail modal (T7),
  dev verify (T8). Background-thread execution (T1). Server-side is auth-only
  (`CurrentUser`) matching finance-api reads; `view_finance` gates client-side via
  the nav item (T6).
- **Run-row ownership resolution:** the router does NOT pre-create a QboSyncRun;
  `run_sync` (on the worker thread) creates and owns it. The trigger returns
  `{status:"started"}`; the UI discovers the new run via `/sync/status`. This
  avoids the double-row bug and is called out explicitly in Task 2 Step 3.
- **No placeholders:** every endpoint, model map, and component is written out.
  The two frontend wiring points that depend on peer patterns (routes.ts entry,
  nav icon import) instruct the implementer to copy the exact in-repo shape rather
  than guessing — the surrounding code is cited by file.
- **Type consistency:** `qboApi` shapes (QboStatus/QboRun/QboPage/QboDetail) match
  the router's JSON (`_run_out`, browse `{items,total,page,page_size}`, detail
  `{header,lines,attachments}`). Entity slugs in `ENTITY_TABS` ⊆ `_ENTITIES` keys.
- **Conventions:** Decimal→float on the backend (`_row_dict`) AND `Number()` on
  the frontend (`num`); detail modal uses `createPortal` to body; English-only copy.

## Out of scope

- Production deployment of migrations 0027+0028 and the real full import (separate
  follow-up, per the UI-first decision).
- Browse tabs for the 11 long-tail `qbo_raw` masters.
- Association with NC65 / finance AP; editing QBO data.
