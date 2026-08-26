# NC65 主数据直读 Survey — material / supplier / person / UOM 换算

日期：2026-08-25 ｜ 实测环境：`ncsc@10.10.95.67:1521/ORCL`（只读 select，未写入）

本文是 `2026-08-25-nc-sync-scheduler-design.md` 的事实依据。
**表名/列名以本文为准，不以任何模块的 SQL 为准**（沿用 `2026-08-03-nc-bom-survey.md` 的约定）。

## 0. 最重要的结论

`http://10.10.95.66/firmusData/touch_mdm_mes/*` 这个 webapi **不是飞鹤集团 MDM 中台，
它就是本套 NC65 库的一个只读视图**。仓库根目录的《主数据与MES数据集成接口文档v1.1》是集团
通用模板（示例里是 12 位集团料号、北京/克东公司），与本实例返回的数据无关。

证据：

| 证据 | 数值 |
|---|---|
| 镜像料号编码体系 | `M0438` / `CR0297`，与 `NCSC.BD_MATERIAL.CODE` 同一套 |
| raw_payload 透出 NC 原生 PK | `pk_material` / `pk_supplier` / `pk_psndoc`（20 位 NC PK） |
| raw_payload 带 `rn` | Oracle `ROW_NUMBER()` 残留 → 服务端就是一条 NC SQL |
| 行数 | 物料 镜像 2573 / NC 启用 2594 / NC 全量 2613 |
| | 供应商 镜像 1177 / NC 启用 1176 / NC 全量 1188 |
| | 人员 镜像 149 / NC 启用 155 / NC 全量 293 |
| `accounting_group` | `0102` → `Raw Ingredient`，精确等于 `BD_MARBASCLASS.CODE/NAME` |

**因此改直读 NC 是同源换取数方式，不是换数据集。** 镜像表结构与下游消费方可以零改动。

## 1. 贯穿全局的规律：webapi 只取 ENAME

三类主数据的"名称/描述"字段，webapi **一律取 `ENAME`（英文名），且不回落 `NAME`**；
`ENAME` 为 NULL 时该 key 在 JSON 里直接消失。

实测对照：

| NC 行 | `NAME` | `ENAME` | webapi 返回 |
|---|---|---|---|
| `BD_MATERIAL` CR0297 | `OPO 6224` | `OPO 6224` | `description: "OPO 6224"` |
| `BD_MATERIAL` M0438 | `EasyTempTMR33-U1ABDBAX1AAA` | NULL | **无 `description` key** |
| `BD_SUPPLIER` 0000426 | `Modern Niagara` | `Modern Niagara Ottawa Inc` | `suppliername: "Modern Niagara Ottawa Inc"` |
| `BD_MEASDOC` KGM | `千克` | `Kilograms` | `unitname_meas: "Kilograms"` |

这是 `erp_materials.description` 大量为空的根因。直读后改用 `ENAME ?? NAME` 可以补上，
属于**有意的口径改进**（见设计文档 §5）。

## 2. Material — `NCSC.BD_MATERIAL`（2613 行，`nvl(DR,0)=0`）

全部 2613 行 `PK_ORG = '0001A1100000000034BL'`（单组织，无需按 org 过滤）。

| 镜像列 | NC 来源 | 状态 |
|---|---|---|
| `erp_part_no` | `BD_MATERIAL.CODE` | ✅ 已验证 |
| `description` | `BD_MATERIAL.ENAME`（直读后改 `ENAME ?? NAME`） | ✅ 已验证 |
| `unit_meas` | `BD_MEASDOC.CODE` via `PK_MEASDOC` | ✅ 已验证 |
| `part_status` | `BD_MATERIAL.ENABLESTATE`（2=启用 3=停用） | ✅ 已验证 |
| `weight_gross` | `BD_MATERIAL.UNITWEIGHT` | ✅ |
| `volume` | `BD_MATERIAL.UNITVOLUME` | ✅ |
| `exp` | `BD_MATERIALSTOCK.QUALITYNUM`（CR0297=24，精确命中） | ✅ 已验证 |
| `weight_net` / `dim_quality` / `part_product_family` | **webapi 从来不返回**，镜像里 100% NULL | ✅ 确认为死列 |
| `item_mes_type` | ⚠️ **未定** —— CR0297=0，M0438=9 | ❌ 见 §6 |

`ENABLESTATE` 分布：`2` → 2594 行，`3` → 19 行。无其它取值。

`BD_MARBASCLASS`（物料基本分类）带层级 `PK_PARENT`：`0102 Raw Ingredient` 的父级是 `01 原材料（CR)`；
`07 Mechanical` 无父级。webapi 的 `accounting_group` / `accounting_group_name` 取的是叶子节点的 `CODE` / `NAME`。

## 3. Supplier — `NCSC.BD_SUPPLIER`（1188 行）

