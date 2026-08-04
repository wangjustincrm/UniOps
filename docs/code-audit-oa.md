# 代码审计:OA 前端(费用报销 / 差旅 / PA)

- **基准**:前端 `main = 5c8a98f`;后端 `expense-api` 结论已核对 main 版本(deploy 目标)一致。
- **范围**:`oa/src` 34 文件 ~7900 行 + 交叉核对 `expense-api/app`。
- **方法**:4 个并行审查代理(正确性/数据层/类型安全/可维护性)+ 人工复核所有 P1。
- **一句话**:OA 前端整体姿势正确(主数据走 API、授权交后端、Decimal-as-string 已处理);**真正的高危债在 `expense-api` 后端——多个读端点缺对象级授权(IDOR),以及里程报销首行费率算错**。

> 说明:最严重的几条在**后端** expense-api,是审 OA 数据链时带出来的;已标注 [后端]。

---

## 🔴 P1 — 安全 / 资金 / 丢数据(已逐条复核)

### 1. [后端] 对象级授权缺失(IDOR)—— 多个读/删端点只验登录不验归属
以下端点依赖均为 `_: CurrentUserDep`(把 user 丢弃,结构上无法校验归属),已核对 main 版:
- `GET /pa/{id}`([pa.py:229](../expense-api/app/api/v1/pa.py#L229))、`GET /pa/{id}/history`([:278](../expense-api/app/api/v1/pa.py#L278))
- `GET /pa/by-po/{po_id}`([:117](../expense-api/app/api/v1/pa.py#L117))—— 对任意 po_id 返回该 PO 下**全部 PA 完整明细**,还把 PA 的 UUID 喂出来,使上面的明细 IDOR 可被枚举
- `GET /expenses/{id}`、`/{id}/approval-status`
- invoice/expense 附件 `list` / `serve(/file)` / `delete`([invoice_attachments.py:90/108/124](../expense-api/app/api/v1/invoice_attachments.py#L108))

**对比**:写端点 `update_pa`([pa.py:213](../expense-api/app/api/v1/pa.py#L213))有 `created_by != user_id → 403`;读端点没有。前端 `isOwner` 只是隐藏按钮的 UX 遮挡。
**影响**:任一登录员工带自己 token 直接请求即可**读取/删除他人财务单据与发票凭证(PII/金额/供应商)**。invoice 附件 DELETE 连状态闸都没有。
**建议**:读/删端点补作用域校验(经 invoice→PA→created_by / claim.employee_id + 审批参与人),与列表端点同一套作用域逻辑;`by-po` 校验调用方(原为 EPMS 内部链路)。

### 2. MIL 里程首行冻结 fallback 费率 0.72 → 报销金额算错(两代理独立命中)
[MilCreatePage.tsx:79-84](../oa/src/pages/expenses/MilCreatePage.tsx#L79):`useState` 用 `emptyTrip(1, rate, defaultAccountId, …)` 初始化,而 `rate`/`defaultAccountId` 来自 `expense-policy` 查询——**冷渲染(直达/硬刷新)时 policy=undefined**,首行落 `rate_per_km='0.72'`、`budget_account_id=null`。注释 `// Sync rate when policy loads` + `const currentRate = rate` 是**空操作**(没有 effect 回填首行)。后端 `_compute_totals_mil` **信任前端传的 rate**,不回读 policy。
**影响**:组织费率≠0.72(如 CRA 0.70)时,**首行金额算错**、且**无预算科目**入库(后端 Optional 静默接受)。后续"Add trip"的行正常。
**建议**:`trips` 初始化不吃 policy;加 `useEffect([policy])` 回填仍持默认值的行,或首行渲染 gate 在 policy 就绪后。

---

## 🟠 P2 — 错数据 / 卡死 / 陈旧

### 3. PA-DIR 空供应商名 `includes('')` → 自动错配供应商还显示 Matched ✓
[PaDirectCreatePage.tsx:331-341](../oa/src/pages/pa/PaDirectCreatePage.tsx#L331):OCR 无供应商名时 `extractedName=''`,`vendors.find(v => v.name.includes('') || …)` 因 `String.includes('')` 恒真 → 选中 `vendors[0]`,并渲染绿色"Matched ✓"。用户信任绿勾即可对**错误供应商**建 PA-DIR(真实付款 + dedup key)。
**建议**:effect 加 `if (!extractedName) return`,匹配要求非平凡长度,禁用 `includes('')`。

### 4. PA-DIR 非原子创建 → 孤儿发票 + 锁死重试(两代理命中)
[PaDirectCreatePage.tsx:730-795](../oa/src/pages/pa/PaDirectCreatePage.tsx#L730):先 `POST /invoices`(**服务端同时跑 `finance_sync.sync_ap_invoice`**),再 `POST /pa/direct`。若 PA 步失败,invoice + AP 行已存;重试时 invoice POST 撞 `(vendor_id, invoice_number)` dedup → **409 "already recorded"**,用户**永远建不成 PA**,留孤儿发票 + 孤儿 AP 行需手工清。
**建议**:重试复用已建 invoice(按 vendor+number 查已存并继续建 PA),或 PA 先建 / 单事务端点,或延后 AP 同步到 PA 成功后。

### 5. PA 列表缓存 key 不匹配 → 审批/付款后列表陈旧
[PaDetailPage.tsx:741](../oa/src/pages/pa/PaDetailPage.tsx#L741) `invalidateAll()` 失效 `['pa-list']`,但列表 query key 是 `['pa-list-dir', status, page, pageSize]`([PaListPage.tsx:35](../oa/src/pages/pa/PaListPage.tsx#L35))——**永不命中**,审批/return/reject/recall/pay 后列表不刷新直到硬刷新。对照 ExpenseDetailPage 用 `['expense-list']` 前缀是对的。
**建议**:失效 `['pa-list-dir']` 或统一前缀。

### 6. Admin 路由无守卫 → 非管理员可打开并读配置
[routes.tsx:43](../oa/src/app/routes.tsx#L43):`/admin/expense-config`、`/admin/custom-forms` 无条件注册;`isAdmin`(`role==='system_admin'`,来自 localStorage)只用于隐藏侧栏链接。手输 URL 即可渲染 Admin 页。后端**写**端点有 403 兜底,但 **读** `GET /policy`、`GET /custom-forms` 只验登录 → 报销政策/自定义表单配置越权可见。
**建议**:前端 admin 路由加角色守卫;后端两个 GET 加 admin 校验。

### 7. 跨服务客户端无 401 处理 + `refreshToken` 死代码
[api.ts:147-207](../oa/src/lib/api.ts#L147):只有主 `request`/`postForm`/`getBlob` 处理 401;`epmsRequest`/`budgetRequest`/`mdmRequest`/`financeRequest` **不处理** → 令牌过期时打这些服务(AccountPicker/ProcessPaymentModal 等)只抛通用错、不登出。`auth.ts` 持久化 `refreshToken` 但**全库无人使用**——每次过期都是硬登出而非刷新。
**建议**:抽一个 `handle401()` 给所有 client;要么实现单飞刷新,要么删 `refreshToken` 别误导。

### 8. [后端授权设计] `_CAN_PAY` / `_INBOX_STEP_ROLES` 硬编码角色,不走配置/Access Control
见架构一致性 ②③(下)。付款权限与任务收件箱用硬编码角色集,workflow/Access Control 改了会漂移。

---

## 🟡 P3 — 边界 / 类型 / 健壮性

- **stray `0` 渲染**:[ExpenseDetailPage.tsx:542](../oa/src/pages/expenses/ExpenseDetailPage.tsx#L542) `{claim.total_km && (…)}`,total_km=0 时渲染字面 "0"。用 `!= null`。
- **receipt scan 丢 OCR 税额**:[TrvCreatePage.tsx:159](../oa/src/pages/expenses/TrvCreatePage.tsx#L159) 用 net×HST 重算税,丢弃扫描到的 `tax_amount`(外币/免税/舍入会不符)。ExpenseCreatePage:382 同类(忽略行选定 tax_code)。
- **搜索无 debounce**:InvoicesPage、PaDirect 供应商框每键一请求(queryKey 含 search)。加 ~250ms 防抖。
- **无请求超时**:[api.ts:66](../oa/src/lib/api.ts#L66) fetch 无 AbortController → 挂起=无限转圈;网络错原样抛 `Failed to fetch`。
- **actor_role null 崩 History**:[PaDetailPage.tsx:674](../oa/src/pages/pa/PaDetailPage.tsx#L674) `ev.actor_role.replace(...)` 未 guard(下面 comment 有 guard)。用 `(ev.actor_role ?? '')`。
- **`window.location.href` 破坏多页签**:[PaListPage.tsx:115](../oa/src/pages/pa/PaListPage.tsx#L115) 行点击整页重载,毁 shell SPA 状态;别处用 `navigate()`。
- **query error 静默降级空**:taxCodes/accounts/policy 失败静默 `[]` → 表单被 submit 过滤挡住却无提示(budget-api 挂时"No categories")。surface `isError`。
- **CfmAdminPage 渲染中 setState**:[CfmAdminPage.tsx:205](../oa/src/pages/admin/CfmAdminPage.tsx#L205) render body 里 `setForms(...)`,应 useEffect/select。
- **大量 `any`**:`onSuccess:(data:any)=>replaceTab(\`/…/${data.id}\`)`(4 页,后端变形则跳 `/undefined`)、`catch(e:any)` 遍布。mutation 给显式泛型。
- **localhost 兜底 URL ×4**:EPMS/BUDGET/MDM/FINANCE_BASE 缺 build arg → 静默打 localhost(既知坑 ×3,见记忆 dockerfile_missing_build_arg)。prod 启动应 fail-fast。

---

## 架构一致性(用户重点 ①②③)

### ① 主数据是否引用 Portal Admin 正源 —— ✅ 基本合规
UOM(`/uom` mdm)、税码(`/tax/codes` mdm)、预算科目(budget-api)、成本中心(epms)、银行账户、餐补限额/里程费率/HST(expense policy `/policy`)全走 API 正源。
**瑕**:币种硬编码 CAD/USD([ExpenseCreatePage:493](../oa/src/pages/expenses/ExpenseCreatePage.tsx#L493));`hst_rate` 双源(mdm tax_codes vs expense policy,可能漂移);TRV_CATEGORIES 类目结构硬编码。

### ② 审批流是否参照 Approval Flow 配置 —— ✅ 主链是,收件箱不是
`can_approve`(`_can_act_on_claim` 读 approval-api 写的 tasks 表,源自 `workflow_defs`)、动作执行、列表可见性、步骤展示均走 `workflow_defs`(Approval Flow 配置)✅。
**❌** `_INBOX_STEP_ROLES`([expenses.py:31](../expense-api/app/api/v1/expenses.py#L31))硬编码 step→role,注释自认 *"If the workflow is customised this may drift"* —— Approval Flow 改了收件箱对不上。

### ③ 权限是否全部参照 Access Control —— ⚠️ 前端合规,后端两处硬编码绕过
**前端** OA 不判角色,审批/付款按钮查后端 `perms` 端点 ✅(唯一硬编码 `AppLayout:76 role==='system_admin'`)。
**后端** `can_approve` 走 tasks/workflow ✅;但 **`_CAN_PAY`**([expenses.py:25](../expense-api/app/api/v1/expenses.py#L25))与 `_INBOX_STEP_ROLES` 是硬编码角色集,**不查 Access Control 矩阵**、也不查 workflow pay 步。expense-api 整体不引用 EPMS Access Control 矩阵。approval 用 workflow 合理,但 pay/inbox 应从配置派生而非写死,且与 EPMS「矩阵驱动」不一致。

---

## 可维护性 / 重复(清理债,量化)

**重复(最高优先):**
- `api.ts` 6 个 fetch 包装:token+header 块 6×、401 重定向块 3×,且跨服务 client **漏 401**。抽一个 `authFetch(base, path, opts, {handle401})`。
- `ActionModal` ×2(ExpenseDetail/PaDetail)、`ErrorBanner` ~10×、`formatSize` ×3、blob 下载 ×3、附件卡 ×3、发票行表 ×3、预算选择器 ×2、供应商框 ×2、列表脚手架 ×3、`today()` ×5、`titleCase` ×7(badge.tsx 已有但没 export)。
- **魔法状态串 26 处 / 7 文件**;`paid`(Expense/Task) vs `processed`(PA)终态漂移;action 串散落。→ 抽 `lib/status.ts` 常量。

**死代码:** `PaCreatePage.tsx` 是 "coming in OA Sprint 1" 占位(routes 注册但无人链接)、`fetchAllPages` 导出未用、PaDirect:789 if/else 同分支、`filteredVendors = vendors` 无意义别名。

**巨型文件:** PaDirectCreatePage **1053**、PaDetailPage **851**、ExpenseDetailPage **744**(5 种单据类型 switchboard)、ExpenseCreatePage **681**。PdfPreview/ImagePreview 应下沉 `components/`。

**干净:** 全库无 `console.*`/`TODO`/`FIXME`/`@ts-ignore`/注释代码块;无 XSS/`dangerouslySetInnerHTML`/`eval`;附件下载已用 `api.getBlob` 带鉴权。

---

## 修复优先级

1. **P1-1 后端 IDOR** —— 读/删端点补对象级授权(最高危,资金/PII 泄漏)。
2. **P1-2 MIL 首行费率** —— 修 useState 初始化(误付款)。
3. **P2-3/4 PA-DIR** —— 空名错配 + 孤儿发票重试锁死。
4. **P2-5 pa-list 缓存 key** —— 一行改动,消陈旧。
5. **P2-6 Admin 路由守卫 + 读端点鉴权**。
6. **②③ 硬编码授权** —— `_CAN_PAY`/`_INBOX_STEP_ROLES` 改配置驱动。
7. 可维护性:抽 `authFetch` + `lib/status.ts` + 共享组件;删 stub/死代码。
