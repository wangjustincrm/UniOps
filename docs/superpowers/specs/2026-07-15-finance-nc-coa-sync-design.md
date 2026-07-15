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
- **权限 = 仅 `system_admin`**(JWT role),预览与应用同门禁。
  COA 页现有 `_require_manage` 不变、不受影响。
- **架构 = 同步执行**:无后台 worker、无进度表、无轮询。
- **辅助核算真相源 = `coa_aux_items`**:给它加 `required` 列、填 `seq`;
  `aux_dimensions` JSONB 不再人工维护,COA 页编辑器降为只读,列留在原地待后续清理。
- **存量修复 = 靠同步自然修复**:upsert 覆盖 NC 事实即纠正,不另写修复脚本。

### 1.3 为什么不照搬 voucher 的形状

`nc_sync_runs` + 后台线程 + 30 分钟 stale 清扫 + 前端轮询,全部因凭证量大、跑得久而存在。
COA 仅 360 行、辅助核算 205 行,读取约 1-2 秒。且「预览 → 确认」本就需要两次调用,
同步返回比轮询自然。同理**不**把 `nc_sync_runs` 泛化成 `kind` + JSONB 计数:
那要动刚在生产验证通过的 voucher 同步,为尚不存在的需求承担回归风险。

## 2. NC 复核结论(2026-07-15 实测)

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

### 2.3 存量缺陷实测(本地 dev 库,360 科目)

| 缺陷 | 科目数 | 根因 |
|---|---|---|
| `normal_balance` 记反 | **18** | 按编码前缀猜,未读 `BD_ACCOUNT.BALANORIENT`(NC 360/360 有值) |
| `quantity_accounting` 错 | **29** | 读了 `QUANTITY` 列,而真正判据是 `UNIT` 是否有真实值 |
| `default_currency` 全空 | 181 可填 | 未读 `CURRENCY`(需 join `BD_CURRTYPE` 取 code) |
| `default_uom` 全空 | 30 可填 | 未读 `UNIT`(需 join `BD_MEASDOC` 取 code) |
| `coa_aux_items.seq` 恒为 0 | 205 行 | 未读 `BD_ACCASS.ID`(真值 1-7) |
| `aux_dimensions.required` 从未填充 | 205 行 | 未读 `BD_ACCASS.ISEMPTY`(187 必填 / 18 可空) |

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
| `name` | `BD_ACCASOA.NAME2` → `.NAME` → `BD_ACCOUNT.NAME2` → `.NAME` → `code` | `~` 清洗后回退链(ACCASOA 覆盖 348/360,BD_ACCOUNT.NAME2 仅 184) |
| `account_type` | `BD_ACCTYPE.CODE` + `BD_ACCOUNT.BALANORIENT` | 1=asset,2=liability,4=equity,5=expense(成本),**6(损益)按 balanorient 拆:1(贷)=revenue、0(借)=expense** |
| `normal_balance` | `BD_ACCOUNT.BALANORIENT` | 0=debit,1=credit(**事实,非推断**) |
| `is_postable` | `BD_ACCASOA.ENDFLAG` | ='Y'(实测与 pid 推断 100% 一致:54 父 / 306 末级) |
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
  实测本账簿 `BD_ACCOUNT`(enablestate=2)与 `BD_ACCASOA` 均为 360 行、严格一一对应,
  故缺失即属异常,不得降级为「按 pid 推断叶子」之类的兜底。
- `BD_MEASDOC` / `BD_CURRTYPE` 查不到对应 pk → 抛错(而非把 UUID 原样写进 code 列)。

失败即整次同步中止、一行不写(§9 单事务)。**宁可拒绝同步,也不写入猜测值** ——
本设计的全部价值在于用事实替换推断,兜底默认值会把推断从前门赶出去、又从后门放进来。

### 3.2 明确不承接

| UniOps 字段 | 原因(实测) |
|---|---|
| `mnemonic` | `REMCODE` 在 NC **全空**(BD_ACCOUNT 0/360,BD_ACCASOA 1/360) |
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
| `0004` | 客商 | 按 §3.4.1 派生 | — | ✓ / ✗ |
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

#### 3.4.1 `0004 客商` 的派生规则

