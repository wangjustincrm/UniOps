# NC65 BOM 表结构调研

**日期**: 2026-08-03
**目的**: 为 MRP Phase 0 Task 4（`nc_bom`/`nc_bom_b` 原始镜像）与 Task 5（规范化 `boms`/`bom_lines`/`bom_substitutes`）提供落地依据。
**方法**: `python-oracledb 2.5.1` thin 模式只读连接 NC65（10.10.95.67:1521/ORCL，用户 `ncsc`），连接值取自 `c:/Project/nc65_conn.env`（密码未写入本文档或任何 repo 文件）。调研脚本临时存于本机 scratchpad，未提交。

## 结论摘要

1. **表名与 Task 1 brief 的猜测一致**：`NCSC.BD_BOM`（表头）/ `NCSC.BD_BOM_B`（子件行）就是真实 BOM 主档，无需改用 FORMULA/MM_ 系列表。另有 `NCSC.BD_BOM_REPL`（替代料表）对应规划中的 `bom_substitutes`。
2. **NC 没有 BOM 数据 = 0 行的情况不存在**：`BD_BOM` 实测 **1016 行**，`BD_BOM_B` 实测 **10169 行**，`BD_BOM_REPL` 实测 **664 行**（均为 `select count(*)` 实测值；`all_tables.num_rows` 统计信息是陈旧值，偏低约 30%，不可用作行数依据）。
3. **三层级联（制粉→干混（可选）→包装）已用真实数据验证连通**，见下文"级联验证"一节。**级联的载体不是 BD_BOM 的任何结构化字段，而是 BOM 头部 `HCMATERIALID`（母件）关联的 `BD_MATERIAL.CODE` 前缀**：
   - `CS` 前缀（`BD_MARBASCLASS` 分类 `04 粉仓粉/Storage Silo Powder`）= 制粉层产出（milling），其自身 BD_BOM 消耗 `CM`(标准化奶)/`CR`(原料) → 产出粉仓粉。
   - `CW` 前缀（**未在 `BD_MARBASCLASS` 里独立分类** —— 与 `CS` 共用同一个 `PK_MARBASCLASS`='04'，仅编码前缀不同）= 干混层产出（dry-mix，可选），其自身 BD_BOM 消耗 `CS`(粉仓粉) + 少量 `CR`(如预混核苷酸) → 产出干混粉。
   - `CF` 前缀（`BD_MARBASCLASS` 分类 `05 产成品/Finished Products`）= 成品层，其自身 BD_BOM（即"查询成品只见到的那张 BOM"）消耗 `CW`(干混粉，若存在) 或直接 `CS`(粉仓粉，跳过干混) + `CP`(包装物)。
   - **`FBOMTYPE`（BD_BOM 头部字段）不是可靠的层级判别字段**：全库仅出现值 `1`（1012 行，几乎全部实际生效的 BOM，无论逻辑层级）与 `2`（4 行，命名含"包装BOM"字样但均是被弃用的历史草稿/仅 1 条获批且未被任何成品实际引用）。`BD_MATERIAL.MATERIALTYPE` 列在全表 2590 行中**全部为 NULL**，同样不可用。
   - **结论：Task 5 的 `boms.bom_type`（milling/drymix/packaging）必须从母件物料编码前缀（CS/CW/CF）派生，而不是从 NC 任何结构化类型字段读取。** 需要在 transform 层维护一张前缀→bom_type 映射（详见下文映射表）。
