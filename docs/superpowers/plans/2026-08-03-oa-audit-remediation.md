# OA 审计整改 实施计划（Program Plan）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把 `docs/style-audit-epms-vs-oa.md` + `docs/code-audit-oa.md` 两份审计的发现,拆成按风险/紧急度排序的 5 个独立工作流,先落地本 Plan 详列的 **Phase 1(安全 + 正确性 P1/P2)**,其余 4 个工作流各出自己的 Plan。

**Architecture:** 每个工作流 = 一个独立分支 + worktree(遵守多会话发布纪律 R1/R2),单点汇合发布(R4)。后端改 `expense-api`(FastAPI + pytest,测试库 `epms_test`),前端改 `oa`(React 19 + Vite,tsc 门禁)。Phase 1 只碰授权与错误逻辑,**不做任何风格/重构**,把改动面压到最小、可快速上生产。

**Tech Stack:** FastAPI / SQLAlchemy async / pytest(后端);React 19 / react-query / zustand / TypeScript(前端)。

## Global Constraints

- 生产当前 = `6beac58`;main = `5c8a98f`(待部署)。**Phase 1 从 `main` 切分支**,别基于工作树(当前 detached HEAD `1fb2c27` 且 epms/oa 已删)。
- 一会话一分支、一 worktree;随手 commit 零 WIP;发布单点汇合全 15 镜像同 sha(见记忆 `feedback_uniops_multisession_release_discipline`)。
- 后端测试库 `epms_test`,同刻只跑一个套件,基线 72 failed 存量——**对基线增量判断,别信"无输出=通过"**(记忆 `feedback_verification_positive_evidence` / `test_db_concurrency`)。
- 前端 UI 文案全英文;OA 用绝对 URL(Vite proxy 已死);tsc 门禁 `tsc -p tsconfig.app.json`。
- 授权判定不信 localStorage 的 user 对象,以后端为准。
- 本 Phase **不改** DB schema(无迁移),不动 Caddyfile。

---

## 工作流分解与排序（方案总览）

| 工作流 | 内容 | 风险 | 紧急 | 归属 Plan |
|---|---|---|---|---|
| **WS-A 安全**(Phase 1) | 后端 IDOR 对象级授权 + Admin 读端点鉴权 | 高危(资金/PII 泄漏) | 🔴 立即 | **本 Plan** |
| **WS-B 正确性**(Phase 1) | MIL 费率、PA-DIR 错配/孤儿、pa-list 缓存、前端 Admin 守卫、若干 P3 | 中(错数据) | 🔴 立即 | **本 Plan** |
| WS-C 授权配置化 | `_CAN_PAY`/`_INBOX_STEP_ROLES` 改配置驱动;②③ | 中 | 🟠 次期 | 独立 Plan |
| WS-D 风格收敛 OA→EPMS | shell `Button`、共享 `ActionModal`、`FormActionBar`、审批按钮布局 | 中(大面积 UI) | 🟡 排期 | 独立 Plan(见 style 审计第 7 节) |
| WS-E 可维护性 | `authFetch` 消重、`lib/status.ts`、共享组件、删死代码、拆巨型文件 | 低 | 🟡 排期 | 独立 Plan |

**排序理由**:WS-A 是唯一的真安全洞,先修;WS-B 与 WS-A 同为"改逻辑不改结构",可同一发布窗口出。WS-C 依赖对 workflow_defs 结构的设计,单独做。WS-D/E 是大重构,受 WS-B 影响的文件(PaDetail/ExpenseDetail/PaDirectCreate)会被 WS-B 先动一遍,故排在后面避免冲突。

**发布策略**:Phase 1 = 一个分支 `fix/oa-audit-p1`(或两个:`fix/expense-api-idor` + `fix/oa-correctness`,若想分开审)→ 汇合发布。WS-A 后端改动需重建 `expense-api` 镜像;WS-B 前端改动需重建 `oa` 镜像;按发布法 build+push 全 15:<sha>。

---

## Phase 1 文件清单

**后端(WS-A):**
- Modify `expense-api/app/api/v1/expenses.py` — 新增 `_can_view_claim()`;给 `get_expense`/`approval-status` 加校验。
- Modify `expense-api/app/api/v1/pa.py` — 新增 `_can_view_pa()`;给 `get_pa`/`get_pa_history`/`list_pas_by_po` 加校验。
- Modify `expense-api/app/api/v1/invoice_attachments.py` — 给 `list`/`serve`/`delete` 加归属校验。
- Modify `expense-api/app/api/v1/expense_attachments.py` — 给 `list`/`serve`/`delete` 加归属校验(经 claim)。
- Modify `expense-api/app/api/v1/policy.py`、`custom_forms.py` — 读端点加 admin 校验。
- Test: `expense-api/tests/test_authz_scope.py`(新建)。

