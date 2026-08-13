# MRP 意向产品 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让销售预测能记录还没有 ERP 物料编码的策划产品（意向产品），它进 outlook 快照但不排产，将来可绑定到真实物料码并带走已录数字。

**Architecture:** 意向行**复用现有 `mrp_demand_series`**，用 `INTENT-xxxxxxxx` 占位码存进 `material_code`——粘贴、自动保存、变更日志、KG/吨切换全部零改造。新建 `mrp_intent_products` 只存名称与绑定状态；`mrp_forecast_lines` 加两列让快照能脱离该表自解释。绑定是一个事务内的 `material_code` 改名 + 审计留痕。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic（mrp-api，端口 8011）；React 19 + TS + Tailwind + `@uniops/shell`（mrp 前端，端口 5179）

**Spec:** `docs/superpowers/specs/2026-08-12-mrp-weekly-planning-and-intent-products-design.md`（§4.1 意向产品部分、§5.3、决策 D7/D8/D11）

## Global Constraints

- **计划口径恒为 KG**：`PLANNING_UOM='KG'`，任何数量入库前必须是 KG；前端吨显示是纯展示层（`unitScale`）
- **金额/数量一律 `Decimal`**，禁止 float 进入计算链；Pydantic 会把 Decimal 序列化成 JSON 字符串，前端必须 `Number()` 转换
- **前端 user-facing 文案全英文**；代码注释可中文
- **UI 样式以 EPMS 为模板**，用 `@uniops/shell` 的 Button 等原语，不要裸 `<button>`；浮层用 `createPortal` 到 body
- **权限**：不新增权限键。写操作走 `mrp.demand.write`，读走 `mrp.report.view`
- **测试库禁止并发**：同一时刻只能有一个 mrp-api 套件在跑（见 `feedback_uniops_test_db_concurrency`）。本计划专用测试库 `mrp_intent_test`，通过 `TEST_MRP_DB` 指定，避免和别的会话抢 `mrp_test`（★库名必须**以 `_test` 结尾**——conftest 有 DROP SCHEMA 安全护栏，`mrp_test_xxx` 这种命名会被直接拒绝）
- **跑测命令**（宿主机，容器内无 pytest）：
  ```bash
  PGPW=$(docker inspect uniops_postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep POSTGRES_PASSWORD | cut -d= -f2)
  cd mrp-api && JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD="$PGPW" \
    TEST_MRP_DB=mrp_intent_test ALLOWED_ORIGINS='["http://localhost:5179"]' \
    python -m pytest tests -q
  ```
- **基线**：mrp-api 现有 **167 passed**，不许降；新增测试在其之上
- **前端门禁**：`cd mrp && npx tsc -p tsconfig.app.json --noEmit`，只许剩既有的 baseUrl 一条；用 `--listFiles | grep <改动文件>` 正面确认文件真被编译过（见 `feedback_uniops_frontend_tsc_false_gate`）

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `mrp-api/alembic/versions/mrp09_intent_products.py` | 建 `mrp_intent_products`；给 `mrp_forecast_lines` 加 `is_intent` / `intent_name` |
| `mrp-api/app/models/intent.py` | `MrpIntentProduct` ORM 模型 |
| `mrp-api/app/services/intent_products.py` | 占位码生成 + 绑定事务（纯业务逻辑，不含 HTTP） |
| `mrp-api/app/api/v1/intent.py` | `/intent-products` 路由（list / create / bind / drop） |
| `mrp-api/tests/test_intent_products.py` | 上述全部的测试 |
| `mrp/src/pages/forecast/intentApi.ts` | 前端 API client |
| `mrp/src/pages/forecast/AddIntentProductModal.tsx` | 新建意向产品弹窗 |
| `mrp/src/pages/forecast/BindIntentModal.tsx` | 绑定到真实物料码弹窗 |
| `mrp/src/pages/forecast/SalesForecastPage.tsx` | 接入上述两个弹窗 + 行着色 + 徽章 + 行操作 |

---

### Task 1: 数据模型与迁移

