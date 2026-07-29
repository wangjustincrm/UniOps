# PR 部门选择器 + 部门驱动审批 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 EPMS PR 增加独立的 `department_id` 字段，制单人可在 New PR 上显式选择部门（默认自己部门，支持跨部门代提），并让该选择驱动 Cost Center/预算筛选、审批人解析（dept_manager/gm_or_opm/director）和 PR PDF。

**Architecture:** epms-api 给共享表 `purchase_requests` 加 `department_id` FK + 回填迁移；approval-api（同库同表镜像）加列声明并把「按部门」审批解析从"读创建人部门"改为"读 PR.department_id（NULL 回退创建人部门）"；epms 前端在 PR 创建/编辑页加部门选择器驱动 Cost Center 筛选。

**Tech Stack:** FastAPI + SQLAlchemy(async) + Alembic + Pydantic v2（epms-api / approval-api）；React + TypeScript + react-query + react-hook-form（epms 前端）；ReportLab（PDF）；pytest（后端测试）。

## Global Constraints

- 分支/worktree：`feature/pr-department-selector` @ `c:/Project/uniops-pr-dept`（基于 origin/main `ce71376`）。所有改动在此 worktree，勿碰主 checkout。
- 迁移只归属 **epms-api**（表 owner）；approval-api 只加 model 列声明，**不加迁移**。
- 新 alembic 迁移前必须 `alembic heads` 确认单一 head，`down_revision` 挂真实链尾（禁止按文件名猜）。
- 宿主 .env 指向生产库：**所有 alembic/pytest 必须在容器内跑或显式覆盖 `POSTGRES_*`/`DATABASE_URL` 指向本地 docker**，严禁打生产 10.10.50.20。
- epms 前端 UI 文案纯英文；部门选择器 label 用 "Department"。
- epms 前端无绿色 tsc 基线（约 69 存量错）；前端门禁 = `tsc -p tsconfig.app.json --noEmit` 相对 69 基线**零新增**，不是零错。
- `department_id` 全链路 nullable（旧数据/回退兼容）。
- 审批边界：`dept_manager`/`gm_or_opm`/`director` 跟 PR 选择的部门走；`supervisor`（个人直属上级）保持按创建人不变。

---

## 文件结构

**epms-api**
- `app/models/pr.py` — 加 `department_id` 列
- `app/schemas/pr.py` — `PrCreate`/`PrUpdate`/`PrResponse` 加 `department_id`
- `app/crud/pr.py` — `_resolve_names` 改从 `department_id` 派生 `department_name`；create/update 存 `department_id`
- `alembic/versions/<new>_add_department_id_to_pr.py` — 加列 + 回填
- `tests/` — crud + 迁移回填测试

**approval-api**
- `app/models/pr.py` — 加 `department_id` 列声明（读用，无迁移）
- `app/crud/engine.py` — 新增 `_routing_department_id`；`_get_dept_manager_id`/`_resolve_gm_or_opm`/`_resolve_director` 改接受 `dept_id`；改所有调用点（execute_action / _create_approve_task / _actor_can_approve / _resolved_assignee_for_step / _resync_document）
- `tests/test_engine_dept_manager_routing.py` — 扩展跨部门 + 回退用例

**epms 前端**
- `src/services/pr.ts` — `CreatePrBody`/`UpdatePrBody`/`ApiPr` 加 `department_id`
- `src/pages/pr/PrCreatePage.tsx` — 部门选择器 + 驱动 Cost Center + 清级联 + payload
- `src/pages/pr/PrEditPage.tsx` — 同上 + 从 `pr.department_id` 回显

---

## Task 1: epms-api — 加 `department_id` 列 + 回填迁移

**Files:**
- Modify: `epms-api/app/models/pr.py:33`（在 `department_name` 旁加列）
- Create: `epms-api/alembic/versions/<rev>_add_department_id_to_pr.py`
- Test: `epms-api/tests/test_pr_department_backfill.py`

**Interfaces:**
- Produces: `purchase_requests.department_id UUID NULL`（FK→departments.id）；`PurchaseRequest.department_id` ORM 属性。

- [ ] **Step 1: 加 model 列**

