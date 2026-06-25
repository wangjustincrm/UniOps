# UniOps — 统一 Sprint 计划
**版本：** 1.3  
**日期：** 2026-05-11  
**覆盖范围：** EPMS Phase 1 收尾 + OA Phase 2 全量开发  
**参考文档：** PRD v2.17 · PRD-OA v1.4 · PRD-PORTAL v1.0 · SPRINT-OA.md

---

## 总体进度（2026-05-05 快照）

```
EPMS 收尾  ████████████ 100%  ✅ 全部完成
OA S1     ████████████ 100%  ✅ 全部完成
OA S2     ████████████ 100%  ✅ 全部完成
OA S3     ████████████ 100%  ✅ 全部完成
BF        ████████████ 100%  ✅ 全部完成
AE        ████████████ 100%  ✅ 全部完成
BF2       ████████████ 100%  ✅ 全部完成
OA S4     ████████████ 100%  ✅ 全部完成
```

| Sprint | 状态 | 主要目标 |
|--------|------|---------|
| **EPMS 收尾** | ✅ 完成 | 基础设施、移动端侧边栏、PA 迁移准备、测试环境 |
| **OA S1** | ✅ 完成 | expense-api 骨架、Portal、OA 框架、PA 迁移 |
| **OA S2** | ✅ 完成 | EXP 通用报销 + MIL 里程报销 |
| **OA S3** | ✅ 完成 | PA-DIR + Invoice + OCR |
| **BF** | ✅ 完成 | PR List 权限、PA Submit 按钮、pa_type 修正 |
| **AE** | ✅ 完成 | Approval Engine Action Key 架构重构 + Portal Admin 工作流配置 |
| **BF2** | ✅ 完成 | canApprove 前端审批按钮修复（任务驱动 + WF-UI-001/002） |
| **OA S4** | ✅ 完成 | TRV 差旅 + Admin 配置面板 + CFM 自定义表单 |

---

## 已完成 Sprint 存档

### ✅ EPMS 收尾（E0–E3）

| 任务 | 说明 | 状态 |
|------|------|------|
| E0 | `docker-compose.dev.yml` + `check-health.sh` + `reset-db.sh` | ✅ |
| E1 | EPMS 移动端侧边栏折叠（`mobileOpen` + 汉堡菜单） | ✅ |
| E2 | EPMS PA 迁移准备（Banner + PO 深链 + DocumentChainTree） | ✅ |
| E3 | `run_tests.sh` 使用 `.venv` 路径 | ✅ |

### ✅ OA S1（PA 迁移）

| 层 | 任务 | 状态 |
|----|------|------|
| Backend | expense-api :8006 骨架、PA endpoints、health、stats | ✅ |
| DB | Alembic migrations 0001–0005 | ✅ |
| Portal | PortalHome、LoginPage、AdminPanel | ✅ |
| OA | PA 列表/创建/详情/Direct PA 页面、OA Sidebar/Layout | ✅ |

### ✅ OA S2（EXP + MIL）

| 层 | 任务 | 状态 |
|----|------|------|
| Backend | `expense_claims` 模型 + EXP/MIL CRUD endpoints + policy | ✅ |
| Frontend | ExpenseListPage、ExpenseCreatePage、MilCreatePage、ExpenseDetailPage | ✅ |

### ✅ OA S3（PA-DIR + Invoice OCR）

| 层 | 任务 | 状态 |
|----|------|------|
| Backend | `expense_invoices` 模型 + invoices CRUD + `ocr_service.py` | ✅ |
| Frontend | PaDirectCreatePage（三步 Stepper）、InvoicesPage | ✅ |

### ✅ BF — Bug Fix Sprint

| 任务 | 涉及文件 | 实际变更 | 状态 |
|------|---------|---------|------|
| **BF-1A** PR List 后端权限 | `epms-api/app/api/v1/pr.py` | `list_prs()` 按角色注入 `created_by`（requester）/ `department_ids`（dept_manager / department_admin）/ dept mapping（gm / opm）过滤；PRD PL-001–003 | ✅ |
| **BF-1B** PR List crud 过滤 | `epms-api/app/crud/pr.py` | `get_all()` 新增 `department_ids` 参数，通过 `cost_center → department` 子查询过滤；空列表时直接返回 `[], 0` | ✅ |
| **BF-1C** PR List 前端标题 | `epms/src/pages/pr/PrListPage.tsx` | Requester 角色时页面标题改为 "My Purchase Requisitions" | ✅ |
| **BF-2** OA PA Detail 操作按钮 | `oa/src/pages/pa/PaDetailPage.tsx` | 全量重写：新增 `ActionArea` 组件（draft → Submit+Cancel；returned → Resubmit+Cancel；submitted/in_review → Recall）；调用 `POST /api/v1/pa/{id}/action`，完成后 `invalidateQueries` 刷新 | ✅ |
| **BF-3** PA-DIR pa_type 修正 | `expense-api/app/api/v1/pa.py` | `pa_type="regular"` → `pa_type="PA-DIR"` | ✅ |