**Files:**
- Create: `mrp-api/alembic/versions/mrp09_intent_products.py`
- Create: `mrp-api/app/models/intent.py`
- Modify: `mrp-api/app/models/__init__.py`（注册新模型——**不注册则测试建不出表**，见 `reference_uniops_epms_test_invocation` 同类坑）
- Modify: `mrp-api/app/models/forecast.py`（`ForecastLine` 加两列）
- Modify: `mrp-api/tests/conftest.py`（补 `auth_headers` fixture，见 Step 1b）
- Test: `mrp-api/tests/test_intent_products.py`

**Interfaces:**
- Consumes: 无
- Produces: `MrpIntentProduct`（列：`id, code, name, note, status, bound_material_code, bound_at, bound_by, created_by`）；`ForecastLine.is_intent: bool`、`ForecastLine.intent_name: str | None`

- [ ] **Step 1: 先确认 alembic 链尾是单 head**

Run: `cd mrp-api && python -m alembic heads`
Expected: 只输出一行 `mrp08 (head)`。若出现两行，**停下**并先解决双 head，不要硬接（见 `feedback_uniops_alembic_new_migration_check_heads`）。

- [ ] **Step 1b: 先给 conftest 补一个 `auth_headers` fixture**

本计划所有测试都用 `auth_headers`，但 `mrp-api/tests/conftest.py` 现在只有 `admin_token`（各测试自己拼 header）。**先加这两行**，否则后面每个任务的测试都会因缺 fixture 而收集失败：

```python
@pytest_asyncio.fixture
async def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}
```

若该 fixture 已存在（另一份计划先加过），跳过本步。

- [ ] **Step 2: 写失败测试**

追加到 `mrp-api/tests/test_intent_products.py`：

```python
import pytest
from sqlalchemy import inspect, text


@pytest.mark.asyncio
async def test_intent_tables_exist(db_session):
    """mrp09 建表 + forecast_lines 加列。"""
    cols = (await db_session.execute(text(
        "select column_name from information_schema.columns "
        "where table_name = 'mrp_intent_products'"
    ))).scalars().all()
    assert {"id", "code", "name", "note", "status",
            "bound_material_code", "bound_at", "bound_by", "created_by"} <= set(cols)

    fc = (await db_session.execute(text(
        "select column_name from information_schema.columns "
        "where table_name = 'mrp_forecast_lines'"
    ))).scalars().all()
    assert {"is_intent", "intent_name"} <= set(fc)


@pytest.mark.asyncio
async def test_intent_code_is_unique(db_session):
    from app.models.intent import MrpIntentProduct
    db_session.add(MrpIntentProduct(code="INTENT-aaaaaaaa", name="A", status="active"))
    await db_session.flush()
    db_session.add(MrpIntentProduct(code="INTENT-aaaaaaaa", name="B", status="active"))
    with pytest.raises(Exception):
        await db_session.flush()
```

- [ ] **Step 3: 运行，确认失败**

Run: `python -m pytest tests/test_intent_products.py -q`（带 Global Constraints 里的 env 前缀）
Expected: FAIL —— `relation "mrp_intent_products" does not exist` 或 `ModuleNotFoundError: app.models.intent`

- [ ] **Step 4: 写模型**

`mrp-api/app/models/intent.py`：

```python
"""Intent products: planned SKUs that have no ERP material code yet.

They live in the forecast under an `INTENT-xxxxxxxx` placeholder code stored
in the ordinary `mrp_demand_series.material_code`, so the whole grid stack
(paste, autosave, change log, KG/t toggle) needs no special case. This table
only carries the human name and the binding lifecycle.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class MrpIntentProduct(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "mrp_intent_products"

    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    bound_material_code: Mapped[str | None] = mapped_column(String(50))
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bound_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
```

在 `mrp-api/app/models/__init__.py` 里加 `from app.models.intent import MrpIntentProduct  # noqa: F401`。

在 `mrp-api/app/models/forecast.py` 的 `ForecastLine` 末尾加：

```python
    # Snapshot self-description (design §4.1): an outlook snapshot must stay
    # readable after the intent product is bound to a real code or dropped,
    # so the flag and the human name are frozen into the line itself rather
    # than joined from mrp_intent_products at read time.
    is_intent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    intent_name: Mapped[str | None] = mapped_column(String(200))
```

- [ ] **Step 5: 写迁移**

`mrp-api/alembic/versions/mrp09_intent_products.py`：

