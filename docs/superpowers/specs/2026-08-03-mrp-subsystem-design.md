# MRP 子系统设计（UniOps 单工厂一期）

日期：2026-08-03（V2.0 修订 2026-08-04）｜ 状态：**V2.0** ｜ 依据：`奶粉企业MRP子系统PRD_单工厂一期.md` V1.0 + UniOps 现有架构 + 用户业务澄清

> **V2.0 是当前有效版本。** Phase 0 已实施完成（见第五部分）；**第六部分是 Phase 1 的重新设计**——经与业务方确认，MRP 之前还有一层此前未纳入范围的「产能受限主生产计划（MPS）」，那才是 MRP 的真正输入。V1.1–V1.8 修订记录保留在下方作为决策溯源。

**V1.1 修订（按用户拍板）**：① BOM 从 ERP 数据库直连拉取；② WMS 库存从 WMS 数据库直连拉取；③ **MRP 只出采购建议、不建采购单**——原料采购在 NC 执行，UniOps 从 NC 拉采购单做付款（复用已上线的 NC 采购镜像链路）。
**V1.2 修订**：经核实 `nc_purchase_sync` 代码（main），NC 采购镜像**已覆盖全部已审批 PO**（原奶+原料/包材），无需扩展镜像范围。
**V1.3 修订（用户确认）**：**物料编码全企业一套，NC/ERP/WMS 各系统一致**（NC 采购镜像映射时已直接读 NC 物料编码）——编码对齐核对项取消，`materials.code` 即全局物料主键，各镜像按 code 直接关联。
**V1.4 修订（用户确认）**：ERP 对接方式复用系统已保存配置。ERP 现存两条已建成通道：① HTTP 数据接口（mdm-api ErpClient，`ERP_BASE_URL=10.10.95.66`）；② **NC65 Oracle 只读直连**（`NC65_*` env，10.10.95.67:1521/ORCL，nc_purchase_sync/finance 已验证连通）。"BOM 从 ERP db 拉取"落地为**直连 NC65 Oracle**，复用已保存连接，无需新申请账号；Phase 0 仅剩 BOM 具体表名调研（NC65 制造模块 BOM 表）。
**V1.5 修订（用户澄清术语）**：**不存在独立的"Firmus"系统，NC 就是 ERP**。"Firmus"是集成接口文档与 mdm-api 代码注释里沿用接口路径（`/firmusData/...`）的叫法——10.10.95.66 的 HTTP 数据接口即 NC ERP 的对外数据服务。上游主数据源唯一：NC ERP（HTTP 接口通道 + Oracle 直连通道）。
**V1.6 修订（WMS 接入信息落地）**：WMS 数据库 = **Oracle `10.10.95.43:1521/wmsdb`，用户 FEIHE_WMS**（凭据存 `c:/Project/wms_conn.env`，不入设计文档/git，仿 nc65_conn.env 惯例）。实测：① dev 机 TCP 1521 连通 ✅；② **服务端为老版本 Oracle（≤11g，thin 模式报 DPY-3010 不支持）→ mrp-api 的 WMS reader 必须用 python-oracledb thick 模式 + Oracle Instant Client 19c（Dockerfile 需打入 instantclient，19c 客户端兼容 11g 服务端；NC65 直连不受影响仍走 thin）**。
**V1.7 修订（WMS 表结构调研完成 ✅）**：凭据修正后已完成只读调研，**WMS = 富勒 Flux WMS**，Phase 0 的 WMS 部分基本完成，结果见附录 A。库存状态映射可直接预填：`QLT_STS 02=Release→available / 01=Block→hold / 04=Under Inspection→hold / 过期由失效日期派生`。
**V1.8 修订（用户补充 BOM 级联结构）**：**一个产品的 BOM 是级联三层：制粉 BOM → 干混 BOM（可选）→ 包装 BOM**。检索成品只能看到包装 BOM；包装 BOM 的组件中含半成品粉，半成品粉再关联制粉 BOM 或干混 BOM。设计影响：① 规范化 `boms` 表增加 `bom_type` 字段（milling 制粉 / drymix 干混 / packaging 包装，NC 侧区分方式由 Phase 0 调研确认）；② 引擎展开必须**按组件递归逐层展开**（成品→包装 BOM→半成品粉→干混/制粉 BOM→原料），与既有 LLC 逐层展开设计一致，禁止只展开成品一层；③ `GET /boms/effective` 按"产品物料 code"查单层，多层链路由引擎/前端逐层跟随组件递归查询。

---

## 第一部分：PRD 评审结论

### 总体判断

**PRD 可落地，质量较高。** 边界克制（WMS 只读、不做 MES/APS、单工厂）、核心运算公式明确（可用库存/毛需求/净需求/PAB）、行业规则具体、验收标准场景化。性能指标（增量 15 分钟、全量 60 分钟）对单工厂规模非常宽松，FastAPI + Postgres 预计分钟级即可完成，无技术风险。

### A. 原阻塞性缺口 → 已拍板解决

| # | 缺口 | 拍板结论 |
| --- | --- | --- |
| A1 | UniOps 无 BOM/配方主数据，NC ERP 现有 11 个 HTTP 数据接口里也没有 BOM 接口 | **BOM 从 NC65 Oracle 直连拉取**（只读镜像，复用 `NC65_*` 已保存连接）。Phase 0 需完成 BOM 相关表结构调研与逐列核对 |
| A2 | 现有集成清单里没有任何 WMS 库存接口 | **WMS 库存从 WMS 数据库直连拉取**（只读镜像）。Phase 0 需完成 WMS 库表结构调研与逐列核对；PRD 的"30 分钟接口拉取"落地为定时 DB 同步作业 |

> 直连外部库的纪律：只读账号、独立连接配置（勿混入 `POSTGRES_*`）、镜像模型建表前逐列核对物理表（已三踩的教训）、同步失败标记库存/主数据时效风险并出异常消息，不做任何回写。

### B. 采购衔接 → 已拍板：MRP 不建单

原评审指出"建议转 EPMS PR 缺少部门/预算映射"。用户拍板：**MRP 只输出采购建议，不直接创建采购单**。原料采购在 NC 中执行，UniOps 既有链路已从 NC 拉取采购单做付款（NC 采购镜像 → EPMS 付款，已上线）。因此：

