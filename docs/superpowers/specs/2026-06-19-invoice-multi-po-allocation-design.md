# Invoice 多 PO 行级分摊 — 设计文档

- 日期：2026-06-19
- 模块：EPMS（epms-api / epms 前端）
- 状态：设计待评审

## 1. 背景与问题

当前 EPMS 发票（Invoice）与采购订单（PO）是**单一外键**关系：`invoices.po_id` / `po_number` 单值
（`epms-api/app/models/invoice.py`）。三方匹配 `crud/invoice.py:match()` 按单个 PO 计算 variance，
逻辑是「该 PO 上所有发票 `total_amount` 之和 vs 该 PO（或选定行）的 reference total」——即已支持
「一个 PO 对多张发票」，但反过来**一张发票只能挂一个 PO**。

新需求带来两个现实场景：

1. **一票多 PO**：供应商把多个采购单合并开一张发票，需要把发票金额分摊到多个 PO。
2. **发票行与 PO 行对不上**：供应商发票行通常**比 PO 行更细、口径也不一致**
   （PO 一行 "Packaging consumables $1000"，发票拆成 5 行规格明细）。因此无法假设
   「发票行结构 = PO 行结构」，对账只能落在**金额维度**，而归类靠人工。

## 2. 关键决策（已与干系人确认）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 是否按 PO 分摊金额 | **需要**按 PO 分摊（完整 ERP 方向） |
| 2 | 分摊粒度 | **PO 行级**（分摊目标是 PO 的行，不是 PO 头） |
| 3 | 数据建模 | **方案 A**：独立分摊明细表 `invoice_po_allocations` |
| 4 | GR（收货）对账粒度 | **本期不变**，GR 仍整票级 `gr_ids`；三方匹配中 GR 暂整票比对 |
| 5 | 内部完整性校验 | **必须分完才能 match**：各分摊合计 == 发票总额，否则 422 |
| 6 | 分摊录入交互 | **发票行拖到 PO 行**（多个发票行归集到一个 PO 行，金额自动汇总） |

## 3. 目标 / 非目标

**目标（本期）**
- 一张发票可关联并分摊到多个 PO 的多个行。
- 发票行加稳定 id，支撑「发票行 → PO 行」的拖拽归类与追溯。
- 三方匹配改造为：整票完整性校验 + 逐 PO 行 variance + 整票状态汇总。
- 权限可见性改造（任一关联 PO 命中用户 scope 即可见）。
- 存量单 PO 发票数据迁移。
- 前端「发票行 ↔ PO 行」分摊编辑 UI。

**非目标（后续迭代）**
- GR 行级三方匹配（本期 GR 维持整票级 `gr_ids`）。
- posting / 预算入账按分摊维度入账（仅在数据模型上预留）。
- 按 PO 的部分付款（发票仍是整票付款单位）。
- 税行拆分到 PO（本期 allocation 含 `allocated_tax` 字段即够）。

## 4. 数据模型

### 4.1 发票行加稳定 id

`invoices.line_items` 现为 JSONB 数组、行无主键。为支撑拖拽归类与追溯，每行新增稳定 `id`（uuid）：

```jsonc
{ "id": "<uuid>", "description": "...", "quantity": 1, "unit": null,
  "unit_price": 0, "line_total": 0 }
```

- `InvoiceLineItem` schema 增加 `id: uuid.UUID`（创建时若缺省由后端生成、入库固化）。
- 存量发票行在迁移中补 id（见 §8）。

### 4.2 新表 `invoice_po_allocations`