```python
"""intent products

Revision ID: mrp09
Revises: mrp08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "mrp09"
down_revision = "mrp08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mrp_intent_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("bound_material_code", sa.String(50)),
        sa.Column("bound_at", sa.DateTime(timezone=True)),
        sa.Column("bound_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_mrp_intent_products_code", "mrp_intent_products", ["code"])
    op.add_column("mrp_forecast_lines",
                  sa.Column("is_intent", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("mrp_forecast_lines", sa.Column("intent_name", sa.String(200)))


def downgrade() -> None:
    op.drop_column("mrp_forecast_lines", "intent_name")
    op.drop_column("mrp_forecast_lines", "is_intent")
    op.drop_index("ix_mrp_intent_products_code", table_name="mrp_intent_products")
    op.drop_table("mrp_intent_products")
```

**注意** `revision` 字符串必须 ≤32 字符（见 `project_uniops_pa_create_on_behalf` 踩过的坑）。

- [ ] **Step 6: 运行测试，确认通过**

Run: `python -m pytest tests/test_intent_products.py tests/test_alembic_revisions.py -q`
Expected: PASS。`test_alembic_revisions.py` 会校验单 head，顺带确认没接错链。

- [ ] **Step 7: Commit**

```bash
git add mrp-api/alembic/versions/mrp09_intent_products.py mrp-api/app/models/intent.py \
        mrp-api/app/models/__init__.py mrp-api/app/models/forecast.py \
        mrp-api/tests/test_intent_products.py
git commit -m "feat(mrp): intent product table + forecast line snapshot flags"
```

---

### Task 2: 意向产品服务与 CRUD 端点

**Files:**
- Create: `mrp-api/app/services/intent_products.py`
- Create: `mrp-api/app/api/v1/intent.py`
- Modify: `mrp-api/app/api/v1/__init__.py`（注册 router）
- Test: `mrp-api/tests/test_intent_products.py`

