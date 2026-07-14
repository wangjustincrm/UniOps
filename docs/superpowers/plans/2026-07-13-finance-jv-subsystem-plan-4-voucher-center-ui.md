# Finance JV Plan 4 — 凭证中心 UI + 科目余额表 / Budget Actual 前端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立部署的 Finance 前端(`finance/`)交付凭证中心(列表/详情/批量审核/批量过账/红冲)与科目余额表、Budget Actual 三个页面，并补齐后端列表分页/搜索、详情维度解析与权限端点。

**Architecture:** 后端只做加法(finance-api `journal_voucher.py` 列表分页+搜索、详情名称/维度解析、`/journal-vouchers/permissions`；新增 `mirrors.Department` 只读镜像)。前端三个新页面进 `finance/src/pages/finance/`，走既有 `financeApi` client + `PortalChromeLayout` + react-query 模式(参照 `GeneralLedgerPage.tsx`)，路由注册进 `routes.tsx`，侧边栏项加进 `AppLayout.tsx` 的 `NAV`(permission=`view_finance`)。凭证详情做成共享 Modal，被三个页面复用(余额表/预算实际钻取到凭证后可直接打开凭证详情)。

**Tech Stack:** FastAPI + SQLAlchemy async (finance-api)、React 19 + @tanstack/react-query + Tailwind + @uniops/shell tab 引擎 (finance 前端)、pytest。

## Global Constraints

- **分支**: 全部工作在 `feature/finance-jv-subsystem`(主目录 `c:/Project/uniops`)。开工前确认 `git branch --show-current` 输出该分支名。
- **⚠️ 工作树里有用户未提交的 invoice-tax WIP**(epms/、epms-api/、expense-api/、portal/ 多文件)。**只允许 `git add <逐个指定文件>`，绝对禁止 `git add -A`/`-a`/`.`、`git stash`、`git reset`、`git checkout --`**。
- **UI 文案纯英文**(会计术语用行业标准英文)；代码注释可中文。
- **金额显示**: 后端 Decimal 序列化为 JSON 字符串，前端必须 `Number()` 强转后再运算/格式化。
- **每个 Portal/Finance 页面**包在 `PortalChromeLayout` 里，不渲染裸 div。
- **权限门禁**: UI 动作按钮以服务端能力端点(`/journal-vouchers/permissions`)判定，**不看 `jwt.role` 前端硬编码**；导航可见性走 Access Control Matrix key `view_finance`(AppLayout 已有机制)。
- **后端测试命令**(Windows，必须带密码 env，用 venv python，本地 uniops_postgres 容器):
  `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <test-file> -q`
  ⚠️ 测试单库串行，**一次只跑一个 pytest 进程，禁止后台并发跑测试**。
- **前端 typecheck 命令**(TS 6.0，`tsc -b` 因 baseUrl 弃用直接报错):
  `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
- **改了 finance-api 代码要 `docker restart uniops_finance_api`**(Windows 挂载不触发 --reload)。前端 vite 容器 `uniops_finance_frontend`(:5177) 热更新，不需要重启。
- **镜像模型必须忠于物理表**: 新镜像列已对 information_schema 核对(2026-07-13，见 Task 2)。

---

## 现状(实现者需知)

已存在、本计划**直接消费**的后端端点(finance-api，前缀 `/finance/v1`):

| 端点 | 说明 |
|---|---|
| `GET /journal-vouchers?period&status&source_doc_type&limit` | 凭证列表(Task 1 改造为分页+搜索) |
| `GET /journal-vouchers/{jv_id}` | 详情 `{voucher, lines}`(Task 2 增强) |
| `POST /journal-vouchers/{id}/review · /unreview · /post · /unpost · /reverse` | 单笔动作；403=权限/SoD，409=状态非法 |
| `POST /journal-vouchers/review-batch · /post-batch` body `{ids:[uuid]}` | 批量，返回 `[{id, ok, error?}]` 风格数组(每项含 `ok: bool`) |
| `GET /gl/account-balance?period=YYYY-MM` | ① 科目余额表 `{period, rows:[{account_code,account_name,account_type,opening,period_debit,period_credit,closing}], totals:{...}, balanced}` |
| `GET /gl/account-balance/{code}/expand?period` | ② 按成本中心展开 `{account_code, period, rows:[{cost_center_id,cost_center_code,cost_center_name,amount}]}` |
| `GET /gl/account-balance/{code}/vouchers?period&cost_center_id` | ③ 凭证钻取 `{account_code, period, rows:[{jv_id,jv_number,voucher_date,summary,local_debit,local_credit,cost_center_id,source_doc_type,source_doc_id,source_doc_number}]}` |
| `GET /gl/budget-actual?period` | ④ `{period, rows:[{account_code,category,cost_center_id,cost_center_code,cost_center_name,actual}]}` category ∈ MOH/RD/SELL/GA |

JV 状态机: `draft →(review)→ reviewed →(post)→ posted →(reverse)→ reversed`；`unreview: reviewed→draft`；`unpost: posted→reviewed`。动作后端角色门 `_JV_ROLES = {finance_manager, finance_bp, system_admin}`(JWT role)。

前端既有模式(照抄，不新造):
- API client: `finance/src/lib/api.ts` 的 `financeApi.get/post`(自带 `/finance/v1` 前缀与错误提取)。
- 页面骨架/样式: `finance/src/pages/finance/GeneralLedgerPage.tsx` —— `PortalChromeLayout`、`inputCls/primaryBtn/secondaryBtn` 常量、`money()` helpers、react-query `useQuery`、局部 `Modal` 组件、zebra 表格、`#085E5E` 主色。
- 路由: `finance/src/app/routes.tsx` 的 `financeRoutes`(tab.icon 是 lucide 图标名字符串，shell `resolveIcon` 解析)。
- 侧边栏: `finance/src/components/layout/AppLayout.tsx` 的 `NAV` 常量(item 带 `permission: 'view_finance'`)。

---

### Task 1: 后端 — JV 列表分页 + 搜索

**Files:**
- Modify: `finance-api/app/api/v1/journal_voucher.py`(`list_vouchers`,约 38-52 行)
- Test: `finance-api/tests/test_jv_api.py`

**Interfaces:**
- Produces: `GET /finance/v1/journal-vouchers?period&status&source_doc_type&q&limit&offset` → `{"total": int, "items": [hdr...]}`。`q` 对 `jv_number`/`summary` ILIKE 模糊；`limit` 默认 50 上限 200；`offset` 默认 0。hdr 字段与现 `_hdr` 相同。**响应从裸数组改为对象 —— 前端 Task 4 依赖 `{total, items}`。**

- [ ] **Step 1: 写失败测试**(加到 `test_jv_api.py` 末尾；同文件已有 `_draft_jv`/`client`/`_h` fixture 复用)

```python
async def test_list_pagination_and_search(client, db_session):
    a = await _draft_jv(db_session)
    b = await _draft_jv(db_session)
    # paginated envelope
    r = await client.get("/finance/v1/journal-vouchers?limit=1&offset=0", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    assert len(body["items"]) == 1
    # offset walks the list
    r2 = await client.get("/finance/v1/journal-vouchers?limit=1&offset=1", headers=_h())
    assert r2.json()["items"][0]["id"] != body["items"][0]["id"]
    # q matches jv_number
    r3 = await client.get(f"/finance/v1/journal-vouchers?q={a.jv_number}", headers=_h())
    ids = [row["id"] for row in r3.json()["items"]]
    assert str(a.id) in ids and str(b.id) not in ids
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_api.py::test_list_pagination_and_search -q`
Expected: FAIL(`KeyError: 'total'` —— 现响应是裸数组)

