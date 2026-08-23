# Purchase Agreement（协议采购 / Open PO 替代方案）设计

- 日期：2026-08-06
- 模块：EPMS（epms-api + epms 前端）、approval-api、Portal
- 状态：设计已与用户逐节确认，待写实现计划

---

## 1. 背景与问题

现状有两类支出被硬塞进了不适合它们的流程：

**① Princess Auto（挂账领用）** — 工程部人员到店直接拿货，店头开一张 Receipt（小票），月底商家出一张月结 Invoice。目前做法是在 EPMS 建一张 PO 当作 "Open PO"，之后不停修改、追加 Line Item，每次收到发票再做 PA。财务用纸质小票人工比对月结单（AP Review）。

**② 合同/协议类周期付款**（公司网络费、电话费等） — 按月收 Invoice、按月付款。目前在**集团总部的 OA 系统**里用 Direct PA 处理（非 UniOps OA）。该系统数据无法拉取，因此**历史付款不需要迁移**。

**③ 合同类阶段付款** — 有合同的项目按阶段（里程碑）分批付款，如预付 / 到货 / 验收各付一部分。同样缺少协议层落点，且与现有 PA 的 `prepayment` / `settlement` 机制存在重叠，需要理清而非另造。

问题：

- ① 与 PR→PO→GR 流程冲突：一张 PO 无限追加行，`qty`/`received_qty` 语义崩坏，PO 状态机无法收口
- ② 无法跟踪：不知道某供应商是否按月都支付了、累计付了多少、这个月的账单来了没有
- 两者都缺少"协议层"这个概念——授权、有效期、议定价格没有落点

---

## 2. 行业标准调研结论（加拿大 / 北美）

### 2.1 正确形态是"协议层 + 释放层"两层结构

| 形态 | 出处 | 特征 |
|---|---|---|
| Blanket PO / Standing Order | 北美企业通用 | 对某供应商、某期间预谈价格条款；带**有效期** + **not-to-exceed 金额**；不预先展开明细行 |
| Standing Offer / Supply Arrangement | 加拿大联邦 PSPC | 通常 1–3 年；每次用 **call-up** 释放；带 **limitation of expenditure** |
| Outline Agreement（Value / Quantity Contract）+ Release Order | SAP / ERP | 头上挂 target value + 有效期；服务类另有 **Service Entry Sheet** 与 **limit item** |

**共同点：协议层不做数量核销的三方匹配。** 它是"授权与价格的容器"，不是"待收货的订单"。

### 2.2 2-way vs 3-way 的行业分界线

- **3-way（PO + GR + Invoice）**：有实物入库、有明确单价数量
- **2-way（PO/合同 + Invoice）**：服务、订阅、租金、水电、经常性协议支出——用签署的合同/SOW 充当 PO 的角色

Non-PO / 经常性发票在典型企业占发票量 **30–50%**，是被专门设计流程的主流场景，不是边缘情况。

### 2.3 砍掉 GR 后必须补的补偿控制

1. 协议本身走一次性审批
2. Not-to-exceed 累计额度控制
3. 有效期控制
4. 价目表比价（补上 Blanket PO"无单价明细导致无法自动核价"的天然弱点）
5. 业务方履约确认（代替仓库 GR 的那条腿）
6. 重复发票检测
7. 定期对账与到期重认（annual review & renew）

### 2.4 🇨🇦 加拿大合规约束

CRA ITC（进项税抵扣）文档要求：

- **> $150** 的交易凭证必须含供应商名（**须与 CRA GST/HST 注册名一致**）、GST/HST 注册号、买方名称、明细描述、**税额分列**
- 供应商名不符 → ITC 被剔除
- 记录**保存 6 年**

**推论**：协议本身不能替代发票。每次付款仍必须有供应商正式发票并分列税额——这支持"以 Invoice 为付款凭证"的方案，同时要求 Agreement 必须绑定 vendor master 权威记录。

**⚠️ 待核**：CRA Notice 199（Procurement Cards — Documentary Requirements for Claiming ITCs）与 ① 的挂账月结场景直接相关，决定**能否只凭月结单抵 ITC、还是必须留每张店头小票**。抓取被 403 挡下未读到正文，**上线前需由会计确认**。

---

## 3. 关键洞察：两个子类型的匹配模型不同

