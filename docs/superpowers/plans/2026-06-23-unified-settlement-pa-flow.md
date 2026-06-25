# 统一 Settlement PA 流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Prepayment PA 的「Settle」按钮统一为打开完整的 Create New PA(Settlement 类型)页面,默认信息从来源预付带入;Settlement 类型的「Original Prepay PA」改为按所选 PO 自动加载的下拉(修 UUID bug);Settlement PA 审批通过后自动把来源预付标记为 settled。

**Architecture:** 后端给 `payment_applications` 加 `prepayment_applied` 抵扣列,`payment_amount = subtotal+tax+shipping+other − prepayment_applied`(= 净付余款,finance 执行器直接用);Settlement PA 转 approved 时在已有钩子里回填来源预付的 `settlement_status='settled'`。前端 Settle 按钮改跳 `/pa/new?settleFrom=<id>`,PaCreatePage 锁定/预填 PO 与来源预付,下拉选择存 UUID,charge breakdown 增加抵扣行与 Net Payable。退役 in-place `/settle` 端点与 SettlementTaskPage。

**Tech Stack:** FastAPI + SQLAlchemy(async)+ Alembic + Pydantic(epms-api);React + TS 6 + React Query + react-router(epms 前端)。

> **本轮项目规则:UniOps 当前不 commit / 不 push / 不建分支(见 memory feedback_uniops_no_commit_until_batch)。** 因此下面每个 Task 的最后一步是「验证 + 留在工作区」,不要执行 `git commit`。所有改动累积在工作区,等用户统一提交。

> **测试 DB 约定:** epms-api 的 pytest 连不上生产库,需把 `POSTGRES_*` 覆盖到本地 docker `uniops_postgres`(密码 `docker exec` 取),见 memory feedback_uniops_admin_test_db_env。

---

## File Structure

**后端(epms-api)**
- `app/models/pa.py` — 加 `prepayment_applied` 列
- `alembic/versions/z1_add_prepayment_applied_to_pa.py` — 新迁移(down_revision = `y1_invoice_po_allocations`,先用 `alembic heads` 确认)
- `app/schemas/pa.py` — `PaCreate`/`PaUpdate`/`PaResponse` 加 `prepayment_applied`
- `app/crud/pa.py` — `_compute_payment` 减抵扣;`create`/`update` 写入;新增 `mark_prepayment_settled` 内部 helper;`settle()` 退役为内部用途
- `app/api/v1/pa.py` — `create_pa` 加 Net Payable ≥ 0 校验;`pa_action` approved 钩子加自动回填;删除 `POST /pa/:id/settle`
- `tests/test_pa.py` — settlement 创建/净额/自动回填/overpaid 测试

**前端(epms)**
- `src/services/pa.ts` — `ApiPa`/`CreatePaBody`/`UpdatePaBody` 加 `prepayment_applied`;删除 `settle` 与 `SettlePaBody`
- `src/hooks/usePas.ts` — 删除 `useSettlePa`
- `src/pages/pa/PaDetailPage.tsx` — Settle 按钮改 `navigate('/pa/new?settleFrom=...')`
- `src/pages/pa/PaCreatePage.tsx` — 下拉 + UUID + prepayment_applied + Net Payable + settleFrom 预填 + PO 锁定
- `src/pages/pa/SettlementTaskPage.tsx` — 删除
- `src/App.tsx` — 删除 `/pa/:id/settle` 路由与 import

---

## Task 1: 后端 — 加 `prepayment_applied` 列与迁移

**Files:**
- Modify: `app/models/pa.py`(`prepayment_pa_id` 块之后,`subtotal` 之前)
- Create: `alembic/versions/z1_add_prepayment_applied_to_pa.py`

- [ ] **Step 1: 确认当前 alembic head**

Run: `cd epms-api && alembic heads`
Expected: 输出包含 `y1_invoice_po_allocations (head)`。若不同,以实际 head 作为下方 `down_revision`。

- [ ] **Step 2: 模型加列**

