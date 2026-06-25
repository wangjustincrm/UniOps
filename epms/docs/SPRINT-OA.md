# UniOps OA — Sprint Plan
**版本：** 1.0  
**日期：** 2026-04-28  
**依赖：** PRD-OA v1.3 · EPMS PRD v2.12  
**状态：** Planning

---

## 总览

| Sprint | 目标 | 周期 | 交付物 |
|--------|------|------|--------|
| S1 | 基础设施 + PA 迁移 | Week 1 | Portal、OA骨架、expense-api骨架、EPMS PA重定向 |
| S2 | EXP + MIL 报销 | Week 2 | 通用费用报销、里程报销、统一列表页 |
| S3 | PA-DIR + OCR | Week 3–4 | Invoice上传、AI识别、PA-DIR三步流程 |
| S4 | TRV + Admin + 收尾 | Week 4–5 | 差旅报销、Admin配置面板、自定义表单 |

**并行策略：** 每个 Sprint 内，Backend（expense-api）和 Frontend（oa/portal）可并行进行。Backend先于Frontend 1–2天启动，Frontend联调时有API可用。

**DX 决策（plan-devex-review 2026-04-28）：**
- Docker Compose **仅用于本地开发**，生产环境各服务分布到独立服务器
- `docker-compose.dev.yml`（明确命名，避免误用于生产）一键启动本地全栈
- `check-health.sh` 统一健康检查脚本（dev + prod 两种模式）
- 每个服务的跨服务 URL 全部来自环境变量，不硬编码 `localhost`，支持 dev/prod 切换
- Sprint 1 第一个任务：创建 `docker-compose.dev.yml` + `check-health.sh` + 根目录 README + `.env.example` 含 prod 配置说明

---

## Sprint 1 — 基础设施 + PA 迁移（Week 1）

### 🎯 Sprint 目标
让 PA 从 EPMS 迁移到 OA，不破坏任何现有审批流程。Portal 和 OA 骨架可以跑起来。

### DX 基础设施（最高优先级，在所有其他任务之前）

**B1-0a** 根目录 `README.md`

内容：
```markdown
# UniOps — Enterprise Management Platform

## Local Development (Docker Compose)

Prerequisites: Docker Desktop

docker compose -f docker-compose.dev.yml up   # start all services
./check-health.sh                             # verify all healthy
./reset-db.sh --seed                          # reset + seed data (dev only)
./run_tests.sh                                # run test suite

## Services (local dev ports)
| Service         | Port  | Directory           |
|-----------------|-------|---------------------|
| EPMS API        | :8000 | uniops/epms-api     |
| Approval Engine | :8003 | uniops/approval-api |
| MDM Stub        | :8002 | uniops/mdm-api      |
| Finance Core    | :8004 | uniops/finance-api  |
| File Server     | :8005 | uniops/file-api     |
| Expense API     | :8006 | uniops/expense-api  |
| Portal          | :5174 | uniops/portal       |
| EPMS Frontend   | :5173 | uniops/epms         |
| OA Frontend     | :5175 | uniops/oa           |

## Production Deployment
See DEPLOYMENT.md — services run on separate servers (DB server,
file server, app server). Docker Compose is NOT used in production.

## Adding a New Expense Form Type
See expense-api/README-DEV.md
```

**B1-0b** `docker-compose.dev.yml` — **开发环境专用，不用于生产**

> ⚠️ 生产环境各服务部署到独立服务器（数据库服务器、文件服务器、应用服务器）。
> 此文件仅用于本地开发快速启动全栈。

包含：
- PostgreSQL 15（含健康检查）
- 所有 FastAPI 服务（volume mount 代码目录，`--reload` 模式，`depends_on: postgres`）
- 所有 Vite 前端（volume mount src 目录，热更新）
- 每个服务 `healthcheck: test: curl -f http://localhost:{port}/health`
- 从根目录 `.env.dev` 加载环境变量

启动命令：
```bash
docker compose -f docker-compose.dev.yml up
```

