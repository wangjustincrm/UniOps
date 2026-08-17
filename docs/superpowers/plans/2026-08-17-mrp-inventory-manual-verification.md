# Inventory — 浏览器点验清单

分支 `feature/mrp-inventory`（叠在 `feature/mrp-1c-prereqs` 上）。
入口：MRP 左侧栏新增 **Inventory**，在 Consignment Stock 下面。

本地 dev 已经带上这套代码（`uniops_mrp_api` / `uniops_mrp_frontend` / `uniops_mdm_api`
三个容器挂的都是这个 worktree），刷新即可。

---

## 先说三件你会先注意到的事

**1. 原奶（NC 分类 0101）在这个页面上完全看不到。** 27 个物料、包括
`CR0059 Pasteurized Milk`，库存和在途两边都不计。页面顶部有一行说明，
不是 bug。判定按 **NC 物料基本分类**，不是按编码前缀 —— `CR0059` 顶着
原料前缀却是 0101，前缀规则两头都会错。

**2. "预计到货"这一列现在是空的。** ERP 里 `PO_ORDER_B.DPLANARRVDATE`
4,890/4,890 行都有值，同步代码也写好了，但**还没跑过一次全量 NC 同步**，
所以 `po_line_items.planned_arrival_date` 目前是 0 行有值。见文末"已知缺口"。

**3. 数字已经和 SQL 对过账了**，14 项交叉核对全过（在途 87 行 / 71 物料 /
902,779；Aging 五档 92 / 11 / 11 / 95 / 334）。所以如果你看到的数字和下面
对不上，那是真出问题了，值得报。

---

## A. Lots —— 批次检索

进 Inventory，默认落在 **Lots** 标签，物料类别默认 **0102 Raw Ingredient**。

- [ ] **A1** 表格出数，右上角显示"N matching lot(s)"。
- [ ] **A2** 搜索框输 `lactose` —— 应该按**物料名称**命中（不只是编码）。
      再试 `CR0025`（编码）、一个批次号、一个供应商批次号，四种都该能搜到。
- [ ] **A3** 把类别切到 **All classes**，数量应明显变大；切到
      **02 Packaging Material**，Shelf life 那一列应该大片显示 **no expiry**
      —— 包材不过期，这是对的。
- [ ] **A4** 点表头 Material / Lot / Qty / Expiry / Received 排序，再点一次反向。
- [ ] **A5** 翻到第 2 页再翻回第 1 页，**不该出现同一个批次在两页都有**。
      （这条专门修过：同一物料的批次排序值全相等，没有稳定次序会串页。）
- [ ] **A6** 已过期的行有淡红底，Shelf life 显示 `N d past`；未过期显示 `N d left`。
- [ ] **A7** 底部有 `1–25 of N · as of <日期>`。**as of 是服务端算的日期** ——
      如果它和你的今天不一致，说明服务器时区有问题，值得报。

## B. Aging —— 保质期预警

切到 **Aging** 标签。

- [ ] **B1** 五张卡片：Expired / Under 30 days / 30 to 60 / 60 to 180 / Over 180，
      默认选中 **Expired**。在 0102 类别下数字应该是
      **92 / 11 / 11 / 95 / 334**（批次数）。
- [ ] **B2** 逐个点五张卡片，下面的表跟着换，**表里的行数要和卡片上的数字一致**。
      这是最值得点的一条：卡片和表格是两条代码路径读同一个定义，
      我第一版写的时候有三个边界差一天（卡片说 92、表里 91，还不报错），
      已经改成由服务端解析同一套阈值，并加了跨 560 天的一致性测试。
- [ ] **B3** 五张卡片的数字相加 = **543**，等于 0102 类里有保质期的批次总数。
      （不重不漏。）
- [ ] **B4** 类别切到 **02 Packaging Material**：五张卡片基本都是 0，
      下面会出现一行灰底提示 **"N lot(s) 没有保质期，不在任何档里 —— 包装和
      硬件不过期"**。这一行必须有 —— "排除了多少"和"一个都没有"不能长得一样。
- [ ] **B5** Days 列：过期的是负数且红字，30 天内的是橙字。

## C. Materials —— 物料汇总（含在途）

切到 **Materials** 标签，类别切到 **All classes**。

