# 后端门禁统一(权限重构②期)设计

> 状态:设计稿(2026-07-16 brainstorm,逐点经用户确认)。
> 前序:①权限中枢已上生产(`2026-07-14-authz-hub-phase1-design.md`,TAG 195436e);③审批路由归位已完成待发布(`2026-07-15-approval-routing-phase3-design.md`,分支 feature/approval-routing-phase3)。**用户改序先③后②**——③清掉 `role_management` 后②才干净。
> 发布:**②③一起发**(用户决定:生产虽已发布但尚未正式启用,没有在用用户要保护,合并成一次发布更合算)。

## 1. 目标

让权限矩阵**真正管住后端**。①期矩阵只管前端可见性,后端仍是硬编码角色元组(双轨);②期把业务门禁统一到 `require_permission`,矩阵开关从此对后端生效。

## 2. 用户已确认的决策

1. **建后端共享包 `packages/authz`**——用户以「未来还会继续增加模块」为由拍板,而非「现在最省事」的同库直读散写。见 §3 的扩展性论证。
2. **纯 admin 端点保持硬编码**(`require_roles("system_admin")`):vms 3 处、booking 1 处、mdm 的 erp 同步等是运维/系统级操作,业务上永远不会想「把 ERP 同步开放给 ap_clerk」,做成开关只是矩阵噪音。只迁**业务门禁**。
3. **②③一起发布**。

## 3. 架构决策:共享包 + 包内同库直读

三个候选按**扩展性**排序(用户的判据):

| | 新模块接入成本 | 改 authz 逻辑 | 将来分库/加缓存/改表结构 |
|---|---|---|---|
| 同库直读散写各服务 | 复制几行 SQL | **改 N 处** | **全崩**——表结构成了公共契约 |
| 沿用①期 HTTP 代理 | 复制 ~100 行客户端 | **改 N 处** | 能扛(API 是契约),但 `access_scope` 无 token 难题永在 |
| **共享包(选定)** | 加依赖 + import | **改 1 处** | **只改包内部,消费者零感知** |

关键在最后一列:包内**现在**用同库直读(快、无 token 问题、无缓存),将来真分库了改包内部换 HTTP,9 个服务一行不动。直读散写则把「同库」焊进每个服务。authz 是典型**横切关注点**(每模块都要),正是共享包的用武之地。

前端 `packages/shell` 已走通同一条路(`context: .` + `dockerfile: xxx/Dockerfile`),后端照搬。

### ⚠️ 附带的大简化:①期整套遗产退役

①期为让 epms 读矩阵建了 `authz_client`:HTTP + 60s 缓存 + identity 宕机回落 + **写穿透镜像**(PATCH 时同步写回 `company_config.role_permissions` JSONB)。这套复杂度的根源是「必须通过 HTTP 问 identity」。包内同库直读后:**identity 服务挂了,库还在,直读照常工作**——反而比 HTTP 少一个故障点。于是:

| ①期的东西 | ②期后 |
|---|---|
| HTTP 调用 + token 传递 | 删(同库) |
| 60s 进程缓存 | 删(一条索引 SQL) |
| identity 宕机回落 | 删(库挂了整个系统都挂) |
| **写穿透镜像 + `company_config.role_permissions` JSONB** | **退役**(无人再读;列留档不删) |
| `access_scope` 拿不到 token 的难题(①期终审 F1 的根因) | **消失** |

## 4. `packages/authz` 对外接口

只暴露两个:

```python
from uniops_authz import require_permission, effective_permissions

@router.post("/po", dependencies=[Depends(require_permission("epms.po.write"))])
```

- `require_permission(key)` → FastAPI 依赖。**`system_admin` 短路**(保留现有 `require_roles` 语义),否则:用户角色并集(`users.role` 主 ∪ `user_roles` 副) × 生效矩阵(`role_permissions` ∪ `role_permission_locks`)。
- `effective_permissions(db, user_id, base_role) -> dict[str, bool]` → 给 `access_scope` 这类**无 token 的纯 DB 调用链**用。
- **不做缓存**(YAGNI:矩阵实测 149 授予行 + 13 锁定行、走索引、复用同一连接;真慢了再加,且只加包内一处)。

生效矩阵一条 SQL:
```sql
SELECT role_code, permission_key FROM role_permissions
UNION SELECT role_code, permission_key FROM role_permission_locks
```

**⚠️ 角色并集必须查两源**:`users.role`(主) ∪ `user_roles`(副)。③期教训:只查 `user_roles` 会丢掉主角色持有者。

**与③期 finance_bp 教训的界线(别混淆)**:③期修的是「**谁是被指派的审批人**」——那里 `finance_bp` 只认 `user_roles` 指派,因为持有职能 ≠ 被指派审批。②期问的是完全不同的问题:「**这个人的角色有没有这个权限**」——按角色并集(主∪副)查矩阵是正确的,主角色 `finance_bp` 的人本来就该有 finance_bp 这个角色的权限。**②期不涉及岗位解析,不要把③期的 assignment-only 规则套过来**。

## 5. 权限键:12 个新键,`module.action` 命名(①期定的风格)