| | 时序 | 履约凭证 | 匹配关系 | 匹配模式 |
|---|---|---|---|---|
| 常规 PO | 先订 → 后收 → 后票 | 仓库 GR | Invoice ↔ GR 多对一 | 3-way |
| **① house_account** | **先领 → 后票（月结）** | **店头小票** | 一张月结发票 ↔ N 张小票 | **仍是 3-way**，GR 换形态 |
| **② recurring**（周期付款） | 无实物 | 无 | 发票 ↔ 周期排期行 | **真 2-way** |
| **③ milestone**（阶段付款） | 阶段达成 → 开票 | **阶段验收确认** | 发票 ↔ 阶段排期行 | 3-way（验收即第三条腿） |

③ 与 ② 都由"付款计划"驱动，共用一张排期表（§5.3）；区别在触发条件（日历周期 vs 阶段达成）和确认语义（服务正常 vs 阶段验收）。

① 不是"免收货确认"，而是**把仓库 GR 换成领用小票登记**。这个"多对一"结构 UniOps 已有：`invoices.gr_ids` 就是 JSONB 数组（[epms-api/app/models/invoice.py:60](../../../epms-api/app/models/invoice.py)）。

同时这让"轻量确认"落在**正确的时点**——领货当下（谁领的、算哪个成本中心），而不是月底让人回忆。财务侧的对账因此变成基本自动。

---

## 4. 归属决策：Agreement 落 EPMS

**决策**：Agreement 及其子实体落 **EPMS（epms-api）**；确认动作走 **Portal 统一任务箱**（用款部门不需要进采购界面）；**OA 的 Direct PA 保持原样**，只划边界。

**理由**：

1. **vendor master 权威表在 EPMS**（`business_partners`）。OA 侧 `epms_mirrors.py` 的 `EpmsVendor` 是镜像。Agreement 要满足 CRA"供应商名与 GST/HST 注册名一致"，必须绑权威表。
2. **Princess Auto 现在就是 EPMS 的 PO**。Agreement 本质是 blanket PO 的正确形态，是采购工具；且要与 PR/PO 共用 vendor、tax_code、预算维度做支出分析。

> 更正记录：调研初期曾以"OA 没有 Invoice 实体"为由主张 EPMS，**该论据不成立**——UniOps OA 确有 `ExpenseInvoice`/`ExpenseInvoiceLine`，且 `(vendor_id, invoice_number)` 去重是跨 OA + EPMS 两张表做的（[expense-api/app/api/v1/invoices.py:59](../../../expense-api/app/api/v1/invoices.py)）。`payment_applications` 表本来就是两个模块共用。真正成立的理由是上面两条。

### 4.1 Direct PA 的边界（不搬家，只划线）

分界线不是"有没有发票"，而是**这笔支出事前有没有承诺（commitment）**：

| 类别 | 判据 | 归属 |
|---|---|---|
| 员工垫付 / 差旅报销 | 收款方是**员工** | OA Expense。不动 |
| 无承诺的一次性对外付款 | 收款方是供应商，事后才知道要付：银行手续费、政府规费、一次性律师费、罚款、年费 | **留 OA Direct PA** |
| 有承诺的周期 / 协议支出 | 事前有合同或授权文件，可预测、需累计、需对账 | **EPMS Agreement** |

**防漂移探测**：同一 vendor 在 6 个月内出现 ≥3 次 Direct PA → 提示"这看起来是周期性支出，建议建 Agreement"。没有这个机制，边界定完还会慢慢烂回去。

---

## 5. 数据模型

