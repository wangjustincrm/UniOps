# Finance 预实对比(Budget vs Actual)真表 —— Phase 1 设计

> 状态:设计稿(2026-07-18 brainstorm,基于 `C:\Project\Budget` 的 Excel 工具 + 用户逐项确认)。
> 归属:Finance —— 替代财务每月手工的 Excel 预实(`C:\Project\Budget\app_v3.py`)。
> 分支:`feature/finance-predreal`,worktree `c:/Project/uniops/.worktrees/finance-predreal`。
> 关联:`2026-07-17-finance-ab-nonleaf-rollup`(复用 `expand_by_dims`/`account_vouchers`/subtree)、`nc_sync.py`(改 account-aware)、budget-api plan(预算源)。
> 范围:**仅 Phase 1**(会计口径预实,已结月取 NC posted)。当前月采购预判 + 前瞻可用余额 = **Phase 2**,本 spec 不含。

## 0. 目标与一览

**目标**:部门经理(只读,权限 = Budget Dashboard)看到一张按 **成本中心 × 收支项目** 的表:`预算 / 实际 / 差异`;实际值可**下钻到构成凭证**,并可**按辅助核算维度展开**——直接替代财务每月在 Excel 里手工做的预实。

**核心定位**:NC = 账本(实际的法定来源),UniOps = 规则与分析层。实际取自**同步回来的 NC posted 凭证**;映射用**导入的权威映射表**(account-aware),替换现有 sync 里 account-blind 的硬编码字典。

| # | 组件 | 性质 |
|---|---|---|
| 1 | 权威映射表(导入 Mapping.xlsx) | 新表 + 导入脚本 |
| 2 | sync 改 account-aware 解析 | 同步逻辑改 + JV 行加 nc_cc_code + 存量全量重灌 |
| 3 | 预实网格后端 | 新 GL 读接口(5 类,成本中心×收支项目 + P/D 兜底) |
| 4 | 异常面板(= 映射空缺校验) | 复用下钻,列未映射行 |
| 5 | 前端只读预实页 | 重构 `BudgetActualPage` |

---

## 1. 权威映射表(#1)

### 1.1 事实(Excel 工具规格,`C:\Project\Budget\Budget vs Actual Mapping.xlsx` 2026-07-18 修订)

映射钥匙 = **`(费用类别/科目 + 部门NC + 成本中心NC)` → UniOps 成本中心**,**account-dependent**(同部门在不同科目落不同成本中心)。要点:
- 成本中心 code 存在时按 code(P01/P02/P03/S02/S03/Q01/H01),否则按部门;`ALL` = 通配;
- `6602+0106`、`6602+0109` **故意不在表里** → 落进来即异常(工程/研发费用不该进管理);
- `GA-0100`(GM+IT)是**多部门聚合**(0100 总经办/IT + 0108 Project);
- `6603 财务费用 → FN-0103`(工具没做,本 Phase 补上)。

当前 21 行(权威值以 Excel 为准,导入时读取):

| account | dept | nc_cc | → uniops_cc |
|---|---|---|---|
| 5101 | 0106 | ALL | MOH-0106-E01 |
| 5101 | 0107 | S02 | MOH-0107-S02 |
| 5101 | 0104 | P01/P02/P03 | MOH-0104-P01/P02/P03 |
| 5101 | 0105 | Q01 | MOH-0105-LAB |
| 5101 | 0101 | H01 | MOH-0101 |
| 5301 | ALL | ALL | RD-0109 |
| 6602 | 0103 | ALL | GA-0103 |
| 6602 | 0100 | ALL | GA-0100 |
| 6602 | 0108 | ALL | GA-0100 |
| 6602 | 0107 | ALL | GA-0107 |
| 6602 | 0105 | ALL | GA-0105 |
| 6602 | 0101 | ALL | GA-0101 |
| 6601 | 0107 | S03 | SELL-0107-S03 |
| 6601 | 0111/0110/0112/0113 | ALL | SELL-0111/0110/0112/0113 |
| 6603 | ALL | ALL | FN-0103 |

### 1.2 数据模型

新表 `finance` 库 `budget_actual_cc_map`(finance-api 拥有;NC 事实映射):

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | uuid pk | |
| `account_code` | varchar(10) NOT NULL | 5101/5301/6601/6602/6603 |
| `dept_code` | varchar(20) NOT NULL | NC 部门 code;`ALL` = 任意部门 |
| `nc_cc_code` | varchar(20) NOT NULL | NC 成本中心 code;`ALL` = 不看成本中心 |
| `uniops_cc_code` | varchar(50) NOT NULL | 目标 UniOps 成本中心 code(→ `cost_centers.code`) |

- 迁移编号查 `alembic heads` 取真实链尾(勿猜,per feedback)。
- 唯一约束 `uq_ba_cc_map (account_code, dept_code, nc_cc_code)`。
- 导入:`scripts/import_cc_map.py` 读 xlsx 灌库(truncate + reload,幂等)。Admin 编辑 Phase 1 走重导(YAGNI)。

