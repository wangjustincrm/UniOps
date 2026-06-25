# Budget Module — Product Requirements Document

**版本:** v1.0  
**日期:** 2026-05-15  
**状态:** 已批准 — 实施中  
**服务:** `budget-api` (`:8007`)  
**作者:** 架构与产品团队

---

## 1. 模块概述

### 1.1 定位
Budget 模块是 UniOps 企业预算管理的中央服务，作为独立微服务运行。负责：
- 预算账户目录（科目模板）的全局维护
- 各成本中心（Cost Center）的年度预算编制
- 实际发生数据的实时汇总
- 为 EPMS / OA / Finance Core 提供统一的预算数据源

### 1.2 边界

| 在范围内 | 不在范围内 |
|---------|------|
| L1/L2 账户模板 CRUD | Department / CostCenter 管理（在 EPMS） |
| 因子配置 | 工作流审批引擎（在 approval-api） |
| 预算编制（plan）| PA / Invoice 详情（在 expense-api / finance-api） |
| 实际发生汇总 | 财务凭证 / 总账 |
| 余额查询 API（供 PR、PA 调用） | 跨年预算调整 |
| 预算修订 (revision)：approved plan 创建新版本走审批 | |

### 1.3 服务架构
独立微服务 `budget-api`，端口 :8007。共享 PostgreSQL（与 epms 同库，独立表空间）。其他服务通过 HTTP 调用，**不再直接写预算表**。

```
budget-api:8007 (FastAPI)
  ├─ PostgreSQL（共享 epms DB）
  └─ JWT 验证（共享 JWT_SECRET_KEY）

调用方：
  ├─ epms-api    → GET /balance       （PR 超预算检测）
  ├─ expense-api → POST /book-expense  （费用付款入账）
  ├─ finance-api → POST /commit, /release, /actualize（PA 流程）
  ├─ epms frontend → 所有目录/编制/dashboard 端点
  └─ oa frontend → GET /hierarchy     （费用单 budget account 下拉）
```

---

## 2. 术语

| 名词 | 定义 |
|------|------|
| **Department** | 部门（在 EPMS 维护） |
| **Cost Center (CC)** | 成本中心，一个 Department 包含多个 CC |
| **Budget L1 (Category)** | 一级分类，全局唯一，**所有 CC 共享**。例：销售费用、管理费用 |
| **Budget Account (L2)** | 二级科目，在 L1 内唯一，**所有 CC 共享同一份目录** |
| **Factor (决定因子)** | 某 Account 在编制时的可选拆分维度。例：市场推广费 → Brand × Channel |
| **Factor Value** | 因子的可选值。例：Brand={A, B, C}, Channel={Online, Offline} |
| **Budget Plan** | 一个 CC 一个会计年度的预算计划（一份 plan = 12 × N 个 plan_lines） |
| **Plan Line** | plan 中的一行：CC × Account × Month 的金额 |
| **Plan Breakdown** | plan_line 在因子维度的拆分行（仅 decomposition_enabled 的 account） |
| **Budget Actual** | 月度实际发生数据，来自 PA / Expense Claim |
| **Budget Ledger** | 跨服务写入的去重账本表 |

---

## 3. 数据模型概要

```
budget_l1                  ←─ 共享 L1 目录
  └ budget_accounts        ←─ 共享 L2 模板（可启用 decomposition）
        └ budget_account_factors      ←─ 每 account 可选的因子定义（可从 factor_templates 复制而来；复制后独立）
              └ budget_account_factor_values  ←─ 因子取值

factor_templates             ←─ 全局可复用模板库（Portal → Finance → Factor Library）
  └ factor_template_values   ←─ 模板取值（attach 时仅 is_active=true 被复制）

budget_plans              ←─ per CC × year，编制实体
  └ budget_plan_lines     ←─ per CC × account × month 的汇总金额
        └ budget_plan_breakdowns  ←─ 因子分解行（可选）

budget_ledger             ←─ 跨服务写入的去重账本（实际/承诺都从这里聚合）

budget_settings           ←─ 单行系统配置表
```

详细 DDL 见 [DESIGN.md](./DESIGN.md) §2。

---

## 4. 功能需求

### 4.1 Account Catalog（账户目录）

