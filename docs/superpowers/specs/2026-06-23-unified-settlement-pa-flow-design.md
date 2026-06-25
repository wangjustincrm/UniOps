# 统一 Settlement PA 流程 — 设计文档

- 日期:2026-06-23
- 模块:UniOps EPMS — Payment Application (PA)
- 状态:已批准设计,待写实现计划

## 1. 背景与问题

EPMS 当前有两种产生 Settlement PA 的方式,行为不一致、概念冲突:

**Path A —— Prepayment PA 详情页的「Settle」按钮**
- 路由 `/pa/:id/settle` → [SettlementTaskPage.tsx](../../../epms/src/pages/pa/SettlementTaskPage.tsx)
- 简单表单:仅填「最终发票金额」+ 备注
- 调用 `POST /pa/:id/settle`,**就地修改原预付 PA**:置 `settlement_status`、`settled_at`、`settlement_variance`
- **不产生新 PA、不付款、不走审批**——只是在原预付上记一笔对账

**Path B —— Create New PA 选择 Settlement 类型**
- [PaCreatePage.tsx](../../../epms/src/pages/pa/PaCreatePage.tsx),`paType='settlement'`
- 完整页面:PO 选择 + Invoice/GR/行 匹配 + charge breakdown
- **创建一个全新的 `settlement` 类型 PA**,通过 `prepayment_pa_id` 关联原预付,走完整审批流程
- 不触碰原预付的 `settlement_status`