### 1.3 解析函数

`app/services/cc_map.py::resolve_uniops_cc(rows, account_code, dept_code, nc_cc_code) -> str | None`:
- 三级查:`(account, dept, nc_cc)` 精确 → `(account, dept, ALL)` → `(account, ALL, ALL)`;
- 全落空 → `None`(= 未映射,进异常)。
- `rows` 是预加载的整表(表小),调用方缓存。

---

## 2. sync 改 account-aware(#2)

### 2.1 现状问题

`nc_sync.py::_resolve_dims` 现在 `epms = CC_BY_CODE.get(c) if c else CC_BY_DEPT.get(d)` —— **不看科目**,和 §1 account-aware 映射冲突(`6602+0106` 被静默记进 MOH 而非报异常;`0108→RD-0109` 已被用户表推翻)。

### 2.2 改法(HYBRID —— 2026-07-18 实测修正,见 §2.4)

**不是**"删掉 CC_BY_* 全量 account-aware"(那样会回归 20k 行,见 §2.4),而是**混合**:

- **仅 5 个预实科目(5101/5301/6601/6602/6603 及其 COA 子树)走 account-aware map**;其它所有科目**保留** `CC_BY_CODE/CC_BY_DEPT` 兜底(约 2 万行余额表/其它科目依赖它,且这些科目没有 account-aware 答案)。
- `make_category_of(parent_map)`:用 `chart_of_accounts.parent_code` 把某行科目**自底向上**找到它属于哪个预实科目(header),**不用 code 前缀猜**(660303 可能挂 6603 也可能挂 6601)。
- `_resolve_dims(assid, aux, cc_map_rows, category, ...)`:`category` 非空 → `resolve_uniops_cc(cc_map_rows, category, dept, nc_cc)`(未命中 None → 异常);`category` 为空 → `CC_BY_CODE.get(c) if c else CC_BY_DEPT.get(d)`(原样)。
- `transform(..., skip_pks, cc_map_rows=None, category_of=None)`:尾部关键字默认参,**不传时全走兜底**——现有 transform 测试零改动。
- **额外存原始 NC 成本中心 code**:`journal_voucher_lines` 加列 `nc_cc_code varchar(20)` 可空——日后调映射可重映射存量。line tuple 增一列,`_run_worker` INSERT 带 `nc_cc_code`,`tot` 解包补一个 `_`。
- `_run_worker` 启动加载 `chart_of_accounts`(建 category_of)+ `budget_actual_cc_map`(cc_map_rows,缺表则 []=预实科目暂无成本中心,须先 import)传入 transform。
- ⚠️ **部署顺序**:必须**先 import_cc_map 再全量重灌**,否则预实科目全部落成"无成本中心"。

### 2.4 实测发现(2026-07-18,dev DB 68w 行)

- 非预实科目上有 **20,643 行**已带 `cost_center_id`(经 CC_BY_* 兜底)——纯 account-aware 会把它们全清 None,回归 Account Balance 的成本中心展开。→ 必须保留兜底(HYBRID)。
- 预实行**混用** header 码(5101=25978、6602=14783、6601、5301)和 leaf 码(510101=8239、660101=7058、660303…)→ 必须用 COA 树把 leaf 归到 category,前缀不可靠。

### 2.3 存量

- 迁移加 `nc_cc_code` 列(可空);
- **发布后全量重灌**(同 nc-coa-sync §14.5 已验证路径)补齐 account-aware `cost_center_id` + `nc_cc_code`。

---

## 3. 预实网格后端(#3)

### 3.1 科目 → 费用类别(5 类,含新增 6603)

`account_balance.py` 现有 `BUDGET_ACTUAL_ACCOUNTS` 加 `"6603": "FN"`。

### 3.2 实际取数(已结月)

新 CRUD `budget_actual_grid(db, period, budget_lookup) -> dict`,对 5 类:
- 取该科目 subtree 的 posted JV 行,`fiscal_period == period`;
- **明细行**:按 `(cost_center_id, income_expense_item_id)` 分组,取**本期借方 `Σ local_debit`**(费用科目结转后净≈0,借方才是发生额,同现有 `能力④`);
- **排除** income_expense_item 的 `budget_account.code` 以 **CRM004(折旧)/CRM007(Payroll)** 开头的行;
- **P/D 兜底行**(每类):对被排除的 CRM004、CRM007 各出一条**大类级、不分成本中心**的行,使 `明细 + Payroll + 折旧 = NC 科目本期借方总额`(tie-out)。P/D 兜底行**只有实际,无预算**(用户 2026-07-18:财务只按大类统计)。

### 3.3 预算取数

预算 = **budget-api 当前批准计划**(`BudgetPlanLine.amount`,`plan.is_current AND status='approved'`),钥匙 `(cost_center_id, account_id=income_expense_item, month)`。
- finance-api 经 budget-api 新增只读接口 `GET /internal/plan-lines?fiscal_year=&month=` 批量取(返回 `[{cost_center_id, account_id, amount}]`),避免 N 次调用;
- **⚠️ 前提(见待确认①)**:公司 2026 预算已录入 budget-api 且 = 财务 Excel 里的预算。

