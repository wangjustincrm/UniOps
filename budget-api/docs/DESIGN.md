# Budget Module — System Design

**版本:** v1.0  
**日期:** 2026-05-15  
**PRD 引用:** [PRD.md](./PRD.md) v1.0

---

## 1. 服务架构

### 1.1 部署

```
budget-api (FastAPI :8007)
  ├─ PostgreSQL（与 epms 同 DB，独立 schema/table）
  └─ JWT 验证（共享 JWT_SECRET_KEY）

调用方：
  ├─ epms-api    → GET /balance           （PR 超预算检测）
  ├─ expense-api → POST /book-expense      （费用付款入账）
  ├─ finance-api → POST /commit, /release, /actualize（PA 流程）
  ├─ epms frontend → 所有目录/编制/dashboard 端点
  └─ oa frontend → GET /hierarchy         （费用单 budget account 下拉）
```

### 1.2 项目结构

```
budget-api/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py        # DATABASE_URL, JWT_SECRET_KEY
│   │   └── deps.py          # CurrentUserPayload, SessionDep
│   ├── db/
│   │   └── base.py          # Base, UUIDPrimaryKey, TimestampMixin
│   ├── models/
│   │   ├── catalog.py       # BudgetL1, BudgetAccount
│   │   ├── factor.py        # BudgetAccountFactor, FactorValue
│   │   ├── plan.py          # BudgetPlan, PlanLine, PlanBreakdown
│   │   ├── ledger.py        # BudgetLedger
│   │   └── settings.py      # BudgetSettings (single row)
│   ├── schemas/
│   │   ├── catalog.py
│   │   ├── factor.py
│   │   ├── plan.py
│   │   ├── balance.py
│   │   ├── actual.py
│   │   ├── ledger.py
│   │   └── settings.py
│   ├── crud/
│   │   ├── catalog.py
│   │   ├── factor.py
│   │   ├── plan.py          # 含季度/年度计算
│   │   ├── ledger.py
│   │   ├── balance.py       # 余额计算（plan + ledger）
│   │   └── settings.py
│   ├── api/v1/
│   │   ├── catalog.py       # L1 / accounts endpoints
│   │   ├── factor.py
│   │   ├── plan.py
│   │   ├── balance.py       # GET /balance
│   │   ├── actual.py        # GET /actuals, /actuals/summary
│   │   ├── hierarchy.py     # GET /hierarchy (兼容 OA)
│   │   ├── crossservice.py  # /commit /release /actualize /book-expense
│   │   ├── settings.py
│   │   └── health.py
│   └── services/
│       ├── plan_aggregator.py  # 季度/年度计算
│       └── http_clients.py     # epms-api / approval-api HTTP client（用于查 CC、委托审批）
├── alembic/
│   ├── env.py
│   └── versions/
├── docs/
│   ├── PRD.md
│   └── DESIGN.md
├── tests/
│   ├── conftest.py
│   ├── test_catalog.py
│   ├── test_factor.py
│   ├── test_plan.py
│   ├── test_balance.py
│   └── test_crossservice.py
├── alembic.ini
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── .env.example
└── README.md
```

---

## 2. 数据模型 DDL

### 2.1 共享目录

```sql
-- L1 分类（共享）
CREATE TABLE budget_l1 (
  id           UUID PRIMARY KEY,
  code         VARCHAR(20) UNIQUE NOT NULL,
  name         VARCHAR(255) NOT NULL,
  description  TEXT,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  sort_order   INT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by   UUID,
  updated_by   UUID
);
CREATE INDEX ix_budget_l1_active ON budget_l1(is_active);

-- L2 账户（共享模板）
CREATE TABLE budget_accounts (
  id           UUID PRIMARY KEY,
  code         VARCHAR(50) NOT NULL,
  name         VARCHAR(255) NOT NULL,
  description  TEXT,
  l1_id        UUID NOT NULL REFERENCES budget_l1(id) ON DELETE RESTRICT,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  decomposition_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order   INT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by   UUID,
  updated_by   UUID,
  CONSTRAINT uq_budget_account_code_l1 UNIQUE (code, l1_id)
);
CREATE INDEX ix_budget_accounts_l1_id ON budget_accounts(l1_id);
CREATE INDEX ix_budget_accounts_active ON budget_accounts(is_active);
```

### 2.2 因子定义

