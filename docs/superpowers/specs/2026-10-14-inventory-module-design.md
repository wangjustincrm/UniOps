# Inventory 模块（一期：WMS / NC 三套库存 / 四方对比）设计文档

- 日期：2026-10-14
- 状态：**口径已全部确认（2026-10-14），开工**
- 分支：`feat/inventory-module`
- 归属：**新独立模块**：`inventory/`（前端）+ `inventory-api/`（后端）

---

## 1. 背景与目标

现在 MRP 下的 Inventory 页只显示 WMS 库存。可 NC 里还有三套「库存」，三套各有口径：

| 来源 | 视角 | 有什么 |
|---|---|---|
| WMS（Flux） | 仓库实物 | 数量、库位、批次、质量状态 |
| NC 供应链 · 库存管理（IC） | 业务账 | 数量、仓库、批次、库存状态 |
| NC 财务会计 · 存货核算（IA） | 成本账 | 数量 + 金额（物料级计价） |
| NC 总账 · 存货科目（GL） | 会计账 | 金额 + 数量（挂物料辅助核算） |

四套数据之间有差异，这是正常的：有时间差、有不计成本仓、有不经过存货核算的分录。可是目前没有工具能把差异并排摆出来，也没有工具说明每笔差异的原因。

一期目标：

1. 建立 Inventory 独立模块，作为今后所有库存功能的归属地（用户原话：「未来针对库存这里还要加很多的功能」）。
2. **WMS Inventory**：把 MRP 现有的 Inventory 页迁过来。
3. **NC Inventory**：在一个页面里看 NC 三套库存。
4. **四方对比（Inventory Reconciliation）**：在任意日期把四套数据横向比对，可勾选按批次展开，并对差异**自动归类**。

## 2. 用户已拍板的决定（2026-10-14）

| # | 决定 |
|---|---|
| D1 | **单独起 Inventory 模块**，不放 MRP，也不放 Finance |
| D2 | WMS 同步**一期留在 mrp-api**，Inventory 只读；以后再迁过来，届时 MRP 改为从 Inventory 读。（实现时改了）WMS Inventory 页的前端直接调 mrp-api 接口（权限放宽为 MRP 或 Inventory 任一键）；对比和追溯用 inventory-api **自己镜像的 WMS 事务日志**（D2′），不读 mrp-api 的表，因此不需要列契约测试 |
| D3 | MRP 现有的 Inventory 页**迁到新模块并改名 WMS Inventory**；MRP 里删掉，导航改成跳到新模块 |
| D4 | IA 数据由 **inventory-api 自己直连 NC** 取，不复用 finance-api 的 IA 镜像（finance 那份保持不动） |
| D5 | 对比日期**可以任意指定**，默认是月末 |
| D6 | 存货核算**按会计期间**取数 |
| D7 | 总账科目取 1402–1408 按物料比；1471 跌价准备单独一行，不参与物料比对；5001–5008 暂不纳入 |
| D8 | SC16（不计成本仓）**标出来**，不剔除 |
| D9 | 差异**自动归类** |
| D10 | 批次：WMS 和 NC 都有**供应商批次**和 **NC 质检批次**两个号，**任意一对相等就算匹配** |
| D11 | **CF00AF / CF00WT 从 2026 年起不再进 NC**，对比时不按批次展开 |
| D12 | **仓库口径**：NC 的 **SC11 / SC12 / SC13 / SC16 才是 WMS 管理的仓库**；SC05、SC08 等是外部代储仓，WMS 没有记录。WMS↔NC 比对**只用 WMS 仓**的 NC 数量，其余仓单列 |
| D2′ | （用户 Q7 已同意）mrp-api 现有的 WMS **现存量**同步保持不动，符合 D2。inventory-api **另外只读镜像 WMS 的事务日志**，这是 MRP 没有用到的新数据，用于历史日期和追溯（§4.7）。这样 inventory-api 也要连 WMS（thick 模式） |
| D14 | **NC 时间按中国时区（UTC+8）解读**。这是 NC 本身的问题，时区被写死在代码里。另外加一个「本地时区」开关，方便切换（Q8） |
| D15 | WMS 的「ASN-QC 期初库存导入」类单据**不和 NC 对应**，追溯时 WMS 侧直接显示「手工期初导入」（Q9） |
| D16 | **批次只认两种**：供应商批次和 NC 质检批次。「-INVEST」后缀等其他形式**一律不做特殊处理**，配不上就按配不上显示（Q10） |
| D17 | 总账**默认含未记账**凭证（Q2）；做**权限矩阵**，由用户自己分配（Q3）；tie-out 不平**不推 Task**，人工进来看（Q4） |
| D18 | **AI 助理暂时不动**：等所有功能完善后，单独开分支更新向导层（Q5，用户明确把这个模块排除在「每次变更同步向导层」规则之外） |
| D19 | 域名：`inventory.canadaroyalmilk.com`（前端）、`inventory-api.canadaroyalmilk.com`（API），由用户去加 DNS（Q6） |
| D13 | 新增**批次追溯**：NC 与 WMS 有差异时，能看到该批次（以及整个物料）在两边的全部变动，包括期初、采购入库、领用、退回等，两边并排显示并**标出差异点**（§7.5） |

## 3. 定位与红线

