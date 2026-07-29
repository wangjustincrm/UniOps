# EPMS New PR — 部门选择器 + 部门驱动审批

**日期**: 2026-07-28
**分支**: `feature/pr-department-selector`（worktree `c:/Project/uniops-pr-dept`，基于 origin/main `ce71376`）
**状态**: 设计已确认，待写实现计划

---

## 1. 背景与目标

目前 EPMS 的 New PR 存在两个限制：

1. **无法跨部门代提**：Cost Center（预算科目的顶层）下拉写死按**登录用户自己的部门**筛选（[`PrCreatePage.tsx:62`](../../../epms/src/pages/pr/PrCreatePage.tsx) 传 `department_id: user?.department_id`）。有跨部门协助做 PR 申请的实际需求，但当前做不到。
2. **部门不是一等公民**：PR 上没有独立的部门字段，`department_name` 是从选中的 Cost Center 派生的字符串快照。审批引擎更是**完全不读 PR 上的部门**——它取 `PR.created_by`（创建人）再读 `User.department_id` 来决定部门相关审批人。

本需求引入 PR 上的**独立部门字段**，让制单人可以在 New PR 上显式选择部门（默认自己的部门），并让该选择驱动预算科目筛选、后续审批人解析和 PDF 展示。

### 四条原始需求

1. 在 Preferred Vendor 和 Budget Account 之间增加一个部门选择器，默认值为当前 requester 所在部门；后续可人工选择不同部门（跨部门代提）。
2. 选完部门后，Budget Account 按选择的部门筛选。
3. 后续所有审批涉及的逻辑调整：之前按 `PR.Requester` 所在部门决定后续审批人，改为按 PR 里选择的部门决定。
4. 系统自动生成的 PR PDF 带上部门。

---

## 2. 已确认的设计决策

| # | 决策点 | 结论 |
|---|---|---|
| D1 | 部门存储方式 | PR 上**独立持久字段 `department_id`**（FK → departments），非派生 |
| D2 | 选部门权限 | **任何人可选任意部门**；默认 = 当前登录用户部门 |
| D3 | Type 1 覆盖 | **所有 PR（含 Type 1 无预算/无 Cost Center）都可选部门** |
| D4 | 旧数据回填 | 有 Cost Center 的从 `cc.department` 派生；无 cc 的从 `created_by` 的 `user.department_id` |
| D5 | 在途审批 | **不主动重算存量在途**，只影响新 PR |
| D6 | 编辑改部门 | 部门只在 **draft/未提交**时可改；提交后只读 |
| D7 | 审批边界 | 「部门角色」（`dept_manager`、`gm_or_opm`、`director`）跟选择的部门走；`supervisor`（个人直属上级）保持按创建人 |

### 关键系统现实（设计前置）

- **Cost Center 一对一属于某部门**：`CostCenter.department_id` 是非空 FK（[`epms-api/app/models/cost_center.py:18`](../../../epms-api/app/models/cost_center.py)）。
- **Budget Account（L1/L2 科目）本身无部门归属**，是全局共享目录；预算选择器里唯一带部门的层级就是最顶层的 Cost Center。所以「Budget Account 按部门筛」= Cost Center 按部门筛。
- **approval-api 的 PR 是 epms-api 同库同表的镜像**：两个 model 都 `__tablename__ = "purchase_requests"`（[`approval-api/app/models/pr.py:14`](../../../approval-api/app/models/pr.py) docstring 明确 "Read-write mirror of EPMS purchase_requests"）。因此 `department_id` 加列迁移**只归属 epms-api**，approval-api 只需在其 model 加列声明即可读取，**无需数据同步**。
- **审批链路 PA→PO→PR 通过 `_routing_user_id` 终结于 PR**（[`approval-api/app/crud/engine.py:421-455`](../../../approval-api/app/crud/engine.py)）。让 PR 的部门解析改由 PR.department_id 驱动后，PO/PA 审批自动继承，无需给 PO/PA 加字段。

---

## 3. 详细设计

### 3.1 数据模型 & 迁移（epms-api）

**Model 变更** — [`epms-api/app/models/pr.py`](../../../epms-api/app/models/pr.py)：
- `purchase_requests` 新增列：
  ```python
  department_id: Mapped[uuid.UUID | None] = mapped_column(
      UUID(as_uuid=True), ForeignKey("departments.id", ondelete="RESTRICT"),
      nullable=True, index=True
  )
  ```
