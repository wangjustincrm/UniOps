# 采购协议 · Pickup Slip 小票对账

**日期**：2026-08-11
**前置**：[2026-08-06 采购协议设计](2026-08-06-purchase-agreement-design.md)（§5.2 是原始出处）、Phase 1A、[Phase 1B](2026-08-10-agreement-recurring-milestone-design.md)
**分支**：继续在 `feature/purchase-agreement` 上
**状态**：待实施

---

## 0. 为什么做

house_account 协议匹配发票时，界面要求填 **"Reason for settling without receipt evidence"**。这个质问不成立：匹配只表明"这张发票属于这个协议"，还没到对凭证的时候；而系统里根本没有上传凭证的地方，所以这个"例外通道"是**唯一通道**——100% 的匹配都要填，它想暴露的"有多少笔在绕过对账"永远是 100%，指标失效。

根因是命名撞车：原设计的 **"1B" 指 house_account 小票对账**，并留了护栏"`legacy_settlement` 在 1B 上线后必须收窄"。但 2026-08-10 把第二期提前，交付的 "Phase 1B" 是 recurring + milestone。两个不同的东西同名，护栏指向了一个从未发生的里程碑。本设计补上它。**本特性一律称 "Pickup Slips"，不用期号。**

### ★ 设计过程中被推翻的两个前提（保留记录，避免重蹈）

**其一**：初稿假设 Princess Auto 寄**月结汇总单**，设计了金额加总对账 + 差额阈值。用户提供实际单据后确认是**逐笔发票**。

**其二**（更重要）：据此改成 1:1、并把唯一索引写进了迁移。用户指出**这是把一个供应商的偶然事实固化成模型**——下一个 house account 若发汇总单，那个索引会直接把它挡在门外，得改迁移才能接。

**最终立场**：模型按 **N:1 通用形态**建（一张发票覆盖一到多张小票），Princess Auto 的逐笔发票只是 N=1 的特例；**编号匹配是能用则用的加速路径，人工按金额挑选是永远成立的基线**。

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
| 8 | 匹配策略 | **编号匹配是特例；匹配不上就退回人工按金额匹配** |

**决策 7 的理由（用户）**：小票由员工当面交给财务，这个交接动作本身就是入口控制——不会凭空收到来路不明的小票，交单人就是领用人。

**因此接受的代价**：系统里没有领用人本人的数字签认，`picked_by` 由代录人根据交接事实填写。证据载体是人不是软件。对单一供应商、仅工程部使用的挂账户，这是合理的风险接受。**不要在 UI 或文档里把它描述成"领用人已确认"。**

---

## 1. 模型的通用形态

一个 house account 只保证两件事：

1. 领用会产生**某种纸质凭证**（小票、送货单、柜台单），上面**至少有日期和金额**
2. 供应商会**开票**，一张发票覆盖一次或多次领用

**除此之外一律不假设**：不假设凭证上有可用编号，不假设编号能和发票上的编号对上，不假设一张发票只对一张小票，不假设金额能完全对平。

任何依赖上述之外事实的能力，都必须做成**可选的加速路径**，且在事实不成立时无声退化，而不是失效。

### 1.1 Princess Auto 的具体情况（**一个实例，不是模型**）

**发票**：`INVOICE NUMBER 3900096`、`STORE 27 KINGSTON`、`Till Tx. Nbr: 3-408757`、`A/C Name: Brian Gordon`、`NAME/PO NUMBER`（柜台报的用途）、完整行项目。逐笔开票。

**小票页脚**：
```
STORE  TILL  OP NO.  TRANS.    DATE
027    1     20888   510076    27-07-26 10:35
```

**编号假设**：发票的 `Till Tx. Nbr` = 小票的 `{TILL}-{TRANS}`。按此，上面那张小票对应的发票应印 `1-510076`。

> ⚠️ **未坐实**——手头两份单据不是同一笔交易（小票 7/27，发票 8/6）。实施时用一对配对单据验证。**但这次验证不是阻塞项**：假设不成立只意味着加速路径对 Princess Auto 不生效，基线（人工按金额）照常工作。这与初稿把整个匹配押在这个键上是根本不同的。

**小票上没有的东西**：人名、签名、发票号。`OP NO.` 是收银员工号，不是领货人。

---

## 2. 数据模型