4. **物料关联**：`BD_BOM.HCMATERIALID`（母件）与 `BD_BOM_B.CMATERIALID`（子件）均直接等于 `BD_MATERIAL.PK_MATERIAL`（CHAR(20) 定长主键），无需经过版本表中转（`HCMATERIALVID`/`CMATERIALVID` 存在但样本中对母件多为 `~`，对子件恒等于 `CMATERIALID` 本身，可直接忽略，只用 `HCMATERIALID`/`CMATERIALID`）。
5. **增量水位字段**：`TS`（CHAR，格式 `'YYYY-MM-DD HH24:MI:SS'` 字符串，**始终有值**）是可靠水位；`MODIFIEDTIME`（CHAR，同格式）**仅在单据被二次修改后才有值**，新建未改动的单据该列为 NULL——**增量同步应以 `TS` 为准，`MODIFIEDTIME` 仅供参考**。两者均为字符串而非原生 DATE/TIMESTAMP 类型，同步时需按字符串比较或显式转换。
6. **审批状态字段**：`BD_BOM.FBILLSTATUS`（NUMBER）。实测仅两个取值：`1`=已审核（854 行，`APPROVER` 列同时非空）、`-1`=未审核/草稿（162 行，`APPROVER`='~' 空值占位符）。**NC 用 `'~'` 作为空值占位符**（而非 NULL），转换时需识别。
7. **损耗字段在行级但全表为空**：`BD_BOM_B.NBFIXSHRINKNUM` / `NBFIXSHRINKASTNUM` / `NDISSIPATIONUM`（候选"损耗率"字段）在全部 10169 行中**无一行有非空非零值**——**本 NC 实例未在 BOM 层维护损耗率数据**。Task 5 的 `bom_lines.scrap_rate` 只能落默认值 0（或后续从生产工单实际投料差异 / 手工维护渠道获取，不在 NC BOM 表内）。
8. **生效日期在行级、不在头级**：`BD_BOM` 表头 60 列中**没有**生效/失效日期列；生效窗口在 `BD_BOM_B` 行级：`CBEGINPERIOD`/`CENDPERIOD`（CHAR，`'YYYY-MM-DD HH24:MI:SS'`，观察到 `CENDPERIOD` 常年填 `'2999-12-31 23:59:59'` 或 `'3000-01-01 12:59:59'` 表示"长期有效"）。头级只有 `HVERSION`（版本号字符串，如 `'1.0'`/`'1.1'`）作为生命周期标识——**同一母件在 `BD_BOM` 里可以有多条不同版本的头记录同时 `FBILLSTATUS=1`（已审核）**（示例 CS0026 有 7 个已审核版本 1.0~1.6 并存），Task 5 的"生效版本查询"需要额外的选版策略（观察到的实践是"取最新版本号"或"取行级 `CBEGINPERIOD` 最新且覆盖查询日期的一行"，两者在样本中结果一致，建议以 `HVERSION` 数值排序取最大作为主策略）。
9. **组织维度**：`PK_ORG`/`PK_ORG_V` 在 `BD_BOM` 上有 6 个不同值（主力两个：`0001A11000000000348U` 633 行、`0001A1100000000034BL` 359 行），Task 4 的 reader 若只需单一工厂可加 `WHERE PK_ORG=...` 过滤；Phase 0 镜像建议先原样落盘全部组织，不在 reader 层过滤，交给 Task 5 transform 决定是否按组织切分。
10. **替代料表 `BD_BOM_REPL`**（664 行）：`CBOM_BID`（fk → `BD_BOM_B.CBOM_BID`）+ `CREPLMATERIALOID`（替代料 → `BD_MATERIAL.PK_MATERIAL`）+ `VROWNO`（顺序）+ `VREPLACEINDEX`（换算比例字符串，样本恒为 `'1.00/1.00'`）。这正是 Task 5 `bom_substitutes` 的源表；`BD_BOM_B.BCANREPLACE`（'Y'/'N'）标记该行是否允许替代，但样本行全部为 `'N'`——替代关系目前使用率很低，Task 5 可先落地映射逻辑，实际数据量不必预期很大。

---

## 表 1：`NCSC.BD_BOM`（BOM 表头，实测 1016 行）

### 全列清单（60 列，原名 : Oracle 类型）