- PRD §5.4"支持将建议转为采购申请，并回写申请号和状态"**一期不实现转单**，改为：建议确认 → 导出/推送给采购员 → 采购员在 NC 下单。
- 闭环方式：MRP 从 **NC 采购镜像**读取开口 PO 作为在途供给；Phase 2 增加"建议 ↔ NC PO 对账匹配"，PO 覆盖后建议自动关闭。
- **镜像范围已核实充足**（`epms-api/app/services/nc_purchase_sync/`，main）：同步拉取 NCSC.PO_ORDER 全部已审批 PO（forderstatus=3，含表头/行/到货/物料码）。交易类型 `21-Cxx-CRM01`=原料/包材采购（进付款流，status issued/closed）；其余类型=原奶采购（status `nc_milk` 只读，不进付款清单但**同样已镜像**）。MRP 在途供给两类都取：`开口量 = 订单行数量 − 到货累计(nastnum)`，剔除 closed/最终关闭单。
- 附带效果：不涉及 EPMS 预算闸门与部门映射，原设计中的 `requisition_dept_id` 参数与 `mrp.proposal.convert` 权限取消。
- **物料编码已确认全系统一致**（NC/ERP/WMS 同一套编码，NC 镜像映射时已直接读 NC 物料编码）：在途供给、库存镜像、BOM 展开均按 `materials.code` 直接关联，无需映射表。

### C. 需求来源（维持原结论）

UniOps 没有销售/订单模块。一期需求形态为 **Excel 导入 + 手工维护**为主，接口自动化列为二期；PRD §8.2 的"每小时接口"改为"以导入批次为准"。

### D. PRD 内部矛盾/含糊点（建议修订 PRD）

1. **"实时影响计划"（FR-007）vs 周期拉取矛盾** → 改为"准实时（同步周期内）"，解封/放行由 WMS 同步时的状态 diff 触发增量重算。
2. **库存状态字典语义重叠**（unrestricted 与"放行"）→ 以 WMS 实际状态枚举为源，做可配置**映射表**：映射到 MRP 内部 `available / hold / rejected / expired` 四类计划语义。
3. **Demand 主键缺行号**：`factory+item+demand_date+source_doc` 需加 `source_line`。
4. **保质期"按客户等级"超出一期能力**：一期降为 `物料 + 需求类型` 两维门槛，客户等级留二期。
5. **PAB 口径**：供需明细页同时展示"含/不含安全库存扣减"两条 PAB，避免误读。
6. **需求冲销策略收敛**：一期只做 `累加`、`订单冲销同期间预测` 两种；跨期间 consumption window 留二期。
7. **日历简化**：一期做"工厂节假日日历 + 周末规则"；供应商到货日历后置二期。

### E. 建议裁剪

- FR-009 模拟试算、FR-012 接口监控页 → 二期（一期用同步状态表 + 异常消息覆盖）。
- §5.9 计划准确性报表依赖历史数据积累 → 上线 3 个月后再做。
- 替代料一期固定为"预警不自动替换"（`suggest` 模式）。
- 计划驾驶舱与计划工作台一期合并为一页（工作台顶部 KPI 卡片）。

---

## 第二部分：架构方案权衡

| 方案 | 内容 | 结论 |
| --- | --- | --- |
| **A（推荐）：新建 mrp-api + mrp 前端，主数据镜像/扩展进 mdm-api** | mrp-api(8011) 管计划域（需求/供给/运算/建议/异常/计划参数/WMS 镜像）；mdm-api 扩展制造主数据（materials 升格 + ERP BOM 镜像）；新 mrp/ Vite 前端(5179) | **采用**。符合每域一服务惯例；BOM 等主数据未来（成本核算等）会被复用，归口 mdm 符合"主数据统一归口" |
| B：并入 epms-api | MRP 作为 EPMS 子模块 | 否。EPMS 是采购付款执行域，MRP 是计划域；epms-api 已很大，发布/迁移/权限纠缠 |
| C：全部自包含在 mrp-api（含物料/BOM） | 主数据也放 mrp-api | 否。违背主数据统一归口，未来要二次搬迁 |

---

## 第三部分：子系统设计

### 1. 服务拓扑

```
NC ERP 数据接口(10.10.95.66) ──HTTP同步(物料/单位/供应商,现有)──> mdm-api(8002)
NC65 Oracle(10.10.95.67,复用NC65_*只读连接) ──直连拉取(BOM)──> mdm-api
WMS Oracle(10.10.95.43:1521/wmsdb,≤11g须thick模式) ──直连只读拉取(库存批次/状态/保质期)──> mrp-api(8011)
NC65(Oracle) ──采购镜像(已上线,覆盖全部PO:原奶+原料/包材)──> epms-api(8000) ──HTTP──> mrp-api(在途PO快照)
需求(Excel导入/手工) ────────────────────────────────────> mrp-api
mrp-api ──HTTP(转发JWT)──> mdm-api(物料/BOM/供应参数)
mrp-api ──(Phase 2)参数审批──> approval-api(8003)
mrp/ 前端(5179) ──> mrp-api；portal navConfig 注册入口
采购建议 ──导出/通知──> 采购员在 NC 下单 ──> NC镜像回流为在途 → 闭环
```

- **mrp-api**：FastAPI async + SQLAlchemy 2.0 + asyncpg，共享 epms 库，`version_table="alembic_version_mrp"`，路由前缀 `/api/v1`，端口 8011。模型用 `UUIDPrimaryKey + TimestampMixin`。构建上下文用仓库根（挂 `packages/authz`）。
- UniOps 服务间一律 **HTTP + 转发 Bearer token**（MdmClient 模式）；对 ERP/WMS/NC 等外部库为**只读直连镜像**（nc_* 先例）。批量运算前先把物料/BOM/供应参数/在途 PO 拉成本地快照，运算不依赖在线调用。
- 环境变量：`MDM_API_URL`、`EPMS_API_URL`、`IDENTITY_API_URL`、`POSTGRES_*`（`POSTGRES_PASSWORD: ${DB_PASSWORD}` 必须设）；新增 `WMS_*`（HOST=10.10.95.43/PORT=1521/SERVICE=wmsdb/USER/PASSWORD，值见 `c:/Project/wms_conn.env`）；BOM 拉取复用 `NC65_*` 连接（compose 已有 anchor，注入 mdm-api + 装 oracledb）。⚠️ mrp-api 镜像需打入 Oracle Instant Client 19c（WMS 服务端 ≤11g，thick 模式必需）。

### 2. 主数据落位（mdm-api 扩展，Phase 0）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `materials` | code(唯一), name, spec, item_type(raw/aux/packaging/semi/finished), base_uom, shelf_life_months, procurement_type, product_family, factory_code, erp_id, is_active | 正式制造物料主数据，由现有 `erp_material` HTTP 镜像**升格同步**（ERP 为源，治理字段本地可编辑），按 erp_id 去重 |
| `erp_bom_*`（表名以 NC65 实际表定） | 忠于 NC65 物理表逐列镜像 | **BOM 原始镜像**（只读，直连 NC65 Oracle 定时拉取，复用 `NC65_*` 连接 + oracledb，仿 nc_purchase_sync 模式；含版本/生效日期/用量/损耗/收率/替代料等原始字段） |
| `boms` / `bom_lines` / `bom_substitutes` | product_material_id, **bom_type(milling/drymix/packaging)**, version, status, effective_from/to, yield_rate; line: component_material_id, qty_per, uom, scrap_rate; substitute: priority, mode(suggest) | **规范化 BOM**，由 erp_bom_* 转换同步而来（版本语义统一、物料码对齐 materials）。**BOM 级联三层**：成品挂包装 BOM（组件=半成品粉+包材），半成品粉挂干混 BOM（可选）/制粉 BOM——引擎按组件递归逐层展开。运算只吃规范化表；**不提供本地编辑**（ERP 为唯一权威），Portal 出只读浏览页 |
| `material_suppliers` | material_id, partner_code, lead_time_days, moq, order_multiple, is_primary | 供应参数。若 ERP 库有对应数据则镜像，缺失字段（如提前期）本地补录 |
| `uom_conversions` | from_uom, to_uom, rate | 单位转换，ERP `unitTranf` 接口已有，补镜像 + 查询端点 |

