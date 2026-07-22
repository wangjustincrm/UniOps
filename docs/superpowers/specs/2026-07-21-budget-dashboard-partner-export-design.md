# Budget Dashboard 客商展开 XLSX 导出 — 设计文档

**日期**: 2026-07-21
**范围**: EPMS 前端 BudgetDashboard 页 + finance-api 后端(新导出端点)
**类型**: 新功能(前端按钮 + 后端服务端 xlsx 生成)

---

## 1. 背景与目标

Budget Dashboard(菜单名,实为 EPMS `BudgetDashboard.tsx`,Finance 通过 `EpmsEmbed` 嵌入)里的 **"Monthly Plan vs Actual"** 表,每个预算科目行可手动展开成"按客商(供应商/客户)"的 NC 实际明细。用户希望在该表加一个 **"Export XLSX"** 按钮:一键把**当前所选 fiscal year + cost center** 下的这张表,导成一个 Excel,且**所有预算科目都预先按客商展开**——相当于把当前页面所有科目都展开后的一张大表。

### 已确认决策(brainstorming)

| 决策点 | 结论 |
|--------|------|
| 目标页 | EPMS `BudgetDashboard.tsx` 的 "Monthly Plan vs Actual" 表(Finance 嵌入的 Budget Dashboard) |
| 导出范围 | 跟随当前选择的 **fiscal year + cost center**;cost center = "All Cost Centers" 时导出**跨 CC 汇总**(与屏幕一致) |
| 指标 | **只要 Plan 和 NC 实际**;**不要 Actual(docs)** |
| 布局 | 忠实版:每科目小计块 = **Plan / NC 两行**;其下每个客商一行(Metric=NC);月度 12 列 + 年合计 |
| 生成方式 | **服务端 finance-api 用 openpyxl 生成 .xlsx** 直接返回(不引入前端 xlsx 库) |
| 权限 | 与现有 `/finance/v1/gl/*` 端点一致:**只需登录**,不做部门/CC scoping(与 `nc-partner-monthly` 等现状一致) |

---

## 2. 数据来源(已核实)

| 数据 | 来源 | 说明 |
|------|------|------|
| 科目清单 + L1 分组 + 科目名 + **Plan** 每科目×月 | budget-api `GET /api/v1/actuals/monthly-summary?fiscal_year=&cost_center_id=` | 返回 `accounts[]`:`account_id, account_code, account_name, l1_code, plan_by_month{1..12}, plan_year`(还有 `actual_by_month/actual_year`,本功能**忽略**) |
| **NC 实际** 每科目×月 | finance-api `crud.nc_actuals_monthly(db, fiscal_year, cost_center_id)` | 现成函数,返回 `{account_id_str: {month:amount_str}}`;已排除 CRM007/CRM004 |
| **NC 实际 每科目×客商×月** | finance-api `crud.nc_partner_monthly` 的**新批量版** | 新增 `nc_partner_monthly_all(db, fiscal_year, cost_center_id)`,一条 SQL 按 `(income_expense_item_id, partner_id, partner_name, month)` 汇总,输出 `{account_id_str: [partner...]}` |

关键事实:
- budget-api `account_id` == JV 行的 `income_expense_item_id`(共享 `budget_accounts` 表),故三处数据都按该 id 对齐。
- **行集合以 budget-api monthly-summary 的 accounts 为准**(与屏幕表格一致:表格行来自 budget-api 月度 summary,NC 只是叠加)。NC 有额但无预算计划的科目不单独成行(与 dashboard 现状一致;`filter isPayrollOrDeprec` 也照搬:排除 `CRM004*`/`CRM007*`)。
- 客商名沿用 `nc_partner_monthly` 的做法:直接读 JV 行反范式化的 `JournalVoucherLine.partner_name`(**不**走 `_resolve_dim` 的 supplier∪customer 联合解析),与 dashboard 一致;`partner_id IS NULL` 归 "(no vendor)" 桶。
- `budget_client` **当前无**取月度 summary 的方法,需新增,且该 budget-api 端点需鉴权 → 新方法要**转发调用者 bearer token**。

---

## 3. 后端设计(finance-api)

### 3.1 新增 `budget_client.fetch_monthly_summary`
`finance-api/app/services/budget_client.py`(free-function 模块)新增:
```
async def fetch_monthly_summary(
    *, bearer_token: str | None, fiscal_year: int, cost_center_id: uuid.UUID | None
) -> list[dict]:
    # GET {settings.budget_api_url}/api/v1/actuals/monthly-summary
    #   params: fiscal_year, cost_center_id(有则带)
    #   headers: _auth_headers(bearer_token)   # 该端点需鉴权
    # 返回 response.json()["accounts"]  (每个 dict 含 account_id/code/name/l1_code/plan_by_month/plan_year)
    # 失败(budget-api 不可用)→ 抛出或返回 []?见 §6 错误处理
```