```
APPROVER:VARCHAR2; BILLMAKER:VARCHAR2; BKITITEM:CHAR; CBOMID:CHAR; CREATIONTIME:CHAR; CREATOR:VARCHAR2;
DR:NUMBER; FBILLSTATUS:NUMBER; FBOMTYPE:NUMBER; HBCUSTOMIZED:CHAR; HBDEFAULT:CHAR; HBISFEATURE:CHAR;
HCASSMEASUREID:VARCHAR2; HCECNID:VARCHAR2; HCFEATURECLASSID:VARCHAR2; HCFEATURECODE:VARCHAR2;
HCMATERIALID:VARCHAR2; HCMATERIALVID:VARCHAR2; HCMEASUREID:VARCHAR2; HCPROJECTID:VARCHAR2;
HFBOMSOURCE:NUMBER; HFVERSIONTYPE:NUMBER; HNASSPARENTNUM:NUMBER; HNPARENTNUM:NUMBER; HRTVERSION:VARCHAR2;
HSRCID:VARCHAR2; HVCHANGERATE:VARCHAR2; HVDEF1..HVDEF20:VARCHAR2 (20个自定义列，样本均为'~');
HVECNBILLCODE:VARCHAR2; HVERSION:VARCHAR2; HVNOTE:VARCHAR2; MODIFIEDTIME:VARCHAR2; MODIFIER:VARCHAR2;
PK_GROUP:VARCHAR2; PK_ORG:VARCHAR2; PK_ORG_V:VARCHAR2; TAUDITTIME:CHAR; TMAKETIME:CHAR; TS:CHAR;
VBILLCODE:VARCHAR2; VBILLTYPE:VARCHAR2
```

### 关键字段说明

| 列名 | 类型 | 含义 | 观察值 |
|---|---|---|---|
| `CBOMID` | CHAR(20) | BOM 头主键 | 唯一，作为 `nc_source_pk` |
| `HCMATERIALID` | VARCHAR2 | **母件物料** → `BD_MATERIAL.PK_MATERIAL` | 决定这张 BOM 属于哪个产出物（成品/干混粉/粉仓粉） |
| `HVERSION` | VARCHAR2 | 版本号 | `'1.0'`,`'1.1'`,`'1.2'`... 同一母件可多版本并存且都已审核 |
| `FBILLSTATUS` | NUMBER | 审批状态 | `1`=已审核(854行,APPROVER非空)；`-1`=未审核(162行,APPROVER='~') |
| `FBOMTYPE` | NUMBER | NC 内部 BOM 类型码 | 观察值仅 `1`(1012行,主流"生产BOM") / `2`(4行,历史"包装BOM"草稿，**不可靠，勿用作层级判别**) |
| `VBILLCODE` | VARCHAR2 | 单据编号/名称（含公司名+物料code+"生产BOM"/"包装BOM"+版本号的拼接文本） | 如 `'加拿大皇家妙克无限责任公司 CF0092 生产BOM 1.3'` |
| `VBILLTYPE` | VARCHAR2 | 单据类型码 | 恒为 `'19B1'` |
| `HVCHANGERATE` | VARCHAR2 | 头级用量换算比 `"输出/输入"` | 如 `'1/1'`、`'1000/1'` |
| `HNPARENTNUM`/`HNASSPARENTNUM` | NUMBER | 头级基本产出数量（分子/分母） | 配合 `HVCHANGERATE` |
| `HCMEASUREID`/`HCASSMEASUREID` | VARCHAR2 | 母件计量单位 → `BD_MEASDOC.PK_MEASDOC` | 如 `'0001Z0100000000000XI'` = `KGM`(千克) |
| `TS` | CHAR | **增量水位**（始终有值） | `'2019-09-04 20:38:30'` 格式字符串 |
| `MODIFIEDTIME` | VARCHAR2 | 仅二次修改后有值，新建未改则 NULL | 不适合做水位主字段 |
| `PK_ORG`/`PK_ORG_V` | CHAR | 组织 | 6个不同值，主力2个 |
| `APPROVER` | VARCHAR2 | 审批人 | `'~'`=空值占位符（NC 惯例，非 NULL） |
| `HVDEF1..HVDEF20` | VARCHAR2 | 自定义字段 | 样本几乎全 `'~'`，个别头有值（如 `HVDEF1` 存过工艺文件编号） |

### 样本 3 行（原始，未脱敏——业务数据非密钥）