### ✅ AE — Approval Engine 重构

| 任务 | 涉及文件 | 实际变更 | 状态 |
|------|---------|---------|------|
| **AE-1A** ExpenseClaim 镜像 Model | `approval-api/app/models/expense.py`（新建） | 映射 `expense_claims` 表；`created_by` 别名 `employee_id` 列；`@property title` | ✅ |
| **AE-1B** engine _DOC_META 扩展 | `approval-api/app/crud/engine.py` | 新增 `pa_dir / exp / mil / trv / cfm` 到 `_DOC_META`（via `_expense_meta()` 工厂）；`_WORKFLOW_DEFAULTS`（8 个 action key 正确默认步骤）；`_post_approve_pa_dir` / `_post_approve_exp`；`reject` / `process` 覆盖 `pa_dir`；`_resolve_meta()` 支持 `cfm_<code>` | ✅ |
| **AE-1C** _DOC_TYPES 扩展 | `approval-api/app/api/v1/approvals.py` | `_STATIC_DOC_TYPES = frozenset(_DOC_META.keys())`；`_is_valid_doc_type()` 额外允许 `cfm_<code>` | ✅ |
| **AE-1D** PA model nullable 修正 | `approval-api/app/models/pa.py` | `po_number: Mapped[str]` → `Mapped[str \| None]`（PA-DIR 无 po_number） | ✅ |
| **AE-2** 启动 seed workflow_defs | `approval-api/app/main.py` | lifespan 钩子调用 `seed_default_workflows()`：读 `CompanyConfig`，补齐缺失 action key，不覆盖已有配置 | ✅ |
| **AE-3A** EXP/MIL 审批委托 | `expense-api/app/api/v1/expenses.py` | `expense_action` endpoint 加 `BearerTokenDep`；非 pay 动作调用 `delegate_action(action_key, ...)`；pay 动作重定向到 `/pay` 端点；新增 `_action_key()` 映射函数 | ✅ |
| **AE-3B** 提取 process_pay | `expense-api/app/crud/expense.py` | 新增 `process_pay(actor_id, actor_name, actor_role, comment)`，从 `process_action` 的 pay 分支提取；旧 `process_action` 保留 | ✅ |
| **AE-4** PA-DIR action key 路由 | `expense-api/app/api/v1/pa.py` | `action_key = "pa_dir" if pa.po_id is None else "pa"` | ✅ |
| **AE-5A** Portal Admin Workflows | `portal/src/pages/admin/AdminPanel.tsx` | 新增 `ApprovalWorkflows` 组件（8 tab × step 编辑器）；`SECTIONS` 加 `workflows`；`CompanyConfig` 加 `workflow_defs` 字段 | ✅ |
| **AE-5B** EPMS Admin 只读 | `epms/src/pages/admin/AdminPanel.tsx` | `case 'workflows'` → `<MovedToPortal section="Approval Workflows" />` | ✅ |

### ✅ BF2 — 审批按钮前端修复

**根因：** 前端 `canApprove` 通过 `user.role === currentNode.role` 字符串比较决定是否显示审批按钮。该方式在以下场景失效：
- `gm_or_opm` 是工作流路由令牌，用户实际存储的是 `"gm"` 或 `"opm"`，永远不等于 `"gm_or_opm"`
- 多角色用户（PRD §1.2，如 GM 兼任部门经理）存储角色与步骤令牌不同

**修复方案（PRD WF-UI-001/002）：** 用任务驱动判断取代角色字符串比较。

| 任务 | 涉及文件 | 实际变更 | 状态 |
|------|---------|---------|------|
| **BF2-1** PrDetailPage canApprove 重写 | `epms/src/pages/pr/PrDetailPage.tsx` | 引入 `useTasks({ is_completed: false })`；`hasApproveTask` = 当前用户是否有该 PR 的 `approve_pr` 任务；`canApprove = hasApproveTask \|\| isAdmin \|\| (!tasksLoaded && roleMatchesStep)` | ✅ |
| **BF2-2** PoDetailPage specialRoleMatch 修补 | `epms/src/pages/po/PoDetailPage.tsx` | 原有 `specialRoleMatch` 缺少 `gm_or_opm`、`finance_bp`、`system_admin` 覆盖；全部补齐 | ✅ |
| **BF2-3** PaDetailPage canApprove 补全 | `epms/src/pages/pa/PaDetailPage.tsx` | 原无 `specialRoleMatch`；新增同 BF2-2 的完整映射 | ✅ |
| **BF2-4** PRD 更新 | `epms/docs/PRD.md` | 新增 §3.3.7（前端 canApprove 规范）；新增 WF-UI-001/002；修正 §3.3.5 system_admin bypass 说明；版本升至 v2.17 | ✅ |