### 3.2 新增批量客商取数 `crud.nc_partner_monthly_all`
`finance-api/app/crud/account_balance.py` 新增(仿 `nc_partner_monthly`,去掉单 `income_expense_item_id` 过滤、增加按 `income_expense_item_id` 分组):
```
async def nc_partner_monthly_all(
    db, *, fiscal_year: int, cost_center_id: uuid.UUID | None = None
) -> dict[str, list[dict]]:
    # accts = await _predreal_subtree(db)            # 5 根科目子树并集(现成)
    # 查 posted JV 行:fiscal_period LIKE '{year}-%', account_code IN accts,
    #   local_debit != 0(与单版一致,滤掉纯贷方结转), 可选 cost_center_id
    # GROUP BY income_expense_item_id, partner_id, partner_name, month
    # 组装 { account_id_str: [ {partner_id, partner_name, by_month{m:amt_str}, year_total} ... ] }
    #   每个科目内 partners 按 year_total 降序;partner_id 为空 → "(no vendor)"
```
> 该函数与逐科目多次 `nc_partner_monthly` 语义等价,但一次查询覆盖全部科目,供导出高效使用。

### 3.3 新增 xlsx 生成器 `services/predreal_export.py`
纯函数,输入三份已取好的数据 + 元信息,输出 `bytes`:
```
def build_partner_export_xlsx(
    *, accounts: list[dict],                         # budget-api monthly-summary accounts(已滤 payroll/deprec)
    nc_monthly: dict[str, dict[int, str]],           # nc_actuals_monthly["accounts"]
    partners_by_account: dict[str, list[dict]],      # nc_partner_monthly_all 输出
    fiscal_year: int, cost_center_label: str,
) -> bytes:
    # openpyxl.Workbook();单 sheet
```
**工作簿布局(单 sheet "Budget vs Actual"):**
- 标题区(前几行,合并/加粗):
  - `Budget vs Actual — Partner Breakdown`
  - `FY {fiscal_year} · Cost Center: {cost_center_label}`
- 表头行:`Category | Account Code | Account Name | Vendor | Metric | Jan | Feb | … | Dec | Year`
- 主体:按 `l1_code` 分组(组内科目按 `account_code` 升序);每个科目:
  1. `[l1_code, code, name, "(subtotal)", "Plan", plan_by_month[1..12], plan_year]`
  2. `[l1_code, code, name, "(subtotal)", "NC",   nc_by_month[1..12], nc_year]`
  3. 每个客商:`[l1_code, code, name, partner_name or "(no vendor)", "NC", by_month[1..12], year_total]`
  - 客商顺序按 year_total 降序(取数已排序)。
- 合计:表尾一行 **Grand Total**,Plan 行与 NC 行各一条(全科目求和)。L1 小计**可选**(本版不做,YAGNI)。
- 金额单元格写 **数值**(`float(Decimal)`),套用会计数字格式(如 `#,##0.00`);0 或缺失写 0(或留空,见 §6)。
- 月份键统一 1..12;缺失月补 0。

### 3.4 新增路由
`finance-api/app/api/v1/account_balance.py` 增加(auth 同其它 `/gl`:仅 `CurrentUser`;另需拿到**原始 bearer** 以转发 budget-api):
```
@router.get("/budget-actual/partner-export")
async def budget_actual_partner_export(
    request: Request,                     # 用于取原始 Authorization 头转发
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    fiscal_year: int = Query(...),
    cost_center_id: uuid.UUID | None = Query(default=None),
):
    token = <从 request.headers['authorization'] 提取 Bearer>
    accounts = await fetch_monthly_summary(bearer_token=token, fiscal_year=fiscal_year, cost_center_id=cost_center_id)
    accounts = [a for a in accounts if not (a['account_code'].startswith('CRM004') or a['account_code'].startswith('CRM007'))]
    nc = (await nc_actuals_monthly(db, fiscal_year=fiscal_year, cost_center_id=cost_center_id))['accounts']
    partners = await nc_partner_monthly_all(db, fiscal_year=fiscal_year, cost_center_id=cost_center_id)
    cc_label = <CC 名 or 'All Cost Centers'>   # cost_center_id 有则查 CostCenter.name,否则 'All Cost Centers'
    data = build_partner_export_xlsx(accounts=accounts, nc_monthly=nc, partners_by_account=partners,
                                     fiscal_year=fiscal_year, cost_center_label=cc_label)
    fname = f"budget-actual-FY{fiscal_year}.xlsx"
    return Response(content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
```
> 完整路径:`GET /finance/v1/gl/budget-actual/partner-export?fiscal_year=&cost_center_id=`。返回 `Response` 模式照搬 `ap_invoices.py` 的 `/nc-export`。