- **只读 NC、只读 WMS，不回写。** 对比结果只用于呈现，不生成任何 NC 单据。
- **不悄悄少数。** 被过滤、配不上、算不进去的行都要计数，并且在页面上能查到。具体有：WMS 独有的物料、NC 没有批次档案的物料、匹配不上的批次、单位不一致的物料。
- **镜像必须和 NC 勾稽（tie-out）。** 每次同步后自动与 NC 的现成结存核对，不平就在页面顶部显示红条（见 §5.4）。
- **金额也是数据权限。** 没有金额权限的人，后端直接不返回金额字段，不是只在前端藏起来（见 §9）。
- AI 助理向导层：用户明确决定本模块**暂不更新**，等功能完善后单独开分支（D18）。

## 4. NC 取数口径（2026-10-14 只读实测）

调研脚本放在 `/srv/uniops/nc65_introspect/inventory_2026_10_14/`。

### 4.1 组织

| 维度 | PK | 说明 |
|---|---|---|
| 库存组织（IC） | `0001A1100000000034BL` | 01010104 加拿大皇家妙克 |
| 成本域（IA.pk_org） | `1001A1100000003CFXTH` | CRM01 |
| 账簿（IA.pk_book = GL） | `1001A1100000003CGCBX` | 01010104-0003 |

注意：`10024` 是 WMS 的 CUSTOMERID，**不是** NC 的组织编码。三个 PK 写进配置，不写死在代码里。

### 4.2 各源的表与粒度

| 源 | 表 | 粒度 | 数量 | 金额 | 批次 | 仓库 | 历史日期 |
|---|---|---|---|---|---|---|---|
| IC 现存量 | `IC_ONHANDDIM` ⋈ `IC_ONHANDNUM` | 仓×物料×批次×状态 | `nonhandnum` | 无 | ✓ | ✓ | 只有当前值 |
| IC 流水 | `IC_FLOW` | 单据行 | `ninnum − noutnum` | 不可靠 | ✓ | ✓ | **能还原任意日**（实测和现存量逐键一致） |
| IA 结存 | `IA_MONTHNAB` | 物料×期间 | `nabnum` | `nabmny` | ✗ | ✗ | 只有已关账月 |
| IA 明细账 | `IA_DETAILLEDGER` | 单据行 | `nnum`（`fdispatchflag` 0 入 1 出） | `nmny` | 有 `vbatchcode`（只有数量可用） | 有 `cstordocid` | 能累加到任意期间（实测和 monthnab 一致） |
| GL | `GL_DETAIL` ⋈ `GL_VOUCHER` ⋈ `GL_FREEVALUE` | 分录行 | `debit/creditquantity` | `localdebit/creditamount` | ✗ | ✗ | 当年 `00` 期到 P 期 |

**物料主键三方一致**：`IC.cmaterialoid = IA.cinventoryid = GL 物料辅助值 = BD_MATERIAL.pk_material`。在本库里 oid 与 vid 全部相同。

### 4.3 现存量与可用量

- **可用量**在 NC 里不落库，是现算出来的：`可用 = nonhandnum − nlocknum（冻结）− nrsnum（预留）`。加拿大目前没有预留，也没有借入借出。
- **Block / Under Inspection 状态在 NC 里不扣可用量**（所有状态都是 `iusability=1`），只有「冻结」才扣。这一点和 WMS 不同：WMS 的 hold 是质量冻结。
- 有 16 行残留数据：现存量为 0，冻结量却大于 0。所以可用量要先过滤掉现存为 0 的行，再把下限取 0。
- 不做 ATP。`IC_ATP_F` 缓存数据异常，有负数，NPICKMNUM 达 7,900 万。

### 4.4 WMS ↔ NC 批次匹配（D10）

| | 供应商批次 | NC 质检批次 |
|---|---|---|
| WMS | `INV_LOT_ATT.LOTATT05` | `INV_LOT_ATT.LOTATT04` |
| NC | `SCM_BATCHCODE.vvendbatchcode` | `vbatchcode` |

**规则**：两边都去掉所有空白并转大写。在同一物料下，如果 WMS 的 {att04, att05} 和 NC 的 {vbatchcode, vvendbatchcode} 有交集，就算匹配。

一个 WMS 批次可能对上多个 NC 批次，反过来也可能。所以在每个物料内部，用「有交集即合并」（并查集）把匹配上的批次合成**批次组**，对比按批次组进行。这样不会重复计数，也不会漏数。

**实测**（WMS 活跃批次共 1,019 个组合）：

| 情况 | 组合数 | 说明 |
|---|---|---|
| NC 没有批次档案的物料（STORE、托盘、备件等） | 286 | 归为「WMS 独有物料」 |
| 其余 | 733 | 按个数匹配 69%，**按数量匹配 95%** |
| 其余中没匹配上的 | 224 | 175 个是 CF00AF/CF00WT，NC 已停记（D11），属于真实差异 |

★ **WMS 侧批次键用 `NVL(LOTATT04, LOTATT05)` 兜底**：成品和部分包材的 LOTATT04 为空，NC 质检批次填在 LOTATT05 里。上面「任意一对相等」的规则已经覆盖这种情况。
★ mrp-api 的 WMS 镜像**没有同步 LOTATT04**。比对和追溯用 inventory-api 自己镜像的 `INV_LOT_ATT`（§5.2），所以不依赖 mrp-api。mrp-api 补这一列，只是为了让 WMS Inventory 页能显示 QC batch 列。
★ WMS 有「批次-INVEST」这种带后缀的伪批次（拆批调查用）。按 D16 不做特殊处理，配不上就显示「仅 WMS」。

### 4.5 2026-08-31 试对账（证明这个页面有用）

- **IC 与 IA**：1,134 个物料中 1,117 个数量一致。17 个不一致的原因：
  - SC16 不计成本仓（11 个原料，差额恰好等于 SC16 的量）
  - 成品已出库未开票（IC 在出库时扣减，IA 要等开票 I5 才扣减）
  - 一张未签字的 4A
