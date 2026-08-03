# OA 出差申请（Travel Application / TRA）+ 差旅报销闸门 — 设计文档

**日期**: 2026-08-03
**分支**: `feature/oa-travel-application`（基于 `origin/main` = 6beac58）
**参照纸质单**: 《COP-FC-003 Attachement 002 Application of Travel》（出差审批表，中英双语）

## 1. 背景与目标

公司差旅流程为：**先做出差申请 → 申请审批通过 → 才能基于该申请做差旅费报销**。

当前 OA 报销模块（`expense-api` + `oa` 前端）已有四种单据：EXP（普通报销）、MIL（里程）、**TRV（差旅报销）**、CFM（自定义表单），审批统一委派 `approval-api`。现状缺失"出差申请"这一前置环节，且 TRV 报销无任何前置约束。

**目标**：
1. 参照纸质《出差审批表》，在 OA 里新增**出差申请（TRA）**单据类型及录入表单。
2. TRA 走审批链 `部门负责人 → 财务领导 → 总经理`。
3. 差旅报销 TRV **必须选择一张已批准的 TRA** 才能提交（硬性强制，仅对新建 TRV 生效）。

## 2. 关键决策（已与用户确认）

| # | 决策 | 结论 |
|---|---|---|
| Q1 | TRA 实现方式 | **A** — 新单据类型，复用 `expense_claims` 表 + 审批引擎 + Task Inbox + 附件基础设施 |
| Q2 | TRA 审批链 | **A+C** — 默认 `dept_manager → finance_manager → gm`，配置化；HR 确认忽略（无 ADP 对接，线下沟通） |
| Q3 | TRA↔TRV 关系 | **A** — 一张 TRA → 多张 TRV |
| — | 报销资格 | **(b)** — TRA 的"出差人员"名单成员均可报销（含创建人，只要在名单里） |
| — | 预估金额 | 不做金额上限校验；交通住宿仅勾选、无金额 |
| Q4 | 出差人员字段 | **只多选系统用户**；外部同行人员（司机/高管外部朋友等）写入**备注**字段 |
| Q5 | UI 入口 | **A** — 独立「Travel Applications」列表页（侧边栏独立入口） |
| Q6 | PDF | **A** — 生成中英双语《出差审批表》PDF，签字栏用审批记录填充 |
| Q7 | 报销闸门严格度 | **A** — 硬性强制，无豁免；仅对新建 TRV 生效，历史/草稿老单不动 |

## 3. 数据模型（expense-api）

### 3.1 复用 `expense_claims` 表（`claim_type='TRA'`）

TRA 无金额，`total_amount/tax_amount/net_amount` 恒为 0。字段映射：

| 纸质单栏目 | 复用字段 | 说明 |
|---|---|---|
| 部门 | `department_id` / `department_name` | 默认创建人部门，可改 |
| 申请时间 | `submission_date` | 默认今天 |
| 出差地点 | `travel_destination` | 文本 |
| 时间 起/至 | `travel_from_date` / `travel_to_date` | 日期 |
| 具体事由及依据 | `purpose`（String 500） | 出差事由 |
| 备注（外部同行人员说明） | `notes`（Text） | 自由文本 |

### 3.2 新增列（迁移到 `expense_claims`，仅 TRA 语义使用）

- `transport_modes JSONB NOT NULL DEFAULT '[]'` — 勾选数组，取值集合：
  `airplane`（飞机）/ `train`（火车）/ `ship`（轮船）/ `car`（汽车）/ `accommodation`（住宿）/ `meal`（餐饮）/ `other`（其他）
- `leave_from_date DATE NULL` — 请假时间（选填）
- `leave_to_date DATE NULL` — 销假时间（选填）

> 说明：这三列对 EXP/MIL/TRV/CFM 无影响（保持默认/NULL）。遵循"镜像模型忠于物理表"，模型与迁移逐列对齐。

### 3.3 新增子表 `expense_travelers`（复用 line_items/trip_items 子表模式）

```
expense_travelers
  id           UUID PK
  claim_id     UUID FK → expense_claims(id) ON DELETE CASCADE, indexed
  user_id      UUID            -- 系统用户，用于报销资格校验
  user_name    String(255)     -- 快照姓名（显示用）
  seq          Integer         -- 名单顺序
```

`人数` = travelers 行数，前端自动计算，不落库。

### 3.4 TRV 新增引用列