- 消费方式：mrp-api 通过 `GET /mdm/v1/materials`、`/boms/effective?date=`、`/material-suppliers`、`/uom-conversions` 拉取（新增端点，转发 token）。
- 包装转换不单独建表：**已经用户确认** ERP BOM 即级联组织——成品的包装 BOM 组件行包含半成品粉+罐+勺+膜+纸箱，半成品粉再挂干混/制粉 BOM，一套层级展开模型统一表达，无需本地补建包装层。

### 3. mrp-api 数据模型

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `mrp_demands` | factory, material_code, demand_date, qty, uom, demand_type(forecast/order/mps/manual), source_doc, source_line, version, freeze_flag, import_batch_id, status | 毛需求。Excel 导入（openpyxl 先例）+ 手工维护；重导入生成新 version 旧版软失效；冻结需求不被导入覆盖 |
| `mrp_supplies` | factory, material_code, supply_date, qty, supply_type(nc_po_open/manual_plan), source_doc, status | 供给。`nc_po_open`=运算前经 epms-api 拉取的 NC 镜像开口 PO 快照；`manual_plan`=人工确认内部供给计划 |
| `wms_inventory_lots` | warehouse_id, material_code(=SKU), lot_no(=LOTNUM), qty, qty_allocated, qty_onhold, wms_status(=LOTATT08), mapped_status(available/hold/expired), production_date(LOTATT01), expiry_date(LOTATT02), inbound_date(LOTATT03), supplier_batch(LOTATT05), supplier_code(LOTATT13), source_doc(LOTATT14), synced_at, sync_batch_id | WMS 库存镜像，**直连 Flux WMS Oracle 定时拉取**（`INV_LOT` join `INV_LOT_ATT`，默认 30 分钟，水位=EDITTIME）。源表结构已实测核对（附录 A）；同步失败置时效风险异常 |
| `mrp_status_mapping` | wms_status → mapped_status, allow_reserve_flag | 库存状态映射字典（评审 D2），管理员可配 |
| `mrp_planning_params` | material_code, factory, version, status, lot_size_rule(lot_for_lot/fixed/min_max/period), fixed_lot/min_lot/max_lot/order_multiple, safety_stock, lead_time_override, qc_buffer_days, fence_days, min_shelf_life_days, planner_group | 计划参数，版本化；变更记审计，Phase 2 接审批 |
| `mrp_calendars` | factory, date, is_workday | 节假日日历 |
| `mrp_runs` | run_no(前缀 MRPRUN), scope_type(full/planner_group/material), scope_ref, status, started/finished_at, triggered_by, stats(jsonb), log | 运算批次；`pg_advisory_lock` 防并发；单号用 epms `_numbering.next_number()` 模式 |
| `mrp_pab` | run_id, material_code, bucket_date, gross_demand, scheduled_receipts, on_hand_start, safety_stock, projected_available, net_requirement | 逐期间供需平衡（PRD 验收硬要求） |
| `mrp_proposals` | proposal_no(前缀 MRP-P), run_id, proposal_type(purchase/internal_supply), material_code, theoretical_qty, scrap_qty, total_qty, uom, due_date, start_date, suggested_partner_code, bom_version, risk_flags(jsonb), status(open/confirmed/exported/covered/ignored/closed), locked_flag, matched_nc_po(jsonb), source_demands(jsonb) | 计划建议。**无 pr_id/转单字段**；`covered`=被 NC PO 对账覆盖（Phase 2 自动匹配），`exported`=已导出/通知采购员 |
| `mrp_exceptions` | exception_no(前缀 MRP-E), run_id, exception_type, severity, material_code, object_ref, message, suggestion, status(open/ignored/closed), handled_by/at, comment | 异常消息，可关闭/忽略/批注/重开，留审计 |
| `mrp_sync_state` | source(demand/wms/erp_bom/nc_po), last_success_at, last_error, watermark | 同步时效状态（一期的"接口监控"） |
| `mrp_audit_logs` | actor, action, object_type/id, before/after(jsonb) | 参数改动、建议锁定、异常忽略、导出留痕 |

### 4. 运算引擎

- **触发**：手动（运行控制台）+ 每日定时批（后台 asyncio 循环任务，仿 VMS 每日通知调度）+ WMS 同步状态 diff 触发的增量重算。
- **流程**（单 run 内）：
  1. 快照：物料/BOM/供应参数（mdm HTTP）、NC 开口 PO（epms HTTP）、需求有效版本、WMS 库存镜像、计划参数生效版本 → run 级快照，保证可复算。
  2. 低级码（LLC）：按 BOM 图算层级，自上而下展开（成品 → 半成品粉/包材 → 原辅料），展开量按 `qty_per × (1+scrap_rate) ÷ yield_rate` 修正，跨单位走 `uom_conversions`。
  3. 逐物料按日 bucket 铺 PAB：`可用库存 = mapped_status='available' 且剩余保质期≥门槛的批次量 + 在途 + 人工供给计划 − 已分配 − 安全库存`；`净需求 = max(0, 毛需求 − 可用 − 期内供给)`。
  4. 批量策略修正 → 建议数量；`建议日期 = 需求日 − 前置期 − 质检缓冲`，按日历顺延；围栏内只出异常不改建议。
  5. 写 proposals + exceptions；diff 上一 run 生成"提前/延后/取消/数量变化"异常。
- 性能：单工厂全量预计 <5 分钟，远低于 PRD 60 分钟门槛，无需独立 worker。

### 5. 采购闭环（不建单模式）

1. **建议输出**：采购建议带建议供应商/数量（理论+损耗）/建议下单日/到货日/风险标签；计划员确认后**导出 Excel / 邮件通知采购员**（角色池共享邮箱模式），采购员在 **NC** 下单。
2. **在途回流**：NC 采购镜像（现有链路，已覆盖全部 PO）→ epms-api 新增开口 PO 查询端点（`开口量 = 行订单量 − 到货累计`，含 `nc_milk` 状态单，剔除 closed）→ mrp-api 运算前快照为 `nc_po_open` 供给。
3. **对账关闭（Phase 2）**：按物料+数量+日期容差自动匹配"已确认建议 ↔ NC PO"，命中后建议置 `covered`；长期未覆盖的已确认建议出"建议未执行"异常。
4. MRP 不向 NC / WMS / ERP 写任何数据。