| 键 | 替换 | 默认角色集(**必须逐字等于现状**) |
|---|---|---|
| `epms.invoice.match` | `_AP_ROLES` | system_admin, ap_clerk, finance_manager, finance_bp |
| `epms.po.write` | `_PO_WRITE_ROLES` | system_admin, procurement_officer, procurement_manager |
| `epms.pa.write` | `_PA_WRITE_ROLES` | system_admin, finance_bp, finance_manager, ap_clerk, requester |
| `epms.gr.receive` | `_WAREHOUSE_ROLES` | system_admin, warehouse_staff, procurement_officer |
| `finance.coa.manage` | `_MANAGE_ROLES` | system_admin, finance_manager |
| `finance.period.close` | `_CLOSE_ROLES` | system_admin, finance_manager |
| `finance.jv.post` | `_JV_ROLES` | system_admin, finance_manager, finance_bp |
| `budget.catalog.write` | budget `_WRITE_ROLES`(catalog.py + factor.py) | system_admin, finance_manager, finance_bp |
| `budget.plan.write` | plan.py 的 `_WRITE_ROLES` | system_admin, finance_manager, finance_bp, dept_manager |
| `budget.opening.write` | `_OPENING_WRITE_ROLES` | **system_admin**, finance_manager, finance_bp |
| `mdm.finance.write` | mdm 5 处 `("system_admin","finance_manager","ap_clerk")` | system_admin, finance_manager, ap_clerk |
| `mdm.vendor.write` | mdm 1 处 `("system_admin","vendor_manager","finance_manager")` | system_admin, vendor_manager, finance_manager |

矩阵 17 → **29 键**;Portal 的 Access Control 页已按 `permission_defs.module` 分组,新增 module:`budget`、`mdm`(现有 `epms`/`finance`/`booking`)。

### ⚠️ 本期最容易踩的坑:`system_admin` 硬短路

`require_roles` 的实现里有 `if role == "system_admin": return payload` 的**硬短路**。所以 `_OPENING_WRITE_ROLES = ("finance_manager","finance_bp")` 字面不含 system_admin,**但 system_admin 实际能过**。迁移时每个新键的默认值必须算上它,否则就是行为变化(收紧)。逐个核对每个常量的**实际**准入集,不要照抄字面元组。

## 6. 迁移范围

- **57 个 `require_roles(...)` 调用点** → `require_permission("...")`:budget 34(共用 4 个常量)、mdm 7、epms 8、vms 6、expense 1、booking 1。其中**纯 admin 的不动**(决策 2)。
- finance 的 3 个内联门禁(`_MANAGE_ROLES`/`_CLOSE_ROLES`/`_JV_ROLES`)同理。
- identity 加迁移:`permission_defs` 插 12 键(带 module/label/sort)+ `role_permissions` 按 §5 表 seed 默认值(幂等 `ON CONFLICT DO NOTHING`)。
- **①期遗产退役**:epms `core/authz_client.py` 的 HTTP/缓存/回落/写穿透删除;`/config/*` 的 authz 代理端点改由共享包直读(**booking 前端仍读 `/config/role-permissions`,须保持响应形状不变**——①期终审发现的第 4 个前端,至今未迁)。
- **不迁**:数据可见性 scope(`auditor` 只读、`dept_manager` 只看本部门等),业务语义留各服务(①期 spec 已定)。

## 7. 基建改造(一次性)

9 个后端 api 服务:`build: { context: ./xxx-api }` → `context: .` + `dockerfile: xxx-api/Dockerfile`;Dockerfile 内路径加前缀 + `COPY packages/authz` + 安装。dev/prod 两个 compose 都要改。

⚠️ 前端 `packages/shell` 踩过**「拷贝陈旧」**(`npm --install-links` 是拷贝非符号链接,改了包容器仍跑旧码,见 [[feedback_uniops_shell_install_links_stale]])。Python 侧须在计划里钉死:用 `COPY packages/authz` + 镜像重建,**不要**用 `pip install -e` 指向宿主路径(dev 容器 bind mount 下会同样陈旧)。dev 改包后必须 `docker compose build` 相应服务,不能只 restart。

## 8. 验收

- **平价断言**(对标③期的 PARITY OK):脚本对每个 (角色 × 12 新键) 组合,比对**旧** `require_roles` 的准入判定与**新** `require_permission` 的判定,任一不等即失败并打印 `DIFF role=<code> key=<key> old=<bool> new=<bool>`。这是本期铁律。
- 各服务失败集对基线**零新增**:epms(基线 72,③期后)/finance 201/expense 83/vms 175/approval 39/identity 20 + budget、mdm、booking 各自基线。
- 前端 tsc:portal 0 / epms 69 基线 / finance 0。
- dev 实测:Portal 改一个新键的开关 → 对应后端端点的准入立即随之变化(证明矩阵真的管住了后端——这是②期存在的全部意义)。

## 9. 范围外

数据可见性 scope;审批路由(③已完成);`company_config.role_permissions` 列的物理删除(留档;且 `reconstruct.py` 的豁免仍在,见③期 spec);booking 前端迁移到 `/config/me/permissions`(它仍读 `/config/role-permissions` 兼容端点,该端点本期保留)。