- **IA 与 GL**：IA 12,497,908.76，GL（不含 1471）11,936,297.51，差 −561,611.25。差额集中在 CF00AF（−418k）和 CF0086（−183k），其余来自 GL 里不经过 IA 的分录（AP 直接借记存货、GL 手工调整）。

### 4.6 NC 仓库分类（D12，2026-10-14 实测有现存量的仓库）

| 分类 | NC 仓 | 现存量 | 参与 WMS↔NC 比对 |
|---|---|---|---|
| **WMS 管理** | SC11 原料、SC12 包材、SC13 成品、SC16 RM_MONCHIKIN_OLD（★同时是不计成本仓） | 266,716 / 2,136,962 / 259,887 / 1,110 | ✓ |
| **外部代储** | SC05 Amazon CA、SC06 MomsChoice、SC07 Amazon US、SC08 Radient、SC15 FG(1000853817)、SC17 FG Toronto | 5,236 / 538 / 20 / 29,471 / 164 / 420 | ✗，单列 |
| **生产线边**（用户已确认 Pro01/Pro02） | Pro01 原料、Pro02 包材 | 31,585 / 90,136 | ✗，**单列显示** |
| **瞬时临时仓**（用户：不用管） | Pro03 半成品、015/016 稀奶油、021 粉线 | 650 / 0 / 10,572 / 10,000 | ✗，**不单列** |
| 其他（现存量为 0） | SC01、RMC01 | 0 | — |

分类存放在配置表 `inv_warehouse_class` 里（NC 仓 → `wms` / `external` / `production` / `transient` / `other`）。`transient` 仓不在对比页单列、不参与 WMS 比对，但数量**仍然计入「IC 合计」**，因为 IA 不分仓库，剔掉会让 IA−IC 凭空出现差额。在 Settings 中维护。**NC 新出现、还没分类的仓库**在对比页顶部提示「N 个仓库未分类」，并默认按「不参与 WMS 比对」处理，确保这部分数量不会被悄悄算进 WMS 的比对里。

### 4.7 WMS 事务日志（2026-10-14 实测）

- **`ACT_TRANSACTION_LOG`** 是完整的库存事务日志：从 **2025-02-03** WMS 上线起保存，没有归档。加拿大只有一个仓（CANADA / 10024），共 7.3 万行。
- **能从事务日志还原库存**：只取 `STATUS='99'`（90 是已取消），按下面的口径累加到 (SKU, LOTNUM)。现存的 3,370 个批次里有 **3,369 个（99.97%）与 `INV_LOT.QTY` 一致**。所以 **WMS 任意历史日期的库存都能算出来**，不需要每天存快照。

| 事务类型 | 含义 | 对库存总量的影响 |
|---|---|---|
| IN | 收货 | `+TOQTY_EACH` |
| SO | 出货（含生产叫料） | `−FMQTY_EACH` |
| AD | 调整 | `+(TOQTY_EACH − FMQTY_EACH)`，因为 FM 是调整前的数、TO 是调整后的数 |
| TR | 转移 | 源批次 `−TOQTY_EACH`、目标批次 `+TOQTY_EACH`。批次相同时只是换库位；批次不同时是改批次或拆批 |
| PA / PK / MV / HD / RL | 上架、拣货、移动、冻结、释放 | 不改变总量 |

★ 数量必须用 `*_EACH` 列，因为 `FMQTY` 有 360 行是箱或托单位。
- 代码含义查码表 `BSM_CODE` / `BSM_CODE_ML`（`TRN_TYP` / `DOC_TYP` / `ASN_TYP` / `SO_TYP` / `ADJ_TYP`）。
- WMS 只有一个仓，没有 NC 仓的概念。NC 里 SC11↔SC16 之间的转库，在 WMS 里没有对应事务。

### 4.8 NC 流水分类与两边单号配对

**NC 交易类型**：`IC_FLOW.vtrantypecode`，名称通过 `ctrantypeid → BD_BILLTYPE.billtypename` 取。
- NC **没有转库（4K）行**，转库落成一笔 4I 出加一笔 4A 入，`cotherwhid` 是对方仓。
- 「退回」既有独立的交易类型（采购退库 45-CRM01、生产退料 4A-002 / 4I-006），也有红冲负数行。

**业务类别对照**（追溯页的分组口径）：

| 类别 | NC | WMS |
|---|---|---|
| 采购入库 / 退货 | 45 / 45-CRM01 | ASN-PR / SO-RT |
| 生产叫料 / 退料 | 4I-005 / 4A-002、4I-006 | SO-DM、DD、DM_WH / ASN-RM |
| 成品入库 | 4A-093、4A-099 | ASN-FP、ZK_CA |
| 转出到代储或外部 | 4I-103 / 099 / 101 | SO-QTCK、OT |
| 销售 | 4C | SO-XS |
| 领用 / 取样 / 报废 | 4I-CRM03 / 009 / 22 / CRM06 | SO-OT、SA、BF |
| 盘点 / 调整 | 4A-03、4I-03、4I-CRM004、4A/4I-055 | ADJ、SO-QCTZCK、PDXJ |
| 手工期初导入（D15，只在 WMS 侧，不配对） | — | ASN-QC |
| 改批次 / 改物料 | 4A-010 / 4I-015 | TR（批次变化） |
| 不影响比对 | 4460 状态调整、SC11↔SC16 转库 | PA/PK/MV/HD/RL、同批次 TR |

**单号配对**（三级，实测配对率）：

