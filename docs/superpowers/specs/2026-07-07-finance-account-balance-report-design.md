# 科目余额表 + Budget Actual 钻取 设计

> 状态:设计稿(brainstorm 产出,逐点经用户确认;待写实现计划)。日期:2026-07-07。
> 归属:Finance 重构 —— GL 报表层,坐在 JV 之上。
> 关联:`2026-07-07-finance-jv-subsystem-design.md`(读其「已过账 JV 本币分录」,**依赖其 GL 读取层迁移
> Plan 3**)、`2026-07-04-nc65-finance-migration-mapping-design.md`(辅助核算/科目来自 NC)。

## 1. 背景与目标
对齐用友 NC 的**科目余额表 / 科目辅助余额表**能力,并在其上支撑 **Budget Actual** 的构成检索。
因初始 JV 全量从 NC 导入(已过账),历史与 go-forward 共用同一套已过账 JV 分录出表。

四项能力:
1. **科目余额表**:每 GL 科目 期初 / 本期借 / 本期贷 / 期末(本位币 CAD)。
2. **按辅助核算展开(可勾选动态组合)**:把一个科目按其配置的辅助核算项的**任意子集**展开分组。
3. **凭证钻取**:展开后的任一行 → 查出由哪些 JV 凭证构成。
4. **Budget Actual 视图**:4 个费用科目按 Cost Center 前缀归类 → 各 Cost Center 的实际。

## 2. 范围与数据源
- **数据源 = 已过账 JV 分录**(`journal_voucher_lines` where 所属 `journal_vouchers.status='posted'`),取 **本位币金额**
  `local_debit/local_credit`,按 `fiscal_period`/`account_code`/维度聚合。历史 NC 导入 JV 自动纳入。
- **科目主数据** = `chart_of_accounts`(NC 中式科目 + 层级/级次 + 余额方向)。
- **对账**:期末余额可与 NC `GL_BALANCE` 逐科目核对(迁移期)。
- 现有 `finance-api/app/crud/gl.py` 的 `trial_balance`(期初/发生/期末)、`account_ledger`(科目明细)是雏形,
  迁移后改读 JV;本设计把它们扩成完整科目余额表 + 辅助展开 + 钻取。

## 3. 能力①:科目余额表
- 输入:期间范围(period 或 period_from..period_to)、科目范围/级次、是否含未过账(默认否)。
- 每科目输出:`opening_debit/credit`、`period_debit/credit`、`closing_debit/credit`(本位币),按科目方向
  呈现净额。
- 期初 = 期间之前所有已过账 JV 分录的累计;本期 = 区间内发生;期末 = 期初 + 本期。
- 支持按科目**级次(INNERCODE 层级)汇总/展开**(父科目汇总子科目)。

## 4. 能力②:按辅助核算展开(可勾选动态组合)
NC 科目辅助余额表玩法:
- 每个 GL 科目在设置里挂**一组辅助核算项**(来自 NC `BD_ACCASSITEM`,如 部门 / Cost Center / 收支项目 /
  供应商 / 项目)。这份「科目→可用辅助核算项」配置需**随迁移导入**并存于 COA(见 §7)。
- 报表 UI 列出该科目**可展开的辅助核算项**,用户**任选子集**(勾 2 个按 2 维分组;全勾全展开;组合/顺序随意)。
- 后端 **动态 GROUP BY**:`account + [选中的维度…]` → 每个维度组合一行余额(期初/本期借贷/期末)。
- **统一维度取值查询模型**(关键):选中的维度可能混合两类——
  - **列式高频维度**(`journal_voucher_lines.cost_center_id / department_id / partner_id / project_id`)→ 直接 GROUP BY 列;
  - **KV 长尾维度**(`jv_line_dimensions`,如 收支项目 income_expense_item)→ 按 `dim_code` LEFT JOIN 取值再 GROUP BY。
  - 报表层用一个「维度描述符」表把每个可选维度映射到「列 or KV(dim_code)」,拼动态查询。
  > 优化点:**收支项目(income/expense item)** 因 Budget Actual 高频用到,建议在 JV 落地时**提升为
  > `journal_voucher_lines` 的列**(`income_expense_item_id`),避免每次 join KV;其余低频维度留 KV。

## 5. 能力③:凭证钻取
- 展开后任一行(某 account + 维度组合值)→ 列出构成它的 **JV 分录**(该 account + 匹配维度 + 期间的
  `journal_voucher_lines`),每行链到其凭证头(`journal_vouchers`,含凭证号/日期/摘要/来源单据)。
- 凭证头再链 `source_doc_*` → 源业务单据(Document Chain 子项目③的基础,先做单跳)。

