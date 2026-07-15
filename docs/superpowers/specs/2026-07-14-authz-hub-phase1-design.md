# 权限中枢一期(identity 升级 + Portal 管理页)设计

> 状态:设计稿(2026-07-14 brainstorm,逐点经用户确认)。
> 背景:权限体系历史上长在 EPMS(第一个系统)身上——矩阵存 epms `company_config.role_permissions` JSONB,键集混入 Finance/Booking 键,Portal/finance-web 依赖 epms API 决定自身可见性,后端另有硬编码角色元组双轨。本期只做「中枢搬家+多角色+管理页」,纯增量零行为变化。
> 分期:①本 spec;②各服务后端 require_permission 统一(另起 spec);③审批岗位指派迁 approval-api(另起 spec,用户确认单独做)。

## 0. 术语边界(用户确认)

- **权限(本期)**= 角色 → 能看/能做什么(Access Control Matrix)。
- **审批岗位指派(③期,本期不碰)**= EPMS Admin「Role Management」页签的 GM/OPM/Finance BP 指派+部门映射(`role_management`/`dept_gm_opm_mapping`/`dept_director_mapping`/`dept_supervisor_enabled`),归审批域,将来迁 approval-api,不进 identity。本期对它零影响。

## 1. 用户已确认的决策

1. **范围**:只做①(中枢搬家);②③各自另起 spec。
2. **键命名**:现有 17 个扁平键**原样迁移**(前端零改动),`permission_defs.module` 列分组;今后新键用 `module.action` 风格。
3. **多角色边界**:`users.role` 仍是主角色(JWT 不动,旧后端照常);`user_roles` 存**附加角色**,一期只影响 `/me/permissions` 并集(前端可见性先享受多岗);后端授权等②期。
4. **EPMS 旧矩阵页签**:直接下线;epms 后端端点保留为只读代理。
5. **指派 UI**:Portal 新页统一管(主角色单选+附加角色多选);EPMS Users 页 role 下拉保留(同一数据 users.role)。

## 2. 数据模型(identity-api,新迁移)

- `role_defs(code varchar(50) PK, label varchar(100), sort int, is_active bool default true)` — seed 现有 17 角色;今后加角色=加行。
- `permission_defs(key varchar(64) PK, module varchar(20) not null, label varchar(120), sort int)` — seed 现有 17 键;module 归属:`epms`=view_pr/view_po/view_gr/view_invoice/view_pa/create_pr/create_gr/invoice_upload/vendor_master/parts_catalog/admin_panel/data_maintenance,`finance`=view_budget_dashboard/view_budget_plans/view_finance,`booking`=view_booking/manage_meeting_rooms。
- `role_permissions(role_code FK, permission_key FK, updated_by uuid, updated_at, PK(role_code,permission_key))` — **只存授予行**(有行=true),带审计列。
- `role_permission_locks(role_code, permission_key, PK(两列))` — seed 自 epms `LOCKED_PERMISSIONS` 硬编码 dict;锁定=强制 true 且 UI 不可改。
- `user_roles(user_id FK users, role_code FK, PK(两列))` — 附加角色。

## 3. API(identity-api `/authz/*` + `/me/permissions`)

| 端点 | 权限 | 行为 |
|---|---|---|
| `GET /authz/matrix` | 登录 | `{role: {key: bool}}` 生效矩阵(授予 ∪ 锁定) |
| `PATCH /authz/matrix` | system_admin | 批量 `{role: {key: bool}}` upsert/删行;命中锁定格 → 409 带明细 |
| `GET /authz/defs` | 登录 | `{roles:[...], permissions:[{key,module,label,locked_for:[roles]}]}` 供管理页渲染 |
| `GET /me/permissions` | 登录 | `{permissions: {key: bool}, roles: [主+附加]}` — 主角色 ∪ 附加角色并集 |
| `PUT /authz/users/{id}/roles` | system_admin | `{primary, additional[]}` 单事务写 users.role + user_roles(全删全插) |

未知角色 → 空权限;is_active=false 的角色不参与并集。

## 4. 存量消费方处理