```sql
CREATE TABLE budget_account_factors (
  id           UUID PRIMARY KEY,
  account_id   UUID NOT NULL REFERENCES budget_accounts(id) ON DELETE RESTRICT,
  factor_code  VARCHAR(30) NOT NULL,
  factor_name  VARCHAR(100) NOT NULL,
  sort_order   INT NOT NULL DEFAULT 0,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_account_factor_code UNIQUE (account_id, factor_code)
);
CREATE INDEX ix_factor_account ON budget_account_factors(account_id);

CREATE TABLE budget_account_factor_values (
  id           UUID PRIMARY KEY,
  factor_id    UUID NOT NULL REFERENCES budget_account_factors(id) ON DELETE RESTRICT,
  value_code   VARCHAR(50) NOT NULL,
  value_name   VARCHAR(255) NOT NULL,
  sort_order   INT NOT NULL DEFAULT 0,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_factor_value_code UNIQUE (factor_id, value_code)
);
CREATE INDEX ix_factor_value_factor ON budget_account_factor_values(factor_id);
```

### 2.3 预算编制

```sql
CREATE TABLE budget_plans (
  id              UUID PRIMARY KEY,
  cost_center_id  UUID NOT NULL,              -- FK to epms cost_centers (logical, no DB FK)
  fiscal_year     INT NOT NULL,
  status          VARCHAR(20) NOT NULL DEFAULT 'draft',
  submitted_at    TIMESTAMPTZ,
  approved_at     TIMESTAMPTZ,
  approval_step_idx INT NOT NULL DEFAULT 0,
  notes           TEXT,
  -- ── Revision lineage (Plan A) ───────────────────────────────────────────
  version         INT NOT NULL DEFAULT 1,
  parent_plan_id  UUID REFERENCES budget_plans(id) ON DELETE SET NULL,
  is_current      BOOL NOT NULL DEFAULT TRUE,
  revision_notes  TEXT,
  -- ─────────────────────────────────────────────────────────────────────────
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by      UUID NOT NULL,
  CONSTRAINT uq_budget_plan_cc_year_version UNIQUE (cost_center_id, fiscal_year, version),
  CONSTRAINT ck_plan_status CHECK (status IN ('draft','submitted','in_review','approved','returned','rejected','cancelled')),
  CONSTRAINT ck_plan_version_positive CHECK (version >= 1)
);
CREATE INDEX ix_plan_cc_year ON budget_plans(cost_center_id, fiscal_year);
CREATE INDEX ix_plan_status ON budget_plans(status);
CREATE INDEX ix_budget_plans_parent_plan_id ON budget_plans(parent_plan_id);

-- Partial unique index: at most one current version per (cc, fy).
-- Older versions stay in the table with is_current=false (audit trail).
CREATE UNIQUE INDEX uq_budget_plan_current
  ON budget_plans (cost_center_id, fiscal_year)
  WHERE is_current = true;

CREATE TABLE budget_plan_lines (
  id           UUID PRIMARY KEY,
  plan_id      UUID NOT NULL REFERENCES budget_plans(id) ON DELETE CASCADE,
  account_id   UUID NOT NULL REFERENCES budget_accounts(id) ON DELETE RESTRICT,
  month        INT NOT NULL,
  amount       NUMERIC(15,2) NOT NULL DEFAULT 0,
  notes        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_plan_line UNIQUE (plan_id, account_id, month),
  CONSTRAINT ck_plan_line_month CHECK (month BETWEEN 1 AND 12)
);
CREATE INDEX ix_plan_lines_account_month ON budget_plan_lines(account_id, month);

CREATE TABLE budget_plan_breakdowns (
  id             UUID PRIMARY KEY,
  plan_line_id   UUID NOT NULL REFERENCES budget_plan_lines(id) ON DELETE CASCADE,
  factor_combo   JSONB NOT NULL,
  amount         NUMERIC(15,2) NOT NULL DEFAULT 0,
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_breakdown_plan_line ON budget_plan_breakdowns(plan_line_id);
CREATE INDEX ix_breakdown_combo_gin ON budget_plan_breakdowns USING GIN (factor_combo);
```

### 2.4 系统配置（单行表）

