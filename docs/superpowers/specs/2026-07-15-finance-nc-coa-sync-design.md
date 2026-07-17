# NC 科目表(COA)同步接口设计 —— 含辅助核算与存量数据修复

> 状态:设计稿(2026-07-15 brainstorm,逐段经用户确认)。
> 归属:Finance 重构 —— NC65 迁移工作流的 UI 化(并行期运维能力)。
> 关联:`2026-07-13-finance-nc-sync-button-design.md`(凭证同步,姊妹件与对照组)、
> `2026-07-04-nc65-finance-migration-mapping-design.md`(映射规则来源)、
> `finance-api/scripts/nc_migration/coa_import.py` 与 `aux_items_import.py`(被移植的脚本,保留)。
>
> **本设计的全部字段映射均经 2026-07-15 直连生产 NC(10.10.95.67)只读复核,
> 并与用户提供的 NC UI 截图逐列对照。数据字典(`C:\Project\nc_dict`)与实例不符之处
> 一律以实例为准 —— 详见 §2.1。**

## 1. 背景与目标

凭证同步已 UI 化(NC Sync 按钮),但 COA 与辅助核算至今是开发机上的一次性手工脚本
(`coa_import.py`、`aux_items_import.py`):NC 连接硬编码 `C:\Project\nc65_conn.env`,
默认写死本地 dev DSN,且硬拒生产(DSN 含 `10.10.50` 即 `sys.exit`)。
生产的 COA 只能靠「本地跑脚本 → 导出 CSV → 生产页面上传」的迂回路径进入,
辅助核算(`coa_aux_items`)连 CSV 这条后路都没有。

硬耦合:**凭证行按 NC 科目编码落账,COA 与 NC 不一致则凭证入不了账;
`coa_aux_items` 与 NC 不一致则 Account Balance 按辅助核算展开出错。**
COA、辅助核算、voucher 必须同源。

### 1.1 本设计不只是新功能,更是生产数据修复

复核过程中发现:**现有导入器在 NC 已给出事实的地方靠科目编码前缀猜测**,
导致存量 COA 存在实测缺陷(§2.2)。其中 18 个科目方向记反,
直接影响 Account Balance 与 GL 的取数正确性。

### 1.2 用户已确认的决策

- **范围 = COA + 科目辅助核算挂接**(`chart_of_accounts` + `coa_aux_items`)。
  不收编 `customers_import.py`。
- **删除语义 = 停用,不删**:NC 未返回的科目置 `is_active=false`,永不 DELETE。
- **确认模型 = 先预览再应用**:预览展示差异,admin 确认后才写库。
- **权限 = `finance.coa.manage`**(Access Control 矩阵键),预览与应用同门禁。
  **2026-07-16 修订**:原定「仅 `system_admin`(JWT role)」,对齐 voucher NC Sync。
  期间 authz ②期落地(main `f7e23d8`),确立「**COA 谁能改由矩阵决定,不改代码**」,
  COA 页全部写操作(新增/改/删/CSV 导入)已改走 `require_permission("finance.coa.manage")`,
  默认授予 (system_admin, finance_manager)。
  **理由**:`POST /coa/import` 上传 CSV 即可整表覆盖 COA,与本同步破坏力相同 ——
  同步的数据源还是 NC 而非人工 CSV,反而更安全。若同步独用 system_admin,
  则 finance_manager 传个 CSV 就能达成同样效果、点同步却 403:**同样的破坏力两套门禁**,
  且严的那套绕得过。故复用同一把锁,零新增权限键。想让谁同步,在 Portal 勾。
- **架构 = 同步执行**:无后台 worker、无进度表、无轮询。
- **辅助核算真相源 = `coa_aux_items`**:给它加 `required` 列、填 `seq`;
  `aux_dimensions` JSONB 不再人工维护,COA 页编辑器降为只读,列留在原地待后续清理。
- **存量修复 = 靠同步自然修复**:upsert 覆盖 NC 事实即纠正,不另写修复脚本。

### 1.3 为什么不照搬 voucher 的形状

`nc_sync_runs` + 后台线程 + 30 分钟 stale 清扫 + 前端轮询,全部因凭证量大、跑得久而存在。
COA 仅 350 行、辅助核算 234 行,读取约 1-2 秒。且「预览 → 确认」本就需要两次调用,
同步返回比轮询自然。同理**不**把 `nc_sync_runs` 泛化成 `kind` + JSONB 计数:
那要动刚在生产验证通过的 voucher 同步,为尚不存在的需求承担回归风险。

## 2. NC 复核结论(2026-07-15 实测)

### 2.0 ⚠️ 科目表用错了(2026-07-17 修订,推翻本设计的原始前提)

**原 spec 与现有 `coa_import.py` 都用 `CHART = 1001A1100000003CG6GD`,并称其为
「Canada Royal Milk 的科目表」。这是错的** —— 它是 `0005_0005 飞鹤加拿大_根科目表`。
Canada Royal Milk 是**另一张表**:

| pk_accchart | code | name |
|---|---|---|
| `1001A1100000003CG6GD` | `0005_0005` | 飞鹤加拿大_根科目表 ← 原 spec 用的 |
| **`1001A1100000003CGN3F`** | **`CRM0001`** | **加拿大皇家妙克 ← 正确** |
| `1001A1100000003CGMUM` | `CRM001` | 飞鹤加拿大 |

**决定性证据**:`nc_sync.py` 的 `PK_BOOK = 1001A1100000003CGCBX`(已对账 0 差异的
凭证同步)下,**全部 315,539 条 `GL_DETAIL` 行的科目都属于 CRM0001,root 一条也没有**。
凭证记在 CRM0001,COA 就必须是 CRM0001 的,否则回到本设计要解决的原始问题。

