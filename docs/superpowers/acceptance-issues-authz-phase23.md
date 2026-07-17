# 权限重构 ②③ 发布后验收问题清单

> 用户边验收边报,攒成一批统一修(不逐个发版)。每条记:现象 + 根因 + 修复要点 + 状态。
> 攒够后走 spec→plan→执行统一修改。

## Issue 1 — EPMS Admin 的三个部门设置与 Portal Approval Routing 重复 [OPEN]

**用户现象**:EPMS Admin Panel 下还有 `Dept → GM/OPM Mapping`、`Department Directors`、`Department Supervisors` 三个页签,和 Portal → Approval Routing 的内容重复。

**根因(③期迁移不完整)**:`dept_gm_opm_mapping` / `dept_director_mapping` 两份配置被**两方读**——
- approval-api:审批**路由**(派单给谁审) → ③期已迁到 `approval_dept_routing` 表 ✅
- epms `access_scope`:文档**可见性**(GM/OPM/Director 能看哪个部门的 PR/PO/PA) → **仍读旧 `company_config.dept_*` JSONB** ❌(`access_scope.py:82-101` 的 `_mapped_dept_ids`/`_director_dept_ids`)

③期把整份 dept 配置当"审批路由"迁了,漏了 epms access_scope 也用它做可见性(同②漏 booking 后端/③漏 7 处 role_management 的同类错误)。现两套并行不同步:Portal 改路由不影响 epms 可见性,反之亦然。
`dept_supervisor_enabled` 例外:**只有 approval 读**,epms 不读 → 那个页签是**纯死设置**。

**修复要点**:
1. `epms-api/app/core/access_scope.py` 的 `_mapped_dept_ids`(:82) + `_director_dept_ids`(:97) 改读 `approval_dept_routing` 表(同库直读,统一单一源) —— 不能简单删页签,否则可见性失去数据源。
2. 删 EPMS AdminPanel 三个页签(`dept_mapping`/`dept_director_mapping`/`dept_supervisor`,AdminPanel.tsx:54-56 + 对应区块)及后端 config 写入。
3. Portal → Approval Routing 成为部门路由的唯一入口。
4. 平价验证:改前后 GM/OPM/Director 的**文档可见性**scope 不变(access_scope 相关测试)。
5. `company_config.dept_*` 三列留档不删(回滚 + reconstruct.py 仍读 dept_gm_opm_mapping)。

**状态**:OPEN,待攒批统一修。

## Issue 2 — Data Maintenance 页永久 loading 无报错 [OPEN,根因已定位]

**用户现象**:管理员登录点 Data Maintenance,页面卡在 Loading,无报错。

**根因(用户自己在 F12 找到)**:pending 请求的 Request URL 是 **`http://localhost:8004/finance/v1/admin/entities`** —— 回落到了 `adminApi.ts:34` 的 fallback 默认值。**`portal/Dockerfile` 漏声明 `ARG VITE_FINANCE_API_URL`**(有 EPMS/OA/VMS/BUDGET/MDM 的,唯独没有 FINANCE_API),compose 的 portal-web build args 也没传 → Vite 静默丢弃 → 浏览器去连用户本机 8004 → 永久挂起(不是拒绝,所以无报错、无限 loading)。

**为什么整页卡死**:`adminApi.entities()` 用 `Promise.allSettled` 等**全部 4 个系统**的 `/admin/entities`。allSettled 只在所有 promise 都 settle 后 resolve;一个**永久 pending** 的 fetch(无超时)让它永不 resolve → `useAdminEntities` 永远 isLoading → 整页 Loading。容错设计因为缺超时而形同虚设。

**与②③无关**:后端全部正常(容器内 epms 200 / expense 403 / vms 403 秒回,finance 的 list_entities 根本不查库)。这是 portal 构建期的 env 漏配,一直存在,Data Maintenance 首次在生产被点开才暴露。

**⚠️ 这是 [[feedback_uniops_dockerfile_missing_build_arg]] 的第二次**:上次(2026-06-30)是 portal 漏 `VITE_FINANCE_URL` → Finance 模块跳 localhost:5177,当时只补了跳转用的 `VITE_FINANCE_URL`,**没补 API 用的 `VITE_FINANCE_API_URL`**(两个名字太像)。