```sql
CREATE TABLE budget_settings (
  id                          UUID PRIMARY KEY,
  max_factors_per_account     INT NOT NULL DEFAULT 10,
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by                  UUID,
  CONSTRAINT ck_max_factors_range CHECK (max_factors_per_account BETWEEN 1 AND 50)
);
-- 服务启动时若无记录则插入默认行
INSERT INTO budget_settings (id, max_factors_per_account)
SELECT gen_random_uuid(), 10
WHERE NOT EXISTS (SELECT 1 FROM budget_settings);
```

### 2.5 Ledger（跨服务调用幂等去重 + 实际汇总来源）

```sql
CREATE TABLE budget_ledger (
  id               UUID PRIMARY KEY,
  source_service   VARCHAR(30) NOT NULL,
  source_doc_type  VARCHAR(30) NOT NULL,
  source_doc_id    UUID NOT NULL,
  operation        VARCHAR(20) NOT NULL,        -- 'commit' / 'release' / 'actualize' / 'book_expense'
  cost_center_id   UUID NOT NULL,
  account_id       UUID NOT NULL,
  fiscal_year      INT NOT NULL,
  month            INT NOT NULL,
  amount           NUMERIC(15,2) NOT NULL,
  notes            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_ledger_idempotency UNIQUE (source_service, source_doc_type, source_doc_id, operation),
  CONSTRAINT ck_ledger_op CHECK (operation IN ('commit','release','actualize','book_expense')),
  CONSTRAINT ck_ledger_month CHECK (month BETWEEN 1 AND 12)
);
CREATE INDEX ix_ledger_lookup ON budget_ledger(cost_center_id, account_id, fiscal_year, month);
CREATE INDEX ix_ledger_operation ON budget_ledger(operation);
```

---

## 3. API 规范

### 3.1 Catalog

| Method | Path | 说明 | 权限 |
|--------|------|------|------|
| GET    | `/api/v1/l1` | 列 L1（`?is_active=`, `?include_accounts=`） | 已登录 |
| POST   | `/api/v1/l1` | 创建 | system_admin / finance_manager |
| PATCH  | `/api/v1/l1/{id}` | 更新 | 同上 |
| DELETE | `/api/v1/l1/{id}` | 软删除（`is_active=false`） | 同上 |
| GET    | `/api/v1/accounts` | 列 Account（`?l1_id=`, `?is_active=`, `?decomposition_enabled=`） | 已登录 |
| POST   | `/api/v1/accounts` | 创建 | system_admin / finance_manager |
| PATCH  | `/api/v1/accounts/{id}` | 更新（含切换 decomposition_enabled） | 同上 |
| DELETE | `/api/v1/accounts/{id}` | 软删除 | 同上 |
| POST   | `/api/v1/catalog/import` | CSV 批量导入 | system_admin |
| GET    | `/api/v1/catalog/export` | CSV 导出 | 已登录 |

### 3.2 Factor

| Method | Path | 说明 |
|--------|------|------|
| GET    | `/api/v1/accounts/{id}/factors` | 列出该 Account 的因子（含 values） |
| POST   | `/api/v1/accounts/{id}/factors` | 添加因子（校验 max_factors_per_account） |
| PATCH  | `/api/v1/factors/{id}` | 更新因子（name / sort_order / is_active） |
| DELETE | `/api/v1/factors/{id}` | 删除因子（若有 plan_breakdowns 引用则禁止） |
| POST   | `/api/v1/factors/{id}/values` | 添加因子取值 |
| PATCH  | `/api/v1/factor-values/{id}` | 更新取值 |
| DELETE | `/api/v1/factor-values/{id}` | 软删除取值 |

### 3.3 Plan

| Method | Path | 说明 |
|--------|------|------|
| GET    | `/api/v1/plans` | 列 plan（`?cost_center_id=`, `?fiscal_year=`, `?status=`） |
| POST   | `/api/v1/plans` | 创建新 draft plan（自动 seed 所有有效 account × 12 个月的 line=0） |
| GET    | `/api/v1/plans/{id}` | 完整 plan 树（lines + breakdowns + Q/Y 聚合） |
| PATCH  | `/api/v1/plans/{id}` | 更新 plan meta（notes 等） |
| PATCH  | `/api/v1/plans/{id}/lines/{account_id}/{month}` | 直接编辑单元格（仅 decomposition 关闭时） |
| GET    | `/api/v1/plans/{id}/lines/{account_id}/breakdowns` | 列因子组合行 |
| POST   | `/api/v1/plans/{id}/lines/{account_id}/breakdowns` | 添加因子组合行 |
| PATCH  | `/api/v1/breakdowns/{id}` | 更新组合行金额 |
| DELETE | `/api/v1/breakdowns/{id}` | 删除组合行 |
| POST   | `/api/v1/plans/{id}/action` | submit / approve / return / reject（委托 approval-api，action_key=`budget_plan`） |
| POST   | `/api/v1/plans/{id}/copy-from-prev` | 从上一年同 CC 的 approved plan 复制金额 |

