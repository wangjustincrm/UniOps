# 快递运费付款(Freight)设计文档

- 日期：2026-10-19
- 状态：**D1–D13 已定；剩 §11 的 Q5(历史数据)、Q6(人员名单)，都不影响开工**
- 分支：`feat/epms-freight`(基于 main `cfc23949`)
- 归属：EPMS 内的新页面 **Freight**(PROCUREMENT 分区，放在 Payment Applications 旁)，不单独成模块

---

## 1. 背景与目标

我们和 UPS、FedEx、Purolator 等快递公司合作，都是快递上门取件。目前的痛点：

- 运费走 PR → PO → Invoice → PA 太折腾：一票几十块的快递也要先建 PR、PO。
- 也不能走 Agreement(采购协议)：协议锁定一个部门，可快递是全公司在用。
- 快递发票是**汇总发票**，系统里看只有一行；但发票后面几页列出了**每个 Track No 对应的金额**。一张发票里常常混着好几个部门的快递。
- 现在采购人员拿着原始运单，逐票手工和发票核对，再按部门分摊。

目标：

1. **寄件时登记运单**：只记三样东西：承运商、Track No、承担部门(成本中心)。
2. **发票到达后自动匹配**：上传快递发票，AI 抽出每一票的 Track No 和金额，和已登记的运单自动对上。
3. **人工处理差异**：发票上有但没登记的票、登记了但没计费的票、附加费、退款等。
4. **一键生成 PA**：不依赖 PO 和协议。分摊按运单的成本中心自动汇总，审批走 OPM → GM → Finance Manager → Finance BP，付款走现有的付款执行器。
5. **把分摊带到 NC**：预算实际数取自 NC 凭证，分摊必须进 NC 才算数(§7)。

规模：量很小，FedEx 最多也不超过 10 单/月。所以不做批量操作和复杂的匹配界面，一个列表加勾选框就够。

### 1.1 和已下线的 OA Direct PA 是什么关系

系统里原本有 OA 的「Direct PA」(PA-DIR)，即不依赖 PO、凭发票直接付款。它在 **2026-08-07 定下线，2026-09-11 确认**(docs/prd/OA.md §15)，下线原因文档里没写。本设计**不是**恢复它，区别在于：

| | OA Direct PA(已下线) | Freight PA(本设计) |
|---|---|---|
| 适用范围 | 任何供应商、任何费用 | **只限登记过的承运商**，只付运费 |
| 金额依据 | 发票 | 发票的**逐票明细**，每一票都要对到运单 |
| 部门分摊 | PA 表头一个成本中心 | 按运单分到**多个成本中心** |
| 审批 | finance_bp → finance_manager | OPM → GM → Finance Manager → Finance BP |

「只限登记过的承运商」这条是关键：它防止 Freight PA 变成绕开 PR/PO 的后门。

## 2. 已确认的决定(2026-10-18 ~ 10-19)

| # | 决定 |
|---|---|
| D1 | 不扩展 Agreement，做 EPMS 内的专门入口 |
| D2 | 一张 PA 可以包含多张发票，一张发票可以跨多个部门；PA 由**供应链专人**统一建 |
| D3 | 运单上只有 Track No 有用。可以手工输入，也可以上传运单让 AI 识别；登记时选承担部门 |
| D4 | 都是快递上门取件。入向运费很少，有也能拿到 Track No，照样登记 |
| D5 | 发票里带每个 Track No 对应的金额，按 Track No 自动匹配 |
| D6 | 预算科目**固定为 CRM0150102**，所有部门都用这一个科目 |
| D7 | 审批流程 OPM → GM → Finance Manager → Finance BP，由用户在审批配置里设定，代码只给默认值 |
| D8 | 部门经理**不收通知** |
| D9 | 总部 OA 的付款申请单是自由定制的，**不用参照** |
| D10 | **不做预算对比**：不做超预算检查，页面上也不显示预算余额 |
| D11 | **成本中心默认规则**(原 Q1)：先选部门；部门只有一个成本中心就自动带出；有多个的，默认带出该部门在当年预算里编了 CRM0150102 的成本中心，允许改。现在全靠人工判断，经常选错，所以由系统给默认值(§5.2) |
| D12 | **NC 应付导出按成本中心拆行**(原 Q3，§7) |
| D13 | **税按明细行记**(原 Q2，依据 §3.5 样本；用户 10-19 让按建议推进)：发票逐行印了税额的(Purolator)，直接用；按费用类别印的(UPS「On-Call Pickup Requests 13% 12.96 → 1.68」)，按金额比例分回该类别的明细行，尾差给金额最大的一行；剩下归不到类别的税，按比例分到所有明细行。校验：**各行税额合计 = 发票印的税额**。PA 税额 = 发票印的税额，**不按税率重算**。这和「PA 税只按行项目算」的规则精神一致(税挂在行上)，只是金额取自发票，不是行合计×单一税率，因为同一张快递发票里跨境件零税率、国内件 13%，没有单一税率 |