### 2.1 新表 `agreement_pickup_slips`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `agreement_id` | UUID FK → `purchase_agreements` ON DELETE CASCADE，索引 | |
| `slip_date` | Date | 凭证日期。**必填**——它和金额是基线匹配仅有的两个依据 |
| `slip_ref` | String(64) nullable，索引 | **凭证上的参考号，什么都行**：小票号、交易号、送货单号。Princess Auto 的情况下由 OCR 抽出的 `TILL` + `TRANS` 拼成 `1-510076`，但**模型不关心它怎么来的**，只当它是个字符串 |
| `amount` | Numeric(15,2) | 税前 |
| `tax_amount` | Numeric(15,2) server_default 0 | |
| `total_amount` | Numeric(15,2) | 含税。**匹配比对用的就是它** |
| `picked_by` | UUID FK → `users` | 领用人（= 交单人，代录人据交接事实填） |
| `missing_slip_reason` | Text nullable | 无照片时必填 |
| `ap_reviewed_by` / `ap_reviewed_at` | UUID nullable / timestamptz nullable | AP 对"无照片"的裁定痕迹 |
| `status` | String(20) | `pending_ap_review` \| `open` \| `reconciled` \| `voided` \| `rejected` |
| `invoice_id` | UUID nullable，索引 | 认领它的发票。**一张小票只属于一张发票** |
| `notes` | Text nullable | |
| `created_by` | UUID FK → `users` | 代录人 |

**刻意不建的列，及理由**

- ~~`store_no` / `till_no` / `trans_no`~~ —— 这是 Princess Auto 小票的版式，不是模型。三列拆开存，下一个供应商的凭证就装不进去。统一收进 `slip_ref` 一个字符串；拼接规则属于 OCR 提示词与前端，不属于 schema
- ~~`is_backfilled`~~ —— 代录模式下恒为 true。用 `created_by ≠ picked_by` 表达，且不会说谎
- ~~`department_id` / `cost_center`~~ —— 只有工程部用，从协议继承

**约束**

- **去重（E6）**：`(agreement_id, slip_ref)` **部分唯一索引** `WHERE slip_ref IS NOT NULL`。参考号可能糊了、可能压根没有、OCR 可能抽不到——一堆 NULL 不该互相撞
- **`invoice_id` 只加普通索引，不加唯一索引。** 一张发票可以认领多张小票（N:1）。反方向由 `slip.invoice_id` 单值天然保证

### 2.2 新表 `agreement_slip_attachments`

照抄 `pr_attachments` 模具（第 6 份，附件路由的重复已由 owner 拍板接受）：`slip_id`(FK CASCADE) / `filename` / `content_type` / `file_size` / `file_data` / `storage_key`，走 file-api。

**为什么是独立表而不是行上的几个列**：

1. **要接进现有证据包机制**。`useChainAttachments` 沿 PA → 发票 → GR → PO → PR 血缘走，对每个单据打 `/{type}/{id}/attachments`。协议 PA 的血缘是 PA → 发票 → **小票**，与 `invoice.gr_ids → GR 附件` 结构对称。真附件表只需加一个分支；行上的列要开特例
2. 一张凭证可能要拍两张——长条小票、正反面
3. 留存/下载/审计与其他单据一致（CRA 支持性凭证 6 年留存）

### 2.3 `invoices` 新增 2 列

- **`slip_ids` JSONB nullable** —— 本次对账覆盖的小票集合，对应 `gr_ids` 的角色。**是数组**：逐笔发票下长度为 1，汇总单下长度为 N。数组不额外花成本，而单值会把「一张发票只对一张小票」这个偶然事实固化成约束
- `slip_variance_reason` Text nullable —— 合计与发票金额不符时的说明。**与 `legacy_settlement_reason` 分开存**：「对上了但差几块」与「根本没有凭证」是两件事，混存会让 §6 的收窄失去意义

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

Phase 1B 在这件事上栽过两次：排期行被认领后，发票改路由或 match-review 驳回时不释放，那一期就永久停在 `received`、挂着一张不再支撑它的发票，任何后续发票都认领不了它；更糟的是确认痕迹也留着，重新认领时直接算作"已确认"，**无人确认就付了款**。

**发票离开某个 house_account 协议的任何路径，都必须把它认领的**全部**小票放回 `open`、清各自的 `invoice_id`、清 `invoice.slip_ids` 与 `slip_variance_reason`。** 已知路径三条：改路由到 PO、改挂到另一个协议、match-review 驳回。

Phase 1B 已把释放逻辑抽成 `_release_schedule_row`（`epms-api/app/crud/invoice.py:518`）并从这些点调用。**小票释放扩进同一个函数**，不要另写平行实现；顺手改名为 `_release_agreement_evidence`——它从此负责排期行**和**小票两种凭证。注意小票是**多张**，释放要遍历。

`voided` 只能对 `open` / `pending_ap_review` 执行；已 `reconciled` 的必须先释放。

