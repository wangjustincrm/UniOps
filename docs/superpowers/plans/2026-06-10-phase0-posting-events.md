# Phase 0-B1: posting_events 记账事件脊柱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立全系统统一的记账事件表(posting_events / posting_lines,finance-api 拥有),并在两个真实资金流出点接上发射:PA 打款(approval-api engine 的 `process` 动作)和报销打款(expense-api 的 `process_pay`),为未来 AP/AR/FA/GL 模块积累可回放的会计事件。

**Architecture:** finance-api 通过 alembic 迁移拥有两张表;发射方(approval-api、expense-api)各持有一份轻量镜像模型 + 一个纯函数 `emit_event()` 帮助器,在各自的业务事务内直接 INSERT(与现有 tasks 表的共享库模式一致)。幂等靠 `UNIQUE(source_doc_type, source_doc_id, event_type)` + `ON CONFLICT DO NOTHING`。无跨服务 FK、无消息队列。

**Tech Stack:** FastAPI / SQLAlchemy 2 async / Alembic / pytest + pytest-asyncio / PostgreSQL(测试用本地 docker `uniops_postgres`,localhost:5432, user `epms` / `epms_dev`)

**前置条件:** 本地 docker 栈在跑(`docker compose -f docker-compose.dev.yml up postgres`)。测试一律连**本地容器**,不连 10.10.50.20。

---

## 表结构(本计划的契约,所有任务遵守)

```
posting_events
  id                 UUID PK default gen
  source_service     VARCHAR(20)  NOT NULL          -- 'epms' | 'oa' | 'finance' | ...
  source_doc_type    VARCHAR(30)  NOT NULL          -- 'pa' | 'pa_dir' | 'exp' | 'mil' | 'trv' | 'cfm' | ...
  source_doc_id      UUID         NOT NULL
  source_doc_number  VARCHAR(40)  NOT NULL
  event_type         VARCHAR(30)  NOT NULL          -- 'payment' | 'expense_paid' | ...
  occurred_at        TIMESTAMPTZ  NOT NULL
  status             VARCHAR(10)  NOT NULL default 'pending'   -- pending | posted | void
  created_at / updated_at TIMESTAMPTZ
  UNIQUE uq_posting_events_source (source_doc_type, source_doc_id, event_type)
  INDEX  ix_posting_events_doc (source_doc_type, source_doc_id)

posting_lines
  id             UUID PK
  event_id       UUID NOT NULL FK posting_events.id ON DELETE CASCADE (indexed)
  line_no        INTEGER      NOT NULL
  line_role      VARCHAR(30)  NOT NULL              -- 'accounts_payable' | 'bank' | 'employee_expense' | 'sales_tax'
  account_code   VARCHAR(20)  NULL                  -- COA 未建,GL 阶段回填
  cost_center_id UUID         NULL                  -- 裸 UUID,无 FK(松耦合,设计决议)
  partner_id     UUID         NULL                  -- 今天存 vendor_id;B3 后语义升级为 business_partner id
  partner_name   VARCHAR(255) NULL
  debit          NUMERIC(15,2) NOT NULL default 0
  credit         NUMERIC(15,2) NOT NULL default 0
  tax_code       VARCHAR(20)  NULL                  -- B2 税码主数据落地后回填
  currency       VARCHAR(10)  NOT NULL default 'CAD'
  fx_rate        NUMERIC(12,6) NOT NULL default 1
  memo           VARCHAR(255) NULL
  created_at / updated_at TIMESTAMPTZ
  CHECK ck_posting_lines_one_side: NOT (debit > 0 AND credit > 0)
```

`emit_event()` 帮助器契约(三个服务中的实现完全一致):

```python
async def emit_event(
    db, *,
    source_service: str,
    source_doc_type: str,
    source_doc_id: uuid.UUID,
    source_doc_number: str,
    event_type: str,
    lines: list[dict],          # keys: line_role(必填), debit, credit, partner_id,
                                #       partner_name, cost_center_id, tax_code,
                                #       currency, fx_rate, memo
    occurred_at: datetime | None = None,
) -> uuid.UUID | None           # None = 该 (doc_type, doc_id, event_type) 已发射过(幂等跳过)
```

规则:debit、credit 同时为 0 的行**静默丢弃**;事件无有效行时不插入事件、返回 None。

---

### Task 1: finance-api — 测试基建 + posting 模型 + 迁移 0002

**Files:**
- Create: `finance-api/requirements-dev.txt`
- Create: `finance-api/tests/__init__.py`(空文件)
- Create: `finance-api/tests/conftest.py`
- Create: `finance-api/tests/test_posting_models.py`
- Create: `finance-api/app/models/posting.py`
- Modify: `finance-api/app/main.py:7`(模型注册 import)
- Create: `finance-api/alembic/versions/0002_create_posting_events.py`