| 级 | 适用 | 配对键 | 配对率 |
|---|---|---|---|
| 1 单号 | 生产叫料 4I-005 ↔ SO-DM/DD | 55A2 生产订单 = `SOREFERENCE2` + 物料 | 93% |
| | 生产退料 4A-002 ↔ ASN-RM | ZK 号 = `ASNREFERENCE1` | 92% |
| | 采购入库 45 ↔ ASN-PR | PO 号（`vfirstbillcode` = `ASNREFERENCE1`）+ 物料 | 91% |
| | 取样、盘盈盘亏 | NC `vheaduserdef13` = WMS 的 CASO / CAADJ | 86–100% |
| | 销售 4C ↔ SO-XS | DN 号 | 34% |
| 2 数量+时间 | 成品入库 4A-093 ↔ ASN-FP，以及各类手工单 | 物料 + 批次 + 数量 + 时间窗（成品要放到 −27 天到 +7 天） | 成品批次级 58/99 数量完全相等 |
| 3 类别 | 都配不上的 | 按上表的类别和月份汇总比对 | — |

**★ 时区**：NC 的 `ts` 和系统自动填写的 `dbizdate` **推断是中国时间（UTC+8）**，由应用服务器写入；NC 数据库和 WMS 都是多伦多时间。依据是单点实测，加上 4A-002 与 WMS 收货的时差众数在 +11 到 +12 小时（n=913）。部分 `dbizdate` 只有日期（00:00:00，MES 生成的单据），这些只能按天比。**取数和追溯都要先把 NC 时间换算成多伦多时间**（D14）。配置项 `nc_time_zone` 默认 `Asia/Shanghai`，可以在 Settings 切换成 `America/Toronto`。用 IANA 时区库换算，冬令时和夏令时自动处理。

**实测三个差异批次**（证明追溯有用）：
- **CR0190、CP0168**：两边物料合计**完全相等**，只是批次标签不同。WMS 把货收到了别的批次，或者先拆出一个伪批次再发料，NC 则从另一个批次发料。
- **S0104**：真正的数量差。WMS 在 10-06 已经把 5,040 发往 Radiant（SC08），NC 一直没做转库。
- 全量看：现存的 1,159 个（物料，批次）键中只有 248 个两边相等；按 SKU 汇总是 78/246 相等。**批次层面的差异远多于物料层面**，所以追溯页必须并排显示物料级和批次级。

## 5. 数据同步（inventory-api）

### 5.1 为什么镜像，不实时查

实测数据量很小：IC_FLOW 全量 7.5 万行；IA 明细 9.3 万行；GL 存货科目分录全年份 10.9 万行、2026 年 2.6 万行。任意日期在 NC 上实时聚合也都在 1 秒以内，所以两种做法都可行。仍然选择镜像，原因有四：

1. 符合 Finance 定位的红线：镜像加 tie-out，同步时一次性证明数据完整。实时查询没有这个检查点。
2. 下钻到单据、做差异归类时要反复按多维度扫流水，在本地 Postgres 里做，不会给 NC 加压。
3. NC 不可达时页面仍可用，并显示「数据截至 X」。
4. 以后的库存功能（周转、呆滞、效期）都要用流水，镜像一次，后面复用。

### 5.2 镜像表（前缀 `inv_`）

| 表 | 来源 | 范围 | 刷新方式 |
|---|---|---|---|
| `inv_nc_ic_flow` | IC_FLOW | 34BL 全量 | 全量替换 |
| `inv_nc_ic_onhand` | IC_ONHANDDIM ⋈ IC_ONHANDNUM | 34BL，现存或冻结不为 0 | 全量替换（当前值：状态、冻结） |
| `inv_nc_batch` | SCM_BATCHCODE | 被 IC 引用到的批次 | 全量替换 |
| `inv_nc_warehouse` | BD_STORDOC | 34BL | 全量替换（含 `iscalculatedinvcost`） |
| `inv_nc_material` | BD_MATERIAL（+BD_MEASDOC） | 被以上任一表引用到的 | 全量替换（pk→code/名称/主单位/分类） |
| `inv_nc_ia_ledger` | IA_DETAILLEDGER | CFXTH，**dr=0** | 全量替换 |
| `inv_nc_ia_monthnab` | IA_MONTHNAB | CFXTH | 全量替换（tie-out 用，也作为已关账月的权威值） |
| `inv_nc_ia_period` | IA_ACCOUNT | CFXTH | 全量替换（判断最近关账期间） |
| `inv_nc_gl_line` | GL_DETAIL ⋈ GL_VOUCHER（+物料辅助解析） | 账簿 CGCBX，科目 1402–1408、1471 | 全量替换（带 year、period、prepareddate、tallydate、来源系统、科目、物料 pk、数量、金额） |
| `inv_wms_txn` | ACT_TRANSACTION_LOG | CANADA/10024，STATUS='99'，只保留会影响总量的类型和 TR | 增量（按 `TRANSACTIONID`），每周全量对一次 |
| `inv_wms_lot_att` | INV_LOT_ATT | 加拿大全部批次（含历史） | 全量替换（LOTNUM → att04 / att05 / 生产日期 / 失效日期） |
| `inv_wms_doc` | DOC_ASN_HEADER/DETAILS、DOC_ORDER_HEADER/DETAILS、调整单 | 被 `inv_wms_txn` 引用到的 | 增量（类型、参考号 `ASNREFERENCE1-4`、`SOREFERENCE1-5`、`PONO`、备注） |
| `inv_wms_code` | BSM_CODE / _ML | 五类码 | 全量替换 |
| `inv_sync_state` | — | 每个源一行 | `last_synced_at` / `last_success_at` / `row_count` / `tieout_ok` / `last_error` |