### 已确认的 Bug
Path B 的「Original Prepayment PA Number」是**自由文本**,期望输入 `PA-YYYYMMDD-XXXX`,
但后端 `prepayment_pa_id` 是 **UUID 外键**([models/pa.py:42](../../../epms-api/app/models/pa.py#L42)、
[schemas/pa.py:49](../../../epms-api/app/schemas/pa.py#L49))。输入 PA 编号字符串会 422。
即 Path B 目前实际不可用。

### 下游约束
finance 付款执行器直接用 `pa.payment_amount` 作为银行付款额与 AP 借方
([payment_execute.py:307-331](../../../finance-api/app/crud/payment_execute.py#L307))。
因此 Settlement PA 的 `payment_amount` 必须正好等于要付的余款。

## 2. 目标

1. 在 Prepayment PA 详情页点「Settle」时,进入与 Create New PA(Settlement 类型)**同一个完整页面**,
   只是把很多默认信息直接从来源预付 PA 带入。
2. 在 New PA 选 Settlement 类型时,「Original Prepay PA」根据所选 PO **自动加载**关联的预付 PA 列表
   (下拉选择,替换自由文本,并修上述 UUID bug)。
3. 消除两种机制的概念冲突:统一为「创建一个 Settlement 类型 PA」这一种产生方式。

## 3. 关键决策(已与用户确认)

| 决策点 | 结论 |
| --- | --- |
| Settlement PA 审批后,原预付 `settlement_status` 怎么处理 | **自动标记为 settled**(并算好 variance);旧 in-place `/settle` 端点 + SettlementTaskPage 退役 |
| Settlement PA 的 `payment_amount` 代表什么 | **余款 = 最终发票 − 已预付**(银行实付金额) |
| 余款在 charge breakdown 怎么体现 | **思路 A**:新增 `prepayment_applied` 抵扣列,Net Payable = 全额 − 抵扣 |
| 从 Settle 按钮进入后 PO 是否可改 | **锁定 PO**,预填且不可改;Original Prepay PA 也直接预填为来源预付 |
| 自动标 settled 的时机 | Settlement PA 转为 **approved** 时(与 PDF 生成同一钩子) |

## 4. 详细设计

### 4.1 后端 — 数据模型(思路 A)

- [models/pa.py](../../../epms-api/app/models/pa.py):新增
  `prepayment_applied: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)`
  ——预付抵扣额。
- 新建 alembic 迁移:`payment_applications` 加 `prepayment_applied`(nullable,默认 NULL),
  注意挂在 epms-api 的 alembic head 之后。
- `_compute_payment`([crud/pa.py:53](../../../epms-api/app/crud/pa.py#L53))改为:
  `subtotal + tax_amount + shipping_amount + other_charges − (prepayment_applied or 0)`
  → 得到净付余款 `payment_amount`。finance 执行器读的就是这个净额,**无需改 finance**。
- [schemas/pa.py](../../../epms-api/app/schemas/pa.py):`PaCreate`、`PaUpdate`、`PaResponse`
  增加 `prepayment_applied: Decimal | None`(`ge=0`)。
- `crud/pa.py` 的 `create()` / `update()` 写入并参与 `payment_amount` 重算。

### 4.2 后端 — 校验 & 自动回填

**创建校验(api/v1/pa.py `create_pa`)**
- 保留现有 `settlement`/`balance` 分支:`prepayment_pa_id` 必填且必须是该 PO 下的 prepayment PA
  (PP-009,已存在)。前端改传 UUID 后此校验真正生效,修复 422 bug。
- 新增:settlement PA 的 Net Payable(`subtotal+tax+ship+other − prepayment_applied`)必须 ≥ 0。
  - 若预付 > 最终发票额(overpaid):允许净额 = 0 的 reconcile-only 结算(不付款),
    并在响应/备注提示「overpaid — credit note expected」;**不允许负数**。

**自动回填预付 settled(api/v1/pa.py `pa_action`)**
- 在已有「`new_status == 'approved'`」分支([api/v1/pa.py:267](../../../epms-api/app/api/v1/pa.py#L267))内补充:
  若 `pa.pa_type == 'settlement'`,加载 `pa.prepayment_pa_id` 指向的预付 PA,设置:
  - `settlement_status = 'settled'`
  - `settled_at = now`
  - `settled_by = 审批动作的 actor`
  - `settlement_variance = 净余款(Net Payable)`
  - `settlement_note = f"Settled via {settlement_pa.pa_number}"`
- 复用 `pa_crud.settle()` 的内部逻辑(改造为内部 helper,见 4.3)。

### 4.3 后端 — 退役旧机制

- 删除 HTTP 端点 `POST /pa/:id/settle`([api/v1/pa.py:304](../../../epms-api/app/api/v1/pa.py#L304))
  及对外 `PaSettleRequest` 用法。
- `pa_crud.settle()` 不再由 HTTP 直接调用;保留为内部 helper(或重写为
  `mark_prepayment_settled(db, prepayment_pa, settlement_pa, actor_id)`)供 4.2 的自动回填复用。
- 前端 `paService.settle` / `SettlePaBody` / `useSettlePa` 一并移除(见 4.5)。

### 4.4 前端 — Settle 按钮改跳转

- [PaDetailPage.tsx](../../../epms/src/pages/pa/PaDetailPage.tsx) 两处「Settle Prepayment」/「Settle Now」按钮:
  由 `navigate('/pa/${pa.id}/settle')` 改为 `navigate('/pa/new?settleFrom=${pa.id}')`。
- 删除 [SettlementTaskPage.tsx](../../../epms/src/pages/pa/SettlementTaskPage.tsx)。
- 删除 [App.tsx](../../../epms/src/App.tsx) 中 `/pa/:id/settle` 路由与 import。

### 4.5 前端 — PaCreatePage 统一页

**下拉替换自由文本(满足目标 2)**
- settlement/balance 类型时,「Original Prepayment PA」改为下拉:
  数据源 `usePas({ po_id: selectedPoId })`(已有 `poActivePas`),过滤
  `pa_type === 'prepayment'` 且 `status ∉ {cancelled, rejected}`。
- 选项展示 PA 编号 + 金额 + 日期;选中存 **PA.id(UUID)** 到 `prepaymentPaId`。
- 随 `selectedPoId` 变化自动重载;切 PO 时清空已选预付。
- 该 PO 无可关联预付时显示空态提示。

**Settle 按钮预填(`?settleFrom=<prepaymentPaId>`)**
- 读取 URL 参数,用 `usePa(settleFrom)` 加载来源预付 PA。
- 加载后:
  - `selectedPoId = prepay.po_id`,并**锁定 PO 选择步骤**(只读,隐藏/禁用搜索与列表)。
  - `paType = 'settlement'`。
  - `prepaymentPaId = prepay.id`(下拉预选且锁定)。
  - 默认勾选全部(或全部已收)PO 行,得出最终发票额(沿用现有 autoSubtotal / 自动税额逻辑)。
  - `prepayment_applied` 预填 = `prepay.payment_amount`。

**charge breakdown 加抵扣行**
- 在 Total 之上新增只读/可调「Prepayment Applied −X」字段(绑定 `prepayment_applied`)。
- 底部「Total Payment Amount」改为 **Net Payable = subtotal+tax+ship+other − prepayment_applied**。
- 右侧 sidebar 的 Payment Summary 同步增加「Prepayment Applied」与「Net Payable」两行。
- overpaid(净额 < 0)时显示警告并把净额按 0 处理(reconcile-only),提示需 credit note。

### 4.6 提交 payload 修正

`handleSubmit`([PaCreatePage.tsx:236](../../../epms/src/pages/pa/PaCreatePage.tsx#L236))中:
- `prepayment_pa_id` 传 UUID(下拉选中的 PA.id),不再是自由文本。
- 增加 `prepayment_applied`(settlement/balance 时传,其余 undefined)。

## 5. 边界情况

- **Overpaid**(预付 > 最终发票):净额=0 的 reconcile-only 结算,不付款,提示 credit note;禁止负数。
- **来源预付不存在 / 已 settled**:`?settleFrom` 指向无效或已结算的 PA 时,页面给出明确提示并允许返回。
- **非 prepayment 来源**:`settleFrom` 指向的 PA 非 prepayment 类型时拒绝预填。
- **PO 锁定**:从 Settle 进入后不可切 PO;直接在 New PA 选 settlement 时 PO 可选,Original Prepay PA 随 PO 联动。
- **在途旧数据**:已存在的、用旧 in-place `/settle` 标记过的预付不受影响(已是 settled);
  退役端点不影响历史记录。

## 6. 测试

**epms-api/tests/test_pa.py**
- Settlement PA 创建:`prepayment_pa_id` 用合法 UUID,引用同 PO 的 prepayment → 成功;
  引用别的 PO / 非 prepayment / 不存在 → 422。
- 净额计算:`payment_amount == subtotal+tax+ship+other − prepayment_applied`。
- 自动回填:settlement PA 审批至 approved 后,来源预付 `settlement_status=='settled'`、
  `settlement_variance` 正确、`settled_by` 为审批人。
- Overpaid 校验:净额 < 0 被拒;净额 = 0 reconcile-only 通过。

**前端**
- typecheck:`tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`。
- 手动/冒烟:Settle 按钮 → 统一页预填正确、PO 锁定;直接选 settlement → 下拉随 PO 联动。

## 7. 不在本次范围

- 深层预付会计科目处理(prepaid-asset 冲销等);沿用现有简化 AP/bank posting。
- `balance` 类型 PA 的独立流程(仅同步修复其 `prepayment_pa_id` 下拉,行为不变)。
- 退款/credit note 单据的自动生成(仅提示)。