### 3.4 返回结构

```jsonc
{ "period": "2026-06",
  "categories": [
    { "account_code": "5101", "category": "MOH",
      "detail": [ { "cost_center_code": "MOH-0106-E01", "cost_center_name": "...",
                    "income_expense_code": "CRM003", "income_expense_name": "IT General Fee",
                    "budget": "1000.00", "actual": "820.00", "variance": "180.00" } ],
      "payroll_actual": "12345.00", "depreciation_actual": "6789.00",
      "detail_actual_total": "...", "category_actual_total": "...",
      "nc_account_debit": "...", "tie_ok": true } ],
  "unmapped": [ /* §4 */ ] }
```

### 3.5 下钻(复用现成)

- 明细实际格 → `account_vouchers(account_code, period, dims_values={cost_center, income_expense_item})`(已建);
- 「按辅助核算展开」→ `expand_by_dims`(已建);
- P/D 兜底行 → `account_vouchers` 按 income_expense_item 过滤(不带 cost_center)。

---

## 4. 异常面板(= 映射空缺校验,#4)

**"落不进格子"的行即异常**(= 财务盯 error log 的等价物,且 = 未映射兜底桶,一个功能):
- **未映射成本中心**:posted JV 行 `cost_center_id IS NULL AND nc_cc_code IS NOT NULL`(sync account-aware 未命中);
- 返回在 `unmapped[]`,每条带 `(account_code, nc_cc_code, income_expense_code, 金额, jv 张数)` + 下钻凭证入口(按 `account_code` + `nc_cc_code` filter,`cost_center IS NULL`)。
- **处置**(前端两入口):① "去 NC 改"(展示凭证信息);② "加映射"(指引去映射表)。Phase 1 只做指引,不做系统内改 NC。

---

## 5. 前端只读预实页(#5)

重构 `finance/src/pages/finance/BudgetActualPage.tsx`(现只有实际、无预算):
- 顶部期间选择;
- 5 类分块(MOH/RD/SELL/GA/FN),每块列:`成本中心 | 收支项目 | 预算 | 实际 | 差异 | 下钻`;
- 每类末尾两条灰色兜底行:`Payroll`、`折旧`(只有实际列);
- 类脚 `合计` + tie-out 徽章(对不上高亮);
- 底部「异常」区块 = `unmapped[]`;
- 下钻复用 `AccountVouchersModal` / `JvDetailModal` / expand;
- **只读**;权限 = `view_budget_dashboard`;放 finance 模块(待确认③)。

---

## 6. 存量与部署

- **迁移**:①新表 `budget_actual_cc_map` ②`journal_voucher_lines.nc_cc_code` ——查 alembic heads。
- **映射导入**:发布后跑 `import_cc_map.py`。
- **全量重灌**:补 account-aware `cost_center_id` + `nc_cc_code`(dev 先验)。
- **budget-api**:新增只读 plan-lines 批量接口,随 budget-api 镜像发。
- 全 15 镜像同 sha 构建部署(per 发布纪律 R4)。

## 7. 测试

- **cc_map**:`resolve_uniops_cc` 三级回退(精确/dept-ALL/全 ALL/未命中→None);`6602+0106+ALL`→None、`5101+0104+P02`→MOH-0104-P02、`6603+X+Y`→FN-0103。
- **sync**:`_resolve_dims` 传 account_code,account-aware 命中 + 未命中计 unmapped;`nc_cc_code` 落列。live-NC 冒烟(照纪律)。
- **budget_actual_grid**:造 posted JV(CRM003 明细 + CRM004/CRM007)→ 明细排除 P/D、P/D 兜底 = ΣCRM004/007、`明细+P+D == 科目借方`、`tie_ok`;join 预算算 variance;未映射行进 unmapped。
- **前端**:tsc baseline 不增;tie-out 对不上高亮;下钻打开 modal;只读无编辑入口。

## 8. 明确不做(YAGNI / Phase 2)

- 不做当前月预判(采购事件驱动)、前瞻可用余额(4 层)——Phase 2。
- 不做政策级错分类规则引擎(方案2)——映射空缺已覆盖工程部→6602 类。
- 不做系统内改 NC 凭证——异常只给指引。
- 不做映射表复杂 Admin(Phase 1 导入 + 只读列表)。
- 不做回推 PO/PA(NC 联查坏,已放弃)。

---

## 待确认(3 项)

1. **预算源**:预实「预算」列取 budget-api 当前批准计划。前提是公司 2026 预算已录入 budget-api 且 = 财务 Excel。**若只在 Excel**,Phase 1 含一步"把 Excel 预算导入 budget-api"(现成 `import_plan_csv`);计划里已放**验证任务 + 缺失则导入**的兜底。
2. **P/D 兜底行预算**:只显实际、预算空(采纳,和"只按大类统计"一致)。
3. **页面放置**:finance 模块 + `view_budget_dashboard`(采纳)。