在 `epms-api/app/models/pr.py`，`department_name`（line 33）之后加：

```python
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
```

- [ ] **Step 2: 确认 alembic head**

Run（容器内或覆盖 env）：`cd epms-api && alembic heads`
Expected: 单一 head。记下 revision 作为新迁移的 `down_revision`。若多 head，先停下报告（禁止猜）。

- [ ] **Step 3: 生成迁移骨架并写加列 + 回填**

Create `epms-api/alembic/versions/<rev>_add_department_id_to_pr.py`（`down_revision` = Step 2 的 head）：

```python
"""add department_id to purchase_requests + backfill

Revision ID: <rev>
Revises: <head_from_step2>
"""
import sqlalchemy as sa
from alembic import op

revision = "<rev>"
down_revision = "<head_from_step2>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_requests",
        sa.Column("department_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_purchase_requests_department_id", "purchase_requests", ["department_id"]
    )
    op.create_foreign_key(
        "fk_purchase_requests_department_id", "purchase_requests", "departments",
        ["department_id"], ["id"], ondelete="RESTRICT",
    )
    # Backfill (idempotent — only NULL rows):
    # 1) rows with a cost center → that cost center's department
    op.execute("""
        UPDATE purchase_requests pr
        SET department_id = cc.department_id
        FROM cost_centers cc
        WHERE pr.cost_center_id = cc.id AND pr.department_id IS NULL
    """)
    # 2) remaining NULL (no cost center) → the creator's department
    op.execute("""
        UPDATE purchase_requests pr
        SET department_id = u.department_id
        FROM users u
        WHERE pr.created_by = u.id AND pr.department_id IS NULL
    """)
    # 3) realign department_name to the resolved department (keep denorm consistent)
    op.execute("""
        UPDATE purchase_requests pr
        SET department_name = d.name
        FROM departments d
        WHERE pr.department_id = d.id
          AND pr.department_name IS DISTINCT FROM d.name
    """)


def downgrade() -> None:
    op.drop_constraint("fk_purchase_requests_department_id", "purchase_requests", type_="foreignkey")
    op.drop_index("ix_purchase_requests_department_id", table_name="purchase_requests")
    op.drop_column("purchase_requests", "department_id")
```

- [ ] **Step 4: 写回填测试（先失败）**

Create `epms-api/tests/test_pr_department_backfill.py`。参照现有 tests 的 fixtures（`conftest.py` 提供 async session / 建表）。测试：插入 (a) 有 cost center 的 PR、(b) 无 cost center 但有 created_by 部门的 PR，手动跑三段回填 SQL，断言 department_id 分别 = cc 部门 / 创建人部门，且幂等重跑不变。

```python
import pytest
from sqlalchemy import text

# reuse the three backfill statements from the migration
BACKFILL = [
    """UPDATE purchase_requests pr SET department_id = cc.department_id
       FROM cost_centers cc WHERE pr.cost_center_id = cc.id AND pr.department_id IS NULL""",
    """UPDATE purchase_requests pr SET department_id = u.department_id
       FROM users u WHERE pr.created_by = u.id AND pr.department_id IS NULL""",
    """UPDATE purchase_requests pr SET department_name = d.name
       FROM departments d WHERE pr.department_id = d.id
       AND pr.department_name IS DISTINCT FROM d.name""",
]


@pytest.mark.asyncio
async def test_backfill_from_cost_center_then_creator(db_session, seed_depts_users_cc):
    # seed_depts_users_cc fixture creates: dept A + dept B, a user in dept B,
    # a cost center in dept A, one PR with that cost center, one PR with no cc.
    ctx = seed_depts_users_cc
    for _ in range(2):  # run twice → idempotent
        for stmt in BACKFILL:
            await db_session.execute(text(stmt))
    await db_session.flush()
    pr_with_cc = await db_session.get(type(ctx.pr_with_cc), ctx.pr_with_cc.id)
    pr_no_cc = await db_session.get(type(ctx.pr_no_cc), ctx.pr_no_cc.id)
    assert pr_with_cc.department_id == ctx.dept_a_id          # from cost center
    assert pr_no_cc.department_id == ctx.dept_b_id            # from creator
    assert pr_with_cc.department_name == ctx.dept_a_name
```

