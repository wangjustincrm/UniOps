# 采购协议 · Pickup Slip 小票对账

**日期**：2026-08-11
**前置**：[2026-08-06 采购协议设计](2026-08-06-purchase-agreement-design.md)（§5.2 是原始出处）、Phase 1A、[Phase 1B](2026-08-10-agreement-recurring-milestone-design.md)
**分支**：继续在 `feature/purchase-agreement` 上
**状态**：待实施

---

## 0. 为什么做，以及一个被推翻的前提

house_account 协议匹配发票时，界面要求填 **"Reason for settling without receipt evidence"**。这个质问不成立：匹配只表明"这张发票属于这个协议"，还没到对凭证的时候；而系统里根本没有上传凭证的地方，所以这个"例外通道"是**唯一通道**——100% 的匹配都要填，它想暴露的"有多少笔在绕过对账"永远是 100%，指标失效。

根因是命名撞车：原设计的 **"1B" 指 house_account 小票对账**，并留了护栏"`legacy_settlement` 在 1B 上线后必须收窄"。但 2026-08-10 把第二期提前，交付的 "Phase 1B" 是 recurring + milestone。两个不同的东西同名，护栏指向了一个从未发生的里程碑。本设计补上它。**本特性一律称 "Pickup Slips"，不用期号。**

### ★ 设计中途被推翻的前提

初稿假设 Princess Auto 寄**月结汇总单**，因此设计了"N 张小票加总 ↔ 1 张发票"的金额对账、差额阈值与说明。

用户提供实际单据后确认：**Princess Auto 是逐笔发票**。每次柜台领用生成一张独立发票，自带发票号、单一门店、单一日期、行项目，以及 `Till Tx. Nbr`。

于是关系是 **1 张小票 : 1 张发票**，加总对账那一整套连同差额阈值全部作废。本文档是按新事实重写的版本。

### 用户决策记录

| # | 决策 | 取值 |
|---|---|---|
| 1 | 谁录入 | **后勤/AP 代录**（领用人交纸小票），桌面端 |
| 2 | 录入时机 | 平时零星录，发票到了再对账 |
| 3 | 金额不符 | **只提示不拦** |
| 4 | 小票照片 | **必须作为附件留存系统** |
| 5 | 小票号 | **OCR 一并抽取** |
| 6 | 代码位置 | 继续在 `feature/purchase-agreement` |
| 7 | 方案取舍 | **A（小票登记簿）**，不走 B（领用人任务箱确认） |

**决策 7 的理由（用户）**：小票由员工当面交给财务，这个交接动作本身就是入口控制——不会凭空收到来路不明的小票，交单人就是领用人。

**因此接受的代价**：系统里没有领用人本人的数字签认，`picked_by` 由代录人根据交接时的事实填写。证据载体是人不是软件。对单一供应商、仅工程部使用、金额有限的挂账户，这是合理的风险接受，不是疏漏。**不要在 UI 或文档里把它描述成"领用人已确认"。**

---

## 1. 两份单据上的号码

**发票**（`INVOICE / FACTURE`）：`INVOICE NUMBER 3900096`、`STORE 27 KINGSTON`、`Till Tx. Nbr: 3-408757`、`A/C Name: Brian Gordon`、`NAME/PO NUMBER`（柜台报的用途）、完整行项目。

**小票页脚**：
```
STORE  TILL  OP NO.  TRANS.    DATE
027    1     20888   510076    27-07-26 10:35
```

**连接键假设**：发票的 `Till Tx. Nbr` = 小票的 `{TILL}-{TRANS}`。按此，上面那张小票对应的发票应印 `1-510076`；`3-408757` 则是 3 号收银台第 408757 笔。

> ⚠️ **这是假设，尚未坐实**——手头两份单据不是同一笔交易（小票 7/27，发票 8/6）。**实施前必须用一对配对单据验证**：任取一张发票，找出其对应小票，核对 `Till Tx. Nbr` 是否等于该小票的 `TILL-TRANS`。整个自动匹配都押在这个键上，假设不成立就要改匹配策略（退化为按金额+日期建议，见 §4）。这一步写进实施计划的第一个任务。