**NC 的模型**:科目**定义**在 root(`BD_ACCOUNT.pk_accchart` = 创建科目表),
但在各账簿的科目表里**分别启用并配置**(`BD_ACCASOA`,每 chart 一行 —— 实测单个科目
最多有 19 行 ACCASOA)。故:

> **账户集取 `BD_ACCOUNT`;启用状态、末级标志、科目名、辅助核算 —— 一律取
> CRM0001 的 `BD_ACCASOA`。判断启用用 `soa.enablestate = 2`,不是 `a.enablestate`。**

实测后果:root 启用但 **CRM0001 已停用(enablestate=3)的科目有 10 个** ——
`221110`、`22250301 PST paid`、`22250302 PST Collected`、`22250399 PST Others`、
`222505 应交房产税`、`60010203 折扣`、`640106 存货冲销`、`660305 NR 税`、
`660306 资产税`、`660307 担保费用`。用 root 口径会把这 10 个当启用科目灌进来。

**用户已确认改用 CRM0001**(2026-07-17)。

### 2.0.1 ⚠️ 辅助核算 join 错了列

`BD_ACCASS` 同时有 `PK_ACCASOA` 与 `PK_COVERACCASOA` 两列。
现有 `aux_items_import.py`(及原 plan,照抄自它)join 的是 `pk_coveraccasoa` ——
**拿用户提供的 NC UI 截图当真值实测,该 join 返回空**:

| 科目 | NC UI 真值 | join `pk_coveraccasoa` | join `pk_accasoa` |
|---|---|---|---|
| `101201` | 序1 `0022` 银行类别、序2 `0011` 银行账户 | **空** ✗ | `[0022 1 N, 0011 2 N]` ✓ |
| `1402` | 序1 `0006` 物料基本信息 | **空** ✗ | `[0006 1 N]` ✓ |
| `6002` | 序1 `0004` 客商、序2 `0006`(允许为空☑) | 空 ✗ | `[0004 1 N, 0006 2 Y]` ✓ |

> **正确 join = `soa.pk_accasoa = a.pk_accasoa`**,并以 `soa.pk_accchart = CRM0001`
> + `soa.enablestate = 2` 过滤。三个科目全部逐项吻合(含 6002「允许为空」勾选
> ↔ `isempty='Y'`)。

### 2.1 数据字典不可信,以实例为准

`C:\Project\nc_dict`(NC6.5 通用数据字典)与本实例存在实质差异:

- 字典称 `bd_account` 有 `endflag` 末级标志 —— **实例无此列**(实测 ORA-00904),
  它在 `BD_ACCASOA` 上。
- 字典列出的 `accassname`/`dispname`/`creationtime`/`def1-5`/`usedesc` 等,
  `BD_ACCOUNT` 实例均无;实例反有字典未列的 `DR`/`TS`/`NAME3-6`。

**结论:字典仅用于理解语义,列的存在性与取值一律以 `all_tab_columns` 实测为准。**

### 2.2 `~` 是 NC 的空值哨兵

NC 用字符串 `~` 表示空,而非 NULL。`count(col)` 会把 `~` 计为有值 —— 实测填充率必须
排除 `~` 后再算。现有 `coa_import.py` 已知此事(`_clean(v) = v if v and v != "~" else None`),
新实现必须对 **`unit` / `currency` / `pid` / `name*`** 一律做 `~` 清洗。

### 2.3 存量缺陷实测(2026-07-17 按 CRM0001 口径重算)

CRM0001 实测基线:**350 个启用科目**(`soa.enablestate=2`),acctype 分布
1:94 / 2:95 / 4:24 / 5:35 / 6:102,`endflag` Y=296 / N=54,辅助核算 **234 行**
(`isempty` N=200 / Y=34,`seq` 取值 1-7,20 个不同辅助核算项,
`(account,item)` 重复数 **0**)。

| 缺陷 | 科目数 | 根因 |
|---|---|---|
| **科目表用错** | **10 个多余** | 用 root 而非 CRM0001 → 10 个 CRM 已停用的科目被当启用(§2.0) |
| `normal_balance` 记反 | **18** | 按编码前缀猜,未读 `BALANORIENT`(CRM0001 350/350 有值):17 个备抵科目(1 开头却贷方)+ 1 个成本类贷方 |
| `quantity_accounting` 错 | **29** | 读了 `QUANTITY` 列,而真正判据是 `UNIT` 是否有真实值(30 个有真实 unit,旧规则只蒙对 6002 一个) |
| `default_currency` 全空 | **172** 可填 | 未读 `CURRENCY`(需 join `BD_CURRTYPE` 取 code) |
| `default_uom` 全空 | **30** 可填 | 未读 `UNIT`(需 join `BD_MEASDOC` 取 code) |
| **辅助核算 join 错列** | 全部 | join 了 `pk_coveraccasoa`(§2.0.1),UI 真值实测返回空 |
| `coa_aux_items.seq` 是**合成的**,非 NC 序号 | 234 行 | 老脚本用 `len([r for r in rows if r[0]==acct])+1` 按 SELECT 返回顺序**编出**一个 1..N 计数器(dev 实测取值 1-4),而 NC 的真值在 `BD_ACCASS.ID`(取值 1-7)。**顺序是任意的,不反映 NC 配置的序号** —— 2026-07-17 更正:此前本行误称「恒为 0」,实测 100/100 行 seq>0 |
| `aux_dimensions.required` 从未填充 | 234 行 | 未读 `BD_ACCASS.ISEMPTY`(200 必填 / 34 可空) |

17 个备抵科目(CRM0001 与 root 相同):`1231`、`123101`~`123104`、`1471`、`1512`、
`1602`、`1603`、`1607`、`1608`、`1620`、`1621`、`1702`、`1703`、`1713`、`190102` ——
累计折旧、累计摊销、各类坏账/减值准备、政府资助固定资产及其折旧。