- [ ] **Step 1: 建虚拟环境并装依赖**

```bash
cd /c/Project/uniops/finance-api
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

创建 `finance-api/requirements-dev.txt`:

```
pytest==8.3.4
pytest-asyncio==0.24.0
psycopg2-binary==2.9.10
```

```bash
.venv/Scripts/pip install -r requirements-dev.txt
```

- [ ] **Step 2: 写 conftest(照搬 budget-api 模式:真 alembic 建测试库)**

创建 `finance-api/tests/__init__.py`(空文件)和 `finance-api/tests/conftest.py`:

```python
"""Test fixtures for finance-api.

Schema is built by running the real alembic migrations against a dedicated
local Postgres database (default: finance_test on the uniops_postgres docker
container). Mirrors budget-api/tests/conftest.py.

NOTE: migration 0001 has a FK to payment_applications (an epms-api table),
so we pre-create a one-column stub before running alembic.
"""
import os
import subprocess
import sys

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_DB = os.getenv("TEST_FINANCE_DB", "finance_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
ADMIN_SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/postgres"


def _ensure_db():
    eng = sa.create_engine(ADMIN_SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB}"'))
    eng.dispose()


def _migrate():
    """Reset public schema, stub cross-service FK targets, run alembic upgrade head."""
    _ensure_db()
    eng = sa.create_engine(SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        # 0001_payment_records FKs payment_applications (owned by epms-api)
        conn.execute(sa.text("CREATE TABLE payment_applications (id UUID PRIMARY KEY)"))
    eng.dispose()

    env = dict(os.environ)
    env["DATABASE_URL"] = ASYNC_URL
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root, env=env, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_session():
    _migrate()
    engine = create_async_engine(ASYNC_URL, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
```

> 已确认:`finance-api/alembic/env.py` 读 `settings.database_url`(pydantic-settings),
> 环境变量优先于 .env 文件,所以 `env["DATABASE_URL"] = ASYNC_URL` 可靠生效。

并创建 `finance-api/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 3: 写失败测试(模型往返 + 幂等约束)**

创建 `finance-api/tests/test_posting_models.py`:

```python
"""posting_events / posting_lines schema + constraint tests."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.posting import PostingEvent, PostingLine


def _event(**over) -> PostingEvent:
    kw = dict(
        source_service="epms",
        source_doc_type="pa",
        source_doc_id=uuid.uuid4(),
        source_doc_number="PA-2026-0001",
        event_type="payment",
        occurred_at=datetime.now(timezone.utc),
    )
    kw.update(over)
    return PostingEvent(**kw)


async def test_event_and_lines_roundtrip(db_session):
    ev = _event()
    db_session.add(ev)
    await db_session.flush()
    db_session.add_all([
        PostingLine(event_id=ev.id, line_no=1, line_role="accounts_payable",
                    debit=Decimal("100.00"), partner_name="ACME Inc"),
        PostingLine(event_id=ev.id, line_no=2, line_role="bank",
                    credit=Decimal("100.00")),
    ])
    await db_session.flush()

    rows = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id).order_by(PostingLine.line_no)
    )).scalars().all()
    assert len(rows) == 2
    assert rows[0].currency == "CAD"          # server default
    assert rows[0].fx_rate == Decimal("1")    # server default
    assert ev.status == "pending"


async def test_unique_source_constraint(db_session):
    doc_id = uuid.uuid4()
    db_session.add(_event(source_doc_id=doc_id))
    await db_session.flush()
    db_session.add(_event(source_doc_id=doc_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_line_cannot_have_both_sides(db_session):
    ev = _event()
    db_session.add(ev)
    await db_session.flush()
    db_session.add(PostingLine(event_id=ev.id, line_no=1, line_role="bank",
                               debit=Decimal("1.00"), credit=Decimal("1.00")))
    with pytest.raises(IntegrityError):
        await db_session.flush()
```

- [ ] **Step 4: 跑测试确认失败**

```bash
cd /c/Project/uniops/finance-api
.venv/Scripts/python -m pytest tests/test_posting_models.py -v
```

预期:FAIL — `ModuleNotFoundError: No module named 'app.models.posting'`

- [ ] **Step 5: 写模型**

创建 `finance-api/app/models/posting.py`:

```python
"""Posting events — the accounting spine (Phase 0-B1).

finance-api OWNS these tables (alembic 0002). approval-api and expense-api
hold thin mirrors and INSERT within their own transactions. No cross-service
FKs by design: partner_id / cost_center_id are bare UUIDs.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PostingEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "posting_events"
    __table_args__ = (
        UniqueConstraint("source_doc_type", "source_doc_id", "event_type",
                         name="uq_posting_events_source"),
        Index("ix_posting_events_doc", "source_doc_type", "source_doc_id"),
    )

    source_service: Mapped[str] = mapped_column(String(20), nullable=False)
    source_doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_doc_number: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")


class PostingLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "posting_lines"
    __table_args__ = (
        CheckConstraint("NOT (debit > 0 AND credit > 0)", name="ck_posting_lines_one_side"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posting_events.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    line_role: Mapped[str] = mapped_column(String(30), nullable=False)
    account_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, server_default=text("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, server_default=text("0"))
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default="CAD")
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default=text("1"))
    memo: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

修改 `finance-api/app/main.py` 第 7 行的模型注册 import:

```python
from app.models import pa, payment, posting  # noqa: F401 — register with metadata
```

- [ ] **Step 6: 写迁移 0002**

创建 `finance-api/alembic/versions/0002_create_posting_events.py`:

```python
"""create posting_events and posting_lines

Revision ID: 0002_posting_events
Revises: 0001_payment_records
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_posting_events"
down_revision = "0001_payment_records"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "posting_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_service", sa.String(20), nullable=False),
        sa.Column("source_doc_type", sa.String(30), nullable=False),
        sa.Column("source_doc_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_doc_number", sa.String(40), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_doc_type", "source_doc_id", "event_type",
                            name="uq_posting_events_source"),
    )
    op.create_index("ix_posting_events_doc", "posting_events",
                    ["source_doc_type", "source_doc_id"])

    op.create_table(
        "posting_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True),
                  sa.ForeignKey("posting_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer, nullable=False),
        sa.Column("line_role", sa.String(30), nullable=False),
        sa.Column("account_code", sa.String(20), nullable=True),
        sa.Column("cost_center_id", UUID(as_uuid=True), nullable=True),
        sa.Column("partner_id", UUID(as_uuid=True), nullable=True),
        sa.Column("partner_name", sa.String(255), nullable=True),
        sa.Column("debit", sa.Numeric(15, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("credit", sa.Numeric(15, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("fx_rate", sa.Numeric(12, 6), nullable=False, server_default=sa.text("1")),
        sa.Column("memo", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("NOT (debit > 0 AND credit > 0)", name="ck_posting_lines_one_side"),
    )
    op.create_index("ix_posting_lines_event_id", "posting_lines", ["event_id"])


def downgrade():
    op.drop_table("posting_lines")
    op.drop_table("posting_events")
```

> 模型里 `UUIDPrimaryKey` 带 Python 端 default(uuid4),迁移不需要 server_default,
> 与 0001_payment_records 的写法一致。

- [ ] **Step 7: 跑测试确认通过**

```bash
cd /c/Project/uniops/finance-api
.venv/Scripts/python -m pytest tests/test_posting_models.py -v
```

预期:3 passed

- [ ] **Step 8: Commit**

```bash
cd /c/Project/uniops
git add finance-api/app/models/posting.py finance-api/app/main.py \
        finance-api/alembic/versions/0002_create_posting_events.py \
        finance-api/tests/ finance-api/pytest.ini finance-api/requirements-dev.txt
git commit -m "feat(finance-posting): posting_events/posting_lines tables + test infra (Phase 0-B1)"
```

---

### Task 2: finance-api — 事件查询 API

**Files:**
- Create: `finance-api/app/schemas/posting.py`
- Create: `finance-api/app/api/v1/posting.py`
- Modify: `finance-api/app/api/v1/__init__.py`
- Test: `finance-api/tests/test_posting_api.py`

- [ ] **Step 1: 写失败测试**

创建 `finance-api/tests/test_posting_api.py`:

```python
"""GET /finance/v1/posting/events — query API tests."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.posting import PostingEvent, PostingLine


def _token(role: str = "finance_manager") -> str:
    payload = {
        "sub": str(uuid.uuid4()), "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_list_events_by_doc(client, db_session):
    doc_id = uuid.uuid4()
    ev = PostingEvent(
        source_service="epms", source_doc_type="pa", source_doc_id=doc_id,
        source_doc_number="PA-2026-0042", event_type="payment",
        occurred_at=datetime.now(timezone.utc),
    )
    db_session.add(ev)
    await db_session.flush()
    db_session.add(PostingLine(event_id=ev.id, line_no=1, line_role="bank",
                               credit=Decimal("55.00")))
    await db_session.flush()

    r = await client.get(
        "/finance/v1/posting/events",
        params={"source_doc_type": "pa", "source_doc_id": str(doc_id)},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["source_doc_number"] == "PA-2026-0042"
    assert len(body[0]["lines"]) == 1
    assert body[0]["lines"][0]["credit"] == "55.00"


async def test_requires_auth(client):
    r = await client.get("/finance/v1/posting/events")
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /c/Project/uniops/finance-api
.venv/Scripts/python -m pytest tests/test_posting_api.py -v
```

预期:FAIL — 404(路由不存在)

- [ ] **Step 3: 写 schema + 路由**

创建 `finance-api/app/schemas/posting.py`:

```python
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PostingLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_no: int
    line_role: str
    account_code: str | None
    cost_center_id: uuid.UUID | None
    partner_id: uuid.UUID | None
    partner_name: str | None
    debit: Decimal
    credit: Decimal
    tax_code: str | None
    currency: str
    fx_rate: Decimal
    memo: str | None


class PostingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_service: str
    source_doc_type: str
    source_doc_id: uuid.UUID
    source_doc_number: str
    event_type: str
    occurred_at: datetime
    status: str
    lines: list[PostingLineOut]
```

创建 `finance-api/app/api/v1/posting.py`:

```python
"""Posting events query API — read-only window into the accounting spine."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.posting import PostingEvent, PostingLine
from app.schemas.posting import PostingEventOut, PostingLineOut

router = APIRouter(prefix="/posting", tags=["posting"])


@router.get("/events", response_model=list[PostingEventOut])
async def list_events(
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    source_doc_type: str | None = Query(default=None),
    source_doc_id: uuid.UUID | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    q = select(PostingEvent).order_by(PostingEvent.occurred_at.desc()).limit(limit)
    if source_doc_type:
        q = q.where(PostingEvent.source_doc_type == source_doc_type)
    if source_doc_id:
        q = q.where(PostingEvent.source_doc_id == source_doc_id)
    if event_type:
        q = q.where(PostingEvent.event_type == event_type)
    events = (await db.execute(q)).scalars().all()
    if not events:
        return []
    lines = (await db.execute(
        select(PostingLine)
        .where(PostingLine.event_id.in_([e.id for e in events]))
        .order_by(PostingLine.line_no)
    )).scalars().all()
    by_event: dict[uuid.UUID, list[PostingLine]] = {}
    for ln in lines:
        by_event.setdefault(ln.event_id, []).append(ln)
    return [
        PostingEventOut(
            **{c: getattr(e, c) for c in (
                "id", "source_service", "source_doc_type", "source_doc_id",
                "source_doc_number", "event_type", "occurred_at", "status")},
            lines=[PostingLineOut.model_validate(ln) for ln in by_event.get(e.id, [])],
        )
        for e in events
    ]
```

修改 `finance-api/app/api/v1/__init__.py`:

```python
from fastapi import APIRouter
from app.api.v1.budget import router as budget_router
from app.api.v1.ap import router as ap_router
from app.api.v1.payments import router as payments_router
from app.api.v1.posting import router as posting_router
from app.api.v1.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(budget_router)
api_router.include_router(ap_router)
api_router.include_router(payments_router)
api_router.include_router(posting_router)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/ -v
```

预期:Task 1 的 3 个 + Task 2 的 2 个 = 5 passed

- [ ] **Step 5: Commit**

```bash
cd /c/Project/uniops
git add finance-api/app/schemas/posting.py finance-api/app/api/v1/posting.py \
        finance-api/app/api/v1/__init__.py finance-api/tests/test_posting_api.py
git commit -m "feat(finance-posting): GET /posting/events query API"
```

---

### Task 3: expense-api — 报销打款发射点

**Files:**
- Create: `expense-api/app/models/posting_mirror.py`
- Create: `expense-api/app/services/posting.py`
- Modify: `expense-api/app/crud/expense.py:267`(process_pay 内,_book_budget 之后)
- Modify: `expense-api/tests/conftest.py:16-24`(模型 import 区)
- Test: `expense-api/tests/test_posting_emit.py`

- [ ] **Step 1: 写镜像模型**

创建 `expense-api/app/models/posting_mirror.py`:

```python
"""Write-only mirror of finance-api's posting_events / posting_lines.

Schema is OWNED by finance-api (alembic 0002_posting_events). Do not migrate
these tables from expense-api. Mirrors follow the existing pattern
(epms_mirrors.py, task_mirror.py).
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class PostingEvent(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "posting_events"
    __table_args__ = (
        # 镜像必须声明 unique 约束:测试用 create_all 建表,缺它 ON CONFLICT 会报
        # InvalidColumnReferenceError(执行中发现,生产表由 finance-api 迁移创建本就有)
        UniqueConstraint("source_doc_type", "source_doc_id", "event_type",
                         name="uq_posting_events_source"),
    )

    source_service: Mapped[str] = mapped_column(String(20), nullable=False)
    source_doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_doc_number: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")


class PostingLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "posting_lines"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posting_events.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    line_role: Mapped[str] = mapped_column(String(30), nullable=False)
    account_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("1"))
    memo: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

- [ ] **Step 2: 写 emit 帮助器**

创建 `expense-api/app/services/posting.py`:

```python
"""emit_event() — write one posting event + lines inside the caller's transaction.

Idempotent on (source_doc_type, source_doc_id, event_type) via ON CONFLICT
DO NOTHING. Lines with debit == credit == 0 are silently dropped.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.posting_mirror import PostingEvent, PostingLine

_ZERO = Decimal("0")


async def emit_event(
    db: AsyncSession,
    *,
    source_service: str,
    source_doc_type: str,
    source_doc_id: uuid.UUID,
    source_doc_number: str,
    event_type: str,
    lines: list[dict],
    occurred_at: datetime | None = None,
) -> uuid.UUID | None:
    effective = [
        ln for ln in lines
        if Decimal(str(ln.get("debit", 0))) != _ZERO or Decimal(str(ln.get("credit", 0))) != _ZERO
    ]
    if not effective:
        return None

    stmt = (
        pg_insert(PostingEvent)
        .values(
            id=uuid.uuid4(),
            source_service=source_service,
            source_doc_type=source_doc_type,
            source_doc_id=source_doc_id,
            source_doc_number=source_doc_number,
            event_type=event_type,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            status="pending",
        )
        .on_conflict_do_nothing(
            index_elements=["source_doc_type", "source_doc_id", "event_type"]
        )
        .returning(PostingEvent.id)
    )
    event_id = (await db.execute(stmt)).scalar_one_or_none()
    if event_id is None:
        return None  # already emitted — idempotent skip

    db.add_all([
        PostingLine(
            event_id=event_id,
            line_no=i + 1,
            line_role=ln["line_role"],
            account_code=ln.get("account_code"),
            cost_center_id=ln.get("cost_center_id"),
            partner_id=ln.get("partner_id"),
            partner_name=ln.get("partner_name"),
            debit=Decimal(str(ln.get("debit", 0))),
            credit=Decimal(str(ln.get("credit", 0))),
            tax_code=ln.get("tax_code"),
            currency=ln.get("currency", "CAD"),
            fx_rate=Decimal(str(ln.get("fx_rate", 1))),
            memo=ln.get("memo"),
        )
        for i, ln in enumerate(effective)
    ])
    await db.flush()
    return event_id
```

- [ ] **Step 3: 写失败测试**

创建 `expense-api/tests/test_posting_emit.py`:

```python
"""emit_event helper + process_pay emission tests."""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models.posting_mirror import PostingEvent, PostingLine
from app.services.posting import emit_event


async def test_emit_event_writes_event_and_lines(db_session):
    doc_id = uuid.uuid4()
    ev_id = await emit_event(
        db_session,
        source_service="oa", source_doc_type="exp", source_doc_id=doc_id,
        source_doc_number="EXP-2026-0001", event_type="expense_paid",
        lines=[
            {"line_role": "employee_expense", "debit": Decimal("90.00"),
             "partner_name": "Jane Doe"},
            {"line_role": "sales_tax", "debit": Decimal("0")},      # dropped
            {"line_role": "bank", "credit": Decimal("90.00")},
        ],
    )
    assert ev_id is not None
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev_id).order_by(PostingLine.line_no)
    )).scalars().all()
    assert [ln.line_role for ln in lines] == ["employee_expense", "bank"]


async def test_emit_event_is_idempotent(db_session):
    doc_id = uuid.uuid4()
    kw = dict(
        source_service="oa", source_doc_type="exp", source_doc_id=doc_id,
        source_doc_number="EXP-2026-0002", event_type="expense_paid",
        lines=[{"line_role": "bank", "credit": Decimal("10.00")}],
    )
    first = await emit_event(db_session, **kw)
    second = await emit_event(db_session, **kw)
    assert first is not None
    assert second is None
    count = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == doc_id)
    )).scalars().all()
    assert len(count) == 1


async def test_emit_event_all_zero_lines_skips(db_session):
    ev_id = await emit_event(
        db_session,
        source_service="oa", source_doc_type="exp", source_doc_id=uuid.uuid4(),
        source_doc_number="EXP-2026-0003", event_type="expense_paid",
        lines=[{"line_role": "sales_tax", "debit": Decimal("0")}],
    )
    assert ev_id is None
```

> conftest 用 `Base.metadata.create_all` 建表,所以必须把镜像模型加进
> `expense-api/tests/conftest.py` 的 import 区。
> 已确认:expense-api conftest 目前**没有** `db_session` fixture(只有 `test_engine`
> 和 client 类 fixture),需要补一个(下面一并给出)。

修改 `expense-api/tests/conftest.py`:模型 import 区加一行,并在 `test_engine`
fixture 之后补 `db_session` fixture:

```python
import app.models.posting_mirror   # noqa: F401
```

```python
@pytest.fixture
async def db_session(test_engine):
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with maker() as session:
        yield session
```

(`async_sessionmaker` 该 conftest 已 import;session 级 event loop 由现有的
`pytest_collection_modifyitems` 钩子统一处理,无需额外标记。)

- [ ] **Step 4: 跑测试确认失败 → 实现已就绪则直接通过**

```bash
cd /c/Project/uniops/expense-api
.venv/Scripts/python -m pytest tests/test_posting_emit.py -v
```

预期:3 passed(Step 1-2 已写实现;若 fixture 名不符按 conftest 调整)

- [ ] **Step 5: 接线 process_pay**

修改 `expense-api/app/crud/expense.py` 的 `process_pay`(约 267 行,`await _book_budget(...)` 之后、`db.add(ExpenseApprovalEvent(...)` 之前)插入:

```python
    from app.services.posting import emit_event as emit_posting_event
    await emit_posting_event(
        db,
        source_service="oa",
        source_doc_type=claim.claim_type.lower(),
        source_doc_id=claim.id,
        source_doc_number=claim.claim_number,
        event_type="expense_paid",
        lines=[
            {"line_role": "employee_expense", "debit": claim.net_amount,
             "partner_name": claim.employee_name, "currency": claim.currency},
            {"line_role": "sales_tax", "debit": claim.tax_amount,
             "currency": claim.currency},
            {"line_role": "bank", "credit": claim.total_amount,
             "partner_name": claim.employee_name, "currency": claim.currency},
        ],
    )
```

(import 放函数内,与该文件 `_book_budget` 的局部 import 风格一致;若文件头部 import
更符合现状,移到头部亦可——以该文件现有风格为准。)

- [ ] **Step 6: 跑 expense-api 全量测试**

```bash
.venv/Scripts/python -m pytest tests/ -v
```

预期:全部通过(原有 6 个测试文件 + 新增 1 个)。若现有 process_pay 的测试存在,
确认其断言未被破坏;若它验证了 paid 流程,可顺手加断言:posting_events 里出现一条
`expense_paid` 事件。

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops
git add expense-api/app/models/posting_mirror.py expense-api/app/services/posting.py \
        expense-api/app/crud/expense.py expense-api/tests/
git commit -m "feat(oa-posting): emit expense_paid posting event on process_pay (Phase 0-B1)"
```

---

### Task 4: approval-api — PA 打款发射点 + 最小测试基建

**Files:**
- Create: `approval-api/app/models/posting.py`(镜像,内容同 Task 3 Step 1,改 import 基类路径即可——approval-api 的 `app/db/base.py` 同样提供 `Base, TimestampMixin, UUIDPrimaryKey`)
- Create: `approval-api/app/crud/posting.py`(emit 帮助器,内容与 Task 3 Step 2 **完全一致**,仅 import 行改为 `from app.models.posting import PostingEvent, PostingLine`)
- Modify: `approval-api/app/models/pa.py`(镜像补 vendor_id、currency 两列)
- Modify: `approval-api/app/crud/engine.py:766-780`(process 分支)
- Create: `approval-api/requirements-dev.txt`、`approval-api/pytest.ini`
- Create: `approval-api/tests/__init__.py`、`approval-api/tests/conftest.py`
- Test: `approval-api/tests/test_posting_emit.py`

- [ ] **Step 1: 建测试基建(approval-api 首批测试)**

```bash
cd /c/Project/uniops/approval-api
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

创建 `approval-api/requirements-dev.txt`:

```
pytest==8.3.4
pytest-asyncio==0.24.0
psycopg2-binary==2.9.10
```

```bash
.venv/Scripts/pip install -r requirements-dev.txt
```

创建 `approval-api/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

创建 `approval-api/tests/__init__.py`(空)和 `approval-api/tests/conftest.py`:

```python
"""Minimal test fixtures for approval-api (first tests in this service).

approval-api owns no schema (it mirrors other services' tables), so we
create ONLY the posting tables it writes to, on a dedicated local DB.
Engine state-machine tests (P1) will extend this conftest.
"""
import os

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.posting import PostingEvent, PostingLine

TEST_DB = os.getenv("TEST_APPROVAL_DB", "approval_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
ADMIN_SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/postgres"

_POSTING_TABLES = [PostingEvent.__table__, PostingLine.__table__]


def _build_schema():
    admin = sa.create_engine(ADMIN_SYNC_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()

    eng = sa.create_engine(SYNC_URL)
    from app.db.base import Base
    Base.metadata.drop_all(eng, tables=_POSTING_TABLES)
    Base.metadata.create_all(eng, tables=_POSTING_TABLES)
    eng.dispose()


@pytest_asyncio.fixture
async def db_session():
    _build_schema()
    engine = create_async_engine(ASYNC_URL, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
```

> approval-api 的 `Settings` 要求 `database_url`/`jwt_secret_key` 必填(无默认值),
> 而导入 `app.models.posting` 会触发 `app.db.base` → `app.core.config` 加载。
> approval-api 目录下已有 `.env`(此前确认存在),pydantic-settings 会读它,测试可直接跑;
> 若 CI 环境无 .env,需 export 这两个变量。

- [ ] **Step 2: 写镜像模型 + 帮助器**

创建 `approval-api/app/models/posting.py` — 内容与 Task 3 Step 1 的
`expense-api/app/models/posting_mirror.py` 完全相同(docstring 中 "expense-api"
改为 "approval-api";基类 import `from app.db.base import Base, TimestampMixin, UUIDPrimaryKey`
路径相同,无需更动)。

创建 `approval-api/app/crud/posting.py` — 内容与 Task 3 Step 2 的
`expense-api/app/services/posting.py` 完全相同,仅首行 import 改为:

```python
from app.models.posting import PostingEvent, PostingLine
```

- [ ] **Step 3: 写失败测试**

创建 `approval-api/tests/test_posting_emit.py`:

```python
"""emit_event helper tests (same contract as expense-api's copy)."""
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.crud.posting import emit_event
from app.models.posting import PostingEvent, PostingLine


async def test_pa_payment_event_shape(db_session):
    doc_id = uuid.uuid4()
    ev_id = await emit_event(
        db_session,
        source_service="epms", source_doc_type="pa", source_doc_id=doc_id,
        source_doc_number="PA-2026-0007", event_type="payment",
        lines=[
            {"line_role": "accounts_payable", "debit": Decimal("1200.50"),
             "partner_id": uuid.uuid4(), "partner_name": "ACME Inc", "currency": "CAD"},
            {"line_role": "bank", "credit": Decimal("1200.50"), "currency": "CAD"},
        ],
    )
    assert ev_id is not None
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev_id).order_by(PostingLine.line_no)
    )).scalars().all()
    assert lines[0].line_role == "accounts_payable"
    assert lines[0].debit == Decimal("1200.50")
    assert lines[1].credit == Decimal("1200.50")


async def test_double_process_emits_once(db_session):
    doc_id = uuid.uuid4()
    kw = dict(
        source_service="epms", source_doc_type="pa", source_doc_id=doc_id,
        source_doc_number="PA-2026-0008", event_type="payment",
        lines=[{"line_role": "bank", "credit": Decimal("5.00")}],
    )
    assert await emit_event(db_session, **kw) is not None
    assert await emit_event(db_session, **kw) is None
    events = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == doc_id)
    )).scalars().all()
    assert len(events) == 1
```

- [ ] **Step 4: 跑测试**

```bash
cd /c/Project/uniops/approval-api
.venv/Scripts/python -m pytest tests/ -v
```

预期:2 passed

- [ ] **Step 5: PA 镜像补列 + 引擎接线**

修改 `approval-api/app/models/pa.py`,在 `vendor_name` 列后加两列
(物理表 payment_applications 已有这两列,镜像只是补声明):

```python
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
```

修改 `approval-api/app/crud/engine.py` 的 process 分支(766 行起),在
`_set_status(meta, doc, "processed")` 之后、invoice 标记 paid 循环之后插入:

```python
        from app.crud.posting import emit_event as emit_posting_event
        await emit_posting_event(
            db,
            source_service="epms",
            source_doc_type=doc_type,            # 'pa' | 'pa_dir'
            source_doc_id=doc.id,
            source_doc_number=doc.pa_number,
            event_type="payment",
            lines=[
                {"line_role": "accounts_payable", "debit": doc.payment_amount,
                 "partner_id": doc.vendor_id, "partner_name": doc.vendor_name,
                 "currency": doc.currency},
                {"line_role": "bank", "credit": doc.payment_amount,
                 "currency": doc.currency},
            ],
        )
```

(import 放分支内与 engine.py 现状一致性较差——engine.py 的 import 全在文件头;
把 `from app.crud.posting import emit_event as emit_posting_event` 放到文件头部
import 区更符合该文件风格,二选一,以文件头为准。)

- [ ] **Step 6: 跑测试 + 手工冒烟**

```bash
.venv/Scripts/python -m pytest tests/ -v
```

预期:2 passed。

手工冒烟(本地栈在跑、且 10.10.50.20 已执行 finance-api `alembic upgrade head` 之后):
走一遍 PA 审批到 process(EPMS UI 或 curl),然后:

```bash
curl -s "http://localhost:8004/finance/v1/posting/events?source_doc_type=pa" \
  -H "Authorization: Bearer <token>" | head -40
```

预期:返回一条 `event_type: "payment"` 的事件,两条 lines 借贷相等。

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops
git add approval-api/app/models/posting.py approval-api/app/models/pa.py \
        approval-api/app/crud/posting.py approval-api/app/crud/engine.py \
        approval-api/tests/ approval-api/pytest.ini approval-api/requirements-dev.txt
git commit -m "feat(approval-posting): emit payment posting event on PA process (Phase 0-B1)"
```

---

### Task 5: 收尾 — 共享库迁移上线 + 文档 + 全量回归

**Files:**
- Modify: `README.md`(Architecture 段)
- Modify: `DEPLOYMENT.md`(迁移顺序)
- Modify: `reset-db.sh:31`(SERVICES 列表已含 finance-api,确认即可,无需改动)

- [ ] **Step 1: 对共享 DB(10.10.50.20,当前为测试性质)执行 finance-api 迁移**

```bash
cd /c/Project/uniops
docker compose -f docker-compose.dev.yml run --rm finance-api alembic upgrade head
```

预期输出含:`Running upgrade 0001_payment_records -> 0002_posting_events`

验证:

```bash
docker compose -f docker-compose.dev.yml exec postgres psql -U epms -d epms \
  -c "\d posting_events" 2>/dev/null || echo "(本地容器无此库——直接在 10.10.50.20 上验证)"
```

> 注意:服务连的是 10.10.50.20,`run --rm finance-api alembic` 会对 10.10.50.20 执行
> ——这正是当前预期(该库为测试性质)。验证用任一能连 10.10.50.20 的 psql。

- [ ] **Step 2: README 架构段加一行**

在 `README.md` 的 Architecture 图下方加:

```markdown
**Accounting spine (Phase 0):** every money-moving action (PA processed, expense paid)
writes a row to `posting_events` / `posting_lines` (owned by finance-api, migration
`0002_posting_events`). Emitters: approval-api (PA `process` action), expense-api
(`process_pay`). Idempotent on `(source_doc_type, source_doc_id, event_type)`.
Query: `GET :8004/finance/v1/posting/events`. Future AP/AR/FA/GL modules replay these events.
```

`DEPLOYMENT.md` 的迁移说明处加一句:**finance-api 的 alembic 必须先于
approval-api / expense-api 新版本部署执行**(发射方 INSERT 的表由 finance-api 迁移创建)。

- [ ] **Step 3: 全量回归**

```bash
cd /c/Project/uniops
./run_tests.sh --unit-only
```

预期:epms-api / expense-api / budget-api / vms-api / mdm-api 原有套件全绿,
finance-api(5)与 approval-api(2)的新测试全绿。
(run_tests.sh 自动发现各服务 .venv;finance-api / approval-api 是新建的 .venv,
确认脚本枚举的服务列表包含它们,若不含则把两个目录加进脚本的服务循环。)

- [ ] **Step 4: Commit**

```bash
git add README.md DEPLOYMENT.md run_tests.sh
git commit -m "docs(posting): accounting spine notes + migration ordering (Phase 0-B1)"
```

---

## 自检记录(writing-plans Self-Review)

- **Spec 覆盖:** 表 + 迁移(Task 1)、查询窗口(Task 2)、两个发射点(Task 3/4)、
  上线与文档(Task 5)。Roadmap 中 B2/B3(税码、partner)的列已在 schema 预留
  (tax_code / partner_id / account_code 可空)。对账任务(扫描 processed PA / paid
  claim 与 posting_events 差集)**有意排除**,属 P1 预算对账工作流,在 roadmap 中记录。
- **占位符扫描:** 无 TBD/TODO;Task 4 的"内容与 Task 3 完全一致仅改 import"是明确
  指令而非占位(两份拷贝是本代码库的既定模式,见各服务 deps.py)。
- **类型一致性:** `emit_event` 签名、line dict 键名(line_role/debit/credit/...)、
  表名与列名在 Task 1/3/4 与契约段一致;`uq_posting_events_source` 的三列与
  `on_conflict_do_nothing(index_elements=[...])` 一致。
- **已知风险(均已核实关闭):**
  1. ~~expense-api conftest fixture 名~~ → 已确认无 `db_session`,计划含补充代码;
  2. ~~finance-api alembic env.py URL 来源~~ → 已确认读 `settings.database_url`,
     环境变量覆盖可靠;
  3. engine.py 的 `act == "process"` 在 approval-api 自身事务内,
     posting 与状态翻转同事务,满足"同库同事务"决议。