| FR ID | 需求 |
|-------|------|
| **BACC-001** | 系统管理员可 CRUD L1 分类。code 全局唯一（A-Z0-9，最长 20 字符）。删除前需检查无 L2 子项。 |
| **BACC-002** | 系统管理员可 CRUD L2 Account。code 在 L1 内唯一。L1 不可在创建后变更（保持稳定）。 |
| **BACC-003** | 每个 Account 有布尔标志 `decomposition_enabled`，控制是否在预算编制时按因子拆分。 |
| **BACC-004** | Account 删除：若已被任何 plan_line 引用，禁止物理删除；可标记 `is_active=false`（停用）。 |
| **BACC-005** | 停用的 Account 不在新建 plan 时出现，但已存在的 plan_line 保留。 |
| **BACC-006** | CSV 导入/导出仅含目录定义（code/name/L1），不含 plan/actual 数据。 |

### 4.2 Factor 配置（决定因子）

| FR ID | 需求 |
|-------|------|
| **BFAC-001** | 当 Account.decomposition_enabled = true 时，可为该 Account 配置 1 至 **3** 个因子（**硬上限 3**）。上限对应 Matrix 编辑器的三个轴：Primary=Rows、2nd=Columns、3rd=Sub-Columns。 |
| **BFAC-001a** | `budget_settings.max_factors_per_account` 字段保留向后兼容，但**业务代码硬编码 3** 为创建守卫（[budget-api/app/crud/factor.py:`MAX_FACTORS_PER_ACCOUNT`](budget-api/app/crud/factor.py)）；该 setting 在 Portal Budget Config 不再以可编辑控件出现。 |
| **BFAC-002** | 每个因子下定义可选值列表（最少 1 个，无上限）。 |
| **BFAC-003** | 因子和取值修改不影响已存在的 plan_breakdowns（保留历史快照）。 |
| **BFAC-004** | 若一个 Account 已有 plan_breakdowns，禁止移除其因子；只能停用因子值。 |
| **BFAC-005** | 因子取值的"启用/停用"标志：停用值不在新建 breakdown 时出现。 |
| **BFAC-006** | 因子层级由 `sort_order` 隐式决定：`sort_order=0` → **Primary**（Matrix rows）、`1` → **2nd**（Matrix columns）、`2` → **3rd**（每个 column 下的 sub-columns）。Catalog 页面在每个因子卡片上显示对应角色徽章。 |
| **BFAC-007** | 存量超出 3 个因子的 Account（grandfathered）：DB 保留全部数据；Catalog 显示 amber 警告条；Matrix 编辑器仅使用 `sort_order` 最小的 3 个因子，超出部分只读。 |

### 4.2.1 Factor Library（可复用因子模板）

为减少在多个 Account 上重复维护相同因子（如 `brand`、`channel`、`region`），系统提供独立的 **Factor Library** 管理页面（Portal → Finance → Factor Library）。库中的 `factor_templates` 是配置模板，不直接参与计算，attach 到 Account 时复制到现有的 `budget_account_factors` / `budget_account_factor_values` 表，**复制后两侧独立**——后续修改模板不会回写到已 attach 的 Account（保护 plan_breakdowns 历史完整性）。

