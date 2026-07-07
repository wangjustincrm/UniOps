# NC65 → UniOps Finance 迁移映射设计

> 状态:设计稿(brainstorm 产出,待用户评审)。日期:2026-07-04。
> 关联:[[project_uniops_finance_jv_nc65]]、JV 总账凭证子系统设计(未落稿,本文件为其前置)、
> `2026-06-20-finance-ap-invoice-*`(AP 模块)。
> 内省脚本:`c:\Project\nc65_introspect\`(conn/discover/core/find_coa/books/target_book/aux)。

## 1. 背景与目标

公司现用**用友 NC65**(Oracle 12c,飞鹤集团共享库)记账,财务是其核心。UniOps 要重建 Finance
模块并**一次性迁移、切换后退役 NC65**。迁移颗粒度 = **全部历史凭证,多年明细全导**。

「兼容」的真正含义:**新模型必须能无损表达 NC65 映射后的历史数据**(科目/凭证/余额/往来/辅助核算),
而非复制 NC 的中式处理逻辑。新模型按北美(加拿大)运营设计,NC65 仅作数据源,中间建 **NC65→新模型
导入映射层**。

**对接模型是双向 + 分期的**(非纯单向切换):
1. **一次性历史导入**(NC65 → UniOps):全历史凭证/余额/档案导入,见 §3–§8。
2. **并行期同步**(UniOps → NC65):切换后有一段**双系统并行期**,UniOps 成为 AP 等业务的操作 SOR,
   飞鹤集团合并/法定账仍在 NC。UniOps **导出 AP 业务单据(非 JV)** 灌入 NC 应付模块,NC 审核后**自己
   推式生成 JV**;两边 JV **定期复核**匹配(见 §9)。并行期结束 NC 退役,本流关闭。
> 「沿用 NC 中式科目 + 保留 NC 辅助核算/档案编码」除历史忠实外,也是**并行期同步的前提**:AP 单据导出按
> NC 档案 code 反查(`nc_id_map` 双向可查),且两边 JV 用同套科目才便于复核匹配。

## 2. 范围(已定决策)

| 决策项 | 结论 |
|---|---|
| 迁移源 | schema **`NCSC`**(活库,2019–2026);日期后缀 schema(0826/0903/1109)是过时备份,忽略 |
| 目标账簿 | **仅 `[01010104-0003] 加拿大皇家妙克无限责任公司`**(pk_book=`1001A1100000003CGCBX`,pk_accchart=`1001A1100000003CGN3F`)。其余 16 个飞鹤中国 RMB 主体不迁 |
| 历史颗粒度 | 全历史凭证(2020–2026,~39,738 凭证 / ~314,688 分录) |
| 科目表 | **沿用 NC 中式科目**(历史零重映射);北美报表需求走税码维度 + 未来可叠报表科目映射视图(见 §9) |
| 主数据 | **NC 为准**,导入 UniOps 成权威主数据,与现有少量数据去重合并 |
| 本位币 | 该账簿本位币 = **CAD**;存在 USD/CNY/EUR/GBP 外币交易 → 需原币+本币双额+汇率 |
| 对接模型 | **双向 + 分期**:①一次性 NC→UniOps 历史导入;②并行期 UniOps→NC 同步(见 §9)。并行期结束 NC 退役 |
| 并行期同步方式 | **导 AP 单据(非 JV)**:UniOps 生成 NC 应付模块引入文件(按用户提供的 NC 导入模板)→ 财务引入 → NC 审核后**自己生成 JV**;两边 JV **定期复核**。不写飞鹤生产库,可插拔后续切接口/EAI |
| 并行期同步范围 | **分阶段**:Phase 1 = AP 发起的应付单据;后续按需扩其他业务单据 |

## 3. 源系统模型(NC65,实测)

### 3.1 凭证 `GL_VOUCHER`(头,该账簿 ~39.7k)
- 主键 `PK_VOUCHER`(CHAR20);账簿 `PK_ACCOUNTINGBOOK`;凭证类别 `PK_VOUCHERTYPE`(该库**只有「记」记账凭证**)
- 会计年度 `YEAR`(CHAR4)+ 期间 `PERIOD`(CHAR2)+ 凭证号 `NUM`
- 摘要 `EXPLANATION`;来源系统 `PK_SYSTEM`、来源单据 `PK_SOURCEPK`
- 四岗:制单 `PK_PREPARED`、审核 `PK_CHECKED`、出纳 `PK_CASHER`、主管 `PK_MANAGER`
- 日期:`PREPAREDDATE`/`SIGNDATE`/`TALLYDATE`(制单/签字审核/记账,CHAR19)
- 合计:`TOTALDEBIT`/`TOTALCREDIT`(原币)+ `...GLOBAL`(本位币 CAD)+ `...GROUP`(集团币)
- 标志:`TEMPSAVEFLAG`(暂存)/`SIGNFLAG`

### 3.2 分录 `GL_DETAIL`(87 列,该账簿 ~314.7k)
- 主键 `PK_DETAIL`;头 `PK_VOUCHER`;行号 `DETAILINDEX`
- 科目 `ACCOUNTCODE`(VARCHAR40,直接存科目编码);科目 pk `PK_ACCASOA`;科目表 `PK_ACCCHART`
- 金额四套:原币 `DEBITAMOUNT`/`CREDITAMOUNT`;本币 `LOCALDEBITAMOUNT`/`LOCALCREDITAMOUNT`(CAD);
  本位币 `GLOBAL...`;集团 `GROUP...`(**原币与本币普遍不等 = 真外币**)
- 数量核算:`DEBITQUANTITY`/`CREDITQUANTITY` + `PRICE` + `UNITNAME`(存货类普遍启用)
- 币种 `PK_CURRTYPE` + 汇率 `EXCRATE1..4`
- 摘要 `EXPLANATION`;方向 `DIRECTION`
- **辅助核算 `ASSID`**(→ `GL_FREEVALUE.FREEVALUEID`,该账簿分录 **100% 有值**);往来单位 `PK_UNIT`
- 核销 `VERIFYNO`/`VERIFYDATE`;票据 `CHECKNO`/`CHECKDATE`;去规范化冗余 `YEARV`/`PERIODV`

### 3.3 辅助核算(维度)
- `BD_ACCASSITEM`:科目↔辅助核算类型 关联(定义某科目挂哪些辅助核算)
- `GL_FREEVALUE`:辅助核算值组合(`FREEVALUEID` + `TYPEVALUE1..9`,一个组合最多 9 维)
- `GL_DTLFREEVALUE`:每分录最多 `FREEVALUE1..30`(更宽的行级辅助值)
- 值 = 指向档案的 PK(客户/供应商/部门/职员/项目/自定义)

### 3.4 档案主数据(实测均为真实加拿大数据)
| 表 | 量 | 键/名 | 样例 |
|---|---|---|---|
| `BD_CUSTOMER` 客户 | 84 | CODE/NAME | Shoppers Drug Mart Inc. |
| `BD_SUPPLIER` 供应商 | 1,165 | CODE/NAME | H2Flow Equipment Inc |
| `BD_PSNDOC` 职员 | 286 | CODE/NAME | SMITH GRANT |
| `ORG_DEPT` 部门 | 40 | CODE/NAME | Sales / Project(英文) |
| `BD_PROJECT` 项目 | 903 | (defdoc 结构) | |
| `BD_DEFDOCLIST` 自定义档案**类型** | 223 | CODE/NAME | 合同类型 |
| `BD_DEFDOC` 自定义档案**值** | 22,318 | CODE/NAME | |

### 3.5 科目表 `BD_ACCOUNT`
`CODE`(科目编码,中式 1资产/2负债/4权益/5成本)、`NAME`、`INNERCODE`(内码=层级路径)、`PID`(父)、
`PK_ACCCHART`(科目表)、`BALANORIENT`(余额方向)、`QUANTITY`(数量核算标志)、`CURRENCY`、
`ACCLEV`(级次)、`ENABLESTATE`。

### 3.6 币种/期间
`BD_CURRTYPE`(pk_currtype, code CAD/USD/CNY/EUR/GBP, name);
`BD_ACCPERIOD`/`BD_ACCPERIODMONTH`(会计期间方案)。期初余额本身以「期初」凭证存在数据里。

## 4. 目标模型(UniOps Finance,含 NC 逼出的新增能力)

- **`chart_of_accounts`**(现有 `ChartOfAccount`):承载 NC 科目(保留原码 + 内码层级 + 方向 + 数量标志)。
- **`journal_vouchers`**(JV 子系统新建):历史凭证导入为 `status=posted`,`posting_event_id` 为空
  (NC 历史无 UniOps 业务事件 → 直插已过账 JV,不走 emit_event)。
- **`journal_voucher_lines`** —— **相对 JV 原设计新增两组字段**:
  - 双币金额:`orig_debit`/`orig_credit` + `local_debit`/`local_credit`(CAD)+ `currency` + `fx_rate`
  - 数量核算:`quantity` + `unit` + `price`
- **`jv_line_dimensions`**:承载辅助核算(维度 KV)。
- **NC-PK→UUID 映射表** `nc_id_map(archive_type, nc_pk CHAR20, uniops_id UUID, code, name)`:每类档案/科目/凭证
  一份,幂等 + 可追溯(迁移可重跑)。

## 5. 迁移架构

```
NC65 Oracle (只读)
   │  抽取(oracledb thin,按 pk_accountingbook 过滤单账簿)
   ▼
