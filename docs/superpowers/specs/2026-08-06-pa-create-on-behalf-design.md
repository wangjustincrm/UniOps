# EPMS — Procurement Officer 代建 PA(Create PA on behalf)

- 日期:2026-08-06
- 分支:`feature/pa-create-on-behalf`(worktree `c:/Project/uniops-pa-onbehalf`,基 `main=c323f64`)
- 相关记忆:`project_uniops_erp_pa_officer_role`、`project_uniops_pa_receipt_gate`、`feedback_epms_access_control`、`project_uniops_gr_create_additional_role_403`

## 1. 目标

让 **Procurement Officer(`procurement_officer`)** 能够替任何人创建 Payment Application:

1. 对**任意** PO 建 PA,不受"只能付自己 PR 对应的 PO"的归属限制。
2. **审批流不变** —— 仍由关联 PR 的 Requester(及其部门)决定审批人。
3. **不因此收到邮件提醒** —— `create_pa` 任务仍然只派给 PR Requester,officer 不进任务池、不进每日提醒。officer 收到的其他通知(PA 被退回的 revise 任务、审批结果等)保持正常。

### 非目标

- 不改 PA 的审批工作流定义、不改审批引擎的路由算法。
- 不改 `create_pa` 任务的派发对象。
- 不新增角色、不做 identity 角色表扩展(复用现有基础角色 `procurement_officer`)。
- 不改 OA 的 Direct PA(`pa_dir`)流程。

## 2. 现状(实测结论)

| 关注点 | 现状 | 是否需要改 |
|---|---|---|
| 审批人解析 | `approval-api/app/crud/engine.py` 的 `_routing_user_id` / `_routing_department_id`:`pa` 显式沿 `PA → po_id → PO.pr_id → PR.created_by / PR.department_id` 解析,**与 `doc.created_by` 无关**(注释明写 "PAs are created by AP staff, so `doc.created_by` is the wrong department") | ❌ 零改动,加测试锁住 |
| `create_pa` 任务派发 | `epms-api/app/api/v1/invoices.py::_create_or_renotify_create_pa`(实时)与 `app/crud/task.py::_backfill_create_pa_tasks`(兜底)都派给 PR Requester;只有 NC 且无 PR 的 PO 才派给 `erp_pa_officer` 池 | ❌ 零改动,加测试锁住 |
| PA 可见性 | `epms-api/app/core/access_scope.py::_RESTRICTED_ROLES` 不含 `procurement_officer` → 天然 unrestricted,可见全部 PO/PA | ❌ 无需改 |
| 视图权限 | `identity-api/scripts/seed_authz.py::DEFAULTS` 已给 `procurement_officer` 全套 `view_pr/po/gr/invoice/pa` | ❌ 无需改 |
| **创建权限** | `epms.pa.write` 默认角色集为 `system_admin / finance_bp / finance_manager / ap_clerk / requester / erp_pa_officer` —— **不含 `procurement_officer`** | ✅ 要改 |
| **归属 403** | `epms-api/app/api/v1/pa.py::create_pa`:当 **JWT 基础角色 == `requester`** 时校验 PO 归属,逃逸口只认"持 `erp_pa_officer` 且 PO 为 NC 无 PR" | ✅ 要改 |
| **前端入口** | `epms/src/pages/pa/PaListPage.tsx` 的 `canCreate` 硬编码角色数组(已含 `procurement_officer` / `procurement_manager`,但后端会 403 —— 既有不一致) | ✅ 要改 |
| PA 创建页 PO 下拉 | `epms/src/pages/pa/PaCreatePage.tsx` 的 `requesterScoped` 仅在 `user.role === 'requester'` 时收窄;officer 不受限 | ❌ 无需改 |

**副作用(已知且接受)**:officer 建 PA 后,PR Requester 那条 `create_pa` 任务会被 `task.py::_complete_stale_create_pa_tasks` 自动完成,Requester 不会被继续催办。

## 3. 设计

### 3.1 授权:矩阵驱动 + 迁移里默认授予

新增 identity 迁移 `0005_procurement_officer_pa_write`(`down_revision = "0004_erp_pa_officer_role"`,当前唯一 head),幂等地把 `epms.pa.write` 授给 `procurement_officer`:

- `INSERT INTO permission_defs(...) VALUES ('epms.pa.write','epms','Create / Edit PAs',102) ON CONFLICT (key) DO NOTHING`(防新库缺 def 行导致 FK 失败,照抄 0004 做法)
- `INSERT INTO role_permissions(role_code,permission_key) VALUES ('procurement_officer','epms.pa.write') ON CONFLICT DO NOTHING`
- `downgrade()` 只删这一条 grant 行,不动 `permission_defs`/`role_defs`(它们由 seed 共享)

同步一处常量以免漂移:

- `identity-api/scripts/seed_phase2_keys.py::PHASE2_DEFAULTS["epms.pa.write"]` 追加 `"procurement_officer"`(新库 seed 走这里,必须与迁移一致)

**`identity-api/scripts/verify_gate_parity.py` 保持不动**:它的 `PHASE2_DEFAULTS` 是 phase-2 割接时刻的**冻结快照**,模块 docstring 明写"割接后任何一次有意的矩阵编辑都会让它对该 role×key 打印 DIFF —— 那是预期且正确的后果,不是回归"。把新授权补进去会让它变成"拿一份字典和自己比",丧失比对价值。运行它出现 `DIFF role=procurement_officer key=epms.pa.write old=False new=True` 是本次改动的**正确表现**。

不加 lock:这个能力是可撤销的业务授权,admin 应能在 Access Control 矩阵里关掉。