**前提(2026-07-15 计划期核实)**:identity-api 无公网域名,浏览器从不直连它(前端无任何 VITE_IDENTITY;auth 都是 epms-api 服务端转发)。若让前端直调 identity 要牵出 DNS+Caddyfile+CORS+三个 Dockerfile ARG 一整片基建——①期不做。**修正:identity 只做服务端事实源,epms-api 作纯转发网关(零 authz 逻辑)**,前端零基建变化;②期各服务后端也是服务端直调 identity,浏览器永远不需要直连。

- **epms-api `/config/*` authz 端点全部变纯代理**(服务端 httpx 调 identity,IDENTITY_API_URL 已有;读端点 60s 进程内缓存):
  - `GET /config/role-permissions` → identity `GET /authz/matrix`
  - `GET /config/locked-permissions` → identity locks(保持旧响应形状 `{role:[keys]}`)
  - `PATCH /config/role-permissions` → identity `PATCH /authz/matrix`(保留,代理写,Portal 管理页经此保存;透传调用方 Bearer,鉴权在 identity)
  - 新增 `GET /config/me/permissions` → identity `GET /me/permissions`
  - 新增 `GET /config/authz-defs` → identity `GET /authz/defs`
  - 新增 `PUT /config/users/{id}/roles` → identity `PUT /authz/users/{id}/roles`
  - **identity 不可达时读端点回落**冻结的 company_config JSONB(保可用;写端点直接 502)。
- **epms 后端内部消费**(core/deps.py:93、core/access_scope.py:173):新增 `app/core/authz_client.py` 缓存代理(60s TTL+同款回落),两处调用点改用之;`get_effective_role_permissions` 本体保留仅作回落路径。
- **三处前端 hook**(portal/finance 的 useRolePermissions、epms useConfig 的矩阵消费):改调 epms `GET /config/me/permissions`(仍走 epmsApi,零新增 env/CORS)。
- **EPMS Admin Panel「Access Control Matrix」页签**:删除。
- **EPMS Admin「Custom Roles」页签(计划期发现,存量功能)**:同属 Access Control 域,一并删除;`GET /config/roles` 改为代理 identity 角色表(供 Users 页 role 下拉),POST/PATCH/DELETE `/config/roles/*` 删除(dev/生产现存自定义角色 0 个,seed 会迁移如有;identity 侧角色 CRUD API 待有需求再加)。
- **EPMS Users 页 role 下拉**:保留(主角色,同一列)。

## 5. Portal → Admin → Access Control 管理页

PortalChromeLayout + navConfig 加项(`admin_panel` 门禁),两 tab:

- **Permission Matrix**:行=权限按 module 分组小节,列=角色;锁定格灰显+锁图标;保存=批量 PATCH。
- **User Roles**:用户全量列表(**翻页取全**,防 20 条截断)+主角色单选+附加角色多选;保存调 epms 代理 `PUT /config/users/{id}/roles`。管理页所有读写均走 epms 代理端点(见 §4),不直连 identity。

UI 文案全英文(项目铁律)。

## 6. 迁移与发布

- identity alembic 新迁移建 5 表;seed 脚本(容器内跑,幂等 on-conflict-skip):epms 生效矩阵 JSONB→role_permissions、键+module 映射→permission_defs、LOCKED dict→locks、17 内建角色+company_config.custom_roles(如有)→role_defs。identity 与 epms 共享同一物理库,seed 直接 SQL 读 company_config。
- 发布同一 TAG:identity migrate+seed → epms 代理生效 → 前端切 `/me/permissions`。回滚:全程 additive,epms 冻结 JSONB 即兜底。
- ⚠️宿主 .env 指向生产库,迁移/seed 一律容器内跑([[feedback_uniops_host_env_points_at_prod]])。

## 7. 测试

- identity:矩阵 CRUD/锁拒改 409/并集逻辑(主+附加/is_active 过滤/未知角色)/PUT roles 事务/权限门禁(非 admin 403)。
- epms:代理返回与 identity 一致、identity 宕机回落 JSONB、PATCH 路由已除(405)。
- seed:幂等重跑不重复;迁移前后 `GET /authz/matrix` == 迁移前 epms 生效矩阵(逐键断言)。
- 前端:三 app tsc;Portal 管理页手工冒烟。

## 8. 范围外

②后端 require_permission 替换硬编码角色元组;③审批岗位指派迁 approval-api(含 EPMS「Role Management」页签下线);JWT 结构变更;自定义角色创建 UI(role_defs 已支持,待有需求);细粒度资源级权限(access_scope 业务语义留在各服务)。