18 个方向记反的科目全部是备抵科目,NC `name2` 自证:
累计折旧 Accumulated depreciation、累计摊销 Accumulated amortization、
坏账准备(关联方/非关联方/预付/其他应收)、存货跌价准备 Inventory reserve、
长期股权投资减值准备、固定资产减值准备、政府资助固定资产及其折旧 PPE Contra account,
外加 1 个成本类贷方科目。

### 2.4 `数量核算` 的判据 = `UNIT` 有值(全列穷举排除法)

UI 真值(用户截图):`1230 发出商品`/`1402 在途物资`/`6002 销售折扣` 数量核算**勾选**、
计量单位千克;`101201 信用卡押金` **未勾**、无计量单位。
而 DB 中 `QUANTITY`:1230='N'、1402='N'、**6002='Y'** —— 与 UI 勾选状态无对应关系。

对 `1402`(勾) vs `101201`(不勾)做**两表全列 diff**,排除名称/时间戳/层级/主键后,
唯一有意义的差异列为 `BALANFLAG`(已证 = 余额方向控制)、`CASHTYPE`(已证 = 现金分类)、
`ACCLEV`(科目级次)、**`UNIT`**。**无任何布尔列能区分数量核算开关。**

> **结论:`quantity_accounting = (unit 有真实值,即非 NULL 且非 `~`)`。**
> `QUANTITY` 列语义不明(既非数量核算、亦非凭证必输项-数量:6002 的凭证必输项「数量」
> 未勾却 `QUANTITY='Y'`),**本设计不使用该列**。

### 2.5 UI 复选框 → 列的已验证对照

`账簿余额双向显示`=`BOTHORIENT`、`余额方向控制`=`BALANFLAG`、
`发生额方向控制`=`INCURFLAG`、`表外科目`=`OUTFLAG`、`末级标志`=`BD_ACCASOA.ENDFLAG`。
(以 1402 / 101201 双向验证通过。)

## 3. 字段映射表(NC → UniOps,全部经实测)

### 3.1 NC 拥有 —— 同步每次覆盖

| UniOps 字段 | NC 来源 | 规则 |
|---|---|---|
| `code` | `BD_ACCOUNT.CODE` | 主键 |
| `name` | `BD_ACCASOA.NAME2` → `.NAME` → `BD_ACCOUNT.NAME2` → `.NAME` → `code` | `~` 清洗后回退链;ACCASOA(CRM0001 的那行)覆盖率远高于 `BD_ACCOUNT.NAME2` |
| `account_type` | `BD_ACCTYPE.CODE` + `BD_ACCOUNT.BALANORIENT` | 1=asset,2=liability,4=equity,5=expense(成本),**6(损益)按 balanorient 拆:1(贷)=revenue、0(借)=expense** |
| `normal_balance` | `BD_ACCOUNT.BALANORIENT` | 0=debit,1=credit(**事实,非推断**) |
| `is_postable` | `BD_ACCASOA.ENDFLAG`(CRM0001 那行) | ='Y'(CRM0001 实测 Y=296 末级 / N=54 父) |
| `parent_code` | `BD_ACCOUNT.PID` → pk2code | `~` = 顶层 → NULL |
| `quantity_accounting` | `BD_ACCOUNT.UNIT` | 有真实值(非 `~`)= true(见 §2.4) |
| `default_uom` | `BD_MEASDOC.CODE` via `BD_ACCOUNT.UNIT` | join 取 code(KGM/MTQ/LTR/EA/°);`~` → NULL |
| `default_currency` | `BD_CURRTYPE.CODE` via `BD_ACCOUNT.CURRENCY` | join 取 code(CAD);`~` → NULL |
| `is_off_balance` | `BD_ACCOUNT.OUTFLAG` | ='Y'(实测本账簿全 N) |
| `is_active` | 存在性 / `ENABLESTATE` | NC 返回=true,未返回=false(见 §5) |

### 3.1.1 未知取值一律显式失败,不静默降级

现有 `coa_import.py` 的 `account_type()` 以 `return "asset"` 兜底 —— **任何不认识的
科目编码都被静默当作资产**,正是 §2.3 那类缺陷的成因模式。新实现反其道而行:

- `BD_ACCTYPE.CODE` ∉ {1,2,4,5,6} → **抛错并在预览中列出该科目**,不猜。
  (实测本账簿仅这 5 类;NC 若新增科目类型,应由人决定映射,而非代码默认。)
- `BALANORIENT` ∉ {0,1} → 抛错。
- `BD_ACCASOA` 行缺失 → 抛错。`is_postable`(ENDFLAG)与 `name` 均依赖它;
  CRM0001 口径下 350 个启用科目各有且仅有一行 ACCASOA(join 已按 `soa.pk_accchart`
  约束,见 §2.0),故缺失即属异常,不得降级为「按 pid 推断叶子」之类的兜底。
- `BD_MEASDOC` / `BD_CURRTYPE` 查不到对应 pk → 抛错(而非把 UUID 原样写进 code 列)。

失败即整次同步中止、一行不写(§9 单事务)。**宁可拒绝同步,也不写入猜测值** ——
本设计的全部价值在于用事实替换推断,兜底默认值会把推断从前门赶出去、又从后门放进来。

### 3.2 明确不承接

| UniOps 字段 | 原因(实测) |
|---|---|
| `mnemonic` | `REMCODE` 在 NC **全空**(实测 BD_ACCOUNT 0 条有值,BD_ACCASOA 仅 1 条) |
| `cash_flow_category` | 疑似来源 `CASHTYPE` **语义不符**:NC 是「现金分类」(0=其它/1=现金科目/2=银行科目/3=现金等价物,用于标记哪些科目属现金及等价物),我们是现金流量表类别 |
| `effective_from` / `effective_to` | **NC 无对应列**;UI 有「生效日期」栏但实测全为 `0000-00-00`,无数据 |
| `subtype` | UniOps 自有概念,NC 无对应 |
| `entity_id` | UniOps 多主体字段,NC 单账簿场景无来源 |
| `aux_dimensions` (JSONB) | 真相源迁至 `coa_aux_items`(§1.2),同步不写,待后续清理退役 |