| FR ID | 需求 |
|-------|------|
| **BFAC-LIB-001** | 新表 `factor_templates`（id, factor_code 全局唯一, factor_name, description?, is_active, timestamps）+ `factor_template_values`（id, template_id CASCADE, value_code, value_name, sort_order, is_active）。Alembic: `20260527_0004_factor_templates`。 |
| **BFAC-LIB-002** | 端点：`GET /factor-templates`、`GET /factor-templates/{id}`、`POST /factor-templates`、`PATCH /factor-templates/{id}`、`DELETE /factor-templates/{id}`；`POST /factor-templates/{id}/values`、`PATCH /factor-template-values/{id}`、`DELETE /factor-template-values/{id}`。读端点开放给所有登录用户；写端点要求 `system_admin | finance_manager | finance_bp`（与现有 per-account 因子写角色一致）。 |
| **BFAC-LIB-003** | `factor_code` 模板创建后**不可修改**（acts as the default copy key）。`factor_name` / `description` / `is_active` 可改；`is_active=false` 时模板不出现在 EPMS 的 From Library 选择器内。 |
| **BFAC-LIB-004** | 删除模板永远成功——不级联到任何已 attach 的 Account（per-Account 因子表没有 FK 回模板）；仅 `factor_template_values` 通过 CASCADE 一并删除。 |
| **BFAC-LIB-005** | 模板值 `factor_template_values` 支持硬删除（不影响已 attach 的 Account 拷贝）；推荐使用 `is_active` 软停用以便在 UI 上保留历史可见性。 |
| **BFAC-LIB-006** | 新端点：`POST /accounts/{account_id}/factors/from-template`，body `{ template_id, factor_code?, factor_name?, sort_order? }`。语义：拉取模板 + 仅 `is_active=true` 的模板值，复制到 `budget_account_factors` / `budget_account_factor_values`。可选 `factor_code` / `factor_name` 覆盖默认值——同一模板可在两个 Account 上以不同 factor_code attach。3-因子上限和 `(account_id, factor_code)` 唯一约束仍适用。 |
| **BFAC-LIB-007** | EPMS BudgetCatalog 的 FactorConfigPanel 在 "Add Factor" 旁新增 "From Library" 按钮。点击展开模板选择器（活跃模板列表 + 值预览），选中后可选地覆盖 code/name，确认后调用 BFAC-LIB-006 端点。模板已 attach 后，本 Account 的因子与模板**完全独立**——UI 上不显示 "来自模板" 之类的回链。 |
| **BFAC-LIB-008** | 模板侧的 CRUD 仅在 Portal 上提供（`portal/src/pages/budget/FactorLibraryPage.tsx`）；EPMS 仅作只读消费方（`GET /factor-templates?active_only=true`）。Portal 侧边栏 FINANCE 区新增 "Factor Library" 入口，受角色守卫 `[system_admin, finance_manager, finance_bp]`。 |

### 4.3 Budget Plan 编制

| FR ID | 需求 |
|-------|------|
| **BPLAN-001** | 每个 (cost_center_id, fiscal_year) 可有多份 budget_plan **版本**（v1, v2, …）。同一 (CC, year) 任意时刻最多一份为 `is_current=true`（DB 偏唯一索引强制）。 |
| **BPLAN-002** | Plan 状态机：`draft → submitted → in_review → approved`；或 `submitted/in_review → returned/rejected`。同一 (CC, year) 同时只能有一个非终态版本。 |
| **BPLAN-003** | 用户为每个有效 Account × 12 个月填写计划金额。空值视为 0。 |
| **BPLAN-004** | 当 Account.decomposition_enabled = true：用户在 **Matrix 矩阵编辑器**（spreadsheet-style 二维 / 三维 grid）填报，系统自动 sum 至 plan_line.amount。直接编辑 plan_line.amount 在此情形下被禁止。Matrix 编辑器支持 Spread 工具（Evenly / Proportional / Fill）、Baseline 侧栏（去年同月 + YTD actuals + Copy from Last Year）、Excel-style 键盘导航、Excel 粘贴。 |
| **BPLAN-005** | 因子组合可以是**部分笛卡尔积**（用户选哪些组合就填哪些，不强制全组合）。 |
| **BPLAN-006** | 季度（Q1=1+2+3，Q2=4+5+6 等）和年度合计由系统**计算**得出，不持久化。 |
| **BPLAN-007** | Plan 提交需走审批流（复用 approval-api，action key = `budget_plan`，默认 `dept_manager → finance_manager`）。 |
| **BPLAN-008** | Plan draft 自动保存（前端节流，每 5 秒）。 |
| **BPLAN-009** | 支持"从去年 plan 复制"快捷功能（`POST /plans/{id}/copy-from-prev`）。 |
| **BPLAN-010** | **Plan 删除**：仅 `status='draft'` 的 plan 允许删除（`DELETE /plans/{id}`，写角色守卫）。Plan_lines / plan_breakdowns 通过 `ON DELETE CASCADE` 自动连删；若该 draft 是修订版（`parent_plan_id` 非空），删除不影响父版本（`parent_plan_id` 字段在 budget_plans 的 self-FK 为 `ON DELETE SET NULL`，反向）。其他状态（submitted/in_review/approved 等）必须走工作流 reject/cancel 以保留审计轨迹。 |
| **BPLAN-011** | **Bulk Breakdown 替换**：`PUT /plans/{id}/lines/{account_id}/{month}/breakdowns` 原子端点，body `{ breakdowns: [{ factor_combo, amount, notes? }] }`。在单一事务内 DELETE 该 cell 全部 breakdowns + INSERT payload + 重算 plan_line.amount。Matrix 编辑器 Save 时使用，避免 N 次 PATCH 的网络往返。写角色守卫 + draft/returned 状态守卫。响应 `{ breakdowns, line_amount }`。 |
| **BPLAN-012** | **Baseline 查询**：`GET /plans/{id}/lines/{account_id}/{month}/baseline` 返回 `{ last_year_same_month, current_month_actual, ytd_actual, last_year_breakdowns }`，用于 Matrix 编辑器的右侧 Reference 侧栏；`last_year_breakdowns` 支持 "Copy from Last Year" 一键填充。Actuals 来自 `budget_ledger` (operation in actualize/book_expense)；去年数据来自 (cc, fy-1) 的 approved+current plan。 |