**前端(WS-B):**
- Modify `oa/src/pages/expenses/MilCreatePage.tsx` — 费率回填。
- Modify `oa/src/pages/pa/PaDirectCreatePage.tsx` — 供应商空名守卫 + 重试复用 invoice。
- Modify `oa/src/pages/pa/PaDetailPage.tsx` — pa-list 缓存 key + actor_role guard。
- Modify `oa/src/pages/expenses/ExpenseDetailPage.tsx` — total_km stray 0。
- Modify `oa/src/app/routes.tsx` + `oa/src/components/layout/AppLayout.tsx` — Admin 路由角色守卫。

---

## Task 0: 建 Phase 1 worktree（前置）

**Files:** 无代码改动。

- [ ] **Step 1:** 从 main 建 worktree(用 superpowers:using-git-worktrees 或):

```bash
cd /c/Project/uniops
git worktree add -b fix/oa-audit-p1 ../uniops-oa-audit-p1 main
```

- [ ] **Step 2:** 确认 worktree 干净且在 main:

```bash
cd ../uniops-oa-audit-p1 && git log --oneline -1   # 期望 5c8a98f
git status -s                                        # 期望空
```

- [ ] **Step 3:** 起后端测试环境(容器内 or 覆盖 POSTGRES_* 指向本地 docker `uniops_postgres`,库 `epms_test`;别打生产 10.10.50.20,见记忆 `host_env_points_at_prod`)。跑一次基线记录 failed 数(对比用)。

---

## WS-A 后端安全

### Task A1: PA 读端点对象级授权

**Files:**
- Modify: `expense-api/app/api/v1/pa.py`（`get_pa` 229、`get_pa_history` 278、`list_pas_by_po` 117）
- Test: `expense-api/tests/test_authz_scope.py`

**Interfaces:**
- Produces: `async def _can_view_pa(db, pa, user_id: uuid.UUID, role: str) -> bool`
- Consumes: 既有 `_can_act_on_claim`(expenses.py，读 tasks 表判审批参与)、`_CAN_PAY`。

- [ ] **Step 1: 写失败测试**(非 owner、非参与者读他人 PA 应 403;owner / 审批参与者 / _CAN_PAY / admin 应 200)

```python
# expense-api/tests/test_authz_scope.py
import pytest, uuid

@pytest.mark.asyncio
async def test_get_pa_forbidden_for_unrelated_user(client, seed_pa, make_token):
    pa = await seed_pa(created_by=uuid.uuid4())              # 属于别人
    r = await client.get(f"/api/v1/pa/{pa.id}",
                         headers={"Authorization": f"Bearer {make_token(role='employee')}"})
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_get_pa_ok_for_owner(client, seed_pa, make_token):
    uid = uuid.uuid4()
    pa = await seed_pa(created_by=uid)
    r = await client.get(f"/api/v1/pa/{pa.id}",
                         headers={"Authorization": f"Bearer {make_token(sub=str(uid), role='employee')}"})
    assert r.status_code == 200
```