- **全量替换**指在一个事务里先 DELETE 再 INSERT，与 mrp-api 的 WMS 同步同一形状。数据量小，全量替换不需要处理增量时的「被删除行」问题，这是最大的好处。
- GL 和 IA 的 dr=1 行、`discardflag='Y'` 的作废凭证，在抽取时就过滤掉，同时**记下过滤掉的行数**写进 sync_state。

### 5.3 调度

- 照 WMS 同步和 NC 同步的现有形状：后台 asyncio 调度器，用 advisory lock 防并发，间隔参数**在 Inventory → Settings 可调**（实现时改的：放在模块自己的设置页，Portal Admin 不另加设置区）。NC 和 WMS 事务日志两个来源共用这个间隔，各自按自己的状态行判断是否到期，每个来源单独加锁。
  - 默认 60 分钟，0 表示停用。
  - 部署开关 `INV_NC_SYNC_SCHEDULER_ENABLED`，dev 环境设为 `false`。
- 每个页面右上角有「数据截至 …」时效条，加一个 **Refresh** 按钮（需要 `inventory.admin` 权限）。
- 时效显示只读 `last_success_at`。原因：`last_synced_at` 失败时也会写，用它会让页面在同步失败后仍显示「刚同步」。这个坑 MRP 踩过（mrp16）。

### 5.4 Tie-out（每次同步后自动跑）

| 检查 | 方法 | 不平时 |
|---|---|---|
| IC 流水 ↔ 现存量 | Σ `inv_nc_ic_flow` 全期，按仓+物料+批次聚合，与 `inv_nc_ic_onhand` 比较（不比状态维，因为状态无法从流水还原） | 列出不平的键 |
| IA 明细 ↔ 月结存 | Σ 明细（`caccountperiod ≤` 最近关账期）按物料聚合，与该期 `inv_nc_ia_monthnab` 比较数量和金额 | 同上 |
| GL 分录 ↔ GL_BALANCE | 当年按科目聚合，与 NC `GL_BALANCE` 比较（同步时顺便取） | 同上 |
| WMS 事务日志 ↔ 现存量 | Σ `inv_wms_txn` 按 (SKU, LOTNUM) 聚合，与 WMS `INV_LOT.QTY` 比较（同步时顺便取） | 列出不平的批次（实测基线有 2 个已知例外：一个是收货行事后被编辑，一个是虚拟 SKU FULLCARTON） |

结果写进 `inv_sync_state.tieout_ok`，明细存入 `inv_tieout_issue`。页面顶部显示红条「同步数据与 NC 不平（N 项）」，点开能看到明细。**不推 Task、不发通知**，与 AI 财务分析的做法一致；如果要推，见 §12 Q4。

## 6. 任意日期 D 的取数（D5 / D6）

记 P = D 所在的会计期间（YYYY-MM），Y = D 所在年份。

| 源 | D 的取数 |
|---|---|
| **WMS** | Σ `inv_wms_txn`，条件 `TRANSACTIONTIME ≤ D 23:59:59`（多伦多时间），按 SKU × 批次键聚合（§4.7 口径）。**任意日期都能算**。D 早于 2025-02-03 时，WMS 列显示「WMS 未上线」 |
| **IC** | Σ `inv_nc_ic_flow`，条件 `dbizdate ≤ D 23:59:59`（按 §4.8 先把中国时间换算成多伦多时间），按仓×物料×批次聚合，只保留不为 0 的行。**状态和冻结只有 D = 今天时才显示**。按 §4.6 的仓库分类拆成 **IC(WMS 仓)** 和 **IC(其他仓)** 两部分 |
| **IA**（按会计期间） | `caccountperiod < P` 的全部，加上 `caccountperiod = P 且 dbizdate ≤ D` 的部分。D 是月末时，这就等于 `caccountperiod ≤ P`，与 IA_MONTHNAB 口径相同 |
| **GL** | Y 年 `period '00'..P−1` 的全部，加上 P 期中 `prepareddate ≤ D` 的凭证。默认**含未记账**，可以勾选去掉（和 Budget 页的「Include unposted」是同一套交互） |

**期间状态提示**（显示在页面顶部）：

- P ≤ 最近关账期：「已关账」。
- P > 最近关账期，但 P 期出库行全部有金额：「未关账，成本已计算」。
- P 期仍有出库行 `nmny` 为空：「**未关账，N 行出库成本未计算，IA 金额不完整**」。实测 2026-10 就是这个状态。

**为什么不再做 WMS 每日快照**：WMS 的事务日志能还原任意历史日期，实测一致率 99.97%，所以原 Q1 不再需要。

## 7. 页面

模块导航：**WMS Inventory / NC Inventory / Reconciliation / Trace / Settings**。

### 7.1 WMS Inventory（从 MRP 迁过来）

- 保留原来的三个 tab（Batches / Aging / Materials）、时效条和 Refresh 按钮，代码整体搬到 `inventory/`。
- 新增一列 **NC QC batch**（LOTATT04）。
- 数据仍然由 mrp-api 提供：前端直接调 mrp-api 的 `/inventory/*` 接口。mrp-api 的 CORS 加上新前端的源；接口权限改为 `mrp.report.view` **或** `inventory.view` 任意一个即可。
- MRP 那边删掉这个页面，导航项改成外链「Inventory ↗」，跳到新模块。

### 7.2 NC Inventory

页面顶部有日期选择器（默认今天）、物料类别筛选、搜索框。下面三个 tab：