---

## 4. 匹配

发票匹配到 **house_account** 协议时，不再显示"无凭证结算理由"，而是候选小票列表。

### 4.1 基线：人工按金额挑选（永远成立）

**候选池**：该协议 `status = open` 的小票，按 `slip_date` 倒序（`pending_ap_review` 未获 AP 认可、`rejected` 已被否，都不进）。

操作员勾选，界面实时显示 **已选合计 / 发票金额 / 差额**。这条路径**不依赖任何编号**，只依赖日期和金额——即 §1 里那两个唯一被保证的事实。任何供应商、任何凭证版式都能用。

**这是基线，不是兜底。** 加速路径的作用只是把常见情形的勾选动作省掉，而不是取代它。

### 4.2 加速路径（能用则用，用不上无声退化）

按顺序尝试，命中即**预勾选**，操作员随时可改：

1. **`slip_ref` 精确匹配** —— 操作员在匹配面板填入发票上的参考号（可选输入框），与某张小票的 `slip_ref` 相等则预选它
2. **金额精确 + 日期邻近** —— 存在 `total_amount` 与发票金额完全相等、且 `slip_date` 在发票日期前 14 天内的**唯一**一张小票，则预选它。**存在多张就不预选**——猜错比不猜更糟
3. 都不命中 → 不预选，走 §4.1

> **为什么发票侧的参考号靠人工填而不是 OCR**：本期**不改发票 OCR 提示词**。`_INVOICE_PROMPT` 是 EPMS 发票解析在生产使用的，提示词不是加法——改一句可能扰动既有字段的抽取。而且不同供应商的发票上这个号叫什么、在哪，各不相同，写进通用提示词本身就是一次过度拟合。等某个供应商的量大到值得，再单独评估。

### 4.3 金额差额

差额 ≠ 0 时要求填 `slip_variance_reason`，**但永远允许提交**（决策 3）。不设阈值——阈值需要证据支撑，现在没有；只提示不拦已经满足需求。

### 4.4 提交

写 `invoice.slip_ids`（数组），选中的每张小票 → `reconciled` 并记 `invoice_id`。

---

## 5. 录入（OCR 辅助 + 人工确认）

### 5.1 新增 OCR 模式 `slip`

`expense-api` 已有 `POST /api/v1/ocr/{mode}`（`invoice` | `receipt`），Claude Haiku 4.5 视觉，收 JPEG/PNG/WebP/GIF/PDF ≤25MB，任何登录用户可调；EPMS 前端已在跨服务调它。

**新增 `slip` 模式，不改 `receipt`**——`receipt` 是 OA 报销在生产使用的路径，提示词改动有扰动风险；零售柜台凭证与通用报销收据本就不同类，分开抽得更准。

`slip` 模式返回：`{slip_ref, date, amount, tax_amount, total_amount, currency}`。

**`slip_ref` 是最佳努力**：提示词要求模型找出凭证上最像"交易/凭证编号"的那个值；若凭证把它拆成几列（如 Princess Auto 的 `TILL` + `TRANS`），用连字符拼接。**抽不到就返回 null，不是错误**——基线匹配不需要它。

**只抽抬头，不抽行项目。** 对账只需要总额；"买了什么"发票上本来就有完整行项目，照片也保留了原件。行项目一旦开抽会连带拉出分类、预算归集、行级对账一串问题，不服务于本期目标。

**失败姿态沿用现有设计**：文件读不出（HEIC/糊/损坏）→ 422 "Please enter the details manually"，**不是** 503。OCR 不可用时录入页照常工作，只是不预填。

### 5.2 录入页

EPMS 协议详情页新增 Pickup Slips 区块 + 录入表单：

1. 选文件 → 自动调 `/ocr/slip` → 预填 `slip_ref` / 日期 / 金额
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

**house_account 协议的发票要建 PA，必须 `slip_ids` 非空，或被标记 `legacy_settlement` 且有理由。**

- 存量发票都满足后半条（Phase 1A 强制标了 legacy），**不需要数据迁移**
- 与 recurring 的履约确认闸门对称：两条免收货路径各有自己的凭证要求
- milestone 仍无闸门（本期不变）

---

## 8. 证据包扩展

`useChainAttachments` 的血缘加一个分支：**PA → 发票 → `invoice.slip_ids` → 小票附件**，与现有 `invoice.gr_ids` → GR 附件对称（同样是数组，同样要去重）。

这是把附件做成独立表的兑现点：财务点开一张协议 PA，一次性拿到"发票 + 支撑它的全部小票照片"。**这是本特性真正的交付物**——用户最初的诉求就是"财务拿 Receipt 跟 Invoice 对账"。