| 镜像列 | NC 来源 | 状态 |
|---|---|---|
| `erp_supplier_code` | `BD_SUPPLIER.CODE` | ✅ |
| `supplier_name` | `BD_SUPPLIER.ENAME ?? NAME` | ✅ 已验证 |
| `supplier_address` | `BD_ADDRESS.DETAILINFO` via `BD_SUPPLIER.CORPADDRESS` | ✅ 已验证（0000426 精确命中） |
| `supplier_type` | `BD_SUPPLIER.SUPPROP`（0000426 = `0`，与 webapi `suppliertype: 0` 一致） | ✅ |
| `supplier_tel` / `supplier_fax` | **webapi 从来不返回**（NC 侧 `TEL1`/`FAX1` 实测为 NULL） | ✅ 确认为死列 |

`BD_ADDRESS` 可用列：`CODE, COUNTRY, CITY, PROVINCE, DETAILINFO, POSTCODE, VSECTION, PK_ADDRESS`。
直读后可顺带补 city/province/postcode（本轮不做，避免扩大范围）。

## 4. Person — `NCSC.BD_PSNDOC`（293 行）

| 镜像列 | NC 来源 | 状态 |
|---|---|---|
| `erp_person_code` | `BD_PSNDOC.CODE` | ✅ |
| `person_name` | `BD_PSNDOC.NAME`（人员表无 `ENAME` 列） | ✅ 已验证 |
| `is_valid` | `BD_PSNDOC.ENABLESTATE`（2 → `'1'`） | ✅ |
| `pk_psndoc` | `BD_PSNDOC.PK_PSNDOC` | ✅ |
| `department_code` / `department_name` | `ORG_DEPT.CODE` / `.NAME` via `BD_PSNJOB.PK_DEPT`，取 `ISMAINJOB='Y'` | ✅ 已验证（100100 → `0104` / `Production`） |
| `company_code` / `company_name` | ⚠️ **未定** —— webapi 给 `10024` / `Canada Royal Milk ULC`，`ORG_ORGS` 给 `01010104` / `加拿大皇家妙克无限责任公司` | ❌ 见 §6 |

⚠️ `BD_PSNDOC` **没有 `ENAME` 列**（第一次 survey 查询在此 ORA-00904）。可用姓名列：
`NAME` / `LASTNAME` / `FIRSTNAME` / `NICKNAME` / `SHORTNAME` / `USEDNAME` / `NAME2..NAME6`。

⚠️ 一个人可以有多条 `BD_PSNJOB`。**必须按 `ISMAINJOB='Y'` 取主职**，否则同一人会产生多行。

## 5. UOM 换算 — 现有模型与 NC 不兼容

现状：`uom_conversions` 表 **0 行**，`erp_sync_state` 里**没有** `uom_conversion` 那条状态记录，
即 `POST /uom-conversions/sync` **从未成功跑过一次**。全仓消费方 grep 只命中 `mdm-api/app/main.py`
的模型注册，**没有任何真实读取方**。

NC 侧真实结构 —— `NCSC.BD_MATERIALCONVERT`（2101 行）：

```
PK_MATERIAL          -- 物料级！
PK_MEASDOC           -- 辅助计量单位
PK_APARTMEASDOC
MEASRATE             -- 换算率，字符串分数形式："1/1" "3.6/1" "4.8/1" "5.4/1"
ISPUMEASDOC / ISSTOCKMEASDOC / ISSALEMEASDOC / ...
```

与我们的 `uom_conversions(from_uom, to_uom, rate)` + `UNIQUE(from_uom,to_uom)` 冲突：

| 指标 | 实测 |
|---|---|
| 不同 (辅助单位, 主单位) 对 | 19 |
| 其中同一对存在**多个不同换算率**的 | **4 对** |
| 最严重的一对 | `PIECES → KGM`：**9 种不同换算率**，覆盖 48 条物料 |

**结论：强行聚合成全局表会静默给出错误换算率。** 本轮删除这条死链路（见设计文档 §7）。

## 6. 遗留未定项（实施 Task 1 收尾）

这三项都只影响 `raw_payload` 或单一列，不阻塞整体设计：

1. **`item_mes_type`** —— CR0297=`0`，M0438=`9`。候选来源：`BD_MATERIALSTOCK.MARTYPE`（CR0297=`'PR'`）、
   `BD_MARBASCLASS` 层级深度、某个 `DEF*` 槽位。**有真实消费方**
   （`material_sync.py` → `parts.erp_item_type`），必须查清，不能留空。
2. **`itemtype`** —— CR0297=`43`，M0438 无此 key。仅存在于 `raw_payload`，无结构化消费方。
3. **`company_code` / `company_name`** —— webapi 的 `10024` / `Canada Royal Milk ULC` 与
   `ORG_ORGS` 对不上，需确认是 `ORG_ORGS.ENAME` + 另一套编码，还是 `BD_PSNJOB`/HR 组织。
4. **`itemvalidityunit`** —— CR0297=`M`、M0438=`J`，NC 侧 `BD_MATERIALSTOCK.QUALITYUNIT` 是数字（CR0297=`1`）。
   需要确认字母映射表。

## 7. 复现方式

```bash
# 连接信息取自仓库根 .env 的 NC65_*（注意值里有前后空格，需 trim）
NCPW=$(grep '^NC65_PASSWORD=' .env | cut -d= -f2- | tr -d ' \r')
./finance-api/.venv/Scripts/python.exe <script> "$NCPW"
```

`finance-api/.venv` 是本仓库唯一装了 `oracledb` 的 venv。全部查询为只读 `select`。