### 6. 审批流落位

- **一期**：不接 approval-api。计划参数变更用权限门禁 + `mrp_audit_logs`；建议确认/导出是操作不是审批。
- **Phase 2**：参数变更注册进 approval-api（`_DOC_META` + `company_config.workflow_defs` 加 `mrp_param` doc_type + 只读镜像模型），任务落共享 `tasks` 表，通知走 `dispatch_task_notification`（`_task_link()` 加 `MRP_URL` 映射）。BOM 因 ERP 为权威源，无本地审批需求。

### 7. 权限落位

- identity-api `permission_defs` 增 `module="mrp"` 键（迁移 + seed_authz 更新）：

| key | 用途 |
| --- | --- |
| `mrp.demand.write` | 需求导入/维护/冻结 |
| `mrp.run.execute` | 发起/停止运算 |
| `mrp.proposal.confirm` | 建议确认/锁定/忽略 |
| `mrp.proposal.export` | 建议导出/通知采购员 |
| `mrp.exception.handle` | 异常关闭/忽略/批注 |
| `mrp.param.write` | 计划参数/状态映射/日历维护 |
| `mrp.report.view` | 报表/供需明细/BOM 浏览查看 |

- 新角色建议：`mrp_planner`（计划员）、`mrp_viewer`（采购/生产协同/仓储查看）。授权在 portal Access Control 矩阵勾选，**不硬编码角色**；端点（含读端点）全部 `require_permission()` 门禁。
- 数据范围：单工厂一期不做工厂级 scope；计划员组做筛选器默认值，不做硬隔离。
- 前端：portal `navConfig` 加 MRP 入口（`anyPermission`）+ `resolveNavHref` 分支。

### 8. 前端（mrp/ Vite app，端口 5179）

- React + TS + Tailwind + `@uniops/shell`（多页签/共享 UI/tokens），UI 样式对照 EPMS 模板，页面包 chrome，统一 StatusBadge，浮层 createPortal，Decimal 字段 `Number()`，**user-facing 文案全英文**。
- 一期页面（P0）：
  1. **Planning Workbench**（工作台+KPI 卡片兼驾驶舱）：建议/异常/风险聚合，筛选+批量确认/导出/忽略+drill-down。
  2. **MRP Run Console**：发起运算、run 列表、日志、同步状态（兼接口监控）。
  3. **Material Supply/Demand Detail**：逐期间 PAB 双口径、需求来源、批次明细（FEFO 视图）、BOM 展开路径。
  4. **Purchase Proposals**：确认/调整/导出 Excel/通知采购员、交期风险标签、（Phase 2）NC PO 覆盖状态。
  5. **Internal Supply References**：配方版本/批量/可供给日、按产品族聚合、导出/通知。
  6. **Exception Center**：分级、处理、审计。
  7. **Demand Management**：Excel 导入向导（模板下载/上传/校验报告）、版本、冻结。
  8. **Reports**：短缺清单、库存健康（库龄/到期/封存占比）、建议执行率。
  - P1（Phase 2）：Parameter Maintenance（含审批）、模拟对比。
- 接线清单：compose 两份加 `mrp-api`/`mrp-web`；**全部服务** `ALLOWED_ORIGINS` 加 5179/新域名；`mrp/Dockerfile` 的 `VITE_*` 必须 `ARG`+`ENV` 成对；Caddyfile 加 `mrp.` + `mrp-api.` 子域；`migrate-prod.sh` 加 mrp-api；发布镜像 15 → 17，遵循全量同 sha 发布纪律（R1–R5）。

### 9. 阶段划分

| 阶段 | 内容 | 出口标准 |
| --- | --- | --- |
| **Phase 0 主数据与镜像地基** | NC65 BOM 表结构调研（连接已就绪）→ erp_bom_* 镜像 + boms 规范化同步；materials 升格同步；WMS 库连通 + 表结构调研 → wms_inventory_lots 镜像 + 状态映射；需求 Excel 模板定稿 | 成品→包材→原料 BOM 能按日期取版浏览；库存快照进镜像表且时效可见 |
| **Phase 1 计划闭环 MVP** | mrp-api 骨架 + 权限 + 需求导入 + 运算引擎 + PAB + 采购建议（导出/通知）+ 异常中心 + 工作台/运行控制台/供需明细前端 | 真实一个月数据回放：需求进 → 建议出 → NC 下单 → 在途回流，闭环走通 |
| **Phase 2 行业规则深化** | 内部供给参考页 + 保质期门槛/FEFO + 替代料提示 + 报表中心 + 建议↔NC PO 对账 + 参数审批 + 增量重算触发 | PRD §11 验收场景全部通过 |
| **Phase 3（二期）** | 模拟试算、接口监控页、需求接口自动化、供应商日历、客户等级保质期规则、计划准确性报表 | — |

### 10. 测试与质量门禁

- mrp-api pytest 走本地 docker `uniops_postgres` 独立测试库（`mrp_test`，conftest 覆盖 `POSTGRES_*`，防宿主 .env 直连生产库），与 epms 套件不并发跑；ERP/WMS 直连在测试中以 fixture 数据替身。
- 引擎核心（LLC/展开/批量策略/日历顺延/PAB）写纯函数单测；PRD §11 的 8 个验收场景各一条集成测试。
- 前端 tsc 门禁：新 app 以 0 errors 基线起步。
- 验证以正面证据为准（对基线错误计数，不以"无输出"当通过）。

---

## 第四部分：待用户拍板的问题（V1.1 更新）

1. **建议交付形态**：采购建议给采购员的形式——导出 Excel、共享邮箱邮件通知、还是两者都要？
2. **需求侧责任人**：销售预测/主生产计划由谁、以什么频率提供 Excel？模板定稿后需对方确认。
3. **角色划分**：`mrp_planner` / `mrp_viewer` 是否符合岗位实际？计划员组怎么划分？
4. **部署形态确认**：mrp-api=8011、前端=5179、域名 `mrp.canadaroyalmilk.com` / `mrp-api.canadaroyalmilk.com`。

（已解决：① NC 就是 ERP，无独立"Firmus"系统——BOM 直连 NC65 Oracle（复用 `NC65_*`），物料主数据走既有 NC ERP HTTP 数据接口同步；② WMS 接入已打通并完成表结构调研，见附录 A。）

---

## 附录 A：WMS 库表调研结果（2026-08-03 只读实测，Phase 0-WMS 已基本完成）