> 关于 D6：CRM0150102 在预算科目表里的名称是「Feight Out FTL/LTL」(原文拼写如此，挂在 CRM01501 Freight 下)。FTL/LTL 字面上指整车/零担运输，快递包裹记在这里是用户的决定，这里只是提醒科目名称和用途字面上不完全一致。

## 3. 关键约束(现有代码调研，2026-10-19，main `cfc23949`)

下面几条决定了设计的形状，都来自代码，括号里是位置。

1. **「PO 和协议都为空」在全系统里等于「OA Direct PA」。**
   - EPMS 的列表和详情会把这种 PA 过滤掉(`epms-api/app/crud/pa.py:147-150`，`api/v1/pa.py` 各端点返回 404)。
   - finance-api 的 `is_direct` 把它当成 `pa_dir`(`finance-api/app/models/pa.py:40-54`)：批量付款里显示成「Direct PA」，**付款后 EPMS 发票不会被标成已付**(`payment_execute.py:395`)。
   - expense-api 也有同样的 `is_direct`。
   - → Freight PA 必须有**自己的非空标识**(§5.3)，而且所有 `is_direct` 和「po_id/agreement_id 至少一个非空」的判断要一起改。
2. **PA 没有部门或成本中心字段，PA 行项目也没有。**审批引擎在没有 PO 和协议时，按**建单人的部门**路由(`approval-api/app/crud/engine.py:770-822`)。
3. **审批流程配置界面不能新增单据类型。**Portal 的 `ACTION_KEYS` 是写死的列表(`portal/src/pages/admin/AdminPanel.tsx:1379`)。不在列表里的类型，下一次保存流程配置时会被整个删掉。新单据类型必须同时改代码。
4. **预算实际数 = NC 凭证，不是 UniOps 单据。**
   - Budget vs Actual 按「预算科目 × 成本中心」汇总 NC 凭证行(`finance-api/app/crud/account_balance.py:602-647`)。
   - NC 的部门和成本中心编码通过 `budget_actual_cc_map` 映射回 UniOps 成本中心。
   - 「按单据算的实际数」已于 2026-09-17 从界面撤下。
