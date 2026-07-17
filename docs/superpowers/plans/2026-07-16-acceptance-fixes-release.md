# 验收修复 + 生产事故修复 合并发布说明

> **一次发布,四部分内容**(用户决定合并发布):①用户报的 3 条验收问题 ②今日生产事故(全员登录瘫痪)的修复
> ③终审查出的同类缺陷 ④booking 单场取消(另一会话开发+审查,已在 main)
> 基线:生产在跑 `bdf6be8`。本次 main HEAD 见文末。**未 push、未部署。**

## 一、本次发布做了什么

### 用户报的 3 条验收问题

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | EPMS Admin 三个部门设置与 Portal Approval Routing 重复 | ③期只迁了**路由**读取,**可见性/角色判定**仍读冻结的 `company_config.dept_*` JSONB → 两套并行不同步 | 5 个消费点全部归位到 `approval_dept_routing`;删三个页签 |
| 2 | Data Maintenance 永久 loading、无报错 | `portal/Dockerfile` 漏 `ARG VITE_FINANCE_API_URL` → Vite 静默丢弃 → 回落 `localhost:8004` → 浏览器**永久 pending**(挂起≠拒绝,无 error)→ `Promise.allSettled` 永不 resolve | 补 build arg;并给 `adminApi.request()` 加 `AbortSignal.timeout(15000)` |
| 3 | Access Control / Approval Routing 不符系统 UI 风格 | 三个 admin 页都是裸 div,没渲染 `PortalSidebar` | 抽 `PortalPageLayout`,复用真 `TopHeader` |

**Issue 1 的 5 个消费点**(★ 这是本次最大的认知修正 —— 起初以为只有 2 个):
1. `epms-api/app/core/access_scope.py::_mapped_dept_ids` / `_director_dept_ids` —— 文档可见性
2. 同文件 `_effective_role_codes` 的 **director 角色判定** —— 下游 `dashboard.py` / `task.py`
3. `expense-api/app/api/v1/invoice_list.py` —— **OA 发票列表 gm/opm 范围过滤**(PRD §INV-VIS 活功能)
4. EPMS AdminPanel 三个页签 —— 冻结 JSONB 的写入者
5. `AdminPanel.tsx` 里藏在 Approval Workflows 页签内的 supervisor 副本(死代码,已删)

**保留不动**:`company_config.dept_*` 三列(回滚 + `import_pms/reconstruct.py` 读历史快照)、
approval 的 `seed_routing.py` / `verify_routing_parity.py`(**故意**读旧 JSONB 的迁移/平价工具)。

### 今日生产事故的修复

**事故经过**:用户在 Admin 打开公司级 MFA → **全员登录瘫痪**。
根因:`REDIS_HOST=10.10.50.20` 指向的机器上**根本没装 Redis**(缺席 ≥33 天没人发现,因为登录只在 **MFA 分支**碰 Redis)。
**已在生产修复**(装 Redis + `bind 127.0.0.1 10.10.50.20` + ufw 放行 10.10.50.65),登录已恢复。

本次发布带上的代码侧修复:
- **`identity` SMTP 587 发不出**(`[SSL: WRONG_VERSION_NUMBER]`):identity 的 `email.py` 是 epms 的**陈旧拷贝**,
  epms 早修了 TLS 分流而拷贝没跟上。已移植:`implicit_tls = use_tls and port==465` / `start_tls = use_tls and port!=465`。
- **可选 `REDIS_PASSWORD`**(生产 Redis 现在裸奔,而它存 MFA 的 OTP)。
- **identity `/health` 真探 DB+Redis**(此前只返回写死的 `{"status":"ok"}`,事故全程显示 healthy,把排查带偏)。
- **删死表 `temp_assignments`**(上次发布刻意推迟的收尾)。

### 终审查出的同类缺陷

- **`epms` / `oa` 前端也漏 `VITE_FINANCE_API_URL`**(同 Issue 2,**该坑第三次**)→ 生产上 EPMS 付款来源下拉、
  OA 银行账户下拉会连用户自己机器的 8004。用户没报是因为还没点到那两处。
- **GM/OPM 可见性与引擎语义不一致**:`approval_dept_routing` **只有 seed 当时的活跃部门有行**
  (mdm 建新部门**不写** routing 行),而 engine 是 `.get(dept, "gm")` **默认派给 GM**。
  原实现 `WHERE gm_or_opm='gm'` 对无行部门返回空 → **新建部门的单子派给 GM 审、但 GM 看不见**
  (正是本次要消灭的那个不一致,差点复刻给未来)。已改为 `LEFT JOIN ... COALESCE(r.gm_or_opm,'gm')`。
  **两侧(epms + expense)都改,且互留同步注释。**