```python
{'APPROVER': '~', 'BILLMAKER': '1001A11000000000HR92', 'BKITITEM': 'Y', 'CBOMID': '1001A11000000000R3PP',
 'CREATIONTIME': '2019-09-04 18:22:59', 'DR': 1, 'FBILLSTATUS': -1, 'FBOMTYPE': 1, 'HCMATERIALID': '1001A11000000000QWSJ',
 'HNASSPARENTNUM': 1, 'HNPARENTNUM': 1, 'HVCHANGERATE': '1/1', 'HVERSION': '4.0', 'PK_ORG': '0001A11000000000348U',
 'TMAKETIME': '2019-09-04 18:22:59', 'TS': '2019-09-04 20:38:30', 'VBILLCODE': '集团总部 B101701 生产BOM 4.0', 'VBILLTYPE': '19B1'}

{'APPROVER': '~', 'CBOMID': '1001A11000000000R3QN', 'FBILLSTATUS': -1, 'FBOMTYPE': 1, 'HCMATERIALID': '1001A11000000000QWSL',
 'HVDEF1': 'Q/HFR-03-YF-136-A2', 'HVERSION': '3.0', 'MODIFIEDTIME': '2019-09-04 18:31:28', 'TS': '2019-09-04 20:38:30',
 'VBILLCODE': '集团总部 B103601 生产BOM 3.0', 'VBILLTYPE': '19B1'}

{'APPROVER': '~', 'CBOMID': '1001A11000000000R4DX', 'FBILLSTATUS': -1, 'FBOMTYPE': 1, 'HCMATERIALID': '1001A11000000000QWSH',
 'HNPARENTNUM': 1000, 'HVCHANGERATE': '1000/1', 'HVERSION': '1', 'TS': '2019-09-09 13:06:54',
 'VBILLCODE': '集团总部 B101601 生产BOM 1', 'VBILLTYPE': '19B1'}
```

---

## 表 2：`NCSC.BD_BOM_B`（BOM 子件行，实测 10169 行）

### 全列清单（87 列）

```
BATPCHECK,BBCHKITEMFORWR,BBISFEATURE,BBSTEPLOSS,BBUNIBATCH,BCANREPLACE,BCFEATURECLASSID,BCFEATURECODE,
BCUSTOMMATERIAL,BDELIVER,BISCHOICE,BKITMATERIAL,BMAINMATERIAL,BMIXEDMATERIAL,BOUTSOURCE,BPROJECTMATERIAL,
BUPINT: 全部 CHAR('Y'/'N' 标记位)
CASSMEASUREID,CBEGINPERIOD,CBOM_BID,CBOMID,CCUSTOMERID,CENDPERIOD,CMATERIALID,CMATERIALVID,CMEASUREID,
CNUMFEATURE,CPRODUCTORID,CPROJECTID,CVENDORID: VARCHAR2/CHAR
DR:NUMBER; FBACKFLUSHTIME,FBACKFLUSHTYPE,FCONTROL,FITEMSOURCE,FITEMTYPE,FREPLACETYPE,FSUPPLYMODE: NUMBER
IBASENUM,ILEADTIMENUM,NASSITEMNUM,NBFIXSHRINKASTNUM,NBFIXSHRINKNUM,NDISSIPATIONUM,NITEMNUM: NUMBER
PK_GROUP,PK_ORG,PK_ORG_V:VARCHAR2; TS:CHAR
VCHANGERATE,VCONFIGVERSION,VDEF1..VDEF20,VFREE1..VFREE10,VITEMVERSION,VMATINGNO,VNOTE,VPACKVERSION,
VROWNO,VSELECTCOND: VARCHAR2
```

### 关键字段说明