### 3.4 Balance & Actual

| Method | Path | 说明 |
|--------|------|------|
| GET    | `/api/v1/balance` | `?cost_center_id=&account_id=（或 account_code=）&fiscal_year=` → `{annual_budget, committed, actual_spent, available}` |
| GET    | `/api/v1/actuals` | `?cost_center_id=&fiscal_year=&account_id=&month=` → ledger 聚合 |
| GET    | `/api/v1/actuals/summary` | 按 L1/Account 聚合的 plan vs actual |

### 3.5 Hierarchy（兼容 OA 现有调用）

| Method | Path | 说明 |
|--------|------|------|
| GET    | `/api/v1/hierarchy` | 返回 CC → L1 → L2 树（响应格式同 expense-api 原 `/budget/hierarchy`） |

### 3.6 Cross-service write（幂等）

| Method | Path | 调用方 | Body |
|--------|------|--------|------|
| POST | `/api/v1/commit` | finance-api | `{source_doc_type, source_doc_id, cost_center_id, account_id, fiscal_year, month, amount}` |
| POST | `/api/v1/release` | finance-api | 同上 |
| POST | `/api/v1/actualize` | finance-api | 同上 |
| POST | `/api/v1/book-expense` | expense-api | `{source_doc_type, source_doc_id, lines: [{cost_center_id, account_id, fiscal_year, month, amount}]}` |

幂等策略：每次写入按 `(source_service, source_doc_type, source_doc_id, operation)` 在 `budget_ledger` 表去重。重复调用返回 200 + `{idempotent: true}`，不重复插入。

### 3.7 Settings

| Method | Path | 说明 |
|--------|------|------|
| GET    | `/api/v1/settings` | 获取当前配置 |
| PATCH  | `/api/v1/settings` | 修改（仅 system_admin） |

### 3.8 Health

| Method | Path | 说明 |
|--------|------|------|
| GET    | `/health` | 健康检查（含 DB 连接） |

---

## 4. 业务逻辑

### 4.1 余额计算（Ledger 聚合）

```python
annual_budget(cc, account, year) = SUM(plan_line.amount 
                                       WHERE plan.cost_center_id=cc 
                                       AND plan.fiscal_year=year 
                                       AND plan.status='approved'
                                       AND plan_line.account_id=account)

committed(cc, account, year) = 
    SUM(ledger.amount WHERE operation='commit')
  − SUM(ledger.amount WHERE operation IN ('release','actualize'))

actual_spent(cc, account, year) = 
    SUM(ledger.amount WHERE operation IN ('actualize','book_expense'))

available = annual_budget − committed − actual_spent
```

### 4.2 季度/年度聚合（运行时计算，不持久化）

```
Q1 = sum(line.amount for month in [1,2,3])
Q2 = sum(month in [4,5,6])
Q3 = sum(month in [7,8,9])
Q4 = sum(month in [10,11,12])
Year = Q1 + Q2 + Q3 + Q4
```

实现：`services/plan_aggregator.py` 在序列化 plan 响应时计算并附加。

### 4.3 因子拆分一致性

当 `account.decomposition_enabled = true` 时：

```
plan_line.amount = SUM(plan_line_breakdowns WHERE plan_line_id = plan_line.id)
```

保证方式：所有 `POST/PATCH/DELETE /breakdowns` 在同一事务内重算 plan_line.amount。直接 `PATCH /lines/{id}` 在此情形下返回 409。

### 4.4 幂等性