### 4.3.1 Budget Plan CSV 导入 / 导出

| FR ID | 需求 |
|-------|------|
| **BPIE-001** | `GET /plans/{id}/export` 返回宽表 CSV，列：`L1 Code, L1 Name, Account Code, Account Name, Decomposed, Jan..Dec, Q1..Q4, Year`。文件名 `budget-plan-fy{year}-v{version}.csv`，任何角色可下载。 |
| **BPIE-002** | `POST /plans/{id}/import` 接收 multipart CSV，写角色守卫，仅 `status ∈ {draft, returned}` 时允许。`Account Code` 列为主键，多余/缺失列容忍。 |
| **BPIE-003** | 月份单元格语义：空格 = 不修改、`"0"` = 显式置零、其他数字 = 覆盖。接受 Excel 千分号 `1,234.56`、货币符号 `$` / `CAD`。无效数字累计到 errors 列表，不阻塞同行其他月份。 |
| **BPIE-004** | 因子分解账户（`decomposition_enabled=true`）整行跳过导入，统计为 `accounts_skipped`（保护 `line.amount = Σ breakdowns` 不变量；breakdowns 必须走 UI 编辑）。 |
| **BPIE-005** | 响应 schema：`{ lines_updated, accounts_touched, accounts_skipped, errors: string[] }`，前端展示统计 + 前 6 条问题。 |

### 4.3.2 Budget Plan 调整（预算修订）

已批准的 plan 不能直接编辑，必须通过"修订" (revision) 流程创建新版本。

| FR ID | 需求 |
|-------|------|
| **BPREV-001** | 只有 `status='approved' AND is_current=true` 的 plan 才可被修订。`POST /plans/{id}/revise` 创建新版本（`version = parent.version + 1`，`status='draft'`，`is_current=false`，`parent_plan_id=parent.id`），并复制父版本所有 plan_lines 和 breakdowns 作为起点。**此外为所有当前 `is_active=true` 但父版本未覆盖的 BudgetAccount × 12 个月补种 `amount=0` 行**，确保父版本之后新加的科目也能在修订版里填预算。 |
| **BPREV-002** | 同一 (CC, year) 同时只能有一个非终态版本。若已存在 draft/submitted/in_review/returned 版本，再次发起 revise 返回 409。 |
| **BPREV-003** | 修订版需走完整审批流（与新建 plan 同 action key `budget_plan`）。 |
| **BPREV-004** | 修订版被 approved 时，`approval-api` 的 post-approve hook 原子地将父版本 `is_current` 翻转为 false，新版本翻转为 true。父版本 `status` **保持 'approved'**（用于审计），不再参与余额计算。 |
| **BPREV-005** | 修订版被 reject/cancel 时，父版本保持 `is_current=true`，不受影响。 |
| **BPREV-006** | `GET /plans/by-cc-year/{cc_id}/{year}/versions` 返回该 (CC, year) 的完整版本链（含历史 + 当前），按 version 倒序。UI 用于历史审计视图。 |
| **BPREV-007** | `GET /plans?include_history=true` 可同时返回历史版本；默认仅返回 `is_current=true` 的版本。 |
| **BPREV-008** | 所有余额 / 实际汇总查询（`GET /balance`、`GET /actuals/summary`）均按 `status='approved' AND is_current=true` 过滤；revision 处于 draft/submitted 期间，外部读到的仍是父版本的预算金额。 |
| **BPREV-009** | revision 字段 `revision_notes` 记录修订原因（必填建议在 UI 层做，DB 不强制）。 |