### 3.3 辅助核算(`coa_aux_items`) —— NC 拥有

| UniOps 字段 | NC 来源 | 规则 |
|---|---|---|
| `account_code` | `BD_ACCASS` → `BD_ACCASOA.pk_accasoa` → `BD_ACCOUNT.code` | 现有 join 链不变 |
| `dim_code` | `BD_ACCASSITEM.CODE` → §3.4 映射表 | **改为按 NC item code 映射(不再按名称子串)**,见 §3.4 |
| `seq` | `BD_ACCASS.ID` | **新增读取**,实测取值 1-7 |
| `required` | `BD_ACCASS.ISEMPTY` | **新增列 + 新增读取**:`required = (isempty == 'N')`(允许为空=N → 必填);实测 N=187 / Y=18。已用 UI 双向验证(101201 两项「允许为空」未勾 ↔ isempty='N') |

### 3.4 辅助核算项映射(全部 20 项,按 NC item code)

**现有 `NAME_MAP` 按名称子串匹配,实测有真 bug**:`D45 项目类型` 与
`CRM02 政府拨款项目` 均因含「项目」二字被错映射为 `project`。
**新实现按 `BD_ACCASSITEM.CODE` 精确映射** —— code 是稳定标识,名称是模糊的。

用户决策:**20 项全部写入 `coa_aux_items`**,展不开的以 `supported:false` 呈现
(`supported` 由 `account_balance._dimensions()` 判定,见 §3.5)。

| NC code | NC 名称 | dim_code | 英文标签 | 可展开 |
|---|---|---|---|---|
| `ra01` | 成本中心 | `cost_center` | Cost Center | ✓ |
| `0001` | 部门 | `department` | Department | ✓ |
| `0008` | 收支项目 | `income_expense_item` | Income/Expense Item | ✓ |
| `0019` | 供应商档案 | `supplier` | Supplier | ✓ |
| `0017` | 客户档案 | `customer` | Customer | ✓ |
| `0004` | 客商 | `partner` | Partner (Vendor/Customer) | ✓(见 §3.4.1) |
| `0006` | 物料基本信息 | `item` | Item / Material | ✗ |
| `0012` | 物料基本分类 | `item_category` | Item Category | ✗ |
| `0010` | 项目 | `project` | Project | ✗ |
| `D45` | 项目类型 | `project_type` | Project Type | ✗ |
| `CRM02` | 政府拨款项目 | `government_grant_project` | Government Grant Project | ✗ |
| `fa01` | 资产类别 | `asset_category` | Asset Category | ✗ |
| `D47` | VAT Tax Code and Tax Rate | `tax_code` | VAT Tax Code / Rate | ✗ |
| `0022` | 银行类别 | `bank_category` | Bank Category | ✗ |
| `0023` | 银行档案 | `bank` | Bank | ✗ |
| `0011` | 银行账户 | `bank_account` | Bank Account | ✗ |
| `0044` | 国家地区 | `country_region` | Country / Region | ✗ |
| `0002` | 人员档案 | `employee` | Employee | ✗ |
| `D09` | 销售类型 | `sales_type` | Sales Type | ✗ |
| `CRM01` | 信用卡号 | `credit_card` | Credit Card | ✗ |

dim_code 尽量复用 `aux_dimension_types` 已登记的 code
(`cost_center`/`department`/`income_expense_item`/`item`/`project`/`bank_account`/
`bank_category`/`country_region`/`sales_type`/`partner`),避免第三套命名。

**NC item code 不在表中 → 抛错**(§3.1.1 同一原则:不 slug、不猜)。
新增辅助核算项应由人决定映射。

#### 3.4.1 `0004 客商` = `partner` 维度(2026-07-17 重做,取代原「派生规则」)

**原设计**:客商在 NC 中同时涵盖供应商与客户,而我们分 `supplier`/`customer` 两个维度,
故按科目性质**派生**(资产→customer、负债→supplier、损益按方向拆…),并为语义硬伤
科目维护例外名单。**该设计已废弃** —— 它两次被实测推翻:

1. 例外名单 `{4001, 1511, 1512}` 在 CRM0001 口径下不完整:客商科目从 23 个增至 41 个,
   新增的包含整个 `1511/151101/151102/151103` 长期股权投资子树,以及 `1131 应收股利`、
   `1132 应收利息`、`6111 投资收益` —— 对方全是被投资单位,按规则会误判为 customer。
2. 规则本身就错:`660101 销售费用(fix)` 是损益借方 → 规则给 supplier,
   但用户指出销售费用的对方是**客户**。同为损益借方的 `640202 劳务成本` 却确实是
   supplier。同一分支两个科目,语义相反 —— **方向推不出往来方性质**。
3. `660101` 同时挂 `0004` 与 `0019 供应商档案`,派生出的 supplier 与显式 supplier
   **撞车**(违反 `uq_coa_aux_items_acct_dim`),而「first wins」依赖 SELECT 顺序。

**新设计(用户提出)**:不派生 —— 把 `partner` 做成**真维度**,主数据 = 供应商集 ∪ 客户集。

> `AUX_ITEM_MAP["0004"] = "partner"`,直接映射。
> **`derive_party_dim` / `PARTY_EXCEPTIONS` / `__party__` 哨兵全部删除。**

这样:NC 说是客商就是客商,零推断;例外名单不再需要(投资类/实收资本照常显示 partner,
展开后就是真实往来方);撞车消失(`0004→partner` 与 `0019→supplier` 是不同维度,
660101 两个都保留、各自展开)。