- `travel_application_id UUID NULL` — 引用已批准 TRA 的 `expense_claims.id`（同表软引用，无跨表 FK 约束以保持与现有风格一致；或加自引用 FK，见开放问题）。

## 4. 审批引擎（approval-api `app/crud/engine.py`）

新增 `tra` 动作键：

- `_DOC_META["tra"] = _expense_meta("TRA")`
  - 自动得到任务键 `approve_tra` / `revise_tra`，状态机 draft→submitted→in_review→approved，valid_* 与其他 expense 一致。
- `_DEFAULT_STEPS["tra"] = [`
  `  {"id": "dept_manager",  "role": "dept_manager",    "label": "Department Manager"},`
  `  {"id": "finance_mgr",   "role": "finance_manager", "label": "Finance Manager"},`
  `  {"id": "gm",            "role": "gm",              "label": "General Manager"},`
  `]`
- `_POST_APPROVE["tra"]` = 轻量回调：**无预算/记账动作**。可选：给 travelers 名单成员发"出差申请已批准，可发起差旅报销"通知（如实现，复用现有通知渠道）。
- engine 文档字符串的 action-key 清单加入 `tra`。

expense-api 侧同步：
- `_action_key`：`{"EXP":"exp","MIL":"mil","TRV":"trv","TRA":"tra"}`
- `_workflow_key` / `_BASE_WF_KEY`：加 `TRA→"tra"`
- `_INBOX_STEP_ROLES`（TRA 专用 step→role，用于任务箱 my_actions）：
  `0:{dept_manager, system_admin}, 1:{finance_manager, system_admin}, 2:{gm, system_admin}`
- 列表可见性 `_roles_for`/参与判定复用现有逻辑，`tra` 纳入 workflow 遍历。

## 5. 报销闸门（TRV）

### 5.1 后端 `create_claim`（expense-api）

当 `claim_type='TRV'`：
- `travel_application_id` **必填**（缺失 → 422）。
- 校验目标 TRA：存在、`claim_type='TRA'`、`status='approved'`（否则 400/404）。
- 校验**当前用户 `user_id` 在该 TRA 的 `expense_travelers` 名单内**（否则 403）。
- **仅对新建 TRV 生效**；已存在的 TRV（历史/草稿）不受影响。

### 5.2 新增只读端点

`GET /api/v1/travel-applications?eligible=true`
- 返回 `status='approved'` 且当前用户是 traveler 的 TRA 列表（供 TRV 创建页下拉）。
- 无 `eligible` 参数时按现有列表可见性规则返回 TRA（供列表页）。

## 6. 前端（oa）

### 6.1 新增页面

- **`TraCreatePage`**（`/travel/new`）：
  - 字段：部门（默认创建人，可改）、申请时间、**出差人员（多选系统用户，带搜索）**、出差地点、时间起/止、具体事由（多行）、请销假时间（起/止，选填）、**交通住宿（多选勾选：飞机/火车/轮船/汽车/住宿/餐饮/其他）**、备注（外部人员说明）。
  - 人数只读 = 出差人员数。
  - 提交 → 创建 TRA draft → 详情页；提交审批复用现有 expense submit 动作。
- **`TravelApplicationsListPage`**（`/travel`）：只列 TRA，带状态筛选 + "我的/待我审批"筛选，复用现有列表组件风格与当前审批环节显示。

### 6.2 详情页

复用 `ExpenseDetailPage`，加 TRA 分支渲染：出差人员名单、交通住宿勾选、请销假时间、备注、**关联的报销单（TRV）列表**、生成/重新生成 PDF 按钮。

### 6.3 TRV 创建页改造 `TrvCreatePage`

- 顶部新增**必选下拉「Travel Application」**，数据源 `GET /travel-applications?eligible=true`。
- 选中后自动带出 `destination` / `from_date` / `to_date` / `purpose`（可覆盖）。
- 未选不允许提交（前端拦截 + 后端强制）。
- 提交 body 增加 `travel_application_id`。

### 6.4 路由 / 导航 / 权限

- `oa/src/app/routes.tsx` 加 `/travel`、`/travel/new` 路由（tab 配置）。
- Portal `navConfig` 加「Travel Applications」入口（anyPermission 门禁按现有 expense 模块规范）。
- 遵循 UI 全英文、PortalChromeLayout、浮层选择器 createPortal 等既有规范。