客商在 NC 中同时涵盖供应商与客户,而我们分 `supplier`/`customer` 两个维度
(二者共用 `JournalVoucherLine.partner_id`,由 `account_balance._dimensions()` 分别
解析到 `ErpSupplier` / `NcCustomer`)。实测 23 个科目挂载客商,分布:
资产 4 / 负债 4 / 权益 1 / 损益 14 —— **仅靠资产·负债两分支无法覆盖**。

派生规则(输入为已映射的 `account_type` 与 `account_code`):

```
EXCEPTIONS = {"4001", "1511", "1512"}   # 股东 / 被投资单位,既非供应商亦非客户
if account_code in EXCEPTIONS:  -> "partner"     # 不展开
elif account_type == "asset":   -> "customer"    # 应收款,对方欠我们
elif account_type == "liability": -> "supplier"  # 应付款,我们欠对方
elif account_type == "expense": -> "supplier"    # 含成本(5)与损益借方
elif account_type == "revenue": -> "customer"    # 损益贷方
else:                           -> "partner"     # 权益及任何未预见情形,不展开
```

**例外名单的依据**(用户决策:宁可不展开,也不展错):
- `4001 实收资本`(权益)—— 对方是股东
- `1511 长期股权投资` / `1512 长期股权投资减值准备`(资产)—— 对方是被投资单位,
  按资产分支会误判为 customer

`partner` 复用 `aux_dimension_types` 中已有的 code(Partner (Vendor/Customer)),
它不在 `_dimensions()` 中,故自然呈现为 `supported:false` —— 即「不展开」。

**冲突已排除**(2026-07-15 实测):无任何科目同时挂载 `0004` 与 `0019`/`0017`,
故派生出的 `supplier`/`customer` 不会与显式配置的同名维度撞车。

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

三者均 `system_admin` 门禁。阻塞的 oracledb 读一律
`await loop.run_in_executor(None, fetch_coa_from_nc)`,不卡事件循环。

### 7.1 apply 重新读取,不用快照

apply **不接受前端回传的预览快照,服务端也不存快照**:永远不让客户端告诉服务端该写什么。
预览是给人看的建议,apply 自己重新读 NC、重新算 diff、再写。COA 变更频率极低,
两次读之间的漂移可忽略;真漂移了,apply 返回的实际计数与预览对不上,反而把它暴露出来。
同时省掉快照存储与过期语义。

## 8. 安全阀

### 8.1 NC 零行守卫

NC 返回 0 个科目直接拒绝(503)。账簿 pk 配错或 NC 侧异常返回空集时,
按「未返回即停用」的规则会把整个 360 科目全部停用。零行时一行不写。
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
| 非 `system_admin` | 403 | 无 |
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
- **回归专项**:`D45`(项目类型)→ `project_type`、`CRM02`(政府拨款项目)→
  `government_grant_project`,**断言二者都不是 `project`** —— 钉死旧 `NAME_MAP`
  子串匹配的假阳性
- 未登记的 item code → 抛错,**断言不回退为 slug**

`derive_party_dim`(§3.4.1),每个分支一个用例:
- `("112201", "asset")` → `customer`;`("220202", "liability")` → `supplier`
- `("640202", "expense")` → `supplier`;`("6002", "revenue")` → `customer`
- `("4001", "equity")` → `partner`(权益兜底)
- **例外名单**:`("1511", "asset")` → `partner`,**断言不是 `customer`**;
  `("1512", "asset")` → `partner`

`map_account` 的显式失败(§3.1.1),每条一个用例:
- `acctype='3'`(或任何未登记编码)→ 抛错,**断言不会静默返回 `asset`**
- `balanorient=2` → 抛错
- `BD_ACCASOA` 行缺失 → 抛错,**断言不回退为 pid 推断**
- `unit` 指向 `BD_MEASDOC` 查无此 pk → 抛错,**断言不把 UUID 写进 `default_uom`**

**第二层:API 测试**(照 `tests/test_nc_sync.py` 现成的 `_fetch` monkeypatch 缝)

- 403(非 system_admin)、503(未配置)、503(NC 零科目)、503(NC 零辅助核算行)
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
