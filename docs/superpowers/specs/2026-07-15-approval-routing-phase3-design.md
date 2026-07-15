# 审批路由归位(权限重构③期)设计

> 状态:设计稿(2026-07-15 brainstorm,逐点经用户确认)。
> 前序:①权限中枢已上生产(`2026-07-14-authz-hub-phase1-design.md`,TAG 195436e)。
> 后续:②后端门禁统一(`require_roles`→`require_permission`)——**用户决定③先于②**,因为③清掉 `role_management` 这层后②会干净很多。②期探索结论已存档在本文 §8。

## 1. 问题:`role_management` 一份数据背着三种语义

EPMS `company_config.role_management` + `dept_*` 三件套是历史包袱(EPMS 是第一个系统),现被四方消费,且**语义不同**:

| 消费方 | 拿它干什么 | 本质 |
|---|---|---|
| approval-api | named role → 具体审批人 | **审批路由**(它的本职) |
| finance-api | `can_pay`、JV 复核/过账授权 | **权限门禁** |
| expense-api | `can_pay` 门禁 | **权限门禁** |
| epms `access_scope` | 可见性并集 | 数据可见性 |

即「你是 Finance BP」既决定**单子路由给你审**,也决定**你能不能付款**——两个概念焊死。这也是②③纠缠的根因:②要动门禁,就绕不开 `role_management`。

③把这份数据拆开,各归各位。

## 2. 用户已确认的决策

1. **③先于②**(先清 `role_management`,②才干净)。
2. **③只搬审批路由**;语义分离(can_pay 变权限键)留给②。
3. **人选归 user_roles,不新建"指派"页面**——Access Control → User Roles 的主/副角色已能表达「谁是 GM」,与 `role_management` 的 `*_user_id` 完全重复。
4. **部门路由三件套(Dept→GM/OPM、Department Directors、Department Supervisors)保留,但迁出 EPMS**——它们是**组织结构级**全局设置,放业务模块下不合理,应在 Portal(UniOps)Admin 下。
5. **`temp_assignments`(临时代班)砍掉**——有表/UI/API 但审批引擎从不读,配了不生效(生产数据为空);留着只会让人以为它 work。

## 3. 拆解:三样东西各回各家

| 现状(EPMS `company_config`) | 去向 | 理由 |
|---|---|---|
| `gm_user_id`/`opm_user_id`/`finance_bp_user_ids`/`vendor_manager_user_id`/`finance_manager_user_id`/`procurement_manager_user_id` | **identity `user_roles` 副角色**(一期已有表) | 就是「谁是什么岗」,与副角色重复 |
| `dept_gm_opm_mapping`(dev 实测 12 部门)/`dept_director_mapping`(3 部门)/`dept_supervisor_enabled`(1 部门) | **approval-api 新表 + Portal Admin 新页** | 组织结构级**路由规则**,不是角色;副角色无部门维度 |
| `gm_backup_user_id`/`opm_backup_user_id`(dev 实测 GM/OPM **互为备份**) | **approval-api 新表** | 「主审批人不在时找谁」是路由语义;副角色分不清主/备 |
| `temp_assignments` 表 + UI + API + `TEMP_ROLE_OPTIONS` | **删除** | 死功能 |

## 4. 数据模型(approval-api,与各服务同库)

```
approval_dept_routing(
  dept_id uuid PK,
  gm_or_opm varchar(3) NOT NULL,          -- 'gm' | 'opm'
  director_user_id uuid NULL,
  supervisor_enabled bool NOT NULL DEFAULT true,
  updated_by uuid, updated_at timestamptz
)
approval_backups(
  role_code varchar(50) PK,               -- 'gm' | 'opm'
  backup_user_id uuid NOT NULL,
  updated_by uuid, updated_at timestamptz
)
```
三个 JSONB 合成一张按部门的行表——**部门是天然主键**,三件套本来就是同一部门的三个属性。`dept_supervisor_enabled` 缺省语义为 true(现 JSONB 只显式记 false 的部门),迁移时只有显式 false 的部门写 false,其余部门要么建行为 true、要么不建行(读取端 `.get(dept, True)`);**实现取「为每个已知部门建行」**,让 UI 能列全并显式管理。

## 5. 读取方式:同库只读镜像,不走 HTTP

**关键事实(计划期核实)**:所有服务共享同一物理库(`postgres:5432/epms`)。因此:

- **approval-api**:路由从自己的表读;人选从 `user_roles` 读(只读镜像)。**不再读 `company_config`** → 斩断 approval→epms 的错误归属(改为「approval 拥有审批数据,别人读它」)。
- **epms `access_scope._effective_role_codes`**:`role_management` → `user_roles`(同库直读)。**一期遗留的「access_scope 拿不到 token」难题就地消失**,epms 的写穿透镜像(`company_config.role_permissions`)从此具备退役条件(实际退役在②期,因 `require_permission` 仍读矩阵)。
- **finance/expense 的 `can_pay`**:`role_management` → `user_roles` → 语义变成「主角色或副角色是 finance_manager」,**这就是「多角色后端生效」**,②期不必再碰。
- 零 HTTP、零缓存、零 token 传递——同库拓扑给的红利(与①期 epms→identity 的 HTTP 代理不同,因①期跨的是"服务边界事实源",此处是同库数据读取,依 UniOps 既有镜像模型惯例)。

镜像模型须**忠于物理表**(逐列核对 information_schema,勿套惯例)——见 [[feedback_uniops_mirror_models_match_reality]]。

## 6. 管理界面

- **人选** → 复用 **Portal → Admin → Access Control → User Roles**(不新建页;决策 3)。
- **路由规则** → **Portal → Admin → Approval Routing** 新页(PortalChromeLayout + navConfig,`adminOnly`):一张部门表,每行 = 部门名 / GM-or-OPM 单选 / Director 下拉 / Supervisor 开关;页尾 GM、OPM 备份人各一下拉。写走 approval-api 新端点。
- **EPMS Admin → Role Management 页签整个删除**(连同代班 UI 与 `TEMP_ROLE_OPTIONS`)。
- 部门列表:`departments` 主数据(epms/mdm 均有模型,实现前核实主源)供页面下拉;用户列表翻页取全(防 20 条截断,见 [[feedback_uniops_users_pagination_truncation]])。
- UI 文案全英文。

## 7. 迁移与零行为变化

一次性幂等 seed(**容器内跑**,见 [[feedback_uniops_host_env_points_at_prod]]):
- 6 个 `*_user_id`/`finance_bp_user_ids` → `user_roles` 行(该用户若主角色已是该岗则跳过——副角色不与主角色重复,与一期 `PUT /authz/users/{id}/roles` 的 `set(additional) - {primary}` 语义一致);
- `dept_gm_opm_mapping` + `dept_director_mapping` + `dept_supervisor_enabled` → `approval_dept_routing` 行;
- `gm_backup_user_id`/`opm_backup_user_id` → `approval_backups` 行。

**平价断言(本期验收铁律,对标①期的矩阵逐键相等)**:迁移前后,对每个 (部门 × 单据类型) 组合,**审批引擎解析出的审批人必须逐个相同**。实现为一个可重复运行的比对脚本:遍历所有部门 × {pr, po, pa},对比新旧解析结果,任一差异即失败。

## 8. ②期探索结论存档(③完成后重启②时直接用)

- **门禁真实规模**:调用点 ~76 处但**概念只有 ~10-13 个**(budget 30 处共用 1 个 `_WRITE_ROLES` 常量;budget 实为 3-4 概念/epms 4/finance 3/mdm 2);矩阵将从 17 键涨到 ~28,可控。
- **不该迁的**:approval-api 31 处是审批岗位解析(③处理);expense/vms 30 处是数据可见性 scope(`auditor` 只读、`dept_manager` 只看本部门),业务语义留在各服务(①期 spec 已定)。
- **陷阱**:`require_roles` 实现里 **system_admin 硬短路**(如 `_OPENING_WRITE_ROLES=("finance_manager","finance_bp")` 不含 system_admin 但它照样能过)——迁移时默认值必须算上短路,否则是行为变化。
- **JWT 带权限方案已否决**:access token 有效期 480 分钟(8 小时),权限收回最长 8 小时才生效,安全上不可接受。仍用「拉权限 + 短缓存」。
- **无后端共享包**(`packages/` 只有前端 shell),各服务 Dockerfile context 是自己的目录;只有 epms 接了 identity(`IDENTITY_API_URL`),finance/budget/mdm 若要读矩阵需各自接入——②期需决策「复制 authz_client」vs「建后端共享包(动 7 个 Dockerfile context,发布风险)」。

## 9. 范围外

②期后端门禁统一(`require_roles`→`require_permission`、can_pay 变权限键、写穿透镜像退役);审批流程步骤定义(`workflow_defs`)不动;代班功能重做;`departments` 主数据归属整理(epms/mdm 双模型)。