```python
async def write_ledger(db, source_service, source_doc_type, source_doc_id, operation, ...):
    existing = await db.execute(
        select(BudgetLedger).where(
            BudgetLedger.source_service == source_service,
            BudgetLedger.source_doc_type == source_doc_type,
            BudgetLedger.source_doc_id == source_doc_id,
            BudgetLedger.operation == operation,
        )
    )
    if existing.scalar_one_or_none():
        return {"idempotent": True}
    db.add(BudgetLedger(...))
    await db.flush()
    return {"idempotent": False}
```

数据库 unique 约束 `uq_ledger_idempotency` 作为最后防线。

### 4.5 状态机（Plan）

```
draft ──submit──► submitted ──approve(step 1)──► in_review ──approve(final)──► approved
                     │                              │
                     ├──return──► returned ─────────┤
                     └──reject──► rejected          └──reject──► rejected
```

`approved` 仍是状态机的终态——已批准的 plan 不能直接编辑。
如需调整预算，走 **§ 4.6 修订 (revision)** 流程创建新版本。

委托 approval-api（`action_key="budget_plan"`），workflow 默认 `dept_manager → finance_manager`。

### 4.6 预算修订（Plan A — 版本化）

一组 (CC, fiscal_year) 在 `budget_plans` 表中可以有多行，通过 `version` 区分；
`is_current=true` 标志当前生效版本（同 (CC, fy) 最多 1 行，DB 偏唯一索引强制）。

```
v1 (status=approved, is_current=true)   ◄── balance / actuals 读这里
                       │
                       │ POST /plans/{v1.id}/revise
                       ▼
v2 (status=draft, is_current=false, parent_plan_id=v1.id)   ◄── 编辑这里
                       │
                       │ submit → in_review → approve
                       ▼
v2 (status=approved, is_current=true)    ◄── balance / actuals 从此读这里
v1 (status=approved, is_current=false)   ◄── 历史，保留审计
```

**关键点：**

1. **修订入口** — `POST /plans/{id}/revise`：
   - 仅 `status='approved' AND is_current=true` 的 plan 允许；其他 → 409
   - 同 (CC, fy) 已存在非终态版本 → 409（避免同时多个 draft 修订）
   - 新版本 `version = max(version)+1`，`status='draft'`，`is_current=false`，`parent_plan_id=parent.id`
   - 复制所有 plan_lines + breakdowns 作为编辑起点

2. **修订期间** — `is_current` 不动：
   - balance / actuals 查询仍读父版本 → 业务连续性不受影响
   - 用户在 v2 中编辑、提交、审批

3. **修订批准** — `approval-api` 的 `_post_approve_budget_plan` hook：
   ```python
   if plan.parent_plan_id is not None:
       parent.is_current = False  # 旧版让位
   plan.is_current = True          # 新版接管
   ```
   - 同一事务内原子完成，避免出现 0 个 / 2 个 current 版本的窗口
   - 父版本 `status` 保留 `'approved'`，仅 `is_current=false`（审计可见）

4. **修订拒绝/取消** — 父版本不变：
   - 新 v2 终态 `cancelled` / `rejected`
   - 父 v1 继续 `is_current=true`

5. **查询语义统一**：
   - 所有"当前生效"语义 = `status='approved' AND is_current=true`
   - 历史视图 = `GET /plans/by-cc-year/{cc}/{fy}/versions`

**API 端点**

| Method | Path | 用途 |
|--------|------|------|
| `POST` | `/plans/{id}/revise` | 创建修订版（v+1 draft） |
| `GET` | `/plans?include_history=true` | 包含历史版本的列表 |
| `GET` | `/plans/by-cc-year/{cc_id}/{fy}/versions` | (CC, year) 的完整版本链，version 倒序 |

**幂等性 & 并发：**
- DB 偏唯一索引 `uq_budget_plan_current` 保证任意时刻 (CC, fy) 至多 1 个 `is_current=true`
- 复合唯一约束 `uq_budget_plan_cc_year_version` 保证版本号不重复
- `revise_plan` CRUD 在事务内完成"查父→建子→copy lines→copy breakdowns"

---

## 5. 与其他服务集成

### 5.1 epms-api

**改造点：**
- 删除：`app/models/budget.py`, `app/schemas/budget.py`, `app/crud/budget.py`, `app/api/v1/budget.py`
- 修改：
  - `app/core/config.py`：新增 `BUDGET_API_URL: str = "http://localhost:8007"`
  - `app/crud/pr.py`：`_compute_over_budget` → `GET {BUDGET_API_URL}/api/v1/balance`，对 404 / 5xx 降级 fail-open
  - `app/crud/dashboard.py`：`_budget_overview` / `_over_budget_count` → `GET /actuals/summary`
  - `app/crud/cost_center.py`：删除 BudgetL1 引用（CC 删除前不再校验 budget 占用，由 budget-api 自治）
  - `app/main.py`：移除 budget router