- **系统识别**：富勒 **Flux WMS**，Oracle ≤11g（thick 模式必需），schema `FEIHE_WMS`（1081 表）。单仓：`BSM_WAREHOUSE` 仅 `CANADA`；组织 `ORGANIZATIONID='FEIHE'`；库存货主唯一 `CUSTOMERID='10024'`。
- **镜像源表**（`wms_inventory_lots` 的取数基础）：
  - `INV_LOT`（批次库存汇总，约 3.5k 活跃行）：`WAREHOUSEID, LOTNUM, SKU, QTY, QTYALLOCATED, QTYPREALLOCATED, QTYONHOLD, EDITTIME`
  - `INV_LOT_ATT`（批次属性，join 键 `ORGANIZATIONID+LOTNUM+CUSTOMERID+SKU`）：**LOTATT01=生产日期、LOTATT02=失效日期（'YYYY-MM-DD' 字符串）、LOTATT03=入库日期、LOTATT04=质检批次、LOTATT05=供应商批次、LOTATT06=生产厂商、LOTATT08=质量状态（QLT_STS）、LOTATT13=供应商、LOTATT14=来源单号**，另有 `RECEIVINGTIME`（属性模板=Canada 仓 `FHCABCC_E`，英文标签）
  - `INV_LOT_LOC_ID`（批次×库位明细，含 `LOCATIONID/QCSTATUS`）——库位/FEFO 明细视图需要时用
  - `BAS_SKU`（2461 行）：`SKU, SKUDESCR1/2`；SHELFLIFE 字段基本未维护 → **保质期月数以 NC 物料主数据 `exp` 为准，失效日期以批次 LOTATT02 为准**
- **质量状态字典**（`BSM_CODE`/`BSM_CODE_ML`，CODETYPE=`QLT_STS`）：`01=Block(禁用)`、`02=Release(放行)`、`04=Under Inspection(待检)`。→ `mrp_status_mapping` 预填：02→available；01→hold；04→hold；expired 由 `LOTATT02 < today` 派生；`QTYONHOLD` 部分单独扣减。
- **可用量口径（一期）**：`available = QTY − QTYONHOLD`（仅 status=02 且未过期、剩余保质期≥门槛的批次）。`QTYALLOCATED`（WMS 出库分配量）是否扣减列为计划参数（默认扣减——已分配即将消耗）。
- **增量水位**：`INV_LOT.EDITTIME` / `INV_LOT_ATT.EDITTIME`；同步默认 30 分钟。
- **SKU 编码样本**：CF0086 / CP0100 / CM0040 / M0900 / S0093 等，与 NC 物料码同套（用户确认全企业一致；Phase 0 首次同步时抽样比对 NC `BD_MATERIAL.code` 作核验）。
- 凭据：`c:/Project/wms_conn.env`（WMS_HOST/PORT/SERVICE/USER/PASSWORD）。调研脚本模式存 scratchpad `wms_survey.py`/`wms_columns.py`（init_oracle_client thick + user_tab_columns + 抽样）。

---

*本设计文档未提交 git（commit 需用户同意）。评审通过后进入 writing-plans 出实施计划（Phase 0 先行）。*

---

## 第五部分：Phase 0 实施结果（已完成 2026-08-04）

分支 `feature/mrp-phase0-foundations`（worktree `c:/Project/uniops-mrp-phase0`），19 commits，**未合并未 push**。

交付：mdm-api 迁移 0007–0013（materials 主数据 / erp_material 扩 exp+product_family / uom_conversions / NC BOM 三表原始镜像 / 规范化 boms+bom_lines+bom_substitutes + `/boms/effective` / material_suppliers + 主供应商偏唯一索引）；**新服务 mrp-api:8011**（`alembic_version_mrp`，Dockerfile 打入 Instant Client 19c 走 thick 模式，`wms_inventory_lots` + `mrp_status_mapping` + `mrp_sync_state`）；identity-api 8 个 mrp 权限键。

真实数据验证：BOM 三层级联 CF0092(包装 v1.3)→CW0001(干混 v1.1)→CS0026(制粉 v1.6)→16 条 CR 原料；WMS 镜像 3532 批次（available 2708 / hold 525 / expired 299）；materials 2567 条。测试 mdm 72 / mrp 14 全通过。

上生产必做：`migrate-prod.sh` 已含 mrp-api；**须手动跑 `seed_authz`**（DEPLOYMENT.md 已记，注意重跑会复活管理员取消过的授权）；发布镜像数 15→16。

**Phase 0 补丁（2026-08-04 实测发现，随分支修复）**：① `bom_type` 前缀映射补 S→packaging（S 是当前成品编码，CF 是早期编码，31 张被误判 unknown）；② `bom_lines.uom` 存的是 NC `pk_measdoc` 裸主键，reader 须补 `BD_MEASDOC` 映射；③ 补辅单位量（NC `NASSITEMNUM`）——S 成品主单位 KG、辅单位 PIECES，BOM 两个单位的值都有；④ `EA` 与 `PIECES` 语义相同（个），归一为 `EA`；⑤ CM（标准化奶）两年前已弃用，母件 BOM 55 张排除、CM 组件行计入 warnings。

---

## 第六部分：Phase 1 重新设计（V2.0 核心）

### 6.1 为什么重新设计

原设计把 MRP 的输入简化为「需求导入」。与业务方确认后，真实链路在 MRP 之前还有一整层：**销售预测 → 扣库存得净需求 → 受产能约束编制生产计划（含提前生产）→ 生产计划才是 MRP 的输入**。这一层不是 PRD 排除的「详细有限产能排程」（排到工序/设备/班次），而是月度粗产能校验 + 提前生产（RCCP + MPS），**必须做，否则 MRP 没有输入**。

另经实测确认（`demand-source-survey.md`）：**全厂没有任何系统持有前瞻需求**——NC 的预测/MPS 表全部 0 行，销售订单与生产订单都无未来日期，WMS 出库单 1575 张里仅 1 张未来日期。因此销售预测**必须由 UniOps 自建表单承载**，不是降级方案而是唯一方案。

### 6.2 端到端链路

```
① 销售预测（18 个月 x 产品 x 月度量）      <- 系统内建网格表单 + 导入导出，月度滚动版本
        |  扣减
② 可用库存 = WMS 本地库存（自动 30 分钟）
           + 代储仓库存（单个主仓，成品，周度人工录入：产品+数量+盘点日期+批次号）
           + 已排产未入库
        v
③ 净需求（月 x 产品）
        |  受产能规则约束压缩／前移（保质期硬校验）
        v
④ 生产计划 MPS（产品 x 月 x 数量 x 计划生产日期，标注是否提前生产）
        |  计划员调整 -> 确认发布
        v
⑤ MRP 运算：按 BOM 逐层展开（S/CF 包装 -> CW 干混(可选) -> CS 制粉 -> CR 原料 / CP 包材）
        v
⑥ 采购建议 -> 导出/通知采购员 -> NC 下单 -> NC 采购镜像回流成在途
        v
⑦ 月末：实际产出回填（NC MM_MO.NINNUM 预填 + 人工确认）-> 计划达成率 + 差欠结转决策
```

### 6.3 关键业务规则（实测确认，实现不得简化）