### ✅ OA S4 — 代码审查确认（2026-05-11）

| 层 | 任务 | 涉及文件 | 状态 |
|----|------|---------|------|
| Backend | TRV 白名单 + 超限校验 | `expense-api/app/api/v1/expenses.py`；`crud/expense.py`（meal limits + is_over_budget） | ✅ |
| Backend | TRV 审批委托 | `_action_key()` → `"trv"` → `workflow_defs["trv"]` | ✅ |
| Backend | Policy PATCH 餐费上限 | `expense-api/app/api/v1/policy.py`；`schemas/policy.py` | ✅ |
| Backend | CFM 提交 + action key | `"cfm_<code>"` 路由；`custom_forms` 存于 policy singleton | ✅ |
| Frontend | TrvCreatePage.tsx（397行） | `oa/src/pages/expenses/TrvCreatePage.tsx`；meal limits from API；amber 超限标注 | ✅ |
| Frontend | ExpenseListPage TRV badge/filter | `oa/src/pages/expenses/ExpenseListPage.tsx` | ✅ |
| Frontend | ExpenseConfigPage.tsx（179行） | `oa/src/pages/admin/ExpenseConfigPage.tsx`；HST/MIL/餐费 PATCH | ✅ |
| Frontend | OA Sidebar Admin 入口 | `oa/src/components/layout/AppLayout.tsx`（`ADMIN_NAV`；system_admin 守卫） | ✅ |
| Frontend | CfmAdminPage.tsx（269行） | `oa/src/pages/admin/CfmAdminPage.tsx` | ✅ |
| Frontend | CfmCreatePage.tsx（160行） | `oa/src/pages/expenses/CfmCreatePage.tsx`；基于 `custom_forms` JSON 动态渲染 | ✅ |
| Frontend | App.tsx 路由完整 | `/expenses/new/trv`；`/expenses/new/cfm/:formCode`；`/admin/expense-config`；`/admin/custom-forms` | ✅ |

---

## OA S4 — TRV + 自定义表单 + 收尾（已完成存档）

> 前置：BF + AE 已全部完成。  
> 审批路由通过 AE 建立的 action key 机制，工作流配置入口在 Portal Admin。

---

### S4-A — TRV 差旅报销

| 层 | 任务 | 说明 |
|----|------|------|
| Backend | 确认 `expense_claims` schema 完整 | `travel_from/to/purpose/dest` 字段已在模型中 |
| Backend | `create_expense` 白名单加 `TRV` | 当前仅允许 EXP/MIL，需开放 TRV |
| Backend | TRV 超限校验 | 餐费上限从 `expense_policy_config` 读取，超限时打 `is_over_budget=True` |
| Backend | 审批委托 | `delegate_action("trv", ...)` → approval-api 读 `workflow_defs["trv"]` |
| Frontend | `TrvCreatePage.tsx`（新建） | 日期范围、目的地/事由、按 Category 折叠明细行（交通/住宿/餐费/杂费），超限行 amber 标注 |
| Frontend | `App.tsx` | 新增路由 `/expenses/new/trv` |
| Frontend | `ExpenseListPage.tsx` | 确认 TRV Type Badge 和过滤 tab |

**完成标准：**
- TRV 表单可提交 → 进入 `workflow_defs["trv"]`（默认 `dept_manager → finance_bp`）
- 超限行 amber 标注，上限值从 policy API 读取，非硬编码

---

### S4-B — OA Expense Config（Admin 配置面板）

> OA 前端内的 expense 专属配置页，非 Portal Admin（工作流已在那里）。

| 层 | 任务 | 说明 |
|----|------|------|
| Backend | 确认 `PATCH /api/v1/policy` 支持 TRV category mapping | 字段：`trv_category_mapping` |
| Frontend | OA Admin 费用配置页（新建） | HST 税率 / MIL 费率 / 餐费上限（早/中/晚/杂）/ TRV 类别→预算科目映射 |
| Frontend | OA Sidebar 加 Admin 入口 | 仅 `system_admin` 可见 |

---

### S4-C — 自定义表单（CFM）