**小票上没有的东西**：人名、签名、发票号。`OP NO.` 是收银员工号，不是领货人。所以"谁领的"只能来自交接事实（决策 7）或发票的 `A/C Name`。

---

## 2. 数据模型

### 2.1 新表 `agreement_pickup_slips`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `agreement_id` | UUID FK → `purchase_agreements` ON DELETE CASCADE，索引 | |
| `slip_date` | Date | 小票日期，OCR 预填 |
| `store_no` | String(10) nullable | 门店号，OCR 预填（如 `027`） |
| `till_no` | String(10) nullable | 收银台号，OCR 预填 |
| `trans_no` | String(20) nullable | 交易号，OCR 预填 |
| `till_tx_ref` | String(32) nullable，索引 | **归一化连接键** = `{till_no}-{trans_no}`，去掉前导零后拼接。发票侧的 `Till Tx. Nbr` 用同一规则归一后比对 |
| `amount` | Numeric(15,2) | 税前 |
| `tax_amount` | Numeric(15,2) server_default 0 | |
| `total_amount` | Numeric(15,2) | 含税，匹配比对用的就是它 |
| `picked_by` | UUID FK → `users` | 领用人（= 交单人，代录人据交接事实填） |
| `missing_slip_reason` | Text nullable | 无照片时必填 |
| `ap_reviewed_by` / `ap_reviewed_at` | UUID nullable / timestamptz nullable | AP 对"无照片"的裁定痕迹 |
| `status` | String(20) | `pending_ap_review` \| `open` \| `reconciled` \| `voided` \| `rejected` |
| `invoice_id` | UUID nullable | 认领它的发票 |
| `notes` | Text nullable | |
| `created_by` | UUID FK → `users` | 代录人 |

**约束**

- **去重（E6）**：`(agreement_id, till_tx_ref)` **部分唯一索引** `WHERE till_tx_ref IS NOT NULL`。小票号可能糊了、OCR 可能抽不到，一堆 NULL 不该互相撞。
- **1:1**：`invoice_id` **部分唯一索引** `WHERE invoice_id IS NOT NULL`。一张发票只能被一张小票支撑，反之亦然。

**不建的列，及理由**

- ~~`is_backfilled`~~ —— 代录模式下恒为 true。用 `created_by ≠ picked_by` 表达，且不会说谎。
- ~~`department_id` / `cost_center`~~ —— 只有工程部用，从协议继承。

### 2.2 新表 `agreement_slip_attachments`

照抄 `pr_attachments` 模具（第 6 份，附件路由的重复已由 owner 拍板接受）：`slip_id`(FK CASCADE) / `filename` / `content_type` / `file_size` / `file_data` / `storage_key`，走 file-api。

**为什么是独立表而不是行上的几个列**：

1. **要接进现有证据包机制**。`useChainAttachments` 沿 PA → 发票 → GR → PO → PR 血缘走，对每个单据打 `/{type}/{id}/attachments`。协议 PA 的血缘是 PA → 发票 → **小票**，与 `invoice.gr_ids → GR 附件` 结构对称。真附件表只需加一个分支；行上的列要开特例。
2. 一张小票可能要拍两张——长条小票、正反面。
3. 留存/下载/审计与其他单据一致（CRA 支持性凭证 6 年留存）。

### 2.3 `invoices` 新增 2 列

- `slip_id` UUID nullable —— **单值，不是数组**。逐笔发票下就是 1:1；若将来真出现汇总单，再改成数组并重做匹配
- `slip_variance_reason` Text nullable —— 金额不符时的说明。**与 `legacy_settlement_reason` 分开存**：`` "对上了但差几块" `` 与 `` "根本没有凭证" `` 是两件事，混存会让 §6 的收窄失去意义

