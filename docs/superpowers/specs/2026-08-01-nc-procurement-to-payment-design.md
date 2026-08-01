# NC 采购链 → UniOps 付款申请与付款跟踪 设计文档

- **日期**: 2026-08-01
- **分支**: `feature/nc-procurement-to-payment`（基于 `origin/main` = 27c51ca）
- **状态**: 设计定稿，待实现计划

## 1. 背景与目标

公司的原奶、原料、包材采购走用友 NC65（Oracle）的一套完整采购链：

```
采购订单(po_order) → 到货单(po_arriveorder) → 采购入库单(ic_purchasein)
  → 采购发票(po_invoice) → 结算单(po_settlebill) → 供应商付款单(ap_paybill)
```

NC 里这条链**没有付款审批**——从发票开始一路"推式"自动生成下游单据。UniOps（EPMS）里其实早就为"原料/包材"预留了采购类别 `type=1`，但前端一直**禁用**（`ProcurementTypeSelector.tsx` `disabled:true`、`noBudget:true`），原因就是 NC 已有流程。

**目标**：把 NC 的采购订单和到货单读进 UniOps，在 UniOps 里对这类采购做**付款申请 + 审批 + 付款 + 付款跟踪**，全程复用 EPMS 已有的"上传发票 → 三方匹配 → 付款申请(PA) → 付款批次 → QBO/银行对账"管线。

**非目标**：不在 UniOps 里重做采购（无 PR→PO→GR 工作流）、不回写 NC、不导入 NC 的发票/结算/付款单、不建物料操作级主数据、不改动现有 EPMS 采购与付款代码（全部只做加法）。

## 2. 已确认的关键决策（来自与用户的澄清）

| 主题 | 决策 |
|---|---|
| 交接边界 | **发票为准**；发票在 **UniOps 上传**（不读 NC 发票）。NC 继续自己跑，UniOps 对 NC **只读、不回写**。 |
| 匹配对象 | 发票匹配 NC **订单 + 到货**，**主锚到货**（因常有部分到货/供应商超发，实测超发占订单行 27%、部分到货 8%）。 |
| 付款执行/跟踪 | 复用 UniOps 现有 **Payment Batch + payment_execute + QBO/银行对账**。 |
| 范围/切换 | 设**切换日**，只处理 `dbilldate >= 切换日` 的 NC 订单；之前的留在 NC。 |
| 申请粒度 | **一发票一付款申请**。 |
| 供应商映射 | 映射到**已有 UniOps vendor**（`business_partners.erp_id`）。 |
| 上下文深度 | 发票 + 关联订单/到货**只读展示**，供审批人核对。 |
| QC 合格/不合格 | **不考虑**（NC 有 `nelignum`/`nnotelignum` 字段但未使用）。GR 数量只取到货数量 `nastnum`。 |
| PO 单号冲突 | **不冲突**（供应商不同），不做单号隔离，仅加 `nc_source_pk` 溯源列。 |
| 供应商码等价 | ✅ 已确认 Firmus `suppliercode` == NC `bd_supplier.code`，按 code 解析成立。 |
| 付款银行信息 | **不特殊处理**，沿用现有 EPMS 付款时选择银行账户的方式，NC 这批 PA 一视同仁。 |
| type-1 前端开关 | **暂时只对"NC 镜像来源"放开**，不整体启用该采购类别（手工建单仍禁用）。 |
| 双重付款 | ✅ 无风险：**NC 仅记账、不关联任何真实出款动作**；真实打款只在 UniOps。 |

## 3. NC 侧事实（连生产库 10.10.95.67/ORCL 只读实测，2026-08-01）

- 四张核心表行数：`po_order` 1,851 / `po_order_b` 5,283 / `po_arriveorder` 2,405 / `po_arriveorder_b` 6,011。
- **链路 100% 可 join**：`po_arriveorder_b` 物理列 `pk_order`、`pk_order_b`、`pk_arriveorder` 全部非空（6011/6011）；到货行→订单行命中 6011/6011，0 孤儿。`vsourcecode` 恒等于订单号 `vbillcode`（人肉核对兜底）。
- 数据字典**未列**这些系统外键，但物理表**确实存在**（已用 `all_tab_columns` 证实）。
- 到货 vs 订单数量（订单行 5283）：未到货 589 / 部分到货 431 / 刚好 2824 / **超发 1435（27%）**。
- 一张到货单几乎只对一张订单（平均 1.01，最多 3）。
- 状态：`po_order.forderstatus` 3=生效(1680) / 0(158) / 2(13)；`po_arriveorder.fbillstatus` 3=生效(2223) / 0(182)。**只同步 status=3**。
- NC 连接与只读辅助：凭据 `c:/Project/nc65_conn.env`，helper `c:/Project/nc65_introspect/conn.py`（oracledb thin，schema NCSC）。