- [ ] **Step 3: 改 `list_vouchers` 实现**(整个函数替换；`func`/`or_` 需要加 import)

文件顶部 import 行 `from sqlalchemy import select` 改为:

```python
from sqlalchemy import func, or_, select
```

`list_vouchers` 整体替换为:

```python
@router.get("")
async def list_vouchers(_: CurrentUser, db: AsyncSession = Depends(get_db),
                        period: str | None = Query(default=None),
                        status: str | None = Query(default=None),
                        source_doc_type: str | None = Query(default=None),
                        q: str | None = Query(default=None),
                        limit: int = Query(default=50, le=200),
                        offset: int = Query(default=0, ge=0)):
    base = select(JournalVoucher)
    if period:
        base = base.where(JournalVoucher.fiscal_period == period)
    if status:
        base = base.where(JournalVoucher.status == status)
    if source_doc_type:
        base = base.where(JournalVoucher.source_doc_type == source_doc_type)
    if q:
        like = f"%{q}%"
        base = base.where(or_(JournalVoucher.jv_number.ilike(like),
                              JournalVoucher.summary.ilike(like)))
    total = (await db.execute(
        select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(
        base.order_by(JournalVoucher.voucher_date.desc(),
                      JournalVoucher.jv_number.desc())
        .offset(offset).limit(limit))).scalars().all()
    return {"total": total, "items": [_hdr(jv) for jv in rows]}
```

- [ ] **Step 4: 修复受影响的旧测试** —— `test_jv_api.py::test_list_and_get_detail` 第 56 行:

```python
    assert any(row["id"] == str(jv.id) for row in r.json()["items"])
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_api.py -q`
Expected: 全部 PASS(5 个)

- [ ] **Step 6: Commit(surgical add)**

```bash
cd c:/Project/uniops
git add finance-api/app/api/v1/journal_voucher.py finance-api/tests/test_jv_api.py
git commit -m "feat(finance): JV list pagination + jv_number/summary search"
```

---

### Task 2: 后端 — 权限端点 + 详情增强(三岗姓名/成本中心/部门/维度) + Department 镜像

**Files:**
- Modify: `finance-api/app/models/mirrors.py`(加 `Department`)
- Modify: `finance-api/tests/conftest.py`(镜像表 import + create_all 列表加 `Department`)
- Modify: `finance-api/app/api/v1/journal_voucher.py`(`get_voucher` 增强 + 新 `/permissions` 路由)
- Test: `finance-api/tests/test_jv_api.py`

**Interfaces:**
- Consumes: `mirrors.User`(id/email/full_name)、`mirrors.CostCenter`(id/code/name)、`app.models.coa.ChartOfAccount`(code/name)、`app.models.journal_voucher.JvLineDimension`。
- Produces:
  - `GET /finance/v1/journal-vouchers/permissions` → `{"can_act": bool}`(role ∈ `_JV_ROLES`；与后端动作门同一集合，UI 据此显隐动作按钮)。**必须定义在 `/{jv_id}` 动态路由之前**，否则 FastAPI 会把 "permissions" 当 UUID 解析 → 422。
  - `GET /journal-vouchers/{jv_id}` 详情:
    - `voucher` 在 `_hdr` 基础上追加: `prepared_by_name/prepared_at/reviewed_by_name/reviewed_at/posted_by_name/posted_at`(名字查不到时 None)、`nc_source_pk`、`source_service`。
    - 每条 `line` 追加: `account_name`、`quantity/unit/price`(字符串或 None)、`cost_center_code/cost_center_name`、`department_code/department_name`、`dims: [{dim_code, value_text}]`。

- [ ] **Step 1: 加 `Department` 镜像模型**(`mirrors.py`，插在 `CostCenter` 类之后；列已于 2026-07-13 对本地 dev 库 information_schema 核对: id/code/name/is_active/created_at/updated_at，全 NOT NULL)

```python
class Department(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror of the shared `departments` master (epms owns schema).
    Columns verified against information_schema 2026-07-13:
    id/code/name/is_active/created_at/updated_at (all NOT NULL)."""
    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

- [ ] **Step 2: conftest 注册镜像表** —— `tests/conftest.py` 两处:

第 58-61 行 import 列表加 `Department`:

```python
    from app.models.mirrors import (  # noqa: F401
        CompanyConfig, CostCenter, Department, ExpenseApprovalEvent, ExpenseClaim,
        ExpenseLineItem, ExpenseTripItem, Invoice, InvoiceTaxLine, SodRule, Task, User,
    )
```

第 64-70 行 `create_all` tables 列表在 `CostCenter.__table__,` 后加:

```python
        Department.__table__,
```

- [ ] **Step 3: 写失败测试**(加到 `test_jv_api.py` 末尾)

```python
async def test_jv_permissions_endpoint(client, db_session):
    r = await client.get("/finance/v1/journal-vouchers/permissions", headers=_h())
    assert r.status_code == 200          # not 422 — route must sit above /{jv_id}
    assert r.json()["can_act"] is True
    r2 = await client.get("/finance/v1/journal-vouchers/permissions",
                          headers=_h(role="requester"))
    assert r2.json()["can_act"] is False