一条记录 = 「发票的某一行的金额，核销到某个 PO 的某一行」。支持多对一
（多个发票行归一个 PO 行）；模型上也允许一个发票行拆到多个 PO 行（金额可小于行额），
但 MVP 拖拽 UI 仅做「整行拖入」。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | uuid PK | |
| `invoice_id` | uuid FK invoices ON DELETE CASCADE | |
| `invoice_line_id` | uuid | 归属的发票行 id（追溯用） |
| `po_id` | uuid FK purchase_orders ON DELETE RESTRICT | |
| `po_line_id` | uuid FK po_line_items ON DELETE RESTRICT，nullable | null = 该 PO 头级分摊（回退） |
| `allocated_amount` | numeric(15,2) | 不含税 |
| `allocated_tax` | numeric(15,2) default 0 | |
| `allocated_total` | numeric(15,2) | = amount + tax |
| `variance` | numeric(15,2) nullable | 该 PO 行算出的偏差（match 时写） |
| `variance_pct` | numeric(8,4) nullable | |
| `note` | text nullable | 备注 |
| `created_at` / `updated_at` | timestamptz | |

索引：`invoice_id`、`po_id`、`po_line_id`。

> 实现注意（项目历史教训）：新建镜像/新表前先核对物理列定义，nullable / unique / 默认值
> 不要套惯例；不要盲目套 `TimestampMixin`，与本表实际列对齐即可。

### 4.3 发票头字段：保留 + 语义调整（向后兼容）

照搬已验证的「多 GR」模式（`gr_id` 存首个、`gr_ids` 存全集）：

- `po_id` / `po_number`：保留，存**主/首个**分摊 PO（让列表页、权限 scope、旧前端少改）。
- `po_total` / `gr_value` / `variance` / `variance_pct`：保留为**汇总值**
  （所有关联 PO reference 之和 / 所有 GR 之和 / 发票总额 − 总 reference），用于列表摘要；
  明细看 allocation 表。
- `matched_po_line_ids` / `matched_reference_total`：被 allocation 表取代，保留兼容读，新流程不再写。
- `status`：**整票仍为单一状态**（unmatched | matched | exception | approved | paid）——
  发票是审批与付款单位，不拆成多状态。

## 5. API 改造

### 5.1 匹配请求

`InvoiceMatchRequest` 由「单 PO」改为「提交一组分摊」：

```python
class AllocationInput(BaseModel):
    invoice_line_id: uuid.UUID
    po_id: uuid.UUID
    po_line_id: uuid.UUID | None = None   # null = PO 头级
    allocated_amount: Decimal             # 不含税
    allocated_tax: Decimal = Decimal("0")
    note: str | None = None

class InvoiceMatchRequest(BaseModel):
    allocations: list[AllocationInput]
    gr_ids: list[uuid.UUID] | None = None   # 整票级，维持现状
```

### 5.2 匹配流程（`crud/invoice.py:match()` 重写）

1. **完整性校验（硬，违反 → 422）**：`sum(allocated_amount + allocated_tax)` 必须等于
   `invoice.total_amount`（允许极小舍入差，如 `abs(diff) <= 0.01`）。分不完 / 超分不允许进 match。
   并校验每个 `invoice_line_id` 属于本发票、`po_line_id` 属于对应 `po_id`。
2. **重建分摊（幂等）**：删除该发票旧 allocation，按请求批量插入（与现有 match 可重复调用语义一致）。
3. **逐 (po_id, po_line_id) 算 variance**：
   - reference = 该 PO 行 `line_total`（`po_line_id` 为 null 时回退 `po.total`）。
   - invoiced = **所有发票**对该 PO 行的累计 `allocated_total`
     （现有按 `po_id` 聚合 `invoice.total_amount` 的自然推广：聚合键变 `po_line_id`，被聚合量变 allocation 额）。
   - variance / variance_pct 用现有 `within_tolerance()`（FIN-AP-001）判定，结果写回对应 allocation 行。
4. **汇总回整票**：
   - 任一分摊超容差 → `status = exception`，`exception_reason` 列出超差的 PO/行与差额。
   - 全部在容差内 → `status = matched`（零差或容差内自动匹配，差额留档审计）。
   - 头级 `po_total` / `gr_value` / `variance` / `variance_pct` 写汇总值；`po_id` / `po_number` 写主 PO。
5. GR：沿用现有整票级处理（`gr_ids` 解析、`gr_value` 汇总），本期不下沉到分摊。

### 5.3 读取