### 5.1 `purchase_agreements`（Agreement / AGR）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `number` | String(40) unique | `AGR-YYYYMM-####`，沿用现有单据号生成法（max 尾号+1 + advisory lock） |
| `title` | String(255) | |
| `agreement_type` | String(20) | `house_account` \| `recurring`（周期付款）\| `milestone`（阶段付款） |
| `contract_no` | String(100) nullable | 合同编号 |
| `contact_email` | String(255) nullable | 供应商联系邮件（协议相关通知/催票收件人） |
| `vendor_id` / `vendor_name` | UUID FK → business_partners / String | |
| `vendor_reference` | String(100) nullable | **供应商侧的账号 / 引用号**。切换时把现有 Open PO 号登记于此，供应商无需改号 |
| `valid_from` / `valid_to` | Date | |
| `grace_days` | Integer default 30 | 过期后仍可匹配的宽限窗口（见 E7） |
| `not_to_exceed` | Numeric(15,2) nullable | |
| `consumed_amount` | Numeric(15,2) default 0 | 随每张匹配发票累加 |
| `currency` | String(10) default CAD | |
| `tax_code` / `tax_rate` | String(20) / Numeric(5,4) | mdm 快照，同 PO/PA 约定 |
| `department_id` / `budget_code` | | 预算与作用域维度 |
| `owner_id` | UUID FK → users | 协议责任人，接收预警与缺票告警 |
| `status` | String(20) | `draft` \| `in_review` \| `active` \| `expired` \| `closed` \| `cancelled` |
| `approval_step_idx` | Integer | 同 PO/PA 约定 |
| `notes` | Text | |
| 附件 | 复用现有附件机制 | 合同/协议原件挂在协议档案上 |

**进发票匹配候选池的条件**：`status = active`，**或** `status = expired` 且当前日期 ≤ `valid_to + grace_days`（见 E7）。其余状态一律不进。

可选子表 `agreement_price_lines`（价目表 / rate card）：`description`、`unit`、`unit_price`、`effective_from/to`。用于第 2.3 节控制 #4 的自动比价。**首期不实现**，留字段位置。

### 5.2 `agreement_pickup_slips`（Pickup Slip，仅 house_account）

| 字段 | 说明 |
|---|---|
| `agreement_id` | FK |
| `slip_date` | 领用日期 |
| `store_slip_no` | 店头小票号 |
| `amount` / `tax_amount` | |
| `picked_by` | UUID FK → users，领用人 |
| `department_id` / `cost_center` | 归属 |
| 附件 | 小票照片 —— **必填**（见下） |
| `missing_slip_reason` | Text nullable，无附件时**必填**理由 |
| `is_backfilled` | Boolean，**代录标记**（非领用人现场录入） |
| `ap_reviewed_by` / `ap_reviewed_at` | 无小票时 AP 的确认痕迹 |
| `status` | `pending_ap_review` \| `open`(待对账) \| `reconciled` \| `voided` \| `rejected` |

去重键：`(agreement_id, store_slip_no)`（见 E6）。

**小票强制策略（用户决策）**：附件必填。确实找不到小票时，允许填 `missing_slip_reason` 提交，此时 slip 进 `pending_ap_review`，**AP 确认后才转 `open` 参与对账**，驳回则转 `rejected` 退回补充。协议详情上单列"无小票"计数，与 `legacy_settlement` 一样作为健康度指标——这个口子长期高企说明现场拍照没落实。

### 5.3 `agreement_payment_schedule`（付款计划，recurring + milestone 共用）

周期付款与阶段付款结构高度相同（都是"预先排好的若干期，每期等一张发票"），因此用一张表带 `schedule_type` 区分，避免两张几乎一样的表。

| 字段 | 适用 | 说明 |
|---|---|---|
| `agreement_id` | 全部 | FK |
| `schedule_type` | 全部 | `period`（周期）\| `milestone`（阶段） |
| `sequence` | 全部 | 排序 |
| `expected_amount` | 全部 | 预期金额 |
| `expected_date` | 全部 | period 必填；milestone 可选（预计达成日） |
| `status` | 全部 | `pending` \| `received` \| `overdue` \| `waived` |
| `invoice_id` | 全部 | 认领到的发票（nullable） |
| `period_label` | period | 如 `2026-08` |
| `tolerance_pct` | period | 容差百分比（协议级默认，可逐期覆盖） |
| `overdue_after_days` | period | 逾期判定天数 |
| `milestone_name` | milestone | 阶段名（如 "设备到货"、"验收合格"） |
| `amount_pct` | milestone | 占合同总额百分比（与 `expected_amount` 二选一录入，另一个推算） |
| `trigger_condition` | milestone | Text，阶段达成的判定条件 |
| `accepted_by` / `accepted_at` | milestone | **阶段验收确认痕迹** |
| 验收附件 | milestone | 验收单 / 交付物证明 |

**两者确认语义不同**：
- `period` → 确认"本月服务正常"，容差内一键确认（见 E4）
- `milestone` → 确认"阶段已达成/验收合格"，由**用款部门或项目负责人**确认并附证据。这一步实质上就是阶段付款的第三条腿，不能省