### 4.4 实际发生汇总

| FR ID | 需求 |
|-------|------|
| **BACT-001** | 系统按 (CC, account, year, month) 汇总实际发生金额。 |
| **BACT-002** | 数据来源（Ledger 模式）：① expense-api 在 expense_claim 付款时 POST `/book-expense`；② finance-api 在 PA 流程时 POST `/commit`、`/release`、`/actualize`。 |
| **BACT-003** | committed = sum of ledger entries with operation='commit' − sum of operations 'release' 和 'actualize'。 |
| **BACT-004** | actual_spent = sum of ledger entries with operation='actualize' 或 'book_expense'。 |
| **BACT-005** | Dashboard 显示 plan vs actual 月度对比图（按 CC / L1 / Account 维度切换）。 |
| **BACT-006** | 单账户余额查询：available = annual_total(plan_lines) − committed − actual_spent。 |
| **BACT-007** | 实时性：ledger 写入即时生效（每次 POST 触发聚合），无需后台刷新。 |

### 4.5 跨服务 API

| FR ID | 需求 |
|-------|------|
| **BAPI-001** | `GET /api/v1/balance?cost_center_id=X&account_code=Y&fiscal_year=Z` → 单账户余额（替代 epms-api `_compute_over_budget`）。 |
| **BAPI-002** | `POST /api/v1/book-expense` → 由 expense-api 在 expense_claim 付款时调用。Body 含 line items 列表。 |
| **BAPI-003** | `POST /api/v1/commit` → 由 finance-api 在 PA 审批通过时调用。 |
| **BAPI-004** | `POST /api/v1/release` → 由 finance-api 在 PA 取消时调用。 |
| **BAPI-005** | `POST /api/v1/actualize` → 由 finance-api 在 PA 付款时调用（committed → actual_spent）。 |
| **BAPI-006** | 所有写操作幂等：同 `(source_service, source_doc_type, source_doc_id, operation)` 重复调用不重复入账（数据库 unique 约束保证）。 |
| **BAPI-007** | 所有 API 需 JWT 鉴权，共享 `JWT_SECRET_KEY`。 |
| **BAPI-008** | `GET /api/v1/hierarchy` → 返回 CC → L1 → L2 树（兼容 OA 现有调用格式）。 |

### 4.6 系统配置（Budget Config）

Budget 模块的可配置项分两层存储：

| 字段 | 存储位置 | 编辑入口 |
|------|---------|----------|
| `available_fiscal_years: list[int]` — Plan dropdown 的可选年份 | `epms-api.company_config.budget_admin_config` JSONB | Portal → FINANCE → Budget Config |
| `yellow_threshold_pct`, `red_threshold_pct` — Dashboard 颜色阈值 | 同上 | 同上 |
| `over_budget_mode` — `fm_gm_opm` / `fm_only` / `hard_block` | 同上 | 同上 |
| `max_factors_per_account: int` — 单 Account 因子上限 | `budget-api.budget_settings.max_factors_per_account` | 同上（GET/PATCH `/api/v1/settings`） |

| FR ID | 需求 |
|-------|------|
| **BCFG-001** | `available_fiscal_years` 是整数数组（2000-2100 范围内、自动去重、按升序存储）。"New Budget Plan" 弹窗的 Fiscal Year 下拉、Budget Plans 列表的年份过滤、Dashboard 年份选择器**全部读取此字段**。空列表时阻止创建 plan 并提示管理员。 |
| **BCFG-002** | 删除 `available_fiscal_years` 里的年份**只是从下拉里隐藏**，已存在的 plan 仍可正常访问/编辑（防止误删数据）。 |
| **BCFG-003** | **会计年度 = 日历年**（隐式假设）：跨服务的日期→会计年度推导一律用 `date.year`（`expense-api/crud/expense.py`、`epms-api/crud/pr.py` 等）。**无可配置的财年起止月份**——历史上存在的 `fiscal_year_start/end` 字段已在 2026-05-19 移除（属于 dead config，从未被业务逻辑读取）。 |
| **BCFG-004** | Budget Config 页面（Portal）使用 `PortalChromeLayout` 共享外壳，保证与 PortalHome / Budget Dashboard iframe / Budget Plans iframe / Account Catalog iframe 视觉一致（同一 sidebar、同一顶栏、同一用户菜单）。 |

