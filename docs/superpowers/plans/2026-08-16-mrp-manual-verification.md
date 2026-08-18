# MRP 点验清单（第三～六轮：最小批量 / 周起始日 / 锁定区 / 版本模型 / 差异视图 / 采购建议）

本地环境已备好，**这份清单是给浏览器点验用的**。每条都写了「怎么点」「该看到什么」「看到什么算 bug」。
看到不对的直接告诉我条目编号即可。

## 0. 环境（已就绪，无需你操作）

| 项 | 值 |
| --- | --- |
| MRP 前端 | http://localhost:5179 |
| 登录 | `admin@epms.local` / `DevTest2026!` |
| 集成分支 | 主 checkout `c:/Project/uniops` 上的 `test/mrp-1c-local`（= origin/main + 六个功能分支） |
| 迁移 | `mrp11` / `mrp12` / `mrp13` 已跑，单 head |
| 已验证 | 新路由在线、前端跑的是新代码、DB 新列存在、采购建议端到端出 606 行 + 真 xlsx |

**点验完成后如何还原环境**：
```bash
cd c:/Project/uniops && git switch --detach 1460f48    # 回到原来的 detached HEAD
docker restart uniops_mrp_api uniops_mdm_api uniops_mrp_frontend
```
（`test/mrp-1c-local` 是临时集成分支，**不要发布它**；要发布的是六个 feature 分支。）

---

## ★先看这两条（我在环境里发现的，需要你判断）

### V0-1 包材损耗率现在是 5%，按你们的规则应该是 0

打开 **Capacity Rules** 页 → 底部 **Loss Rates**。

- 现在：原料 2%、**包材 5%**。
- 规则（2026-08-04 你定的）：**包材率必须是 0**，因为部分包装 BOM 已把损耗写进用量了（S0093 700g×6：理论 600 罐，BOM 写 610）。再乘 5% 就是重复放大，会多买包材。
- **请确认**：这个 0.05 是谁设的、要不要改回 0。页面上那段黄色说明就是为防止这种情况写的。

### V0-2 供应商参数目前只有 2 行（我为冒烟测试灌的）

打开 **Supply Parameters** 页。现在只有 `CP0115-1 / SUP-PACK` 与 `CW0001 / SUP-POWDER` 两行 —— 是我测试时粘的假数据。
真实点验采购建议前，**要么删掉它们**、要么补上真实的提前期，否则建议单上 606 行里有 596 行是「无供应商」。

---

## A. 周起始日（周六→周五）

### A-1 设置项存在且能存
**Capacity Rules** 页 → **Week starts on**。
- 该看到：下拉里是 Monday…Sunday，当前值 **Saturday**（dev 库里已设成 5）。
- 改成 Monday 再改回 Saturday，两次都该出现绿色提示；若有检修周被平移，提示里会说「N maintenance week(s) moved」。
- **算 bug**：保存报错；或改完刷新又变回去。

### A-2 新计划的周真的是周六
**Production Plan** 页 → 选一个已确认的预测版本 → **Generate**。
- 该看到：矩阵的周列日期**全是周六**；周标签形如 `Sep W2 · Sep 12–18`（**不带 ISO 周号** —— 周六制下 ISO 周号会指错周，所以刻意去掉了）。
- **算 bug**：列还是周一；或标签里出现 `2026-W38` 这种 ISO 周号。

### A-3 老计划不会被改设置带跑
在版本选择器里挑一个**旧的** run（generate 之前就存在的）。
- 该看到：它的周列仍是**周一**，标签带 ISO 周号 —— 因为周起始日是**按 run 快照**的。
- **算 bug**：老计划的周列跟着新设置变了（那等于已发布的计划被偷偷重画）。

### A-4 检修周还能录进去
**Capacity Rules** 页 → Week exceptions → 新增一条，日期选一个**周六**。
- 该看到：能保存。
- **算 bug**：报「Must be a Monday」（这是我这轮修掉的坑，若复现说明修复没生效）。

---

## B. 最小生产批量

### B-1 按产品配置
**Capacity Rules** → 新增规则 → Scope 选 **Product** → 用物料选择器挑一个成品（如 `S0093`）→ Constraint 选 **Minimum lot size** → 填一个明显偏大的值（比如 20000 KG）。
- 该看到：能存；列表里 scope 显示为 Product。
- **算 bug**：Scope 里没有 Product；或选了 Product 却还要手打物料码。

### B-2 顶批量真的发生
回 **Production Plan** → Generate。找 B-1 那个产品。
- 该看到：某些周的产量被**顶到 20000**，格子 tooltip 写 `Net requirement X + Y minimum-lot surplus`；被超产抵掉的后续月显示 **covered**（灰色斜体）。
- **请你判断**：顶出来的量和「多做的货放到后面月份消化」这个做法，是不是你要的。**这是最需要你拍板的一条**。
- **算 bug**：顶到批量后，后续月份**还在重复生产**同样的货（说明结转没生效）。

### B-3 凑不够就不开工
把 B-1 的批量改成一个**大于全厂周产能**的数。
- 该看到：保存被拒（422），提示「minimum lot size … exceeds the factory max_output_qty …」。
- **算 bug**：能存进去（那样这个产品永远排不出来）。

---

## C. 锁定区

### C-1 灰化与不可点
**Production Plan** → 打开一个**发布之后再生成的**计划。
- 该看到：当月起 3 个月的列是**灰底**，鼠标悬停写 `Frozen — materials for this month are already purchased`；点这些格子**不弹调整抽屉**。
- **算 bug**：锁定区能点开调整（后端会 422，但不该让你点到那一步）。