> ⚠️ **命名冲突提醒**：approval-api 已有 `mil` action key，但那是 **OA 的 expense milestone claim**（`_expense_meta("MIL")`，dept_manager → finance_bp），与采购阶段付款无关。不要复用该 key，也不要在代码/文案里混用 "milestone" 而不加限定。

> ⚠️ **与现有 PA 预付/结算机制的关系必须理清**：`payment_applications` 已有 `pa_type`（`regular` / `prepayment` / `settlement` / `balance`）以及 `prepayment_pct`、`expected_settlement_date`、`prepayment_applied`、`settlement_*` 一整套字段（[epms-api/app/models/pa.py:38-87](../../../epms-api/app/models/pa.py)）。阶段付款里的"预付阶段"应当**映射到现有 `pa_type=prepayment` 并沿用 settlement 抵扣逻辑**，而不是另造一套并行机制。实现前必须逐字段核对映射关系——见 §13。

### 5.4 现有表的改动

**`invoices`**（epms-api）新增：

- `agreement_id` UUID nullable FK → purchase_agreements
- `agreement_number` String(40) nullable
- `slip_ids` JSONB nullable — 本次对账覆盖的 Pickup Slip 集合（对应 `gr_ids` 的角色，house_account 用）
- `schedule_id` UUID nullable FK → agreement_payment_schedule（recurring / milestone 用）
- `match_route` String(20) nullable — `po` \| `agreement`
- `match_route_auto` Boolean default false — 是否为系统识别（人工切换过则 false）
- `legacy_settlement` Boolean default false + `legacy_settlement_reason` Text — 存量结清通道（见 §9）

**`payment_applications`**（共用表）新增：

- `agreement_id` UUID nullable FK
- `agreement_number` String(40) nullable

> ⚠️ `payment_applications` 与 `invoices` 是**多服务消费**的表（epms-api / expense-api / approval-api / finance-api 各有模型或镜像）。加列前必须逐个服务清点消费者与镜像模型，逐列核对物理表——此坑本项目已多次踩中。

---

## 6. 审批：新增独立工作流 `agr`

approval-api 是统一引擎，action key 存在 `CompanyConfig.workflow_defs`（JSONB），启动时从 `_WORKFLOW_DEFAULTS` **只补缺、不覆盖管理员已配置的**（[approval-api/app/main.py:16-36](../../../approval-api/app/main.py)）。

因此新增工作流**零迁移**：往 `_WORKFLOW_DEFAULTS` 加 `agr` 键，部署后自动补种。

**默认步骤（种子值，管理员可在 Portal Admin 改）**：
发起人（采购）→ 用款部门负责人 → 采购经理 → 财务经理 →（超阈值时）GM

比 PO 更严一级的理由：**这是一次性授权，批完之后一整年的发票都不再走完整审批链**，控制强度全押在这一步。

**必须重走审批的变更**：
- 改 `not_to_exceed`、改 `valid_from/to`、改 `vendor_id`
- **到期 renew 必须重批，不能自动延**（annual review & renew）
- 改备注 / 联系人 / owner → 免批

**⚠️ 待核**：Portal Admin 的审批流配置 UI 是否自动枚举 `workflow_defs` 的 key。若为硬编码列表，需加一行。

---

## 7. 控制点落地

| # | 控制 | 落地 | 强度 |
|---|---|---|---|
| 1 | 协议一次性授权 | `agr` 工作流 | **强**（主控制） |
| 2 | NTE 累计额度 | `consumed_amount` 进度条；80% / 100% / 120% 三档通知 owner + 采购经理；**只预警不拦截**，不阻断匹配与 PA | 弱（用户决策） |
| 3 | 有效期 | 候选池条件见 §5.1；超出 `valid_to + grace_days` 一律退出 | 强 |
| 4 | 价格核对 | house_account 比小票金额合计；recurring / milestone 比排期行 `expected_amount ± tolerance_pct` | 中 |
| 5 | 履约确认 | Pickup Slip（house_account，领用当下）/ 任务箱一键确认（recurring，容差内）/ 阶段验收 `accepted_by` + 附件（milestone） | 强 |
| 6 | 重复发票检测 | **已有**：`(vendor_id, invoice_number)` 跨 OA + EPMS 去重 | 强 |
| 7 | 到期重认 | renew 走 `agr` 审批 + 到期前 N 天提醒（复用现有通知） | **强**（主控制） |