## 7. PDF（expense-api）— 仅英文

> **重要**：expense-api 当前**无任何 PDF 基础设施**（无 reportlab/weasyprint，requirements 里没有）。PO/PR/PA 的 PDF 在 epms-api（用 `reportlab==4.2.5` + `pdf_template.py`，纯英文单据、字体 Helvetica）。所以 TRA PDF 是给 expense-api **首次引入 PDF 渲染**。

**决策（已确认）**：TRA PDF **只出英文，不做中英双语**。标签取纸质单的英文栏名（Application of Travel / Department / Time of Application / Staff / Number of Persons / Location / Reasons and Explanation / Period off / Transportation and Accommodation / Head of Department / Approved by Finance Department / Approved by General Manager）。

新增 `app/services/pdf_tra.py`（参照 epms-api `pdf_po.py`/`pdf_pr.py` 的 reportlab 用法，非像素级还原纸质格式，只参照内容）：
- **依赖**：expense-api `requirements.txt` 增加 `reportlab==4.2.5`（与 epms-api 对齐版本）。
- **字体**：标签与正文默认 Helvetica（与现有 epms-api PDF 一致，无双语、无需注册 CJK 字体）。
- 内容：部门、申请时间、出差人员+人数、出差地点、时间、事由、请销假时间、交通住宿勾选、备注。
- 三个签字栏（Head of Department / Finance / General Manager）用 `expense_approval_events`（approval-api 写入的审批记录）填充审批人姓名与时间。
- 详情页提供"生成/重新生成 PDF"，附件存储复用现有 expense attachments 机制。
- API：新增 `POST /api/v1/expenses/{id}/regenerate-pdf`（或 TRA 专用端点），生成后作为附件挂到该 TRA。

> **数据侧中文风险（待定，低成本兜底）**：标签虽全英文，但用户填的数据（出差人员姓名、出差地点、事由、备注）可能含中文字符，纯 Helvetica 会渲染成方框。兜底方案：仅对这些**数据字段**注册 reportlab 内置 CID 字体 `UnicodeCIDFont('STSong-Light')`（无需携带 TTF，零额外依赖），标签仍走 Helvetica。默认**采用此兜底**以避免方框；若你确定数据也全英文，可去掉。

## 8. 权限与边界

- **谁能建 TRA**：默认所有能建报销的角色（`requester` 等），沿用现有 expense create 权限门禁。
- **出差人员**：仅系统用户；外部人员写备注。
- **编辑/撤回**：沿用现有 draft/returned 可改、submitted 后锁定规则。
- **报销资格**：TRA travelers 名单成员（含创建人若在名单）。
- **金额**：TRA 无金额；TRV 金额照旧走现有预算/超预算逻辑。

## 9. 改动范围汇总

| 服务 | 改动 |
|---|---|
| `expense-api` | 模型（expense.py 加列 + 新子表 travelers）、Alembic 迁移、schemas、crud（create/list + TRV 闸门）、API（travel-applications 端点 + regenerate-pdf）、`pdf_tra.py`、`requirements.txt` 加 `reportlab==4.2.5`、`_action_key`/`_workflow_key`/`_INBOX_STEP_ROLES` |
| `approval-api` | `engine.py`：`_DOC_META["tra"]` + `_DEFAULT_STEPS["tra"]` + `_POST_APPROVE["tra"]`(可选通知) + 文档串 |
| `oa` 前端 | `TraCreatePage`、`TravelApplicationsListPage`、`ExpenseDetailPage` TRA 分支、`TrvCreatePage` 闸门、routes、navConfig |
| Portal | navConfig 入口（如需） |

## 10. 开放问题 / 后续

- `travel_application_id` 是否加自引用 FK（`expense_claims.id`）以获得引用完整性，还是保持软引用（与现有跨服务软引用风格一致）。倾向**加库内自引用 FK**（同库同表，无跨服务问题）。
- TRA 批准后给 travelers 发通知：本期做与否（低优先，可后续加）。
- 交通住宿勾选目前纯信息展示，不驱动任何逻辑（无金额）。

## 11. 非目标（YAGNI）

- 不对接 HR/ADP 系统，不做请销假的独立审批环节。
- 不做出差预估金额、预算预留。
- 不改动 EXP/MIL/CFM 现有行为。
- 不迁移/回填历史 TRV（老单无需关联 TRA）。
