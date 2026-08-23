# 采购协议 · 凭证模型重构（取代 Pickup Slip 设计）

> 本文取代 `2026-08-11-agreement-pickup-slips-design.md` 的第 4、5.2、6 节与
> 数据模型命名部分。原文其余部分（释放不变式、PA 闸门、证据包、OCR）继续有效，
> 原文保留作为过程记录。

## 0. 为什么推翻

用户在验收 Pickup Slip 交付时提出三点，全部成立：

1. **匹配与对账被捆在一起了。** 把发票关联到协议，本身只是"这张票属于这份协议"
   这一件事。而实现把它做成了"关联的同时必须交代凭证"——不选小票就必须写一句
   "为什么没有凭证"。用户原话：*关联一个 Invoice 到 Agreement 就是一个独立的事，
   Invoice 跟小票关联是另外一件事。*

2. **录入入口放错了地方。** 小票录入表单挂在协议详情页上。用户原话：*在我的构想里，
   上传小票类似上传 GR，然后可以在 Invoice 里另外匹配小票。* PO 路线本来就是这样：
   收货是独立动作（Create GR），发票匹配时去挑已存在的 GR。协议路线没有理由不一样。

3. **★ 凭证类型被写死成"柜台小票"了。** 用户原话：*Invoice 对账，依赖小票或是 GR/SR。
   你的设计和实现不能写死了，否则就只能做一个 PrincessAuto 这个客户了。*
   house_account 的通用形态是"围绕一份协议存在多张发票，每张发票靠某种签收凭证对账"，
   柜台小票只是其中一种。按月送货的挂账、外包服务的挂账都对不上。

### 前一版已经被推翻过一次

原设计曾把 Princess Auto 的小票号格式写进模型（`store_no`/`till_no`/`trans_no`），
用户指出耦合太紧后改成不透明的 `slip_ref`。**这次是同一个错误的更深一层**：那次
泛化的是"凭证上的编号"，这次要泛化的是"凭证本身是什么"。

### 用户决策记录（2026-08-11）

| 问题 | 决定 |
|---|---|
| 匹配时小票的地位 | **完全解耦**——匹配只做关联，不问凭证也不问理由；约束移到付款闸门 |
| 录入入口 | **独立菜单，类比 GR**——列表页 + 新建页，建时选协议 |
| 凭证实体 | **协议凭证，带类型字段**——不复用 PO 绑定的 GR，也不只做小票 |

### 为什么不复用现有 GR

查证结果：`goods_receipts.po_id` 是 **NOT NULL**，GR 死绑在 PO 上；`gr_line_items`
指向 `po_line_id`；确认流程假定存在 PR 申请人（2026-08-05 曾因"无 PR 的 GR"
向 59 人群发过通知）。协议没有 PO，把 `po_id` 改成可空等于在生产核心单据上开一个
大口子。**协议凭证与 PO-GR 保持两个实体，概念上并列，物理上隔离。**

现有 GR 已有 `gr_type: physical | service` 字段——用户说的 "GR/SR" 在本代码库里
本就是一个实体的两种形态，不是两样东西。协议凭证沿用同一思路。

---

## 1. 通用形态

```
一份 Agreement（前提）
   ├── 多张 Invoice（围绕它发生）
   └── 多份 Receipt（签收凭证）
             ↑
    Invoice 挂载 N 份 Receipt 完成对账
```

三层各自独立：建协议、收票、收凭证互不阻塞。**唯一的硬约束在付款**——起 PA 时，
一张 house_account 发票必须要么挂着凭证、要么带着一句显式的"无凭证结算"声明。

## 2. 数据模型

### 2.1 `agreement_pickup_slips` → `agreement_receipts`

新增判别列：

```
receipt_type   counter_slip | delivery | service      NOT NULL, default 'counter_slip'
```

- `counter_slip` —— 柜台领用的纸小票（Princess Auto 这类）
- `delivery` —— 送货签收单 / 收货凭证
- `service` —— 服务验收 / 工时签认