> **用户决策记录**：NTE 选择"只预警不拦截"。代价是控制 #2 退化为预警信号，真正拦得住的只剩 #1 和 #7 两道。协议列表需支持按"已超额"筛选，供定期清理与 renew 时重新定额。

---

## 8. 发票路由与匹配

### 8.1 候选池扩展

现有 `GET /invoices/{id}/match-candidates` 按 `vendor_id` + 开放状态返回 PO（[epms-api/app/api/v1/invoices.py:397-402](../../../epms-api/app/api/v1/invoices.py)）。扩成**同时返回该 vendor 下符合 §5.1 候选池条件的 Agreement**（`active`，或 `expired` 但仍在 grace 窗口内）。上传流程不变。

### 8.2 自动识别：引用号解析

发票上的引用号不区分类型（Princess Auto 现在印的就是 Open PO 号），因此**不能靠"有没有号"消歧，要靠号本身解析**：

1. OCR 抓到引用号 → 先查**开放状态的 PO 号段**（`_MATCHABLE_PO_STATUSES`，[invoices.py:378](../../../epms-api/app/api/v1/invoices.py)）
2. 未命中 → 查 **Agreement 号段** 及 Agreement 的 **`vendor_reference`**

**切换零沟通成本**：把现有 Open PO 号登记为新 Agreement 的 `vendor_reference`，供应商继续印老号，系统解析到 Agreement。

**⚠️ 顺序必须写死**：老 PO 须先置 `closed` 才退出 PO 候选池（`closed` 不在 `_MATCHABLE_PO_STATUSES` 内）。否则同一个号会同时命中 PO 表和 `vendor_reference`。解析只查开放 PO + 老 PO 已关闭 → 无歧义。

### 8.3 人工切换开口（必须有）

自动识别做**默认值**，不做必填输入。匹配面板顶部两组候选切换：

```
Match this invoice to:   [ Purchase Orders (2) ]  [ Agreements (1) ]
                          ↑ 自动识别：引用号 PO-585-2606-01 命中开放 PO
```

- 自动识别只做三件事：选中一组、预选中那条候选、**显示识别依据**
- 识别不出来 → 两组都列、不预选（即现有行为）
- 人可随时切换，切换即改路由
- **留痕**：`match_route` + `match_route_auto`。人工改过的记下来，既是审计需要，也是健康度信号——某 vendor 老被人工改路由说明其 `vendor_reference` 没登记对

> 显示识别依据不是装饰：财务看不到判断理由就只能每张自己再验一遍，自动识别等于没做。

### 8.4 两条匹配路径

- **house_account** → 月结发票 ↔ 本期未对账 Pickup Slip 集合（多对一，写入 `slip_ids`），命中的 slip 置 `reconciled`
- **recurring** → 发票 ↔ `schedule_type=period` 的排期行（vendor + 日期窗口 + 金额容差自动认领），写入 `schedule_id`，排期行置 `received`
- **milestone** → 发票 ↔ `schedule_type=milestone` 的排期行。**只有已 `accepted_by` 验收的阶段才可认领**（见 E8）；金额比对 `expected_amount`，超差走 E4

### 8.5 付款侧

两条路径最终都建 **Payment Application**，`po_id` 为空、`agreement_id` 有值——走 [epms-api/app/models/pa.py:22](../../../epms-api/app/models/pa.py) 已有的 PO-less 通道。

需要改的现有逻辑：

- **EPMS PA 列表过滤**：现在硬性 `po_id IS NOT NULL`（为过滤掉 OA Direct PA），需放宽为 `po_id IS NOT NULL OR agreement_id IS NOT NULL`
- **PA 收货闸门**：现在要求 matched + `gr_id`，需为 agreement 路由放行（house_account 用 `slip_ids` 满足、recurring 免收货）

---

## 9. 状态机与异常处理

**状态机**

- Agreement：`draft → in_review → active → expired / closed / cancelled`
- Pickup Slip：`open → reconciled → voided`；无小票旁支 `pending_ap_review → open`（AP 确认）或 `rejected`（AP 驳回）
- Payment Schedule 行：`pending → received / overdue → waived`

**异常清单**（这是财务日常工作的实质内容）