> **共享表影响**：`invoices` 被 expense-api、finance-api、approval-api 镜像，但都是**显式列清单**，不声明的新列它们看不见。additive-nullable，**无部署顺序约束**——与 Phase 1B 的 `schedule_id` 同理。

---

## 3. 状态机

```
录入（有照片）        → open
录入（无照片 + 理由）  → pending_ap_review ──AP 通过──→ open
                                          └─AP 驳回──→ rejected
open ──被发票认领──→ reconciled
open ──录错作废──→ voided
reconciled ──发票释放──→ open      ← 硬约束，见下
```

### ★ 释放是硬约束

Phase 1B 在这件事上栽过两次：排期行被认领后，发票改路由或 match-review 驳回时不释放，那一期就永久停在 `received`、挂着一张不再支撑它的发票，任何后续发票都认领不了它；更糟的是确认痕迹也留着，重新认领时直接算作"已确认"，无人确认就付了款。

**发票离开某个 house_account 协议的任何路径，都必须把它认领的小票放回 `open`、清 `slip.invoice_id`、清 `invoice.slip_id` 与 `slip_variance_reason`。** 已知路径三条：改路由到 PO、改挂到另一个协议、match-review 驳回。

Phase 1B 已把释放逻辑抽成 `_release_schedule_row`（`epms-api/app/crud/invoice.py:518`）并从这些点调用。**小票释放扩进同一个函数**，不要另写一份平行实现；顺手改名为 `_release_agreement_evidence`——它从此负责排期行**和**小票两种凭证。

`voided` 只能对 `open` / `pending_ap_review` 执行；已 `reconciled` 的必须先释放。

---

## 4. 匹配（替换现在那个理由输入框）

发票匹配到 **house_account** 协议时，不再显示"无凭证结算理由"，而是候选小票列表：

**候选池**：该协议 `status = open` 的小票（`pending_ap_review` 未获 AP 认可、`rejected` 已被否，都不进）。

**自动建议顺序**（命中即选中，操作员可改）：

1. **`till_tx_ref` 精确匹配** —— 发票侧的 `Till Tx. Nbr` 归一后与小票 `till_tx_ref` 相等。这是唯一确定性的一条
2. **金额精确 + 日期邻近** —— `total_amount` 完全相等且 `slip_date` 在发票日期前 14 天内。逐笔发票下金额本应完全相等，所以这条的命中率很高
3. 都不命中 → 不预选，操作员手工挑

> **发票侧的 `Till Tx. Nbr` 从哪来**：本期**不改发票 OCR 提示词**。`_INVOICE_PROMPT` 是 EPMS 发票解析在生产使用的，提示词不是加法——改一句可能扰动既有字段的抽取。因此发票的 `Till Tx. Nbr` 由操作员在匹配时**可选填入**一个输入框（照着纸发票敲，短），填了就走路径 1，不填就走路径 2。等路径 2 的准确率在实际使用中被证明不够，再单独评估改发票提示词。

**金额比对**：选中小票后并排显示 `小票含税总额 / 发票金额 / 差额`。差额 ≠ 0 时要求填 `slip_variance_reason`，**但永远允许提交**（决策 3）。逐笔发票下差额本应为 0，所以非零就是真异常，值得记一笔。

**提交后**：写 `invoice.slip_id`，小票 → `reconciled` 并记 `invoice_id`。

---

## 5. 录入（OCR 辅助 + 人工确认）

### 5.1 新增 OCR 模式 `slip`

`expense-api` 已有 `POST /api/v1/ocr/{mode}`（`invoice` | `receipt`），Claude Haiku 4.5 视觉，收 JPEG/PNG/WebP/GIF/PDF ≤25MB，任何登录用户可调；EPMS 前端已在跨服务调它。

**新增 `slip` 模式，不改 `receipt`**——`receipt` 是 OA 报销在生产使用的路径，提示词改动有扰动风险；零售柜台小票与通用报销收据本就不同类（门店/收银台/交易号只在前者有），分开抽得更准。

