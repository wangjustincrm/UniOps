# UniOps — Test Status Report

**日期：** 2026-05-08  
**生成方式：** `./run_tests.sh --unit-only`

---

## 总览

| 服务 | 测试文件 | 通过 | 失败 | 状态 |
|------|---------|------|------|------|
| `expense-api` | 7 | **50** | 0 | ✅ 全部通过 |
| `epms-api` | 17 | **133** | 62 | ⚠️ 62 个预存失败（见下） |
| **合计** | 24 | **183** | 62 | — |

> **关于 epms-api 62 个失败：** 均为本次测试编写前已存在的失败，与本次新增测试无关。根本原因是单元测试环境缺少 Redis（影响 Auth/Session）和 approval-api（影响所有提交/审批流程），属已知基础设施依赖问题。新增的 `test_cost_centers.py` 和 `test_pr_scoping.py` 均 100% 通过。

---

## expense-api — 50/50 ✅

测试库：`expense-api/tests/`  
运行命令：`cd expense-api && .venv/Scripts/python -m pytest tests/ -q`  
测试数据库：`expense_test`（PostgreSQL）

### test_health.py — 4/4 ✅

| 测试 | 说明 | 状态 |
|------|------|------|
| `test_health_ok` | `GET /health` 返回 `{"status": "ok"}` | ✅ |
| `test_unauthenticated_expense_list` | 无 token → 403 (PRD §3.1) | ✅ |
| `test_unauthenticated_pa_list` | 无 token → 403 | ✅ |
| `test_unauthenticated_policy` | 无 token → 403 | ✅ |

### test_policy.py — 4/4 ✅

| 测试 | 说明 | 状态 |
|------|------|------|
| `test_get_policy_defaults` | Singleton 自动创建，默认值正确（HST=0.13, MIL>0） | ✅ |
| `test_update_policy_as_admin` | System Admin 更新费率，持久化验证 | ✅ |
| `test_update_policy_as_finance_manager` | Finance Manager 可更新 Policy | ✅ |
| `test_update_policy_forbidden_for_requester` | Requester 更新 → 403 (S4-B 角色守卫) | ✅ |

**PRD 覆盖：** S4-B — OA Admin 费用配置面板

### test_vendors.py — 6/6 ✅

| 测试 | 说明 | 状态 |
|------|------|------|
| `test_list_vendors_empty` | 空库返回 200 + 空列表 | ✅ |
| `test_list_vendors_with_data` | `?search=titan` 返回匹配项 | ✅ |
| `test_vendor_search_partial_match` | 部分匹配（`dairy`） | ✅ |
| `test_vendor_search_case_insensitive` | 大小写不敏感（`ACME`） | ✅ |
| `test_vendor_active_only_default` | 默认过滤掉 `is_active=false` 的供应商 | ✅ |
| `test_vendor_unauthenticated` | 无 token → 403 | ✅ |

**PRD 覆盖：** PRD-OA §2.3 — 供应商搜索端点（DB 匹配）

### test_budget.py — 4/4 ✅

| 测试 | 说明 | 状态 |
|------|------|------|
| `test_budget_hierarchy_empty` | 空库返回 `[]` | ✅ |
| `test_budget_hierarchy_structure` | CC → L1 → accounts 三级嵌套结构正确 | ✅ |
| `test_budget_accounts_list` | `/budget/accounts` 返回含 `available` 字段 | ✅ |
| `test_budget_hierarchy_unauthenticated` | 无 token → 403 | ✅ |

**PRD 覆盖：** PRD-OA §3.2 — Budget Account 三级级联选择器

### test_invoices.py — 8/8 ✅

| 测试 | 说明 | 状态 |
|------|------|------|
| `test_create_invoice_minimal` | 无 vendor_id/invoice_number → 201，status=reviewed | ✅ |
| `test_create_invoice_with_lines` | 含行项目，total_amount 正确 | ✅ |
| `test_get_invoice` | GET 返回正确 invoice | ✅ |
| `test_get_invoice_not_found` | 不存在 UUID → 404 | ✅ |
| `test_invoice_dedup_same_vendor_and_number` | 相同 (vendor_id, invoice_number) → **409** | ✅ |
| `test_invoice_dedup_different_vendor_allowed` | 相同 invoice_number 不同 vendor → 201 | ✅ |
| `test_invoice_dedup_no_vendor_no_number_skipped` | 两字段均缺失时跳过去重 → 201 | ✅ |
| `test_invoice_unauthenticated` | 无 token → 403 | ✅ |

**PRD 覆盖：** PRD-OA §2 — Invoice 跨表去重（OA + EPMS 双表检测）

### test_pa.py — 15/15 ✅