## 4. 现有 UniOps 管线事实（代码探查所得）

- **PO/GR/发票/付款申请(PA)** 都在 `epms-api`、共享 `epms` 库；**付款批次/付款执行/QBO** 在 `finance-api`（同库读写）。
- 表：`purchase_orders`/`po_line_items`（`epms-api/app/models/po.py`）、`goods_receipts`/`gr_line_items`（`models/gr.py`）、`invoices`（`models/invoice.py`）、`invoice_po_allocations`（`models/invoice_allocation.py`）、`payment_applications`/`pa_line_items`（`models/pa.py`）。
- **采购类别 = PO 上的整型 `type`（1-6）**；`1=Raw Materials/Packaging`（`po.py:18`），前端 `ProcurementTypeSelector.tsx:12` `disabled:true` + `noBudget:true`。GR 从 PO 复制 `procurement_type`；`schemas/gr.py` `PHYSICAL_TYPES={1,2,3,5}`。
- **无通用 `source` 列**在 PO/GR/invoice 上（PMS 导入靠单号约定区分）。`finance` 的 `ap_invoices` 才有 `source`/`source_invoice_id` 的多来源范式。
- **收货闸门**是单一函数 `po_has_three_way_matched_invoice(db, po_id)`（`crud/po.py:526`）：PO 上存在 `status='matched'` 且 `gr_id IS NOT NULL` 的发票即通过；GR 创建时 `_autofill_gr_to_matched_invoices` 会把 `gr_id` 回填到已匹配发票。PA 创建处（`crud/pa.py:231-250`）强制该闸门，可 `receipt_override` + 理由 + 审计列强过。
- **发票上传/匹配**：`POST /invoices`（`invoice_upload` 权限）→ `POST /invoices/{id}/match`（`crud/invoice.py` 3-way，算 `po_total/gr_value/variance`，链 `gr_ids`，容差内置 `matched`）。已有"By total amount"总额匹配模式与上传即自动匹配。
- **Create PA**：`POST` `create_pa`（`api/v1/pa.py:73`，权限 `epms.pa.write`）；`PaCreate` 需 `po_id`、`invoice_ids[]`、`gr_ids[]`、`subtotal`、税/运费/其他费用等。
- **付款**：`finance-api` `crud/payment_execute.py` 是唯一"出款"实现——置 `pa.status='processed'`+`paid_at`、`invoices.status=paid/partially_paid`、回写 `ap_invoices.status=paid`、写 `payment_records`。QBO 是**只读镜像**（不回写"已付"）；付款闭环靠**银行对账**（`api/v1/bank.py`）。
- **PMS 导入先例**（`epms-api/scripts/import_pms/`）：直接写表、状态 crosswalk、绕过审批、按名映射 vendor（未匹配自动建 `PMS-NNNN`）、**不建 GR**（只填 `received_qty`）。是"注入外部单据"的现成范式，但本方案**要建真实 GR**。

## 5. 现有主数据映射（复用）

- **供应商 → vendor**：已有 `erp_suppliers` 镜像（来自 Firmus HTTP 中间件 10.10.95.66，`suppliercode`）+ vendor 表 `business_partners.erp_id`（存 NC 供应商码，如 `0000415`）+ `POST /vendors/import-from-erp` 导入流程 + 解析器 `crud/vendor.py:87 get_by_erp_id(code)`（`_normalize_code` 去前导零）。
  - **解析路径**：NC `pk_supplier` →（NC Oracle join `bd_supplier`）→ `code` → `get_by_erp_id(code)` → `business_partners` 行。
  - **待验证前提**：Firmus 的 `suppliercode` == NC `bd_supplier.code`（Firmus 应为 NC 前置中间件，落地前确认）。
- **物料**：只镜像到 `erp_materials`（键 `erp_part_no`），**无操作级主数据**；EPMS 采购行本就用自由文本 `material_id`。故 NC 物料**不需映射**——镜像时把 NC `bd_material.code`（如 `CR0025`）写进行的 `material_id`、名称写进描述。
- **注意**：`pk_supplier`/`pk_material` 在现有代码中从未存储，一律**按可读 code 解析**（与 finance JV 同步一致）。

## 6. 架构与数据流