5. **NC 应付导出每张发票只出一行，维度取金额最大的那条分摊**(`finance-api/app/services/nc_ap_export.py:245-252`，2026-08-05 的改动，注释写的是「NC wants a single line per AP」)。一张跨部门的快递发票照现状导出，**全部金额会记到一个部门**。
6. **预算的键是成本中心，不是部门。**一个部门可能有多个成本中心(Production 3 个、Supply Chain 3 个、QA/HR/Finance 各 2 个)。FY2026 的 CRM0150102 预算只编在 4 个成本中心上：SELL-0107-S03(Supply Chain)、SELL-0110(Sales)、SELL-0111(Marketing)、SELL-0113(E-COM)。数据来自 dev 库快照，开工前要用生产数据再核一次。
7. **EPMS 重复发票拦截只在前端做**(`InvoiceListPage.tsx:469-530`)。后端只有 OA 那边的跨表检查(`expense-api/app/api/v1/invoices.py:53-98`)。PA 占用发票也没有服务端的锁(`PaCreatePage.tsx:321-353` 是前端锁)。
8. **现有 AI 发票识别读不了长账单。**invoice 模式最多 100 行、`max_tokens` 8192，整份 PDF 一次送进模型(`expense-api/app/services/ocr_service.py:518-552`)。要读多页明细，得参照银行对账单的做法：用 pypdf 分页，每页调用一次 Haiku(`finance-api/app/services/bank_statement_parse.py:566-630`)。应用里调用 AI **只能用 Haiku**。
9. **最接近的先例**是协议里的 house account 收货单(`agreement_receipts`)：逐笔登记 → 月度汇总发票关联 N 笔 → 无 PO 的 PA 付款。但一张发票只能挂一个协议，所以跨不了部门。

## 3.5 三家真实发票样本(2026-10-19，`/home/crmadmin/shipping/`)

| | UPS `00004E4F77416` | FedEx `2-749-42565` | Purolator `550316833` |
|---|---|---|---|
| 页数 | 4 | 5 | 2 |
| 币种 | CAD | CAD(美国段按当日汇率折算) | CAD |
| 逐票明细 | Pickup Date、Tracking Number、服务、Billed Charge(运费 + 燃油附加费) | 每票一块：Ship Date、Tracking ID、运费、Earned Discount(负数)、燃油附加费、清关费 | Service Date、Shipment #(12 位 PIN)、运费、住宅附加费、燃油附加费、**本票税额** |
| 多包裹 | 一票两件：主单号 `1Z…551028` 加子单号 `1Z…544637` | 一票两件：`877326061702` 加 `877326061713` | 每票一件 |
| 不带单号的费用 | **上门取件费**(每次 $6.48，按取件单号)；**滞纳金**(上期未付 $261.26 × 8% = $20.90) | **上门取件费**(Transaction ID，取件地址在供应商那边) | 无 |
| 税 | 只在发票末页按**费用类别**列：只有取件费收 13% HST，跨境运费零税率 | 本张无税(入向国际件) | **逐票**列 GST/HST |
| 参考信息 | 「1st ref」、Sender: CRMPURCHASING | **Ref.#2: PO-099-2608-01**(供应商发来的入向件，写着我们的 PO 号) | 寄件人:Forough Salehi |
| 表头陷阱 | 页 1 同时印着「本期应付 $86.82」和「累计未付 $354.11」，**只能取本期** | — | — |

由此得出的设计调整(已落到下面各节)：

1. **一票多件**：登记的人可能填主单号，也可能填子单号。发票明细行要存全部包裹单号，任一个对上都算匹配(§5.4、§6)。
2. **不带单号的费用很常见**(取件费、滞纳金)，不是例外情况。明细行要有「费用类型」，匹配页能把这类费用挂到某票运单上(比如 FedEx 的取件费跟着那票入向件走)，或者挂到默认成本中心(§6)。
3. **税按明细行记**：Purolator 逐票给出税额；UPS 按类别给出，要分回到那一类的明细行；FedEx 本张无税。这就是 Q2 的答案(§11)。
4. **参考号可以用来推荐成本中心**：FedEx 入向件的 Ref 写着 PO 号。如果能在 EPMS 里找到这个 PO，就推荐它所属 PR 的成本中心(P2)。
5. **付款时效**：UPS 的付款期只有 16 天，逾期罚 8%，这张发票里已经有一笔滞纳金。四级审批要跑得赢付款期，列表要显示到期日，按到期日排序(§9)。
6. **AI 识别量不大**：三张都在 5 页以内，逐票明细每张 1–3 票。P1 整份 PDF 一次识别就够，`max_tokens` 调高即可；分页识别留作超过 10 页时的退路(§10 R2)。

## 4. 业务流程