| 列名 | 类型 | 含义 | 观察值 |
|---|---|---|---|
| `CBOM_BID` | CHAR(20) | 行主键 | 唯一，作为 `nc_source_pk` |
| `CBOMID` | CHAR(20) | fk → `BD_BOM.CBOMID` | |
| `CMATERIALID` | VARCHAR2 | **子件/组件物料** → `BD_MATERIAL.PK_MATERIAL` | `CMATERIALVID` 样本中恒等于 `CMATERIALID`，可忽略 |
| `IBASENUM` | NUMBER | 基本用量分母 | 样本恒为 `1` |
| `NITEMNUM` / `NASSITEMNUM` | NUMBER | 用量（每头件用量） | 样本中两者恒相等，直接取 `NITEMNUM` 作为 `qty_per` |
| `VCHANGERATE` | VARCHAR2 | 用量换算比 `"分子.00/分母.00"` | 样本恒为 `'1.00/1.00'` |
| `NBFIXSHRINKNUM` / `NBFIXSHRINKASTNUM` / `NDISSIPATIONUM` | NUMBER | **候选损耗率字段** | **全表 10169 行无一行非空非零** —— 本实例未使用 |
| `CBEGINPERIOD` / `CENDPERIOD` | CHAR | **行级生效窗口**（BD_BOM 头没有此字段） | `'2999-12-31 23:59:59'`/`'3000-01-01 12:59:59'` 表示长期有效 |
| `CMEASUREID` / `CASSMEASUREID` | VARCHAR2 | 组件计量单位 → `BD_MEASDOC.PK_MEASDOC` | |
| `BMAINMATERIAL` | CHAR | 主料标记 | 样本恒 `'N'` |
| `BCANREPLACE` | CHAR | 是否允许替代（关联 `BD_BOM_REPL`） | 样本恒 `'N'`（替代关系罕见） |
| `VROWNO` | VARCHAR2 | 行号（字符串如 `'10'`,`'20'`） | Task5 `line_no` 需转 int |
| `TS` | CHAR | 增量水位 | 同头表格式 |

### 样本 3 行

```python
{'CBOM_BID': '1001A11000000000R3PQ', 'CBOMID': '1001A11000000000R3PP', 'CMATERIALID': '1001A11000000000QWTE',
 'IBASENUM': 1, 'NASSITEMNUM': 5000, 'NITEMNUM': 5000, 'CBEGINPERIOD': '2019-09-01 00:00:00',
 'CENDPERIOD': '2999-12-31 23:59:59', 'BMAINMATERIAL': 'N', 'BCANREPLACE': 'N', 'VCHANGERATE': '1.00/1.00',
 'TS': '2019-09-04 20:38:30', 'VROWNO': '10'}

{'CBOM_BID': '1001A11000000000R3PR', 'CBOMID': '1001A11000000000R3PP', 'CMATERIALID': '1001A11000000000QWSW',
 'NASSITEMNUM': 221, 'NITEMNUM': 221, 'VROWNO': '20', 'TS': '2019-09-04 20:38:30'}

{'CBOM_BID': '1001A11000000000R3PS', 'CBOMID': '1001A11000000000R3PP', 'CMATERIALID': '1001A11000000000QWSY',
 'NASSITEMNUM': 1, 'NITEMNUM': 1, 'VROWNO': '30', 'TS': '2019-09-04 20:38:30'}
```

---

## 表 3：`NCSC.BD_BOM_REPL`（替代料，实测 664 行）——`bom_substitutes` 源表

### 全列清单（44 列，节选关键列，其余为 VDEF1-20/VFREE1-10 自定义列样本恒 `'~'`）

```
CBOM_BID:CHAR; CBOM_REPLACEID:VARCHAR2; CREPLMATERIALOID:VARCHAR2; CREPLMATERIALVID:VARCHAR2;
IREPLORDER:NUMBER; DR:NUMBER; PK_GROUP/PK_ORG/PK_ORG_V:VARCHAR2; TS:CHAR;
VREPLACEINDEX:VARCHAR2; VROWNO:VARCHAR2; VNOTE:VARCHAR2
```

| 列名 | 含义 |
|---|---|
| `CBOM_BID` | fk → `BD_BOM_B.CBOM_BID`（哪条 BOM 行允许被替代） |
| `CBOM_REPLACEID` | 本行主键 |
| `CREPLMATERIALOID` | **替代物料** → `BD_MATERIAL.PK_MATERIAL` |
| `VROWNO` | 顺序（对应 `bom_substitutes.priority`） |
| `VREPLACEINDEX` | 换算比例字符串，样本恒 `'1.00/1.00'` |

### 样本 2 行

```python
{'CBOM_BID': '1001A11000000000R3Q2', 'CBOM_REPLACEID': '1001A11000000000R3S2', 'CREPLMATERIALOID': '1001A11000000000QWSS',
 'VREPLACEINDEX': '1.00/1.00', 'VROWNO': '10', 'TS': '2019-09-04 20:38:29'}
{'CBOM_BID': '1001A11000000002ZAK8', 'CBOM_REPLACEID': '1001A1100000000GTFCH', 'CREPLMATERIALOID': '1001A110000000011NLH',
 'VREPLACEINDEX': '1.00/1.00', 'VROWNO': '10', 'TS': '2020-02-29 21:20:03'}
```