---

## 4. 前端设计(EPMS)

### 4.1 下载助手 `epms/src/lib/api.ts`
新增 `downloadFinanceFile(path, params?, fallbackName?)`,仿现有 `downloadCsv`(同文件)但打到 **finance-api**:
```
// 原始 fetch + Authorization: Bearer(getToken()),res.blob() + <a download> 触发下载;
// 解析 Content-Disposition 文件名,失败回退 fallbackName。
// URL: `${FINANCE_BASE}/finance/v1${path}?${qs}`
```

### 4.2 按钮 `epms/src/pages/budget/BudgetDashboard.tsx`
在 "Monthly Plan vs Actual" 卡片头部(与 Expand all / Collapse all 同一行)加 **"Export XLSX"** 按钮:
- 点击 → 调 `downloadFinanceFile('/gl/budget-actual/partner-export', { fiscal_year: fiscalYear, ...(ccId!=='all' ? { cost_center_id: ccId } : {}) }, 'budget-actual.xlsx')`。
- 下载中禁用按钮 + 文案切 "Exporting…";出错弹提示(与页面现有错误处理风格一致)。
- 无数据(monthlyAccounts 为空)时按钮可禁用或照常导出空表(后端返回仅表头的空表)。

---

## 5. 实现单元(文件清单)

**finance-api**
- 修改 `app/services/budget_client.py` — 加 `fetch_monthly_summary`。
- 修改 `app/crud/account_balance.py` — 加 `nc_partner_monthly_all`。
- 新建 `app/services/predreal_export.py` — `build_partner_export_xlsx`。
- 修改 `app/api/v1/account_balance.py` — 加路由。
- 新建测试 `tests/test_predreal_export.py` — 见 §7。

**epms 前端**
- 修改 `src/lib/api.ts` — 加 `downloadFinanceFile`。
- 修改 `src/pages/budget/BudgetDashboard.tsx` — 加按钮 + handler + 下载中状态。

---

## 6. 错误处理与边界

- **budget-api 不可用**:`/budget-actual-grid` 现有做法是 fail-open(预算记 0 照渲染)。本导出**同样 fail-open**:`fetch_monthly_summary` 失败 → 记日志、accounts 视为空 → 导出仅有表头/合计的空表(不 500),避免因预算服务抖动整个下载失败。
- **无数据**:accounts 为空 → 返回仅表头 + Grand Total(全 0)的工作簿。
- **某科目无 NC / 无客商**:仍出 Plan 行 + NC 行(NC 全 0),无客商子行。
- **金额**:Decimal → `float()` 后写数值单元格并套 `#,##0.00`;缺失月补 0。
- **月份**:统一整型 1..12,后端各来源已是该键。
- **cost_center_id='all'**:前端不带该参 → 后端 None → 各取数函数聚合全 CC;`cc_label='All Cost Centers'`。
- **payroll/deprec**:后端按 `CRM004*`/`CRM007*` 前缀排除(与前端 `isPayrollOrDeprec` 一致)。

## 7. 测试

- **finance-api `tests/test_predreal_export.py`**(纯函数,无需 DB):喂 `build_partner_export_xlsx` 一组固定的 accounts / nc_monthly / partners_by_account,用 openpyxl 读回断言:
  - 表头行正确;
  - 每科目 2 行小计(Plan/NC)+ 正确条数的客商行;
  - 某月/年合计数值正确;`(no vendor)` 桶落位正确;
  - Grand Total = 各科目 Plan/NC 之和。
- **`nc_partner_monthly_all`** 若有 DB fixture 可加等价性测试(与逐科目 `nc_partner_monthly` 结果一致);无则至少冒烟(能跑通、返回 dict)。
- **前端**:EPMS 前端无测试框架 → 类型检查(TS 5.9.3:`npx tsc -p tsconfig.app.json --noEmit`)+ 目视 QA(起 dev server,选 CC/FY 点导出,打开 xlsx 核对结构与几个数值对得上屏幕)。

## 8. 明确不做(Out of scope)

- 不导 Actual(docs) 指标;不导凭证明细(那是页面里再下钻的层级)。
- 不做 L1 小计行、不做多 sheet(全 CC 也是单张汇总表)。
- 不改现有 `/gl` 端点、不加部门 scoping。
- 不引入前端 xlsx 库;不改 dashboard 现有渲染/展开交互。