`slip` 模式返回：`{store_no, till_no, trans_no, date, amount, tax_amount, total_amount, currency}`。

**只抽抬头，不抽行项目。** 对账只需要总额与交易号；"买了什么"发票上本来就有完整行项目，而且照片保留了原件。行项目一旦开抽会连带拉出分类、预算归集、行级对账等一串问题，不服务于本期目标。

**失败姿态沿用现有设计**：文件读不出（HEIC/糊/损坏）→ 422 "Please enter the details manually"，**不是** 503。OCR 不可用时录入页照常工作，只是不预填。

### 5.2 录入页

EPMS 协议详情页新增 Pickup Slips 区块 + 录入表单：

1. 选文件 → 自动调 `/ocr/slip` → 预填门店/收银台/交易号/日期/金额，并由 `till_no` + `trans_no` 拼出 `till_tx_ref`
2. **所有预填值可编辑**。OCR 结果是草稿不是结论——金额抽错而无人复核比不抽更糟
3. 人工补 `picked_by`（交单人），用 `/users/directory` 选人——**不是** `/users`，那个是 system_admin 专属
4. 无照片时必填 `missing_slip_reason`，提交后进 `pending_ap_review`

**权限**：录入/编辑/作废跟随 `epms.agreement.write`；AP 裁定 `pending_ap_review` 用与发票 match-review 相同的 `ApDep`（`epms-api/app/api/v1/invoices.py:606`），不新建权限键。

---

## 6. `legacy_settlement` 收窄

**理由框只在"没有选中任何小票"时出现。** 有小票就不该被问为什么没有凭证。此时才写 `legacy_settlement = True` + `legacy_settlement_reason`。

于是协议详情上的 "settled without receipt" 计数恢复成真正的健康度指标——数的是"有多少笔完全无凭证付掉"，而不是恒等于全部。

**权限收窄不做**：原设计说"限定角色"。`epms.agreement.write` 已是受控集合，在没有真实滥用数据之前先加门禁只是制造摩擦。先让计数器跑起来。列入未决项。

---

## 7. house_account 的 PA 凭证闸门

`epms-api/app/api/v1/pa.py:94` 有一句注释写着 "house_account 走 slip 路径"，**这句话目前是假的**——house_account 的 PA 没有任何凭证闸门（Phase 1B 的确认闸门只对 recurring 生效）。本期把它变成真的：

**house_account 协议的发票要建 PA，必须 `slip_id` 非空，或被标记 `legacy_settlement` 且有理由。**

- 存量发票都满足后半条（Phase 1A 强制标了 legacy），**不需要数据迁移**
- 与 recurring 的履约确认闸门对称：两条免收货路径各有自己的凭证要求
- milestone 仍无闸门（本期不变）

---

## 8. 证据包扩展

`useChainAttachments` 的血缘加一个分支：**PA → 发票 → `invoice.slip_id` → 小票附件**，与现有 `invoice.gr_ids` → GR 附件对称。

这是把附件做成独立表的兑现点：财务点开一张协议 PA，一次性拿到"发票 + 支撑它的小票照片"。**这是本特性真正的交付物**——用户最初的诉求就是"财务拿 Receipt 跟 Invoice 对账"。

---

## 9. 异常集

| | 做 | 说明 |
|---|---|---|
| **E9** 无小票提交 | ✅ | §3 的 `pending_ap_review` 流程 |
| **E6** 重复小票 | ✅ | `(agreement_id, till_tx_ref)` 部分唯一索引 |
| **E3** 金额不符 | ✅ | 差额显示 + 说明，不拦（决策 3） |
| **E2** 有发票无小票 | ✅ | **复用发票匹配已有的指派机制**（`AssignMatchRequest` + `Task`），不新造。AP 指派某人去补录小票，形成闭环 |
| **E1** 有小票无发票 | ⚠️ **只做展示** | 协议详情列出**超过 45 天仍 `open`** 的小票（逐笔发票通常紧随领用而来，45 天远超正常间隔；命名常量 `SLIP_AGING_DAYS`，不做配置项）。**日跑批告警不做**——Phase 1B 刚证明那种循环容易写错且测不到（写出来的定时器根本不触发，靠评审模拟算术才发现）。先让展示用起来 |