## 二、★ 部署顺序(错了会再断一次生产登录)

### 无迁移?有一个

- epms `z4_drop_temp_assignments`(挂在 z3 后,已验 upgrade→downgrade→upgrade 往返可用,仍单 head)。
- **无新 seed。**

### Redis 密码:★ 本次发布【不设密码】(用户 2026-07-16 决定)

**本次只发代码,`REDIS_PASSWORD` 留空,生产 Redis 不设 `requirepass`。**
代码在该变量为空时产生**与改动前逐字相同**的无密码 URL,所以本次发布对 Redis 连接**零影响**。
**发布时不需要为 Redis 做任何事** —— 别照下面的步骤去设密码。

理由:同日生产已因 Redis 瘫过一次全员登录,不在同一天再制造一个不可用窗口。
防线暂时仍靠 ufw(6379 只放行 app server 10.10.50.65),风险可控。

**将来真要启用密码时**(挑个低峰窗口,★ 顺序不能反):
```
1) 代码已发(本次就发了)—— REDIS_PASSWORD 为空,连接行为不变
2) 设密码  —— 生产 Redis `requirepass` + app server .env 填 REDIS_PASSWORD
3) 重启     —— identity-api / epms-api
```
⚠️ **第 2→3 步之间有登录不可用窗口**(Redis 已要密码、服务还没拿到)。
无零停机方案(Redis 6+ ACL 可平滑过渡,属更大改造)。
缓解:用 `redis-cli CONFIG SET requirepass <pwd>`(运行时生效、可秒回滚)把窗口压到最小。

### 常规发布步骤

按 [[reference_uniops_prod_release_workflow]]:本地 `git pull` → 改 `/tmp/uniops-domain.env` 的 `TAG`
→ **串行 build+push 全部 15 个镜像**的 `:<sha>`(⚠️别只建改动的,app server 全局 TAG pull 会对缺失镜像
报 not found,已两踩)→ app server `git pull` → 改 `.env` 的 `TAG` → `pull` → `migrate-prod.sh`(有 z4)
→ `--profile edge up -d`。

**★ 必须全量重建镜像**:`VITE_*` 是构建期烤死的,本次动了 portal/epms/oa 三个前端的 build arg。

## 三、发布后验证

```bash
# Issue 2:Data Maintenance 能正常加载(不再永久 loading)
# 终审 2:EPMS PA 详情的付款来源下拉、OA ProcessPaymentModal 的银行账户下拉能拉到数据
# Issue 3:Access Control / Approval Routing / Data Maintenance 三页有 sidebar + 标准 header
# Issue 1:EPMS Admin 已无三个部门页签;Portal → Approval Routing 是唯一入口
# 事故:MFA 若开启,登录能收到 OTP 邮件(SMTP 587 已修);identity /health 现在会如实报告 DB/Redis
```

**★ Issue 1 的生产预检**(可选,今天影响为 0):确认 12 个活跃部门在 `approval_dept_routing` 里都有行。
无行的部门现在会 COALESCE 成 `'gm'`(与引擎一致),不再是"看不见"。

## 四、回滚

全部 additive 或纯前端。回退 TAG 即恢复。
`company_config.dept_*` 三列未删,旧数据源仍在。z4 有可用的 `downgrade()`(已实测往返)。

## 五、已知遗留(未做,记 backlog)

- **其他所有服务的 `/health` 都是假绿灯**(epms/budget/mdm/vms/finance/approval/booking):
  它们其实**写了** `/health/db` `/health/redis` 真探测端点,**但 compose 的 healthcheck 从来不调用**。
  本次只修了 identity。
- **OTP 在 Redis 里是明文**:即便加了密码,拿到密码或持久化文件的人仍能读到验证码。可改存哈希。
- **两份 `REDIS_URL` 是复制粘贴**(identity + epms),已互留同步注释,但未抽共享包。
- **compose 给 expense-api 传了它根本不用的 `REDIS_HOST`** —— 死配置,可清理。
- `AdminPanel.tsx` 里 `UserManagement` / `NotificationSettingsSection` 两个同类死组件仍在(改动前就已零引用)。
- **epms 套件常年 72 failed**(先于本次改动)——等于回归警报器一直在响,新问题藏在里面看不见。值得单独治理。
- 防复发:加**构建期断言** —— 缺 `VITE_*_API_URL` 就 fail build,而不是静默回落 localhost。
