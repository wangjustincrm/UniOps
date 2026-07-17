# Finance JV 列表排序 + System 列 + 科目余额维度汇总 设计

> 状态:设计稿(2026-07-17 brainstorm,逐项经用户确认)。
> 归属:Finance —— 用户在 dev 测试 NC 同步时提出的三项界面完善。
> 分支:`feature/finance-nc-coa-sync`(与 NC COA/凭证同步同分支,用户定 2026-07-17)。
> 关联:`2026-07-15-finance-nc-coa-sync-design.md`(#2 紧贴其凭证同步、#3 紧贴其 partner 维度)。
> 复核:三项的 NC 事实与口径均经直连生产 NC 只读实测 + 用户 NC UI 截图对照。

## 0. 三项一览

| # | 需求 | 性质 |
|---|---|---|
| 1 | JV 列表点列头排序 | 前端+后端小功能 |
| 2 | 导入 NC 的 System 列(GL/AP/AR…) | 数据模型+同步+展示 |
| 3 | 科目余额按辅助核算展开显示汇总金额 | **既有缺陷**(展开从来对不齐父级),partner 维度上线后暴露 |

三项相互独立,共用本 spec,实现拆 3 个 Task。

---

## 1. JV 列表点列头排序(#1)

### 1.1 背景

`GET /finance/v1/jv` 是**服务端分页**(默认 50/页,`total`+`offset`,可能上万张),现在写死
`order_by(voucher_date.desc(), jv_number.desc())`。**纯前端排序只能排当前页那 50 条**,
故必须服务端排序。列表已有 `period/status/source_doc_type/q` 四个过滤参数。

### 1.2 后端

`GET /jv` 增两个参数:

- `sort: str | None`(列名白名单;默认 `voucher_date`)
- `dir: str`(`asc` / `desc`;默认 `desc`)

**白名单**(其余值 → 422):`voucher_date`、`jv_number`、`summary`、`total_debit`、
`status`、`source_subsystem`(#2 新列)。**白名单是安全边界** —— 列名直接进 `order_by`,
不得接受任意字符串(SQL 注入面)。

排序键统一追加 `jv_number` 作次级键,保证分页稳定(同排序值的行跨页不跳)。

### 1.3 前端

`JournalVouchersPage.tsx`:列头(Voucher No. / Date / Summary / Debit / Status / System)
可点;当前排序列显升/降箭头;再点同列切方向,点别的列换列(方向重置 desc)。
改排序 → **重置到第 0 页**(否则可能停在空页)。就地重取。

---

## 2. 导入 NC System 列(#2)

### 2.1 NC 事实(实测 2026-07-17)

NC UI 凭证列表的 `System` 列 = `GL_VOUCHER.PK_SYSTEM`(**CHAR,空格补位** —— 必须 strip,
本次已两踩 CHAR padding)。本账簿实测 **9 个值**:

| 码 | 张数 | 全名(NC UI/用户/数据三方印证) |
|---|---|---|
| AP | 19,989 | Accounts Payable |
| GL | 17,454 | General Ledger |
| IA | 936 | Inventory Accounting |
| FA | 376 | Fixed Assets |
| CM | 331 | Cash Management |
| AR | 327 | Accounts Receivable |
| EGL | 298 | Exchange Gain/Loss(数据:"汇兑损益结转") |
| OT | 152 | **未确认 → 回退显示原码** |
| PLCF | 126 | Gain/Loss Carry-Forward(用户口径;=Profit/Loss Carry Forward) |

> 教训记录:首次查 `PK_SYSTEM` 用了 `[:8]` 截断,漏掉了第 9 个 `PLCF` —— 正是用户问的
> "Gain/Loss Carry-Forward"。**枚举 NC 取值不得截断**。

### 2.2 数据模型

`journal_vouchers` 加 `source_subsystem varchar(10)` **可空**(go-forward 手工 JV 无此列)。
迁移 **0024**,`down_revision = "0023_coa_sync_runs"`(实测链尾,勿猜)。

### 2.3 同步(`nc_sync.py`)

- `NcExtract.vouchers` 元组多取一列 `pk_system`(在 tallydate 之后)。
- `fetch_from_nc` 的 voucher 查询 select 加 `pk_system`。
- `transform` 写入 voucher dict:`"source_subsystem": (pk_system or "").strip() or None`
  (**strip 补位**;空 → None)。
- `_run_worker` 的 `v_rows` INSERT 带上该列。
- 存**原码**(GL/AP/…),不在同步层做全名映射(展示层的事)。

### 2.4 展示

- `_hdr(jv)`(列表序列化器)返回 `source_subsystem`(原码)+ `source_subsystem_label`(全名)。
  映射为 `app/api/v1/journal_voucher.py` 内的 8 键常量 `_SUBSYSTEM_LABELS`;
  **未命中(OT/未来新码)→ label = 原码**(不猜)。`None` → label = None。
- 前端列表新增一列 **System**,显 label(全名);可排(§1.2 白名单已含 `source_subsystem`)、
  可筛(§2.5)。

### 2.5 过滤

`GET /jv` 增 `source_subsystem: str | None` 参数(精确匹配原码);前端列表加一个 System 下拉
(选项 = 已知 9 码 + 全名),与既有 status/period 过滤并列。

---

## 3. 科目余额维度展开显示汇总(#3)

### 3.1 根因(实测,非猜测)

父级科目行的 `-349,824.95` 是**期末累计余额 closing**(`account_balance` 算
opening[fiscal_period < period 累计] + movement[本期] = closing)。而 `expand_by_dims`
**只算本期 movement**(`fiscal_period == period`)。660101 本期 gross debit 971,338.12 ==
gross credit 971,338.12(全冲平)→ 每个维度组本期净额 = 0 → 展开全显 `0.00`/`–`。

即:**子行(本期发生)加不到父级(累计余额)**。这不是渲染 bug,是口径不一致。

**用户参照 NC 自己的「科目余额表按辅助项展开」**(截图):NC 每个维度行显示
`期初余额 / 本期借 / 本期贷 / 期末余额 / 方向`,净额为零时方向标 "Balanced"、余额留空。

### 3.2 后端 `expand_by_dims`

每个维度组返回**四个数**(取代原单个 `amount`):

- `opening` = `_net(Σ local_debit, Σ local_credit)` where `fiscal_period < period`
- `period_debit` = `Σ local_debit` where `fiscal_period == period`(gross)
- `period_credit` = `Σ local_credit` where `fiscal_period == period`(gross)
- `closing` = `opening + period_debit − period_credit`

`_net(d,c) = d − c`(实测:`account_balance.py:90`,简单相减、**线性可加**)。故
**子行逐列相加 = 父级科目行的同名列**(opening/period_debit/period_credit/closing),
数学上自洽 —— 这正是「自动汇总」。

实现:现有查询按 dim 列 `GROUP BY` 且只 `fiscal_period == period`。改为**两段查询**:
一段 `< period`(opening,按 dim 分组)、一段 `== period`(本期 gross,按 dim 分组),
在 Python 按 dim key 合并(与父级 `sums()` 的 opening/movement 双查同构)。
返回 `rows: [{keys, opening, period_debit, period_credit, closing}]`。

> **零行维度**:某维度组本期无发生但有期初余额 → 仍要出现(closing 非零)。故合并键 =
> opening 组 ∪ 本期组(与父级 `set(opening) | set(movement)` 同构)。

### 3.3 前端 `AccountBalancePage.tsx`

`ExpandResp.rows` 的 `amount: string` → `{opening, period_debit, period_credit, closing}`。
展开子行从「一个金额」改为对齐父级的多列:期初 / 本期借 / 本期贷 / 期末。
`closing == 0` 时期末列显 "Balanced"(灰)、余额留空,余同 NC。`Vouchers` 钻取按钮不变。

> 已实测:父级科目行返回的正是 `opening / period_debit / period_credit / closing`
> 四个字段(`account_balance.py:59-61`),与本设计给展开的四列**一字不差**,子行直接镜像。

---

## 4. 存量与部署

- **#2 只对新导入生效**:现有 39,977 张无 `source_subsystem`。跟 §14.5 一样,**发布后跑一次
  全量重灌**即补齐(dev 已验证重灌路径)。迁移 0024 先行。
- **#3 纯读侧**,无数据迁移,上线即生效。
- **#1 纯接口/前端**,无数据变更。

## 5. 测试

- **#1**:API 测试 —— `sort=jv_number&dir=asc` 返回有序;非法 `sort` → 422;默认无参仍 date desc。
- **#2**:`transform` 单测 —— `pk_system` 带补位空格 → 存 strip 后原码;`'~'`/空 → None。
  `_hdr` 单测 —— 已知码 → 全名,`OT`/未知 → 原码,None → None。API 测试 —— `source_subsystem`
  过滤命中。**live-NC 冒烟**(照 §14 教训):驱动已提交的 `fetch_from_nc` 打真库,断言 9 码都出、
  strip 正确。
- **#3**:`expand_by_dims` 测试 —— 造 opening(前期)+ 本期借贷的行,断言四列正确、
  **子行逐列合计 == 父级**;本期冲平但有期初的维度仍出现且 closing 非零。

## 6. 明确不做(YAGNI)

- 不给 System 列做「按 GL/AP 分组小计」(只是列+排序+筛选)。
- 不改父级科目行的口径(它 closing 是对的;是子行要向它对齐)。
- 不为 OT/未知码去 NC 猜全名(回退原码;用户日后给了再加进 8 键常量)。
- #1 不做多列组合排序(单列足够)。