（`seed_depts_users_cc` fixture 在同文件按现有 model 构造；参照 `tests/conftest.py` 既有的 seed 模式。）

- [ ] **Step 5: 跑迁移 + 测试**

Run（容器内 / 覆盖 env 指向 `epms_test`）：
- `alembic upgrade head`（确认加列成功）
- `pytest tests/test_pr_department_backfill.py -v`
Expected: 迁移成功，测试 PASS。

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/models/pr.py epms-api/alembic/versions/ epms-api/tests/test_pr_department_backfill.py
git commit -m "feat(pr): add department_id column + backfill migration"
```

---

## Task 2: epms-api — schema + crud（存部门 + 派生名）

**Files:**
- Modify: `epms-api/app/schemas/pr.py`（`PrCreate` L87-101、`PrUpdate` L109-123、`PrResponse` ~L170）
- Modify: `epms-api/app/crud/pr.py`（`_resolve_names` L36-59、`create` L199-246、`update` L251-292）
- Test: `epms-api/tests/test_pr_crud_department.py`

**Interfaces:**
- Consumes: Task 1 的 `PurchaseRequest.department_id`。
- Produces: `PrCreate.department_id: uuid.UUID | None`、`PrUpdate.department_id: uuid.UUID | None`、`PrResponse.department_id`；`_resolve_names(db, vendor_id, cost_center_id, department_id)` 返回 `(vendor_name, cost_center_name, department_name)`，`department_name` 优先由 `department_id` 派生。

- [ ] **Step 1: schema 加字段**

`schemas/pr.py`：`PrCreate`（在 L92 `cost_center_id` 旁）加 `department_id: uuid.UUID | None = None`；`PrUpdate` 同样加；`PrResponse` 在 `department_name` 旁加 `department_id: uuid.UUID | None`。`PrUpdate` 的 update 循环字段元组（crud）后续加 `department_id`。

- [ ] **Step 2: 写 crud 测试（先失败）**

Create `epms-api/tests/test_pr_crud_department.py`：

```python
import pytest
from app.crud import pr as pr_crud
from app.schemas.pr import PrCreate, PrLineItemIn, PrUpdate


@pytest.mark.asyncio
async def test_create_stores_department_and_derives_name(db_session, seed_dept_a_and_cc):
    ctx = seed_dept_a_and_cc  # dept A (id, name), a cost center in dept A
    payload = PrCreate(
        title="X", type=1, department_id=ctx.dept_a_id,
        line_items=[PrLineItemIn(description="d", qty=1, unit="ea", unit_price=1)],
    )
    pr = await pr_crud.create(db_session, payload, created_by=ctx.user_id)
    assert pr.department_id == ctx.dept_a_id
    assert pr.department_name == ctx.dept_a_name   # derived from department_id, not cost center


@pytest.mark.asyncio
async def test_update_draft_changes_department(db_session, seed_two_depts_pr_draft):
    ctx = seed_two_depts_pr_draft  # a draft PR in dept A, plus dept B
    updated = await pr_crud.update(db_session, ctx.pr, PrUpdate(department_id=ctx.dept_b_id))
    assert updated.department_id == ctx.dept_b_id
    assert updated.department_name == ctx.dept_b_name