1. **Stock (IC)**：行是仓库×物料×批次。列：现存量、冻结、可用（= 现存 − 冻结，下限 0）、库存状态、生产日期、失效日期、供应商批次。
   - D ≠ 今天时，冻结、可用、状态三列置灰，并提示「历史日期只有数量」。
   - 可切换按物料汇总。
   - SC16 行带「不计成本」徽章。
2. **Cost (IA)**：行是物料。列：数量、金额、单价（需要金额权限）。顶部显示期间状态提示。
   - 点数量可以下钻：按仓库和批次拆开的**数量**分布，标注「金额只在物料级」。
   - 点金额可以下钻到明细账行（单据号、类型、暂估标志）。
3. **Ledger (GL)**：行是科目×物料。列：数量、金额。
   - 1471 跌价准备单独一行，放在合计下方。
   - 没有挂物料辅助的行（例如 140306 零配件）归到「(未挂物料)」行，不丢掉。
   - 下钻到凭证分录。

### 7.3 Reconciliation（四方对比）

**顶部控制**：

- 日期（默认上一个月末）
- 类别筛选
- 「只看有差异」
- 差异类别多选
- **☐ 按批次展开**（D3 原始需求）
- GL 的「含未记账」

**汇总卡**：四个源各自的合计（数量，以及有金额权限时的金额），再加上「已解释差异」和「待查差异」两个合计。

**主表**（行 = 物料）：

| 物料 | WMS 数量 | IC(WMS 仓) | IC(外部+线边) | IC 合计（含临时仓） | IA 数量 | GL 数量 | IC(WMS仓)−WMS | IA−IC 合计 | GL−IA | IA 金额* | GL 金额* | GL−IA 金额* | 差异归类 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

- **WMS 只和 IC(WMS 仓) 比**（D12）；IA 和 GL 不分仓库，所以只和 IC 合计比。
- 「IC(外部+线边)」可以点开，按仓库展开。

\* 需要 `inventory.cost.view` 权限。

- **差异归类**列显示徽章，悬停显示每一类解释了多少量或多少金额，最后一个徽章是「待查 X」。
- **勾选「按批次展开」后**，每个物料下面展开批次组子行，显示 WMS / IC / IA 三列数量，GL 列留空（D3：总账没有批次）。
  - D11 名单里的物料（CF00AF、CF00WT）**不展开**，显示「NC 2026 起停记，不按批次」。
  - 匹配不上的批次单独成子行，标「仅 WMS」或「仅 NC」。
- **单位**（实现时改了）：只作为**信息徽章**显示，**不跳过比对**。原因：WMS 基本单位取自包装层级，默认 KG，2,498 个 SKU 里 2,365 个是 KG，连按件计、两边数量完全相同的包材（CP0016 22,000 = 22,000）也会被判「不一致」；若跳过比对，会掩盖 756 个物料，其中有真实差异。
- 表下方另列两块：**WMS 独有物料**（NC 无此物料或无批次档案），以及 **1471 跌价准备**。
- 导出 xlsx。差异归类导出成**文字列**，不用颜色，因为表格会被筛选和转发。

### 7.4 Trace（批次追溯，D13）

**入口**：
- 对比页任意一行（物料或批次组）上的「追溯」按钮，会带上物料、批次和日期。
- 也可以直接在 Trace 页输入物料加批次，批次可以留空，表示整个物料。

**上半部分：两条余额曲线**
- NC（只取 WMS 仓）和 WMS 各一条，横轴是时间，起点是 2025-02-03 WMS 上线日。
- **标出「第一个分歧点」**：两条累计余额从这一刻开始出现差额，并且之后差额不再回到 0。
- 同时显示**物料级**和**批次级**两行汇总，一眼就能区分两种情况：
  - **「批次标签差」**：物料合计相等，只是批次对不上。实测这是最常见的情况。
  - **「数量真差」**：物料合计本身就不相等。

**实现补充**：第二级配对**按单据汇总**后再比数量，因为一张 NC 成品入库常常对应 WMS 同一张 ASN 下的十几托。分歧点之外，还给出「**自上次一致以来最大的未匹配单据**」，因为第一个分歧点可能只是一笔 −4.2，真正要看的 −5,040 在它后面。

**下半部分：两列流水并排**
- 左边 NC，右边 WMS，按多伦多时间排序。每行显示：时间、类别（§4.8）、单据号、数量增减、该侧累计余额。
- **配上的行**并排放在同一行，标出配对等级（单号 / 数量+时间 / 仅类别）和两边的时差。
- **配不上的行**只在一侧出现，标「仅 NC」或「仅 WMS」，并且高亮显示。
- **分歧点所在的那一行置顶高亮**，旁边给出一句定性说明（规则生成，不用 AI），例如：
  - 「WMS 10-06 SO-QTCK 发出 5,040，NC 无对应转库」
  - 「NC 从 20260331015E 发料 2,520，WMS 同数量从 20250815011E 发出，相差 16 天」
- 可以按类别汇总折叠：比如期初、采购入、生产领用、退料、销售、调整，每类各占一行，显示两边合计和差额。这就是需求里说的「期初多少、采购入多少、领用多少、退回多少」。
- 不影响比对的流水（4460、SC11↔SC16 转库、WMS 内部移库）默认隐藏，可以勾选显示。

**范围**：
- NC 只取 WMS 仓（D12）。如果流水的对方仓是外部代储仓或线边仓，就标成「转出 WMS 仓」或「转入 WMS 仓」。
- 物料不能按批次展开时（D11），只做物料级追溯。
- 下钻和导出：每一行都能链接到单据号，整页可以导出 xlsx。