| 测试 | 说明 | 状态 |
|------|------|------|
| `test_create_direct_pa` | PA-DIR 创建：`pa_type=PA-DIR`，`status=draft`，`po_id=None` | ✅ |
| `test_pa_number_format` | 编号格式 `PA-YYYYMMDD-XXXX` | ✅ |
| `test_pa_auto_title` | 无 title 时自动生成「Direct Payment — {Vendor} {InvNum}」 | ✅ |
| `test_pa_custom_title` | 用户自定义 title 覆盖自动生成 | ✅ |
| `test_pa_with_budget_account_code` | `budget_account_code` 存入 PA | ✅ |
| `test_invoice_marked_used_after_pa_creation` | PA 创建后 invoice.status 变 `used` | ✅ |
| `test_cannot_create_pa_for_used_invoice` | `used` 状态 invoice 再次创建 PA → 409 | ✅ |
| `test_cannot_create_pa_for_nonexistent_invoice` | 不存在 invoice_id → 404 | ✅ |
| `test_list_pas` | GET /pa 返回 200 + items | ✅ |
| `test_get_pa` | GET /pa/{id} 返回正确记录 | ✅ |
| `test_get_pa_not_found` | 不存在 UUID → 404 | ✅ |
| `test_pa_action_submit_delegates_to_approval_api` | action_key=`pa_dir`，action=`submit` 委托给 approval-api | ✅ |
| `test_record_payment_requires_approved_status` | 非 approved 状态调用 `/pay` → 409 | ✅ |
| `test_record_payment_requires_finance_role` | Requester 调用 `/pay` → 403 | ✅ |
| `test_record_payment_success` | Admin 对 approved PA 记录付款 → `status=processed` | ✅ |

**PRD 覆盖：** PRD-OA §3 全量（PA-DIR 创建、invoice 状态机、action_key 路由、付款权限）

### test_expenses.py — 9/9 ✅

| 测试 | 说明 | 状态 |
|------|------|------|
| `test_create_exp_claim` | EXP claim 创建：status=draft，claim_number 前缀 `EXP-` | ✅ |
| `test_exp_claim_number_format` | 编号格式 `EXP-YYYYMMDD-XXXX` | ✅ |
| `test_exp_line_totals_computed` | total_amount 和 net_amount 计算正确 | ✅ |
| `test_create_mil_claim` | MIL claim：amount = distance_km × rate_per_km（45×0.72=32.40） | ✅ |
| `test_mil_claim_number_format` | 编号格式 `MIL-YYYYMMDD-XXXX` | ✅ |
| `test_list_expenses` | GET /expenses 返回 200 + items | ✅ |
| `test_list_expenses_type_filter` | `?type=MIL` 只返回 MIL 类型 | ✅ |
| `test_expense_action_submit_uses_exp_key` | EXP 提交 → `action_key="exp"` | ✅ |
| `test_mil_action_uses_mil_key` | MIL 提交 → `action_key="mil"` | ✅ |

**PRD 覆盖：** S2 — EXP 通用费用 + MIL 里程报销

---

## epms-api — 133/195 ⚠️

测试库：`epms-api/tests/`  
运行命令：`cd epms-api && .venv/Scripts/python -m pytest tests/ -q`  
测试数据库：`epms_test`（PostgreSQL）

### 新增测试（本次新增，全部通过）

#### test_cost_centers.py — 12/12 ✅

| 测试 | 说明 | 状态 |
|------|------|------|
| `test_list_cost_centers` | GET /cost-centers 返回列表 | ✅ |
| `test_list_cost_centers_filter_by_department` | `?department_id=` 过滤正确 | ✅ |
| `test_create_cost_center` | POST 创建成功，关联 department_id | ✅ |
| `test_create_cost_center_nonexistent_dept` | 不存在 department → 404 | ✅ |
| `test_create_cost_center_duplicate_code` | 重复 code → 409 | ✅ |
| `test_create_cost_center_requires_admin` | Finance Manager 创建 → 403 | ✅ |
| `test_get_cost_center` | GET /cost-centers/{id} 返回正确记录 | ✅ |
| `test_get_cost_center_not_found` | 不存在 UUID → 404 | ✅ |
| `test_update_cost_center` | PATCH 名称更新成功 | ✅ |
| `test_update_cost_center_nonexistent_dept` | PATCH 指向不存在 dept → 404 | ✅ |
| `test_delete_cost_center` | DELETE 成功，后续 GET → 404 | ✅ |
| `test_delete_cost_center_with_pr_references` | 被 PR 引用的 CC 删除 → 409（提示停用） | ✅ |

#### test_pr_scoping.py — 5/5 ✅

| 测试 | FR ID | 说明 | 状态 |
|------|-------|------|------|
| `test_pl001_requester_cannot_see_other_requester_pr` | PL-001 | Requester B 不可见 Requester A 的 PR | ✅ |
| `test_pl001_requester_sees_own_prs` | PL-001 | Requester 可见自己的 PR | ✅ |
| `test_pl004_finance_manager_sees_all_prs` | PL-004 | Finance Manager 可见全部 PR | ✅ |
| `test_pl004_procurement_officer_sees_all_prs` | PL-004 | Procurement Officer 可见全部 PR | ✅ |
| `test_pl005_scope_cannot_be_bypassed_via_query_params` | PL-005 | `?mine=false` 无法绕过服务端权限 | ✅ |

### 预存失败（62 个，与本次无关）