- `InvoiceResponse` 增加 `allocations: list[AllocationResponse]`（明细），头级字段语义同 §4.3。
- 新增按 PO 行查「已开票额 / 剩余可开票额」的能力（供分摊 UI 显示），可作为 PO 详情或分摊接口的派生字段。

## 6. 权限可见性改造

现 `crud/invoice.py:get_all()` / `is_visible()` 按 `invoice.po_id ∈ po_subq` 过滤。多 PO 后，
主 `po_id` 可能不在 scope、但发票关联了 scope 内的其他 PO，会**错误地不可见**。

改为：「**存在**某条 `invoice_po_allocations.po_id ∈ po_subq`」——用 `EXISTS` 子查询或
`invoice_id IN (SELECT invoice_id FROM invoice_po_allocations WHERE po_id IN (po_subq))`，
与 `uploaded_by` 条件做 OR。`requester` 仅自己上传仍保留。

## 7. 前端（epms）

- 发票详情 / 匹配页：左侧发票行（供应商原样，可更细），右侧已关联 PO 的行（含原额 / 已开票 / 剩余）。
- 交互：把发票行（可多选）拖到目标 PO 行；系统按发票行 `line_total` 汇总该 PO 行的分摊额。
  支持跨多个 PO。
- 实时显示：未分摊余额（必须归零才能提交 match）、每个 PO 行的 variance 提示。
- 所有 user-facing 文案**纯英文**（如 "Unallocated balance"、"Drag invoice lines onto a PO line"、
  "Allocated"、"Remaining on PO line"、"Variance"）。页面包在 PortalChromeLayout / 现有 EPMS chrome 内。
- Decimal 字段后端以字符串返回，前端 `Number()` 强制转换后再 `toFixed()` / 运算。

## 8. 数据迁移

1. 给存量 `invoices.line_items` 每行补 `id`（uuid）。
2. 为每张**已有 po_id** 的发票生成 allocation：
   - 若有 `matched_po_line_ids`：按这些 PO 行生成（金额可先整票挂到首行或按比例，迁移脚本需明确策略——
     建议整票额挂到「单条头级 allocation」`po_line_id=null` 以零风险保平，匹配状态/variance 维持原值）。
   - 否则：单条头级 allocation（`po_line_id=null`，`allocated_total = invoice.total_amount`）。
3. 头级 `po_id` / `po_total` / `variance` 等保持原值（已是单 PO 汇总，天然兼容）。
4. 迁移用独立 alembic revision；执行前核对线上列定义，避免 stamp/列不一致问题。

## 9. 测试

- 单元（epms-api/tests）：
  - 完整性校验：分不完 / 超分 → 422；恰好分完 → 通过。
  - 多 PO 多行分摊：逐 PO 行 variance 正确；任一超差 → 整票 exception；全部容差内 → matched。
  - 多对一：多个发票行归一个 PO 行，金额汇总正确。
  - 幂等：重复 match 覆盖旧 allocation 不重复累计。
  - 权限：发票仅关联 scope 外主 PO、但有 scope 内分摊 PO → 可见。
  - **成功路径必须覆盖**（项目历史教训：冒烟需覆盖 happy path）。
- 迁移测试：存量单 PO 发票迁移后状态 / 金额 / 可见性不变。
- 前端 typecheck：`tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`。

## 10. 风险与权衡

- **发票行 id 进 JSONB**：无法加 FK 约束，靠应用层保证唯一与归属；迁移需回填。可接受（拖拽必需）。
- **头级 variance 语义变化**：从「单 PO 偏差」变「汇总偏差」，依赖该字段的旧报表需复核。
- **GR 仍整票级**：一票多 PO 且各 PO 有不同 GR 时，GR 比对不精确——本期接受，列为后续。
- **分摊 UI 复杂度**：发票极细时拖拽操作量大；后续可加「按金额规则自动建议分摊」。

## 11. 分期

- **本期**：§4–§9 全部（发票行 id、allocation 表、匹配重写、权限、迁移、拖拽 UI、测试）。
- **后续**：GR 行级三方匹配；posting / 预算按分摊入账；按 PO 部分付款；自动分摊建议。