**B1-0c** 统一 `.env.example` — 支持 dev/prod 两种模式

每个服务目录下的 `.env.example` 展示两种配置，让开发者清楚地知道生产环境如何切换：

```bash
# .env.example（每个服务目录下一份）
# ─── Dev（本地 Docker Compose）──────────────────────────
DATABASE_URL=postgresql+asyncpg://epms:epms_dev@localhost:5432/epms

# ─── Prod（生产服务器，各 URL 指向真实服务器）──────────
# DATABASE_URL=postgresql+asyncpg://epms:{password}@db.internal:5432/epms

# ─── 跨服务 URL（dev 用 localhost，prod 用内网 IP/域名）─
APPROVAL_ENGINE_URL=http://localhost:8003/approval/v1
# APPROVAL_ENGINE_URL=http://approval-server.internal:8003/approval/v1

FILE_SERVER_URL=http://localhost:8005/files/v1
# FILE_SERVER_URL=http://file-server.internal:8005/files/v1

EXPENSE_API_URL=http://localhost:8006/api/v1
# EXPENSE_API_URL=http://app-server.internal:8006/api/v1

# ─── 共享（dev/prod 相同结构）────────────────────────────
JWT_SECRET_KEY=change-this-in-production
JWT_ALGORITHM=HS256
```

**生产配置原则：**
- 每台服务器只部署相关服务，只配置该服务需要的环境变量
- 跨服务通信使用内网地址（不暴露到公网）
- `JWT_SECRET_KEY` 所有服务使用同一个值（共享认证）
- DB 服务器只对应用服务器的内网 IP 开放 5432 端口

**B1-0d** `check-health.sh` — 统一健康检查（dev + prod 两种模式）

```bash
#!/usr/bin/env bash
# Dev mode: checks localhost ports
# Prod mode: EPMS_HOST=app-server.internal ./check-health.sh

HOST=${EPMS_HOST:-localhost}

check() {
  curl -sf "http://${HOST}:$2/health" >/dev/null \
    && echo "✅ $1 :$2  healthy" \
    || echo "❌ $1 :$2  NOT RESPONDING"
}

check epms-api     8000
check approval-api 8003
check mdm-api      8002
check finance-api  8004
check file-api     8005
check expense-api  8006
```

**B1-0e** `reset-db.sh` — **仅开发环境，不用于生产**

```bash
#!/usr/bin/env bash
# DEV ONLY: drops and recreates the local development database
echo "⚠️  DEV ONLY: This will destroy all local data."
read -p "Continue? (y/N) " -n 1 -r; echo
[[ $REPLY =~ ^[Yy]$ ]] || exit 1

docker compose -f docker-compose.dev.yml exec postgres \
  psql -U epms -c "DROP DATABASE IF EXISTS epms; CREATE DATABASE epms;"

for svc in epms-api mdm-api finance-api file-api expense-api; do
  docker compose -f docker-compose.dev.yml run --rm $svc alembic upgrade head
done

[ "$1" = "--seed" ] && \
  docker compose -f docker-compose.dev.yml run --rm epms-api python -m scripts.seed
```

**B1-0f** 生产部署说明 — 根目录 `DEPLOYMENT.md`

```markdown
# UniOps — Production Deployment

## Server Layout (Phase 2)

| Server Role      | Services                                          |
|------------------|---------------------------------------------------|
| Database Server  | PostgreSQL 15 (port 5432, internal network only)  |
| File Server      | file-api (:8005) + /file-storage/ disk mount      |
| App Server 1     | epms-api (:8000) + approval-api (:8003)           |
|                  | + mdm-api (:8002) + finance-api (:8004)           |
| App Server 2     | expense-api (:8006)                               |
| Web Server       | portal (:5174) + epms frontend (:5173)            |
|                  | + oa frontend (:5175) + Nginx reverse proxy       |

## Per-Service Setup (each server)
1. Install Python 3.12 + Node 20
2. Clone repo, cd into service directory
3. Copy .env.example → .env, fill in production values
4. Run: alembic upgrade head
5. Start: uvicorn app.main:app --host 0.0.0.0 --port {PORT}

## Shared Requirements Across All App Servers
- Same JWT_SECRET_KEY value
- Network access to Database Server :5432
- Network access to File Server :8005
- Network access to Approval Engine :8003
```

