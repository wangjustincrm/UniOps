# Finance AP Invoice 主模块 — 设计文档

- 日期：2026-06-20
- 模块：finance-api（新 AP Invoice 模块）+ epms-api / expense-api（来源接入）+ 前端（Finance AP 管理）
- 状态：设计待评审

## 1. 背景与目标

需求：**Invoice 的核心最终归属 finance-api**。EPMS 和 OA 上传的供应商发票都属于 **AP 模块下的 Invoice**；未来 Sales 模块的销售发票属于 **AR 模块**。无论入口在 EPMS 还是 OA，最终都指向 Finance 的 Invoice 模块统一管理。

**现状差距**（结构检查结论）：
- finance-api 的 **AR 侧已经是目标形态**——`ar_invoices` 由 finance 拥有（[finance-api/models/ar.py](../../../finance-api/app/models/ar.py)），注释已预设"未来 Sales 模块作 system of record，finance 镜像"。
- finance-api 的 **AP 侧整块缺失**——没有 AP 发票主表，只在 `mirrors.Invoice` 只读镜像 EPMS 的 `invoices`（[finance-api/api/v1/ap.py](../../../finance-api/app/api/v1/ap.py)），且 **OA 的 `expense_invoices` 根本没进 finance AP**。发票主数据分散在 epms-api `invoices` 和 expense-api `expense_invoices` 两套。

**本设计目标**：在 finance-api 新建对称的 **AP Invoice 主模块**，成为所有应付发票的真相源；EPMS、OA 作为录入来源同步喂入；并提供 finance 的 AP Invoice 统一管理界面 + Aging report。

## 2. 关键决策（已与干系人确认）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | finance AP 持有粒度 | **规范化发票头 + 税行**；业务明细（EPMS PO 三方匹配/分摊、OA OCR/行）留源系统，按 `source + source_invoice_id` 关联。与 `ar_invoices` 对称 |
| 2 | 写入机制 | **同步服务调用**（扩展现有 `finance_client` 模式）|
| 3 | 本期范围 | **EPMS + OA 两来源一次接** + finance AP 模块 + 存量迁移 + **前端 AP 管理界面 + Aging report** |
| 4 | 录入起点 | **一录入就进**（draft 全量）；finance 是全量真相源 |
| 5 | 日期字段 | 头含 `invoice_date`(= Issue Date) 与 `due_date`；Aging 以 `due_date` 分桶 |
| 6 | 源系统表去留 | 本期**保留不动**（作录入工作区/业务明细），finance `ap_invoices` 为 AP 真相源 |

## 3. 目标 / 非目标

**目标（本期）**
- finance 新建 `ap_invoices` + `ap_invoice_tax_lines` 表与迁移。
- finance AP CRUD/upsert + list/get/void + 归口号；`/ap/open-items`、`/ap/aging`、accrual 改读 `ap_invoices`。
- EPMS、OA 在发票生命周期关键点同步 upsert 到 finance（fail-open + 对账兜底）。
- 存量 EPMS `invoices` + OA `expense_invoices` 回填到 `ap_invoices`。
- 前端：Finance 下 **AP Invoice 管理页**（全量列表 + 过滤 source/vendor/status）+ **Aging report 页**（消费 `/ap/aging`）。

**非目标（后续）**
- 移除/瘦身源系统的 `invoices` / `expense_invoices` 表（本期保留）。
- AR 侧改造（已是目标形态）；Sales 模块。
- upsert 失败的自动重试队列（本期 fail-open + 对账脚本即可）。
- 统一替换 EPMS/OA 现有发票详情页（本期它们不变，仍读各自后端）。

## 4. 领域模型：AP / AR 对称

finance 下形成对称双模块：
- **AR**（已存在）：`ar_invoices` + `ar_invoice_tax_lines` + `ar_receipts`，finance 拥有，未来 Sales 作来源。
- **AP**（本期新建）：`ap_invoices` + `ap_invoice_tax_lines`，finance 拥有，EPMS/OA 作来源。

## 5. 数据模型

### 5.1 `ap_invoices`（对照 `ar_invoices`）

```
ap_invoices
  id                    uuid PK
  ap_invoice_number     str(40)   # finance 归口号 AP-YYYY-NNNN, 唯一
  source                str(10)   # 'epms' | 'oa'
  source_invoice_id     uuid      # 源系统发票 id
  source_ref            str(40)   # 源系统展示号(EPMS internal_ref / OA invoice_number)
  vendor_id             uuid      # nullable(OA 可能未识别供应商)
  vendor_name           str(255)  # nullable
  vendor_invoice_number str(100)  # 供应商发票号, nullable
  amount                numeric(15,2)   # 税前
  tax_amount            numeric(15,2) default 0
  total_amount          numeric(15,2)
  paid_amount           numeric(15,2) default 0
  currency              str(10) default 'CAD'
  invoice_date          date      # = Issue Date
  due_date              date      # nullable(OA 可能无), Aging 基准
  status                str(20)   # draft|posted|partially_paid|paid|void (finance 统一态)
  source_status         str(30)   # 透传源系统原始状态原文(展示用), nullable
  po_id                 uuid      # nullable(EPMS 来源填, OA 空) — 指针,非明细
  po_number             str(40)   # nullable
  posted_at             timestamptz nullable
  entity_id             uuid      nullable
  created_at/updated_at timestamptz
  UNIQUE(source, source_invoice_id)   # 幂等 upsert 键
  UNIQUE(ap_invoice_number)
  index: vendor_id, status, due_date, source
```