- [ ] **Step 2: 跑测试确认 FAIL**（当前无 403 → 第一个用例失败）
Run: `pytest expense-api/tests/test_authz_scope.py -v`
Expected: FAIL(返回 200 而非 403）

- [ ] **Step 3: 实现 `_can_view_pa` 并接入三个端点**

```python
# pa.py — 新增（放在 get_pa_permissions 附近，复用其判定逻辑）
async def _can_view_pa(db, pa, user_id: uuid.UUID, role: str) -> bool:
    if pa.created_by == user_id:
        return True
    if role == "system_admin":
        return True
    from app.api.v1.expenses import _CAN_PAY, _can_act_on_claim
    if role in _CAN_PAY:
        return True
    # 审批参与者（当前或历史步）— _can_act_on_claim 只读 .id，对 PA 同样适用
    return await _can_act_on_claim(db, pa, user_id)

# get_pa：把签名从 `_: CurrentUserDep` 改成 `user: CurrentUserDep` 并校验
@router.get("/{pa_id}", response_model=PaResponse)
async def get_pa(pa_id: uuid.UUID, db: SessionDep, user: CurrentUserDep):
    pa = await pa_crud.get_by_id(db, pa_id)
    if not pa:
        raise HTTPException(status_code=404, detail="PA not found")
    if not await _can_view_pa(db, pa, uuid.UUID(user["sub"]), user.get("role", "")):
        raise HTTPException(status_code=403, detail="Not authorized to view this PA")
    return PaResponse.model_validate(pa)
```
`get_pa_history` 同法（先取 pa，再 `_can_view_pa`，否则 403）。`list_pas_by_po`:它是 EPMS 链路内部调用——加同样的 `_can_view_pa` 过滤返回项(只返回调用者有权看的 PA),而非整表泄漏。

- [ ] **Step 4: 跑测试确认 PASS**，并确认基线 failed 数无新增
Run: `pytest expense-api/tests/test_authz_scope.py -v`

- [ ] **Step 5: Commit**
```bash
git add expense-api/app/api/v1/pa.py expense-api/tests/test_authz_scope.py
git commit -m "fix(expense-api): add object-level authz to PA read endpoints (IDOR)"
```

### Task A2: Expense 读端点对象级授权

**Files:** Modify `expense-api/app/api/v1/expenses.py`（`get_expense`、`get_approval_status`）

**Interfaces:** Produces `async def _can_view_claim(db, claim, user_id, role) -> bool`。

- [ ] **Step 1: 写失败测试**（非相关 employee 读他人 expense claim 应 403;owner/参与者/_CAN_PAY/admin 200）——照 A1 结构,针对 `/api/v1/expenses/{id}`。
- [ ] **Step 2: 跑测试 FAIL。**
- [ ] **Step 3: 实现 `_can_view_claim`**（复用 A 的判定:owner=`claim.employee_id==user_id`,或 admin,或 `role in _CAN_PAY`,或 `_can_act_on_claim`,或 role 在该 claim_type 的 `workflow_defs` 步里——后者复用 `list_expenses` 里 `_roles_for(_workflow_key(claim.claim_type))` 的现成逻辑)。接入 `get_expense` / `get_approval_status`（`_:`→`user:` + 校验）。
- [ ] **Step 4: 跑测试 PASS，核对基线。**
- [ ] **Step 5: Commit** `fix(expense-api): add object-level authz to expense read endpoints (IDOR)`。

### Task A3: 附件端点归属校验

**Files:** Modify `expense-api/app/api/v1/invoice_attachments.py`、`expense_attachments.py`

- [ ] **Step 1: 写失败测试**——非相关用户 `GET /invoice-attachments/{id}/file`、`DELETE /invoice-attachments/{id}`、`GET /expense-attachments?claim_id=` 应 403。
- [ ] **Step 2: FAIL。**
- [ ] **Step 3: 实现**:
  - `expense_attachments.*`:attachment → `claim_id` → `expense_crud.get_by_id` → `_can_view_claim`;delete 额外保留既有"非 draft 不可删"闸。
  - `invoice_attachments.*`:attachment → 其 invoice → invoice 关联的 PA/created_by → 复用 `_can_view_pa`(或经 invoice.created_by + PA 参与)。把 `_:`→`user:`。**实现时确认** `InvoiceAttachment`→invoice 的 FK 字段名与 invoice→PA/created_by 的解析路径。
- [ ] **Step 4: PASS，核对基线。**
- [ ] **Step 5: Commit** `fix(expense-api): enforce ownership on attachment serve/delete/list (IDOR)`。

### Task A4: Admin 读端点加 admin 校验

**Files:** Modify `expense-api/app/api/v1/policy.py`（`GET /policy`）、`custom_forms.py`（`GET /custom-forms`）

- [ ] **Step 1: 写失败测试**——非 admin `GET /policy` 应 403(当前只验登录返回 200)。
- [ ] **Step 2: FAIL。**
- [ ] **Step 3: 实现**——按写端点已用的角色守卫(`finance_manager`/`system_admin`)加到这两个 GET。⚠️ 前端 ExpenseConfig 只有 admin 能进,不破坏正常用户;若有非 admin 页面读 policy(如 TrvCreate 读 `/policy` 拿餐补限额!)**先确认**——TrvCreate/MilCreate 也调 `/policy`,故 policy 读**不能**限 admin,只 `custom-forms` 的 admin 管理端点限 admin。**修正:policy GET 保持登录可读(餐补/费率是业务所需),只给 custom-forms 管理读加 admin。**
- [ ] **Step 4: 调整测试为 custom-forms admin-only,PASS。**
- [ ] **Step 5: Commit** `fix(expense-api): gate custom-forms admin read behind admin role`。

> A4 注:此条经复核**降级**——policy 读是 TrvCreate/MilCreate 拿餐补/费率的正常业务调用,不能锁 admin。仅 custom-forms 管理读加 admin。前端越权显示由 WS-B Task B5 的路由守卫兜。