async def test_detail_resolves_names_and_dims(client, db_session):
    from app.models.journal_voucher import JournalVoucherLine, JvLineDimension
    from app.models.mirrors import CostCenter, Department, User

    preparer = uuid.uuid4()
    db_session.add(User(id=preparer, email="fin@x.com", full_name="Fin Preparer"))
    cc = CostCenter(id=uuid.uuid4(), code="MOH-0104-P02", name="Processing", is_active=True)
    dept = Department(id=uuid.uuid4(), code="0104", name="Production", is_active=True)
    db_session.add_all([cc, dept])
    await db_session.flush()

    jv = await _draft_jv(db_session)
    jv.prepared_by = preparer
    line = (await db_session.execute(select(JournalVoucherLine).where(
        JournalVoucherLine.jv_id == jv.id).order_by(JournalVoucherLine.line_no))
        ).scalars().first()
    line.cost_center_id = cc.id
    line.department_id = dept.id
    db_session.add(JvLineDimension(jv_line_id=line.id, dim_code="income_expense_item",
                                   value_text="CRM004"))
    await db_session.flush()

    r = await client.get(f"/finance/v1/journal-vouchers/{jv.id}", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["voucher"]["prepared_by_name"] == "Fin Preparer"
    ln = next(l for l in body["lines"] if l["line_no"] == line.line_no)
    assert ln["cost_center_code"] == "MOH-0104-P02"
    assert ln["department_name"] == "Production"
    assert {"dim_code": "income_expense_item", "value_text": "CRM004"} in [
        {"dim_code": d["dim_code"], "value_text": d["value_text"]} for d in ln["dims"]]
```

- [ ] **Step 4: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_api.py::test_jv_permissions_endpoint tests/test_jv_api.py::test_detail_resolves_names_and_dims -q`
Expected: FAIL(permissions 路由 422/404；详情缺 `prepared_by_name`)

- [ ] **Step 5: 实现** —— `journal_voucher.py`:

(a) 在 `list_vouchers` 之后、`get_voucher` 之前插入(**顺序关键**，`/permissions` 必须先于 `/{jv_id}` 注册):

```python
@router.get("/permissions")
async def jv_permissions(user: CurrentUser):
    """UI capability gate — same role set the lifecycle actions enforce."""
    from app.crud.journal_voucher import _JV_ROLES
    return {"can_act": user.get("role") in _JV_ROLES}
```

(b) `get_voucher` 整体替换:

```python
@router.get("/{jv_id}")
async def get_voucher(jv_id: uuid.UUID, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    jv = await crud.get(db, jv_id)
    if jv is None:
        raise HTTPException(status_code=404, detail="Journal voucher not found")
    lines = (await db.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv_id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()

    from app.models.coa import ChartOfAccount
    from app.models.journal_voucher import JvLineDimension
    from app.models.mirrors import CostCenter, Department, User

    async def _lookup(model, ids, key=lambda r: r.id):
        ids = {i for i in ids if i is not None}
        if not ids:
            return {}
        rows = (await db.execute(select(model).where(model.id.in_(ids)))).scalars().all()
        return {key(r): r for r in rows}

    users = await _lookup(User, {jv.prepared_by, jv.reviewed_by, jv.posted_by})
    ccs = await _lookup(CostCenter, {ln.cost_center_id for ln in lines})
    depts = await _lookup(Department, {ln.department_id for ln in lines})
    codes = {ln.account_code for ln in lines if ln.account_code}
    coa = {}
    if codes:
        coa = {a.code: a for a in (await db.execute(
            select(ChartOfAccount).where(ChartOfAccount.code.in_(codes)))).scalars()}
    dims_by_line: dict = {}
    line_ids = [ln.id for ln in lines]
    if line_ids:
        for d in (await db.execute(select(JvLineDimension).where(
                JvLineDimension.jv_line_id.in_(line_ids)))).scalars():
            dims_by_line.setdefault(d.jv_line_id, []).append(
                {"dim_code": d.dim_code, "value_text": d.value_text})

    def _name(uid):
        u = users.get(uid)
        return u.full_name if u else None

    def _iso(dt):
        return dt.isoformat() if dt else None

    voucher = _hdr(jv) | {
        "source_service": jv.source_service, "nc_source_pk": jv.nc_source_pk,
        "prepared_by_name": _name(jv.prepared_by), "prepared_at": _iso(jv.prepared_at),
        "reviewed_by_name": _name(jv.reviewed_by), "reviewed_at": _iso(jv.reviewed_at),
        "posted_by_name": _name(jv.posted_by), "posted_at": _iso(jv.posted_at),
    }

    def _line(ln: JournalVoucherLine) -> dict:
        cc, dept = ccs.get(ln.cost_center_id), depts.get(ln.department_id)
        acct = coa.get(ln.account_code) if ln.account_code else None
        return {
            "line_no": ln.line_no, "account_code": ln.account_code,
            "account_name": acct.name if acct else None, "summary": ln.summary,
            "orig_debit": str(ln.orig_debit), "orig_credit": str(ln.orig_credit),
            "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
            "currency": ln.currency, "fx_rate": str(ln.fx_rate),
            "quantity": str(ln.quantity) if ln.quantity is not None else None,
            "unit": ln.unit,
            "price": str(ln.price) if ln.price is not None else None,
            "cost_center_code": cc.code if cc else None,
            "cost_center_name": cc.name if cc else None,
            "department_code": dept.code if dept else None,
            "department_name": dept.name if dept else None,
            "partner_name": ln.partner_name, "tax_code": ln.tax_code,
            "dims": dims_by_line.get(ln.id, []),
        }

    return {"voucher": voucher, "lines": [_line(ln) for ln in lines]}
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_jv_api.py -q`
Expected: 全部 PASS(7 个)

- [ ] **Step 7: 回归一遍 JV 相关套件(单进程串行)**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_journal_voucher.py tests/test_jv_lifecycle.py tests/test_jv_api.py tests/test_jv_backfill.py tests/test_account_balance.py -q`
Expected: 全部 PASS

- [ ] **Step 8: Commit + 重启 dev 容器**

```bash
cd c:/Project/uniops
git add finance-api/app/models/mirrors.py finance-api/tests/conftest.py finance-api/app/api/v1/journal_voucher.py finance-api/tests/test_jv_api.py
git commit -m "feat(finance): JV permissions endpoint + detail name/dimension resolution (+Department mirror)"
docker restart uniops_finance_api
```

---

### Task 3: 前端 — 共享凭证详情 Modal + 凭证钻取 Modal

**Files:**
- Create: `finance/src/pages/finance/JvDetailModal.tsx`
- Create: `finance/src/pages/finance/AccountVouchersModal.tsx`

**Interfaces:**
- Consumes: `financeApi`(`@/lib/api`)、`cn`(`@/lib/utils`)、Task 2 的详情/permissions 端点、③ 钻取端点。
- Produces(Task 4/5/6 依赖，签名精确):
  - `JvDetailModal({ jvId, canAct, onClose, onActed }: { jvId: string; canAct: boolean; onClose: () => void; onActed?: () => void })` —— 详情 + 生命周期动作按钮(按状态显隐: draft→Review；reviewed→Unreview/Post；posted→Unpost/Reverse)。动作成功后 invalidate `['jv-detail', jvId]` 并调 `onActed?.()`。
  - `AccountVouchersModal({ accountCode, period, costCenterId, title, onClose, onOpenJv }: { accountCode: string; period: string; costCenterId?: string | null; title: string; onClose: () => void; onOpenJv: (jvId: string) => void })` —— ③ 钻取行列表，行内凭证号按钮回调 `onOpenJv(jv_id)`。
  - 两文件各自导出 `money(v: string)`? **否** —— money helper 每页局部定义(照 GL 页惯例)，modal 内各自定义局部 helper，不跨文件导出。

- [ ] **Step 1: 写 `JvDetailModal.tsx`**(完整文件)

```tsx
/**
 * Journal voucher detail modal — header, dual-currency lines, dimensions and
 * lifecycle actions (review / unreview / post / unpost / reverse). Shared by
 * the Journal Vouchers page and the Account Balance / Budget Actual drills.
 * Styling follows GeneralLedgerPage (neutral palette, #085E5E primary).
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Check, Loader2, RotateCcw, Send, Undo2, X } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'

const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'
const dangerBtn = 'flex items-center gap-1.5 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50'

function money(v: string | null | undefined) {
  const n = Number(v ?? 0)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ''
}

export interface JvHeader {
  id: string; jv_number: string; voucher_word: string; voucher_date: string
  fiscal_period: string; summary: string | null; status: string
  source_service?: string | null
  source_doc_type: string | null; source_doc_id: string | null; source_doc_number: string | null
  total_debit: string; total_credit: string
  total_local_debit: string; total_local_credit: string
  reverses_jv_id: string | null; reversed_by_jv_id: string | null
  prepared_by_name?: string | null; prepared_at?: string | null
  reviewed_by_name?: string | null; reviewed_at?: string | null
  posted_by_name?: string | null; posted_at?: string | null
  nc_source_pk?: string | null
}
interface JvLine {
  line_no: number; account_code: string | null; account_name: string | null
  summary: string | null
  orig_debit: string; orig_credit: string; local_debit: string; local_credit: string
  currency: string; fx_rate: string
  quantity: string | null; unit: string | null; price: string | null
  cost_center_code: string | null; cost_center_name: string | null
  department_code: string | null; department_name: string | null
  partner_name: string | null; tax_code: string | null
  dims: { dim_code: string; value_text: string | null }[]
}
interface JvDetail { voucher: JvHeader; lines: JvLine[] }

export const JV_STATUS_STYLE: Record<string, string> = {
  draft: 'bg-neutral-100 text-neutral-600',
  reviewed: 'bg-blue-50 text-blue-700',
  posted: 'bg-green-50 text-green-700',
  reversed: 'bg-red-50 text-red-700',
}

export function JvStatusBadge({ status }: { status: string }) {
  return (
    <span className={cn('inline-flex rounded-full px-2 py-0.5 text-xs font-medium capitalize',
      JV_STATUS_STYLE[status] ?? 'bg-neutral-100 text-neutral-600')}>
      {status}
    </span>
  )
}

function Stamp({ label, name, at }: { label: string; name?: string | null; at?: string | null }) {
  return (
    <div className="text-xs text-neutral-500">
      <span className="font-medium text-neutral-600">{label}:</span>{' '}
      {name || '—'}{at ? ` · ${at.slice(0, 16).replace('T', ' ')}` : ''}
    </div>
  )
}

export function JvDetailModal({ jvId, canAct, onClose, onActed }: {
  jvId: string; canAct: boolean; onClose: () => void; onActed?: () => void
}) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['jv-detail', jvId],
    queryFn: () => financeApi.get<JvDetail>(`/journal-vouchers/${jvId}`),
  })

  const act = async (action: string, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(action); setErr(null)
    try {
      await financeApi.post(`/journal-vouchers/${jvId}/${action}`, {})
      await qc.invalidateQueries({ queryKey: ['jv-detail', jvId] })
      onActed?.()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }

  const v = data?.voucher
  const multiCurrency = data?.lines.some((l) => l.currency !== 'CAD') ?? false

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-800">
            <BookOpen className="h-4 w-4 text-neutral-400" />
            {v ? `${v.voucher_word} · ${v.jv_number}` : 'Journal Voucher'}
            {v && <JvStatusBadge status={v.status} />}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        {isLoading && <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>}

        {v && (
          <>
            <div className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 rounded-lg bg-neutral-50 px-4 py-3 md:grid-cols-3">
              <div className="text-xs text-neutral-500"><span className="font-medium text-neutral-600">Date:</span> {v.voucher_date} · {v.fiscal_period}</div>
              <div className="text-xs text-neutral-500 md:col-span-2"><span className="font-medium text-neutral-600">Summary:</span> {v.summary || '—'}</div>
              <div className="text-xs text-neutral-500 md:col-span-3">
                <span className="font-medium text-neutral-600">Source:</span>{' '}
                {v.source_doc_type ? `${v.source_doc_type} · ${v.source_doc_number ?? v.source_doc_id}` : (v.nc_source_pk ? `NC65 import · ${v.nc_source_pk}` : '—')}
              </div>
              <Stamp label="Prepared" name={v.prepared_by_name} at={v.prepared_at} />
              <Stamp label="Reviewed" name={v.reviewed_by_name} at={v.reviewed_at} />
              <Stamp label="Posted" name={v.posted_by_name} at={v.posted_at} />
            </div>

            {err && <div className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}

            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    <th className="px-3 py-2 w-8">#</th>
                    <th className="px-3 py-2">Account</th>
                    <th className="px-3 py-2">Summary / Dimensions</th>
                    {multiCurrency && <th className="px-3 py-2 w-24 text-right">Orig Dr</th>}
                    {multiCurrency && <th className="px-3 py-2 w-24 text-right">Orig Cr</th>}
                    <th className="px-3 py-2 w-28 text-right">Debit (CAD)</th>
                    <th className="px-3 py-2 w-28 text-right">Credit (CAD)</th>
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map((l, i) => (
                    <tr key={l.line_no} className={cn('border-t border-neutral-100 align-top', i % 2 && 'bg-neutral-50/40')}>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-400">{l.line_no}</td>
                      <td className="px-3 py-2">
                        <span className="font-mono text-xs">{l.account_code ?? '—'}</span>
                        {l.account_name && <span className="ml-1 text-neutral-600">{l.account_name}</span>}
                      </td>
                      <td className="px-3 py-2">
                        {l.summary && <div className="text-neutral-700">{l.summary}</div>}
                        <div className="mt-0.5 flex flex-wrap gap-1">
                          {l.cost_center_code && <Dim label={`CC ${l.cost_center_code}`} title={l.cost_center_name} />}
                          {l.department_code && <Dim label={`Dept ${l.department_code}`} title={l.department_name} />}
                          {l.partner_name && <Dim label={l.partner_name} />}
                          {l.tax_code && <Dim label={`Tax ${l.tax_code}`} />}
                          {l.dims.map((d, j) => <Dim key={j} label={`${d.dim_code}: ${d.value_text ?? ''}`} />)}
                          {l.currency !== 'CAD' && <Dim label={`${l.currency} @ ${l.fx_rate}`} />}
                          {l.quantity && <Dim label={`${l.quantity} ${l.unit ?? ''} @ ${l.price ?? ''}`} />}
                        </div>
                      </td>
                      {multiCurrency && <td className="px-3 py-2 text-right font-mono">{money(l.orig_debit)}</td>}
                      {multiCurrency && <td className="px-3 py-2 text-right font-mono">{money(l.orig_credit)}</td>}
                      <td className="px-3 py-2 text-right font-mono">{money(l.local_debit)}</td>
                      <td className="px-3 py-2 text-right font-mono">{money(l.local_credit)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-neutral-200 bg-neutral-50 font-semibold">
                    <td className="px-3 py-2" colSpan={multiCurrency ? 5 : 3}>Totals (CAD)</td>
                    <td className="px-3 py-2 text-right font-mono">{money(v.total_local_debit)}</td>
                    <td className="px-3 py-2 text-right font-mono">{money(v.total_local_credit)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>

            {canAct && (
              <div className="mt-4 flex justify-end gap-2 border-t border-neutral-100 pt-3">
                {v.status === 'draft' && (
                  <button onClick={() => act('review')} disabled={!!busy} className={primaryBtn}>
                    {busy === 'review' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Review
                  </button>
                )}
                {v.status === 'reviewed' && (
                  <>
                    <button onClick={() => act('unreview')} disabled={!!busy} className={secondaryBtn}>
                      {busy === 'unreview' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Undo2 className="h-4 w-4" />} Unreview
                    </button>
                    <button onClick={() => act('post')} disabled={!!busy} className={primaryBtn}>
                      {busy === 'post' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Post
                    </button>
                  </>
                )}
                {v.status === 'posted' && (
                  <>
                    <button onClick={() => act('unpost')} disabled={!!busy} className={secondaryBtn}>
                      {busy === 'unpost' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Undo2 className="h-4 w-4" />} Unpost
                    </button>
                    <button
                      onClick={() => act('reverse', 'Create a posted red-flush voucher that offsets this one, and mark it reversed?')}
                      disabled={!!busy} className={dangerBtn}>
                      {busy === 'reverse' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />} Reverse
                    </button>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Dim({ label, title }: { label: string; title?: string | null }) {
  return (
    <span title={title ?? undefined}
          className="inline-flex rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-600">
      {label}
    </span>
  )
}
```

- [ ] **Step 2: 写 `AccountVouchersModal.tsx`**(完整文件)

```tsx
/**
 * Voucher drill-down modal (能力③) — the posted JV lines composing one account
 * (+optional cost center) in a period. Each row links into JvDetailModal via
 * onOpenJv. Used by AccountBalancePage and BudgetActualPage.
 */
import { useQuery } from '@tanstack/react-query'
import { BookOpen, Loader2, X } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'

function money(v: string | null | undefined) {
  const n = Number(v ?? 0)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ''
}

interface VoucherRow {
  jv_id: string; jv_number: string; voucher_date: string; summary: string | null
  local_debit: string; local_credit: string; cost_center_id: string | null
  source_doc_type: string | null; source_doc_id: string | null; source_doc_number: string | null
}
interface VouchersResp { account_code: string; period: string; rows: VoucherRow[] }

export function AccountVouchersModal({ accountCode, period, costCenterId, title, onClose, onOpenJv }: {
  accountCode: string; period: string; costCenterId?: string | null
  title: string; onClose: () => void; onOpenJv: (jvId: string) => void
}) {
  const qs = costCenterId ? `?period=${period}&cost_center_id=${costCenterId}` : `?period=${period}`
  const { data, isLoading } = useQuery({
    queryKey: ['ab-vouchers', accountCode, period, costCenterId ?? ''],
    queryFn: () => financeApi.get<VouchersResp>(`/gl/account-balance/${accountCode}/vouchers${qs}`),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-4xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-800">
            <BookOpen className="h-4 w-4 text-neutral-400" /> {title}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>
        {isLoading ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <th className="px-3 py-2 w-24">Date</th>
                  <th className="px-3 py-2 w-36">Voucher</th>
                  <th className="px-3 py-2">Summary</th>
                  <th className="px-3 py-2 w-28 text-right">Debit</th>
                  <th className="px-3 py-2 w-28 text-right">Credit</th>
                </tr>
              </thead>
              <tbody>
                {(data?.rows ?? []).length === 0 && (
                  <tr><td colSpan={5} className="px-3 py-6 text-center text-neutral-400">No vouchers.</td></tr>
                )}
                {(data?.rows ?? []).map((r, i) => (
                  <tr key={`${r.jv_id}-${i}`} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                    <td className="px-3 py-2 font-mono text-xs text-neutral-600">{r.voucher_date}</td>
                    <td className="px-3 py-2">
                      <button onClick={() => onOpenJv(r.jv_id)}
                              className="font-mono text-xs text-[#085E5E] hover:underline">
                        {r.jv_number}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-neutral-700">{r.summary || '—'}</td>
                    <td className="px-3 py-2 text-right font-mono">{money(r.local_debit)}</td>
                    <td className="px-3 py-2 text-right font-mono">{money(r.local_credit)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Typecheck**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 0 errors(⚠️ 验证要正面证据 —— 确认命令真的跑完且退出码 0，不要用"无输出=通过"糊弄；先跑一次故意留个错也行)

- [ ] **Step 4: Commit**

```bash
cd c:/Project/uniops
git add finance/src/pages/finance/JvDetailModal.tsx finance/src/pages/finance/AccountVouchersModal.tsx
git commit -m "feat(finance-ui): shared JV detail modal + account voucher drill-down modal"
```

---

### Task 4: 前端 — 凭证中心页(Journal Vouchers) + 路由/导航

**Files:**
- Create: `finance/src/pages/finance/JournalVouchersPage.tsx`
- Modify: `finance/src/app/routes.tsx`
- Modify: `finance/src/components/layout/AppLayout.tsx`(NAV Finance 区)

**Interfaces:**
- Consumes: Task 1 列表 `{total, items}`、Task 2 `/journal-vouchers/permissions` `{can_act}`、批量端点 `review-batch`/`post-batch`(`{ids: string[]}` → `[{ok: boolean, ...}]`)、Task 3 `JvDetailModal`/`JvStatusBadge`。
- Produces: 路由 `/finance/journal-vouchers`。

- [ ] **Step 1: 写 `JournalVouchersPage.tsx`**(完整文件)

```tsx
/**
 * Journal Voucher Center (凭证中心) — Plan 4.
 * List with period/status/search filters + pagination, row selection with
 * batch Review / batch Post, and the shared JvDetailModal for detail +
 * single-voucher lifecycle actions. Styling follows GeneralLedgerPage.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronLeft, ChevronRight, Loader2, Search, Send } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { JvDetailModal, JvStatusBadge, type JvHeader } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

const PAGE_SIZE = 50
const STATUSES = ['', 'draft', 'reviewed', 'posted', 'reversed'] as const

interface JvList { total: number; items: JvHeader[] }

function money(v: string) {
  const n = Number(v)
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

export default function JournalVouchersPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [period, setPeriod] = useState(thisMonth())
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [qInput, setQInput] = useState('')
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [detailId, setDetailId] = useState<string | null>(null)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState<'review' | 'post' | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })
  const canAct = perms?.can_act ?? false

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) })
    if (period) p.set('period', period)
    if (status) p.set('status', status)
    if (q) p.set('q', q)
    return p.toString()
  }, [period, status, q, page])

  const list = useQuery({
    queryKey: ['jv-list', params],
    queryFn: () => financeApi.get<JvList>(`/journal-vouchers?${params}`),
  })
  const items = list.data?.items ?? []
  const total = list.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const flash = (kind: 'ok' | 'err', text: string) => {
    setBanner({ kind, text }); setTimeout(() => setBanner(null), 6000)
  }
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['jv-list'] })
    setSelected(new Set())
  }
  const resetPage = () => { setPage(0); setSelected(new Set()) }

  const toggle = (id: string) => setSelected((p) => {
    const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n
  })
  const selectable = items.filter((v) => v.status === 'draft' || v.status === 'reviewed')
  const allSelected = selectable.length > 0 && selectable.every((v) => selected.has(v.id))
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(selectable.map((v) => v.id)))

  const runBatch = async (action: 'review' | 'post') => {
    const eligible = items.filter((v) => selected.has(v.id) &&
      (action === 'review' ? v.status === 'draft' : v.status === 'reviewed'))
    if (eligible.length === 0) return flash('err', `No selected ${action === 'review' ? 'draft' : 'reviewed'} vouchers`)
    setBusy(action)
    try {
      const res = await financeApi.post<{ ok: boolean; error?: string }[]>(
        `/journal-vouchers/${action}-batch`, { ids: eligible.map((v) => v.id) })
      const ok = res.filter((r) => r.ok).length
      const failed = res.length - ok
      flash(failed ? 'err' : 'ok',
        failed ? `${action === 'review' ? 'Reviewed' : 'Posted'} ${ok}, failed ${failed} (SoD / closed period / state)`
               : `${action === 'review' ? 'Reviewed' : 'Posted'} ${ok} voucher${ok === 1 ? '' : 's'}`)
      refresh()
    } catch (e) { flash('err', (e as Error).message) } finally { setBusy(null) }
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/journal-vouchers"
      title="Journal Vouchers"
      subtitle="Voucher center — review, post, and trace formal GL journal vouchers"
    >
      <div className="mx-auto max-w-7xl">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <input type="month" value={period}
                 onChange={(e) => { setPeriod(e.target.value); resetPage() }}
                 className={cn(inputCls, 'w-40')} />
          <select value={status} onChange={(e) => { setStatus(e.target.value); resetPage() }} className={inputCls}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? s[0].toUpperCase() + s.slice(1) : 'All statuses'}</option>)}
          </select>
          <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); resetPage() }}>
            <input value={qInput} onChange={(e) => setQInput(e.target.value)}
                   placeholder="Voucher no. / summary…" className={cn(inputCls, 'w-56')} />
            <button type="submit" className={secondaryBtn}><Search className="h-4 w-4" /></button>
          </form>
          {canAct && (
            <div className="ml-auto flex items-center gap-2">
              <button onClick={() => runBatch('review')} disabled={!!busy || selected.size === 0} className={secondaryBtn}>
                {busy === 'review' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                Review Selected
              </button>
              <button onClick={() => runBatch('post')} disabled={!!busy || selected.size === 0} className={primaryBtn}>
                {busy === 'post' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                Post Selected
              </button>
            </div>
          )}
        </div>

        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
            {banner.text}
          </div>
        )}

        {list.isFetching && !list.data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <>
            <div className="overflow-hidden rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    {canAct && (
                      <th className="w-9 px-3 py-2">
                        <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                      </th>
                    )}
                    <th className="px-3 py-2 w-36">Voucher No.</th>
                    <th className="px-3 py-2 w-24">Date</th>
                    <th className="px-3 py-2">Summary</th>
                    <th className="px-3 py-2 w-32">Source</th>
                    <th className="px-3 py-2 w-32 text-right">Debit (CAD)</th>
                    <th className="px-3 py-2 w-24">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 && (
                    <tr><td colSpan={canAct ? 7 : 6} className="px-3 py-6 text-center text-neutral-400">No vouchers match the filters.</td></tr>
                  )}
                  {items.map((v, i) => (
                    <tr key={v.id} onClick={() => setDetailId(v.id)}
                        className={cn('cursor-pointer border-t border-neutral-100 hover:bg-primary-50/40', i % 2 && 'bg-neutral-50/40')}>
                      {canAct && (
                        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                          {(v.status === 'draft' || v.status === 'reviewed') && (
                            <input type="checkbox" checked={selected.has(v.id)} onChange={() => toggle(v.id)} />
                          )}
                        </td>
                      )}
                      <td className="px-3 py-2 font-mono text-xs">{v.voucher_word} · {v.jv_number}</td>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-600">{v.voucher_date}</td>
                      <td className="px-3 py-2 text-neutral-700">{v.summary || '—'}</td>
                      <td className="px-3 py-2 text-xs text-neutral-500">
                        {v.source_doc_type ? `${v.source_doc_type} · ${v.source_doc_number ?? ''}` : '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">{money(v.total_local_debit)}</td>
                      <td className="px-3 py-2"><JvStatusBadge status={v.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-3 flex items-center justify-between text-sm text-neutral-500">
              <span>{total.toLocaleString()} voucher{total === 1 ? '' : 's'}</span>
              <div className="flex items-center gap-2">
                <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0} className={secondaryBtn}>
                  <ChevronLeft className="h-4 w-4" /> Prev
                </button>
                <span>Page {page + 1} / {pageCount}</span>
                <button onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1} className={secondaryBtn}>
                  Next <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {detailId && (
        <JvDetailModal jvId={detailId} canAct={canAct}
                       onClose={() => setDetailId(null)} onActed={refresh} />
      )}
    </PortalChromeLayout>
  )
}
```

- [ ] **Step 2: 注册路由** —— `finance/src/app/routes.tsx`:

import 区加:

```tsx
import JournalVouchersPage from '@/pages/finance/JournalVouchersPage'
```

`financeRoutes` 数组里 General Ledger 行之后插入:

```tsx
  { path: '/finance/journal-vouchers', element: <JournalVouchersPage />, tab: { title: 'Journal Vouchers', icon: 'FileText', keyStrategy: 'static' } },
```

- [ ] **Step 3: 加侧边栏项** —— `AppLayout.tsx`:

lucide import 里加 `FileText`(第 3-8 行的 import 大括号内)。`NAV` Finance section 的 General Ledger 行之后插入:

```tsx
      { label: 'Journal Vouchers', href: '/finance/journal-vouchers', icon: FileText, permission: 'view_finance' },
```

- [ ] **Step 4: Typecheck**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 0 errors

- [ ] **Step 5: 浏览器冒烟**(dev 栈 `uniops_finance_frontend` :5177 vite 热更新，无需重启)

打开 `http://localhost:5177/finance/journal-vouchers`(经 Portal 登录跳转)。预期: 侧边栏出现 Journal Vouchers；默认期间=当月可能为空 → 把期间改到 `2024-06` 应看到 NC 导入的 posted 凭证；点行开详情(分录+维度 chips+三岗)；draft 凭证可勾选批审。

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance/src/pages/finance/JournalVouchersPage.tsx finance/src/app/routes.tsx finance/src/components/layout/AppLayout.tsx
git commit -m "feat(finance-ui): Journal Voucher Center page (list/filters/batch review+post/detail)"
```

---

### Task 5: 前端 — 科目余额表页(Account Balance)

**Files:**
- Create: `finance/src/pages/finance/AccountBalancePage.tsx`
- Modify: `finance/src/app/routes.tsx`
- Modify: `finance/src/components/layout/AppLayout.tsx`

**Interfaces:**
- Consumes: `GET /gl/account-balance?period`、`GET /gl/account-balance/{code}/expand?period`、Task 3 `AccountVouchersModal`/`JvDetailModal`、`/journal-vouchers/permissions`。
- Produces: 路由 `/finance/account-balance`。

- [ ] **Step 1: 写 `AccountBalancePage.tsx`**(完整文件)

```tsx
/**
 * Account Balance report (科目余额表) — Plan 4, 能力①②③ UI.
 * Per-account opening / period Dr / period Cr / closing over POSTED JV lines
 * (local CAD). Rows expand inline by cost center (能力②); every row and
 * expansion drills into its composing vouchers (能力③ → JvDetailModal).
 */
import { Fragment, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Loader2, Scale } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccountVouchersModal } from './AccountVouchersModal'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const linkBtn = 'text-xs font-medium text-[#085E5E] hover:underline'

interface AbRow {
  account_code: string; account_name: string; account_type: string | null
  opening: string; period_debit: string; period_credit: string; closing: string
}
interface AbResp {
  period: string; rows: AbRow[]
  totals: { period_debit: string; period_credit: string; closing: string }
  balanced: boolean
}
interface ExpandRow { cost_center_id: string | null; cost_center_code: string | null; cost_center_name: string | null; amount: string }
interface ExpandResp { account_code: string; period: string; rows: ExpandRow[] }

function money(v: string) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

interface Drill { accountCode: string; costCenterId?: string | null; title: string }

export default function AccountBalancePage() {
  const { user } = useAuthStore()
  const [period, setPeriod] = useState(thisMonth())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [drill, setDrill] = useState<Drill | null>(null)
  const [jvId, setJvId] = useState<string | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })

  const { data, isFetching } = useQuery({
    queryKey: ['account-balance', period],
    queryFn: () => financeApi.get<AbResp>(`/gl/account-balance?period=${period}`),
  })

  const toggle = (code: string) => setExpanded((p) => {
    const n = new Set(p); if (n.has(code)) n.delete(code); else n.add(code); return n
  })

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/account-balance"
      title="Account Balance"
      subtitle="Opening / period movement / closing per account over posted journal vouchers (CAD)"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          <input type="month" value={period}
                 onChange={(e) => { setPeriod(e.target.value); setExpanded(new Set()) }}
                 className={cn(inputCls, 'w-40')} />
          {data && (
            <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
              data.balanced ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700')}>
              <Scale className="h-3 w-3" /> {data.balanced ? 'Balanced' : 'Out of balance'}
            </span>
          )}
        </div>

        {isFetching && !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : data && (
          <div className="overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <th className="w-8 px-2 py-2" />
                  <th className="px-3 py-2 w-24">Code</th>
                  <th className="px-3 py-2">Account</th>
                  <th className="px-3 py-2 w-28 text-right">Opening</th>
                  <th className="px-3 py-2 w-28 text-right">Period Dr</th>
                  <th className="px-3 py-2 w-28 text-right">Period Cr</th>
                  <th className="px-3 py-2 w-28 text-right">Closing</th>
                  <th className="px-3 py-2 w-24" />
                </tr>
              </thead>
              <tbody>
                {data.rows.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No posted activity up to {data.period}.</td></tr>
                )}
                {data.rows.map((r, i) => (
                  <Fragment key={r.account_code}>
                    <tr className={cn('border-t border-neutral-100 hover:bg-primary-50/40', i % 2 && 'bg-neutral-50/40')}>
                      <td className="px-2 py-2">
                        <button onClick={() => toggle(r.account_code)}
                                className="rounded p-0.5 text-neutral-400 hover:text-neutral-700"
                                title="Expand by cost center">
                          {expanded.has(r.account_code) ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{r.account_code}</td>
                      <td className="px-3 py-2">{r.account_name}</td>
                      <td className="px-3 py-2 text-right font-mono text-neutral-500">{money(r.opening)}</td>
                      <td className="px-3 py-2 text-right font-mono">{money(r.period_debit)}</td>
                      <td className="px-3 py-2 text-right font-mono">{money(r.period_credit)}</td>
                      <td className="px-3 py-2 text-right font-mono font-semibold">{money(r.closing)}</td>
                      <td className="px-3 py-2 text-right">
                        <button className={linkBtn}
                                onClick={() => setDrill({ accountCode: r.account_code, title: `Vouchers — ${r.account_code} ${r.account_name} · ${period}` })}>
                          Vouchers
                        </button>
                      </td>
                    </tr>
                    {expanded.has(r.account_code) && (
                      <CostCenterExpansion accountCode={r.account_code} accountName={r.account_name}
                                           period={period} onDrill={setDrill} />
                    )}
                  </Fragment>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-neutral-200 bg-neutral-50 font-semibold">
                  <td className="px-3 py-2" colSpan={4}>Totals</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.totals.period_debit)}</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.totals.period_credit)}</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.totals.closing)}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>

      {drill && (
        <AccountVouchersModal accountCode={drill.accountCode} period={period}
                              costCenterId={drill.costCenterId} title={drill.title}
                              onClose={() => setDrill(null)}
                              onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={perms?.can_act ?? false} onClose={() => setJvId(null)} />
      )}
    </PortalChromeLayout>
  )
}

function CostCenterExpansion({ accountCode, accountName, period, onDrill }: {
  accountCode: string; accountName: string; period: string; onDrill: (d: Drill) => void
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['ab-expand', accountCode, period],
    queryFn: () => financeApi.get<ExpandResp>(`/gl/account-balance/${accountCode}/expand?period=${period}`),
  })
  if (isLoading) {
    return (
      <tr className="border-t border-neutral-100 bg-neutral-50/60">
        <td colSpan={8} className="px-3 py-3 text-center"><Loader2 className="mx-auto h-4 w-4 animate-spin text-neutral-400" /></td>
      </tr>
    )
  }
  const rows = data?.rows ?? []
  if (rows.length === 0) {
    return (
      <tr className="border-t border-neutral-100 bg-neutral-50/60">
        <td colSpan={8} className="px-3 py-2 pl-12 text-xs text-neutral-400">No movement this period.</td>
      </tr>
    )
  }
  return (
    <>
      {rows.map((cc) => (
        <tr key={cc.cost_center_id ?? 'none'} className="border-t border-neutral-100 bg-neutral-50/60 text-xs">
          <td />
          <td colSpan={2} className="px-3 py-1.5 pl-8 text-neutral-600">
            {cc.cost_center_code ? `${cc.cost_center_code} · ${cc.cost_center_name ?? ''}` : '(no cost center)'}
          </td>
          <td colSpan={4} className="px-3 py-1.5 text-right font-mono">{money(cc.amount)}</td>
          <td className="px-3 py-1.5 text-right">
            <button className={linkBtn}
                    onClick={() => onDrill({
                      accountCode, costCenterId: cc.cost_center_id,
                      title: `Vouchers — ${accountCode} ${accountName} · ${cc.cost_center_code ?? 'no cost center'} · ${period}`,
                    })}>
              Vouchers
            </button>
          </td>
        </tr>
      ))}
    </>
  )
}
```

- [ ] **Step 2: 注册路由 + 侧边栏**

`routes.tsx` import 加:

```tsx
import AccountBalancePage from '@/pages/finance/AccountBalancePage'
```

`financeRoutes` 在 Journal Vouchers 行后插入:

```tsx
  { path: '/finance/account-balance', element: <AccountBalancePage />, tab: { title: 'Account Balance', icon: 'Scale', keyStrategy: 'static' } },
```

`AppLayout.tsx` lucide import 加 `Scale`；NAV Finance 区 Journal Vouchers 行后插入:

```tsx
      { label: 'Account Balance', href: '/finance/account-balance', icon: Scale, permission: 'view_finance' },
```

- [ ] **Step 3: Typecheck**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 0 errors

- [ ] **Step 4: 浏览器冒烟**

`http://localhost:5177/finance/account-balance`，期间选 `2024-06`: 应出全科目余额行(NC 历史)；展开 `5101` 应见 MOH-* 成本中心行；点 Vouchers 出钻取列表；点凭证号打开 JV 详情。

- [ ] **Step 5: Commit**

```bash
cd c:/Project/uniops
git add finance/src/pages/finance/AccountBalancePage.tsx finance/src/app/routes.tsx finance/src/components/layout/AppLayout.tsx
git commit -m "feat(finance-ui): Account Balance report page (expand by cost center + voucher drill)"
```

---

### Task 6: 前端 — Budget Actual 页

**Files:**
- Create: `finance/src/pages/finance/BudgetActualPage.tsx`
- Modify: `finance/src/app/routes.tsx`
- Modify: `finance/src/components/layout/AppLayout.tsx`

**Interfaces:**
- Consumes: `GET /gl/budget-actual?period`(rows 按 `category` MOH/RD/SELL/GA 分组，`actual`=期间借方)、Task 3 两个 Modal、`/journal-vouchers/permissions`。
- Produces: 路由 `/finance/budget-actual`。

- [ ] **Step 1: 写 `BudgetActualPage.tsx`**(完整文件)

```tsx
/**
 * Budget Actual (能力④) — actuals of the 4 expense accounts per cost center,
 * grouped by category (5101→MOH · 5301→RD · 6601→SELL · 6602→GA). Actual =
 * period DEBIT movement of posted JV lines (expenses net to ~0 via 结转).
 * Each row drills into its composing vouchers.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccountVouchersModal } from './AccountVouchersModal'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const linkBtn = 'text-xs font-medium text-[#085E5E] hover:underline'

const CATEGORIES: { key: string; label: string; account: string }[] = [
  { key: 'MOH', label: 'Manufacturing Overhead (5101)', account: '5101' },
  { key: 'RD', label: 'R&D Expenses (5301)', account: '5301' },
  { key: 'SELL', label: 'Selling Expenses (6601)', account: '6601' },
  { key: 'GA', label: 'G&A Expenses (6602)', account: '6602' },
]

interface BaRow {
  account_code: string; category: string
  cost_center_id: string | null; cost_center_code: string | null; cost_center_name: string | null
  actual: string
}
interface BaResp { period: string; rows: BaRow[] }

function money(v: string | number) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

interface Drill { accountCode: string; costCenterId?: string | null; title: string }

export default function BudgetActualPage() {
  const { user } = useAuthStore()
  const [period, setPeriod] = useState(thisMonth())
  const [drill, setDrill] = useState<Drill | null>(null)
  const [jvId, setJvId] = useState<string | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })

  const { data, isFetching } = useQuery({
    queryKey: ['budget-actual', period],
    queryFn: () => financeApi.get<BaResp>(`/gl/budget-actual?period=${period}`),
  })

  const grouped = useMemo(() => {
    const g: Record<string, BaRow[]> = {}
    for (const r of data?.rows ?? []) (g[r.category] ??= []).push(r)
    for (const rows of Object.values(g)) rows.sort((a, b) => Number(b.actual) - Number(a.actual))
    return g
  }, [data])

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/budget-actual"
      title="Budget Actual"
      subtitle="GL-side actuals per cost center — period debit of the four expense accounts (posted JV, CAD)"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4">
          <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)}
                 className={cn(inputCls, 'w-40')} />
        </div>

        {isFetching && !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {CATEGORIES.map((cat) => {
              const rows = grouped[cat.key] ?? []
              const total = rows.reduce((s, r) => s + Number(r.actual), 0)
              return (
                <div key={cat.key} className="overflow-hidden rounded-lg border border-neutral-200">
                  <div className="flex items-center justify-between bg-neutral-50 px-3 py-2">
                    <span className="text-sm font-semibold text-neutral-700">{cat.label}</span>
                    <span className="font-mono text-sm font-semibold">{money(total)}</span>
                  </div>
                  <table className="w-full text-sm">
                    <tbody>
                      {rows.length === 0 && (
                        <tr><td className="px-3 py-4 text-center text-xs text-neutral-400">No actuals this period.</td></tr>
                      )}
                      {rows.map((r, i) => (
                        <tr key={r.cost_center_id ?? `none-${i}`}
                            className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                          <td className="px-3 py-2">
                            {r.cost_center_code
                              ? <><span className="font-mono text-xs">{r.cost_center_code}</span><span className="ml-1 text-neutral-600">{r.cost_center_name}</span></>
                              : <span className="text-neutral-400">(no cost center)</span>}
                          </td>
                          <td className="px-3 py-2 w-32 text-right font-mono">{money(r.actual)}</td>
                          <td className="px-3 py-2 w-24 text-right">
                            <button className={linkBtn}
                                    onClick={() => setDrill({
                                      accountCode: r.account_code, costCenterId: r.cost_center_id,
                                      title: `Vouchers — ${r.account_code} · ${r.cost_center_code ?? 'no cost center'} · ${period}`,
                                    })}>
                              Vouchers
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {drill && (
        <AccountVouchersModal accountCode={drill.accountCode} period={period}
                              costCenterId={drill.costCenterId} title={drill.title}
                              onClose={() => setDrill(null)} onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={perms?.can_act ?? false} onClose={() => setJvId(null)} />
      )}
    </PortalChromeLayout>
  )
}
```

- [ ] **Step 2: 注册路由 + 侧边栏**

`routes.tsx` import 加:

```tsx
import BudgetActualPage from '@/pages/finance/BudgetActualPage'
```

`financeRoutes` 在 Account Balance 行后插入:

```tsx
  { path: '/finance/budget-actual', element: <BudgetActualPage />, tab: { title: 'Budget Actual', icon: 'Target', keyStrategy: 'static' } },
```

`AppLayout.tsx` lucide import 加 `Target`；NAV Finance 区 Account Balance 行后插入:

```tsx
      { label: 'Budget Actual', href: '/finance/budget-actual', icon: Target, permission: 'view_finance' },
```

- [ ] **Step 3: Typecheck**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 0 errors

- [ ] **Step 4: 浏览器冒烟**

`http://localhost:5177/finance/budget-actual`，期间 `2024-06`: 四个分类卡应有真实数字(参考值: MOH 卡里 MOH-0106-E01≈967,930、MOH-0104≈687,134、MOH-0105-LAB≈167,467)；点 Vouchers 钻取到凭证。

- [ ] **Step 5: Commit**

```bash
cd c:/Project/uniops
git add finance/src/pages/finance/BudgetActualPage.tsx finance/src/app/routes.tsx finance/src/components/layout/AppLayout.tsx
git commit -m "feat(finance-ui): Budget Actual page (4 expense categories per cost center + voucher drill)"
```

---

### Task 7: 收尾回归 + 验收清单

**Files:** 无新文件(纯验证)。

- [ ] **Step 1: 后端全量 JV/GL 相关回归(单进程串行)**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_journal_voucher.py tests/test_jv_lifecycle.py tests/test_jv_api.py tests/test_jv_backfill.py tests/test_account_balance.py tests/test_gl.py tests/test_coa.py -q`
Expected: 全部 PASS，0 failed(对照基线: 改动前这些文件应先跑一次记录通过数)

- [ ] **Step 2: 前端 typecheck 终验**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0

- [ ] **Step 3: 确认容器已用新代码**

```bash
docker restart uniops_finance_api
docker ps --filter name=uniops_finance --format "{{.Names}} {{.Status}}"
```

- [ ] **Step 4: 用户验收走查清单**(浏览器 :5177，报告给用户)

1. Journal Vouchers: 期间 2024-06 列表出 NC 历史凭证(记-2024-xx，posted)；搜索凭证号命中；分页翻页。
2. 触发一笔业务事件(或用既有 draft)→ 列表 status=draft 出现 → 勾选 → Review Selected → Post Selected → 状态推进；自审自批时应报 SoD 错(banner 显示 failed 原因)。
3. 详情: 借贷平衡、维度 chips(CC/Dept/收支项目 CRM 码)、三岗留痕、NC 来源标注；posted 可 Unpost/Reverse(红冲生成对冲凭证)。
4. Account Balance: 2024-06 全科目、Balanced 徽章、5101 展开成本中心、钻取凭证、凭证号→详情。
5. Budget Actual: 2024-06 四分类数字对上参考值，钻取正常。
6. 非 finance 角色(如 requester): 动作按钮/复选框隐藏，页面只读可见(view_finance 有权限时)。

- [ ] **Step 5: 无需 commit**(若走查发现小修，按所属 Task 的文件 surgical add 单独 commit)

---

## Self-Review 记录

- **Spec 覆盖**: JV spec §7 前端(列表+详情+批量工具条+红冲+来源单据展示) → Task 3/4；余额表 spec §9 两页 → Task 5/6；§8 API 契约无改动(仅加法)。**明确不做**(留后续，与 spec §11/现状快照一致): ② 通用多维展开(现后端只支持 cost center，UI 相应只做 CC 展开)、科目级次树形汇总、`gl.py` 正式切 posted-JV(Plan 4 落地后单独做)、来源单据可点击跳转(Document Chain 子项目③，本次只展示文本)。
- **Placeholder 扫描**: 所有代码块完整可粘贴；无 TBD/"similar to"。
- **类型一致性**: `{total, items}`(T1→T4)、`can_act`(T2→T3/4/5/6)、`JvDetailModal`/`AccountVouchersModal` props(T3→T4/5/6)、`prepared_by_name`/`dims`(T2→T3) 已互查一致。`JvHeader` 从 `JvDetailModal.tsx` 导出供列表页复用。
