# NC 导入 PO 补录（Buyer Details）设计

日期：2026-08-07
分支：`feature/nc-po-buyer-details`（worktree `C:/Project/uniops-nc-po-edit`，基线 `main = d57578d`）

## 背景

NC ERP 镜像进 EPMS 的采购订单（`purchase_orders.source = 'nc'`，`type` 恒为 1）缺少大量面向供应商的辅助信息：供应商料号、样品要求、交期、收货地址、采购备注。这些信息 NC 里没有，只能由人工在 EPMS 侧补录，并体现在发给供应商的 PO 文件上。

当前系统不允许这件事：

- Detail 页 Edit 按钮门禁是 `procurement_officer && status ∈ {draft, returned}`（`epms/src/pages/po/PoDetailPage.tsx:551`），NC PO 状态只有 `issued` / `closed` / `nc_milk`，永远看不到按钮。
- 后端 `PATCH /po/{id}` 对非 `draft`/`returned` 状态直接 409（`epms-api/app/api/v1/po.py:125`）。
- PO PDF 只在审批通过（`status → approved`）时生成（`po.py:212-214`），NC PO 走不到该状态，因此**从不生成 PDF**。
- PDF 模板本身也没有打印 Buyer Notes（`epms-api/app/services/pdf_po.py`，只有全局 Terms）。

## 范围

**适用**：`source = 'nc'` **且** `status = 'issued'` 的 PO。

**不适用**：

- `status = 'closed'`（NC 已终结/已付清）—— 保持只读，避免改动已付款单据的金额口径。
- `status = 'nc_milk'`（原奶采购）—— 沿用既有只读约定。
- 非 NC 来源的 PO —— 走现有 draft/returned 编辑流程，不受本设计影响。

**可编辑字段**

| 层级 | 字段 | 可改 |
|---|---|---|
| 头 | Expected Delivery、Delivery Address、Incoterms、Tax Code + Tax Rate、Prepaid、Buyer Notes | 是 |
| 头 | Vendor、Currency、Title、Type、Budget Code、金额、状态 | 否 |
| 行 | Supplier Item ID、Sample | 是 |
| 行 | Description、Material ID、Qty、Unit、Unit Price、Line Total、增删行 | 否 |

## 1. 数据模型

epms-api 单个迁移，revision `nc03_po_buyer_details`，`down_revision = "nc02_nc_cutover"`。

> `nc02_nc_cutover` 是逐文件核对确认的唯一链尾（`…z1→z2→a1→a2→z3→z4→aa→ab→ac→ad→ae→af→nc01→nc02`）。revision id 长度 21 字符，未超 32 字符上限。

| 表 | 新列 | 类型 | 说明 |
|---|---|---|---|
| `purchase_orders` | `buyer_notes` | `TEXT NULL` | 人工补录的采购备注 / 条款 |
| `purchase_orders` | `incoterms` | `VARCHAR(100) NULL` | 单行自由文本贸易条款，如 `FOB Shanghai` / `DDP Toronto` |
| `purchase_orders` | `buyer_edited_at` | `TIMESTAMPTZ NULL` | 该 PO 已被人工补录的标记 |
| `po_line_items` | `sample` | `VARCHAR(100) NULL` | 自由文本样品量，如 `500 g` / `2 ea` |

无数据回填。`downgrade` 逐列 drop。

### Incoterms 的暴露范围（假设）

列本身建在 `purchase_orders` 头上，**不按 `type` 约束**（数据层不做类型分支，避免将来放开时改 schema）。但本期**只在 NC 补录编辑页提供录入**，Detail 页与 PDF 负责展示；`PoCreatePage` 与既有 `PoEditPage` 不加该输入框。

理由：本功能的立意是补齐导入 PO 缺失的信息；把字段推进 Create/Edit PO 会牵动 PR→PO 带值链路与既有两页的表单校验，超出当前目标。日后若要对所有 type=1 PO 开放，只需在那两页加一个输入框并把字段并进 `PoCreate` / `PoUpdate`，无需迁移。

### 为什么 Buyer Notes 不复用 `purchase_orders.notes`