---

### Backend: `expense-api` 骨架

**B1-1** `uniops/expense-api` 项目初始化
- FastAPI + SQLAlchemy async，端口 `:8006`
- 目录结构：`app/{api/v1, core, crud, db, models, schemas, services}/`
- `.env`：`DATABASE_URL`, `JWT_SECRET_KEY`, `APPROVAL_ENGINE_URL`, `FILE_SERVER_URL`
- `requirements.txt`：fastapi, sqlalchemy, alembic, asyncpg, httpx, python-multipart
- `app/main.py` + `app/core/config.py` + `app/core/deps.py`（复用 EPMS 的 JWT 验证模式）
- `app/db/base.py`（复用 EPMS 的 UUIDPrimaryKey + TimestampMixin 模式）
- `GET /health` 健康端点
- 结构化日志：每次外部 HTTP 调用输出 `{service, doc_type, doc_id, action, status_code, duration_ms}`；502 时额外输出 `"Is {service} running on :{port}?"`
- 启动时检查 pending Alembic migrations：有则输出 `WARNING: expense-api has N pending migration(s). Run: alembic upgrade head`（不阻断启动）
- `expense-api/README-DEV.md`：新增 expense 类型的文件清单（models / schemas / crud / migration / oa component）

**B1-2** PA 数据模型（迁移自 EPMS，扩展 `pa_type`）

新增字段到现有 `payment_applications` 表（Alembic migration）：
```sql
ALTER TABLE payment_applications
  ADD COLUMN pa_type VARCHAR(10) DEFAULT 'PA-PO' NOT NULL,
  ADD COLUMN expense_invoice_id UUID REFERENCES expense_invoices(id) NULLABLE;
```

新建 `expense_invoices` 和 `expense_invoice_lines` 表（Alembic migration）：
见 PRD §15.2 数据模型。

**B1-3** PA CRUD endpoints
- `GET  /api/v1/pa` — 列表（分页，支持 type/status/vendor_id 过滤）
- `POST /api/v1/pa` — 创建（PA-PO 和 PA-DIR，PA-DIR 此阶段要求 advance 类型，Regular 需 invoice 在 S3 完成）
- `GET  /api/v1/pa/{id}` — 详情
- `PATCH /api/v1/pa/{id}` — 更新（仅 draft/returned 状态）
- `POST /api/v1/pa/{id}/action` — 审批动作（委托 approval-api `:8003`）
- `POST /api/v1/pa/{id}/pay` — 记录银行转账（approved → paid）
- PA attachment upload/download（复用 file-api 模式）

**B1-4** EPMS PA 数据读取代理端点（供 EPMS DocumentChainTree 调用）
- `GET /api/v1/pa/by-po/{po_id}` — 返回某 PO 下的 PA 列表
- 供 EPMS Document Chain Tree 读取，替代直接 DB 查询

**完成标准：**
- `GET /health` 返回 200
- `POST /api/v1/pa` 创建 PA-PO，提交后 approval-api 创建审批任务
- EPMS 现有 PA 记录可通过 `expense-api` PA 详情端点读取

---

### Frontend: `portal` 初始化

**F1-1** `uniops/portal` Vite + React app 初始化（端口 `:5174`）
- 复制 EPMS 的 `vite.config.ts`、`tailwind.config` 基础配置
- 共享 `portal-header` 包结构（Phase 2 先作为 portal 内部组件，S4 提取为共享包）
- 路由：`/` → Portal Home，`/login` → 重定向 EPMS 登录