**可行性已实测**:`nc_sync._resolve_dims` **早已把两者归一**——
`hit = uni_sup.get(sup) or uni_cust.get(cust)` → 单个 `partner_id` 列,
且把 `partner_name` 反规范化写在 `journal_voucher_lines` 行上。客商库在数据里本已存在,
只是没有任何东西把它暴露出来。

| 实测(dev 库) | 值 |
|---|---|
| `erp_suppliers` / `nc_customers` | 1148 / 81 |
| 两表 id 重叠 | **0**(union 安全) |
| `journal_voucher_lines` 有 `partner_id` 的行 | 56,291 |
| `partner_id` 能解析为 supplier / customer | 990 / 31 |
| **两边都查不到(孤儿)** | **627** |

孤儿是主数据行已不存在的往来方(`Alloc Vendor` 730 行、`Assign Vendor` 578 行、
`SPS Commerce`…)。**这是既有问题**:今天的 `supplier`/`customer` 维度对它们同样显示空。
但因 `partner_name` 就在行上,partner 维度可回退取用 —— 反而比现有维度更完整。

### 3.4.2 `account_balance` 新增 `partner` 维度

`_dimensions()` 增加一项:`partner` → `JournalVoucherLine.partner_id`,
主数据解析 = `ErpSupplier ∪ NcCustomer`(按 id;实测零重叠)。
`expand_by_dims` 的查找需合并两张表的结果;**主数据查不到时回退到该行的
`partner_name`**(`code=None`,`name=partner_name`),覆盖上述 627 个孤儿。

`DIM_LABELS["partner"] = "Partner (Vendor/Customer)"` 已在 §3.5 中。

**这是本设计范围的一次有意扩张**:它替换掉的是一套已被实测推翻两次的推断规则,
且使 41 个挂客商的科目真正可展开 —— 正是本设计的初衷(凭证导入后按辅助核算展开)。

### 3.5 `DIM_LABELS` 需补齐

`account_balance.DIM_LABELS` 现有 7 项(cost_center/department/income_expense_item/
supplier/customer/employee/project)。§3.4 引入的以下 code 需补充英文标签,
否则界面显示裸 code:`item`、`item_category`、`project_type`、
`government_grant_project`、`asset_category`、`tax_code`、`bank_category`、`bank`、
`bank_account`、`country_region`、`credit_card`、`partner`、`sales_type`。

