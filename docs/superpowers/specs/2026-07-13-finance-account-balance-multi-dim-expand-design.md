# 科目余额表 通用多维展开(能力②完整版)设计

> 状态:设计稿(2026-07-13 brainstorm,决策经用户确认)。
> 归属:Finance 重构 —— GL 报表层,细化 `2026-07-07-finance-account-balance-report-design.md` §4/§7/§11 的留白。
> 前置:JV Plan 1-5 已完成(GL 已切 posted JV);NC 三维辅助核算已导入(CC/部门=行列,收支项目=KV)。

## 1. 用户已确认的决策

- **科目→可勾维度配置 = 忠实 NC**:从 NC `BD_ACCASS`(科目↔辅助项挂接,pk_coveraccasoa→BD_ACCASOA→科目,pk_entity→BD_ACCASSITEM 类型)导入 `coa_aux_items` 表;每科目只列它在 NC 真挂的辅助项。
- **收支项目提列(现在做)**:`journal_voucher_lines` 加 `income_expense_item_id`,迁移内回填(来源 `jv_line_dimensions.dim_code='income_expense_item'` 的 value_id,66,315 行);KV 行保留作审计(CRM code 文本),**查询一律走列**。提列后三个维度全是列,动态分组=纯列 GROUP BY,无 KV join。

## 2. 数据模型(迁移 0019,单迁移)

1. `journal_voucher_lines.income_expense_item_id uuid NULL` + 索引 `ix_jv_lines_ioitem`;同迁移内数据回填(UPDATE…FROM 侧表,value_id 非空才填)。
2. `coa_aux_items(id uuid pk, account_code varchar(10) not null, dim_code varchar(40) not null, seq int not null default 0, created_at/updated_at)`,UNIQUE(account_code, dim_code)。
3. 只读镜像 `mirrors.BudgetAccount`(表 budget_accounts,budget-api 拥有;**列子集** id/code/name/is_active,物理列 2026-07-13 已按 information_schema 核对全 NOT NULL;conftest 注册建表——沿用 budget_accounts 缺表 savepoint 教训,测试库由 conftest 建)。

## 3. 写路径同步(提列后)

- **NC 导入器**(`services/nc_sync.py` transform/载入 + `scripts/nc_migration/voucher_import.py`):line tuple 增加 `income_expense_item_id`(=ba_id,可空)写列;**KV dims 照旧双写**(value_text=CRM code 审计价值)。
- **go-forward** `services/journal_voucher.generate_from_event`:拷贝 posting_line_dimensions 时,遇 `dim_code='income_expense_item'` 除写 KV 外把 value_id 同步写到行列。
- 现网/dev 存量由迁移回填覆盖,无需重导。

## 4. 维度注册表(代码常量,`crud/account_balance.py`)

```python
DIMENSIONS = {
  "cost_center":         (JournalVoucherLine.cost_center_id,         mirrors.CostCenter),
  "department":          (JournalVoucherLine.department_id,          mirrors.Department),
  "income_expense_item": (JournalVoucherLine.income_expense_item_id, mirrors.BudgetAccount),
}
```

`supported` = dim_code ∈ DIMENSIONS。将来加 supplier/customer(往来维度落地后)只需注册一行。

## 5. BD_ACCASS 导入(`scripts/nc_migration/aux_items_import.py`)

- 链:`BD_ACCASS.pk_coveraccasoa → BD_ACCASOA.pk_account → BD_ACCOUNT(code, chart CG6GD)`;`BD_ACCASS.pk_entity → BD_ACCASSITEM(name/code/classid)`。
- NC 辅助项 → dim_code:**按 BD_ACCASSITEM.name 的整定映射**(部门→department、成本中心→cost_center、收支项目→income_expense_item、供应商→supplier、客户→customer、人员/职员→employee、项目→project),未命中→BD_ACCASSITEM.code 小写 slug。**全部导入**(含暂不支持的),UI/API 按 supported 标记。
- 幂等:重跑先清 `coa_aux_items` 再灌(全量小表);硬拒 10.10.50.* 同脚本惯例;dry-run 模式打印 distinct 辅助项名称便于核对映射。

## 6. API(finance-api `/gl`)

- `GET /gl/account-balance/{code}/dims` → `{account_code, dims: [{dim_code, label, supported}]}`(coa_aux_items 按 seq;label=dim_code 的英文显示名常量;科目无配置行时返回 DIMENSIONS 全集作 fallback——NC 导入前/新科目也能用)。
- **`expand` 泛化**:`GET /gl/account-balance/{code}/expand?period&dims=cost_center,department`(逗号子集,缺省 `cost_center`;含未注册 dim → 422)。动态 `GROUP BY` 选中列;响应统一:
  `{account_code, period, dims: [...], rows: [{keys: [{dim_code, id|null, code|null, name|null}], amount}]}`
  维度值 NULL 归 `(none)` 组(key 的 id/code/name 均 null);名称经各自镜像解析。旧响应字段(cost_center_code 等)废弃,前端同步改。
- **`vouchers` 钻取泛化**:`GET /gl/account-balance/{code}/vouchers?period&dims_values=cost_center:<uuid>,income_expense_item:none`(逗号 `dim:value` 对;value=`none` 表示该维为 NULL;未注册 dim → 422)。替换原 `cost_center_id` 参数(内部 API,前端同步改,不留兼容)。
- Budget Actual 端点不动(仍是 CC 预设口径)。

## 7. UI(AccountBalancePage)

- 行内展开改为两段:点展开图标 → 小弹层(portal 到 body,遵循浮层规范)列出 `/dims` 返回的维度 checkbox(默认勾 cost_center;`supported:false` 置灰带 "no data yet" 提示)→ Apply 后按组合就地展开多行。
- 展开行显示:各选中维度的 `code · name`(none 显示 "(none)")+ 金额 + Vouchers 按钮(带 dims_values 组合过滤)。
- `AccountVouchersModal` 的 `costCenterId` prop 改为 `dimsValues?: string`(直接传查询串);Budget Actual 页同步适配(其钻取 = `cost_center:<id>` 单维)。

## 8. 测试

- 迁移回填:侧表行数与列非空数一致(构造数据断言)。
- expand:单维 cost_center 与旧口径同值(回归锚);两维/三维组合各行相加=科目净额;NULL 归 (none);非法 dims 422。
- vouchers:组合过滤(含 `none`)行集正确;非法 dim 422。
- dims 端点:有配置回配置、无配置回全集 fallback、supported 标记。
- aux_items_import:注入 fake NC 行测映射(部门/成本中心/收支项目/供应商→dim_code;未知名→slug)。
- generate_from_event 提列:带 income_expense_item 维度的事件生成 JV 后行列有值。
- 前端 typecheck + 浏览器冒烟(5101 勾 CC+收支项目 两维展开,数字与单维口径核对)。

## 9. 不在范围

- supplier/customer 维度的数据导入(往来维度子项目;本设计只预留注册位与 supported 标记)。
- Budget Actual 改造、科目级次树形汇总、物化/缓存。
- NC BD_ACCASS 之外的辅助配置管理 UI(coa_aux_items 只读,由导入维护)。