其余列原样保留（`agreement_id` / `receipt_date` / `receipt_ref` / `amount` /
`tax_amount` / `total_amount` / `received_by` / `missing_receipt_reason` /
`ap_reviewed_by` / `ap_reviewed_at` / `status` / `invoice_id` / `notes` /
`created_by`）——**只改名，不改语义**。`slip_date`→`receipt_date`、
`slip_ref`→`receipt_ref`、`picked_by`→`received_by`。

`receipt_ref` 继续是**不透明字符串**：小票号、送货单号、工单号都往里放。

同理 `agreement_slip_attachments` → `agreement_receipt_attachments`，
`invoices.slip_ids` → `invoices.receipt_ids`、
`invoices.slip_variance_reason` → `invoices.receipt_variance_reason`。

**一张发票可以混挂不同类型的凭证**（例如同一份挂账下既有柜台领用又有送货）。

### 2.2 类型特有字段

本轮**不加**类型特有字段。三种类型共用同一组列；差异只体现在 UI 文案与 OCR 模式选择上。
理由：还没有真实的第二类客户数据，现在猜字段等于重蹈"写死 Princess Auto"的覆辙。
需要时再加可空列，不影响已有数据。

## 3. 匹配（重写第 4 节）

**发票关联到协议 = 只做关联。** 与 recurring / milestone 完全一致：选中协议、提交、结束。

- 后端 `_match_to_agreement` 的 house_account 分支**不再要求理由**，不再在此设
  `legacy_settlement`，不再接收凭证 id。
- 前端 MatchPanel 的 house_account 分支**移除**凭证选择器与必填理由框。

## 4. 对账（新增，替代原第 4 节的选择器）

在**发票详情页**新增凭证区块，形状对标 PO 路线挑 GR：

- 列出该协议下 `open` 的凭证，可多选挂载 / 取消挂载
- 实时显示 已选合计 / 发票金额 / 差额
- 差额 ≠ 0 时要求填 `receipt_variance_reason`，**但不阻断**
- 一份凭证都没挂时，给一个显式动作："Settle without receipt evidence" + 必填理由
  → 置 `legacy_settlement` 与理由

原设计的两条加速路径（`receipt_ref` 精确命中、金额相等且日期在发票前 14 天内且
**唯一**命中）平移到这里，语义不变。

### 为什么"无凭证声明"放在发票详情而不是起 PA 时

这是发票级别的事实（这张票有没有凭证），不是付款级别的。放到起 PA 时再问，等于
让做付款的人替做对账的人回答。PA 闸门只负责**拦截**，不负责**采集**。

## 5. 录入入口（重写第 5.2 节）

**独立菜单项**，形状照抄 Goods Receipts：

- 侧边栏新增入口（权限 `epms.agreement.receipt.write`，由 `epms.agreement.slip.write` 改名）
- **列表页**：跨协议列出全部凭证，可按协议 / 类型 / 状态筛选
- **新建页**：先选协议 → 选类型 → OCR 辅助录入 → 提交 → 上传照片
- **协议详情页保留只读凭证列表**（就像 PO 详情页能看到它的 GR），录入表单移走

OCR：`counter_slip` 用现有的 `slip` 模式；`delivery` / `service` 也调同一模式
（它抽的是参考号/日期/金额，与凭证类型无关），抽不到就手工录——**不为类型分叉提示词**，
理由同 §2.2。

## 6. 不变的部分

以下已实现且经全分支评审，**原样保留**：

- **释放不变式**：发票改路由 / match_review 驳回 / DM 删除 / 普通删除 时，
  已挂载的凭证释放回 `open`（原设计 §3「释放是硬约束」）
- **PA 凭证闸门**：`not (receipt_ids or legacy_settlement)` → 422，存量 1A 发票放行
- **状态机**：`pending_ap_review | open | reconciled | voided | rejected`，
  含 ag05 那条"作废/驳回不占用参考号"的部分唯一索引
- **证据包**：PA 附件汇总沿 发票 → 凭证 → 照片 展开
- **权限**：独立键 + 与 `epms.agreement.read` 成对授予（label 里带依赖提示）
- **OCR `slip` 模式**（expense-api）
- `update()` 与 `create()` 对齐的状态路由（补理由 ⇒ 转 `pending_ap_review`）