---

## WS-B 前端正确性

### Task B1: MIL 费率首行冷加载回填

**Files:** Modify `oa/src/pages/expenses/MilCreatePage.tsx:79-84`

- [ ] **Step 1:** 首行初始化不吃未加载的 policy——改为 policy 就绪后回填仍持默认值的行。

```tsx
// 79-84 附近，替换裸 useState 初始化 + 加 effect
const [trips, setTrips] = useState<TripItem[]>([
  emptyTrip(1, rate, defaultAccountId, null, null),
])

// policy 到达后，回填仍是默认费率/无科目的行（用户未手动改过的）
useEffect(() => {
  if (!policy) return
  setTrips((prev) => prev.map((t) => {
    const patch: Partial<TripItem> = {}
    if (t.rate_per_km !== rate) patch.rate_per_km = rate
    if (t.budget_account_id == null && defaultAccountId != null) patch.budget_account_id = defaultAccountId
    if (!Object.keys(patch).length) return t
    const merged = { ...t, ...patch }
    merged.amount = calcAmount(merged.distance_km, merged.rate_per_km, merged.is_round_trip)
    return merged
  }))
}, [policy])   // rate/defaultAccountId 由 policy 派生，随其变化
```
> 语义抉择:此实现会把**所有**当前费率行同步到新 policy 费率(用户没有单行费率输入框,费率非用户可编辑数据,统一按组织费率正确)。若担心"用户中途改过距离后被 setTrips 重算金额"——本 effect 只在 policy 变化时跑一次(冷加载→加载完),不会反复覆盖。

- [ ] **Step 2: 验证** `tsc -p tsconfig.app.json` 无新增错误(对齐基线 59)。
- [ ] **Step 3: 手动验证**:改 ExpenseConfig 费率为 ≠0.72(如 0.70)→ 硬刷新直达 `/expenses/new/mileage` → 首行顶部显示 0.70 且输距离后金额按 0.70 算(不是 0.72)。
- [ ] **Step 4: Commit** `fix(oa): backfill MIL first-trip rate/account when policy loads`。

### Task B2: PA-DIR 供应商空名守卫

**Files:** Modify `oa/src/pages/pa/PaDirectCreatePage.tsx:331-341`

- [ ] **Step 1:** effect 加空名短路 + 禁 `includes('')`:

```tsx
useEffect(() => {
  if (selectedVendorId || vendors.length === 0) return
  const extractedName = (initial.vendorName ?? '').trim().toLowerCase()
  if (extractedName.length < 3) return              // OCR 无名/太短 → 不自动匹配
  const exact = vendors.find(v => v.name.toLowerCase() === extractedName)
  const partial = vendors.find(v => {
    const n = v.name.toLowerCase()
    return n.includes(extractedName) || extractedName.includes(n)
  })
  const match = exact ?? partial
  if (match) setSelectedVendorId(match.id)
}, [vendors])
```
- [ ] **Step 2: tsc 验证。**
- [ ] **Step 3: 手动**:上传无供应商名的收据 → 不再自动选中 vendors[0]、不显示 Matched ✓。
- [ ] **Step 4: Commit** `fix(oa): don't auto-match vendor on empty OCR name (includes('') always true)`。

### Task B3: PA-DIR 重试复用已建 invoice

**Files:** Modify `oa/src/pages/pa/PaDirectCreatePage.tsx:730-795`（`handleCreate`）

- [ ] **Step 1:** 把已建 invoice id 存 state,重试时跳过 re-create:

```tsx
const [createdInvoiceId, setCreatedInvoiceId] = useState<string | null>(null)
// handleCreate 内：
let invoiceId = createdInvoiceId
if (!invoiceId) {
  const inv = await api.post<{ id: string }>('/api/v1/invoices', invoicePayload)
  invoiceId = inv.id
  setCreatedInvoiceId(invoiceId)
  await api.postForm(`/api/v1/invoices/${invoiceId}/attachments`, form)  // 若已在建 invoice 时上传则相应调整
}
await api.post('/api/v1/pa/direct', { ...paPayload, invoice_id: invoiceId })
```
> 同时清理审计发现的死分支([:789](../../oa/src/pages/pa/PaDirectCreatePage.tsx#L789) if/else 同体):合并成一条 `setError(detail)`。
- [ ] **Step 2: tsc 验证。**
- [ ] **Step 3: 手动**:构造 PA 创建失败(如断网 PA 步)→ 再点 Create → 不再 409,复用 invoice 成功建 PA。
- [ ] **Step 4: Commit** `fix(oa): reuse created invoice on PA-DIR retry to avoid orphan + 409 lock`。

### Task B4: PA 列表缓存 key 修复 + actor_role guard

**Files:** Modify `oa/src/pages/pa/PaDetailPage.tsx`（invalidate 741、history 674）

- [ ] **Step 1:** `invalidateAll()` 里把 `['pa-list']` 改成 `['pa-list-dir']`(与 PaListPage query key 前缀一致);history 渲染 `(ev.actor_role ?? '').replace(...)`。
- [ ] **Step 2: tsc 验证。**
- [ ] **Step 3: 手动**:审批一张 PA → 返回列表状态即时更新(不需硬刷新)。
- [ ] **Step 4: Commit** `fix(oa): correct pa-list cache key on invalidate + guard null actor_role`。

### Task B5: Admin 路由前端角色守卫 + total_km stray 0

**Files:** Modify `oa/src/app/routes.tsx`、`oa/src/components/layout/AppLayout.tsx`、`oa/src/pages/expenses/ExpenseDetailPage.tsx:542`

- [ ] **Step 1:** 给 `/admin/*` 路由包一个 `<RequireAdmin>`(读 store role,非 `system_admin` → `<Navigate to="/" replace>`);`ExpenseDetailPage:542` 改 `{claim.total_km != null && (…)}`。
```tsx
// 一个小守卫组件（放 routes.tsx 或 components/）
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const role = useAuthStore((s) => s.user?.role)
  if (role !== 'system_admin') return <Navigate to="/" replace />
  return <>{children}</>
}
```
> 注:这是 UX 守卫;真正的读保护由 WS-A / 后端兜。放 Phase 1 因改动极小。
- [ ] **Step 2: tsc 验证。**
- [ ] **Step 3: 手动**:非 admin 手输 `/admin/expense-config` → 重定向到首页;total_km=0 的 MIL 详情不再显示裸 "0"。
- [ ] **Step 4: Commit** `fix(oa): guard admin routes by role + fix total_km stray zero render`。

---

## Phase 1 收尾

- [ ] 全量 tsc(`oa`)对齐基线 59;后端套件 failed 数无新增。
- [ ] 汇合发布:重建 `expense-api` + `oa` 镜像,按发布法 build+push 全 15:<sha>,app 端 pull + edge up -d(无迁移,免 migrate-prod.sh)。
- [ ] 部署后冒烟:①用普通员工 token 直接 `GET /pa/{别人id}` → 403;②MIL 首行费率;③PA 审批后列表刷新。

---

## Phase 2-5 路线图（各自出 Plan）

- **WS-C 授权配置化**:`_CAN_PAY`/`_INBOX_STEP_ROLES` → 从 `workflow_defs`(含 pay 步)派生;消除 drift。需先设计 workflow_defs 的 pay-step 表达。产出 `docs/superpowers/plans/<date>-expense-authz-config-driven.md`。
- **WS-D 风格收敛 OA→EPMS**(style 审计第 7 节):OA 接入 shell `Button`(清 98 裸 button + 16 硬编码 `bg-primary-700`)、共享 `<ApprovalActionModal>` 抽到 shell、`<FormActionBar>`、审批按钮布局对齐、校验错误对齐。大面积 UI,排在 WS-B 之后避冲突。
- **WS-E 可维护性**:`authFetch(base,path,opts,{handle401})` 收 6 个 fetch 包装并补跨服务 401、`lib/status.ts` 干掉 26 处魔法状态串 + `paid/processed` 漂移、共享 `ActionModal/ErrorBanner/AttachmentsCard/formatSize/downloadBlob/today/titleCase`、删 `PaCreatePage` stub + `fetchAllPages` 死导出、拆 PaDirectCreate(1053)。
- **①主数据尾巴**:币种改数据源或明确固定;`hst_rate` 与 mdm tax_codes 去重。

---

## Self-Review 备注

- 覆盖:code 审计 P1-1(A1/A2/A3)、P1-2(B1)、P2-3(B2)、P2-4(B3)、P2-5(B4)、P2-6(B5+A4)、P3 stray0/actor_role(B4/B5);②③→WS-C;风格→WS-D;重复/死代码→WS-E。
- A4 经复核已修正为"policy 读保持登录可读"(TrvCreate/MilCreate 依赖),避免误锁业务。
- 待实现时确认项(非 placeholder,是集成点):invoice_attachment→invoice→PA 的 FK 解析路径(A3)、`pa_crud.get_by_id` 存在性(已见 `expense_crud.get_by_id`,PA 侧同构确认)。