| 规则 | 内容 |
| --- | --- |
| 成品编码双轨 | `CF*` 早期编码、`S*` 当前 SKU 编码，**两者都是成品**，BOM 结构一致 |
| 双单位 | S 成品主单位 **KG**、辅单位 **PIECES**，物料表有换算关系，BOM 行两个单位的值都有。**计划口径统一 KG**（WMS 实际也用 KG；ERP 物料主数据把多数 S0 标为 PIECES，以 WMS/KG 为准） |
| 吨位 | ERP `weight_NET` 全库 100% 为空，不可用；CF/CS/CW/CR 本位单位即 KGM，吨 = 数量 / 1000 直接可算 |
| 包材单位 | `EA` 等同 `PIECES`（个），归一 |
| CM 弃用 | 标准化奶两年前弃用，BOM 全部忽略；实证：涉及 CM 的 25 个成品近 12 个月零生产，73 张在产工单按其实际 BOM 版本走无一触及 CM |
| 版本选择 | 同母件多个已审版本并存，取 **HVERSION 数值最大**（1.94 大于 1.9，禁止字符串比较）+ 行级生效窗口覆盖查询日期 |
| 实际产出 | **绝不作为库存加项**——产出已进 WMS，再加一次会使可用库存翻倍导致严重少采购。用途仅限达成率分析与差欠结转决策 |
| 代储仓效期 | 录入只需批次号；效期由批次号反查 WMS `INV_LOT_ATT`（23973 条 > 在库 3532 条，历史批次属性保留）自动带出，带不出则留空并提示 |

### 6.4 新增数据模型（mrp-api）

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `mrp_forecast_versions` | version_no, status(draft/confirmed/superseded), horizon_start_month, horizon_months(默认18), created_by, confirmed_at, note | 预测版本头，月度滚动 |
| `mrp_forecast_lines` | version_id, material_code, month(YYYY-MM), qty, uom(默认KG), freeze_flag | 预测明细（界面是行=产品列=月，落库为行式） |
| `mrp_consignment_stock` | warehouse_code, material_code, lot_no, qty, count_date, expiry_date(反查WMS自动带出,可空), entered_by | 代储仓周度盘点，单个主仓，仅成品 |
| `mrp_capacity_rules` | scope_type(factory/product_family/line), scope_ref, constraint_type(max_sku_count/max_output_qty), limit_value, uom, effective_from, effective_to, is_active | **产能可定制**：规则行而非硬编码 |
| `mrp_mps_runs` | run_no, forecast_version_id, horizon, status, generated_at, generated_by, stats(jsonb) | MPS 生成批次 |
| `mrp_mps_lines` | run_id, material_code, demand_month, plan_month, plan_date, qty, is_prebuild, prebuild_reason, shelf_life_ok, locked_by_planner, manual_adjusted, status(draft/confirmed/released) | 生产计划行；demand_month 不等于 plan_month 即提前生产 |
| `mrp_actual_output` | material_code, month, planned_qty, actual_qty, source(nc_prefill/manual), variance, carry_forward_qty, confirmed_by, confirmed_at | 实际产出与差异；carry_forward_qty 由计划员决定 |

原 V1 设计的 `mrp_demands` 改为**由 MPS 发布结果生成**（`demand_type='mps'`），不再直接由人工导入；`mrp_supplies` / `mrp_pab` / `mrp_proposals` / `mrp_exceptions` 沿用 V1 设计。

### 6.5 MPS 生成算法

输入：净需求（月 x 产品）、生效产能规则、物料保质期、已锁定的计划行。

```
按需求月份升序遍历：
  1. 装载当月净需求到当月产能
  2. 若违反任一约束（品种数 > 上限 或 总量 > 上限）：
       挑选可前移的产品（按 保质期余量 降序、批量 降序）
       逐个向前月推，每次推前校验：
         a. 目标月剩余产能足够
         b. 保质期硬校验：提前月数 <= 保质期 - 安全余量（参数）
       推不动则标记为「产能缺口」异常，不静默丢弃
  3. 计划员锁定的行不参与自动调整
输出：计划行 + 是否提前生产 + 提前原因 + 产能占用明细
```

计划员可手工改数量/月份/锁定，改后可重算（锁定行保持不变）。确认发布后写入 `mrp_demands` 成为 MRP 输入。

### 6.6 前端 UI 设计

**技术基线**：新建 `mrp/` Vite app（端口 5179），React + TS + Tailwind + `@uniops/shell`（现有导出：多页签 `TabHost/TabBar/TabRouterSync/useTabDirty` + UI 原语 `button/card/badge/input/label/form-field/skeleton/Pagination` + `tokens.css`）。**shell 没有网格组件**——矩阵录入以 `epms/src/pages/budget/BreakdownMatrixModal.tsx`（1296 行，已含粘贴 + undo/redo + 稀疏 Map 状态）为蓝本，在 mrp app 内实现，**不引入 ag-grid/handsontable 等第三方表格库**（样式体系与 Tailwind/EPMS 冲突，且部分为商业授权）。若后续 MPS 页也需要同款网格，再抽到 `@uniops/shell`。**样式对照 EPMS 模板**，页面包 `PortalChromeLayout`，状态徽章用统一 `StatusBadge`，浮层用 `createPortal` 到 body，Decimal 字段前端 `Number()` 转换，**user-facing 文案全英文**。

**通用交互规则**（源自 UX 规则库，均为 High/Medium 级）：
- 表格宽内容包 `overflow-x-auto`，页面本体禁止横向滚动
- 校验 **onBlur** 即时反馈，错误显示在**出错单元格旁**，不只在顶部汇总；错误容器带 `role="alert"`，不只靠红框（视觉之外须有可读文本）
- 所有提交动作走 loading -> success/error 三态，成功给 toast，禁止静默成功
- 批量操作用勾选列 + 顶部动作条，不做逐行重复按钮
- 交互目标不小于 44x44px，动效 150-300ms 且只用于表达含义

#### 页面 1：Sales Forecast（预测录入，本期最重的交互）

```
+- Sales Forecast --------------------------------------- [v2026-08 draft v] -+
| [Import Excel] [Export] [Download Template]        [Save Draft] [Confirm]   |
+------------+-------+-------+-------+-------+--- 18 months ---+--------------+
| Product v  |2026-09|2026-10|2026-11|2026-12| ...             | Total (kg)   |
+------------+-------+-------+-------+-------+-----------------+--------------+
| S0093 700g | 20,000| 20,000|[20,000]| 18,000| ...            |      318,000 |
| S0060 300g | 12,000| 12,000| 12,000| 12,000| ...             |      210,000 |
+------------+-------+-------+-------+-------+-----------------+--------------+
| Monthly Sum| 32,000| 32,000| 32,000| 30,000|                 |      528,000 |
+------------+-------+-------+-------+-------+-----------------+--------------+
```