---

## 5. UI/UX 需求

### 5.1 账户目录页（Portal Admin 新增）
- 左侧：L1 树
- 右侧：选中 L1 后显示该 L1 下的 Account 列表
- Account 行展开后显示因子配置（若启用 decomposition）
- 因子区按钮组：**From Library**（从 Factor Library 选模板复制，BFAC-LIB-007）+ **Add Factor**（手工新建）
- 顶部按钮：New L1 / Import CSV / Export CSV

### 5.2 因子配置弹窗
- 因子列表（拖拽排序）
- 每个因子下的取值列表（启用/停用）
- 保存时验证：若 account 已有 plan_breakdowns，禁止移除因子
- 添加因子前校验当前数量 < `max_factors_per_account` 配置

### 5.3 Budget Config 页（Portal → FINANCE → Budget Config）
- 使用 `PortalChromeLayout` 共享外壳（与 PortalHome / Budget iframe 页面一致的 sidebar + 顶栏）
- 段落 1：**Available Fiscal Years** — 已配置年份的 chip + X 删除，输入框 + Add Year 按钮，范围校验 2000-2100、去重
- 段落 2：**Budget Alert Thresholds** — yellow / red 百分比阈值
- 段落 3：**Over-Budget Approval Mode** — `fm_gm_opm` / `fm_only` / `hard_block` 单选
- 段落 4：**Factor Decomposition** — `max_factors_per_account` 整数（1-50）
- 仅 system_admin 可见 + 可编辑

### 5.4 Budget Plans 列表页
- 顶部：年份过滤（来自 `available_fiscal_years` 降序）+ CC 过滤 + 状态过滤 + "Show superseded versions" 复选框（含 `include_history=true`）
- 列：Cost Center、Fiscal Year、Version（`v{n}` + Current / Superseded 徽章）、Status、Submitted、Approved、Last Updated、Actions
- 默认排序：CC code 升序 → fiscal year 降序 → version 降序
- 操作：Open 链接 + **🗑 删除按钮（仅 `status='draft'` 且写角色可见）**

### 5.5 预算编制页
- 顶部：版本徽章 `vN` + Current / Superseded、Revision Notes（如有）、Status；右上角按钮：
  - **History** — 抽屉显示该 (CC, FY) 完整版本链
  - **Open Draft v{n}**（仅 approved+current 且已存在 open revision）—— 直达卡住的 draft
  - **Revise Plan**（仅 approved+current 且无 open revision）—— 弹窗输入 revision_notes 后创建 v+1
  - **Export CSV** / **Import CSV**（Import 仅 draft/returned）
  - **Copy FY {n-1}**（仅 draft/returned）
  - **Submit / Approve / Return / Reject** — 视状态而定
- 主表：**单一滚动容器**（sticky header 顶部 + 虚拟化 body + sticky 汇总行底部，无重复滚动条）
- 列：Account（sticky-left）+ 12 个月 + Q1-Q4（灰底）+ Year（primary 强调）
- L1 分组行仅显示 L1 Name（不重复 code）
- 启用 decomposition 的 Account 在 amount 单元格内显示锁图标，点击弹出 Breakdown Editor
- 底部 sticky 汇总行：Total 行 sum 全部账户的月份 / 季度 / 年度

### 5.5 因子组合编辑器（子行）
- 行内点击 "+ Add Combination"
- 弹窗：每个因子下拉选一个值 → 提交
- 已存在的组合不重复

### 5.6 Plan vs Actual Dashboard
- 顶部筛选：CC / 年度 / L1（可选）
- 折线图：plan vs actual 月度对比
- 表格：每个 Account 的 plan / committed / actual_spent / available / variance%
- 颜色：超预算 红色，接近预算（≥80%）黄色

---

## 6. 非功能需求