**F1-2** Portal Home 页面
- 页面读取 `localStorage` 的 JWT token（与 EPMS 共享）
- 未登录 → 重定向到 EPMS `/login`
- 已登录 → 显示两个大卡片（见 PRD §17.9）：
  ```
  Welcome back, {user.full_name}
  ┌────────────────────────────────────────────────────────┐
  │  EPMS — Enterprise Procurement                    →   │
  │  {N} tasks pending  · Last visited: {time}            │
  └────────────────────────────────────────────────────────┘
  ┌────────────────────────────────────────────────────────┐
  │  OA — Expense & Payment Management                →   │
  │  {N} items pending  · Last visited: {time}            │
  └────────────────────────────────────────────────────────┘
  ```
- 两个卡片分别从 `epms-api /api/v1/tasks?count_only=true` 和 `expense-api /api/v1/stats/pending` 拉取待办数量

**完成标准：**
- Portal Home 显示两个模块卡片
- 点击 EPMS 卡片跳转到 `:5173`，点击 OA 卡片跳转到 `:5175`
- 未登录时重定向到 EPMS 登录页

---

### Frontend: `oa` 初始化

**F1-3** `uniops/oa` Vite + React app 初始化（端口 `:5175`）
- 复制 EPMS 的依赖配置（TanStack Query, React Router v7, Tailwind）
- 共用 EPMS 的 `auth.store.ts` JWT 逻辑（复制，不共享包）
- App shell：左侧边栏 + 顶部 Header（含 Portal 返回按钮「← UniOps」）

**F1-4** OA Sidebar 导航结构
```
EXPENSES
  Expense Claims
  
PAYMENTS
  Payment Applications
  
[← Back to UniOps Portal]  (底部)
```

**F1-5** PA 列表页 `/pa`
- 复用 EPMS PrListPage 的表格模式
- 列：PA Number、Type Badge（`PO` teal / `Direct` grey）、Vendor、Amount、Status、Date
- 过滤：Type / Status / Date range
- 分页（10/20/50/100）
- "New PA" 下拉按钮：[PO-Linked PA] [Direct Payment (Advance)]

**F1-6** PA-PO 创建页 `/pa/new?po_id={uuid}&po_number={no}&source=epms`
- 读取 URL params，从 `expense-api GET /pa/po-context/{po_id}` 预填充表单
- 显示 Banner：「Creating payment for PO {po_number} from EPMS」
- 表单字段：见 PRD §14.1 PA-PO Form fields
- 提交后重定向到 PA 详情页

**F1-7** PA 详情页 `/pa/{id}`
- 显示所有 PA 字段、审批时间线、附件列表
- Finance BP/Manager 审批动作按钮
- 「Record Payment」按钮（approved 状态时显示）→ 打开支付记录 Modal

---

### EPMS 改动（`uniops/epms` + `uniops/epms-api`）

**E1-1** Sidebar PA 链接 → 重定向到 OA

`Sidebar.tsx`：将 Payment Applications 的 `href` 改为 OA URL，在链接旁加小角标「→ OA」：
```tsx
// 改动前：href: '/pa'
// 改动后：href: OA_BASE_URL + '/pa'，target="_self"（同标签页跳转）
```

**E1-2** PO Detail 页面 → 「Create PA」深链

`PoDetailPage.tsx`：找到「Create PA」按钮（如果有）或新增：
```tsx
<Button onClick={() => {
  window.location.href = `${OA_BASE_URL}/pa/new?po_id=${po.id}&po_number=${encodeURIComponent(po.number)}&source=epms`
}}>
  Create Payment Application →
</Button>
```

**E1-3** DocumentChainTree → PA 节点链接改为 OA

`DocumentChainTree.tsx`：PA 行的 `href` 改为 `${OA_BASE_URL}/pa/{pa.id}`，在新标签页打开：
```tsx
// 改动前：href={pa ? `/pa/${pa.id}` : undefined}
// 改动后：href={pa ? `${OA_BASE_URL}/pa/${pa.id}` : undefined}, target="_blank"
```

**E1-4** DocumentChainTree → 从 expense-api 读取 PA 数据