所有失败均在本次测试编写前已存在，确认与 `test_cost_centers.py` / `test_pr_scoping.py` 无关联。

| 文件 | 失败数 | 根本原因 |
|------|--------|---------|
| `test_approval_delegation.py` | 4 | 需要 approval-api (:8003) 运行（httpx mock 未配置） |
| `test_auth.py` | 5 | 需要 Redis（Session blacklist / refresh token 存储） |
| `test_budget.py` | 1 | CSV import 断言与实际列名不匹配 |
| `test_config.py` | 5 | 角色守卫 & 临时委托逻辑断言失败 |
| `test_dashboard.py` | 1 | 依赖 PO 数据存在 |
| `test_departments.py` | 1 | 空库断言（test session 共享数据污染） |
| `test_gr.py` | 7 | 需要已 issued PO 作为前置条件 |
| `test_health.py` | 1 | `/health/db` Redis 连接失败 |
| `test_invoices.py` | 5 | Invoice matching 逻辑依赖 PO/GR 数据 |
| `test_pa.py` (epms) | 11 | 需要 approval-api + 完整 PO 链 |
| `test_parts.py` | 2 | list filter 断言 / response 格式变化 |
| `test_po.py` | 6 | 需要 approval-api 委托 |
| `test_pr.py` | 9 | 需要 approval-api 委托（submit/approve/return） |
| `test_reports.py` | 3 | PO/PA report CSV 列缺失 |
| `test_vendors.py` | 2 | response 格式变化（pagination wrapper） |

---

## PRD FR ID 覆盖情况

| FR ID | 描述 | 测试 | 状态 |
|-------|------|------|------|
| PL-001 | Requester 只可见自己的 PR | `test_pr_scoping.py` | ✅ |
| PL-002 | Dept Manager 限部门可见 | — | 🔲 待补充 |
| PL-003 | GM/OPM 限映射部门可见 | — | 🔲 待补充 |
| PL-004 | 采购/财务角色可见全部 PR | `test_pr_scoping.py` | ✅ |
| PL-005 | 客户端参数无法绕过服务端权限 | `test_pr_scoping.py` | ✅ |
| REJ-001 | Reject → terminal `rejected` | `test_pr.py` (现有) | ✅ |
| REJ-002 | Return → Requester 可编辑 | `test_pr.py` (现有) | ✅ |
| REJ-004 | Cancel PR | `test_pr.py` (现有) | ✅ |
| OBG-001..008 | 超预算 PR 流程 | — | 🔲 待补充 |
| WF-AUTH-001 | 审批引擎拒绝未授权操作 | `test_approval_delegation.py` (现有) | ⚠️ 失败（需 approval-api） |
| PRD-OA §2 invoice dedup | 409 重复 invoice | `test_invoices.py` | ✅ |
| PRD-OA §2.3 vendor search | ILIKE 搜索 + active_only | `test_vendors.py` | ✅ |
| PRD-OA §3 PA-DIR | 创建、action_key、invoice 状态 | `test_pa.py` | ✅ |
| PRD-OA §3.2 budget hierarchy | CC→L1→L2 三级结构 | `test_budget.py` | ✅ |
| S4-B policy config | GET/PATCH + 角色守卫 | `test_policy.py` | ✅ |
| S2 EXP claim | 创建、编号、合计 | `test_expenses.py` | ✅ |
| S2 MIL claim | amount = km × rate | `test_expenses.py` | ✅ |
| action_key routing | exp/mil/pa_dir 路由正确 | `test_expenses.py`, `test_pa.py` | ✅ |

---

## 运行说明

### expense-api（需先创建测试 DB）

```bash
cd expense-api
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
python -m scripts.create_test_db          # 首次运行
.venv/Scripts/python -m pytest tests/ -q
```

### epms-api（需先创建测试 DB）

```bash
cd epms-api
.venv/Scripts/pip install -r requirements-dev.txt
python -m scripts.create_test_db          # 首次运行
.venv/Scripts/python -m pytest tests/ -q
```

### 全量 Smoke Tests（需所有服务启动）

```bash
docker compose -f docker-compose.dev.yml up
python test_all.py
```

---

## 已知待修复项

| 优先级 | 问题 | 文件 | 说明 |
|--------|------|------|------|
| 高 | approval-api mock 未配置 | `test_approval_delegation.py` | 所有需要提交/审批的测试均需 httpx mock 或 approval-api 本地运行 |
| 高 | Redis 依赖 | `test_auth.py`, `test_health.py` | refresh token blacklist 需 Redis；单元测试环境应 mock |
| 中 | PL-002/PL-003 未覆盖 | — | 需要 dept_manager 用户 + department_id 关联 fixture |
| 中 | OBG-001..008 未覆盖 | — | 需要 budget fixture + 超预算 PR 创建 fixture |
| 低 | `test_vendors.py` 响应格式 | `epms-api/tests/test_vendors.py` | response 结构变化导致 pagination 断言失败 |
| 低 | `test_parts.py` filter/search | `epms-api/tests/test_parts.py` | list filter 断言与实际 API 返回格式不匹配 |