- **行 = 产品，列 = 18 个月**，单元格直接编辑（点击进入、Tab/方向键移动、Enter 下移）
- **★复制粘贴（必做，计划员的实际工作方式就是从 Excel 整块搬）——直接沿用 `epms/src/pages/budget/BreakdownMatrixModal.tsx` 的成熟实现，不要重新发明**：
  - 该文件已实现「以焦点单元格为锚点的矩形块粘贴」（第 327–370 行）+ 完整 undo/redo 历史栈（第 116–151 行 `Snapshot`/`commitSnapshot`/`undo`/`redo`），是同类「行×列矩阵录入」场景，模式可整体照搬
  - 关键做法（照抄）：`window` 级 `paste` 监听 + 锚点单元格判空；**只拦截多单元格粘贴**（内容含 `\t` 或 `\n`），单格粘贴放行给原生 input 保留默认行为；解析按 `\r` 剥离 → 尾部空行剔除 → `\n` 分行 → `\t` 分列；数值清洗 `trim()` 后剥离 `,` 与 `$`，`parseFloat` 后校验 `Number.isFinite`；**越界自动裁剪**（`rowIdx + r < rows.length`）；计算列/合计列跳过不写入
  - 本模块补充规则：① 空单元格视为「跳过不改」而非清零（与预算模块一致，避免误清数据）；② 落在**冻结月份**的单元格跳过并汇总提示「N cells skipped (frozen)」；③ 非数值单元格标红并计入粘贴报告，不中断整块粘贴；④ 粘贴影响 > 100 格时先弹确认摘要（影响行数/列数/格数）
  - **粘贴必须可撤销**：整块粘贴作为一次 snapshot 进 undo 栈，Ctrl+Z 一次撤回整块（预算模块已是此语义）
  - 复制方向同样支持：选中区域 Ctrl+C 输出 TSV，可直接粘回 Excel
- 首列产品名 + 月份表头 **sticky**，横向滚动时不丢失定位
- **行合计、列合计实时更新**——计划员靠总量判断合理性
- 单元格状态用背景色 + 图标双通道表达：已冻结（锁图标）、本次修改未保存（左上角小三角）、校验失败（红边 + 单元格下方错误文本）
- 版本切换器在右上角，可对比上一版本（差异用 +/-% 角标）
- 导入：上传 -> **预览校验报告**（哪一行哪一列错、错在哪）-> 确认导入；错行不导致整批失败
- 产品多时行虚拟化，避免一次渲染上千行

#### 页面 2：Capacity Rules（产能规则维护）

标准列表 + 抽屉表单。列：适用范围 / 约束类型 / 上限值 / 单位 / 生效期 / 状态。新增规则时按 `constraint_type` 渐进展示对应字段（选「月度品种数」就不显示单位选择）。生效期重叠给 inline 警告而非硬拦截（业务上可能有意覆盖）。

#### 页面 3：Production Plan / MPS（生成与调整，本期最有价值的界面）

```
+- Production Plan  [Forecast v2026-08 v]        [Generate] [Recalculate] ---+
| !  2026-11: 15 SKUs / 300t requested -> limit 12 SKUs / 160t              |
|    3 products pre-built to 2026-09/10 . 1 product has capacity gap        |
+---------------------------------------------------------------------------+
| Capacity 2026-09 [########..] 128/160t . 9/12 SKUs                        |
| Capacity 2026-10 [##########] 160/160t . 12/12 SKUs   <- full             |
| Capacity 2026-11 [########..] 152/160t . 12/12 SKUs                       |
+----+---------+----------+----------+--------+---------+--------+----------+
| [] | Product |Demand Mon| Plan Mon |  Qty   |Pre-build| Shelf  | Status   |
+----+---------+----------+----------+--------+---------+--------+----------+
| [] | S0093   | 2026-11  | 2026-09  | 20,000 | ^ 2 mo  | OK     | Locked   |
| [] | S0060   | 2026-11  | 2026-11  | 12,000 |   -     | OK     |          |
| [] | S0074   | 2026-11  |    -     | 18,000 |   -     | GAP    | Blocked  |
+----+---------+----------+----------+--------+---------+--------+----------+
    [Lock selected] [Unlock] [Adjust...] [Confirm & Release ->]
```

- **顶部产能条**是这个页面的核心：每月产能占用一眼可见，满载/超限用色彩 + 文字双通道
- 提前生产行标 `^ n mo` 并在悬浮/展开时给出原因（如「2026-11 超总量上限 140t」）
- 保质期校验独立成列，不合规的行禁止发布并给出可读原因
- 计划员改动：勾选行 -> 批量锁定/解锁；单行 `Adjust...` 打开抽屉改数量或计划月，改完可重算（锁定行不动）
- **产能缺口**不静默吞掉，落成异常行并计入异常中心
- `Confirm & Release` 前弹确认摘要（几个产品、总量、几个提前生产、有无缺口）

#### 页面 4：Consignment Stock（代储仓周度录入）

轻量表单 + 本周已录列表。字段：产品（带搜索的下拉）、批次号、数量、盘点日期。**批次号失焦即反查 WMS 批次属性**，命中则自动带出生产日期/失效日期并置灰显示（来源标注 from WMS），未命中则提示「批次未在 WMS 找到，效期将留空」但不阻断保存。列表顶部显示**数据新鲜度**（Last counted: 2026-08-01, 3 days ago），超过一周变黄提醒。

#### 页面 5：Actual Output（实际产出回填）

行 = 产品，列 = 计划量 / NC 预填实际量 / 确认实际量（可编辑）/ 差异 / 差异率 / 结转决定。NC 预填值置灰显示为参考，计划员在「确认实际量」列录入或直接采纳。差异用 +/- 与颜色双通道，超过阈值（参数）高亮。最右列 `Carry forward?` 让计划员逐行决定差欠是否进下月净需求——**默认不结转**，必须显式勾选。

#### 页面 6：BOM Explorer（BOM 级联查看）

**这不只是一个查看器——它内部就是 MRP 的 BOM 展开引擎。** Phase 1C 的物料展开与本页共用同一套展开逻辑（同一个服务函数），先建于此可提前把展开算法验证在真实数据上，等于给 1C 去风险。目前后端只有单层 `GET /mdm/v1/boms/effective`，多层展开需新增。

**后端新增两个端点（mdm-api）**

| 端点 | 作用 | 关键要求 |
| --- | --- | --- |
| `GET /mdm/v1/boms/explode?product=&date=&max_depth=` | 一次返回整棵级联树 | ① **环检测**（NC 数据可能存在 A→B→A，必须防死循环，命中即截断并在节点上标注）；② `max_depth` 兜底（默认 10）；③ 每个节点返回**累计用量**（相对顶层成品 1 单位，逐层按 `qty_per × (1+scrap_rate) ÷ yield_rate` 累乘）与本层用量两个值；④ 主/辅单位量都带出；⑤ **选版透明**：节点标注实际选中的版本号与候选版本总数（如 `v1.6 (7 approved)`），因为同母件多版本并存是常态，计划员需要知道系统选了哪个；⑥ 组件应有 BOM 却查不到时（如 `-R` 返工变体）标 `missing_bom`，不静默当叶子节点 |
| `GET /mdm/v1/boms/where-used?component=&date=` | 反查：某物料被哪些成品用到 | 影响分析用——原料涨价/断货/质量问题时，一眼看出波及哪些成品。返回路径链而不只是顶层成品 |