> **`list_dims` 是并集,不是交集**(2026-07-15 实测,[account_balance.py:196](../../../finance-api/app/crud/account_balance.py#L196)
> `codes += [c for c in reg if c not in codes]`):`_dimensions()` 的 5 个受支持维度
> 对每个科目**永远可选**,与 `coa_aux_items` 内容无关。故 `coa_aux_items` 影响的是
> **展示顺序(seq)与 NC 配置的呈现**,而非展开能力本身。本设计不改变这一行为。

## 4. 数据模型(finance alembic 0023)

`down_revision = "0022_ap_vendor_invno_unique"`(2026-07-15 实测链尾,勿按文件名猜)。

**4.1 新表 `coa_sync_runs`** —— 轻量审计

| 字段 | 说明 |
|---|---|
| id (uuid pk) / created_at / updated_at | |
| started_by / started_at / finished_at | 操作人与起止时间 |
| accounts_inserted / accounts_updated / accounts_deactivated | COA 写入计数 |
| aux_items_inserted / aux_items_deleted | 辅助核算写入计数 |
| error | 失败原因(text,可空) |

刻意无 `status`/watermark/进度计数:同步执行,行是执行完才写的。

**4.2 `coa_aux_items` 加列 `required`** —— `Boolean NOT NULL DEFAULT false`,
由同步从 `BD_ACCASS.ISEMPTY` 填充。

## 5. 同步语义

**COA(`chart_of_accounts`)= upsert + 停用**,永不 DELETE:
保住历史 `posting_lines` / `account_mappings` 引用与 UniOps 侧元数据。
`is_active` 由存在性推断:NC 返回 → `true`(含重新激活);NC 未返回 → `false`。
NC 侧读 `enablestate = 2`(仅启用),故 NC 停用的科目自然落入停用分支。

**辅助核算(`coa_aux_items`)= 全量替换**(delete + reload):
这是一张纯 NC 投影表、无 UniOps 侧数据、无外键引用它,现有 `aux_items_import.py`
本就是 `--load` 清表重灌。**与 COA 的 upsert 语义不同是有意为之**:
COA 混合所有权且被历史数据引用,辅助核算不是。

**更新时的字段纪律**:`SET` 子句只允许出现 §3.1 的列 + `updated_at`,
永不触碰 §3.2 的 UniOps 字段。新增时 §3.2 取默认值
(`aux_dimensions='[]'::jsonb`、`is_active=true`,其余 NULL)。
**禁止清表重灌 `chart_of_accounts`** —— `coa_import.py --confirm-clear` 那种
`delete from chart_of_accounts` 会抹掉 `subtype`/`aux_dimensions` 等元数据,
接口永不提供该语义。

## 6. 后端服务:`app/services/nc_coa_sync.py`

移植 `coa_import.py` + `aux_items_import.py`,改动:NC 连接改读 `settings`;
去掉写死的 `DEV_DSN` 与生产护栏(护栏是脚本才需要的,接口本就跑在目标库里);
**推断改为读事实**(§3.1)。

- `fetch_coa_from_nc() -> NcCoaExtract` —— 一次读全:`BD_ACCOUNT` join `BD_ACCASOA`
  (`pk_accchart` + `enablestate=2`)、`BD_ACCTYPE`、`BD_MEASDOC`、`BD_CURRTYPE`、
  `BD_ACCASS` join 链、`BD_ACCASSITEM`。
- `map_account(row) -> dict` —— **纯函数**,§3.1 的逐字段映射 + `~` 清洗。
- `diff(nc_rows, db_rows) -> CoaDiff` —— **纯函数、零 I/O**,设计的心脏与测试主战场。
  三个**互斥**的桶 + 一个计数:
  - `to_insert` —— NC 有、DB 无
  - `to_update` —— 两边都有,且「§3.1 任一字段有变化」**或**「需重新激活」;
    每项带 per-field before/after 与 `reactivated: bool`
  - `to_deactivate` —— DB 有(且当前 `is_active=true`)、NC 无
  - `unchanged` —— 仅计数

  **重新激活不是独立的桶**,它是 `to_update` 的一种(`is_active` false→true 亦是字段变化)。
  「既改名又被重新启用」的科目只出现一次,`reactivated=true`。
  故 `accounts_updated` **包含**重新激活的科目,三桶不重不漏。
- `diff_aux(nc_aux, db_aux) -> AuxDiff` —— 纯函数,产出待新增/待删除计数与清单。
- `apply(diff, aux_diff, conn)` —— 唯一写库处。

`nc_configured()` 直接 `from app.services.nc_sync import nc_configured` 复用
(同一套 `NC_*` env,不重复定义、也不为此重构刚上线的 nc_sync)。

## 7. API:`app/api/v1/nc_coa_sync.py`,前缀 `/coa-sync`

| 端点 | 说明 |
|---|---|
| `GET /coa-sync/status` | `{configured, can_sync, last_run}`,供前端决定按钮是否渲染 |
| `POST /coa-sync/preview` | 只读,返回 CoaDiff + AuxDiff。零写入 |
| `POST /coa-sync/apply` | 执行,返回实际计数 |

三者均 `finance.coa.manage` 门禁(经 `app.core.authz.require_permission`,
与 COA 页写操作同一把锁)。阻塞的 oracledb 读一律
`await loop.run_in_executor(None, fetch_coa_from_nc)`,不卡事件循环。

### 7.1 apply 重新读取,不用快照

apply **不接受前端回传的预览快照,服务端也不存快照**:永远不让客户端告诉服务端该写什么。
预览是给人看的建议,apply 自己重新读 NC、重新算 diff、再写。COA 变更频率极低,
两次读之间的漂移可忽略;真漂移了,apply 返回的实际计数与预览对不上,反而把它暴露出来。
同时省掉快照存储与过期语义。

## 8. 安全阀

### 8.1 NC 零行守卫

NC 返回 0 个科目直接拒绝(503)。账簿 pk 配错或 NC 侧异常返回空集时,
按「未返回即停用」的规则会把整个 350 科目全部停用。零行时一行不写。
辅助核算同理:`BD_ACCASS` 返回 0 行时拒绝(否则清表后无数据回填)。

### 8.2 停用引用警告

预览标出「即将停用且被 `account_mappings` 引用」的科目。只警告、不阻断、不自动修。

### 8.3 已知的既有问题(本设计不修,仅使其可见)

`payment_execute._stamp_account_codes` 从 `account_mappings` 取
`line_role → account_code` 盖到 posting line 上,**既不 join COA 也不校验 `is_active`**。
而迁移 0005 种下的占位科目(`1000` Cash / `1010` Bank—CAD Operating / `2000` AP /
`6400` employee_expense)正是 `line_role` 映射的目标,它们并非 NC 科目。

因此:停用占位科目**不会**打断付款入账(执行器照样盖 `2000`),但 posting_lines 会持续
引用非 NC 科目 —— 这是**今天就已存在**的漂移,与本功能无关。COA 同步不制造它,
但会通过 §8.2 的警告让它浮出水面。映射治理属独立任务,不纳入本次范围。

> 依据(2026-07-15 实测):全仓 `services/` + `crud/` 中无任何一处校验 COA 的 `is_active`;
> `GET /coa` 默认按 `is_active` 过滤(可用 `include_inactive` 放开)。
> 即停用只影响列表与选择器的可见性,不影响入账。

## 9. 错误处理

| 情形 | 响应 | 写库 |
|---|---|---|
| 无 `finance.coa.manage` 权限 | 403 | 无 |
| `nc_configured()` 为假 | 503 `"NC connection is not configured"`(与 voucher trigger 同句) | 无 |
| NC 连不上 / oracledb 报错 | 503 带原始错误信息 | 无 |
| NC 返回 0 科目 或 0 辅助核算行 | 503 | 无 |

apply 的写入是**单事务、全有或全无**(COA upsert + 辅助核算清表重灌同事务)。

**审计行必须在主事务之外单独提交**:若与写入同事务,失败回滚会把失败记录一起滚掉 ——
审计恰好在最需要它的时刻消失。故顺序为:主事务写(成功提交/失败回滚)→
**另起事务**插 `coa_sync_runs` 行,失败时 `error` 记录原因。

## 10. 前端

`finance/src/pages/finance/CoaSyncModal.tsx` + Chart of Accounts 页 `headerActions` 按钮。
门禁条件与 JV 页一致:`ncStatus?.configured && ncStatus?.can_sync`
(缺配置 = 功能隐藏,与 voucher 同一约定)。

弹窗两态:预览态(COA 的 +N / ~M / −K 三个计数、辅助核算的 +N / −K、
将被停用的科目清单、§8.2 引用警告)→ 确认 → 结果态(实际计数)。
**更新清单需展示 per-field before/after**,让 admin 看见「18 个科目方向将从 debit 改为 credit」
这类改动再确认。文案全英文(遵循 UI 文案约定)。

`CoaConfigPage` 的辅助核算编辑器降为只读展示(真相源已迁至 `coa_aux_items`)。

## 11. 测试

**第一层:纯函数单测**(零 I/O,可穷举)

`map_account`:
- `balanorient=1` → `normal_balance='credit'`,`=0` → `'debit'`
- `acctype='6'` + `balanorient=1` → `revenue`;`acctype='6'` + `balanorient=0` → `expense`
- `acctype='5'` → `expense`;`'1'/'2'/'4'` → `asset/liability/equity`
- `unit='~'` → `quantity_accounting=False`, `default_uom=None`
- `unit=<真实 pk>` → `quantity_accounting=True`, `default_uom=<BD_MEASDOC.code>`
- `currency='~'` → `default_currency=None`
- name 回退链:ACCASOA.name2 → ACCASOA.name → BD_ACCOUNT.name2 → name → code,`~` 视同空
- `pid='~'` → `parent_code=None`

`diff`:
- NC 有 / DB 无 → `to_insert`
- 两边都有、任一 §3.1 字段变化 → `to_update` 且带 before/after
- DB 有 / NC 无 → `to_deactivate`
- DB 中 `is_active=false` 且 NC 返回 → 落入 `to_update` 且 `reactivated=true`
- DB 中 `is_active=false`、NC 返回、且同时改了 `name` → **仍只出现一次**(验证三桶互斥)
- 完全一致 → `unchanged`,不产生任何操作
- **字段所有权专项**:更新一个带 `aux_dimensions`/`subtype`/`effective_from` 的科目,
  断言同步后三者原封不动。这是全设计最易被未来改动破坏之处,必须用测试钉死。

`diff_aux`:新增/删除/无变化;`isempty='N'` → `required=True`,`='Y'` → `False`

`map_aux_item`(§3.4,按 NC item code):
- `ra01`/`0001`/`0008`/`0019`/`0017` → `cost_center`/`department`/`income_expense_item`/
  `supplier`/`customer`
- `0004`(客商)→ `partner`,**直接映射,无派生**(§3.4.1)
- **回归专项**:`D45`(项目类型)→ `project_type`、`CRM02`(政府拨款项目)→
  `government_grant_project`,**断言二者都不是 `project`** —— 钉死旧 `NAME_MAP`
  子串匹配的假阳性
- 未登记的 item code → 抛错,**断言不回退为 slug**

`partner` 维度解析(§3.4.2,`account_balance`):
- `partner_id` 命中 `ErpSupplier` → 取 `erp_supplier_code` / `supplier_name`
- `partner_id` 命中 `NcCustomer` → 取 `code` / `name`
- **两边都查不到** → `code=None`、`name=` 该行的 `partner_name`
  (覆盖实测 627 个孤儿,如 `Alloc Vendor`;**断言不是空**)
- 同一次展开里 supplier 与 customer 混合出现 → 各自解析正确(union 不串)

`map_account` 的显式失败(§3.1.1),每条一个用例:
- `acctype='3'`(或任何未登记编码)→ 抛错,**断言不会静默返回 `asset`**
- `balanorient=2` → 抛错
- `BD_ACCASOA` 行缺失 → 抛错,**断言不回退为 pid 推断**
- `unit` 指向 `BD_MEASDOC` 查无此 pk → 抛错,**断言不把 UUID 写进 `default_uom`**

**第二层:API 测试**(照 `tests/test_nc_sync.py` 现成的 `_fetch` monkeypatch 缝)

- 403(无 `finance.coa.manage`)、503(未配置)、503(NC 零科目)、503(NC 零辅助核算行)
- **断言 preview 之后库中一行未变**(不能只看它返回了 diff 就算通过)
- **幂等**:连跑两次 apply,第二次应全为 no-op。此条同时抓 upsert 写错
  与 `is_active` 推断写反两类 bug
- **回归专项**:构造一个 `balanorient=1` 的 `1xxx` 科目(模拟累计折旧),
  断言 apply 后 `normal_balance='credit'` —— 钉死 §2.3 的 18 个修复

跑测试须将 `POSTGRES_*` 指向本地 docker 库,勿打生产
(既有约定:宿主 finance-api/.env 指向生产库)。

## 12. 明确不做(YAGNI)

- 不收编 `customers_import.py`(留扩展口,不预先抽象)
- 不泛化 `nc_sync_runs`
- 不修 `account_mappings` 占位科目漂移(仅警告,§8.3)
- 不退役 `aux_dimensions` JSONB 列(仅停止人工维护 + 编辑器只读;退役是独立清理任务)
- 不提供清表重灌 `chart_of_accounts` 的语义
- 不做定时/自动同步(手工触发,与 voucher 一致)
- 不使用 `BD_ACCOUNT.QUANTITY` 列(语义不明,见 §2.4)
- `scripts/nc_migration/coa_import.py` 与 `aux_items_import.py` **保留不删**
  (与 `voucher_import.py` 被移植后的处理一致)

## 13. 部署前提

与 voucher 同步同一前提:app server(10.10.50.65)到 NC(10.10.95.67:1521)网络可达,
且 app server `.env` 含 `NC65_*` 五项、`docker-compose.prod.yml` 的 finance-api
注入 `NC_*`(2026-07-15 已修,main 4854f6d)。

上线后首次 apply 将修正 §2.3 的存量缺陷(18 方向 + 29 数量核算 + 补齐币种/计量单位/
辅助核算 seq 与 required)。**建议先在 dev 跑一次 preview,把改动清单交财务复核后
再在生产执行。**

## 14. 凭证同步(`nc_sync.py`)的四处修复(2026-07-17 新增范围)

COA 复核暴露的是一类模式(照抄老脚本注释、猜而不读)。凭证同步是同一批脚本移植的,
故按同一方法实连 NC 审计了一遍。**结论:账簿口径本来就是对的** ——
`PK_BOOK = 1001A1100000003CGCBX` 下 315,539 条 GL_DETAIL **全部属于 CRM0001**
(这正是证明 COA 该用 CRM0001 的那条证据),`TEMPSAVEFLAG` 全 `N`(无草稿导入),
`CC_BY_CODE` 21 个码 NC 实际用到的一个不缺,`resolve_aux_type_pks` 甚至已经在做
「常量对不上就抛错」—— 比 COA 导入器做得好。

但有四处实测缺陷,用户已逐条拍板(2026-07-17)。

### 14.1 作废凭证被当作已过账导入

`fetch_from_nc` 拉 `GL_VOUCHER` **不过滤任何状态**,而 `DISCARDFLAG='Y'` 实测有 1 张:
2021 年 10 期 57 号「Adjusting/Everest Auto.」,**$4,298.52**,2 行。作废凭证不该在账上。

> **修**:voucher 查询加 `and (discardflag is null or discardflag <> 'Y')`。
> 实测只影响这 1 张(39,978 → 39,977)。

### 14.2 956 行凭证丢失成本中心 —— 缺 4 个部门码

`unmapped_cc_count` 一直记录着 956(设计是对的,有可见性),但无人追查。精确定位:

| NC 部门码 | 名称 | 丢失行数 | → EPMS 成本中心 |
|---|---|---|---|
| `0102` | Purchasing(NOT USE) | 404 | `GA-0107` |
| `0106` | Engineering | 274 | `MOH-0106-E01` |
| `0104` | Production | 163 | `MOH-0104-P01` |
| `0108` | Project | 115 | `RD-0109` |

`404+274+163+115 = 956`,与 `unmapped_cc_count` 分毫不差。

`CC_BY_CODE` 已有 `E01-E07/ENG → MOH-0106-E01`、`P01-P03/PD → MOH-0104-*`,
但 `CC_BY_DEPT` 偏偏缺 `0106`/`0104` —— 只走部门、无成本中心码的行因此白丢,属明显遗漏。
`0102`(名字写着 NOT USE)与 `0108` 在 EPMS 无对应物,**目标由用户指定,不是推断**。

> **修**:`CC_BY_DEPT` 补这 4 个。**注释必须写明 `0102`/`0108` 是 2026-07-17 的人工决策** ——
> 本设计的原则是「代码不许猜」,不是「不许映射」;人拍板与代码推断是两回事。

### 14.3 币种静默默认成 CAD

`extract.ccy.get(curr, "CAD")` —— 查不到的币种**默认 CAD**。与 `coa_import.py` 的
`return "asset"` 同一类病。今天不触发(实测 CAD/USD/CNY/EUR/GBP 五种全部解析),
但 **USD 27,624 行 / CNY 3,002 行**,多币种是真实的:NC 一旦新增币种,那些行会悄悄变 CAD。

> **修**:查不到即抛错(与 §3.1.1 同一原则)。

### 14.4 272 张未记账凭证被标成 posted($173 万,含未来期间)

NC 的 `TALLYDATE` 空 = **尚未记账**。`v_rows` 里 status 硬编码 `"posted"`,故:

| 期间 | 张数 | 内容 |
|---|---|---|
| 2026-07 | 261 | 当期在途,尚未记账 |
| **2026-08 ~ 12** | **10** | **未来期间**的预录折旧调整 |
| 2021-10 | 1 | 即 §14.1 那张作废凭证 |

合计借方 **$1,731,683.19**。已验证 `account_balance.py` 与 `gl.py` 均过滤
`status == POSTED`,故这 $173 万**正在进报表**,其中还有尚未到来的期间。

> **修(用户定:照导,状态分开)**:`status = posted if tallydate else draft`。
> 报表天然把 draft 排除 —— 正是所需口径。

#### 14.4.1 状态回填是必需的,否则制造一批永久 draft

**增量同步永不更新已导入的凭证**:`skip_pks` 取库里全部 `nc_source_pk` 直接跳过,
且水位走 `creationtime` —— 一张 6 月创建、7 月记账的凭证,creationtime 仍是 6 月,
**永远不会再进增量窗口**。若只改 status 而不回填,那 261 张当期凭证会永久停在 draft。

> **修**:`fetch_from_nc` 额外拉一次 **全量 `(pk_voucher, tallydate)`**(仅两列 × 4 万行,
> 远比 details 便宜,且**不受水位限制**);`_run_worker` 在插入后,把库中所有
> `nc_source_pk` 非空凭证的 status **双向对齐** NC 的 tally 事实。
> 水位继续管新凭证(贵的那部分),状态回填走这条廉价通道。

#### 14.4.2 NC 来源的 JV 禁止人工改状态

`review()`/`post()` 现无 `nc_source_pk` 守卫。今天无所谓(NC 凭证全 posted,无可点),
但一旦 272 张变 draft,用户手工 post → 下次同步按 NC 口径 revert → **静默打架**。

> **修(用户定)**:`review()`/`post()` 对 `nc_source_pk` 非空的凭证直接拒绝,
> 提示该凭证镜像 NC 记账状态。与「NC 是事实源」一贯。

### 14.5 存量纠正 = 发布后跑一次全量重灌(用户定)

**四处修复只对新导入生效**。库中 39,901 张是旧逻辑的产物:1 张作废凭证在账、
956 行空成本中心、272 张错标 posted。`NC Sync` 已有 full 模式(需敲 `FULL RELOAD` 确认),
删光 nc 来源凭证重导即可一次到位,无需额外脚本。**dev 先跑验证**。

### 14.6 顺带证实:停用不删除是对的(硬数字)

即将被 COA 同步停用的 10 个科目中,有 7 个在凭证上挂着 **430 行、约 $350 万**:

    22250301 PST paid      221 行  $1,352,408.58
    640106  存货冲销        52 行  $1,098,409.82
    660305  NR Tax          10 行    $574,244.28
    660306  Property Tax    20 行    $516,400.52

实测**没有任何报表按 `is_active` 过滤**(`account_balance.py`/`gl.py` 零引用),
停用后这 430 行照常 join、金额完好。**若当初选了硬删,这 $350 万就是孤儿行。**