## 6. 能力④:Budget Actual 视图
**业务规则(用户定):** Budget Actual 由 4 个费用科目决定,按 Cost Center 前缀归类:

| GL 科目 | 费用类别 | Cost Center 前缀 |
|---|---|---|
| **5101** 制造费用 | MOH | `MOH*` |
| **5301** 研发费用 | R&D | `RD*` |
| **6601** 销售费用 | Selling | `SELL*` |
| **6602** 管理费用 | G&A | `GA*` |

- 取这 4 个科目的已过账 JV 分录,按 **部门 + 收支项目 + Cost Center** 展开(即能力②的一个预设组合),
  金额分摊到各 Cost Center → 各 Cost Center 的 **Budget Actual**;Cost Center 前缀决定归入 MOH/RD/SELL/GA。
- 每个 Cost Center 的实际可再**钻取到构成凭证**(能力③)。
- **与 budget-api 的关系**:budget-api 的 `budget_ledger`(operation=actualize/book_expense)按 **budget
  account_id + cost_center** 记实际,是**独立编码体系**。本视图的 Budget Actual 直接**从 GL(已过账 JV)按上述
  规则算**,作为 GL 侧口径;与 budget-api 的 actual 可做**对账**(两者应一致或差异可解释),不互相覆盖。
- 4 科目↔CC 前缀、以及「哪些辅助核算维度参与展开」做成**配置**(见 §7),不硬编码。

## 7. 数据模型 / 配置新增
1. **科目辅助核算配置**:COA 侧存「每个 GL 科目挂了哪些辅助核算项」(从 NC `BD_ACCASSITEM` 导入)。
   可扩现有 `chart_of_accounts` 或加一张 `coa_aux_items(account_code, dim_code, seq)`。报表据此列可勾选维度。
2. **维度描述符**:一张「可选维度 → 存储位置(column / kv dim_code)+ 显示名 + 取值来源(主数据表)」的
   配置(可代码常量起步),供动态 GROUP BY 与显示解析。
3. **收支项目提列**(优化):JV 落地时在 `journal_voucher_lines` 加 `income_expense_item_id` 列。
4. **Budget Actual 配置**:`budget_actual_accounts(account_code, category, cost_center_prefix)` —— 存
   5101/5301/6601/6602 ↔ MOH/RD/SELL/GA 前缀,避免硬编码。

## 8. API(finance-api,读 JV)
- `GET /gl/account-balance?period_from&period_to&account_from&account_to&level` → 科目余额表。
- `GET /gl/account-balance/{account_code}/expand?period_from&period_to&dims=dept,cost_center,...` →
  按选中维度动态展开(每组合一行余额)。
- `GET /gl/account-balance/{account_code}/vouchers?period&dims_values=...` → 某展开行的构成凭证明细。
- `GET /gl/budget-actual?period_from&period_to[&category=MOH]` → 4 科目按 CC 前缀归类的 Budget Actual,
  行可钻取。
- 全部读 `journal_vouchers(status=posted)` + `journal_voucher_lines`,本位币聚合。

## 9. UI(Portal Finance 区)
- **科目余额表页**:期间/科目/级次筛选;树形按级次展开;每科目一行(期初/本期借贷/期末)。
- 行内「按辅助核算展开」:弹出该科目**可勾选的辅助核算项** → 选中后就地展开为多行;每行可继续「查凭证」。
- **Budget Actual 页**:按 MOH/RD/SELL/GA + Cost Center 呈现实际,钻取到凭证。

## 10. 依赖与排期
- **依赖 JV 子系统 Plan 3(GL 读取层迁移到已过账 JV)** —— 本报表读 JV 本币分录。
- 依赖 NC 迁移导入的:COA(含科目辅助核算配置)、辅助核算维度值、历史已过账 JV。
- 排期:JV Plan 1(✅)→ Plan 2 → **Plan 3** → 本报表。

## 11. 待解决(实现前)
- 收支项目是否提列(§7.3)最终定。
- 辅助核算维度值的显示名解析(维度值 = 档案 PK,需 join 各主数据表取名)。
- 期初余额来源:纯由历史 JV 累计,还是也接受 NC 期初余额行(NC 期初本身是「期初」凭证 → 已在 JV 内)。
- 级次汇总的性能(大量分录聚合,是否需物化/缓存期末余额)。
- Budget Actual 与 budget-api actual 的对账口径与差异处理。

## 12. 不在范围
- Document Chain 完整血缘(仅做凭证→源单据单跳)。
- budget-api 预算计划/编制侧改动(本设计只读 GL 侧实际 + 与其对账)。
- 修改 JV 子系统本身的生命周期/生成(见 JV spec)。