### 5.2 expense-api

**改造点：**
- 删除：`app/models/budget.py`, `app/api/v1/budget.py`
- 修改：
  - `app/core/config.py`：新增 `BUDGET_API_URL`
  - `app/models/epms_mirrors.py`：移除 `EpmsBudgetL1`
  - `app/crud/expense.py`：`_book_budget` → `POST {BUDGET_API_URL}/api/v1/book-expense`
  - `app/main.py`：移除 budget router

### 5.3 finance-api

**改造点：**
- 删除：`app/models/budget.py`, `app/crud/budget.py`
- 修改：`app/api/v1/budget.py` 改为薄代理层，对外 URL 不变，内部转发到 budget-api

### 5.4 approval-api

**新增：** `_DOC_META` 加 `budget_plan` 条目（model=BudgetPlan 镜像 / number=plan_id 或新增 plan_number / amount=total）。  
**workflow_defs:** 默认 `[{role:"dept_manager"}, {role:"finance_manager"}]`，可在 Portal Admin 配置。

---

## 6. 数据迁移

### 6.1 整体策略

**一次性 Alembic migration**，分以下步骤：

#### Step 1: 创建新表
全部 budget-api 新表（budget_l1, budget_accounts, factors, factor_values, plans, plan_lines, breakdowns, settings, ledger）使用临时表名 `_new_xxx` 创建，避免与 epms-api 旧表冲突。

#### Step 2: 数据合并（旧 per-CC L1 → 共享目录）

```sql
-- 2a: 抽取唯一的 (code, name) 作为共享 L1
INSERT INTO _new_budget_l1 (id, code, name, is_active, created_at, updated_at)
SELECT gen_random_uuid(), code, MIN(name), bool_or(is_active), MIN(created_at), now()
FROM budget_l1
GROUP BY code;

-- 2b: 抽取唯一的 (l1_code, account_code, name) 作为共享 Account
INSERT INTO _new_budget_accounts (id, code, name, l1_id, is_active, decomposition_enabled, ...)
SELECT gen_random_uuid(), a.code, MIN(a.name), n_l1.id,
       bool_or(a.is_active), false, MIN(a.created_at), now()
FROM budget_accounts a
JOIN budget_l1 l1 ON a.l1_id = l1.id
JOIN _new_budget_l1 n_l1 ON n_l1.code = l1.code
GROUP BY a.code, n_l1.id;
```

#### Step 3: 旧 annual_budget → plan_lines

```sql
-- 3a: 为 (CC, fiscal_year=current_year) 创建 approved plan
INSERT INTO _new_budget_plans (id, cost_center_id, fiscal_year, status, created_by, ...)
SELECT gen_random_uuid(), l1.cost_center_id, EXTRACT(YEAR FROM now())::INT,
       'approved', '<system_user_uuid>', ...
FROM (SELECT DISTINCT cost_center_id FROM budget_l1) l1;

-- 3b: 旧 annual_budget 平均分 12 月，生成 plan_lines
INSERT INTO _new_budget_plan_lines (id, plan_id, account_id, month, amount)
SELECT gen_random_uuid(), p.id, n_a.id, m,
       ROUND(a.annual_budget / 12, 2)
FROM budget_accounts a
JOIN budget_l1 l1 ON a.l1_id = l1.id
JOIN _new_budget_l1 n_l1 ON n_l1.code = l1.code
JOIN _new_budget_accounts n_a ON n_a.code = a.code AND n_a.l1_id = n_l1.id
JOIN _new_budget_plans p ON p.cost_center_id = l1.cost_center_id
CROSS JOIN generate_series(1, 12) AS m
WHERE a.annual_budget > 0;
```

#### Step 4: 旧 committed / actual_spent → ledger backfill