- [ ] **C1** 一行一个物料：On hand / Available / On hold / Expired /
      Next expiry / On order / Arriving。
- [ ] **C2** 勾上 "Hide materials with no stock and nothing on order" 前后，
      总数应该变化。
- [ ] **C3** 找一个 **On order 不为 0** 的物料（全库有 **71** 个）。
      那个数字是**蓝色可点的**，后面括号写着几张 PO。点开：
      - 展开的表列出 PO 号、供应商、订购量、已收、**Still owed**、Expected
      - **Still owed 那一列加起来必须等于上面那个 On order 数字**
      - 建议直接看 `CP0115`：2 张 PO，合计 **299,291**
- [ ] **C4** Expected 那一列现在应该都是灰色的 **"not stated"** ——
      不是空白。空白在日期列里读起来像"没有"，而"从来没人告诉我们"是另一回事。
- [ ] **C5** 没有在途的物料，On order 显示 **0**（不是空白），Arriving 显示 `—`。
- [ ] **C6** 搜索框输一个物料名，应该能搜到。
- [ ] **C7** 搜 `CR0180` 或 `CR0010`（原奶）——**应该什么都搜不到**。

## D. 跨标签页 / 导航

- [ ] **D1** 在顶部把物料类别改成 `05 Finished Products`，然后切标签页，
      **类别不该被重置**。
- [ ] **D2** 地址栏应该带 `?tab=aging&class=05` 这样的参数；
      复制这个地址新开一个页签，应该直接落在同一个标签页和同一个类别上。
- [ ] **D3** 多页签外壳里，Inventory 页签的图标是个箱子，标题是 "Inventory"。

## E. 权限

- [ ] **E1** 用一个**没有** `mrp.report.view` 的账号登录，
      左侧栏不该出现 Inventory；直接敲 `/inventory` 也应该拿不到数据
      （四个端点都单独加了 403 门禁测试）。

---

## 已知缺口 / 需要你决定的事

**1. ⚠️ epms-api 的 dev 容器挂的是别的 worktree。**
实测 `uniops_epms_api` 挂 `C:/Project/uniops-payofficer/epms-api`，不是主
checkout 也不是我这个 worktree。所以：

- 我加的 `po_line_items.planned_arrival_date` 列**已经手工在 dev 库建好了**，
  Inventory 页面读的是数据库、不经过 epms-api，**功能不受影响**；
- 但**改好的 NC 同步跑不起来**，所以到货日期填不进去。
  要看到 Expected 那一列有值，需要先把 epms-api 的挂载指回来，再跑一次
  全量 NC 同步。要我处理这个挂载、还是你在别的会话里弄？

**2. ⚠️ dev 库的 mdm alembic 版本表停在 `0006`，而迁移目录到 `0016`。**
这是**既有的**环境不一致（快照恢复时版本表没跟上，`materials` 里 0008 才加
的列明明已经存在），不是这轮造成的。我**没有去 stamp**（那会掩盖真实漂移），
而是：迁移文件在**全新空库上从 0001 跑到 0017 验证通过**，dev 库这两列手工
建 + 回填。**上生产前这个不一致要单独查清楚**，否则 `alembic upgrade head`
会在生产上从 0007 开始重跑。

**3. 在途还没接进 1C 的净需求。** 1C 现在仍然按"在途 = 0"算采购建议。
这轮把数据源建好了，但改净需求口径会动到已发布的采购建议，我按 spec 留到
单独一轮。要现在就接说一声。

**4. 没做主动推送。** 你选的是"只在页面看"。Aging 要发邮件/进待办随时能加。

---

## 这轮改动落在哪

| 服务 | 改了什么 | 迁移 |
|---|---|---|
| epms-api | `po_line_items.planned_arrival_date` + NC 同步读 `DPLANARRVDATE` | `ah01_po_line_planned_arrival` |
| mdm-api | `materials.erp_class_code/erp_class_name` + `erp_materials.accounting_group*`，从 `raw_payload` 回填 | `0017_material_acct_group` |
| mrp-api | PO/materials 只读镜像、在途服务、保质期分档、四个端点 | 无 |
| mrp 前端 | Inventory 页 + 三标签页 + 导航 | — |
| epms-api / mdm-api | 各加一个 `test_mrp_consumed_columns.py` 消费者契约测试 | — |