- `department_name` 保留（denormalized 快照，PDF/列表读它），但其**来源改为从 `department_id` 派生**（见 3.4）。

**Alembic 迁移**（挂当前 head，先核 `alembic heads`）：
- `op.add_column` 加 `department_id` + FK + index。
- **同一迁移的 data migration 段做回填**（幂等，只填 `department_id IS NULL`）：
  1. 有 `cost_center_id` 的行：`department_id = (SELECT department_id FROM cost_centers WHERE id = pr.cost_center_id)`。
  2. 仍为 NULL（无 cc 或 cc 无部门）的行：`department_id = (SELECT department_id FROM users WHERE id = pr.created_by)`。
  3. 顺带把 `department_name` 对齐为 `department_id` 对应部门名（保证与新来源一致）。
- 回填用批量 UPDATE ... FROM，不逐行。

**approval-api model** — [`approval-api/app/models/pr.py`](../../../approval-api/app/models/pr.py)：
- 加 `department_id: Mapped[uuid.UUID | None]` 列声明（同表，读用）。**不加迁移**（表由 epms-api 拥有）。

### 3.2 前端 New PR 页（`PrCreatePage.tsx`）

- **新增 Department 选择器**：在 Vendor 块结束（line 500）和 Budget Account 块开始（line 502）之间插入一个 `<select>`，用现有 `useDepartments()` hook 填充（filter `is_active`），参照 [`PrListPage.tsx:183-188`](../../../epms/src/pages/pr/PrListPage.tsx) 的既有 department `<select>` 模式（当前无共享 `<DepartmentSelect>` 组件，各页内联 select，本次沿用内联以保持一致）。
- **State**：`selectedDepartmentId`，初始化为 `user?.department_id`。
- **驱动 Cost Center 筛选**：把 line 61-64 的 `useCostCenters({ department_id: user?.department_id, ... })` 改为 `department_id: selectedDepartmentId`（响应式）。
- **一致性规则**：`onChange` 改部门时，清空 `selectedCostCenter/selectedCostCenterId/selectedL1/selectedL2`（已选 cc 属于旧部门）。
- **提交 payload**：`createPr.mutateAsync({...})` 和 `handleDraftSave` 加 `department_id: selectedDepartmentId`。
- 该选择器对所有 Type 都显示（D3），不受 `requiresBudget` 门禁影响（它在 Budget Account 块之外，之前）。

**契约层同步**：
- [`epms/src/services/pr.ts`](../../../epms/src/services/pr.ts) `CreatePrBody` 加 `department_id?: string`。
- [`epms-api/app/schemas/pr.py`](../../../epms-api/app/schemas/pr.py) `PrCreate` / `PrUpdate` 加 `department_id: uuid.UUID | None`。
- [`epms-api/app/crud/pr.py`](../../../epms-api/app/crud/pr.py) `create()`/`update()` 存 `department_id`，并据它派生 `department_name`（见 3.4）。

### 3.3 前端编辑页（`PrEditPage.tsx`）

- 同样渲染 Department 选择器。
- **仅 draft 状态可编辑**（D6）：非 draft 时渲染为只读文本（显示 `department_name`），选择器 disabled。
- 提交 payload 在 draft 分支才带 `department_id`。

### 3.4 部门名派生逻辑（epms-api `crud/pr.py`）

- 现有 `_resolve_names(db, vendor_id, cost_center_id)` 从 cost center 派生 `department_name`。
- 改为：**`department_name` 优先由 `department_id` 派生**。签名调整为接收 `department_id`；若 `department_id` 提供，`department_name = departments[department_id].name`；未提供时（理论上不会，前端总有默认）回退 cost center 派生以兼容。
- `create()`/`update()` 调用点相应传 `department_id`。

### 3.5 审批引擎改动（approval-api，需求 #3 核心）

引擎中「按部门」解析审批人的函数改为**优先读 PR.department_id**；PR.department_id 为 NULL（旧数据未回填的极端情况）时**回退旧逻辑**（读 routing user 的 `User.department_id`）。