---

## 10. 迁移与部署

**一个迁移**（epms-api，`ag04`，`down_revision = ag03_agreement_schedule`）：建 `agreement_pickup_slips`、`agreement_slip_attachments`，`invoices` 加 `slip_id` 与 `slip_variance_reason`，以及两个部分唯一索引。

**expense-api 无迁移**（只加一个 OCR 模式）。

**部署顺序无约束**（§2.3 已论证）。需重建：`epms-api`、`epms-web`、`expense-api`。**无新环境变量**（`ANTHROPIC_API_KEY` 已配置在 expense-api）。

---

## 11. 测试要点

**连接键验证（实施第一步，先于写代码）**：用一对配对单据坐实 `Till Tx. Nbr == {TILL}-{TRANS}`。不成立就改 §4 的匹配策略。

**释放不变量**（最高优先，Phase 1B 在同一件事上栽过两次）
- 发票改路由到 PO / 改挂到另一个协议 / match-review 驳回 → 小票回到 `open`，`slip.invoice_id`、`invoice.slip_id`、`slip_variance_reason` 全部清空
- 释放后该小票可被另一张发票重新认领

**状态机**：有照片直接 `open`；无照片进 `pending_ap_review`；AP 通过 → `open`、驳回 → `rejected`；`pending_ap_review` 与 `rejected` 不进候选池；`reconciled` 不可直接 `voided`。

**约束**：同协议同 `till_tx_ref` 第二次插入被拒；`till_tx_ref` 为 NULL 的多行可共存（部分索引的意义，必须有一例）；一张小票不能被两张发票认领。

**匹配**：`till_tx_ref` 命中优先于金额+日期；两者都不命中时不预选；差额非零要说明但仍可提交。

**闸门**：house_account 发票无 `slip_id` 且非 legacy → 建 PA 被拒；有 `slip_id` → 放行；存量 legacy 发票 → 放行（不能因本期改动卡死历史数据）。

**OCR**：`slip` 模式返回预期字段；不可读文件 → 422 而非 503；**`receipt` 模式逐字未变**（OA 回归）。

**回归基线**：动手前自己量。epms-api 当前基线 **69 failed / 716 passed**（失败集合已存盘），**必须一次跑完**——分段跑会改变失败集合。epms tsc 基线 **58**（TS 5.9.3）。expense-api 基线另量。

---

## 12. 已知风险与未决

- **★ 连接键假设未坐实**（§1）。整个自动匹配押在 `Till Tx. Nbr == {TILL}-{TRANS}` 上，实施第一步必须验证。
- **没有领用人的数字签认**（决策 7）。证据是交接这个物理动作。**不要在 UI 上宣称"领用人已确认"。**
- **OCR 抽错金额而无人复核**。缓解是预填值全部可编辑 + 匹配时差额会暴露。首月建议抽查。
- **`legacy_settlement` 未做角色收窄**（§6）。先看计数器数据。
- **E1 只有展示没有告警**。小票录了但发票一直不来，要靠人去看协议详情才发现。
- **一张发票对多张小票不支持**（1:1 + 唯一索引）。若 Princess Auto 将来改发汇总单，`slip_id` 要改成数组并重做 §4 的匹配，这是有意的当前限制，不是遗漏。

---

## 相关

- [2026-08-06 采购协议设计](2026-08-06-purchase-agreement-design.md) —— §5.2 小票表原始结构、§9 异常集
- [2026-08-10 Phase 1B 设计](2026-08-10-agreement-recurring-milestone-design.md) —— 本设计沿用其释放不变量与闸门对称性