```
NC65 Oracle (只读)                        epms-api 新增
  po_order/_b, po_arriveorder/_b  ──►  NC 采购同步服务 (仿 finance nc_sync)
                                         oracledb + NC_* env + nc_purchase_sync_runs
                                         手动触发(system_admin) + 增量水位
        │ 幂等镜像 (键 nc_source_pk)
        ▼
epms 库现有表:
  purchase_orders (type=1, pr_id=NULL, status=approved, source='nc')
  goods_receipts  (由 NC 到货生成 → 喂收货闸门)
        │
        └─── 以下全部现有管线, 零业务逻辑改动 ───
             ① UniOps 上传发票 (upload + OCR)
             ② 三方匹配: 发票 ↔ NC订单 ↔ NC到货 (主锚到货=GR)
             ③ 收货闸门 po_has_three_way_matched_invoice (GR 存在 → 通过)
             ④ Create PA (一发票一申请)
             ⑤ 审批 → finance 付款批次 → payment_execute 标记已付
             ⑥ QBO 镜像 / 银行对账 跟踪
```

## 7. 字段映射

### 7.1 `po_order/_b` → `purchase_orders`/`po_line_items`

| EPMS 字段 | NC 来源 | 备注 |
|---|---|---|
| po_number | `vbillcode` | 不撞号，直用 |
| type | 固定 `1` | 原料/包材 |
| pr_id / pr_number | `NULL` | 无请购来源 |
| status | `approved` | 仅同步 NC `forderstatus=3` |
| vendor_id | `pk_supplier`→code→`get_by_erp_id` | 未命中→跳过+报告 |
| order_date | `dbilldate` | |
| currency | `corigcurrencyid`→code | 快照 |
| 金额合计 | `ntotalorigmny` | 价税合计 |
| place_order_method / _reference | `'nc'` / `vbillcode` | 标识来源 |
| **source（新列）** | `'nc'` | provenance |
| **nc_source_pk（新列）** | `pk_order` | upsert 键 |
| 行 material_id / 描述 | `bd_material.code` / 物料名(+`vvendinventoryname`) | 自由文本 |
| 行 数量 | `nastnum` | |
| 行 含税单价 / 税率 / 金额 | `norigtaxprice` / `ntaxrate` / `norigtaxmny` | |
| 行 计量单位 | `castunitid`→code | 快照 |
| 行 税码 | `ctaxcodeid`→code | 快照 |
| 行 **nc_source_pk（新列）** | `pk_order_b` | |

### 7.2 `po_arriveorder/_b` → `goods_receipts`/`gr_line_items`

| EPMS 字段 | NC 来源 | 备注 |
|---|---|---|
| gr_number | 到货单号 `vbillcode`（DH…） | 跨多订单时按 PO 拆多张 GR |
| po_id / po_number | 经到货行 `pk_order_b`→镜像 PO 行→PO | |
| gr_type / procurement_type | `physical` / `1` | |
| status | `approved` | 仅 NC `fbillstatus=3` |
| receipt_date | `dbilldate` | |
| **source / nc_source_pk（新列）** | `'nc'` / `pk_arriveorder` | |
| 行 material_id | `bd_material.code` | |
| 行 数量 | `nastnum`（到货数量=主锚） | 不做 QC 拆分 |
| 行 关联 po_line | 经 `pk_order_b`→镜像 po_line | |
| 行 **nc_source_pk（新列）** | `pk_arriveorder_b` | |

同步时同步回填对应 `po_line_items.received_qty`（供匹配/闸门用）。

## 8. Provenance 与幂等

- 给 `purchase_orders`、`po_line_items`、`goods_receipts`、`gr_line_items` 各加两个可空列：`source`（`'nc'`）、`nc_source_pk`（NC 对应 pk）。
- 迁移：新迁移，`down_revision` 挂真实链尾（先查 alembic heads，勿按文件名猜）。
- 建 `WHERE source='nc'` 的**部分唯一索引**于 `nc_source_pk`，upsert 以此为键。
- 反复增量同步不重复建单，每张单可溯回 NC pk。

## 9. 同步机制（仿 finance「NC Sync」）