NC 增量同步的 UPDATE 会覆写 `notes`，写入「NC 备注 + `[NC Paid]` / `[NC Closed <date>]` 等标记」（`nc_purchase_sync/writer.py:102-107`，标记由 `transform._derive_status_and_note` 拼装）。人工填进 `notes` 的内容下一轮同步即丢失；而这些 NC 标记是财务在读的，也不能挪走。因此两者各占一列：`notes` 归 NC 所有，`buyer_notes` 归人工所有。

## 2. NC 同步保护（`nc_purchase_sync/writer.py`）

同步的 PO UPDATE 覆写 `number, title, status, currency, subtotal, tax_rate, tax_amount, total, vendor_id, vendor_name, notes`；行 UPDATE 覆写 `description, material_id, qty, unit, unit_price, line_total, received_qty, sort_order`。

- `buyer_notes`、`incoterms`、`buyer_edited_at`、`po_line_items.sample`、`po_line_items.supplier_item_id` 都不在这两条语句的列清单里，**天然免疫覆盖**。改动仅在两条 SQL 上方补注释，说明这些列由人工拥有、不得加入 UPDATE 列表。
- `tax_rate / tax_amount / total` **会**被覆写，需要保护：当 `buyer_edited_at IS NOT NULL` 时，保留 DB 中的人工 `tax_rate`，并用 NC 送来的新 `subtotal` **重算** `tax_amount` 与 `total`：

  ```
  tax_amount = round(nc_subtotal * db_tax_rate, 2)
  total      = nc_subtotal + tax_amount
  ```

  之所以重算而非跳过：NC 若调整了行金额，`subtotal` 会变，简单跳过会让 `subtotal + tax ≠ total`。

- 另有一层既有保护：`_po_consumed()`，一旦有任何 invoice 引用该 PO，同步会整单跳过（`writer.py:43-53`），人工补录的数据在那之后完全不会被触碰。

## 3. 后端 API（epms-api）

### 新端点

`PATCH /api/v1/po/{po_id}/imported-details`

请求 schema `PoImportedDetailsUpdate`（`app/schemas/po.py`）：

```
expected_delivery : date | None
delivery_address  : str | None
incoterms         : str | None       (max 100)
tax_code          : str | None       (max 20)
tax_rate          : Decimal | None   (0 <= r <= 1)
is_prepaid        : bool | None
buyer_notes       : str | None
lines             : list[PoImportedLineUpdate]   # { id: UUID, supplier_item_id: str|None, sample: str|None }
```

响应沿用 `PoResponse`（新增 `buyer_notes`、`incoterms`、`buyer_edited_at` 字段；`PoLineItemResponse` 新增 `sample`）。

### 行为

1. **门禁**：`require_permission("epms.po.edit_imported")`。
2. **前置校验**：PO 存在，否则 404；`po.source == 'nc'` 且 `po.status == 'issued'`，否则 409（detail 区分「非 NC 导入 PO」与「状态不允许」两种消息）。
3. **行更新**：对 `lines` 中每个 id，必须存在且 `po_id` 等于本 PO，否则 400；只写 `supplier_item_id` 与 `sample` 两列。请求中未出现的行不动，**不删不建**。
4. **税额重算**：若传了 `tax_rate`，`tax_amount = round(subtotal * tax_rate, 2)`，`total = subtotal + tax_amount`。`subtotal` 永不由本端点改动。
5. 置 `buyer_edited_at = now()`。
6. 写 `admin_audit_log`（记录 PO 号与改动字段集合）。
7. 提交后触发 PDF 重生成（见 §4）。

### 为什么用专用端点而非放宽 `PATCH /po/{id}`

现有 `PoUpdate` 允许替换 `vendor_id`、`currency` 和**整包重建 `line_items`**（`crud/po.py` 的 update 是 delete + rebuild）。若只是给 NC PO 放宽状态门禁，任何持 `epms.po.write` 的人就能改一张已进入发票/付款流程的 NC PO 的金额与供应商。窄 schema 把「不可改」写进类型里；前端 disabled 只是 UX，端点边界才是防线。