```
① 寄件登记(采购员，随时)
   选承运商 → 输入 Track No(或上传运单照片由 AI 识别后确认)→ 选部门/成本中心 → 保存
   状态：待计费

② 上传发票(供应链专人，发票到达时)
   选承运商 → 上传 PDF → AI 分页抽出表头(发票号/日期/税前/税/总额/币种)和逐票明细(Track No/日期/金额)
   → 逐票合计和发票税前金额核对 → 人工确认识别结果 → 保存
   系统做重复发票检查

③ 匹配(自动 + 人工)
   按「承运商 + Track No」自动对上:
     ✅ 已匹配     金额取发票，成本中心取运单
     ⚠️ 未登记     发票上有、没人登记 → 当场补选成本中心(同时补一条运单记录)
     ⚠️ 无 Track No 的费用行(账户级费用、滞纳金等)→ 选成本中心
     ⚠️ 已在别的发票计过费 → 提示(通常是调整或补收)，人工确认
   右侧显示:该承运商仍待计费的运单(登记了但这张发票没有)→ 不处理，留待下张发票
   每一行都有成本中心后，这张发票标记为「已分摊」

④ 生成 PA(供应链专人)
   选同一承运商下一张或多张「已分摊」的发票 → 生成 Freight PA
   表头金额从发票汇总，分摊按成本中心汇总 → 提交

⑤ 审批:OPM → GM → Finance Manager → Finance BP(可配置)

⑥ 付款:进 Finance 待付列表(类型「Freight PA」)→ 付款执行器 → 发票标记已付 → 汇款通知照常

⑦ 进 NC:AP 导出时按成本中心拆成多行(§7)→ Budget vs Actual 里各成本中心的 CRM0150102 才有数
```

一张 PA 只能有一个供应商(现有约束)，所以 UPS 和 FedEx 的发票要分开建 PA。

## 5. 数据设计(epms-api)

### 5.1 承运商配置 `freight_carriers`

这张表同时是白名单：只有登记在这里的供应商才能建 Freight PA。

| 列 | 说明 |
|---|---|
| id | UUID |
| vendor_id | FK `business_partners`，唯一 |
| code | `UPS` / `FEDEX` / `PUROLATOR`，用于界面显示和选择 AI 识别的提示词 |
| tracking_pattern | 可选，Track No 格式校验用的正则(如 UPS 是 `1Z` 开头的 18 位) |
| default_cost_center_id | 可选。无 Track No 的费用行默认挂这里 |
| is_active | |

由 EPMS Admin 维护。供应商主数据本身不加字段，避免改动 MDM。

### 5.2 运单 `freight_shipments`

| 列 | 说明 |
|---|---|
| id | UUID |
| carrier_id | FK `freight_carriers` |
| tracking_no | 规范化后存(去空格、转大写)；**(carrier_id, tracking_no) 唯一** |
| direction | `outbound` / `inbound` |
| ship_date | 可空(运单上没有日期，计费后用发票上的日期回填) |
| department_id / cost_center_id | 承担方。成本中心必填，部门由成本中心派生。默认值规则见 D11：取该部门**当年已批准预算计划**里 CRM0150102 金额不为 0 的成本中心(budget-api 当前计划)；有多个就取金额最大的；一个都没有就要求手工选。用户手工改选到一个没编运费预算的成本中心时，提示一句「该成本中心未编运费预算，确认吗？」，不拦截。匹配页上供应链专人还能再改 |
| note | 可选，比如寄什么、寄给谁 |
| attachment_key | 可选，运单照片(File Server) |
| source | `registered`(寄件时登记)/ `from_invoice`(匹配时从发票补登) |
| status | `pending`(待计费)/ `billed`(已计费)/ `void`(作废，比如寄错了取消) |
| created_by / created_at / updated_at | |

### 5.3 PA 上的标识

`payment_applications` 新增 `source` 列：`po` / `agreement` / `freight` / `direct`。迁移时按现有数据回填，规则是：有 po_id 填 `po`，有 agreement_id 填 `agreement`，其余填 `direct`。