`usePos.ts` / `useGrs.ts` 类似模式，新增 `usePaByPoId(poId)` hook，调用 `expense-api /api/v1/pa/by-po/{po_id}` 而不是 `epms-api /api/v1/pa`。

**E1-5** EPMS PA 页面保留只读（暂不删除）

将 `/pa`, `/pa/:id` 路由改为只读视图（移除创建/编辑按钮），并在顶部显示 Banner：
「Payment Applications have moved to OA. [Open in OA →]」

PA create/edit 路由（`/pa/new`, `/pa/:id/edit`, `/pa/create`）重定向到 OA。

**完成标准：**
- EPMS 侧边栏 PA 点击 → 跳转 OA PA 列表
- EPMS PO 详情「Create PA」→ 跳转 OA PA-PO 表单（含预填充）
- EPMS Document Chain Tree PA 节点 → 新标签页打开 OA PA 详情
- OA PA 列表/详情/创建功能正常
- 现有 EPMS PA 数据可在 OA 中查看

---

## Sprint 2 — EXP 通用费用 + MIL 里程报销（Week 2）

### 🎯 Sprint 目标
第一种员工报销表单上线（EXP 是最高频使用的类型）。

### Backend: expense-api

**B2-1** expense_claims + expense_line_items + expense_attachments 数据模型（Alembic migration）
- 见 PRD §10.1 数据模型

**B2-2** EXP 报销 CRUD endpoints
- `POST /api/v1/expenses` — 创建（type=EXP）
- `GET  /api/v1/expenses` — 列表（所有类型，统一列表）
- `GET  /api/v1/expenses/{id}` — 详情
- `PATCH /api/v1/expenses/{id}` — 更新
- `POST /api/v1/expenses/{id}/action` — 审批动作（委托 approval-api）
- `POST /api/v1/expenses/{id}/pay` — 记录报销打款
- `POST /api/v1/expenses/{id}/attachments` — 上传附件（存 file-api）
- `GET  /api/v1/expenses/{id}/attachments` — 列表

**B2-3** MIL 里程报销 CRUD（同 EXP endpoints，type=MIL，额外字段：distance_km, rate_per_km）

**B2-4** 预算集成（批准时入账 actual_spent）
```python
# expense-api/app/services/budget.py
async def actualize_expense(db, claim_id):
    for line in claim.line_items:
        account = await db.get(BudgetAccount, line.budget_account_id)
        account.actual_spent += line.net_amount
    await db.flush()
```
调用 EPMS shared DB（Phase 2 共享 PostgreSQL，直接写 budget_accounts 表）。

**B2-5** `GET /api/v1/stats/pending` — 供 Portal Home 获取 OA 待办数量

**B2-6** `GET /api/v1/policy` — 读取 expense_policy_config
**B2-7** 初始化 expense_policy_config 单行记录（seed migration）

**完成标准：**
- 创建 EXP claim 后，approval-api 创建审批任务（Task Inbox 可见）
- 审批通过后 budget_accounts.actual_spent 正确增加
- MIL claim 金额 = distance_km × rate_per_km（从 policy 读取）

---

### Frontend: OA

**F2-1** 统一费用/PA 列表页 `/expenses`（或在 `/` 下）
- 两个 Tab：「Expenses」(EXP/TRV/MIL/CFM) 和「Payments」(PA)，或统一列表
- 列：Claim Number、Type Badge、Submitter、Total Amount、Status、Submitted Date
- 「New Claim」下拉：[General Expense (EXP)] [Travel Expense (TRV)] [Mileage Claim (MIL)]

**F2-2** EXP 创建/编辑页 `/expenses/new?type=EXP`

表单实现重点（见 PRD §2.2 + §17.5）：
- 表头：Employee（read-only）、Department（read-only）、日期、币种、项目
- 行项目表格：最多20行，列定义见 §17.5（#、Date、Description、Budget Account、Cost Centre、Total A、Tax B、Net C、删除）
- Budget Account 选择器（见 §17.4）：搜索 + 实时余额进度条
- 「+ Add Line」按钮
- 附件上传区（右侧 sticky sidebar）
- 超预算行：`warning-50` 背景 + amber 左边框
- 底部 Summary：小计、总额（只读）
- 操作按钮：[Save Draft] [Submit]