```

Run: `pytest tests/test_pr_crud_department.py -v` → Expected: FAIL（department_id 未存 / 派生未改）。

- [ ] **Step 3: 改 `_resolve_names` 从 department_id 派生**

`crud/pr.py` `_resolve_names`（L36-59）改签名加 `department_id`，`department_name` 优先由它派生（cost center 仅作 department_id 缺省时的兼容回退）：

```python
async def _resolve_names(
    db: AsyncSession,
    vendor_id: uuid.UUID | None,
    cost_center_id: uuid.UUID | None,
    department_id: uuid.UUID | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Returns (vendor_name, cost_center_name, department_name).

    department_name is derived from the explicitly-selected department_id when
    present; falls back to the cost center's department for legacy callers.
    """
    vendor_name: str | None = None
    cost_center_name: str | None = None
    department_name: str | None = None

    if vendor_id:
        vendor = await db.get(Vendor, vendor_id)
        if vendor:
            vendor_name = vendor.name

    if cost_center_id:
        cc = await db.get(CostCenter, cost_center_id)
        if cc:
            cost_center_name = cc.name
            await db.refresh(cc, ["department"])
            if cc.department:
                department_name = cc.department.name  # fallback

    if department_id:
        from app.models.department import Department
        dept = await db.get(Department, department_id)
        if dept:
            department_name = dept.name  # explicit selection wins

    return vendor_name, cost_center_name, department_name
```

- [ ] **Step 4: create/update 存 department_id**

`create()`：L206-208 调用改为传 `payload.department_id`：
```python
    vendor_name, cost_center_name, department_name = await _resolve_names(
        db, payload.vendor_id, payload.cost_center_id, payload.department_id
    )
```
并在 `PurchaseRequest(...)` 里（L224 `department_name=` 旁）加 `department_id=payload.department_id,`。

`update()`：把 `"department_id"` 加进 L258-259 的可更新字段元组；把触发 `needs_name_refresh` 的条件（L263）扩展为 `if field in ("vendor_id", "cost_center_id", "department_id")`；refresh 调用（L270-271）改为传 `pr.department_id`：
```python
        vendor_name, cost_center_name, department_name = await _resolve_names(
            db, pr.vendor_id, pr.cost_center_id, pr.department_id
        )
```

- [ ] **Step 5: 跑测试**

Run: `pytest tests/test_pr_crud_department.py -v` → Expected: PASS。
再跑既有 PR crud 测试确认无回归：`pytest tests/ -k pr -v`。

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/schemas/pr.py epms-api/app/crud/pr.py epms-api/tests/test_pr_crud_department.py
git commit -m "feat(pr): store department_id, derive department_name from it"
```

---

## Task 3: approval-api — 部门驱动审批解析

**Files:**
- Modify: `approval-api/app/models/pr.py:30`（加 `department_id` 列声明）
- Modify: `approval-api/app/crud/engine.py`（新 helper + 3 函数签名 + 调用点）
- Test: `approval-api/tests/test_engine_dept_manager_routing.py`

**Interfaces:**
- Consumes: Task 1 的 `purchase_requests.department_id`（同物理表）。
- Produces: `_routing_department_id(db, doc_type, doc, routing_uid) -> uuid.UUID | None`；`_get_dept_manager_id(db, dept_id)`、`_resolve_gm_or_opm(db, dept_id, rm, dept_gm_opm)`、`_resolve_director(db, dept_id, mapping)` 现按 dept_id 解析（原按 user_id）。

- [ ] **Step 1: approval-api model 加列声明**

`approval-api/app/models/pr.py`，在 `created_by`（L28-30）旁加：
```python
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
```
（同物理表，仅读；无迁移。）

- [ ] **Step 2: 写审批路由测试（先失败）**

扩展 `approval-api/tests/test_engine_dept_manager_routing.py`（参照文件既有 fixture 与断言风格）。新增用例：

```python
@pytest.mark.asyncio
async def test_pr_department_id_drives_dept_manager(db_session, routing_ctx):
    # routing_ctx seeds: dept A + dept B, a dept_manager in each,
    # a requester whose User.department_id = A, and a PR created by them.
    ctx = routing_ctx
    # PR explicitly selects dept B (cross-department filing)
    ctx.pr.department_id = ctx.dept_b_id
    await db_session.flush()
    dept_id = await engine._routing_department_id(db_session, "pr", ctx.pr, ctx.requester_id)
    assert dept_id == ctx.dept_b_id                       # PR selection wins
    mgr = await engine._get_dept_manager_id(db_session, dept_id)
    assert mgr == ctx.dept_b_manager_id                  # NOT dept A's manager


@pytest.mark.asyncio
async def test_null_department_falls_back_to_creator(db_session, routing_ctx):
    ctx = routing_ctx
    ctx.pr.department_id = None
    await db_session.flush()
    dept_id = await engine._routing_department_id(db_session, "pr", ctx.pr, ctx.requester_id)
    assert dept_id == ctx.dept_a_id                       # requester's own department


@pytest.mark.asyncio
async def test_supervisor_unaffected_by_pr_department(db_session, routing_ctx):
    # supervisor stays keyed to the creator, not the selected department
    ctx = routing_ctx
    ctx.pr.department_id = ctx.dept_b_id
    await db_session.flush()
    sup = await engine._resolve_supervisor(db_session, ctx.requester_id, ctx.dept_supervisor_enabled)
    assert sup == ctx.requester_supervisor_id
```

Run: `pytest tests/test_engine_dept_manager_routing.py -v` → Expected: FAIL（`_routing_department_id` 不存在；`_get_dept_manager_id` 仍按 user_id）。

- [ ] **Step 3: 新增 `_routing_department_id` helper**

`engine.py`，紧接 `_routing_user_id`（L468 之后）加：
```python
async def _routing_department_id(
    db: AsyncSession, doc_type: str, doc: Any, routing_uid: uuid.UUID,
) -> uuid.UUID | None:
    """Department that drives dept-based approval routing (dept_manager /
    gm_or_opm / director). Prefer the department explicitly selected on the
    originating PR; fall back to the routing user's own department (legacy)."""
    pr_id = None
    if doc_type == "pr":
        pr_id = doc.id
    elif doc_type == "po":
        pr_id = getattr(doc, "pr_id", None)
    elif doc_type in ("pa", "pa_dir"):
        po_id = getattr(doc, "po_id", None)
        if po_id:
            pr_id = (await db.execute(
                select(PurchaseOrder.pr_id).where(PurchaseOrder.id == po_id)
            )).scalar_one_or_none()
    if pr_id:
        dept = (await db.execute(
            select(PurchaseRequest.department_id).where(PurchaseRequest.id == pr_id)
        )).scalar_one_or_none()
        if dept:
            return dept
    return (await db.execute(
        select(User.department_id).where(User.id == routing_uid)
    )).scalar_one_or_none()
```

- [ ] **Step 4: 3 个解析函数改按 dept_id**

- `_get_dept_manager_id`（L260-272）：参数 `requester_id` → `dept_id`，删去开头两行读 user 的查询，直接用 `dept_id`：
```python
async def _get_dept_manager_id(db: AsyncSession, dept_id: uuid.UUID | None) -> uuid.UUID | None:
    if not dept_id:
        return None
    mgr_result = await db.execute(
        select(User.id).where(
            User.role == "dept_manager",
            User.department_id == dept_id,
            User.is_active.is_(True),
        ).limit(1)
    )
    return mgr_result.scalar_one_or_none()
```
- `_resolve_gm_or_opm`（L378-390）：参数 `creator_id` → `dept_id`，删去读 user，直接用 `dept_id`：
```python
async def _resolve_gm_or_opm(
    db: AsyncSession, dept_id: uuid.UUID | None, rm: dict, dept_gm_opm: dict,
) -> tuple[str, uuid.UUID | None]:
    resolved_role = dept_gm_opm.get(str(dept_id), "gm") if dept_id else "gm"
    uid_str = rm.get(f"{resolved_role}_user_id")
    return resolved_role, (uuid.UUID(uid_str) if uid_str else None)
```
- `_resolve_director`（L403-415）：参数 `routing_uid` → `dept_id`，删去读 user：
```python
async def _resolve_director(
    db: AsyncSession, dept_id: uuid.UUID | None, dept_director_mapping: dict,
) -> uuid.UUID | None:
    if not dept_id:
        return None
    uid_str = dept_director_mapping.get(str(dept_id))
    if not uid_str:
        return None
    return await _active_user_id(db, uuid.UUID(uid_str))
```
- `_resolve_supervisor`（L418-431）：**不改**（保持按 routing_uid 的个人 supervisor）。

- [ ] **Step 5: 改 `execute_action` 调用点**

`engine.py` execute_action：
- L795 之后加：`routing_dept_id = await _routing_department_id(db, doc_type, doc, routing_uid)`
- L799 改：`director_uid = await _resolve_director(db, routing_dept_id, dept_director)`
- L802-803 改：删掉那次 `select(User.department_id)`，直接 `_routing_dept = routing_dept_id`
- L863-867 `_actor_can_approve(...)` 第 5 个位置参数由 `routing_uid` 改为 `routing_dept_id`（配合 Step 6 的签名改名）
- L879-881：删掉重复的 `select(User.department_id)` 查询，`role_map = _build_role_map(rm, dept_gm_opm, routing_dept_id)`
- L883 改：`dept_mgr_id = await _get_dept_manager_id(db, routing_dept_id)`

- [ ] **Step 6: 改 `_actor_can_approve` 与 `_create_approve_task`**

- `_actor_can_approve`（L302-359）：形参 `creator_id: uuid.UUID` 改名 `routing_dept_id: uuid.UUID | None`；L332 `_get_dept_manager_id(db, routing_dept_id)`；L341 `_resolve_gm_or_opm(db, routing_dept_id, rm, dept_gm_opm)`。（director/supervisor 分支已用预解析的 uid，不改。）更新 docstring 里 `creator_id` 措辞。
- `_create_approve_task`（L492-533）：签名加 `routing_dept_id: uuid.UUID | None = None`；L512 `_get_dept_manager_id(db, routing_dept_id)`；L529 `_resolve_gm_or_opm(db, routing_dept_id, rm, dept_gm_opm)`。execute_action 调用处（L849-852）传 `routing_dept_id=routing_dept_id`。

- [ ] **Step 7: 改 resync 路径**

读 `_resync_document`（~L1045-1182）与 `_resolved_assignee_for_step`（~L1026-1042）。比照 Step 5/6：
- `_resync_document` 里 `routing_uid = _routing_user_id(...)`（~L1080）之后加 `routing_dept_id = await _routing_department_id(db, doc_type, doc, routing_uid)`；把该函数内 `_resolve_director(db, routing_uid, ...)` 改为 `routing_dept_id`；把读 `User.department_id`（~L1085-1086）改为直接用 `routing_dept_id`。
- `_resolved_assignee_for_step`（~L1032-1035）：签名把传入的 user_id 改为（或补充）`routing_dept_id`，`_get_dept_manager_id`/`_resolve_gm_or_opm` 改用它；`_resync_document` 调用处传 `routing_dept_id`。

- [ ] **Step 8: 跑测试**

Run: `pytest tests/test_engine_dept_manager_routing.py -v`（新用例 PASS）。
再跑相关回归：`pytest tests/ -k "routing or engine" -v`。修正因签名改动产生的既有测试调用（把传 user_id 处改为传 dept_id / 或经 helper）。
Expected: 全绿。

- [ ] **Step 9: Commit**

```bash
git add approval-api/app/models/pr.py approval-api/app/crud/engine.py approval-api/tests/test_engine_dept_manager_routing.py
git commit -m "feat(approval): route dept_manager/gm_or_opm/director by PR.department_id"
```

---

## Task 4: epms 前端 — 契约类型 + New PR 页部门选择器

**Files:**
- Modify: `epms/src/services/pr.ts`（`CreatePrBody` L59-75、`UpdatePrBody` L77-89、`ApiPr` interface ~L40-57）
- Modify: `epms/src/pages/pr/PrCreatePage.tsx`（hooks L59-64、state L70-84、JSX L500-502、onSubmit L297、handleDraftSave L334）
- Verify: `tsc -p tsconfig.app.json --noEmit`（零新增错）+ 手动/浏览器验证

**Interfaces:**
- Consumes: Task 2 的后端 `department_id`；现有 `useDepartments()` hook（`src/hooks/useDepartments.ts`）。
- Produces: `CreatePrBody.department_id?: string`、`UpdatePrBody.department_id?: string`、`ApiPr.department_id?: string | null`。

- [ ] **Step 1: 契约类型加字段**

`services/pr.ts`：`CreatePrBody` 加 `department_id?: string`；`UpdatePrBody` 加 `department_id?: string`；`ApiPr`（返回类型）加 `department_id?: string | null`。

- [ ] **Step 2: PrCreatePage — 部门 state + 驱动 Cost Center**

- 顶部 import：`import { useDepartments } from '@/hooks/useDepartments'`。
- state（L70 附近）：`const [selectedDepartmentId, setSelectedDepartmentId] = useState<string | undefined>(user?.department_id ?? undefined)`。
- hooks：`const { data: departmentsData } = useDepartments()`；`const departments = (departmentsData?.items ?? []).filter((d) => d.is_active)`。
- Cost Center 筛选（L61-64）：`department_id: user?.department_id ?? undefined` 改为 `department_id: selectedDepartmentId`。

- [ ] **Step 3: PrCreatePage — 插入部门选择器 JSX**

在 Vendor 块结束（L500 `</div>`）与 Budget 块（L502 `{requiresBudget && (`）之间插入（对所有 Type 显示）：
```tsx
                  {/* Department — drives cost center / budget filtering and approval routing */}
                  <div className="flex flex-col gap-1.5">
                    <label className="text-sm font-medium text-neutral-700">
                      Department <span className="text-danger-600">*</span>
                    </label>
                    <select
                      value={selectedDepartmentId ?? ''}
                      onChange={(e) => {
                        setSelectedDepartmentId(e.target.value || undefined)
                        // department changed → clear cost-center cascade (cc belongs to the old dept)
                        setSelectedCostCenter(''); setSelectedCostCenterId(undefined)
                        setSelectedL1(''); setSelectedL1Obj(null); setSelectedL2('')
                      }}
                      className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                    >
                      <option value="">Select Department…</option>
                      {departments.map((d) => (
                        <option key={d.id} value={d.id}>{d.name}</option>
                      ))}
                    </select>
                  </div>
```

- [ ] **Step 4: PrCreatePage — payload 加 department_id**

`onSubmit` 的 `createPr.mutateAsync({...})`（L297 `cost_center_id:` 旁）加 `department_id: selectedDepartmentId,`。`handleDraftSave` 的 mutateAsync（L334 旁）同样加。

- [ ] **Step 5: tsc 门禁 + 手动验证**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c error`（对齐 69 基线，零新增）。
手动/浏览器：New PR 页部门选择器默认=登录用户部门；切部门后 Cost Center 列表随之变化且级联被清空；提交 payload 含 department_id（F12 Network 核对）。

- [ ] **Step 6: Commit**

```bash
git add epms/src/services/pr.ts epms/src/pages/pr/PrCreatePage.tsx
git commit -m "feat(pr-ui): department selector on New PR drives cost center + payload"
```

---

## Task 5: epms 前端 — Edit PR 页部门选择器（含回显）

**Files:**
- Modify: `epms/src/pages/pr/PrEditPage.tsx`（hooks L44、state L69、prefill useEffect、JSX 在 vendor/budget 间、buildPayload L182-186）
- Verify: `tsc` 零新增 + 手动验证

**Interfaces:**
- Consumes: Task 4 的 `UpdatePrBody.department_id`、`ApiPr.department_id`；`useDepartments()`。
- Produces: 无（终端页面）。

- [ ] **Step 1: state + hooks + Cost Center 筛选**

- import `useDepartments`。
- state：`const [selectedDepartmentId, setSelectedDepartmentId] = useState<string | undefined>(undefined)`。
- `const { data: departmentsData } = useDepartments()`；`const departments = (departmentsData?.items ?? []).filter((d) => d.is_active)`。
- Cost Center（L44）：`useCostCenters({ active_only: true })` 改为 `useCostCenters({ department_id: selectedDepartmentId, active_only: true })`。

- [ ] **Step 2: prefill 回显**

在从 `pr` 数据初始化表单的 useEffect（与 `setSelectedCostCenter` 等 prefill 同处）里加：
```tsx
    setSelectedDepartmentId(pr.department_id ?? user?.department_id ?? undefined)
```
（`user` 来自 `useAuthStore`；若该组件未引入，按 PrCreatePage 方式 `const { user } = useAuthStore()`。）

- [ ] **Step 3: 插入选择器 JSX**

在 Edit 表单里 Vendor 与 Budget 之间插入与 Task 4 Step 3 相同的 `<select>` 块（同 onChange：改部门时清 `setSelectedCostCenter('')/setSelectedCostCenterId(undefined)/setSelectedL1('')/setSelectedL1Obj(null)/setSelectedL2('')`，按本页 state 名对齐）。整个表单已受 L263 `['draft','returned']` gate，天然只在 draft/returned 可编辑。

- [ ] **Step 4: buildPayload 加字段**

`buildPayload()`（L182-186 `cost_center_id:` 旁）加 `department_id: selectedDepartmentId,`。

- [ ] **Step 5: tsc + 手动验证**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c error`（零新增）。
手动：编辑 draft PR，部门回显正确；改部门→Cost Center 随之变、级联清空；保存后 detail/PDF 部门更新。

- [ ] **Step 6: Commit**

```bash
git add epms/src/pages/pr/PrEditPage.tsx
git commit -m "feat(pr-ui): department selector on Edit PR with prefill"
```

---

## Task 6: 端到端验证（PDF + 跨部门审批）

**Files:** 无代码改动（验证 + 记录）。PDF 已在 `epms-api/app/services/pdf_pr.py:106` 打印 `department_name`。

- [ ] **Step 1: PDF 部门显示**

创建一张跨部门 PR（选与登录用户不同的部门）→ 提交生成 PDF → 打开 PDF 确认 Meta grid 第 4 行 Department 显示的是**所选部门**（非登录用户部门）。

- [ ] **Step 2: 跨部门审批路由**

登录用户属部门 A，创建 PR 时选部门 B（B 配了自己的 dept_manager / gm_or_opm / director）→ 提交 → 确认审批任务分派给**部门 B 的** dept_manager / gm_or_opm / director，而非部门 A 的。检查 Task Inbox 归属。

- [ ] **Step 3: Type 1 无预算 PR**

创建 Type 1（无 Cost Center）PR，选部门 B → 提交 → 确认部门 B 有 department_id 且审批走部门 B（验证独立字段方案覆盖无 cost center 场景）。

- [ ] **Step 4: 记录验证结果**

在 PR 描述 / 提交说明里记录三项验证的实际结果（正面证据：截图或任务归属），供发布 review。

---

## Self-Review（spec 覆盖核对）

- **需求① 部门选择器（默认 requester 部门，可改）** → Task 4 Step 2-3（default `user.department_id`）、Task 5。✅
- **需求② Budget Account 按部门筛** → Task 4 Step 2（Cost Center `department_id: selectedDepartmentId`，Cost Center 是预算级联顶层）、Task 5 Step 1。✅
- **需求③ 审批按 PR 选择的部门** → Task 3（`_routing_department_id` + dept_manager/gm_or_opm/director 改按 dept_id + resync）。✅
- **需求④ PDF 带部门** → Task 2（department_name 由 department_id 派生）+ Task 6 Step 1（已有渲染）。✅
- **D4 旧数据回填** → Task 1 Step 3 三段 SQL。✅
- **D5 不动在途** → 未调用 resync-inflight 批处理；Task 3 只改解析逻辑，存量任务不主动重算。✅
- **D6 仅 draft 可改部门** → Task 5（PrEditPage L263 gate 天然满足）。✅
- **D7 supervisor 保持按创建人** → Task 3 Step 4（`_resolve_supervisor` 不改）+ Task 3 Step 2 测试。✅

**类型一致性核对**：`_routing_department_id`(db, doc_type, doc, routing_uid)→ `uuid.UUID | None` 在 execute_action/resync 一致；`_get_dept_manager_id(db, dept_id)`、`_resolve_gm_or_opm(db, dept_id, rm, dept_gm_opm)`、`_resolve_director(db, dept_id, mapping)` 全部调用点已在 Task 3 Step 5-7 对齐为传 dept_id；`department_id` 在 schema(uuid)/service(string)/model(UUID) 各层类型正确。

**待实现时确认（非阻塞）**：
- epms-api legacy `crud/pr.py.execute_action` 是否仍 reachable（live 端点已 delegate 到 approval-api）；若仅测试用则不改，否则同步。
- 无状态 resolver `approval-api/app/api/v1/resolution.py` 是否用于 PR 路由；用到则比照 Task 3 对齐。