| 层 | 任务 | 说明 |
|----|------|------|
| Backend | CFM 表单定义 CRUD | `field_schema` JSON 驱动，存于 `expense_policy_config.custom_forms` |
| Backend | CFM 提交 | `delegate_action("cfm", ...)` 或 `delegate_action("cfm_<code>", ...)` |
| Frontend | CFM 管理页（OA Admin） | 创建/编辑/激活/停用自定义表单 |
| Frontend | CFM 动态渲染器 | 基于 `field_schema` JSON 渲染表单字段 |

**完成标准：** 至少一种 CFM 表单可创建、填写、提交，进入 `workflow_defs["cfm"]` 审批链。

---

### S4-D — 收尾与端对端验证

| 任务 | 说明 |
|------|------|
| 端对端：EXP | 创建→提交→Dept Manager 审批→Finance BP 审批→打款→`actual_spent` 更新 |
| 端对端：MIL | 创建→提交→审批→金额 = km × policy rate |
| 端对端：TRV | 创建（含超限行）→提交→走 `workflow_defs["trv"]` |
| 端对端：PA-PO | EPMS PO 深链→OA PA 创建→Submit→走 `workflow_defs["pa"]` |
| 端对端：PA-DIR | 上传发票→OCR→创建→Submit→走 `workflow_defs["pa_dir"]` |
| Portal Admin 工作流修改验证 | 改 `pa_dir` workflow→保存→下次 PA-DIR 提交使用新配置 |
| PR 权限隔离验证 | Requester A 只看到本人 PR，看不到 Requester B 的 PR |
| TTHW 验证 | `docker-compose.dev.yml` 从零到全部服务健康 < 10 分钟 |
| Portal Header 提取（可选） | `packages/portal-header` 共享包（Phase 3 准备） |

---

## 完整服务地图（Phase 2 完成后）

```
本地开发（docker-compose.dev.yml）          生产环境（分布式）
─────────────────────────────────          ──────────────────────────────────
portal      :5174                          Web Server
epms        :5173                            ├─ Nginx 反向代理
oa          :5175                            ├─ portal + epms + oa (built static)
                                             └─ 域名/SSL 终止
epms-api    :8000
approval-api:8003   ← 所有模块共用          App Server 1
mdm-api     :8002                            ├─ epms-api :8000
finance-api :8004                            ├─ approval-api :8003
file-api    :8005                            ├─ mdm-api :8002
expense-api :8006                            └─ finance-api :8004

postgres    :5432                          App Server 2
redis       :6379                            └─ expense-api :8006

                                           File Server
                                             └─ file-api :8005 + /file-storage/

                                           Database Server
                                             └─ PostgreSQL :5432 (内网)
                                             └─ Redis :6379 (内网)
```

**approval-api action key 覆盖范围（AE 完成）：**

| Action Key | 来源服务 | 文档类型 |
|------------|---------|---------|
| `pr` | epms-api | Purchase Request |
| `po` | epms-api | Purchase Order |
| `pa` | expense-api | PA (PO-linked) |
| `pa_dir` | expense-api | PA (Direct) |
| `exp` | expense-api | General Expense |
| `mil` | expense-api | Mileage Claim |
| `trv` | expense-api | Travel Expense |
| `cfm` / `cfm_*` | expense-api | Custom Forms |

---

## 风险登记

| 风险 | 概率 | 影响 | 缓解 | 状态 |
|------|------|------|------|------|
| EXP/MIL in-flight 记录迁移到 approval-api 时状态不一致 | 中 | 高 | AE-3 切换前确认无 in-flight 记录（dev 环境可重置） | ⚠️ 生产上线前需确认 |
| Portal Admin 修改 workflow_defs 影响 in-flight 单据 | 低 | 高 | WF-P-002：变更只影响新提交（引擎在 submit 时读取当前配置） | ✅ 已在引擎设计中处理 |
| approval-api 读取 `expense_claims` 镜像 model 的 session 隔离 | 中 | 中 | 镜像 model 只读（write 操作仍由 expense-api 执行）；共用 DB | ✅ 已验证架构可行 |
| Claude API OCR 发票识别效果不稳定 | 中 | 中 | 低置信度字段 amber 高亮 + 人工确认机制（S3 已实施） | ✅ 已缓解 |
| TRV 餐费超限校验与 policy 并发读取 | 低 | 低 | policy 在请求时读取，不缓存；超限标记打在 claim 上 | 🔲 S4-A 实施时验证 |
| Docker Compose Windows volume mount 路径问题 | 中 | 中 | E0 完成后已验证通过 | ✅ 已解决 |

---

## 上线检查清单