**F2-3** MIL 创建/编辑页 `/expenses/new?type=MIL`
- 行程日志表（Date, From, To, Purpose, Round Trip 复选框, km, rate, Amount）
- 费率从 policy API 自动填入（只读）
- 底部汇总：总 km、总金额、Budget Account（单一账户）

**F2-4** 费用详情页 `/expenses/{id}`
- 显示所有字段、审批时间线（复用 EPMS ApprovalTimeline 组件）、附件列表
- 审批动作按钮（根据角色和状态显示）
- 「Record Reimbursement」按钮（paid 状态流程）

**完成标准：**
- 员工可完整创建并提交 EXP claim
- Dept Manager 可在 Task Inbox 中看到审批任务并批准
- 批准后 budget actual_spent 正确更新（可在 EPMS Budget Dashboard 验证）
- MIL 金额自动计算正确

---

## Sprint 3 — PA-DIR + Invoice OCR（Week 3–4）

### 🎯 Sprint 目标
直接付款（无 PO）流程上线，含 AI Invoice 识别。

### Backend: expense-api

**B3-1** OCR 服务 `app/services/ocr_service.py`

```python
async def extract(file_bytes: bytes, filename: str, mode: Literal["receipt", "invoice"]) -> dict:
    # Upload to Claude API with vision
    # Mode "receipt": simple extraction (vendor, date, total, tax, description)
    # Mode "invoice": full extraction (vendor, invoice_no, date, due_date, line_items, subtotal, tax, total)
    # Returns: {fields..., confidence: float, low_confidence_fields: list[str]}
```

- `POST /api/v1/ocr/receipt` — 收据识别（EXP 附件）
- `POST /api/v1/ocr/invoice` — 发票识别（PA-DIR 上传）
- 错误处理：API 不可用 → HTTP 503；低置信度整体 < 0.5 → HTTP 422 with message

**B3-2** expense_invoices CRUD endpoints
- `POST /api/v1/invoices` — 上传并保存发票（含 OCR 触发）
  - 保存文件到 file-api
  - 自动触发 OCR（invoice mode）
  - 跨表去重检查：`(vendor_id, invoice_number)` in both `invoices` (EPMS) and `expense_invoices`
  - 去重冲突 → HTTP 409 with reference to existing document
- `GET  /api/v1/invoices/{id}` — 详情（含 OCR 结果）
- `PATCH /api/v1/invoices/{id}` — 更新字段（OCR 后人工修正）
- `GET  /api/v1/invoices/{id}/lines` — 行项目列表

**B3-3** PA-DIR Regular 创建（基于 Invoice）
- `POST /api/v1/pa` with `pa_type=PA-DIR`, `expense_invoice_id={id}`
- 从 invoice lines 自动生成 pa 行项目（用户补充 budget_account_id）
- 验证：invoice 状态必须为 `reviewed`（用户已确认 OCR 结果）
- PA-DIR 始终需要 Finance Manager 审批（approval-api workflow config）

**B3-4** PA-DIR 预付款不需要 Invoice（已有，type=advance 时跳过 invoice 验证）

**B3-5** EPMS Invoice 跨表去重读取
- 实现查询 EPMS `invoices` 表的逻辑（共享 DB，直接查询）

**完成标准：**
- 上传发票 → OCR 返回结构化数据（含置信度）
- 重复发票（同 vendor_id + invoice_number）返回 409
- PA-DIR Regular 从 Invoice 预填充，提交后触发 Finance Manager 审批

---

### Frontend: OA

**F3-1** PA-DIR 创建页 `/pa/new?type=direct` — 三步 Stepper

**Step 1 — Upload Invoice**：
- 文件上传区（拖放 + 点击），支持 PDF/JPG/PNG/HEIC
- 上传后自动触发 OCR，显示 loading state（左侧显示 invoice preview，右侧骨架屏 + 进度文字）