受影响函数（[`approval-api/app/crud/engine.py`](../../../approval-api/app/crud/engine.py)）：
- `_get_dept_manager_id` — dept_manager 解析。
- `_resolve_gm_or_opm` — gm_or_opm 解析。
- `_resolve_director`（D7：director 按部门走）。
- `_build_role_map` / `_actor_can_approve` / `_create_approve_task` / `_resolved_assignee_for_step` — 这些消费上面的部门值，改动集中在「部门从哪来」。

**实现方式**：引入一个 helper，例如 `_routing_department_id(db, doc_type, doc)`：
- 对 PR（及经 `_routing_user_id` 链路解析到的 PO/PA）：取链路终点 PR 的 `department_id`。
- PR.department_id 为 NULL → 回退到 `_routing_user_id` 的 `User.department_id`（现有行为）。
- 各「按部门」函数从读 `User.department_id` 改为调用此 helper。

**保持不变**：
- `_resolve_supervisor`（D7：个人直属上级仍按创建人 `supervisor_id`）。
- `_routing_user_id` 本身仍返回 user（供 supervisor 链和回退用）。

**resync-inflight**：走同一套解析函数，改后自动一致；但按 D5 **不主动**对存量在途跑（`POST /routing/resync-inflight` 仍存在，供需要时手动触发，不在本次自动执行）。

**次要/重复路径**（需实现时确认是否 live）：
- epms-api 内 legacy 路由 `crud/pr.py` `_get_dept_manager_id`（live 端点已 `delegate_action` 到 approval-api，[`epms-api/app/api/v1/pr.py:195`](../../../epms-api/app/api/v1/pr.py)）——确认不 reachable 后不改，或对齐。
- 无状态 resolver `approval-api/app/api/v1/resolution.py`——确认是否用于 PR 路由，用到则对齐。

### 3.6 PDF（epms-api `pdf_pr.py`）

- **无需改渲染**：`generate_pr_pdf` 已在 line 106 打印 `department_name`。
- 只需 3.4 保证 `department_name` 来自 `department_id`，PDF 自动显示正确部门。

---

## 4. 测试

**approval-api**：
- 扩展 [`tests/test_engine_dept_manager_routing.py`](../../../approval-api/tests/test_engine_dept_manager_routing.py)：
  - PR.department_id ≠ 创建人部门时，dept_manager/gm_or_opm/director 按 **PR.department_id** 解析（跨部门代提核心用例）。
  - PR.department_id 为 NULL 时回退到创建人部门。
  - supervisor 仍按创建人（不受部门选择影响）。
  - PO/PA 经链路继承 PR.department_id。

**epms-api**：
- PR create/update 存 `department_id`，`department_name` 由它派生正确。
- Type 1（无 cost center）也能存部门。
- `PrUpdate` 在 draft 改部门生效。

**前端**（若有前端测试基线，否则手动验证）：
- 改部门清空 cost center 级联的交互。
- 默认值 = 登录用户部门。

**回填迁移**：
- 迁移在测试库跑通；回填后有 cc 的 = cc 部门、无 cc 的 = 创建人部门、幂等重跑无变化。

---

## 5. 影响面 / 非目标

**改动文件汇总**：
- epms-api：`models/pr.py`、`schemas/pr.py`、`crud/pr.py`、新 alembic 迁移。
- approval-api：`models/pr.py`、`crud/engine.py`、`tests/test_engine_dept_manager_routing.py`。
- epms 前端：`pages/pr/PrCreatePage.tsx`、`pages/pr/PrEditPage.tsx`、`services/pr.ts`。

**非目标（YAGNI）**：
- 不给 PO/PA 加独立部门字段（审批链路继承 PR）。
- 不改 PO/PA 详情页 UI。
- 不做部门级选择权限模型（D2：任何人可选任意）。
- 不主动重算存量在途审批（D5）。
- 不引入共享 `<DepartmentSelect>` 组件（沿用内联 select）。

**风险 / 待实现时验证**：
- approval-api 是否有独立 alembic 链——`department_id` 列由 epms-api 迁移创建，approval-api model 加声明即可；确认 approval-api 启动不因缺列迁移报错（列在同一物理表已存在）。
- 迁移前核 `alembic heads`（避免双 head，见项目既有踩坑）。
- 确认 epms-api legacy `crud/pr.py.execute_action` 是否仍 reachable（测试/其他调用），决定是否需同步改。