> 实现注意（[feedback_uniops_mirror_models_match_reality]）：本表是 finance 新建主表（非镜像），模型与迁移列对列一致；先核对再建。

### 5.2 `ap_invoice_tax_lines`（对照 `ar_invoice_tax_lines` + epms `invoice_tax_lines`）

```
ap_invoice_tax_lines
  id              uuid PK
  invoice_id      uuid FK ap_invoices ON DELETE CASCADE
  line_no         int
  tax_code        str(20)   # nullable: OA/未编码税进 uncoded(ITC 例外清单)
  taxable_base    numeric(15,2) default 0
  tax_amount      numeric(15,2) default 0
  recoverable     bool default true   # ITC 可抵扣
  created_at/updated_at
  index: invoice_id
```

## 6. 统一状态机 + upsert 时机

### 6.1 状态映射

| finance AP `status` | 含义 | EPMS 源态 | OA 源态 |
|---|---|---|---|
| `draft` | 已录入，负债未成立 | unmatched / exception | uploaded / reviewed |
| `posted` | 负债成立 → 触发 AP accrual | matched / approved | used（PA 已建） |
| `partially_paid` / `paid` | 付款（finance payment executor 管） | paid | paid |
| `void` | 作废/删除 | (删除) | (作废) |

> exception 在 finance 头记为 `draft`（负债未成立），原始源态写入 `source_status`（如 'exception'），管理界面显示原文以便区分。
> `due_date` 为空（OA 可能无）的发票：aging 归入 `current` 桶（不计逾期），open-items 仍可见——避免无到期日的发票被误判逾期。

### 6.2 upsert 时机（同步调用，幂等键 `(source, source_invoice_id)`）

- **创建** → upsert `draft`（全量进 finance）。
- **状态推进**（EPMS matched/approved、OA used）→ upsert `posted`，`posted` 转入时触发 AP accrual（取代现 `post_invoice`）。
- **编辑/重匹配** → upsert 更新金额/税/状态。
- **付款** → finance payment executor 内部更新 `paid_amount`/`status`（不经源系统）。
- **删除/作废** → upsert `void`。

### 6.3 一致性

upsert **fail-open**：finance 不可用不阻塞源系统录入（沿用现有 `post_invoice` 先例）。源系统侧加 `finance_synced bool`（或 last_synced_at）标志；失败记日志。配 **对账脚本**（finance-api/scripts）扫描源系统未同步项补偿，保证最终一致。自动重试队列为后续非目标。

## 7. finance-api 组件

- `models/ap_invoice.py`：`ApInvoice`、`ApInvoiceTaxLine`（§5）。
- `crud/ap_invoice.py`：
  - `upsert(db, source, source_invoice_id, payload, tax_lines)` — 按幂等键建/更新头+重建税行+状态转换；首次建时生成 `ap_invoice_number`（`_next_number`，对照 ar）。
  - `list(filters: source/vendor_id/status/page)`、`get_by_id`、`set_void`。
  - 状态转换校验（draft→posted→partially_paid/paid；任意→void）。
- `api/v1/ap_invoices.py`（新 router，prefix `/ap/invoices`）：
  - `POST /ap/invoices`（upsert；body 含 source/source_invoice_id/source_ref/vendor/金额/税/日期/status/po 指针/tax_lines）。鉴权：限内部服务调用角色/令牌。
  - `GET /ap/invoices`（list + 过滤，AP 管理界面用）。
  - `GET /ap/invoices/{id}`、`POST /ap/invoices/{id}/void`。
- 改造 `api/v1/ap.py`：`/ap/open-items`、`/ap/aging` 从 `mirrors.Invoice` 切到 `ap_invoices`（status ∈ posted/partially_paid，aging 按 due_date）。
- 改造 `crud/ap_accrual.py`：accrual 触发改为 `ap_invoice` 转 `posted` 时；ITC 从 `ap_invoice_tax_lines`（recoverable）取。`mirrors.Invoice` 迁移期保留，迁移完成后可删（非目标本期不删）。

## 8. 源系统接入