---

## 9. 异常集

| | 做 | 说明 |
|---|---|---|
| **E9** 无小票提交 | ✅ | §3 的 `pending_ap_review` 流程 |
| **E6** 重复小票 | ✅ | `(agreement_id, slip_ref)` 部分唯一索引。`slip_ref` 为空时不去重——没有依据可去 |
| **E3** 金额不符 | ✅ | 差额显示 + 说明，不拦（决策 3） |
| **E2** 有发票无小票 | ✅ | **复用发票匹配已有的指派机制**（`AssignMatchRequest` + `Task`），不新造。AP 指派某人去补录小票，形成闭环 |
| **E1** 有小票无发票 | ⚠️ **只做展示** | 协议详情列出**超过 45 天仍 `open`** 的小票（命名常量 `SLIP_AGING_DAYS`，不做配置项）。**日跑批告警不做**——Phase 1B 刚证明那种循环容易写错且测不到（写出来的定时器根本不触发，靠评审模拟算术才发现）。先让展示用起来 |

---

## 10. 迁移与部署

**一个迁移**（epms-api，`ag04`，`down_revision = ag03_agreement_schedule`）：建 `agreement_pickup_slips`、`agreement_slip_attachments`，`invoices` 加 `slip_ids` 与 `slip_variance_reason`，加 `(agreement_id, slip_ref)` 部分唯一索引与 `invoice_id` 普通索引。

**expense-api 无迁移**（只加一个 OCR 模式）。

**部署顺序无约束**（§2.3 已论证）。需重建：`epms-api`、`epms-web`、`expense-api`。**无新环境变量**（`ANTHROPIC_API_KEY` 已配置在 expense-api）。

---

## 11. 测试要点

**通用性**（本设计的核心主张，必须有正面证据）
- 一张 `slip_ref` 为 NULL 的小票能被录入、能进候选池、能被人工勾选认领 —— 证明基线不依赖编号
- 多张 `slip_ref` 为 NULL 的小票可共存（部分索引的意义）
- 一张发票认领**多张**小票，`slip_ids` 长度 > 1，全部转 `reconciled`
- 加速路径命中多张金额相同的小票时**不预选**

**释放不变量**（最高优先，Phase 1B 在同一件事上栽过两次）
- 发票改路由到 PO / 改挂到另一个协议 / match-review 驳回 → 它认领的**每一张**小票回到 `open`，各自 `invoice_id` 清空，`invoice.slip_ids` 与 `slip_variance_reason` 清空
- 释放后这些小票可被另一张发票重新认领

**状态机**：有照片直接 `open`；无照片进 `pending_ap_review`；AP 通过 → `open`、驳回 → `rejected`；`pending_ap_review` 与 `rejected` 不进候选池；`reconciled` 不可直接 `voided`。

**闸门**：house_account 发票 `slip_ids` 为空且非 legacy → 建 PA 被拒；非空 → 放行；存量 legacy 发票 → 放行（不能因本期改动卡死历史数据）。

**OCR**：`slip` 模式返回预期字段；`slip_ref` 抽不到时返回 null 而非报错；不可读文件 → 422 而非 503；**`receipt` 模式逐字未变**（OA 回归）。

**回归基线**：动手前自己量。epms-api 当前基线 **69 failed / 716 passed**（失败集合已存盘），**必须一次跑完**——分段跑会改变失败集合。epms tsc 基线 **58**（TS 5.9.3）。expense-api 基线另量。

---

## 12. 已知风险与未决

- **没有领用人的数字签认**（决策 7）。证据是交接这个物理动作。**不要在 UI 上宣称"领用人已确认"。**
- **Princess Auto 的编号假设未坐实**（§1.1）。不阻塞——不成立只是加速路径不生效，基线照常。
- **OCR 抽错金额而无人复核**。缓解是预填值全部可编辑 + 匹配时差额会暴露。首月建议抽查。
- **`legacy_settlement` 未做角色收窄**（§6）。先看计数器数据。
- **E1 只有展示没有告警**。小票录了但发票一直不来，要靠人去看协议详情才发现。
- **一张小票不能拆给两张发票**（`slip.invoice_id` 单值）。若出现供应商拆单开票，需作废重录为两张。这是有意的当前限制。

---

## 相关

- [2026-08-06 采购协议设计](2026-08-06-purchase-agreement-design.md) —— §5.2 小票表原始结构、§9 异常集
- [2026-08-10 Phase 1B 设计](2026-08-10-agreement-recurring-milestone-design.md) —— 本设计沿用其释放不变量与闸门对称性