| NFR | 内容 |
|-----|------|
| **性能** | summary 查询 < 500ms；plan grid 加载 < 2s（含 60 个 account × 12 个月） |
| **数据一致性** | plan_line.amount 必须 = sum(breakdowns)（启用 decomposition 时） |
| **审计** | 所有写操作记录 created_by / updated_by / timestamp |
| **幂等性** | 跨服务写入按 (source_service, source_doc_type, source_doc_id, operation) 去重 |
| **国际化** | EN + 中文双语 UI 字段标签 |
| **可用性** | budget-api 不可用时，epms-api 的超预算检测降级为 fail-open（允许提交但日志告警），不阻断业务 |

---

## 7. 实施分期

**全部一次性交付**（2026-05-15 决定）：

- Phase A — budget-api 服务搭建 + 共享目录迁移 + 现有功能切换
- Phase B — Plan 编制功能 + 审批流接入
- Phase C — 因子分解
- Phase D — Ledger 跨服务调用 + Actuals 实时汇总

---

## 8. 与其他模块的接口

| 服务 | 集成点 | 方式 |
|------|------|------|
| **epms-api** | PR 超预算检测 (`_compute_over_budget`) | HTTP GET /balance |
| **epms-api** | Dashboard budget 卡片 | HTTP GET /actuals/summary |
| **expense-api** | Expense paid → 入账 | HTTP POST /book-expense |
| **expense-api** | OA 费用单 budget account 下拉 | HTTP GET /hierarchy |
| **finance-api** | PA approved → commit | HTTP POST /commit |
| **finance-api** | PA cancelled → release | HTTP POST /release |
| **finance-api** | PA processed → actualize | HTTP POST /actualize |
| **approval-api** | Plan 审批（action key=`budget_plan`） | 工作流委托 |
| **portal frontend** | Plan / Dashboard 入口 | 直接调用 budget-api |

---

## 9. 数据迁移

详见 [DESIGN.md](./DESIGN.md) §6。要点：
- 现有 per-CC L1/L2 数据按 code deduplicate 合并为共享目录
- 现有 annual_budget 平均分到 12 月，生成 fiscal_year=2026 的 plans（status=approved）
- 现有 committed / actual_spent 一次性 backfill 进 budget_ledger

---

## 10. 已确认决策

| # | 决策 | 选择 | 日期 |
|---|------|------|------|
| 1 | 数据模型 | 共享 L1/L2 目录 | 2026-05-15 |
| 2 | 微服务归属 | 新建独立 budget-api（:8007） | 2026-05-15 |
| 3 | 交付节奏 | 全部一次性交付 | 2026-05-15 |
| 4 | Actual 实现 | Ledger 模式 | 2026-05-15 |
| 5 | Plan 审批 | 复用 approval-api，action key = `budget_plan` | 2026-05-15 |
| 6 | 文档路径 | budget-api/docs/ 下 PRD.md + DESIGN.md | 2026-05-15 |
| 7 | 预算调整 | 采用 **Plan A — 版本化修订**（每个 (CC, year) 多版本，is_current 标志，approved 父版本走 revise 创建 v2 走完整审批，approval-api post-approve hook 原子翻转 is_current） | 2026-05-18 |
| 8 | 因子上限 | 可配置 `max_factors_per_account`，默认 10，范围 1-50 | 2026-05-15 |
| 9 | Budget Config 归属 | 从 EPMS Admin Panel 迁移到 Portal → FINANCE → Budget Config（Portal 路由 `/budget/config`，使用 `PortalChromeLayout` 共享外壳） | 2026-05-18 |
| 10 | Budget pages 嵌入策略 | EPMS 的 Budget Dashboard / Plans / Catalog 通过 **iframe 嵌入到 Portal**（EPMS 检测 `window.self !== window.top` 自动隐藏自身 sidebar/header），保证 UniOps 主导航不切换 | 2026-05-19 |
| 11 | 财年模型 | 隐式 **会计年度 = 日历年**；移除从未被业务读取的 `fiscal_year_start/end` 字段；新增 `available_fiscal_years` 可配置年份列表 | 2026-05-19 |
| 12 | Plan 删除 | 仅 `status='draft'` 允许 hard-delete（`DELETE /plans/{id}`）；其他状态必须走 workflow reject/cancel 以保留审计 | 2026-05-20 |
| 13 | Plan CSV 互通 | Plan 支持宽表 CSV 导出（任何角色）+ 导入（写角色 + draft/returned 状态）；分解账户跳过导入以保护 `line.amount = Σ breakdowns` 不变量 | 2026-05-19 |