---

## 物料分类字典 `NCSC.BD_MARBASCLASS`（全量 11 行）——编码前缀↔分类对照

| CODE | NAME(中) | NAME2(英) | 对应物料编码前缀 |
|---|---|---|---|
| 01 | 原材料（CR) | Ingredient | `CR` |
| 0101 | (子类)Raw Milk | | `CR`(生牛乳类) |
| 0102 | (子类)Raw Ingredient | | `CR`(其他原料) |
| 02 | 包装物(CP) | Packaging Material | `CP` |
| 03 | 标准化奶(CM) | Standardized Milk | `CM` |
| 04 | 粉仓粉(CS) | Storage Silo Powder | `CS` **以及 `CW`（干混粉与 CS 共用同一分类，仅靠编码前缀区分）** |
| 05 | 产成品(CF) | Finished Products | `CF`（另观察到 `S0xxx` 前缀成品，同样挂 05 类，命名不完全统一） |
| 06/07/08/98/99 | Chemical/Mechanical/Laboratory/Fee/Test | | 与 BOM 级联无关 |

**这证实了结论 3**：`PK_MARBASCLASS` 这一 NC 结构化分类字段**也不能区分制粉(CS)与干混(CW)**——两者物理上共用同一个 `04` 分类节点。唯一可用的区分信号是 `BD_MATERIAL.CODE` 的**前 2 位字母前缀**，这是财唯（本公司）的编码约定，不是 NC 系统字段。

---

## 级联验证：真实成品 3 层链路全量数据

**成品**: `CF0092` "700g(251)牛芮思_1婴儿奶粉"（`BD_MATERIAL.PK_MATERIAL=1001A1100000003G6F45`，`select pk_material,code,name from NCSC.BD_MATERIAL where code='CF0092'` 实测复核）

### Layer 3（顶层/包装层，查询成品直接看到的 BOM）——`CF0092` 自身的 BD_BOM（CBOMID=`1001A1100000003GZAG6`，v1.2，已审核）

| 组件 | 名称 | 用量 |
|---|---|---|
| CW0001 | 251-DryBlending powder(103) | 420 |
| CP0115 | 700g-芮思1（251）罐体 | 610 |
| CP0116 | 700g-芮思1（251）包装箱 | 105 |
| CP0114 | 盖-红色带9毫升勺（251/253） | 610 |
| CP0110 | 二维码标签(北美版) | 610 |
| CP0113 | 包装箱标签-芮思1（251） | 105 |
| CP0105 | Tin Can End - 4.969in | 610 |

→ 组件含半成品粉 `CW0001`（干混粉）+ 全部包装物 `CP*`，验证"查成品只见到一张 BOM，其中含半成品粉"。

### Layer 2（干混层，可选）——`CW0001` 自身的 BD_BOM（CBOMID=`1001A1100000003GYV54`，v1.1，已审核）

| 组件 | 名称 | 用量 |
|---|---|---|
| CS0026 | 251基粉半成品 | 999.45 |
| CR0031-2 | Pre-mixed Nucleotides (II) | 0.55 |

→ 干混粉的 BOM 消耗粉仓粉 `CS0026` + 少量原料预混料，产出干混粉。**验证"半成品粉再挂干混/制粉 BOM"。**

### Layer 1（制粉层，底层）——`CS0026` 自身的 BD_BOM（CBOMID=`1001A1100000003H7P7I`，v1.6，已审核）

| 组件 | 名称 | 用量 |
|---|---|---|
| CR0059 | Pasteurized Milk | 1911.503 |
| CR0044 | Calcium hydrogen phosphate | 3 |
| CR0074 | 251A0 Pre-mixed Minerals | 2 |
| CR0169 | Potassium chloride (II) | 5 |
| CR0043 | Sodium citrate | 3 |
| CR0073 | 251A0 Pre-mixed Vitamins | 3 |
| CR0036 | Hydrogen choline tartrate | 2 |
| CR0075 | 251A0 Pre-mixed Nutrients | 1 |
| CR0024 | Mixed vegetable oil (OPO-10372) | 270 |
| CR0077 | DHA (oil form) | 1.3 |
| CR0079 | Vitamin E Oil | 0.11 |
| CR0078 | ARA (oil form) | 1.7 |
| CR0025-1 | Lactose (Hilmar) | 436 |
| CR0032 | GOS Syrup | 80 |
| CR0040 | Whey Protein Concentrate (WPC80) | 75 |
| CR0033 | Calcium Carbonate | 3 |