| | 场景 | 处理 |
|---|---|---|
| **E1** 有小票无发票 | 领了货，月结单还没到 | 正常。slip 停在 `open`；超过 N 天未被任何发票覆盖 → 提醒 |
| **E2 有发票无小票** | 月结单上有一笔没人登记 | **最关键的例外**。**由 AP 指定某人去处理**——照抄现有发票 match 指派机制：`AssignMatchRequest` + `Task(type=..., document_type="invoice", assigned_user_id)`，列表用 `_attach_match_assignees` 同款方式显示被指派人（[epms-api/app/api/v1/invoices.py:45-59](../../../epms-api/app/api/v1/invoices.py)）。被指派人补录 Pickup Slip 后完成对账，形成闭环——不是只报红让财务自己追 |
| **E3** 金额不符 | slip 合计 ≠ 发票金额 | 显示差额。小额（可配阈值）财务带理由接受；大额派回工程部核查 |
| **E4** 超容差 | recurring 发票超 `expected_amount ± tolerance_pct` | 升级完整审批 |
| **E5** 缺票逾期 | 过了 `overdue_after_days` 发票没来 | 报警给 owner + AP；确认那期确实无服务可标 `waived` |
| **E6** 重复小票 | 同一张小票登记两次 | `(agreement_id, store_slip_no)` 去重 |
| **E7 协议过期后来票** | 8/31 到期，9/3 来 8 月账单 | ⚠️ 必踩的坑。`grace_days` 内允许匹配但提示"协议已过期"；超出须 renew |
| **E8 阶段未验收先来票** | milestone 供应商提前开票 | 不允许认领。发票停在 unmatched 并提示"阶段 X 尚未验收"，同时给该阶段的验收责任人派确认任务 |
| **E9** 无小票提交 | 小票丢失，填了理由 | slip 进 `pending_ap_review`，AP 确认转 `open` / 驳回转 `rejected`（见 §5.2） |

**确认形态（用户决策）**：
- `recurring` → 容差内由用款部门在任务箱**一键确认**"本月服务正常"；超容差走完整审批。不做"每月重审一张一样的单"，也不做"完全免确认"
- `milestone` → 由用款部门/项目负责人确认**阶段达成并附验收证据**，写入 `accepted_by` / `accepted_at`
- `house_account` → 确认发生在**领货当下**（Pickup Slip），不在发票环节

---

## 10. 分期与迁移

**⚠️ 顺序：house_account 优先**（用户约束——目前有一批 Princess Auto 发票积压等此功能上线处理）。

### 第一期 · house_account（Princess Auto）

**1A · 让积压能处理**（最小可用）
1. Agreement 实体 + `agr` 审批流
2. 建 AGR：`vendor_reference` = 现有 Open PO 号，NTE 按去年实际支出估
3. 发票路由到 Agreement（引用号识别 + 人工切换开口）
4. **存量结清通道**：允许在**无 Pickup Slip** 的情况下匹配并付款，标记 `legacy_settlement` + 强制填理由

做完这一步积压的发票即可走完流程，不必等对账功能就绪。

**1B · 新流程上线**
1. Pickup Slip 拍照上传 —— 复用 OA 已有的 [ReceiptScanButton.tsx](../../../oa/src/components/ReceiptScanButton.tsx) + `POST /api/v1/ocr/receipt`（返回 vendor / date / description / total_amount / tax_amount / currency，字段正好够用）。`accept="image/*"` 在手机浏览器直接唤起相机，**不需要做 App**
2. 自动对账（发票 ↔ 本期未对账 slip 集合）+ 差额呈现
3. E2 / E3 例外闭环
4. 工程部培训：领货当场拍照上传
5. **并行期 1–2 个月**：纸单照旧交财务，与系统数据双轨核对，确认无遗漏后停纸
6. 老 PO 确认无未付发票后置 `closed`（可用 PO 上已有的 `has_unpaid_invoice` 计算字段，[epms-api/app/schemas/po.py:150](../../../epms-api/app/schemas/po.py)）

> **⚠️ `legacy_settlement` 护栏**：这个通道本质是"无凭证付款"，为积压而开。**1B 上线后必须收窄**——限定角色 + 强制理由 + 在协议详情单列统计。否则它会变成绕过对账的常用后门。

### 第二期 · recurring 周期付款（网络、电话等）