- epms-api 新增 `services/nc_purchase_sync.py`（`oracledb` 直连，`NC_*` env：`NC_HOST/NC_PORT/NC_SERVICE/NC_USER/NC_PASSWORD`；缺失则功能隐藏）。
- 运行表 `nc_purchase_sync_runs`（水位/进度/审计/单飞锁），仿 `nc_sync_runs`。
- `POST /nc-purchase-sync`（system_admin，Incremental / Full Reload 需输确认词）+ `GET /nc-purchase-sync/status` + admin 面板按钮。
- **增量水位取 NC `modifiedtime`**（`char(19)`，无 ts 列），`>=` 水位 + `nc_source_pk` 跳重；这样订单后续新增到货/改动能被重新拾取并更新镜像与 `received_qty`。
- 过滤：`forderstatus=3` 且 `dbilldate >= 切换日`；到货只取属于已同步订单且 `fbillstatus=3` 的。
- 并发防护、心跳陈旧清理、`_mark_terminal` 防翻状态：照搬 finance `nc_sync.py` 范式。
- 依赖：epms-api 需加 `oracledb`；生产 compose 接 `NC_*` 前先验 app server(10.10.50.65)→10.10.95.67:1521 连通。

## 10. 收货闸门与匹配（全复用）

- 因**建了真实 GR**，`po_has_three_way_matched_invoice` 天然满足：发票匹配后 `_autofill_gr_to_matched_invoices` 挂 `gr_id` → 闸门通过 → 可 Create PA。
- **主锚到货**：匹配复用现有逻辑，锚定 GR（=NC 到货）。超发（GR 量 > 订单量）、部分到货由现有容差与"By total amount"模式处理。

## 11. 切换日

- 一个配置项（建议 company_config 或 sync 参数）。只镜像 `dbilldate >= 切换日` 的订单及其到货；之前的留 NC 不管。

## 12. 边界情况

1. **一张到货跨多张订单**（≤3）：按 `pk_order` 拆成每 PO 一张 GR。
2. **订单尚无到货**（open PO，12%）：PO 照常镜像，无 GR → 闸门挡住不能建 PA，等到货同步进来放行（或 `receipt_override` 带审计强过）。
3. **同步后 NC 又改**：增量按 `modifiedtime` 重新拾取更新镜像；但**若该 PO/GR 已被 UniOps 发票/PA 消费**，不做破坏性覆盖，只标记冲突进报告，人工处理。
4. **双重付款防护**：✅ 已确认无风险——**NC 仅记账、其 `ap_paybill` 不关联任何真实出款动作**，真实打款只在 UniOps。无需额外代码或流程约定。
5. **供应商未导入 vendor**：跳过该 PO + 进报告，管理员用 `import-from-erp` 补导后重跑。

## 13. 明确不做（范围边界）

- 不在 UniOps 做 type-1 的 PR→PO→GR 采购工作流（PO/GR 只来自 NC 镜像）。
- 不回写 NC。
- 不导入 NC 发票/结算单/付款单。
- 不建物料操作级主数据。
- 不同步银行信息（付款沿用现有 EPMS 选择银行账户的方式）。
- 不整体启用 type-1 采购类别——前端仅对"NC 镜像来源"放开只读展示，手工建 type-1 单仍禁用。
- 不碰现有 EPMS 采购（其他类别）与发票/匹配/PA/付款代码——只做加法。

## 14. 开放项

已确认（2026-08-01，用户）：① Firmus `suppliercode` == NC `bd_supplier.code`（按 code 解析成立）；② 付款银行信息沿用现有 EPMS 选择银行账户方式；③ 前端 type-1 仅对 NC 镜像来源放开；④ 双重付款无风险（NC 仅记账、不真实出款）。

落地阶段仍需处理的技术项：

1. NC `forderstatus` 的 0/2、`fbillstatus` 的 0 精确含义（自由态/关闭/变更中）；本设计只取 3，实现时抽查确认 0/2 不含"应付而漏"的活跃单。
2. epms-api 引入 `oracledb` 依赖 + 生产网络连通性（app server 10.10.50.65 → NC 10.10.95.67:1521）落地前验证。

## 15. 实现工作量概览（供计划阶段拆分）

1. 迁移：4 张表加 `source`/`nc_source_pk` + 部分唯一索引。
2. `services/nc_purchase_sync.py`：NC 读取（pk→code 解析）+ transform + 幂等 upsert PO/GR + 回填 received_qty。
3. `nc_purchase_sync_runs` 模型 + API（trigger/status）+ 并发/水位/审计。
4. admin 面板"NC 采购同步"按钮（portal AdminPanel）。
5. 前端：发票/PA/PO 详情对 NC 来源的只读上下文展示 + type-1 放开。
6. 测试：同步幂等、pk→code 解析、跨多订单拆 GR、闸门通过、已消费单据不覆盖、切换日过滤。