→ 粉仓粉的 BOM 消耗巴氏杀菌乳 + 多种原料/预混料，无更下层 BOM（`CR*` 均为外购原料，叶子节点）。**链路到此终止，验证"制粉/干混 BOM 到原料"。**

**完整链路**：`CF0092(成品)` → BOM消耗`[CW0001(干混粉)+6种CP包装物]` → `CW0001` BOM消耗`[CS0026(粉仓粉)+CR0031-2]` → `CS0026` BOM消耗`[16种CR原料，主要为巴氏杀菌乳]` → 无更下层。**三层级联完全打通，且证实"干混层可选"**（对照组：`CF0041`"25kg 251"的 BOM 直接消耗 `CS0026`，跳过 `CW` 层，两条产品线共用同一张 Layer1 制粉 BOM）。

---

## 字段 → Task 5 规范化模型映射表

### `boms`（← `BD_BOM`）

| Task5 目标列 | NC 源字段 | 转换规则 |
|---|---|---|
| `product_material_code` | `HCMATERIALID` | join `BD_MATERIAL.PK_MATERIAL` → `CODE` |
| `bom_type` | **无直接源字段** | 由 `product_material_code` 前缀派生：`CS*`→`milling`；`CW*`→`drymix`；`CF*`/`S0*`(等其他成品前缀)→`packaging`；需 transform 层维护前缀映射表，未匹配前缀→`'unknown'`（不猜测） |
| `version` | `HVERSION` | 原样字符串 |
| `factory_code` | `PK_ORG` | 若需要人类可读工厂码，需另建 `PK_ORG`→工厂码映射（本次未展开，6 个 org pk，Phase1 前需求确认） |
| `status` | `FBILLSTATUS` | `1`→`'approved'`；`-1`→`'draft'`；其他未知值（未观察到）→`'inactive'` |
| `effective_from`/`effective_to` | **头表无此字段** | 需从关联的 `BD_BOM_B` 行级 `CBEGINPERIOD`/`CENDPERIOD` 聚合（如取该 BOM 下所有行的最小 begin / 最大 end），或干脆不在头表落日期，改在行级维护——**建议行级落日期，头表 `effective_*` 置空或取行级 MIN/MAX 作展示用途** |
| `yield_rate` | `HVCHANGERATE`（如 `'1000/1'`） | 解析 `分子/分母` 为 Decimal（如 `1000/1`=1000；`'1/1'`=1） |
| `nc_source_pk` | `CBOMID` | 唯一索引 |

### `bom_lines`（← `BD_BOM_B`）

| Task5 目标列 | NC 源字段 | 转换规则 |
|---|---|---|
| `bom_id` | — | FK，同步时按 `nc_source_pk`(=CBOMID) 关联头表 |
| `line_no` | `VROWNO` | 字符串转 int（`'10'`→10） |
| `component_material_code` | `CMATERIALID` | join `BD_MATERIAL.PK_MATERIAL` → `CODE` |
| `qty_per` | `NITEMNUM`（样本中恒等于 `NASSITEMNUM`） | 直接取用（`IBASENUM`/`VCHANGERATE` 样本恒为 1:1，不需再除） |
| `uom` | `CMEASUREID` | join `BD_MEASDOC.PK_MEASDOC` → `CODE`（如 `KGM`） |
| `scrap_rate` | `NBFIXSHRINKNUM`/`NDISSIPATIONUM` | **全表恒 NULL，落默认值 0**；不要假设该字段会有数据 |
| `nc_source_pk` | `CBOM_BID` | 唯一索引 |
| （建议附加，非 Task5 现有列）行级生效窗口 | `CBEGINPERIOD`/`CENDPERIOD` | 若 Task5 决定采纳行级日期而非头级，需在模型中补 `effective_from`/`effective_to` 到 `bom_lines` 而非 `boms` |