**修复要点**:
1. `portal/Dockerfile`:补 `ARG VITE_FINANCE_API_URL` + 加进 `ENV` 那串。
2. `docker-compose.prod.yml` + `docker-compose.dev.yml` 的 portal-web build args:补 `VITE_FINANCE_API_URL: ${FINANCE_API_URL}`(该 env 已存在,finance-web 在用)。
3. **治本(通用)**:`portal/src/services/adminApi.ts` 的 `request()` 给 fetch 加 `AbortSignal.timeout(8000)` —— 任何系统慢/挂都当失败跳过,不再拖死整页。allSettled 的容错本意才成立。
4. 防复发:考虑加一个构建期断言(缺 VITE_*_API_URL 就 fail build,而不是静默回落 localhost),或让 fallback 在 prod 构建下直接抛错。
5. 修完必须**重建 portal-web 镜像**(VITE_* 是构建期烤入的)。

**状态**:OPEN,待攒批统一修。

## Issue 3 — Access Control / Approval Routing 未遵循系统标准 UI(无 sidebar) [OPEN]

**用户现象**:Access Control 和 Approval Routing 两个页面没遵循整个系统标准的 UI 风格。

**根因(控制器的错)**:两页都是**裸 div**,没包 `PortalSidebar` —— 点进去侧边栏整个消失,只剩一个 "Back to UniOps" 链接,与系统其他页面断裂。
标准做法见 `portal/src/pages/PortalHome.tsx:785`:
```tsx
<PortalSidebar activeKey="portal:/" epmsHref=... oaHref=... financeHref=... session=...
  userRole={auth.user?.role ?? null} perms={perms}
  mobileOpen=... onClose=... collapsed=... onToggleCollapse=... />
```
**我在派单时让代理「照 DataMaintenance.tsx 抄 chrome/guard」——而 DataMaintenance 本身就是不合规的裸 div**(先于本次工作就存在),于是把问题复制了两遍。`navConfig.tsx` 早已给这两页配了导航项(`activeKey` 正是为此存在),却因为没渲染 sidebar 而形同虚设。
**直接违反 [[feedback_uniops_portal_page_chrome]]**:"每个 Portal 页面都要包在 chrome 里(sidebar+header,activeKey 对应 navConfig),别渲染裸 div"。

**范围(比用户报的更大)**:`grep -c PortalSidebar` → AccessControl 0 / ApprovalRouting 0 / **DataMaintenance 0**(三个 admin 页全中,DataMaintenance 是既有问题)。

**修复要点**:
1. 三个页面(AccessControl / ApprovalRouting / DataMaintenance)统一包 `PortalSidebar`,照 PortalHome 的用法传 activeKey(对应各自 navConfig 的 href:`portal:/admin/access-control` / `portal:/admin/approval-routing` / `portal:/admin/data-maintenance`)+ perms + session + 折叠/移动端状态。
2. 抽一个 `PortalPageLayout`(sidebar + header + content slot)避免三处再各抄一遍 —— 否则下一个 admin 页还会重复(这正是本次复制两遍的成因)。
3. 保留各页现有的 system_admin 守卫逻辑。
4. 验证:portal tsc 0;三页 sidebar 高亮各自 navConfig 项、折叠/移动端正常。

**状态**:OPEN,待攒批统一修。

### Issue 1 补充 — 第二处双源:`_effective_role_codes` 的 director 判定 [Task 2 审查挖出]

**Task 2 只切了「可见性」(`_mapped_dept_ids`/`_director_dept_ids`),漏了「角色判定」**:
`access_scope.py:136` 仍读冻结 JSONB 判断谁是 director:
```python
if uid_str in (cfg.dept_director_mapping or {}).values():
    codes.add("director")
```
下游:`dashboard.py:105`、`task.py:179`、`access_scope.py:180`。

**真实后果(半迁移比不迁移更糟)**:在 Portal → Approval Routing 指派新 director →
- 他**能看见**该部门单据(Task 2 已切新表)✅
- 但在 **dashboard / 任务箱里不被当作 director** ❌(角色判定仍读没人再写的 JSONB)

**归责**:控制器规划盲区。写 Task 2 的 brief 时只盘了 brief 点名的两个函数,**没全仓 grep「谁读这份 JSONB」** ——
与 ②期漏 booking-api 后端门禁、③期漏 7 处 role_management 是**同一类错误,第三次**。
brief 里那句「若 CompanyConfig 还有别处用就留着」是泛泛保险话,不是盘过这个消费点;
审查者误读为「作者有意延后」,不可顺此下台阶。

**修复**:`_effective_role_codes` 的 director 判定改读 `approval_dept_routing.director_user_id`。
supervisor 判定读 `User.supervisor_id`,与本次无关,不动。

**状态**:Task 2b 修复中。