建 `agreement_payment_schedule` 表（**按通用结构建，含 milestone 列，为第三期留位**）、周期排期生成、容差内一键确认、缺票逾期告警。逐个建协议，观察一到两个周期验证自动认领与告警准确性。

### 第三期 · milestone 阶段付款

阶段排期录入、阶段验收确认（`accepted_by` + 验收附件）、E8 未验收拦截。

**前置**：必须先完成与现有 PA `prepayment` / `settlement` 机制的映射核对（§13 第 9 条）——阶段付款里的预付阶段要落到已有机制上，不能另造并行的一套。这是第三期排在最后的原因。

### 贯穿两期

- OA Direct PA 划边界（§4.1）
- 防漂移探测：同 vendor 6 个月内 ≥3 次 Direct PA → 提示建 Agreement

**历史数据不迁移**（原系统为集团总部 OA，数据无法拉取）。需要累计数完整的协议，在建档时手工填一个"期初已付"起始值。

---

## 11. 实现注意事项（项目既有坑）

1. **新表 alembic 迁移**：写迁移前先查 `alembic heads`，`down_revision` 必须挂真实链尾，避免双 head
2. **权限走 Access Control 矩阵**：新增 `epms.agreement.read/write/approve`、`epms.agreement.slip.create` 等权限码，**不要硬编码角色门禁**
3. **Portal 入口**：走 `navConfig` 的 `anyPermission`，不要各页自建 NAV_SECTIONS
4. **Decimal 序列化**：Pydantic 把 Decimal 发成 JSON 字符串，前端须 `Number()` 转换
5. **UI 文案全英文**（注释可中文）；样式以 EPMS 为模板，用 shell 的 `Button` 不要裸 `<button>`
6. **下拉/popover 必须 `createPortal` 到 body + fixed**，逃 overflow
7. **单据号生成**：沿用 max 尾号+1 + advisory lock，避免撞唯一约束
8. **多服务消费者清点**：改 `invoices` / `payment_applications` 必须清点 epms-api / expense-api / approval-api / finance-api 各自的模型与镜像，逐列核对物理表
9. **前端 tsc 基线**：epms 前端基线 59 tsc / 220 lint，门禁 `tsc -p tsconfig.app.json`；不得增加
10. **新增环境变量须在 Dockerfile 里 `ARG` + `ENV`**，否则前端回落 localhost（已三犯）
11. **OCR 端点归属**：`/api/v1/ocr/receipt` 在 expense-api，Agreement 在 epms-api。跨服务调用 vs 移植组件，实现时定（倾向跨服务复用，避免重复实现）
12. **测试库禁并发**：同一时刻只跑一个 epms 套件；epms-api 测试需覆盖 `POSTGRES_*` 到本地 docker，worktree 还须传 `JWT_SECRET_KEY`

---

## 12. 测试策略

**后端（epms-api，API 级端到端）**
- Agreement CRUD + 状态机 + `agr` 审批流转
- 候选池：active/expired/closed/grace 期内外的进出
- 引用号解析：命中开放 PO / 命中 Agreement number / 命中 `vendor_reference` / 老 PO 未关闭时的歧义（应按"只查开放 PO"规避）
- 人工切换路由：`match_route_auto` 正确翻转
- house_account 对账：多 slip 对一发票、差额计算、slip 状态翻转、重复 slip 拒绝（E6）
- E2 指派闭环：AP 指派 → 任务生成 → 被指派人补录 slip → 对账完成
- 小票必填与例外：无附件且无理由 → 拒绝；有理由 → `pending_ap_review`；AP 确认/驳回状态流转（E9）
- recurring 认领：容差内自动认领、超容差不认领（E4）、逾期标记（E5）
- milestone：未验收阶段不可认领（E8）；已验收阶段正常认领；`amount_pct` 与 `expected_amount` 互算
- 过期后 grace 期内/外匹配（E7）
- `legacy_settlement` 路径：无 slip 可匹配、理由必填、角色限制
- NTE：跨 80/100/120 阈值触发通知但**不阻断**
- PA 生成：`po_id` 为空 + `agreement_id` 有值时，PA 列表可见、收货闸门放行

**前端（epms）**
- 匹配面板两组切换、自动预选、识别依据展示
- Pickup Slip 拍照上传 + OCR 预填 + 手机浏览器唤起相机
- 协议详情 NTE 进度条与超额标识