```sql
-- 4a: 旧 committed 一次性 backfill（按 12 月平均，记到 1 月）
INSERT INTO _new_budget_ledger (id, source_service, source_doc_type, source_doc_id,
                                operation, cost_center_id, account_id, fiscal_year, month, amount)
SELECT gen_random_uuid(), 'migration', 'legacy', a.id,
       'commit', l1.cost_center_id, n_a.id, EXTRACT(YEAR FROM now())::INT, 1,
       a.committed
FROM budget_accounts a
JOIN budget_l1 l1 ON a.l1_id = l1.id
JOIN _new_budget_l1 n_l1 ON n_l1.code = l1.code
JOIN _new_budget_accounts n_a ON n_a.code = a.code AND n_a.l1_id = n_l1.id
WHERE a.committed > 0;

-- 4b: 旧 actual_spent backfill
INSERT INTO _new_budget_ledger (...) ... 'actualize' ... a.actual_spent
WHERE a.actual_spent > 0;
```

#### Step 5: 切换 + 清理

```sql
DROP TABLE budget_accounts;
DROP TABLE budget_l1;
ALTER TABLE _new_budget_l1 RENAME TO budget_l1;
ALTER TABLE _new_budget_accounts RENAME TO budget_accounts;
-- ... 其他表同理
```

### 6.2 Migration 文件位置

迁移由 budget-api 拥有：`budget-api/alembic/versions/<rev>_initial_migration.py`。  
epms-api 的旧 `d35208b1b063_create_budget_l1_and_budget_accounts_.py` migration 保留作历史记录（不动）。budget-api migration 检测旧表存在 → 执行合并；不存在则跳过（新部署）。

---

## 7. 测试策略

| 测试层 | 覆盖 |
|--------|------|
| **单元** | crud/balance.py（余额计算）、services/plan_aggregator.py（Q/Y 聚合）、crud/ledger.py（幂等） |
| **API 集成** | 每个 endpoint 的 happy path + 错误码 |
| **跨服务模拟** | 用 httpx mock 模拟 finance-api / expense-api 调用 budget-api |
| **数据迁移** | 用旧数据快照运行 alembic upgrade，校验数据一致性 |
| **端对端** | docker-compose 起全栈，跑 PR 超预算 / 报销付款 / PA 流程 → 验证 ledger 入账 |

---

## 8. 部署

### 8.1 docker-compose.dev.yml 新增条目

```yaml
budget-api:
  build: ./budget-api
  ports:
    - "8007:8007"
  environment:
    DATABASE_URL: postgresql+asyncpg://epms:epms_dev@postgres:5432/epms
    JWT_SECRET_KEY: ${JWT_SECRET_KEY}
    APPROVAL_API_URL: http://approval-api:8003
    EPMS_API_URL: http://epms-api:8000
  depends_on:
    postgres:
      condition: service_healthy
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8007/health"]
    interval: 10s
    timeout: 5s
    retries: 5
```

### 8.2 check-health.sh 新增
```bash
check budget-api 8007
```

### 8.3 各服务 .env.example 新增

`BUDGET_API_URL=http://localhost:8007`（dev）  
`BUDGET_API_URL=http://app-server.internal:8007`（prod）

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 跨服务 HTTP 调用失败导致 PR 提交受阻 | 中 | 高 | epms-api `_compute_over_budget` 降级 fail-open + 日志告警 |
| ledger 幂等约束冲突导致重复写入丢失 | 低 | 中 | 数据库 unique 约束 + 应用层 return idempotent flag |
| 旧 per-CC L1 数据合并时 code 冲突（同 code 名字不同）| 中 | 中 | migration 选 MIN(name) + 输出 warning 日志，供人工修正 |
| Plan 编制网格性能（60 account × 12 月 × N 因子组合）| 中 | 中 | 服务端预聚合 + 前端虚拟滚动 |
| 数据迁移失败导致两边表都不可用 | 低 | 高 | migration 在事务内执行，失败自动回滚；备份 DB 后再 upgrade |

---

## 10. 开放项

| OI | 待跟进 |
|----|--------|
| OI-1 | 数据迁移前需 dump 一份当前 budget_l1 / budget_accounts 数据快照备份 |
| OI-2 | approval-api 接入 `budget_plan` action key 需更新 Portal Admin Workflow Editor |
| OI-3 | "预算调整" Phase 2 设计：approved plan 怎么走变更流程，是否影响在途 PA |
| OI-4 | 多年度跨期：跨财年的 PA / 支付如何归属（建议按 evt_date 月份归属） |