在 `app/models/pa.py` 的 `prepayment_pa_id` 定义([models/pa.py:42-45](../../../epms-api/app/models/pa.py#L42))之后加:

```python
    # 思路 A：Settlement PA 抵扣的预付金额。payment_amount 已是净付余款
    # (= subtotal+tax+shipping+other − prepayment_applied)。仅 settlement/balance 用。
    prepayment_applied: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
```

- [ ] **Step 3: 写迁移**

Create `alembic/versions/z1_add_prepayment_applied_to_pa.py`:

```python
"""add prepayment_applied to payment_applications

Revision ID: z1_add_prepayment_applied
Revises: y1_invoice_po_allocations
"""
import sqlalchemy as sa
from alembic import op

revision = "z1_add_prepayment_applied"
down_revision = "y1_invoice_po_allocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_applications",
        sa.Column("prepayment_applied", sa.Numeric(15, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_applications", "prepayment_applied")
```

- [ ] **Step 4: 应用迁移到本地测试库并验证列存在**

Run: `cd epms-api && alembic upgrade head`
Expected: 无报错;`payment_applications` 出现 `prepayment_applied` 列。
（验证:`psql ... -c "\d payment_applications" | grep prepayment_applied` 或等价查询。）

- [ ] **Step 5: 验证并留在工作区(不 commit)**

Run: `cd epms-api && alembic current`
Expected: 指向 `z1_add_prepayment_applied`。

---

## Task 2: 后端 — Schema 增加 `prepayment_applied`

**Files:**
- Modify: `app/schemas/pa.py`(`PaCreate`、`PaUpdate`、`PaResponse`)

- [ ] **Step 1: PaCreate 加字段**

在 `PaCreate`([schemas/pa.py:45-63](../../../epms-api/app/schemas/pa.py#L45))的 `prepayment_pa_id` 行之后加:

```python
    prepayment_applied: Decimal | None = Field(default=None, ge=0)  # settlement/balance 抵扣的预付额
```

- [ ] **Step 2: PaUpdate 加字段**

在 `PaUpdate`([schemas/pa.py:66](../../../epms-api/app/schemas/pa.py#L66))中加:

```python
    prepayment_applied: Decimal | None = Field(default=None, ge=0)
```

- [ ] **Step 3: PaResponse 加字段**

在 `PaResponse`([schemas/pa.py:94](../../../epms-api/app/schemas/pa.py#L94))中,`prepayment_pa_id` 行之后加:

```python
    prepayment_applied: Decimal | None
```

- [ ] **Step 4: 验证 import 可加载**

Run: `cd epms-api && python -c "from app.schemas.pa import PaCreate, PaUpdate, PaResponse; print('ok')"`
Expected: 打印 `ok`。

---

## Task 3: 后端 — crud 净额计算、写入、自动回填 helper

**Files:**
- Modify: `app/crud/pa.py`(`_compute_payment`、`create`、`update`、`settle`)

- [ ] **Step 1: 写失败测试 — 净额计算**

在 `tests/test_pa.py` 末尾加(命名沿用文件现有风格;若已有 fixtures 复用之):

```python
import pytest


@pytest.mark.asyncio
async def test_settlement_payment_amount_is_net_of_prepayment(client, settlement_pa_payload):
    # settlement_pa_payload: subtotal=1000, tax=130, shipping=0, other=0,
    # prepayment_applied=500  →  净额 630
    resp = await client.post("/api/v1/pa", json=settlement_pa_payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert float(body["payment_amount"]) == pytest.approx(630.0)
    assert float(body["prepayment_applied"]) == pytest.approx(500.0)
```

> 注:`settlement_pa_payload` fixture 需构造一个合法的 prepayment PA(同 PO)并把其 id 放进 `prepayment_pa_id`。若文件已有创建 PO/prepayment 的辅助,复用;否则在 fixture 里先 POST 一个 prepayment PA 取其返回 id。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd epms-api && POSTGRES_DB=uniops POSTGRES_HOST=localhost ... pytest tests/test_pa.py::test_settlement_payment_amount_is_net_of_prepayment -v`
Expected: FAIL(净额 = 1130 而非 630,因 `_compute_payment` 尚未减抵扣)。

- [ ] **Step 3: 改 `_compute_payment`**

将 [crud/pa.py:53-59](../../../epms-api/app/crud/pa.py#L53) 改为:

```python
def _compute_payment(payload) -> Decimal:
    applied = getattr(payload, "prepayment_applied", None) or Decimal("0")
    net = (
        payload.subtotal
        + payload.tax_amount
        + payload.shipping_amount
        + payload.other_charges
        - applied
    )
    return net if net > Decimal("0") else Decimal("0")
```

- [ ] **Step 4: create() 写入 prepayment_applied**

在 `create()` 的 `PaymentApplication(...)` 构造中([crud/pa.py:132](../../../epms-api/app/crud/pa.py#L132) 附近,`prepayment_pa_id=` 行后)加:

```python
        prepayment_applied=getattr(payload, "prepayment_applied", None),
```

- [ ] **Step 5: update() 重算净额时纳入抵扣**

在 `update()` 中,把 `prepayment_applied` 加入可更新字段并修正净额重算。把 [crud/pa.py:183-188](../../../epms-api/app/crud/pa.py#L183) 段改为:

```python
    # Recompute payment amount if financials changed
    for field in ("subtotal", "tax_amount", "shipping_amount", "other_charges", "prepayment_applied"):
        val = getattr(payload, field)
        if val is not None:
            setattr(pa, field, val)

    applied = pa.prepayment_applied or Decimal("0")
    net = pa.subtotal + pa.tax_amount + pa.shipping_amount + pa.other_charges - applied
    pa.payment_amount = net if net > Decimal("0") else Decimal("0")
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd epms-api && ... pytest tests/test_pa.py::test_settlement_payment_amount_is_net_of_prepayment -v`
Expected: PASS。

- [ ] **Step 7: 加自动回填 helper,settle() 退役为内部用途**

把 [crud/pa.py:225-245](../../../epms-api/app/crud/pa.py#L225) 的 `settle()` 替换为内部 helper(供 approved 钩子调用,不再由 HTTP 直连):

```python
async def mark_prepayment_settled(
    db: AsyncSession,
    settlement_pa: PaymentApplication,
    actor_id: uuid.UUID,
) -> None:
    """A settlement PA reached 'approved' — reconcile its source prepayment PA.

    Variance = the net balance paid by the settlement PA (final − prepaid).
    Idempotent: skips if the source prepayment is missing or already settled.
    """
    if settlement_pa.prepayment_pa_id is None:
        return
    orig = await get_by_id(db, settlement_pa.prepayment_pa_id)
    if orig is None or orig.pa_type != "prepayment":
        return
    if orig.settlement_status == "settled":
        return
    settler = await db.execute(select(User.full_name).where(User.id == actor_id))
    orig.settlement_status = "settled"
    orig.settled_at = datetime.now(timezone.utc)
    orig.settled_by = actor_id
    orig.settled_by_name = settler.scalar_one_or_none()
    orig.settlement_variance = settlement_pa.payment_amount
    orig.settlement_note = f"Settled via {settlement_pa.pa_number}"
    await db.flush()
```

并删除原 `settle()` 函数体(及 `PaSettleRequest` 的 import,若 crud 顶部 import 了它,见 [crud/pa.py:17](../../../epms-api/app/crud/pa.py#L17),从 import 列表移除 `PaSettleRequest`)。

- [ ] **Step 8: 验证模块加载**

Run: `cd epms-api && python -c "from app.crud.pa import mark_prepayment_settled, _compute_payment; print('ok')"`
Expected: 打印 `ok`(确认无残留对已删 `settle`/`PaSettleRequest` 的引用)。

---

## Task 4: 后端 — 创建校验 + approved 自动回填钩子 + 退役 /settle

**Files:**
- Modify: `app/api/v1/pa.py`

- [ ] **Step 1: 写失败测试 — 审批后预付自动 settled + overpaid 拒绝**

在 `tests/test_pa.py` 加:

```python
@pytest.mark.asyncio
async def test_settlement_approved_marks_prepayment_settled(client, approve_pa_to_approved, prepayment_and_settlement):
    prepay_id, settlement_id = prepayment_and_settlement
    await approve_pa_to_approved(settlement_id)  # 推进 settlement PA 至 approved
    resp = await client.get(f"/api/v1/pa/{prepay_id}")
    assert resp.status_code == 200
    assert resp.json()["settlement_status"] == "settled"


@pytest.mark.asyncio
async def test_settlement_net_negative_rejected(client, overpaid_settlement_payload):
    # prepayment_applied 大于 subtotal+tax+ship+other → 净额为负
    resp = await client.post("/api/v1/pa", json=overpaid_settlement_payload)
    assert resp.status_code == 422
    assert "net" in resp.json()["detail"].lower() or "credit" in resp.json()["detail"].lower()
```

> fixtures 说明:`prepayment_and_settlement` 建一个 prepayment PA 与一个引用它的 settlement PA,返回二者 id;`approve_pa_to_approved` 用现有审批 action 把 PA 推到 `approved`(沿用文件里已有的审批推进辅助,如无则按现有 action 端点逐步 approve)。`overpaid_settlement_payload`:`subtotal=100, tax=0, prepayment_applied=200`。

- [ ] **Step 2: 运行确认失败**

Run: `cd epms-api && ... pytest tests/test_pa.py::test_settlement_approved_marks_prepayment_settled tests/test_pa.py::test_settlement_net_negative_rejected -v`
Expected: 两条 FAIL。

- [ ] **Step 3: create_pa 加 Net Payable ≥ 0 校验**

在 `create_pa` 的 `elif body.pa_type in ("settlement", "balance"):` 分支([api/v1/pa.py:135-153](../../../epms-api/app/api/v1/pa.py#L135))末尾(`orig is None` 校验之后)加:

```python
        # Net payable = full charge − prepayment applied. Must be ≥ 0:
        # an overpaid prepayment is a credit-note situation, not a negative payment.
        from decimal import Decimal as _D
        applied = body.prepayment_applied or _D("0")
        net = (body.subtotal + body.tax_amount + body.shipping_amount
               + body.other_charges - applied)
        if net < _D("0"):
            raise HTTPException(
                status_code=422,
                detail="Net payable is negative — prepayment exceeds the final amount. "
                       "A credit note is required; settle with prepayment_applied ≤ total.",
            )
```

> `body.shipping_amount` / `body.other_charges` 在 `PaCreate` 有默认 `Decimal("0")`,可直接相加。

- [ ] **Step 4: pa_action approved 钩子加自动回填**

在 `pa_action` 的 `if result.get("new_status") == "approved":` 块([api/v1/pa.py:267](../../../epms-api/app/api/v1/pa.py#L267))**开头**(生成 PDF 之前)加:

```python
        # 统一 settlement：settlement PA 一旦 approved，回填来源预付为 settled
        if pa.pa_type == "settlement":
            await pa_crud.mark_prepayment_settled(
                db, pa, actor_id=uuid.UUID(user["sub"])
            )
```

- [ ] **Step 5: 删除 in-place /settle 端点**

删除 `settle_pa` 路由函数([api/v1/pa.py:304-317](../../../epms-api/app/api/v1/pa.py#L304)),并从本文件 import 中移除 `PaSettleRequest`([api/v1/pa.py:22](../../../epms-api/app/api/v1/pa.py#L22))。

- [ ] **Step 6: 运行新测试 + 全量 PA 测试**

Run: `cd epms-api && ... pytest tests/test_pa.py -v`
Expected: 新增用例 PASS;其余原有用例不回归(若有用例直接打 `/settle`,改为走 settlement PA + approve 流程或删除)。

- [ ] **Step 7: 验证应用可启动(import 完整性)**

Run: `cd epms-api && python -c "from app.main import app; print('ok')"`
Expected: 打印 `ok`(确认无残留 `PaSettleRequest`/`settle` 引用)。

---

## Task 5: 前端 — service/hooks 类型与清理

**Files:**
- Modify: `src/services/pa.ts`、`src/hooks/usePas.ts`

- [ ] **Step 1: ApiPa 加字段**

在 `ApiPa`([services/pa.ts:35](../../../epms/src/services/pa.ts#L35))的 `prepayment_pa_id` 行后加:

```typescript
  prepayment_applied?: number | null
```

- [ ] **Step 2: CreatePaBody / UpdatePaBody 加字段**

在 `CreatePaBody`([services/pa.ts:79](../../../epms/src/services/pa.ts#L79))`prepayment_pa_id?` 行后、`UpdatePaBody` 内各加:

```typescript
  prepayment_applied?: number
```

- [ ] **Step 3: 删除 settle service 与 SettlePaBody**

删除 `paService.settle`([services/pa.ts:153-154](../../../epms/src/services/pa.ts#L153))与 `SettlePaBody` 接口([services/pa.ts:110-114](../../../epms/src/services/pa.ts#L110))。

- [ ] **Step 4: 删除 useSettlePa**

删除 `useSettlePa`([hooks/usePas.ts:77-90](../../../epms/src/hooks/usePas.ts#L77))及顶部 import 里的 `type SettlePaBody`([hooks/usePas.ts:8](../../../epms/src/hooks/usePas.ts#L8))。

- [ ] **Step 5: typecheck**

Run: `cd epms && tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 仅剩 PaDetailPage / SettlementTaskPage 对 `useSettlePa`/`settle` 的引用报错(将在 Task 6/7 解决);本文件本身无错。

---

## Task 6: 前端 — Settle 按钮改跳转,删除 SettlementTaskPage

**Files:**
- Modify: `src/pages/pa/PaDetailPage.tsx`、`src/App.tsx`
- Delete: `src/pages/pa/SettlementTaskPage.tsx`

- [ ] **Step 1: PaDetailPage 两处 Settle 改跳转**

把 [PaDetailPage.tsx:295](../../../epms/src/pages/pa/PaDetailPage.tsx#L295) 与 [PaDetailPage.tsx:403](../../../epms/src/pages/pa/PaDetailPage.tsx#L403) 的
`onClick={() => navigate(`/pa/${pa.id}/settle`)}` 改为:

```tsx
onClick={() => navigate(`/pa/new?settleFrom=${pa.id}`)}
```

- [ ] **Step 2: 删除 SettlementTaskPage 文件**

Delete `src/pages/pa/SettlementTaskPage.tsx`。

- [ ] **Step 3: App.tsx 删路由与 import**

删除 [App.tsx:36](../../../epms/src/App.tsx#L36) 的 `import SettlementTaskPage ...` 与 [App.tsx:133](../../../epms/src/App.tsx#L133) 的 `<Route path="/pa/:id/settle" ... />`。

- [ ] **Step 4: typecheck**

Run: `cd epms && tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 无错(SettlementTaskPage 相关报错消失)。

---

## Task 7: 前端 — PaCreatePage 统一页(下拉 / UUID / 抵扣 / Net Payable / settleFrom 预填 / PO 锁定)

**Files:**
- Modify: `src/pages/pa/PaCreatePage.tsx`

- [ ] **Step 1: 引入 settleFrom 与来源预付加载**

在组件顶部,`searchParams` 已存在([PaCreatePage.tsx:18](../../../epms/src/pages/pa/PaCreatePage.tsx#L18))。加:

```tsx
  const settleFromId = searchParams.get('settleFrom') ?? ''
  const { data: sourcePrepay } = usePa(settleFromId)  // 从 '@/hooks/usePas' 引入 usePa
```

并在文件顶部 import 中把 `usePas` 一行补上 `usePa`(已 import `usePas`,改为 `import { useCreatePa, usePas, usePa } from '@/hooks/usePas'`)。

- [ ] **Step 2: 加 prepayment_applied 状态**

在 charge 状态区([PaCreatePage.tsx:40-48](../../../epms/src/pages/pa/PaCreatePage.tsx#L40))加:

```tsx
  const [prepaymentApplied, setPrepaymentApplied] = useState('')
```

- [ ] **Step 3: 计算 Net Payable**

把 `paymentTotal` 计算([PaCreatePage.tsx:96-100](../../../epms/src/pages/pa/PaCreatePage.tsx#L100))下方加:

```tsx
  const appliedNum   = parseFloat(prepaymentApplied) || 0
  const grossTotal   = subtotalNum + taxNum + shippingNum + otherNum
  const isSettlementType = paType === 'settlement' || paType === 'balance'
  const netPayable   = isSettlementType ? Math.max(grossTotal - appliedNum, 0) : grossTotal
  const isOverpaid   = isSettlementType && grossTotal - appliedNum < -0.01
```

> 把后续 UI 中显示「Total Payment Amount」的 `paymentTotal` 用 `netPayable` 替换(见 Step 8/9),`grossTotal` 用于展示抵扣前金额。

- [ ] **Step 4: 派生 PO 下的可关联预付列表**

`poActivePas` 已有([PaCreatePage.tsx:72](../../../epms/src/pages/pa/PaCreatePage.tsx#L72))。加:

```tsx
  const linkablePrepayments = (poActivePas?.items ?? []).filter(
    (p) => p.pa_type === 'prepayment' && !['cancelled', 'rejected'].includes(p.status)
  )
```

- [ ] **Step 5: settleFrom 预填 + PO 锁定**

加一个 effect(放在 PO reset effect 之后):

```tsx
  // 从 Prepayment 详情页「Settle」进入：锁定并预填来源预付的上下文
  const lockedFromSettle = Boolean(settleFromId)
  useEffect(() => {
    if (!sourcePrepay) return
    setSelectedPoId(sourcePrepay.po_id)
    setPaType('settlement')
    setPrepaymentPaId(sourcePrepay.id)
    setPrepaymentApplied(String(sourcePrepay.payment_amount ?? ''))
  }, [sourcePrepay?.id]) // eslint-disable-line react-hooks/exhaustive-deps
```

并在 PO reset effect([PaCreatePage.tsx:109-125](../../../epms/src/pages/pa/PaCreatePage.tsx#L109))里,把 `setPrepaymentPaId('')` / `setPrepaymentApplied('')` 的清空**跳过 settleFrom 场景**:在该 effect 开头加 `if (lockedFromSettle) return` 之外的字段照常;为简单起见,仅在该 effect 内**不要**清空 `prepaymentPaId`/`prepaymentApplied`(这两者由 Step 5 的 effect 控制)。同时新增清空逻辑:当用户在非锁定下手动切换 PO 时清空 `prepaymentPaId`、`prepaymentApplied`(见 Step 6 的 onChange)。

- [ ] **Step 6: 用下拉替换自由文本「Original Prepayment PA Number」**

把 [PaCreatePage.tsx:538-550](../../../epms/src/pages/pa/PaCreatePage.tsx#L538) 的自由文本 input 块替换为:

```tsx
                    <div className="flex flex-col gap-1">
                      <label className="text-xs font-medium text-neutral-700">
                        Original Prepayment PA <span className="text-danger-600">*</span>
                      </label>
                      <select
                        value={prepaymentPaId}
                        onChange={(e) => setPrepaymentPaId(e.target.value)}
                        disabled={lockedFromSettle}
                        className="h-9 w-full rounded-lg border border-neutral-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-500"
                      >
                        <option value="">Select a prepayment PA…</option>
                        {linkablePrepayments.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.pa_number} · {formatAmount(p.payment_amount, p.currency)}
                            {p.settlement_status ? ` · ${p.settlement_status}` : ''}
                          </option>
                        ))}
                      </select>
                      {linkablePrepayments.length === 0 && (
                        <p className="text-[11px] text-neutral-400">No prepayment PAs found for this PO.</p>
                      )}
                      <p className="text-[11px] text-neutral-400">
                        The prepayment this {paType} reconciles. Auto-loaded from the selected PO.
                      </p>
                    </div>
```

- [ ] **Step 7: settlement/balance 时显示「Prepayment Applied」抵扣输入**

在 charge breakdown 的 grid([PaCreatePage.tsx:701](../../../epms/src/pages/pa/PaCreatePage.tsx#L701) 的 `</div>` 关闭 grid 之后、Total 之前)加:

```tsx
                {isSettlementType && (
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-medium text-neutral-700">
                      Prepayment Applied (deducted)
                    </label>
                    <input
                      type="number" min={0} step={0.01}
                      value={prepaymentApplied}
                      onChange={(e) => setPrepaymentApplied(e.target.value)}
                      placeholder="0.00"
                      className="h-10 px-3 rounded-lg border border-neutral-300 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary-600"
                    />
                    <p className="text-[11px] text-neutral-400">
                      Amount already paid via the prepayment PA. Net payable = total − this.
                    </p>
                    {isOverpaid && (
                      <p className="text-xs text-warning-700">
                        Prepayment exceeds the final amount — net payable is 0; a credit note is expected.
                      </p>
                    )}
                  </div>
                )}
```

- [ ] **Step 8: Total 行改为 Net Payable**

把 Total 块([PaCreatePage.tsx:717-726](../../../epms/src/pages/pa/PaCreatePage.tsx#L717))改为显示 gross 与 net:

```tsx
                {/* Total */}
                <div className="flex flex-col gap-1 rounded-xl border-2 border-primary-200 bg-primary-50 px-5 py-3">
                  {isSettlementType && appliedNum > 0 && (
                    <>
                      <div className="flex items-center justify-between text-xs text-neutral-500">
                        <span>Gross total</span>
                        <span className="font-mono">{formatAmount(grossTotal, selectedPo.currency)}</span>
                      </div>
                      <div className="flex items-center justify-between text-xs text-neutral-500">
                        <span>Prepayment applied</span>
                        <span className="font-mono">−{formatAmount(appliedNum, selectedPo.currency)}</span>
                      </div>
                    </>
                  )}
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-neutral-700">
                      {isSettlementType ? 'Net Payable' : 'Total Payment Amount'}
                    </span>
                    <span className={cn('font-mono font-bold text-xl', netPayable > 0 ? 'text-primary-700' : 'text-neutral-300')}>
                      {netPayable > 0 ? formatAmount(netPayable, selectedPo.currency) : '—'}
                    </span>
                  </div>
                </div>
```

- [ ] **Step 9: sidebar 同步 + 提交 payload**

在 sidebar 的 charge breakdown([PaCreatePage.tsx:821-824](../../../epms/src/pages/pa/PaCreatePage.tsx#L821) 的 Total 行)把显示值由 `paymentTotal` 换成 `netPayable`,并在其上方(当 `isSettlementType && appliedNum > 0`)加一行「Prepayment applied −X」。

在 `handleSubmit` 的 `createPa.mutateAsync({...})`([PaCreatePage.tsx:236-257](../../../epms/src/pages/pa/PaCreatePage.tsx#L236))中:
- 把 `prepayment_pa_id: (paType === 'settlement' || paType === 'balance') ? prepaymentPaId || undefined : undefined` 保留(现在 `prepaymentPaId` 已是 UUID)。
- 新增 `prepayment_applied: isSettlementType ? (appliedNum || undefined) : undefined,`。

- [ ] **Step 10: 提交校验补充**

在 `errors` 校验([PaCreatePage.tsx:182-194](../../../epms/src/pages/pa/PaCreatePage.tsx#L182))与 `handleSubmit` 早退([PaCreatePage.tsx:222-224](../../../epms/src/pages/pa/PaCreatePage.tsx#L222))加:

```tsx
    if (isSettlementType && !prepaymentPaId) errors.push('Original Prepayment PA is required')
```

handleSubmit 内对应早退:

```tsx
    if (isSettlementType && !prepaymentPaId) return
```

- [ ] **Step 11: typecheck**

Run: `cd epms && tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 无错。

---

## Task 8: 端到端冒烟(手动)

- [ ] **Step 1: 起服务**

Run: 按项目方式起 epms-api 与 epms 前端(docker compose / dev)。

- [ ] **Step 2: Settle 入口冒烟**

打开一个 `processed` 且 `settlement_status=pending` 的 prepayment PA 详情页 → 点「Settle」→ 跳到 New PA:验证 PO 锁定且预填、PA 类型为 Settlement、Original Prepayment PA 下拉预选且禁用、Prepayment Applied 预填 = 预付额、Net Payable = 全额 − 预付。

- [ ] **Step 3: 直接创建路径冒烟**

New PA → 选一个有预付的 PO → 选 Settlement 类型:验证 Original Prepayment PA 下拉随 PO 自动加载;切换 PO 列表随之刷新。

- [ ] **Step 4: 审批回填冒烟**

把该 settlement PA 走审批到 `approved` → 回到来源 prepayment PA 详情:验证其 Settlement Status 显示 settled、variance = 净余款。

---

## Self-Review 记录

- **Spec 覆盖:** §4.1 模型/迁移→Task1;§4.1 schema→Task2;§4.1 净额+§4.2 helper→Task3;§4.2 校验+回填+§4.3 退役端点→Task4;§4.3/§4.5 前端 service 清理→Task5;§4.4 Settle 跳转+删页→Task6;§4.5/§4.6 统一页→Task7;§6 测试散落于 Task3/4 + 手动冒烟 Task8。
- **Overpaid 边界**(§5):后端 Task4 Step3 拒负数;前端 Task7 Step3/7 净额下限 0 + 警告。
- **类型一致:** 后端 `prepayment_applied: Decimal|None`;前端 `prepayment_applied?: number`;helper 名 `mark_prepayment_settled` 在 Task3 定义、Task4 调用一致。
- **占位符:** 无 TODO/TBD;测试 fixtures 处明确标注复用现有辅助的前提(test_pa.py 当前结构需在实现时对齐)。