### 权限键

新增矩阵键 `epms.po.edit_imported`，label `Edit Imported (NC) POs`，module `epms`，sort `104`。

默认授予：`system_admin`、`erp_pa_officer`。

需要同步登记的位置（与 `epms.po.write` / `epms.pa.write` 的既有登记点一致）：

- `identity-api/scripts/seed_phase2_keys.py`：`PHASE2_KEYS` + `PHASE2_DEFAULTS`
- `epms-api/app/crud/config.py`：矩阵镜像（`_P(...)` 角色默认表）
- identity-api 新迁移 `0006_po_edit_imported`（`down_revision = "0005_procurement_officer_pa"`），幂等插入 `permission_defs` 行并 grant 给上述两个角色

**不改** `identity-api/scripts/verify_gate_parity.py`：该文件是冻结基线，按既有约定不随新键同步。

## 4. PDF（`pdf_po.py` + `po.py`）

### 重生成

`_generate_po_pdf_background()` 现在遇到同名附件直接 `return`（`po.py:163-170`）。新增 `force: bool = False` 参数：`force=True` 时先删除该 PO 下同名 `PoAttachment` 记录再生成上传。NC 补录端点以 `force=True` 调用；审批通过路径继续用默认 `force=False`，行为不变。

### 版式

1. **Sample 列**：行表在 Unit 之后插入 `Sample` 列，**仅当该 PO 存在任一行 `sample` 非空时才渲染**。列宽从 Description 让出：`col_w` 中 Description 由 `W*0.28` 降为 `W*0.18`，新列 20mm。

   宽度核算（A4，`W = 210 - 40 = 170mm`）：现有固定列合计 `8+26+15+14+28+28 = 119mm`，Description `W*0.18 = 30.6mm`，Sample `20mm`，总计 `169.6mm ≤ W`。（若沿用 `W*0.20` 会得 173mm 溢出，故取 0.18。）

   既有 PO 不含 sample，走原分支，版式一字不变。
2. **Incoterms 行**：置于 Totals 之后、**Buyer Notes 之前**，渲染为一行 `Incoterms: <值>`，label 用 `lbl_style`、值用 `val_style`（与页头 meta 网格的 `_cell()` 同一套字号，不新增样式）。`incoterms` 为空时整行不渲染。

3. **Buyer Notes 段落**：置于 Incoterms 之后、全局 Terms 之前，有内容才渲染。取值：

   ```
   text = po.buyer_notes or (po.notes if po.source != 'nc' else None)
   ```

   NC PO 在 `buyer_notes` 为空时**不回退** `notes`，否则会把 `[NC Paid]` 这类内部标记印到发给供应商的单子上。非 NC PO 回退 `notes`，顺带补上 Create PO 页那个「Buyer Notes / Terms & Conditions」框，它目前从未进过 PDF。

## 5. 前端（epms）

### PoDetailPage

- Edit 按钮门禁改为：

  ```
  canEdit         = isProcurementOfficer && status ∈ {draft, returned}          // 原逻辑
  canEditImported = po.source === 'nc' && po.status === 'issued'
                    && (user.role === 'system_admin' || perms['epms.po.edit_imported'])
  ```

  `canEditImported` 指向新路由，按钮文案 `Edit Details`（与普通 Edit 区分）。权限读取沿用 `useRolePermissions()`，与 `PaListPage.tsx:122-123` 同一模式。
- 行表格新增 Sample 列（仅当有值时显示，与 PDF 同规则）。
- 头部信息区新增 Incoterms 与 Buyer Notes 展示（均为有值才显示；Incoterms 排在 Buyer Notes 之前，与 PDF 顺序一致）。

### 新页 `PoImportedEditPage.tsx`

路由 `/po/:id/edit-imported`（注册进 `epmsRoutes`）。版式抄 `PoCreatePage` / `PoEditPage`：

