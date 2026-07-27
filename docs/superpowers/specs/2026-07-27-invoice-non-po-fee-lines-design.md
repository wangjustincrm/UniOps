# Invoice 行标记为「非PO费用」以放开 Match PO

- **日期**: 2026-07-27
- **分支**: `feature/invoice-non-po-fee-lines`(worktree `c:/Project/uniops-nonpo-fee`,off main `cad9d17`)
- **状态**: 设计已确认,待写实现计划

## 背景与问题

很多供应商发票的行项里包含 shipping / packaging 等杂费,这些费用在采购 PO 里通常**没有对应行**。当前 Match PO(UI 名 "Allocate to Purchase Orders")要求发票每一分钱都分配到 PO 行才能提交:

- 前端门禁 [`InvoiceAllocationPanel.tsx:54-55`](../../../epms/src/pages/invoices/InvoiceAllocationPanel.tsx#L54-L55):`balanced = |invoice.amount − 已分配| < 0.01`,提交按钮 `disabled={!balanced}`([:159](../../../epms/src/pages/invoices/InvoiceAllocationPanel.tsx#L159))。
- 后端门禁 [`crud/invoice.py:282-286`](../../../epms-api/app/crud/invoice.py#L282-L286):`分配合计` 必须等于 `invoice.amount`(容差 0.01),否则抛 `AllocationImbalance` → HTTP 422。

因此带 shipping 行的发票**永远平不了账**,Match PO 卡死走不下去。

## 目标

允许把发票的某些行标记为「非PO费用」(non-PO fee),这些行**不参与 PO 匹配**,但其金额计入"已交代金额"从而让平账门禁通过,放开 Match PO 流程。费用仍"照付"(计入发票总额、随发票走 AP)。

## 现状调研(关键事实)

- Match PO 全部在 **epms 前端 + epms-api 后端**,产出 `invoice_po_allocations` 行。
- `invoice.line_items` 是 **JSONB**,发票行不是独立表;发票头 `amount`/`tax_amount`/`total_amount` 是独立的用户录入值,与 line_items 不强制相等。
- **不存在**任何"行排除 / 特殊行类型"的概念,需净新增。
- **下游应付有两套且金额来源不同**(见"范围外"):
  - Finance AP 发票([`finance_sync.py:34-58`](../../../epms-api/app/services/finance_sync.py#L34-L58))按**发票头全额**同步 → 含所有行,账面正确。
  - PA 付款授权 + 出纳/记账([`PaCreatePage.tsx:98-100`](../../../epms/src/pages/pa/PaCreatePage.tsx#L98-L100))只从**选中的 PO 行**重算 → 不含非PO费用。
  - 付款执行 [`payment_execute.py:300-303`](../../../finance-api/app/crud/payment_execute.py#L300-L303) 把 `paid_amount = total_amount` 强置 paid,**会掩盖少付差额**。

## 决策(用户已确认)

1. **费用归属**:非PO费用照付,**仅填自由文本备注,不选类别**。
2. **交互**:**右键行 → 标记**(不做拖放投放区)。
3. **范围**:**只做 EPMS 标记**,不动 PA / 付款链路(见"范围外")。
4. **税**:被标记行只把税前 `line_total` 计入平账,不为非PO行单独处理税(与现有面板的税前口径一致,税在发票头统一 reconcile)。

## 设计

### 1. 交互(EPMS Allocation 面板)

在 [`InvoiceAllocationPanel.tsx`](../../../epms/src/pages/invoices/InvoiceAllocationPanel.tsx) 的发票行上:

- **右键** → 自定义上下文菜单 `Mark as other fee (shipping etc.)` → 弹小输入框填**备注**(可选,自由文本)。
- 被标记的行:从可拖拽池**移除**,原地灰显 + `Non-PO fee` 徽章 + 显示备注;右键菜单变为 `Unmark`(可逆)。
- 标记与拖拽**互斥**:标了不能再拖去 match;对已分配的行执行标记时,**自动清除其现有 PO 分配**(标记优先)。
- 上下文菜单是自定义浮层 → 用 `createPortal` + `position: fixed` + 点外关闭(触发+浮层两个 ref),避开祖先 `overflow` 裁剪。UI 文案全英文。

### 2. 平账门禁

`InvoiceAllocationPanel.tsx` 的平账计算改为:

```
excludedTotal = Σ(被标记非PO行的 line_total)      // 税前
unallocated   = invoice.amount − assignedTotal − excludedTotal
balanced      = |unallocated| < 0.01
```

goods 全分到 PO、shipping 全标记后即可平账,提交按钮亮起。

### 3. 持久化(零迁移)

- 复用 `invoice.line_items` JSONB,给被标记行加两个字段:`non_po_fee: true`、`non_po_note: <str|null>`。**不新建表、无 alembic 迁移。**
- 前端在 match 请求里带上被标记行的标识(`line_id` + `note`)。
- 后端 `match()` 把标记写回 `invoice.line_items`(JSONB 变更须 `flag_modified(invoice, "line_items")`)。
- Detail 页 [`InvoiceDetailPage.tsx`](../../../epms/src/pages/invoices/InvoiceDetailPage.tsx) 从 `line_items` 读 `non_po_fee` 渲染徽章+备注;重开 Allocation 面板时按持久化标记**预填**灰显。

### 4. 后端(epms-api)

- 扩展写入模型:`schemas/invoice.py` 的 `AllocationInput`/`InvoiceMatchRequest`(及前端 `MatchInvoiceBody`)增加 `non_po_lines: [{ line_id, note }]`(或等价结构)。
- [`crud/invoice.py`](../../../epms-api/app/crud/invoice.py) 完整性校验 [:282-286](../../../epms-api/app/crud/invoice.py#L282-L286) 改为:
  ```
  excluded_total = Σ(line_items 中 non_po_fee=true 行的 line_total)
  assert |alloc_total + excluded_total − invoice.amount| < 0.01   // 否则仍 AllocationImbalance → 422
  ```
- 在 `match()` 内把 `non_po_lines` 的标记落到 `invoice.line_items` 并 `flag_modified`。
- **`finance_sync` 不动**——AP 发票本就按发票头全额同步,含 shipping,账面正确。

### 5. 数据流

```
用户右键标记行(本地 state) ──▶ 平账门禁放行 ──▶ POST /invoices/{id}/match
   { allocations[], non_po_lines[] }
        │
        ├─ crud.match(): 写 allocations(仅 PO 行) + 写 non_po_fee 标记到 line_items
        │                校验 alloc_total + excluded_total == invoice.amount
        │
        └─ finance_sync.sync_ap_invoice(): 发票头全额(含非PO费用)同步到 finance AP ✅
```

## 范围外(已知限制,须在交付/发布说明中显著标注)

**PA / 付款不会自动带入非PO费用。** PA 仍按 PO 行重算,制单人需在 PA 现有的 `shipping_amount` / `other_charges` 字段**手工补**这笔费用;否则出纳会少付,而 `payment_execute` 会把 AP 强置"已付全额"([`payment_execute.py:300-303`](../../../finance-api/app/crud/payment_execute.py#L300-L303))掩盖差额。

后续建议单独立项做端到端"照付":把非PO费用自动带入 PA 的 `other_charges`,或修 `payment_execute` 的强置掩盖逻辑,使少付留为 AP 未清残余。

## 测试

**后端(epms-api,测试库 epms_test,覆盖 POSTGRES_* 到本地容器,串行跑)**
- 含非PO行的 match 通过(`alloc_total + excluded_total == amount`)平账成功。
- 非PO行合计不匹配 → 仍抛 `AllocationImbalance` / 422。
- `non_po_fee` + `non_po_note` 正确落到 `invoice.line_items` 并持久。
- unmark(不再传该行)后重新 match → 标记清除,回归原门禁。
- 正面证据验证:断言 line_items 内容与状态,不靠"无输出=通过"。

**前端(epms,TS 5.9.3;typecheck 用 `tsc -p tsconfig.app.json --noEmit`,不带 --ignoreDeprecations 6.0;对齐 59 tsc 基线)**
- 右键标记/取消菜单出现且可操作。
- 标记后行灰显、移出拖拽池、显示徽章+备注。
- 平账门禁随标记实时更新,提交按钮正确启用/禁用。
- Detail 页从持久化 line_items 预填徽章;重开面板预填灰显。

## 影响文件清单(预估)

- `epms/src/pages/invoices/InvoiceAllocationPanel.tsx` — 右键菜单、灰显、平账、非PO行 state。
- `epms/src/pages/invoices/MatchPanel.tsx` — 透传 `non_po_lines` 到 match 请求。
- `epms/src/services/invoices.ts` — `InvoiceLineItem` 加 `non_po_fee`/`non_po_note`;`MatchInvoiceBody` 加 `non_po_lines`。
- `epms/src/pages/invoices/InvoiceDetailPage.tsx` — 徽章+备注渲染。
- `epms-api/app/schemas/invoice.py` — `InvoiceMatchRequest` 加 `non_po_lines`。
- `epms-api/app/crud/invoice.py` — 平账校验 + 写 line_items 标记。
- `epms-api/app/api/v1/invoices.py` — 若需在路由层透传。
- `epms-api/tests/...` — 新增后端测试。

## 非目标

- 不做费用类别主数据(仅备注)。
- 不做拖放投放区(仅右键)。
- 不动 finance_sync / PA / payment_execute。
- 无数据库迁移。