### 8.1 EPMS（epms-api，已有 `finance_client`）
新增 `finance_client.upsert_ap_invoice(invoice, status)`，在以下处调用（fail-open）：
- `crud.invoice.create` 后 → draft。
- `crud.invoice.match` 后 → matched→posted / exception→draft（合并现有 `post_invoice`：posted 即触发 accrual）。
- `update` / `resolve_exception` 后 → 同步金额/状态。
- `delete` → void。

字段映射：`source='epms'`、`source_invoice_id=invoice.id`、`source_ref=internal_ref`、`vendor_*`、`amount/tax_amount/total_amount`、`invoice_date/due_date`、`po_id/po_number`；税行取 epms `invoice_tax_lines`（line_no/tax_code/tax_amount/recoverable，taxable_base 缺省 0）。

### 8.2 OA（expense-api，新增轻量 `finance_client`）
在 `expense_invoices` 生命周期调用：创建/上传→draft；确认 reviewed→draft；used（PA 建立）→posted；作废→void。
字段映射：`source='oa'`、`source_invoice_id=expense_invoice.id`、`source_ref=invoice_number`、`vendor_id/vendor_name`(可空)、`vendor_invoice_number=invoice_number`、`amount=subtotal`、`tax_amount`、`total_amount`、`invoice_date`、`due_date`(可空)、`po_id=null`；OA 无税行 → `ap_invoice_tax_lines` 留空（header tax 仍在 `ap_invoices.tax_amount`，无 tax_code 计入 uncoded/ITC 例外）。

## 9. 存量数据迁移

`finance-api/scripts/backfill_ap_invoices.py`（幂等可重跑）：
- 跨库读 EPMS `invoices`（+ `invoice_tax_lines`）与 OA `expense_invoices`，按 §8 映射 upsert 到 `ap_invoices`（复用 crud.upsert，幂等键避免重复）。
- 状态按 §6.1 映射；已 `paid` 的带 `paid_amount`。
- 先在本地/测试库验证；目标生产库 10.10.50.20（当前 EPMS `invoices` 0 条，低风险）执行前再确认计数。

## 10. 前端（epms 或 portal 的 Finance 区）

> 全部 user-facing 文案英文（[feedback_uniops_ui_english_only]）；页面包在 Portal chrome（[feedback_uniops_portal_page_chrome]）；Decimal 字符串 `Number()` 强转（[feedback_uniops_decimal_as_string]）；调用 finance-api 用绝对 base URL，不走死代理（[feedback_uniops_oa_vite_proxy_dead]）。

- **AP Invoices 管理页**：消费 `GET /ap/invoices`，列表显示 ap_invoice_number / source / vendor / vendor_invoice_number / Issue Date / Due Date / total / status；过滤 source(epms/oa)/vendor/status；行点击可跳源系统详情（按 source+source_invoice_id 构造链接）。
- **AP Aging report 页**：消费 `GET /ap/aging`，按 vendor + currency 展示 current/1-30/31-60/61-90/90+ 桶 + 合计。
- 入口与权限：放 Finance 导航（参照 [project_uniops_portal_sidebar] 的 `view_finance` 等权限键门控；如需新键在权限矩阵加）。

## 11. 测试

- finance（pytest + 本地 DB）：upsert 幂等（同键重复→更新不重复）、状态机转换、open-items/aging 改读 ap_invoices、accrual 在 posted 触发并取 ITC、list 过滤、void。**成功路径必覆盖**。
- EPMS：create/match/delete 触发 upsert（mock finance_client，断言调用载荷与状态映射）。
- OA：创建/used/作废触发 upsert（mock）。
- 迁移脚本：样本回填后头/税/状态/计数正确，重跑幂等。

## 12. 错误处理 / 风险

- upsert fail-open + 日志 + 对账脚本（§6.3）。
- **跨服务一致性**：draft 全量同步依赖每个源系统改全生命周期；遗漏点由对账兜底。
- **生产热重载风险**：dev 容器 `--reload` 连生产库 10.10.50.20（见 [project_uniops_invoice_multi_po_allocation] 警示）；改 finance-api/epms-api 会即时生效于生产，迁移/上线需谨慎。
- 本 spec 偏大（跨 finance+epms+expense+前端+迁移），实现计划会拆成多 task（建议顺序：finance 表+crud+API → accrual/ap 改读 → EPMS 接入 → OA 接入 → 迁移 → 前端），可考虑分计划执行。

## 13. 验收对照（需求 → 设计）

| 需求 | 设计 |
|---|---|
| EPMS+OA 发票都属 AP 模块、归 finance | `ap_invoices`(finance 拥有) + §8 两来源接入 |
| finance 拥有 Invoice 核心 | §5 规范化头+税行，finance 真相源；§6 全量 draft |
| 入口指向 finance 管理 | §10 finance AP 管理页 |
| Aging report（Issue/Due Date）| §5 invoice_date/due_date + §7 /ap/aging 改读 + §10 Aging 页 |
| 未来 Sales→AR | §4 对称；AR 已就位（非本期） |