**Step 2 — Review & Correct**：
- 两栏布局（见 PRD §17.3 OCR Review Panel）
- 左：invoice 图片/PDF inline 预览
- 右：提取字段，低置信度字段 amber 高亮
- 置信度 chip（`92% confident` 绿色 / `61% confident` amber）
- 低置信度字段：用户必须点击确认才能激活「Continue」按钮
- 全部高置信度时显示 success banner + 字段 fade-in 动画（见 PRD §17.12）

**Step 3 — Payment Details（PA 表单）**：
- Vendor（从 invoice 预填，可修改）
- Invoice Reference（只读链接）
- 行项目（从 invoice lines 预填，budget_account 和 cost_centre 需要用户填）
- 单行可拆分到多个 budget account（+ 拆分按钮）
- Payment Method、Expected Payment Date、Notes

**F3-2** 移动端 Step 2：两个 Tab（Extracted Data / Invoice Preview）

**F3-3** 发票重复冲突提示（见 PRD §15.4）
- 409 响应时显示全宽红色 Banner：
  「Invoice INV-2026-00892 from this vendor was already recorded on Apr 15. See [PA-20260415-0003 →]」

**完成标准：**
- PA-DIR 三步流程完整可用（upload → OCR review → PA 表单）
- 低置信度字段正确高亮并要求确认
- 重复发票显示明确错误提示（含已有记录链接）
- 移动端 Step 2 两个 Tab 正常切换

---

## Sprint 4 — TRV 差旅 + Admin 配置 + 收尾（Week 4–5）

### 🎯 Sprint 目标
完成所有表单类型，Admin 配置面板，系统可以正式上线使用。

### Backend: expense-api

**B4-1** TRV 差旅报销 CRUD
- 同 EXP endpoints，type=TRV，额外字段：travel_from, travel_to, travel_purpose, travel_dest
- 行项目带 category 字段

**B4-2** TRV 政策校验
- 每日餐费超限检查（早餐$23/午餐$23/晚餐$46）
- 超限标记 `expense_line_items.is_over_policy = True`
- 超限行触发 Finance Manager 审批（在 approval-api workflow 中配置）

**B4-3** Admin 配置 endpoints
- `GET/PATCH /api/v1/policy` — expense_policy_config（HST 税率、MIL 每 km 费率、餐费上限等）
- `GET/PATCH /api/v1/policy/travel-categories` — TRV 类别 → Budget Account 映射
- `GET/POST/PATCH /api/v1/custom-forms` — 自定义表单定义 CRUD

**B4-4** 自定义表单（CFM）动态渲染支持
- `GET /api/v1/custom-forms/{code}/schema` — 返回 field_schema JSON
- `POST /api/v1/expenses`（type=CFM-{code}）— 用 field_schema 验证提交数据

**完成标准：**
- TRV 超限行正确触发 Finance Manager 审批
- Admin 可配置 MIL 费率、餐费上限、TRV 类别账户映射
- 至少一种自定义表单可创建并提交

---

### Frontend: OA

**F4-1** TRV 差旅报销创建页 `/expenses/new?type=TRV`
- 表头：同 EXP + travel_from/to/purpose/dest 字段
- 行项目按 Category 分组（可折叠，见 PRD §17.6）
- 超限 Category：amber 头部 + 政策限额说明
- 多货币：local amount + 汇率 + CAD 等值

**F4-2** Admin 费用配置面板（在 OA 的 Admin 区域，或 EPMS Admin Panel 新增 Tab）

OA Admin 入口：`/admin/expense-config`，包含以下子项：
- **General Policy**：HST 税率、默认币种
- **Travel Policy**：早/午/晚餐上限（数字输入）、酒店限额
- **Mileage Policy**：每 km 费率、最大 km、Budget Account 选择器
- **Category → Budget Account Mapping**：TRV 各类别对应 Budget Account 的可编辑映射表