### C-2 锁定区的量照抄上一版
对比新计划与它继承的那一版在锁定区内的数字。
- 该看到：**逐格相同**。
- **算 bug**：锁定区的数变了（料已经买了，计划不该动）。

---

## D. 生产计划版本

### D-1 刷新不再丢计划
**Production Plan** 打开任一计划 → 看地址栏应有 `?run=<id>` → **按 F5 刷新**。
- 该看到：回到**同一版**计划。
- **算 bug**：刷新后回到空白页（这是这轮要修的核心问题之一）。

### D-2 版本选择器
点顶部 **Version** 下拉。
- 该看到：按「Horizon from YYYY-MM」分组、组内新版在上；生效那版标 **Active**；被更新组接管的标 **Superseded** 并带锁图标。
- **算 bug**：历史版本根本不出现在列表里（找不到＝以为系统弄丢了）。

### D-3 切换生效版
在**同一组**内选一个非生效的已发布版 → 点 **Set as active** → 确认。
- 该看到：确认框写明「这会重写给采购的现行需求集」；确认后该版变 Active。
- 再选一个**旧组**的版本：**Set as active 按钮是灰的**，悬停提示「The plan group only moves forward」。
- **算 bug**：跨组能切（那会让采购少买一个月的料）。

### D-4 历史版只读
打开一个 **Superseded** 的计划。
- 该看到：顶部灰条「A newer plan group has taken over — this version is read-only」；没有 Recalculate / Confirm & Release 按钮。

---

## E. 版本差异

### E-1 差异叠加
**Production Plan** 打开一个**有上一版**的计划 → 点 **Compare**。
- 该看到：顶部蓝条 `vs MPS-… · N product(s) changed across M week(s) · net ±X`；变多的格子带绿 ▲、变少的带红 ▼，悬停写 `Was 30 → now 50 (+20)`。
- **算 bug**：明明两版不同却显示 identical。

### E-2 「整个挪走」的格子也要看得见
找一个**上一版有、这一版没有**的产品/周。
- 该看到：该格显示**划掉的旧数值**（红色删除线）。
- **算 bug**：那一行/那一格直接消失了 —— 这是差异视图最该抓住的变化。

### E-3 第一版没有可比对象
打开某组的**第一版**并点 Compare。
- 该看到：蓝条写「This is the first version of its horizon group — nothing to compare against」，**矩阵不该整片变绿**。

---

## F. 供应参数（新页）

### F-1 Excel 粘贴
**Supply Parameters** → **Paste from Excel** → 粘贴（用 Tab 分隔，可直接从 Excel 复制）：
```
CR0010	SUP-A	30	1000		yes
CR0024	SUP-B	45	500	25	yes
```
- 该看到：导入成功计数；表头计数里「缺提前期」「无主供应商」相应减少。
- 再粘一遍**同样的行但提前期改成 21**：该显示 `updated`，不是重复建行。
- **算 bug**：整批因为一行有问题而全部失败（应该是**逐行报错、好行照落**）。

### F-2 主供应商唯一
给**同一个物料**再粘一行、也勾 primary。
- 该看到：该行报错，文字可读（大意：这个物料已有另一个主供应商）；其余行照常保存。
- **算 bug**：500，或整批丢失。

---

## G. 采购建议（Phase 1C）

### G-1 生成
**Purchase Suggestions** → **Calculate**。
- 该看到：顶部计数条（组件数 / 行数 / 各类告警），表格**按下单日排序**。
- 我实测的基线：**606 行、61 个组件、1 个成品无 BOM、0 个取数失败**。
- **算 bug**：`bom_fetch_failed` 大于 0（说明 BOM 取数在报错，数字不完整、不能拿去下单）。

### G-2 数字口径（**最需要你判断的一条**）
挑一个你熟悉的原料，核对 `Gross / Available / Net / Suggested` 四个数。
- Gross = 展开后的毛需求（含损耗）；Available = WMS + 代储；Net = Gross − Available（**库存先满足最早的周，剩余结转**）；Suggested = Net 提到 MOQ 后再按订货倍数上取整。
- **请你判断**：这几个数量级对不对、损耗加得对不对。**算错就是买错，这条我无法替你验证。**

### G-3 告警可读
- 无供应商的行：Supplier 列显示红色 `none`。
- 无提前期的行：Supplier 后带红色 `?`，悬停说明「订单日期假设货物立即到达」。
- 下单日已过期的行：整行淡红底 + 日历图标。
- **算 bug**：这些行看起来和正常行一样（那就会被照着下单）。

### G-4 状态可标
把某行状态改成 **Ordered** → 勾选「Show only lines still to deal with」。
- 该看到：该行从列表消失（因为已处理）。

### G-5 导出
点 **Export**。
- 该看到：下载 `purchase-suggestions-PUR-….xlsx`；打开后按下单日排序，最后一列 **Attention** 是**文字**（如 `no supplier; no lead time`）而不是靠颜色；数据下方有一行说明：用了哪两个损耗率、**在途库存未计入**。
- **算 bug**：告警只有颜色没有文字（表格一转发/筛选就丢了）。

---

## 已知的、有意为之的局限（不是 bug，别当缺陷报）

1. **在途 / 已下单库存记 0** —— 还没接 NC 的未到货 PO。所以这些是**需求**，不是对已下单的净头寸。页面底部有写。
2. **损耗率默认 0**（当前 dev 是 2% / 5%，见 V0-1）。
3. **采购建议不生成 PR** —— 本轮定的是「看 + 导出，预留 PR 接口」，行上的 `pending/ordered/ignored` 就是那个预留位。
4. **历史附件在本地必 404/502** —— 本地 dev 的老毛病，与本次改动无关。