> 迁移里直接授权(而非只开矩阵入口)是刻意的:上一次 Create GR 改成矩阵权限后,因矩阵未勾导致原本能建 GR 的人 403(见记忆 `project_uniops_gr_create_additional_role_403`)。这里部署完即生效,无需人工去 Portal 勾。

### 3.2 后端:放开归属 403 的逃逸口

`epms-api/app/api/v1/pa.py::create_pa` 中那段"基础角色为 requester 时校验 PO 归属"的检查,逃逸条件从

```
po.pr_id is None and po.source == "nc" and "erp_pa_officer" in effective_roles
```

扩展为**再或上**

```
"procurement_officer" in effective_roles     # 任意 PO,无 NC / 无 PR 限制
```

即:抽出一个 `_may_create_pa_on_behalf(roles, po)` 判定,含两条规则(erp_pa_officer + NC 无 PR PO;procurement_officer + 任意 PO),其余保持 403。

**为什么必须改**:如果把 Procurement Officer 作为**附加角色**授给一个 `users.role == 'requester'` 的人,`epms.pa.write` 矩阵会放行(effective_permissions 取角色并集),但这段基于 JWT 基础角色的归属检查仍会 403。改完后"基础角色是 procurement_officer"和"附加角色是 procurement_officer"两种授权方式行为一致。

**不放宽的部分**:纯 `requester`(无上述任一角色)对他人 PO 建 PA 仍然 403。

### 3.3 前端:入口改矩阵驱动

`epms/src/pages/pa/PaListPage.tsx`:

```ts
// before
const canCreate = ['ap_clerk','procurement_officer','procurement_manager','requester','system_admin'].includes(user?.role ?? '')
// after
const perms = useRolePermissions().data?.permissions
const canCreate = user?.role === 'system_admin' || !!perms?.['epms.pa.write']
```

`GET /config/me/permissions`(`epms-api/app/api/v1/config.py::get_my_permissions` → `uniops_authz.effective_permissions`)返回 `permission_defs` 的全部 key(含 phase2 的 `epms.pa.write`),且已是**基础角色 ∪ 附加角色**的并集,因此附加角色授权同样能点亮按钮。遵循 `feedback_epms_access_control`:用矩阵而非硬编码角色。

**行为变化(已与用户确认)**:`procurement_manager` 的 New PA 按钮会消失 —— 它此前显示但提交必 403,属修正既有不一致;若日后需要,admin 在 Portal → Access Control 勾上即可,无需改代码。

### 3.4 通知:零改动 + 回归测试锁住

不新增任何任务/通知路径。officer 不出现在 `create_pa` 的 assignee 中,因此:

- 不收 create_pa 邮件
- 不进 Daily Follow-up 每日提醒(它只扫开放任务的 assignee)

用测试把"officer 建 PA 不产生指向自己的任务"钉住,防止后续改动漂移。

## 4. 测试计划

epms-api(pytest,跑法见记忆 `feedback_uniops_admin_test_db_env`:覆盖 `POSTGRES_*` 指向本地 docker `uniops_postgres`,库 `epms_test`,`JWT_SECRET_KEY=test-secret`;**禁并发**):

新增 `epms-api/tests/test_pa_on_behalf_authz.py`:

1. `procurement_officer`(基础角色)对**他人 PR 的 PO** POST /pa → **201**
2. `requester` 基础角色 + 附加角色 `procurement_officer` 对他人 PO → **201**
3. 纯 `requester` 对他人 PO → **403**(防回归)
4. `erp_pa_officer` 对 NC 无 PR PO → **201**(原行为不回归,现有 `test_pa_erp_officer_authz.py` 已覆盖,确认仍绿)
5. officer 建 PA 后:`tasks` 表中**不存在** `type='create_pa'` 且 assignee/assigned_role 指向该 officer 的行;PR Requester 原有的 `create_pa` 任务被置为 completed

approval-api:

6. 以 officer 为 `created_by`、PO 关联了他人 PR 的 PA,`_routing_user_id` 返回 **PR.created_by**、`_routing_department_id` 返回 **PR.department_id**(现有逻辑的显式回归测试)

前端:`epms` 的 `npx tsc -p tsconfig.app.json` 对齐基线(基线 59 err,见记忆 `project_uniops_pa_chain_attachments`);worktree 内需先 `npm ci`。

遵循 `feedback_verification_positive_evidence`:每项以正面证据(201/403/行数)断言,不以"无输出"当通过;失败计数对基线比较。

## 5. 部署

- identity-api 跑 `migrate`(0005)**或**等价的 `seed_phase2_keys`。
- 其余按标准发布流程(`reference_uniops_prod_release_workflow`):全 15 镜像同 sha。
- **部署后无需人工勾权限**;若要给 `procurement_manager` 同样能力,在 Portal → Access Control 勾 `epms.pa.write` 即可。

## 6. 风险与边界

| 风险 | 处理 |
|---|---|
| officer 矩阵默认带 `pa_override_receipt=true`,拿到 PA 创建权后可"带理由强制建 PA"(绕过三方匹配闸门) | 现有默认,本次不动(已与用户确认);override 会落 PA 审计字段,可追溯。若要收紧,在 Access Control 关掉该格 |
| PA 被退回时 revise 任务派给创建人 officer(而非 Requester) | 符合用户确认的"其他通知都正常";且只有创建人有权改这张 PA |
| `procurement_manager` 按钮消失 | 已确认为修正;必要时矩阵勾选恢复 |
| 迁移 down_revision 挂错 | 已核实 identity 当前唯一 head = `0004_erp_pa_officer_role`(遵 `feedback_uniops_alembic_new_migration_check_heads`) |