### `bom_substitutes`（← `BD_BOM_REPL`）

| Task5 目标列 | NC 源字段 | 转换规则 |
|---|---|---|
| `bom_line_id` | `CBOM_BID` | FK，按 `nc_source_pk`(=CBOM_BID) 关联 `bom_lines` |
| `substitute_material_code` | `CREPLMATERIALOID` | join `BD_MATERIAL.PK_MATERIAL` → `CODE` |
| `priority` | `VROWNO` | 字符串转 int |
| `mode` | **无源字段** | 固定落 `'suggest'`（Task5 计划默认值），NC 无主动/建议区分字段 |

---

## 与 `BD_MATERIAL` 的关联方式

- `BD_BOM.HCMATERIALID` = `BD_MATERIAL.PK_MATERIAL`（直接等值 join，均为 CHAR(20) 定长编码，如 `1001A1100000003H56DE`）
- `BD_BOM_B.CMATERIALID` = `BD_MATERIAL.PK_MATERIAL`（同上）
- `HCMATERIALVID`/`CMATERIALVID`（版本化物料 id）在样本中对头表母件多为 `'~'`（未启用版本管理），对行表子件恒等于 `CMATERIALID` 本身——**可安全忽略，只用非 V 后缀的 ID 列**。
- `BD_MATERIAL` 本身：`CODE`（如 `CF0092`）、`NAME`、`PK_MARBASCLASS`（分类 fk，见上表，但不足以区分制粉/干混）。全表 2590 行，`MATERIALTYPE` 列全部为 NULL，不可用。

---

## 增量水位与审批状态字段小结（brief 明确要求单独列出）

- **增量水位**：`BD_BOM.TS` / `BD_BOM_B.TS` / `BD_BOM_REPL.TS`（CHAR，`'YYYY-MM-DD HH24:MI:SS'`，始终有值，可靠）。`MODIFIEDTIME` 仅二次编辑后才非空，不适合做同步游标。
- **审批状态**：`BD_BOM.FBILLSTATUS`（NUMBER）。观测值：`1`=已审核（1016 个头里 854 个）、`-1`=未审核/草稿（162 个）。已审核行 `APPROVER` 列同时非空（NC 用 `'~'` 表示空值占位符，非 SQL NULL）。

---

## 未决问题 / Task 4-5 需注意的风险点

1. `boms.bom_type` 派生逻辑（前缀映射表）需要维护在代码里而非从 NC 读取，前缀集合目前观察到 `CS`/`CW`/`CF`/`S0`（Canada 成品另用 `S0` 前缀，如 `S0093`/`S0102`），**新增产品线若引入新前缀会漏判**，建议 transform 报未识别前缀时记录 `bom_type='unknown'` 并计入同步结果的 `skipped`/`warnings`，不要静默吞掉。
2. 同一母件多版本已审核 BOM 并存（如 CS0026 的 7 个版本 1.0-1.6 全部 `FBILLSTATUS=1`），`GET /boms/effective` 的选版策略需要明确：本次样本中"取 `HVERSION` 数值最大"与"取行级 `CBEGINPERIOD` 最新覆盖查询日"结果一致，但两者语义不同，建议 Task 5 实现时选一种并写清楚原因（推荐 `HVERSION` 最大，逻辑更简单且与 NC UI 一致）。
3. `scrap_rate`/损耗率在 NC BOM 层完全空白——如果 Phase 1 引擎需要损耗率参与物料需求计算，需要另找数据源（生产工单实际投料记录，或转为手工维护字段），不能依赖本次镜像。
4. `factory_code` 尚无 `PK_ORG`→人类可读工厂码的映射表，Task 4/5 若需要该字段需另行调研 `BD_ORG` 或同等组织字典表（本次未纳入调研范围，因 brief 未明确要求）。
5. `BD_BOM_REPL`/`BCANREPLACE` 使用率很低（样本行全 `'N'`，全库仅 664 条替代记录 vs 10169 条 BOM 行），`bom_substitutes` 镜像预期是稀疏表，属正常现象不是同步遗漏。