staging(落地/CSV 或临时表)
   │  1) 生成档案「建议映射表」(NC 档案 ⇄ UniOps 现有主数据,按 code/name 自动配对打分)
   │  ★ 人工强制确认关口(见 §6.2)—— 未确认不得进入后续任何步骤
   │  2) 应用已确认映射 → 建 nc_id_map + 建/合并 UniOps 主数据
   │  3) 导 COA(BD_ACCOUNT → chart_of_accounts)
   │  4) 导凭证头(GL_VOUCHER → journal_vouchers, posted)
   │  5) 导分录(GL_DETAIL → journal_voucher_lines,双币+数量)
   │  6) 挂辅助核算(ASSID/GL_FREEVALUE → jv_line_dimensions)
   ▼
UniOps finance DB
   │  校验:①每张凭证 Σ借=Σ贷 ②逐科目/期间余额 vs NC 试算表 ③行数/凭证数对账
```

> 步骤 1→2 之间是**硬阻断的人工确认关口**:凭证导入(4–6)依赖档案 ID 映射,映射未经人工确认,
> 迁移**不得继续**。

幂等:所有加载按 `nc_pk` upsert;可重复执行不产生重复行(与现网 `AP-YYYYMMDD` / posting 幂等一致口径)。

## 6. 字段级映射

### 6.1 科目 `BD_ACCOUNT` → `chart_of_accounts`
| NC | 目标 | 备注 |
|---|---|---|
| CODE | account_code | 保留 NC 中式码 |
| NAME | account_name | |
| INNERCODE / PID / ACCLEV | parent + level | 由内码/父重建层级树 |
| BALANORIENT | normal_side(借/贷) | |
| QUANTITY | quantity_flag | 数量核算科目标志 |
| CURRENCY | restrict_currency | 可空=多币 |
| ENABLESTATE | is_active | |

### 6.2 档案 → UniOps 主数据(NC 为准,导入合并)
| NC 档案 | UniOps 目标 | ID 映射 |
|---|---|---|
| BD_SUPPLIER | mdm 供应商 | nc_id_map('supplier') |
| BD_CUSTOMER | 客户主数据 | nc_id_map('customer') |
| BD_PSNDOC 职员 | 员工/用户 | nc_id_map('employee') |
| ORG_DEPT 部门 | 部门/成本中心 | nc_id_map('dept') |
| BD_PROJECT 项目 | 项目维度 | nc_id_map('project') |
| BD_DEFDOCLIST/BD_DEFDOC 自定义 | 长尾维度类型+值 | nc_id_map('defdoc:<类型>') |
#### 6.2.1 人工强制确认关口(硬性)
档案主数据的合并/导入**绝不自动落库**,必须逐条经人工确认:

1. **生成建议映射表**:迁移工具把每条 NC 档案与 UniOps 现有主数据按 `code`/`name`(可加模糊匹配)
   自动配对,产出候选清单,每条标注建议动作:`match`(命中现有)/`new`(新建)/`conflict`(多个候选或
   code 撞名不同实体),并给出匹配依据与置信度。
2. **人工逐条确认/改判**:审核人在确认界面对每条(至少所有 `conflict` 与低置信 `match`)**显式确认或
   覆盖**目标;可手动指定合并目标、拆分、或标记为新建。**未确认项一律阻断**,不得默认「以 NC 为准」
   静默覆盖。
3. **确认后一次性应用**:仅将**已确认**的映射写入 `nc_id_map` 并建/合并主数据;全过程留操作审计
   (谁、何时、把哪条 NC 档案映射到哪个 UniOps 主数据、依据)。
4. 供应商/客户(直接影响历史 AP/AR 往来)确认要求最严;职员/部门/项目/自定义档案可批量确认但仍需人工
   放行。

> 设计意图:主数据错配会污染整段历史往来与凭证,故「NC 为准」是**确认时的默认倾向**,不是自动执行动作。
> 关口本身沿用 UniOps 现有交互(参照 Data Maintenance admin 风格),不新造独立系统。

### 6.3 辅助核算 → 维度
- 用 `BD_ACCASSITEM` 解析每科目的辅助核算类型序列 → 确定 `GL_FREEVALUE.TYPEVALUE{n}` 每槽位对应的档案类型。
- 每分录:`GL_DETAIL.ASSID` → `GL_FREEVALUE` 取各槽位 PK → 经 nc_id_map 翻成 UniOps 维度值 →
  高频类型(供应商/客户/部门/职员/项目)落 `journal_voucher_lines` 维度列;其余落 `jv_line_dimensions`。
- ⚠️ 构建期需实测确认槽位↔类型的精确对应(见 §10)。

### 6.4 凭证头 `GL_VOUCHER` → `journal_vouchers`
| NC | 目标 |
|---|---|
| PK_VOUCHER | nc_id_map('voucher') → id;原 PK 存 source_ref |
| YEAR+PERIOD | fiscal_period(YYYY-MM) |
| NUM + PK_VOUCHERTYPE | jv_number(保留 NC「记-YYYY-NUM」原号)+ voucher_word=记 |
| EXPLANATION | summary |
| PK_PREPARED/CHECKED/MANAGER + 日期 | prepared_by/reviewed_by/posted_by(经 nc_id_map)+ 时间 |
| TOTALDEBIT/CREDIT + GLOBAL | total_debit/credit(原币)+ 本位币合计 |
| status | 恒 `posted`(历史已记账);`posting_event_id` = NULL |
| PK_ACCOUNTINGBOOK | entity_id |

### 6.5 分录 `GL_DETAIL` → `journal_voucher_lines` (+ `jv_line_dimensions`)
| NC | 目标 |
|---|---|
| DETAILINDEX | line_no |
| ACCOUNTCODE | account_code(经 COA 校验存在) |
| DEBITAMOUNT/CREDITAMOUNT | orig_debit/orig_credit |
| LOCALDEBITAMOUNT/LOCALCREDITAMOUNT | local_debit/local_credit(CAD) |
| PK_CURRTYPE + EXCRATE1 | currency + fx_rate |
| DEBITQUANTITY/CREDITQUANTITY + PRICE + UNITNAME | quantity + price + unit |
| EXPLANATION | summary |
| ASSID(辅助核算) | → 维度列 + jv_line_dimensions |
| PK_UNIT(往来) | partner_id(经 nc_id_map) |
| VERIFYNO | 核销号(留档,可选) |

### 6.6 币种与汇率
`BD_CURRTYPE` → 币种主数据;每分录保留原币+本币双额,`fx_rate` 取 `EXCRATE1`。本位币统一 CAD。

### 6.7 期间与期初
`BD_ACCPERIOD*` → UniOps 会计期间;**期初余额照搬其「期初」凭证,不重算**;导入年度**不重跑 close-year**
(NC 的结转损益凭证已在数据里)。

## 7. ID 策略与幂等
- NC 主键为 CHAR20(部分 UUID),UniOps 用 UUID。统一经 `nc_id_map` 转换,保留 nc_pk 供追溯/重跑。
- 所有加载 upsert-by-nc_pk;迁移脚本可重复执行;每步产出计数与差异日志。

## 8. 对账与校验(硬门槛)
1. 每张 JV `Σ借=Σ贷`(原币与本币各自平)。
2. **逐科目 × 期间余额** 与 NC `GL_BALANCE` / 试算表逐条核对,差异清单必须为空或已解释。
3. 凭证数 / 分录数 / 借贷合计 三层总量对账。
4. 抽样人工核对若干张真实凭证(含外币、数量核算、辅助核算)。

## 9. 反向流:UniOps → NC 并行期同步(导出 AP 单据 + JV 复核)

切换后有**双系统并行期**:UniOps 是 AP 等业务的操作 SOR,飞鹤集团合并/法定账仍在 NC。并行期结束、
NC 退役后本流关闭。**同步模型(用户定稿,2026-07-07):不推 JV,而是导 AP 单据 + 定期复核 JV。**

### 9.1 核心机制:导 AP 单据,让 NC 自己生成凭证
- **UniOps 导出的是 AP 业务单据(AP Record,应付单/发票),不是凭证 JV。**
- 灌进 **NC 应付(ARAP)模块**;NC 按其**推式生成**机制:**AP 单据审核后自动生成 JV**(NC 自己的制单
  规则,凭证 `PK_SYSTEM='AP'`)。
- UniOps 与 NC **各自独立生成 JV**;通过**定期复核**核对两边 JV 是否匹配(控制点),而非把 JV 推给 NC。
- **好处**:UniOps 无需复制 NC 的「AP→科目/税/凭证」制单逻辑,NC 用它自己的规则生成,最稳、最少维护。
  实测支撑:该账簿最大凭证来源就是 `PK_SYSTEM='AP'`(19,843 张),NC 应付模块本就「应付单→审核→制单」。

### 9.2 机制(分期可插拔)
- **Phase 1(起步)**:UniOps 生成 **NC 应付单据引入文件(Excel/txt,按用户提供的 NC 应付模块导入
  模板)**,财务在 NC AP 模块引入 → 审核 → 自动生成 JV。不写飞鹤生产库,最安全合规。
- **适配器化**:导出目标可插拔(`file` / `arap_interface` / `eai`),后续飞鹤授权后可切接口/EAI 不动上层。

### 9.3 范围(分阶段,用户定)
Phase 1 = **AP 发起的应付单据**;导出器按单据来源可配置,后续按需扩其他业务单据。

### 9.4 内容映射(UniOps AP Record → NC 应付单引入)
业务级字段(比凭证少、更稳;科目/税/凭证由 NC 制单规则生成,UniOps **不填**):
| UniOps AP Record | NC 应付单引入字段 |
|---|---|
| 供应商 | 供应商(按 NC 供应商 **code** 反查,`nc_id_map`) |
| 发票号 / 单据日期 / 到期日 | 发票号 / 单据日期 / 到期日 |
| 金额 + 币种 + 汇率 | 原币金额 + 币种 + 汇率(本位币 CAD) |
| 税额 / 税码 | 税额 / 税种(按 NC 税码映射) |
| 成本维度(部门/项目…) | 辅助核算(按 NC 档案 **code**) |
| **UniOps AP 号** | 摘要 / 来源单据号(**复核锚点**,随单据带入 NC) |

### 9.5 JV 复核(核心控制,对应用户第 2 点)
定期(如每期末)把 **UniOps 生成的 JV** 与 **NC 由对应 AP 单据生成的 JV** 逐笔核对:
- **匹配锚点**:UniOps AP 号随单据带进 NC(摘要/来源单据号),NC 生成的凭证回带该号 → 按号匹配;
  辅以 供应商 + 金额 + 期间 兜底。
- **产出差异报告**:UniOps 有/NC 无、NC 有/UniOps 无、金额/科目/税 不一致。
- **容差层级**:因两边**制单规则不同**,JV 可能结构不同(税行拆分/汇兑损益/科目映射差异),复核以
  **净影响 / 关键科目余额 / 往来金额**层面匹配为主,**不强求逐行一致**。差异人工排查。

### 9.6 约束与依赖
- 依赖 **NC 应付模块导入模板(用户提供)** + NC 制单规则(科目/税由 NC 侧决定)。
- 每张导出 AP 单据标记**已导出批次 + 状态**,避免重复引入;复核结果留档。
- 外币单据须带原币 + 币种 + 汇率,满足 NC 多币核算。
- `GL_RTVOUCHER/GL_RTDETAIL`(GL 外部凭证接口)在本模型**不用于 AP 路径**(那是 GL 直接引凭证);
  保留作未来非 AP 场景的备选。

## 10. Go-forward CoA / 税务口径
- 底层总账**沿用 NC 中式科目**,历史与切换后新业务共用同一套科目,历史零重映射。
- 北美税务(GST/HST/PST)通过**税码维度 + tax_codes 主数据**表达,不改科目结构。
- 若将来需要 ASPE 格式对外报表,在中式科目之上叠一层**报表科目映射视图**(不动底层分录)。

## 11. 待构建期解决(Open items)
1. 辅助核算槽位↔档案类型的精确对应(`BD_ACCASSITEM` 解析规则 + `GL_FREEVALUE` vs `GL_DTLFREEVALUE`
   哪个为准)。
2. `PK_SYSTEM`/`PK_SOURCEPK` 来源单据类型枚举(用于 Document Chain 追溯,可选)。
3. 红字/作废凭证在 NC 的表达(`DISCARDFLAG`?)与目标 `reversed` 状态映射。
4. 多科目表/科目版本(`PK_ORIGINALACCOUNT`)是否存在跨年重编码。
5. `BD_PROJECT` 的实际存储(defdoc 结构)与维度归属。
6. **NC 各表导入模板(用户提供)**:反向导出器按模板精确列布局实现;确认 NC「凭证引入」对科目/辅助核算
   是按 code 还是 name、凭证号是否可留空自动编、外币行的原币/本币/汇率列要求。

## 12. 不在本设计范围
- JV 子系统本身的完整设计(制单→审核→过账运行时流程、GL 读取层迁移)——单独 spec,本文件是其数据约束前置。
- Claim Accrual(方案 B)、Document Chain —— 各自独立 spec。
- 迁移的具体实现计划(plan)—— 评审通过后由 writing-plans 产出。