## 7. 迁移

这一版**尚未上生产**，但 `ag04` / `ag05` 已应用于 dev 库。因此不改写既有迁移，
新增一条：

- `ag06` —— 重命名两张表与三列、新增 `receipt_type`（带默认值，存量行落
  `counter_slip`）、重建部分唯一索引为 `(agreement_id, receipt_ref)`

identity 侧 `0007` **尚未在任何环境跑过之外的地方**（dev 已跑），权限键改名需要
`0008`：`epms.agreement.slip.write` → `epms.agreement.receipt.write`，label 改为
`Record Agreement Receipts (needs View Agreements)`。

## 8. 需要返工的既有交付

| 既有 | 处置 |
|---|---|
| Task 9 `SlipEntryForm` | 迁到新建页，加类型选择 |
| Task 9 `SlipTable` | 拆成两处：协议详情页只读版 + 新列表页 |
| Task 10 MatchPanel 凭证选择器 | **移到发票详情**，MatchPanel 恢复清爽 |
| Task 10 invoice-scoped 凭证列表端点 | 保留，改由发票详情消费 |
| Task 5 匹配处的门禁 | 拆除 |
| 其余（表/CRUD/附件/释放/闸门/证据包/OCR/权限） | 改名平移，逻辑不动 |

## 8.5 协议详情页的 Document Chain（用户追加要求）

协议详情页要有与 PR / PO Detail 同款的文档链侧栏，展示 发票 ↔ 凭证 ↔ PA 的关联。

复用现成组件 `epms/src/components/shared/DocumentChainTree.tsx`（PR / PO / PA 三个
详情页已在用），**不新写一个**。

### 现状与要改的地方

该组件现在是 **PO 轴心**的：所有子项都挂在 `poId` 上（`currentType` 为 `pr` 时取
`pr.po_id`、为 `pa` 时取 `pa.po_id`）。协议路线没有 PO，于是：

- `currentType` 需要新增 `'agr'`，并加一条**协议轴心**的取数分支
- ★ **顺带修一个既有缺陷**：协议路线的 PA 其 `po_id` 为 NULL，于是 `poId` 解析成
  空串、所有子查询被禁用——**今天打开一张协议 PA 的详情页，文档链是空的**。加了
  协议分支之后，它应当沿 `pa.agreement_id` 回溯出协议。

### 协议轴心的层级

```
[Agreement card — 当前高亮]
     └─ Invoice row(s)
          ├─ Receipt row(s)   ← 支撑这张发票的凭证（挂载关系）
          └─ PA row(s)        ← 从这张发票起的付款申请
```

与 PO 轴心的排布保持一致：**PA 嵌在它所源自的发票之下**，一眼能看出哪些发票已经起过
付款；凭证与 PA 并列挂在发票下，因为两者都是"围绕这张发票"的事实。

recurring / milestone 协议同样适用——它们的发票没有凭证行，那一层自然为空，
但发票与 PA 两层照常显示。这个页面因此对三种协议类型都有价值，不只 house_account。

### 取数

- 发票：`GET /invoices?agreement_id=` —— **后端与 crud 已支持**
  （`api/v1/invoices.py:204`、`crud/invoice.py:81`）；前端 `InvoiceFilters` 类型
  可能要补一个字段
- 凭证：按发票的 `receipt_ids` 展开（与 `useChainAttachments` 已有的做法同源）
- PA：沿用现成的 `usePas`，按发票归组

### 附件汇总

`ChainAttachmentsPanel` 已挂在该组件里，Task 11 也已把"发票 → 凭证 → 照片"这条
血缘接进 `useChainAttachments`。协议轴心接上后，**协议详情页会自然获得整份协议的
证据包**（全部发票 + 全部凭证照片），这是原设计没有的能力。

---

## 9. 未决

- 第二类 house account 的真实样本还没有。`delivery` / `service` 两个类型现在是
  **结构就位、无真实数据验证**。首次接入真实客户时要回来核对字段够不够。
- 凭证列表页的跨协议筛选需要一个新的列表端点（现有的都按协议作用域）。