**F4-3** 自定义表单管理页
- 表单列表（code、name、active 状态）
- 创建/编辑表单定义：字段拖拽排序、字段类型选择（text/number/date/budget_account/attachment 等）
- 预览模式

**F4-4** 自定义表单渲染页（动态 form renderer）
- 根据 field_schema JSON 动态渲染字段
- budget_account 类型字段自动使用 Budget Account Picker 组件

**F4-5** Portal 全局 Header 组件提取（为 Phase 3 准备）
- 将 portal header 从 portal 项目提取到 `uniops/packages/portal-header`
- EPMS 和 OA 都引入这个包（目前 Phase 2 是复制代码）

**完成标准：**
- TRV 完整表单可提交，超限行触发 Finance Manager 审批
- Admin 可修改 MIL 费率，修改后新建 MIL claim 使用新费率
- 自定义表单可创建、激活并由员工提交
- 全部 Sprint 1–4 流程在本地联调通过

---

## 依赖关系图

```
S1-B1 expense-api骨架
  └─► S1-B3 PA CRUD
        └─► S2-B2 EXP CRUD
              └─► S3-B2 Invoice CRUD
                    └─► S3-B3 PA-DIR Regular

S1-F3 OA骨架
  └─► S1-F5 PA列表
        └─► S1-F6 PA-PO表单
  └─► S2-F1 统一列表
        └─► S2-F2 EXP表单 ←── S2-B2
              └─► S4-F1 TRV表单 ←── S4-B1
  └─► S3-F1 PA-DIR三步 ←── S3-B1 OCR + S3-B2 Invoice CRUD

S1-E1~E5 EPMS改动 (独立，可与S1-B并行)

S1-F1 Portal ←── 任何时候都可独立开发
```

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Claude API OCR 效果不好（发票格式多样） | 中 | 中 | S3 前先手动测试5-10张真实发票，如效果差则增加人工修正字段范围 |
| TRV 表单复杂度超预期 | 低 | 低 | TRV 在 S4，如时间不够先交付 EXP+MIL+PA 三种类型 |
| 审批引擎 PA-DIR 工作流配置 | 低 | 高 | S1 开始时先确认 approval-api 支持「Finance Manager 始终必须」的条件规则 |
| EPMS DocumentChainTree 改造影响现有功能 | 低 | 中 | E1-3/E1-4 做充分的回归测试，保留 fallback 到 epms-api |
| expense-api 共享 PostgreSQL 写 budget_accounts 表 | 低 | 高 | 用相同的 SQLAlchemy model 镜像（只读取 budget_accounts 的结构，expense-api 也映射这张表），避免 raw SQL |

---

## 上线检查清单

**Sprint 1 Done**
- [ ] expense-api `:8006` 健康运行，`GET /health` 返回 200
- [ ] portal `:5174` 两个模块卡片正常显示
- [ ] OA `:5175` PA 列表/创建/详情可用
- [ ] EPMS PA 页面显示迁移 Banner
- [ ] EPMS PO 详情「Create PA」跳转 OA（含 po_id 预填充）
- [ ] EPMS Document Chain PA 节点跳转 OA 详情

**Sprint 2 Done**
- [ ] EXP claim 创建 → 提交 → Dept Manager 审批 → 批准 → budget actual_spent 更新
- [ ] MIL claim 金额自动计算，使用 policy 费率
- [ ] 超预算行正确触发 Finance Manager 审批

**Sprint 3 Done**
- [ ] Invoice 上传 → OCR 识别 → 低置信度字段高亮 → 用户确认 → PA-DIR 创建
- [ ] 重复发票（vendor_id + invoice_number）被硬阻断（409）
- [ ] PA-DIR Regular 始终要求 Finance Manager 审批

**Sprint 4 Done**
- [ ] TRV 差旅报销：超限项触发 Finance Manager
- [ ] Admin 可配置 MIL 费率、餐费上限、TRV 类别账户映射
- [ ] 至少一种自定义表单（CFM）可创建并提交
- [ ] 全流程（创建→提交→审批→打款/报销）端到端测试通过