**Interfaces:**
- Consumes: Task 1 的 `MrpIntentProduct`
- Produces:
  - `generate_intent_code() -> str`（返回 `INTENT-` + 8 位小写 hex）
  - `GET /api/v1/intent-products?status=active|all`（Python 侧参数名不叫 status，用 `Query(alias="status")` 避开与 fastapi 的 `status` 模块重名——照抄 `series.py:199` 的 `alias="from"` 做法） → `list[IntentProductResponse]`
  - `POST /api/v1/intent-products` body `{name, note}` → 201 `IntentProductResponse{id, code, name, note, status, bound_material_code}`
  - `POST /api/v1/intent-products/{id}/drop` → 200，`status='dropped'`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_create_intent_product_generates_placeholder_code(client, auth_headers):
    r = await client.post("/api/v1/intent-products",
                          json={"name": "Stage 3 New Formula", "note": "planning"},
                          headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"].startswith("INTENT-")
    assert len(body["code"]) == len("INTENT-") + 8
    assert body["name"] == "Stage 3 New Formula"
    assert body["status"] == "active"
    assert body["bound_material_code"] is None


@pytest.mark.asyncio
async def test_create_intent_product_rejects_blank_name(client, auth_headers):
    r = await client.post("/api/v1/intent-products", json={"name": "   "}, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_returns_active_only_by_default(client, auth_headers):
    created = (await client.post("/api/v1/intent-products", json={"name": "Keeper"},
                                 headers=auth_headers)).json()
    dropped = (await client.post("/api/v1/intent-products", json={"name": "Gone"},
                                 headers=auth_headers)).json()
    await client.post(f"/api/v1/intent-products/{dropped['id']}/drop", headers=auth_headers)

    codes = [i["code"] for i in (await client.get("/api/v1/intent-products",
                                                  headers=auth_headers)).json()]
    assert created["code"] in codes
    assert dropped["code"] not in codes
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_intent_products.py -q -k intent_product`
Expected: FAIL —— 404（路由不存在）

- [ ] **Step 3: 写服务**

`mrp-api/app/services/intent_products.py`：

```python
"""Intent product lifecycle (design §5.3, decisions D7/D8/D11)."""
import secrets

INTENT_CODE_PREFIX = "INTENT-"


def generate_intent_code() -> str:
    """`INTENT-` + 8 lowercase hex chars.

    Random rather than sequential so two planners creating a row at the same
    moment never collide on a counter; the DB unique index is the backstop.
    """
    return f"{INTENT_CODE_PREFIX}{secrets.token_hex(4)}"


def is_intent_code(code: str) -> bool:
    return code.startswith(INTENT_CODE_PREFIX)
```

- [ ] **Step 4: 写端点**

`mrp-api/app/api/v1/intent.py`（照抄 `capacity.py` 的 dep 与结构风格）：

```python
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.models.intent import MrpIntentProduct
from app.services.intent_products import generate_intent_code

router = APIRouter(prefix="/intent-products", tags=["intent-products"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.demand.write"))]


class IntentProductCreate(BaseModel):
    name: str
    note: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class IntentProductResponse(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    note: str | None
    status: str
    bound_material_code: str | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[IntentProductResponse])
async def list_intent_products(db: SessionDep, _: ReadDep, status_filter: str = "active"):
    stmt = select(MrpIntentProduct).order_by(MrpIntentProduct.created_at.desc())
    if status_filter != "all":
        stmt = stmt.where(MrpIntentProduct.status == status_filter)
    return (await db.execute(stmt)).scalars().all()


@router.post("", response_model=IntentProductResponse, status_code=status.HTTP_201_CREATED)
async def create_intent_product(body: IntentProductCreate, db: SessionDep, payload: WriteDep):
    row = MrpIntentProduct(
        code=generate_intent_code(), name=body.name, note=body.note,
        status="active", created_by=uuid.UUID(payload["sub"]),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/{intent_id}/drop", response_model=IntentProductResponse)
async def drop_intent_product(intent_id: uuid.UUID, db: SessionDep, _: WriteDep):
    row = await db.get(MrpIntentProduct, intent_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "intent product not found")
    if row.status == "bound":
        raise HTTPException(status.HTTP_409_CONFLICT, "a bound intent product cannot be dropped")
    row.status = "dropped"
    await db.commit()
    await db.refresh(row)
    return row
```

在 `app/api/v1/__init__.py` 里 `from app.api.v1 import ... intent ...` 并 `api_router.include_router(intent.router)`。

- [ ] **Step 5: 运行测试，确认通过**

Run: `python -m pytest tests/test_intent_products.py -q`
Expected: PASS（含 Task 1 的两条）

- [ ] **Step 6: 补权限门禁测试**

`mrp-api/tests/test_permission_gates.py` 已有同类用例，**照它的 `_deny_everything` 惯例写**——`mrp_test` 库里没有 identity 的 `role_permissions`/`role_defs`/`user_roles` 表，所以用 `non_admin_token` 打真实 gate 之前，必须 monkeypatch `uniops_authz.core.user_role_codes` 与 `_effective_matrix`（`admin_token` 走 system_admin 快速通道，根本不会触到权限键，用它测不出门禁）。追加一条：非授权 token 打 `POST /api/v1/intent-products` 必须 403。

Run: `python -m pytest tests/test_permission_gates.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add mrp-api/app/services/intent_products.py mrp-api/app/api/v1/intent.py \
        mrp-api/app/api/v1/__init__.py mrp-api/tests/test_intent_products.py \
        mrp-api/tests/test_permission_gates.py
git commit -m "feat(mrp): intent product CRUD endpoints"
```

---

### Task 3: 绑定到真实物料码

**Files:**
- Modify: `mrp-api/app/services/intent_products.py`
- Modify: `mrp-api/app/api/v1/intent.py`
- Test: `mrp-api/tests/test_intent_products.py`

**Interfaces:**
- Consumes: Task 2 的 `MrpIntentProduct`、`is_intent_code`
- Produces: `POST /api/v1/intent-products/{id}/bind` body `{material_code}` → 200 `{intent, moved_months: int, moved_qty: str}`

**为什么是一个事务**：`mrp_demand_series` 与 `mrp_forecast_change_log` 里该占位码的所有行都要改名，中途失败会留下一半改名的数据——那是最难排查的一类脏数据。

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_bind_moves_series_rows_and_marks_bound(client, auth_headers, db_session):
    from sqlalchemy import text
    intent = (await client.post("/api/v1/intent-products", json={"name": "New SKU"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-01", "qty": "1000"},
        {"material_code": intent["code"], "month": "2027-02", "qty": "2000"},
    ]})

    r = await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                          json={"material_code": "S0093"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["moved_months"] == 2

    rows = (await db_session.execute(text(
        "select month, qty from mrp_demand_series where material_code = 'S0093' order by month"
    ))).all()
    assert [(m, str(q)) for m, q in rows] == [("2027-01", "1000.000"), ("2027-02", "2000.000")]
    assert (await db_session.execute(text(
        "select count(*) from mrp_demand_series where material_code = :c"
    ), {"c": intent["code"]})).scalar() == 0

    detail = (await client.get("/api/v1/intent-products?status=all",
                               headers=auth_headers)).json()
    bound = [i for i in detail if i["id"] == intent["id"]][0]
    assert bound["status"] == "bound"
    assert bound["bound_material_code"] == "S0093"


@pytest.mark.asyncio
async def test_bind_rejects_when_target_already_has_forecast(client, auth_headers):
    """D11: business says this cannot happen — so it must be loud, not silently merged."""
    intent = (await client.post("/api/v1/intent-products", json={"name": "Collides"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-01", "qty": "50"},
    ]})
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": "S0060", "month": "2027-01", "qty": "200"},
    ]})

    r = await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                          json={"material_code": "S0060"}, headers=auth_headers)
    assert r.status_code == 409
    assert "already has forecast" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_bind_is_rejected_twice(client, auth_headers):
    intent = (await client.post("/api/v1/intent-products", json={"name": "Once"},
                                headers=auth_headers)).json()
    await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                      json={"material_code": "S0074"}, headers=auth_headers)
    r = await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                          json={"material_code": "S0075"}, headers=auth_headers)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_bind_rewrites_change_log_and_leaves_an_audit_row(client, auth_headers, db_session):
    from sqlalchemy import text
    intent = (await client.post("/api/v1/intent-products", json={"name": "Audited"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-03", "qty": "10"},
    ]})
    await client.post(f"/api/v1/intent-products/{intent['id']}/bind",
                      json={"material_code": "S0064"}, headers=auth_headers)

    assert (await db_session.execute(text(
        "select count(*) from mrp_forecast_change_log where material_code = :c"
    ), {"c": intent["code"]})).scalar() == 0
    sources = (await db_session.execute(text(
        "select source from mrp_forecast_change_log where material_code = 'S0064'"
    ))).scalars().all()
    assert "intent_bind" in sources
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_intent_products.py -q -k bind`
Expected: FAIL —— 404（`/bind` 不存在）

- [ ] **Step 3: 实现绑定服务**

追加到 `mrp-api/app/services/intent_products.py`：

```python
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.demand_series import MrpDemandSeries, MrpForecastChangeLog


class IntentBindConflict(Exception):
    """Raised when the target material code already carries forecast rows.

    Business states this cannot happen (intent products are SKUs still being
    planned), so decision D11 makes it a hard rejection rather than a silent
    merge: if the impossible happens, a planner must look at it.
    """


async def bind_intent_to_material(
    db: AsyncSession, *, intent_code: str, material_code: str, actor_name: str | None,
) -> tuple[int, Decimal]:
    """Move every series cell and change-log row from the placeholder code to
    the real material code, in one transaction. Returns (months, total_qty)."""
    clash = (await db.execute(
        select(func.count()).select_from(MrpDemandSeries)
        .where(MrpDemandSeries.material_code == material_code)
    )).scalar_one()
    if clash:
        raise IntentBindConflict(material_code)

    rows = (await db.execute(
        select(MrpDemandSeries.month, MrpDemandSeries.qty)
        .where(MrpDemandSeries.material_code == intent_code)
    )).all()
    moved_months = len(rows)
    moved_qty = sum((Decimal(str(q)) for _, q in rows), Decimal("0"))

    await db.execute(update(MrpDemandSeries)
                     .where(MrpDemandSeries.material_code == intent_code)
                     .values(material_code=material_code))
    await db.execute(update(MrpForecastChangeLog)
                     .where(MrpForecastChangeLog.material_code == intent_code)
                     .values(material_code=material_code))

    for month, qty in rows:
        db.add(MrpForecastChangeLog(
            material_code=material_code, month=month,
            old_qty=qty, new_qty=qty,
            source="intent_bind", changed_by_name=actor_name,
        ))
    return moved_months, moved_qty
```

- [ ] **Step 4: 挂端点**

追加到 `mrp-api/app/api/v1/intent.py`：

```python
class IntentBindRequest(BaseModel):
    material_code: str


class IntentBindResponse(BaseModel):
    intent: IntentProductResponse
    moved_months: int
    moved_qty: Decimal


@router.post("/{intent_id}/bind", response_model=IntentBindResponse)
async def bind_intent_product(
    intent_id: uuid.UUID, body: IntentBindRequest, db: SessionDep, payload: WriteDep,
):
    row = await db.get(MrpIntentProduct, intent_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "intent product not found")
    if row.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"intent product is {row.status}, only active ones can be bound")
    try:
        moved_months, moved_qty = await bind_intent_to_material(
            db, intent_code=row.code, material_code=body.material_code, actor_name=None,
        )
    except IntentBindConflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{body.material_code} already has forecast rows — merge them by hand first",
        )
    row.status = "bound"
    row.bound_material_code = body.material_code
    row.bound_at = datetime.now(timezone.utc)
    row.bound_by = uuid.UUID(payload["sub"])
    await db.commit()
    await db.refresh(row)
    return IntentBindResponse(intent=row, moved_months=moved_months, moved_qty=moved_qty)
```

需要在文件头补 `from decimal import Decimal` 与 `from app.services.intent_products import IntentBindConflict, bind_intent_to_material, generate_intent_code`。

- [ ] **Step 5: 运行测试，确认通过**

Run: `python -m pytest tests/test_intent_products.py -q`
Expected: PASS

- [ ] **Step 6: 跑整套，确认没碰坏别的**

Run: `python -m pytest tests -q`
Expected: **≥ 167 passed**（基线）+ 本任务新增，0 failed

- [ ] **Step 7: Commit**

```bash
git add mrp-api/app/services/intent_products.py mrp-api/app/api/v1/intent.py \
        mrp-api/tests/test_intent_products.py
git commit -m "feat(mrp): bind an intent product to a real material code"
```

---

### Task 4: 快照带标记，MPS 跳过意向行

**Files:**
- Modify: `mrp-api/app/api/v1/series.py`（`post_outlook` 冻结时写 `is_intent` / `intent_name`）
- Modify: `mrp-api/app/api/v1/mps.py`（generate 过滤意向行，写进 `run.stats`）
- Test: `mrp-api/tests/test_outlook.py`、`mrp-api/tests/test_mps_api.py`

**Interfaces:**
- Consumes: Task 1 的 `ForecastLine.is_intent/intent_name`、Task 2 的 `is_intent_code`
- Produces: `run.stats["skipped_intent"] = [{"code": str, "name": str, "qty": str}]`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_outlook.py`：

```python
@pytest.mark.asyncio
async def test_outlook_snapshot_flags_intent_lines(client, auth_headers, db_session):
    from sqlalchemy import text
    intent = (await client.post("/api/v1/intent-products", json={"name": "Planned SKU"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-06", "qty": "500"},
        {"material_code": "S0093", "month": "2027-06", "qty": "800"},
    ]})
    r = await client.post("/api/v1/series/outlook",
                          json={"anchor_month": "2027-06"}, headers=auth_headers)
    assert r.status_code == 201, r.text

    rows = (await db_session.execute(text(
        "select material_code, is_intent, intent_name from mrp_forecast_lines "
        "where version_id = :v"
    ), {"v": r.json()["id"]})).all()
    by_code = {c: (f, n) for c, f, n in rows}
    assert by_code[intent["code"]] == (True, "Planned SKU")
    assert by_code["S0093"][0] is False
```

追加到 `tests/test_mps_api.py`：

```python
@pytest.mark.asyncio
async def test_generate_skips_intent_rows_and_names_them(client, auth_headers):
    intent = (await client.post("/api/v1/intent-products", json={"name": "Not Yet Real"},
                                headers=auth_headers)).json()
    await client.put("/api/v1/series/cells", headers=auth_headers, json={"cells": [
        {"material_code": intent["code"], "month": "2027-09", "qty": "700"},
        {"material_code": "S0093", "month": "2027-09", "qty": "900"},
    ]})
    version = (await client.post("/api/v1/series/outlook",
                                 json={"anchor_month": "2027-09"}, headers=auth_headers)).json()

    run = (await client.post("/api/v1/mps/runs",
                             json={"forecast_version_id": version["id"]},
                             headers=auth_headers)).json()
    codes = {l["material_code"] for l in run["lines"]}
    assert intent["code"] not in codes
    skipped = run["stats"]["skipped_intent"]
    assert [s["code"] for s in skipped] == [intent["code"]]
    assert skipped[0]["name"] == "Not Yet Real"
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_outlook.py tests/test_mps_api.py -q -k intent`
Expected: FAIL —— `is_intent` 全为 False / `KeyError: 'skipped_intent'`

- [ ] **Step 3: 冻结时写标记**

在 `series.py` 的 `post_outlook` 里，构造 `ForecastLine` 之前先把意向名查出来：

```python
    intent_names = dict((await db.execute(
        select(MrpIntentProduct.code, MrpIntentProduct.name)
    )).all())
    ...
    line = ForecastLine(
        version_id=version.id, material_code=code, month=month, qty=qty, uom="KG",
        is_intent=is_intent_code(code),
        intent_name=intent_names.get(code),
    )
```

- [ ] **Step 4: MPS 生成时过滤**

在 `mps.py` 的 generate 里，把快照行喂给引擎之前分流：

```python
    planned_lines = [l for l in snapshot_lines if not l.is_intent]
    intent_lines = [l for l in snapshot_lines if l.is_intent]
    ...
    stats["skipped_intent"] = [
        {"code": l.material_code, "name": l.intent_name, "qty": str(l.qty)}
        for l in intent_lines
    ]
```

**只按 `is_intent` 列判断，不要在这里再调 `is_intent_code()`**——快照的自解释性正是 Task 1 加那两列的理由；靠前缀猜等于把判定逻辑复制了一份。

- [ ] **Step 5: 运行测试，确认通过**

Run: `python -m pytest tests/test_outlook.py tests/test_mps_api.py -q`
Expected: PASS

- [ ] **Step 6: 跑整套**

Run: `python -m pytest tests -q`
Expected: ≥167 + 新增，0 failed

- [ ] **Step 7: Commit**

```bash
git add mrp-api/app/api/v1/series.py mrp-api/app/api/v1/mps.py \
        mrp-api/tests/test_outlook.py mrp-api/tests/test_mps_api.py
git commit -m "feat(mrp): carry intent flags into outlook snapshots, skip them in MPS"
```

---

### Task 5: 前端

**Files:**
- Create: `mrp/src/pages/forecast/intentApi.ts`
- Create: `mrp/src/pages/forecast/AddIntentProductModal.tsx`
- Create: `mrp/src/pages/forecast/BindIntentModal.tsx`
- Modify: `mrp/src/pages/forecast/SalesForecastPage.tsx`
- Modify: `mrp/src/pages/forecast/GenerateOutlookModal.tsx`（提示含 N 个意向产品）
- Modify: `mrp/src/pages/mps/ProductionPlanPage.tsx`（摘要卡片点名被跳过的意向产品）

**Interfaces:**
- Consumes: Task 2/3 的端点
- Produces: 无（终端消费者）

**没有前端测试框架**——mrp/epms/portal/oa 都没有。纯逻辑放 `verify.ts` 用断言覆盖，UI 靠 tsc + 手工点验，这是本仓库既有做法（见 `mrp/src/components/matrixGrid/verify.ts` 的模块 docstring）。

- [ ] **Step 1: API client**

`mrp/src/pages/forecast/intentApi.ts`：

```ts
import { api } from '@/lib/api'

export interface IntentProduct {
  id: string
  code: string
  name: string
  note: string | null
  status: 'active' | 'bound' | 'dropped'
  bound_material_code: string | null
}

export const intentApi = {
  list: () => api.get<IntentProduct[]>('/api/v1/intent-products'),
  create: (name: string, note?: string) =>
    api.post<IntentProduct>('/api/v1/intent-products', { name, note }),
  bind: (id: string, materialCode: string) =>
    api.post<{ intent: IntentProduct; moved_months: number; moved_qty: string }>(
      `/api/v1/intent-products/${id}/bind`, { material_code: materialCode }),
}
```

**照抄 `forecastApi.ts` 里 `api` 的真实导入路径与方法名**（本仓库前端不用相对 `/api` 走 vite 代理，见 `feedback_uniops_oa_vite_proxy_dead`）。

- [ ] **Step 2: 新建意向产品弹窗**

`AddIntentProductModal.tsx`：名称（必填，`onBlur` 校验，错误文本挂在输入框旁并带 `role="alert"`）+ 备注（选填）+ Cancel/Create。用 `@uniops/shell` 的 `Button`。提交走 loading→success/error 三态，成功 toast。

- [ ] **Step 3: 大表接入意向行**

在 `SalesForecastPage.tsx`：

1. `useQuery` 拉 `intentApi.list()`，得到 `intentByCode: Map<string, IntentProduct>`
2. **行标签**：`matrixRows` 里，若 `intentByCode.has(code)` 则 `label` 用意向名称而不是物料名（否则整行显示 `INTENT-3f9a2b71`，没人看得懂）
3. **着色**：把意向码并入现有 `tintRowIds`（已被 No-BOM 使用的同一机制）
4. **徽章**：在现有 `rowBadge` 里，意向行渲染 `<Badge variant="warning">Intent</Badge>`
5. **行操作**：现有 `rowActions` 里，意向行追加一个 Bind 按钮（`Link2` 图标，`title="Bind to material code"`），点击开 `BindIntentModal`
6. Add Product 按钮旁加第二个按钮 **Add intent product**

**注意**：`rowBadge`/`rowActions` 的 `row` 参数在 Task 之外已经加过 undefined 守卫（commit `f4a0303`），不要在这里重复加。

- [ ] **Step 4: 绑定弹窗**

`BindIntentModal.tsx`：复用现有 `MaterialPicker`（`finishedGoodsOnly` 默认 true）选真实码 → 显示"This will move N months / X t to <code>" → Confirm。**409 必须把后端的 detail 原样显示出来**（那正是 D11 要让人看见的那句话），不要吞成"绑定失败"。

- [ ] **Step 5: 两处提示**

- `GenerateOutlookModal.tsx`：若窗口内含意向行，加一行说明 `This snapshot includes N intent product(s). They are recorded but never scheduled.`
- `ProductionPlanPage.tsx` 的生成摘要卡片：读 `run.stats.skipped_intent`，有值就列出 `Not scheduled (intent): <name> · <qty>`

- [ ] **Step 6: 类型门禁**

Run:
```bash
cd mrp && npx tsc -p tsconfig.app.json --noEmit
npx tsc -p tsconfig.app.json --noEmit --listFiles | grep -E "intentApi|AddIntentProductModal|BindIntentModal"
```
Expected: 第一条只剩 baseUrl 一条诊断；第二条必须**列出这三个新文件**（列不出来说明它们没被编译，第一条的"通过"是假的）

- [ ] **Step 7: Commit**

```bash
git add mrp/src/pages/forecast/intentApi.ts mrp/src/pages/forecast/AddIntentProductModal.tsx \
        mrp/src/pages/forecast/BindIntentModal.tsx mrp/src/pages/forecast/SalesForecastPage.tsx \
        mrp/src/pages/forecast/GenerateOutlookModal.tsx mrp/src/pages/mps/ProductionPlanPage.tsx
git commit -m "feat(mrp): intent products in the Sales Forecast grid"
```

---

## 手工验收（用户，E2E 自动化被安全分类器拦）

1. Add intent product → 大表出现琥珀色行 + `Intent` 徽章，行标签是名称不是占位码
2. 在意向行录数 → 刷新后仍在；单元格历史 popover 正常
3. Generate Outlook → 提示含 N 个意向产品；快照查看器里该行带标记
4. 基于该快照生成 MPS → 意向行**不在计划里**，摘要卡片点名列出它
5. Bind to material → 选一个真实成品码 → 数字原样搬过去，行变成正常产品行
6. 再次基于新快照排产 → 该产品**参与排产**
7. 对一个已有预测的物料码执行绑定 → 弹出 409 原文，绑定被拒