### 7.5 Settings（有 `inventory.view` 就能看，写操作需要 `inventory.admin`）

- **NC 不按批次跟踪的物料名单**：初值 CF00AF、CF00WT，带「自哪天起」字段（D11）。
- **仓库分类**（§4.6）：初值 SC11/12/13/16 = WMS 管理，SC05/06/07/08/15/17 = 外部代储，Pro01/Pro02 = 生产线边，Pro03/015/016/021 = 瞬时临时仓。
- **NC 时区**：默认 `Asia/Shanghai`，可切换为 `America/Toronto`（D14）。
- **比对容差**：数量默认 0.001，金额默认 0.01。
- 同步状态和 tie-out 明细。同步间隔在 Portal Admin 里设，这里只放跳转链接。

## 8. 差异自动归类（D9）

规则是**确定性**的：每条规则算出它能解释的量（或金额），从差额里扣掉，**剩下的归为「待查」**。规则按下表顺序依次扣减，同一份差额不会被重复解释。

**实现时用真实数据（2026-08-31）调出来的修正**：
- IA−IC 与 GL−IA 两侧的规则都**封顶于剩余差额且方向一致**。原因：这些规则用的是累计数，没法逐行对，不封顶就会凭空造差额。例如 S0103 两边相等，累计「出库 − 开票」却有 1,474.2。
- **C8 只算 AP 和手工凭证**，CM（成本管理）、IA 都不算。CM 把生产成本记进存货是 NC 的正常流转，会被 IA 抵消；把它算进来，会把两边相等的物料拆成 ±196 万。
- 新增 **以前年度结转**：年初 00 期 − IA 截至上年末的累计。00 期年初结转凭证的来源是 GL，若混进 C8 会误判。零配件（M0xxx 等）的期初余额只在 GL 里有，也归到这一类。
- **C6 未签字单据**只算 IA 确实没收到的那部分：用 IA `csrcbid` 对 IC `cgeneralbid` 判断，46/4A/4I/4D 能关联上。另外排除不计成本仓，避免和 C4 重复。
- C3 时间差、C9 未关账成本这两条暂未实现。C9 已在期间状态条上提示。
- C10 批次标签差：勾选按批次展开时，对**全部物料**计算，参与状态、计数和筛选。

结果：1,477 个物料中，IA−IC 待查 2 个，GL−IA 待查 7 个，正好是人工查出来的那几个（CF0086 −183,413.73 等）。IC−WMS 待查 137 个，多为包材，属于真实差异。

| # | 类别 | 作用于 | 计算 |
|---|---|---|---|
| C1 | NC 已停记（D11） | IC−WMS | 名单物料在「自哪天起」之后的全部 WMS−IC 差额 |
| C2 | WMS 独有物料 / NC 无批次档案 | IC−WMS | 物料在 NC 不存在，或者没有任何批次档案 |
| C2b | NC 仓库未分类 | IC−WMS | 未分类仓库里的 IC 量（只提示，不计入 WMS 比对） |
| C3 | 时间差（同步时点） | IC−WMS | 只在 D = 今天时适用：两边同步时间之间 NC 发生的流水量 |
| C4 | 不计成本仓（SC16 等） | IA−IC | `iscalculatedinvcost='N'` 的仓库在 D 的 IC 数量 |
| C5 | 已出库未开票 | IA−IC | D 之前已经销售出库（4C）、但截至 D 还没有 I5 开票结转的数量（字段实现时核实，见 §11） |
| C6 | 未签字单据 | IA−IC | IC 流水里 `fbillflag` 为未签字、且 IA 没有对应记录的数量 |
| C7 | 补录跨期 | IA−IC | `caccountperiod ≠ dbizdate 所在月` 的 IA 行在 D 的影响量 |
| C8 | GL 非 IA 来源分录 | GL−IA 金额 | GL 存货科目上来源系统 ≠ IA 的分录合计（AP 直接借存货、GL 手工凭证），按来源分小类显示 |
| C10 | 批次标签差 | 批次级 IC−WMS | 物料级相等，但批次组之间一增一减（只在展开批次时显示） |
| C9 | 未关账成本未计算 | GL−IA 金额 | P 期 `nmny` 为空的出库行（只做提示，不量化） |
| — | **待查** | 全部 | 扣完上面各项后的剩余部分，超过容差即高亮 |

每个类别都能点开看到构成它的单据行（下钻）。「待查」部分直接链接到 Trace 页，带上该物料和批次。归类结果只在请求时计算，不落库，因为日期是任意的。如果实测太慢，再按日期做缓存。

## 9. 权限

| 键 | 含义 | 默认授予 |
|---|---|---|
| `inventory.view` | 看全部**数量**：WMS、IC、IA 数量、对比页 | 不默认授予，在 Portal Access Control 矩阵里授 |
| `inventory.cost.view` | 看**金额**：IA 金额和单价、GL 金额、金额差、C8/C9 | 同上 |
| `inventory.admin` | Settings、手动 Refresh | 同上 |

- 没有 `cost.view` 权限时，**后端在响应里直接去掉金额字段**，并且有测试钉住这一点。tie-out 明细里按金额度量的行也算金额：只返回条数（`amount_issues_hidden`），不返回行。
- 前端按权限决定显示哪些导航和列。
- 新增权限键要在 identity 的 `seed_authz.py` 里登记，发布时运行 seed。

## 10. 改动面