- 只读展示（非输入框）：Vendor、Currency、Type、Title、Budget Code
- 可编辑：Expected Delivery、Delivery Address、Incoterms（单行 `Input`）、Tax Code + Tax Rate、Prepaid、Buyer Notes
- 行项目区使用 `PrLineItems`，开启锁定模式
- 进页先校验 `source === 'nc' && status === 'issued'`，不满足则提示并跳回 Detail
- 税率沿用既有 Create/Edit PO 的规则：`effectiveTaxRate = currency === 'CAD' ? taxRate : 0`（`PoEditPage.tsx:105`）。非 CAD 币种下税率输入禁用并显示 0，避免新页与既有两页出现三种口径
- 保存调用新端点，成功后回 Detail 页并提示 PDF 正在重新生成

### `components/pr/PrLineItems.tsx`

新增两个可选 prop（默认 `false`，PR 侧与既有 PO 页零影响）：

- `lockedExceptSupplier?: boolean`：除 Supplier Item ID / Sample 外全部 readOnly，隐藏增行按钮、禁用删行按钮
- `showSample?: boolean`：显示 Sample 输入列

该组件有**桌面表格**与**移动卡片**两套渲染（约 `:489` 与 `:671` 两处），两处都要改。类型 `PrLineItem`（`epms/src/types`）新增 `sample?: string`。

### 服务层

`epms/src/services/po.ts` 新增 `updateImportedDetails(id, payload)`；`hooks/usePos.ts` 新增 `useUpdatePoImportedDetails()` mutation，成功后 invalidate PO 详情与附件列表查询。

## 6. 测试

### 后端（`epms-api/tests`）

- 权限：无 `epms.po.edit_imported` → 403；`erp_pa_officer` → 200
- 范围：`source != 'nc'` → 409；`status = 'closed'` / `'nc_milk'` → 409
- **越权**：`lines[].id` 属于另一张 PO → 400，且目标行未被修改
- 不可改字段：请求体不含 vendor/currency/qty/unit_price，保存后这些值与改前逐字段相等
- 税额：改 `tax_rate` 后 `tax_amount`/`total` 正确，`subtotal` 不变
- `buyer_edited_at` 由 NULL 置为非 NULL
- 同步保护：模拟一次 NC 增量 upsert，断言 `buyer_notes`、`incoterms`、`sample`、`supplier_item_id` 未变，且 `tax_rate` 保持人工值、`tax_amount`/`total` 按新 `subtotal` 重算

### PDF

- 无 sample：列数与改前一致（版式回归）
- 有 sample：出现 Sample 列
- `buyer_notes` 有值：PDF 含该文本
- `incoterms` 有值：PDF 含该文本，且**位置在 Buyer Notes 之前**（断言两段在 story 中的相对顺序，不只断言存在）
- `incoterms` 为空：不渲染该行（无空 label 残留）
- NC PO 且 `buyer_notes` 为空：PDF **不含** `notes` 里的 NC 标记
- `force=True`：旧附件被替换而非跳过

### 前端

`npx tsc -p tsconfig.app.json` 门禁。基线错误数**动手前先在本 worktree 实测**，不照抄历史数字；worktree 需先 `npm ci`，否则 tsc 只输出安装提示（假阴性）。

## 7. 交付边界

**不做**（YAGNI）：

- Sample 不向 GR / Invoice / PA 传导
- Incoterms 不加进 `PoCreatePage` / 既有 `PoEditPage`（见 §1「Incoterms 的暴露范围」的假设说明）
- Incoterms 不做标准代码下拉（EXW/FOB/CIF…），按需求为单行自由文本
- 不做 sample 的结构化数量/单位拆分（用户选定自由文本）
- 不做编辑历史时间线 UI（仅落 `admin_audit_log`）
- 不改 `closed` / `nc_milk` PO 的只读性
- 不动 `verify_gate_parity.py`

## 8. 部署要点

- 迁移：epms-api `nc03_po_buyer_details`、identity-api `0006_po_edit_imported`（**有迁移**，发布需跑 `migrate-prod.sh`）
- 受影响镜像：`epms-api`、`epms`（前端）、`identity-api`
- 上线后须在 Portal Admin → Access Control 确认 `epms.po.edit_imported` 已勾给 `erp_pa_officer`