- `is_direct` 改成 `source = 'direct'`，在 epms / approval / expense / finance 四个服务的模型里一起改。
- EPMS 的归属过滤改为 `source IN ('po','agreement','freight')`，`core/ontology.py` 的 `scope_pa` 和 `pa_scope_conditions` 同步改。
- 只加一个 `freight_batch_id` 外键也能区分，但每个判断都要写成三个外键的组合，容易漏改；用一个 `source` 列更直接。

这是跨四个服务的共享表改动，开工前必须数清所有读这张表的地方(§10 风险 R1)。

### 5.4 发票：复用 EPMS `invoices` 表

快递发票就存成一张普通的 EPMS 发票，这样附件、AP 镜像、付款后标记已付、汇款通知、重复发票报表都能直接复用。

- 新增匹配路线 `match_route = 'freight'`，加 `freight_carrier_id`。
- `amount` / `tax_amount` / `total_amount` 照常取发票表头。
- 状态：AI 识别并确认后为 `unmatched`；所有明细行都分摊完为 `matched`；付款后为 `paid`(现有流程)。

逐票明细放在新表里，不塞进 `line_items` JSON：

**`freight_invoice_lines`**

| 列 | 说明 |
|---|---|
| id | UUID |
| invoice_id | FK `invoices` |
| charge_type | `shipment`(带单号的运费)/ `pickup`(上门取件)/ `fee`(滞纳金等账户级费用)/ `adjustment`(调整、退款) |
| tracking_no | 主单号；`pickup` / `fee` 可空 |
| package_tracking_nos | 该票全部包裹单号(JSONB 数组，含主单号)，匹配时任一个对上都算 |
| reference | 发票上的参考号或取件单号(UPS 1st ref、FedEx Ref#2、取件单号)，原样保存 |
| ship_date | 发票上的寄件或服务日期 |
| description | 服务类型或费用名称，例如「FedEx International Priority」「Late Payment Fee」 |
| amount | **税前**金额，包括燃油附加费、折扣(负)、附加费；可以为负(退款或调整) |
| tax_amount | 归属到本行的税额(规则见 D13) |
| linked_line_id | `pickup` 行可以挂到同一张发票里的某一票运费上，跟随那一票的成本中心 |
| shipment_id | FK `freight_shipments`，匹配上就有 |
| cost_center_id | 匹配上从运单带过来，未登记的手工选；必填才能完成匹配 |
| match_status | `auto` / `manual` / `unregistered_assigned` / `fee_assigned` |

校验规则：**明细行税前金额合计 = 发票 `amount`**，不相等不能完成匹配。差额来自 AI 漏读或误读，要人工修正明细，不允许塞一个「差额」行了事。

### 5.5 PA 成本分摊 `pa_cost_allocations`

在生成 PA 的那一刻按成本中心汇总，存快照。之后运单再改，也不影响已提交的 PA。

| 列 | 说明 |
|---|---|
| pa_id | FK |
| invoice_id | 分摊来自哪张发票(NC 导出按发票出行，需要这个维度) |
| cost_center_id / department_id | |
| budget_code | 固定 `CRM0150102`，**存纯科目码**，不存「码-科目名」组合串，用 `ensure_known_budget_code` 校验 |
| amount | 税前 |
| tax_amount | 该成本中心各明细行 `tax_amount` 之和 |

PA 表头：`subtotal` = 各发票 `amount` 之和，`tax_amount` = 各发票税额之和，`shipping_amount` / `other_charges` 为 0。PA 行项目一张发票一行(描述「UPS Invoice 12345 · 7 shipments」)，PDF 和现有页面因此能显示正常。

## 6. 匹配规则

1. **规范化**：Track No 去掉空格和连字符，转大写。
2. **自动匹配**：同一承运商下，运单的 `tracking_no` 等于明细行 `package_tracking_nos` 里的任一个，且运单状态为 `pending`。一张运单只能对上一行；同一行的几个包裹单号如果分别登记成了几条运单，以第一条为准，其余标「重复登记」由人工作废。
3. **重复计费**：同一个 Track No 已在别的发票计过费(运单状态 `billed`)，**不自动匹配**，标黄提示「已在发票 X 计费 $Y」。人工确认是补收或调整后，选择沿用原成本中心。
4. **未登记**：当场选成本中心，系统同时补一条 `source = from_invoice` 的运单记录，方便日后统计「有多少票没登记」。
5. **不带单号的费用行**：
   - `pickup`：如果同一张发票里有日期相同(或相邻 1 天)的运费行，就推荐挂上去，跟那一票走(FedEx 样本里的取件费就是那票入向件的)；否则挂承运商的默认成本中心。
   - `fee`(滞纳金等)：挂承运商的默认成本中心。滞纳金是付款慢造成的，不应该分给寄件部门。
   - 都可以人工改。
6. **负数行**(退款或调整)：和正数行一样处理；有 Track No 的跟着原运单的成本中心走。
7. **发票里没有、但已登记的运单**：不处理，保持 `pending`。「待计费运单」列表按登记日期显示账龄，超过 60 天标红(可能寄错、漏账，或者其实没寄)。
8. **完成匹配**：每一行都有成本中心，合计等于发票税前金额 → 发票状态改为 `matched`，相关运单状态改为 `billed`。
9. **撤销**：PA 提交前可以撤销匹配，发票回到 `unmatched`，运单回到 `pending`。PA 提交后只能整张 PA 退回或取消再撤销。

## 7. 进 NC 和预算

这是这个功能能不能真正解决「按部门分摊」的关键。**用户 2026-10-19 定按成本中心拆行**(D12)。

- 现状：AP 导出每张发票只出一行，维度取最大的那条分摊(§3 第 5 条)。这是 2026-08-05 按 NC 的要求改的。
- 如果 Freight 发票也照此导出，一张跨 4 个部门的 UPS 发票，CRM0150102 的实际数会**全记到金额最大的那个部门**，其他部门在 Budget vs Actual 里看不到自己的运费。
- **建议**：`nc_ap_export.py` 对 `match_route = 'freight'` 的发票开例外，**每个成本中心一行**。金额和税取 `pa_cost_allocations`，`revexp` 固定为 CRM0150102，NC 成本中心和部门编码沿用现有的反查(`NC_CC_BY_UNIOPS`、`_dept_code_from_cc`)。
- 可行性：2026-08-05 之前，导出本来就是一条分摊一行(`405ae79b` 才合并成一行，提交说明里没写原因)，所以导出代码以前就支持多行。只对 Freight 发票恢复多行，其他发票不变。当时合并是不是因为 NC 导入不接受多行，记录里查不到，**上线前让 AP 实际导一张多行的试试**。
- 被否掉的替代方案：按成本中心拆成多张 PA，每张对应一张应付单。代价是同一张快递发票要拆成多张单据审批和付款。
- Finance 模块的定位是「NC 为准，我们做辅助层」。本设计不往 `budget_ledger` 写数据(那张表已判死刑)，也不在 UniOps 里另算一套运费实际数。

**预算对比：不做**(用户 2026-10-19 定)。Freight PA 不走超预算检查(现在也只有 PR 走)，匹配页和 PA 页都不显示预算余额。

## 8. 审批、权限、可见性

### 8.1 审批

- 新单据类型 **`pa_frt`**，默认流程 `opm → gm → finance_manager → finance_bp`(`engine.py` 的 `_WORKFLOW_DEFAULTS`)。用户可以在 Portal 里改。
- 必须用**单独的 `opm` 和 `gm` 两个角色**，不能用 `gm_or_opm`。`gm_or_opm` 按单据部门解析，Freight PA 没有单一部门，会一直落到 GM，OPM 那一步就被跳过了。
- 流程里如果加了 `dept_manager` / `director` 这类按部门解析的步骤，会按建单人的部门解析(引擎现有的兜底)。在 Portal 配置界面提示「Freight PA 跨部门，不建议使用按部门解析的角色」。
- 驳回 → `cancelled`(和 `pa` 一致)。批准后 → 生成 `process_pa` 付款任务(和 `pa` 一致)。
- 新单据类型要登记的地方(调研列出的清单)：
  - approval-api：`_DOC_META`、`_WORKFLOW_DEFAULTS`、`_POST_APPROVE`、驳回规则、`_READ_OUT_TYPES`
  - epms-api：`crud/task.py` 的 `TASK_LIVENESS` 和相关表、`tests/test_task_liveness_registry.py`
  - 前端：Portal `ACTION_KEYS`、`PortalHome.tsx` 的 `DOC_PATH`、`epms/src/lib/taskTypes.ts` 的 `HREF_MAP`、`useTasks.ts`
  - expense-api：`PA_DOC_TYPES`
  - 助理知识库：`epms-api/app/knowledge/pa_types.yaml`
  - **向导层**(硬规则：每次需求变更都要同步)

### 8.2 权限(新增权限键)

| 权限 | 能做什么 | 默认给谁 |
|---|---|---|
| `epms.freight.register` | 登记、修改、作废自己的运单 | procurement_officer、requester |
| `epms.freight.manage` | 上传发票、匹配、生成 Freight PA；改任何人的运单 | 供应链专人(在角色矩阵里指定) |
| `epms.freight.admin` | 维护承运商配置 | system_admin |

### 8.3 可见性

- 运单列表：有 `register` 权限的人看自己登记的运单，有 `manage` 权限的人看全部。
- Freight PA：在 `is_pa_visible`(`core/access_scope.py:779-825`)里加一个分支：`manage` 权限持有人、流程里任一步骤角色的当前持有人都能看。
- **注意**：GM、OPM、财务这些步骤的任务是**广播任务**(`assigned_user_id` 为空)，现有的「有指派给我的待办」判断对广播任务不成立。不加这个分支的话，受范围限制的审批人可能收到待办却打不开单据，属于可达性问题。

## 9. 页面(EPMS 前端)

侧边栏 PROCUREMENT 分区，在 Payment Applications 下面加 **Freight**，页面内分三个标签：

1. **Shipments(运单)**
   - 列表：承运商、Track No、方向、部门/成本中心、登记人、登记日期、状态、所在发票。
   - 默认筛选「待计费」，账龄超过 60 天的标红。
   - 「New Shipment」：承运商、Track No(可上传运单照片识别)、成本中心、备注。可以连续录入多条。
2. **Invoices(发票)**
   - 列表：承运商、发票号、日期、金额、票数、匹配状态、所在 PA。
   - 「Upload Invoice」→ 识别结果确认页 → 匹配页。
   - **匹配页**：左边是发票明细行(每行带匹配状态和成本中心下拉)，右边是该承运商的待计费运单；顶部显示合计校验，以及按成本中心汇总的金额。
3. **Create PA**
   - 选承运商 → 勾选「已分摊」的发票 → 预览按成本中心汇总的分摊 → 生成 → 跳到 PA 详情。

PA 详情页(现有页面)遇到 `source = freight` 时：
- 「关联 PO」那一块换成「运费分摊」表(成本中心 × 金额，可展开到逐票明细)。
- PDF 不再打印空的 PO Number 那一格，改成打印分摊表。

界面文字一律英文(前端约定)。

## 10. 风险

| # | 风险 | 处理 |
|---|---|---|
| R1 | `payment_applications` 由四个服务共享，`is_direct` 和「po/agreement 非空」的判断分散在各处，漏改一处就会把 Freight PA 当成 Direct PA 或直接隐藏 | 开工第一步先 grep 出全部消费者列成清单逐个改；加测试：Freight PA 在 EPMS 列表、详情、finance 待付列表、付款回写、汇款通知各处都能正常出现 |
| R2 | AI 识别读不全或读错 | 逐行合计对表头核对，不平就不能完成匹配；表头「本期应付」和「累计未付」分开识别(UPS)；三张样本做成固定回归用例。P1 整份 PDF 一次识别，超过 10 页再分页 |
| R8 | 四级审批拖过付款期(UPS 16 天，逾期罚 8%) | 发票和 PA 列表显示到期日、按到期日排序，离到期不足 5 天的标红 |
| R3 | 同一张发票被付两次 | 服务端做重复发票检查(复用 OA 的跨表检查)；生成 PA 时 `SELECT … FOR UPDATE` 锁住发票，并检查有没有被其他有效 PA 占用(`invoice_ids @>`，有 GIN 索引) |
| R4 | NC 导出不拆行，部门分摊等于白做 | §7，开工前和财务确认 |
| R5 | Portal 保存流程配置会删掉不认识的类型 | `ACTION_KEYS` 和 approval-api 默认值同一次发布上线 |
| R6 | 受范围限制的审批人看不到单据 | §8.3 的可见性分支 + 用无头浏览器以 GM/OPM 身份走一遍 |
| R7 | AI 额度和 Claude Code 共用一个 key | 每张发票按页数调用 Haiku，量小(每月个位数张)，影响可忽略；识别失败时允许手工录入明细 |

## 11. 待定项

| # | 问题 | 建议 |
|---|---|---|
| ~~Q1~~ | 选部门还是选成本中心 | ✅ 已定，见 D11 |
| ~~Q2~~ | 税怎么分 | ✅ 见 D13 |
| ~~Q3~~ | NC 应付导出能否按成本中心拆多行 | ✅ 已定拆行，见 D12 |
| ~~Q4~~ | 币种 | ✅ 三张样本都是 CAD(FedEx 把美国段折算成 CAD)。照现有约束：一张 PA 一个币种 |
| Q5 | 历史数据：上线前已到的发票、已寄出的运单怎么处理？ | 上线日之后寄出的才登记；上线前到的发票照旧在总部 OA 付完 |
| Q6 | 谁是「供应链专人」？哪些人登记运单？ | 用户给名单，配到角色矩阵里 |
| ~~Q7~~ | 发票样本 | ✅ 已提供(§3.5) |

## 12. 分期

**P1(一次上线)**
- 承运商配置、运单登记(手工录入 + 运单照片识别)
- 发票上传 + AI 识别(新的 OCR 模式 `freight_invoice`，整份 PDF 一次识别，三张样本做回归用例)+ 识别结果确认
- 自动匹配 + 人工处理 + 合计校验
- 生成 Freight PA(`source = freight`、`pa_cost_allocations`)、审批类型 `pa_frt`、可见性、PDF
- Finance 待付列表、付款回写、汇款通知能识别 Freight PA
- NC 导出：Freight 发票按成本中心拆行(D12)
- 迁移：`freight_carriers`、`freight_shipments`、`freight_invoice_lines`、`pa_cost_allocations`、`payment_applications.source`(带回填)、`invoices.freight_carrier_id`

**P2(看使用情况再做)**
- 报表：按成本中心 × 月份的运费，待计费运单账龄，未登记票数
- 导入承运商门户导出的 CSV 明细，替代 PDF 识别
- 参考号里出现 EPMS 的 PO 号时，推荐该 PO 所属 PR 的成本中心(§3.5 第 4 条)
- 超过 10 页的发票分页识别
- 运单页用手机拍照扫条码

## 13. 文档同步(合并前必须完成)

- `docs/prd/EPMS.md`：新增 §3.21 Freight；§3.15 PA 增加 `source` 和 Freight PA 说明；§6 导航；§9 预算口径补一句「Freight 分摊通过 NC 导出进入实际数」
- `docs/prd/FINANCE.md`：FIN-PAY-06 待付列表类型筛选增加 Freight PA；NC AP 导出例外规则
- `docs/prd/PORTAL.md`：审批类型列表加 `pa_frt`；Inbox 跳转
- 助理向导层与 `pa_types.yaml`