### ✅ EPMS 收尾（Phase 1 生产就绪）
- [x] `docker compose -f docker-compose.dev.yml up` 全服务启动，健康检查通过
- [x] `./check-health.sh` 全部 ✅（含 /health/db + /health/redis）
- [x] `./run_tests.sh` EPMS 测试全部通过
- [x] 移动端 375px：侧边栏折叠，汉堡菜单可用
- [x] PA 页面：`OA_BASE_URL` 配置后 Banner 显示、深链正常跳转

### ✅ OA S1（PA 迁移完成）
- [x] expense-api `:8006` 健康，`GET /health` 返回 200
- [x] Portal `:5174` 两个模块卡片显示
- [x] OA PA 列表显示 Type Badge（PO teal / Direct grey）
- [x] EPMS PO 详情「Create PA」→ OA PA-PO 表单预填充

### ✅ OA S2（报销上线）
- [x] EXP 完整流程：创建 → 提交 → 审批 → actual_spent 更新
- [x] MIL 金额自动计算（km × policy rate）

### ✅ OA S3（PA-DIR + OCR 上线）
- [x] PA-DIR 三步 Stepper 流程完整可用
- [x] 低置信度字段 amber 高亮，必须逐个确认
- [x] 重复发票 409 Banner 含已有记录链接

### ✅ BF（Bug Fixes）
- [x] Requester 角色 → PR 列表只显示本人 PR（后端强制 `created_by` 过滤）
- [x] Dept Manager → PR 列表按 department 过滤（通过 cost_center 子查询）
- [x] GM/OPM → PR 列表按 dept_gm_opm_mapping 过滤
- [x] Draft PA → Submit for Approval 按钮可见、可点击
- [x] Submitted PA → Recall to Draft 按钮正常
- [x] 新建 PA-DIR → `pa_type = "PA-DIR"`

### ✅ AE（Approval Engine 重构）
- [x] `approval-api` 接受 `pa_dir / exp / trv / mil / cfm` 等 action keys
- [x] `ExpenseClaim` 镜像 model 在 approval-api 中注册
- [x] 首次启动后 `CompanyConfig.workflow_defs` 含全部 8 个 action key 默认配置
- [x] EXP/MIL 提交通过 `delegate_action("exp"/"mil", ...)` 委托 approval-api
- [x] Portal Admin → Approval Workflows tab 含 8 个 action key 的步骤编辑器
- [x] EPMS Admin → Approval Workflows 显示只读提示 + Portal Admin 跳转
- [x] PA-DIR 提交 → `action_key = "pa_dir"` → `workflow_defs["pa_dir"]`
- [x] PA-PO 提交 → `action_key = "pa"` → `workflow_defs["pa"]`

### ✅ BF2（审批按钮前端修复）
- [x] Dept Manager 进入本部门 PR Detail 可见审批按钮（Approve / Return / Reject）
- [x] `gm_or_opm` 路由令牌正确映射至 GM/OPM 用户的 `canApprove`
- [x] PrDetailPage：`canApprove` 改用 `hasApproveTask`（任务驱动）+ system_admin bypass
- [x] PoDetailPage：`specialRoleMatch` 补全 `gm_or_opm`、`finance_bp`、`system_admin`
- [x] PaDetailPage：新增 `paSpecialMatch`（同 PO 模式）
- [x] PRD v2.17 新增 §3.3.7 前端 canApprove 规范（WF-UI-001/002）

### ✅ OA S4（TRV + Admin + CFM，2026-05-11 代码审查确认）
- [x] TRV 表单可提交，进入 `workflow_defs["trv"]`（`delegate_action("trv",...)` + approval-api 路由）
- [x] 超限行（餐费）amber 标注，上限值从 `GET /api/v1/policy` 读取，非硬编码
- [x] OA Admin 费用配置面板（`ExpenseConfigPage.tsx`）：HST/MIL/餐费上限，`PATCH /api/v1/policy` 即时生效
- [x] OA Sidebar Admin 入口仅 `system_admin` 可见
- [x] CFM 自定义表单可在 `CfmAdminPage` 创建/激活，`CfmCreatePage` 动态渲染并提交
- [x] CFM 提交 → `delegate_action("cfm_<code>",...)` → `workflow_defs["cfm"]` 审批链
- [x] PR 权限隔离（BF 已完成）：Requester A 无法看到 Requester B 的 PR
- [x] Portal Admin 工作流修改（AE 已完成）：改配置 → 新提交使用新配置
- [x] TTHW（E0 已完成）：`docker-compose.dev.yml` 全服务启动健康 < 10 分钟
- [ ] Portal Header 提取为 `packages/portal-header`（可选，Phase 3 准备，暂未实施）