**回归基线**：epms-api 现有失败数 69（存量），不得增加；approval-api 53 全绿。

---

## 13. 未决事项

| # | 事项 | 需谁定 |
|---|---|---|
| 1 | CRA Notice 199：能否只凭月结单抵 ITC，还是必须留每张店头小票 | **风险已由"小票强制上传"覆盖**（§5.2）——留存每张小票满足最严解释。剩余风险仅在 `missing_slip_reason` 那条例外路径，请会计确认该路径下 ITC 是否仍可抵 |
| 2 | Portal Admin 审批流配置 UI 是否自动枚举 `workflow_defs` key | 实现时核实 |
| 3 | OCR 端点跨服务调用 vs 移植组件 | 实现时定 |
| 4 | 全部数值阈值：`tolerance_pct`、`overdue_after_days`、`grace_days`、E1 未覆盖提醒天数、E3 小额差异阈值、renew 到期前提醒天数 | 全部做成**协议级可配字段**（`grace_days` 已在 §5.1，其余同理），由业务定初值 |
| 5 | Agreement 建档权限归属哪些角色 | 用户（配 Access Control 矩阵时定） |
| 6 | `legacy_settlement` 通道限定给哪个角色使用 | 用户（1A 上线前定；1B 后须收窄，见 §10） |
| 7 | `agr` 审批链的具体步骤与金额阈值 | 用户（Portal Admin 可自行调整，种子值见 §6） |
| 8 | 价目表（`agreement_price_lines`）是否需要 | 首期不做，留字段位置 |
| 9 | **阶段付款与现有 PA `prepayment`/`settlement` 机制的字段映射** | 实现第三期前必须逐字段核对（`pa_type`、`prepayment_pct`、`prepayment_applied`、`expected_settlement_date`、`settlement_*`），复用而非另造 |
| 10 | milestone 的阶段验收责任人如何确定（协议上指定 / 按部门推导） | 用户（第三期前定） |

---

## 14. 参考资料

- [Blanket PO vs Standard PO — PLANERGY](https://planergy.com/blog/blanket-po-vs-standard-po/)
- [A Complete Guide To Blanket Purchase Order — ProcureDesk](https://www.procuredesk.com/blanket-purchase-order/)
- [2-Way vs 3-Way PO Matching — Mindsprint](https://www.mindsprint.com/resources/blogs/2-way-vs-3-way-po-matching)
- [Invoice Matching: 2-Way, 3-Way, and 4-Way Explained](https://invoicedataextraction.com/blog/invoice-matching-guide)
- [Non-PO Invoice Processing: Workflow, Controls, Best Practices](https://invoicedataextraction.com/blog/non-po-invoice-processing)
- [Standing offers and supply arrangements — CanadaBuys](https://canadabuys.canada.ca/en/tender-opportunities/standing-offers-and-supply-arrangements)
- [Outlining Purchasing Agreements in SAP S/4HANA](https://learning.sap.com/courses/sap-s-4hana-contract-management/outlining-purchasing-agreements-in-sap-s-4hana)
- [SAP Service Entry Sheet Tutorial — ERProof](https://erproof.com/mm/free-training/sap-service-entry-sheet/)
- [Input tax credits — Canada.ca (CRA)](https://www.canada.ca/en/revenue-agency/services/tax/businesses/topics/gst-hst-businesses/calculate-prepare-report/input-tax-credit.html)
- [Canada GST/HST Invoice Requirements: CRA Compliance Guide](https://invoicedataextraction.com/blog/canada-gst-hst-invoice-requirements)
- [CRA disallows ITCs if a business name does not match — CBA](https://cba.org/sections/commodity-tax-customs-and-trade/member-articles/cra-disallows-itcs-if-a-business-name-does-not-match/)
- [CRA Notice 199 — Procurement Cards（未读到正文，待核）](https://www.canada.ca/en/revenue-agency/services/forms-publications/publications/notice199/procurement-cards-documentary-requirements-claiming-input-tax-credits.html)
- [Recurring Invoices in Accounts Payable — Stampli](https://www.stampli.com/resources/recurring-invoices-in-accounts-payable/)
- [AP Automation: A Solution for Lost or Missing Invoices — Medius](https://www.medius.com/blog/ap-automation-a-solution-for-lost-or-missing-invoices/)