**前端页面**

```
+- BOM Explorer -------------------- [Product: S0093 v] [As of: 2026-08-04] -+
| [Expand all] [Collapse all]              [Export Excel]  [Where-used mode] |
+---------------------------------------------------------------------------+
| S0093  Infant Formula 700g              packaging  v1.3 (2 approved)       |
|  |  per 1 kg finished                                    main / secondary  |
|  +- CW0001  Dry-mix Powder      drymix v1.1 (1)   0.984 kg    -            |
|  |   +- CS0026  Silo Powder     milling v1.6 (7)  0.990 kg    -            |
|  |   |   +- CR0031  Skim Milk Powder    raw       0.412 kg    -            |
|  |   |   +- CR0044  Lactose             raw       0.287 kg    -            |
|  |   |   +- ... (14 more)                                                  |
|  |   +- CR0031-2 Nucleotide Premix      raw       0.004 kg    -            |
|  +- CP0115-1  Tin 700g          --                1.000 ea    1 ea         |
|  +- CP0110    Lid                --                1.000 ea    1 ea        |
|  +- CP0132    Carton             --                0.083 ea    1 ea        |
+---------------------------------------------------------------------------+
| ! S0074-R: milling BOM missing in NC (rework variant) — cannot explode      |
+---------------------------------------------------------------------------+
```

- 树形展开/折叠，层级用缩进 + 连接线；`bom_type` 徽章用统一 `StatusBadge`（milling / drymix / packaging / raw / packaging-material）
- **两列用量**：本层用量 + 相对顶层成品的累计用量——后者才是计划员真正关心的（"做 1 吨成品要多少脱脂奶粉"）
- **as-of 日期选择器**：BOM 按日期取版，改日期整棵树重算
- 版本标注 `v1.6 (7 approved)`，点击可展开候选版本列表与各自生效窗口——**这是数据可信度的关键**，多版本并存时计划员必须能确认系统选对了
- `missing_bom` 节点用警示样式 + 可读原因，不静默当叶子
- 环检测命中的节点明确标注并截断
- **Where-used 模式**切换后，输入原料码反查被哪些成品使用，展示完整路径（`CR0031 → CS0026 → CW0001 → S0093`）
- 导出 Excel（含缩进层级与累计用量），计划员要拿去和生产/采购对账

**Sync 按钮（从 NC 同步 BOM）**

页面右上角放 `Sync from NC`，调用 Phase 0 已建好的 `POST /mdm/v1/boms/sync`（会先拉 NC 原始镜像再转换规范化表）。要点：

- **权限分离**：查看只需 `mrp.report.view`，**Sync 按钮需 `mdm.bom.write`**——无此权限时不显示按钮（而非点了报 403）
- **⚠️ 后端缺口（须补）**：当前 BOM 同步**不记录任何同步状态**（`nc_bom_sync` 全量扫描、无水位、无 last-synced 记录），页面就无法显示"上次同步于何时"，Sync 按钮等于盲按。须新增同步状态记录（仿 mrp-api 的 `mrp_sync_state`：source / last_success_at / last_error / 本次统计），并加读端点供页面展示
- **数据新鲜度常驻可见**：标题旁显示 `Last synced: 2026-08-04 10:22 (2 hours ago)`，超过约定时长（如 24 小时）变黄提示
- **长耗时反馈**：一次全量拉 1016 表头 + 10169 行 + 664 替代料 + 2590 物料映射，不是秒级。按钮点击后立即进 loading 态并禁用，避免重复提交；完成前给"Syncing from NC…"进度提示
- **并发守卫**：多人同时点会让 delete+insert 事务相互阻塞。同步入口加 `pg_advisory_lock`（EPMS 单据号生成已有此惯例），已有同步在跑时第二个请求返回明确提示而不是排队干等
- **结果摘要**：同步端点已返回 `boms / lines / substitutes / skipped / warnings / tombstoned`，完成后以 toast + 可展开明细呈现；**`warnings` 与 `skipped` 必须可见**（当前真实数据里 warnings=54，含 41 条 CM 组件排除 + 13 条未知前缀），否则数据静默丢失无人察觉
- **未配置 NC 时**：`nc_configured()` 为假则按钮置灰并说明原因（"NC connection not configured"），不是点了报错

**权限**：查看 `mrp.report.view`，同步 `mdm.bom.write`。BOM 本身不可编辑（NC 为唯一权威），页面显式标注数据来源与最近同步时间。

#### 页面 7 及以后：沿用 V1 设计

Planning Workbench（工作台 + KPI，兼驾驶舱）、MRP Run Console（运算 + 同步状态）、Material Supply/Demand Detail（逐期间 PAB 双口径 + 批次 + BOM 展开路径）、Purchase Proposals、Exception Center、Reports。

#### 导航与权限

Portal `navConfig` 加 MRP 分组（`anyPermission`），组内条目按权限键各自门禁：Forecast(`mrp.demand.write`)、Capacity(`mrp.param.write`)、Production Plan(`mrp.run.execute` / `mrp.proposal.confirm`)、Consignment Stock(`mrp.demand.write`)、Actual Output(`mrp.demand.write`)、Workbench/Reports(`mrp.report.view`)。

### 6.7 Phase 1 阶段划分

| 阶段 | 内容 | 出口标准 |
| --- | --- | --- |
| **1A 需求与库存底账 + BOM Explorer** | 预测版本/明细模型 + 网格表单（含 Excel 粘贴/undo）+ 导入导出 + 代储仓录入（含批次反查效期）+ 净需求计算；**BOM Explorer（多层展开端点 + where-used + Sync 按钮 + 同步状态记录）** | 导入一份真实 18 个月预测，能算出逐月净需求，三个库存来源可追溯且新鲜度可见；**BOM Explorer 能对真实成品展开完整级联树并显示累计用量，Sync 按钮可用且同步时间可见** |
| **1B MPS 产能平衡** | 产能规则维护 + MPS 生成算法（提前生产 + 保质期校验）+ 计划调整/锁定/重算 + 确认发布 | 用真实场景跑通：15 品/300t 压到 12 品/160t 内，超出部分前移且保质期合规，缺口成异常 |
| **1C MRP 物料展开** | 吃 1B 发布的计划 -> BOM 逐层展开 -> PAB -> 采购建议 -> 导出通知 + 实际产出回填 | 真实一个月数据回放，成品计划推出原料/包材采购建议；月末产出回填算出达成率 |

### 6.8 仍待确认

- 销售预测由谁提供、多久滚动一次（决定 1A 的模板定稿与提醒机制）
- 产能约束除品种数与总吨数外是否还有产线/产品族维度（结构已留好，加规则行即可）
- 提前生产的保质期安全余量取值（参数，默认建议保质期的 1/3）