| 位置 | 改什么 |
|---|---|
| **新** `inventory-api/`（端口 8013） | FastAPI 服务骨架、NC 读取（thin 模式，照 `epms-api/.../nc_purchase_sync/reader.py`）、§5 的镜像和调度、§6 的取数、§8 的归类、WMS 每日快照、权限门禁和测试 |
| **新** `inventory/`（端口 5181） | 前端：shell 外壳，四个页面 |
| `mrp-api` | WMS 抽取加 `LOTATT04` → 新列 `qc_batch`（迁移 mrp17）；新增 `tests/test_inventory_consumed_columns.py` 钉住 Inventory 直读的列；`/inventory/*` 权限放宽为 mrp 或 inventory 任意一个；CORS 加新源 |
| `mrp/` | 删 Inventory 页，导航改外链 |
| `identity-api` | 新迁移插入三个权限键（照 `0021_fin_analysis_perm.py` 的形状）+ 在 `seed_phase2_keys.py` 登记；★identity alembic 与 EHS 分支编号有重叠，发布前确认单 head |
| `portal` | 首页模块卡片、Admin 同步间隔设置 |
| 基础设施 | 见 §10.1（compose、域名证书、CORS、发布清单） |
| AI 助理向导层 | **本期不动**（D18） |

### 10.1 基础设施接入清单（照 MRP 接入时的形状，2026-10-14 按 origin/main 梳理）

**端口**：API **8013**，前端 **5181**。8012/5180 已经被尚未合并的 EHS 分支占用，所以跳过。

| # | 位置 | 改什么 |
|---|---|---|
| 1 | `make-stack-env.sh` | 加 `INVENTORY_API_PORT` / `INVENTORY_PORT`（标准端口 + 100×i） |
| 2 | `docker-compose.dev.yml` | 加 inventory-api 段（照 mrp-api，含 `NC_*` env 和 `INV_NC_SYNC_SCHEDULER_ENABLED: "false"`）、inventory-frontend 段、`inventory_node_modules` 卷；**12 行 ALLOWED_ORIGINS 全部追加 5181**；portal 加 `VITE_INVENTORY_URL` |
| 3 | `docker-compose.prod.yml` | 加 `uniops-inventory-api`、`uniops-inventory-web` 两个服务（build context 都是仓库根）；portal-web 加 build arg |
| 4 | `.env.prod.example` / `.env.lan.example`，以及服务器上的真实 .env | ALLOWED_ORIGINS 加新源；加 `INVENTORY_URL` / `INVENTORY_API_URL` |
| 5 | `Caddyfile` | 加 `inventory.` 和 `inventory-api.canadaroyalmilk.com`（`import site_tls`，证书自动签发）。★**必须先建好公网 DNS 和内网 split-DNS**，否则签证书失败 |
| 6 | `DEPLOYMENT.md` | 更新子域名表、DNS 清单，新增 Inventory Module Release Steps |
| 7 | `migrate-prod.sh` / `reset-db.sh` / `run_tests.sh` / `check-health.sh` | 加 inventory-api |
| 8 | `portal/` | `lib/api.ts`（URL + `RETURN_URL_ORIGINS`）、`navConfig.tsx`、`PortalHome.tsx` 卡片（同步设置放在 Inventory Settings，**不加** Portal Admin 设置区）、`Dockerfile` 的 ARG **和** ENV 成对加（只加 ARG 会静默回落到 localhost，已经踩过三次） |
| 9 | AI 助理 | **本期不动**（D18），所有功能完善后单独开分支 |
| 10 | 发布 | 镜像从 17 个变成 **19 个**；发布手册加 `alembic_version_inventory` 前置检查，并 diff 前端烤入的域名 |

inventory-api **要连 WMS 读事务日志**（D2′）。WMS 是 Oracle 11g 及以下版本，必须用 thick 模式，所以 Dockerfile 要照 mrp-api 打入 Oracle Instant Client，并配置 `WMS_*` 环境变量。NC 仍用 thin 模式。

## 11. 实现时要先核实的点（不影响设计形状）

1. **NC 时间的时区**：推断是 UTC+8（§4.8）。实现时向 IT 确认 NC 应用服务器的时区，并确定冬令时和夏令时下的换算规则（13 或 12 小时）。只有日期、没有时刻的行按「当天」处理。
2. **C5 已出库未开票的字段**：销售出库行上的累计开票数量（`IC_SALEOUT_B` 或 SO 模块里的相关字段）。
3. **WMS 与 NC 的单位映射**：哪些物料两边的主单位不一致。
4. **IA 补录跨期（C7）对任意日期的影响方向**：用 2026 年的 1,715 行实测验证。

## 12. 用户答复（2026-10-14，全部已答）

Q1 已不需要（WMS 事务日志能还原任意日期）；Q2–Q10 的答复见 §2 的 D2′、D14–D19。

## 13. 分期

| 期 | 内容 | 可验收的结果 |
|---|---|---|
| P1 | inventory-api 骨架 + NC 九张镜像 + 调度 + tie-out；inventory 前端骨架接入 Portal | 同步跑通，tie-out 全平，页面能打开 |
| P2 | NC Inventory 三个 tab + 任意日期 | 08-31 的数字与 §4.5 实测一致 |
| P3 | WMS Inventory 迁移 + mrp-api 补 LOTATT04 + 每日快照 | MRP 跳转可用，QC batch 列有值 |
| P4 | Reconciliation + 自动归类 + 导出 | 08-31 全部差异归类后「待查」可解释 |
| P5 | Trace 批次追溯（WMS 事务日志镜像已提前在 P2 期间完成，因为对比页的任意日期 WMS 也要用它） | CR0190 / CP0168 / S0104 三个批次的分歧点与 §4.8 实测一致 |
| P6 | 权限矩阵、发布清单（AI 助理不在本期） | 发布 |
