# 采购协议 Phase 1B — Recurring 周期付款 + 录入完善

**日期**：2026-08-10
**前置**：[2026-08-06 采购协议设计](2026-08-06-purchase-agreement-design.md)（下称"原设计"）、Phase 1A 实现（`feature/purchase-agreement`）
**状态**：待实施

---

## 0. 背景

Phase 1A 只落了 `house_account`。`agreement_type` 的另外两个取值（`recurring` / `milestone`）目前是纯标签——能选、能筛，但没有任何行为。这是原设计"第二期 / 第三期"的分期结果，不是遗漏（模型注释 `only house_account is wired in 1A`）。

本期把第二期（recurring）整体做完，第三期（milestone）只做录入。同时补两个 Create 页的缺口：预算科目选择器、协议附件。

**用户拍板记录**

| # | 决策 | 取值 |
|---|---|---|
| 1 | Recurring / Milestone 做到哪一步 | Recurring 全套行为；Milestone 仅录入+展示 |
| 2 | "每月 5 号前收到发票"的语义 | **到票日**（驱动缺票告警）。付款日仍由发票 payment terms 决定，协议不重复存 |
| 3 | 每期预期金额 | **选填**。填了才做容差比对；不填只按顺序认领 |
| 4 | 预算选择器形态 | 照抄 PR 的四级级联（Department → Cost Center → L1 → L2） |
| 5 | Recurring 履约确认 | **做**（原设计补偿控制第 5 条） |
| 6 | 附件是否必填 | **可选**，任何状态都不强制 |
| 7 | Quarterly 起始月 | **显式字段**，不从 `valid_from` 推导 |

---

## 1. 数据模型

### 1.1 新表 `agreement_payment_schedule`

按原设计 §5.3 的通用结构建，**milestone 专用列一并建出**（本期只写 `milestone_name` / `amount_pct` / `expected_amount` / `expected_date` / `trigger_condition`，验收列留空）。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `agreement_id` | UUID FK → `purchase_agreements` ON DELETE CASCADE | 索引 |
| `schedule_type` | String(20) | `period` \| `milestone` |
| `sequence` | Integer | 期次/阶段序号，从 1 起 |
| `expected_amount` | Numeric(15,2) nullable | 选填（决策 3） |
| `expected_date` | Date nullable | period=到票日；milestone=预计达成日 |
| `status` | String(20) | `pending` \| `received` \| `overdue` \| `waived` |
| `invoice_id` | UUID nullable | 认领到的发票 |
| `period_label` | String(20) nullable | 见 §4.2 |
| `tolerance_pct` | Numeric(5,2) nullable | 建行时从协议级默认拷贝，可逐行改。**单位是百分数**：`5.00` = ±5% |
| `overdue_after_days` | Integer nullable | 同上 |
| `milestone_name` | String(255) nullable | |
| `amount_pct` | Numeric(5,2) nullable | 占合同总额百分比 |
| `trigger_condition` | Text nullable | |
| `accepted_by` | UUID nullable FK → `users` | 见下方说明 |
| `accepted_at` | timestamptz nullable | |

**`accepted_by` / `accepted_at` 由 `schedule_type` 决定语义**，两种确认共用这两列：

- `schedule_type='period'` → 履约确认（"本月服务正常"），本期实现
- `schedule_type='milestone'` → 阶段验收，本期不实现

原设计把两者描述为"确认语义不同"但共用一张表。确认痕迹的**结构**完全相同（谁、什么时候），复用可以让 milestone 将来接验收时不必再加列。代价是读代码的人必须先看 `schedule_type` 才知道这条记录是什么意思——所以模型和 API schema 上都要写明。

### 1.2 新表 `agreement_attachments`

完全照抄 `pr_attachments`：`agreement_id`(FK CASCADE) / `filename` / `content_type` / `file_size` / `file_data` / `storage_key`。存储走 file-api（`upload_to_file_server` / `proxy_download`），`file_data` 仅作历史回落，新写入一律用 `storage_key`。

### 1.3 `purchase_agreements` 新增 7 列

| 列 | 类型 | 说明 |
|---|---|---|
| `cost_center_id` | UUID nullable，索引 | 与 `budget_code` 配对，和 PR 一致 |
| `recurring_type` | String(10) nullable | `weekly` \| `monthly` \| `quarterly` \| `yearly` |
| `expected_invoice_day` | Integer nullable | 到票日。weekly=1..7（ISO，周一=1）；其余=1..31 |
| `anchor_month` | Integer nullable | 1..12。**仅 quarterly / yearly 使用**，见 §4.1 |
| `expected_amount_per_period` | Numeric(15,2) nullable | 生成排期行时的每行默认值 |
| `tolerance_pct` | Numeric(5,2) nullable | 协议级默认容差 |
| `overdue_after_days` | Integer nullable，server_default `7` | 协议级默认逾期天数 |

`recurring_type` / `expected_invoice_day` 在 `agreement_type='recurring'` 时必填，其余类型必须为空——**在 schema 层校验，不下沉到 DB 约束**（与现有协议行数据兼容，且错误信息更可读）。

### 1.4 `invoices` 新增 1 列

`schedule_id` UUID nullable FK → `agreement_payment_schedule`（原设计 §5.4 已规划）。

> **共享表影响已核实**：`invoices` 被 expense-api、finance-api、approval-api 三个服务镜像（`expense-api/app/models/epms_mirrors.py:61`、`finance-api/app/models/mirrors.py:94`、`approval-api/app/models/invoice.py:9`），但三者都是**显式列清单**，不声明的新列它们看不见。因此加一个可空列**不产生部署顺序约束**——不同于 Phase 1A 的 `payment_applications.agreement_id`（那次 finance/expense 镜像**声明**了该列，所以镜像必须晚于迁移起来）。本期无此约束，三个服务镜像不需要改。

---

## 2. 预算科目选择器

Create / Edit 页把现在的自由文本 `Budget Code` 换成四级级联，**直接复用 PR 创建页的现成实现**（`useBudgetOverview` + `useCostCenters`，见 `epms/src/pages/pr/PrCreatePage.tsx:60-95`）：

```
Department（已有字段，作为第一级）
  └→ Cost Center（useCostCenters({department_id, active_only})）
       └→ L1 分组（budgetData.l1_groups，全量 active）
            └→ L2 科目（selectedL1.accounts）→ 写入 budget_code
```

协议同时存 `cost_center_id` 和 `budget_code`，与 PR 一致。

**明确不做**：预算余额校验、预算占用（committed）。PR/PO 走占预算逻辑是因为它们是订单；协议是授权容器，把它接进预算占用是独立话题。本期只做选择与展示。

**部门联动**：切换 Department 后清空 Cost Center / L1 / L2 三级选择——旧成本中心可能不属于新部门，留着会写进一个越界的 `budget_code`。

---

## 3. 附件

新增 `agreement_attachments` 表 + `epms-api/app/api/v1/agreement_attachments.py` 路由（list / upload / download / delete），全部照抄 `pr_attachments.py`。

- **Create 页**：选文件暂存在前端 state，`POST /agreements` 拿到 id 后再逐个上传（与 PR 完全一致的两步）
- **Detail 页**：附件列表，可下载、可删除
- **可选，任何状态都不强制**（决策 6）
- 权限：读随 `epms.agreement.read`，写/删随 `epms.agreement.write`

---

## 4. Recurring 行为

### 4.1 排期生成

**时机**：审批通过、状态转 `active` 的那一刻生成整个有效期的排期行。

**生成位置**：**epms-api 的 `/agreements/{id}/action` 端点**，在 approval-api 返回后检查新状态是否为 `active`。不放在 approval-api 的 `_post_approve_agr`——那样 approval-api 就得镜像 `agreement_payment_schedule`，为一个纯 EPMS 概念增加一个跨服务耦合点。approval-api 继续只管状态机。

**幂等**：生成前先查该协议是否已有 `schedule_type='period'` 的行，有则跳过。审批可能因为 resync 之类的操作重入。

**期次锚点**

| `recurring_type` | 期长 | 锚点 | `expected_date` |
|---|---|---|---|
| `weekly` | ISO 周（周一起） | `valid_from` 所在 ISO 周 | 该周第 `expected_invoice_day` 天（1=周一） |
| `monthly` | 日历月 | `valid_from` 所在月 | 该月 `expected_invoice_day` 号 |
| `quarterly` | 3 个月 | **`anchor_month`** 起每 3 个月 | 该期**首月**的 `expected_invoice_day` 号 |
| `yearly` | 12 个月 | **`anchor_month`** 起每 12 个月 | 该期**首月**的 `expected_invoice_day` 号 |

quarterly / yearly 的**首期判定**：期起始月在年历上构成无限序列 `{anchor_month, anchor_month+3, ...}`（yearly 则是每年同一个 `anchor_month`）；首期 = 起始月 ≤ `valid_from` 的**最后一个**期，即覆盖 `valid_from` 的那一期。若该期的 `expected_date` 早于 `valid_from`，由下方"首期跳过"规则处理。

`anchor_month` 前端默认预填 `valid_from` 的月份，**可改**。这是用户明确要求的：很多季度账单不按 1/4/7/10 走，而是按合同起始月走（起始月 2 月 → 2/5/8/11），而合同签订日又未必等于账单周期起点。把锚点隐式绑在 `valid_from` 上，两者不一致时整条排期就错位。

**边界规则**

- **首期跳过**：若首期的 `expected_date` 早于 `valid_from`，跳过该期，从下一期开始。（协议 8/20 生效、每月 5 号到票 → 8 月那张票在生效前就该到了，不该排。）
- **末期纳入**：只要**期起始日** ≤ `valid_to` 就生成该期，即使其 `expected_date` 晚于 `valid_to`——月结票总在期末之后才到，这与 `grace_days` 的设计意图一致。
- **月末钳位**：`expected_invoice_day=31` 遇 2 月取 2 月最后一天。
- **行数上限**：单次生成上限 **500 行**，超出则拒绝并提示缩短有效期或改周期。防止误填（weekly × 10 年 = 520 行）把表撑爆。

**编辑影响**：`draft` / `returned` 状态下改周期参数不需要处理（此时还没生成）。`active` 后改参数**不重算已生成的行**——已经有发票认领在上面了。若确需重排，走"关闭旧协议、建新协议"，与调价的处理方式一致。

### 4.2 `period_label`

统一用**期起始年月**，格式 `YYYY-MM`（weekly 用 `YYYY-Www`）。quarterly 起始月 2026-02 → `2026-02`；yearly 起始月 2026-03 → `2026-03`。

不用 `2026-Q1` 这种写法：非标准季度下"Q1"指哪三个月会产生歧义，而起始年月永远无歧义。

### 4.3 发票认领

发票匹配到 `recurring` 协议时：

1. 候选 = 该协议 `schedule_type='period'` 且 `status ∈ (pending, overdue)` 的行
2. **按 `sequence` 升序取第一条**（FIFO）
3. 若该行填了 `expected_amount`：发票金额须落在 `expected_amount × (1 ± tolerance_pct)` 内
4. 满足 → 写 `invoice.schedule_id`，行置 `received`，行记 `invoice_id`
5. 不满足（**无候选行**，或**金额超容差**）→ 不自动认领，发票停在 `match_review`，由人工指定期次

> **为什么是 FIFO 而不是按发票日期匹配期次**：周期账单的到达日常常落在**下一期**内（8 月的网络费 9 月 3 日才开票），按 `invoice_date` 落在哪个期窗口去选行会系统性地错配一整期。周期账单本身是按顺序来的，FIFO 更贴合实际且不会猜错。代价是发票乱序到达时需要人工指定——这正是第 5 步的兜底。

### 4.4 履约确认（决策 5）

recurring 免 GR，**履约确认是它唯一的代偿**。不做的话，网络断了一个月，发票照样自动认领、自动进 PA、自动付掉，全程无人确认服务交付。

- **触发**：排期行被认领（自动或人工）后，建 `confirm_period` 任务
- **派给**：协议的 `owner_id`；`owner_id` 为空则派给协议 `department_id` 的在职部门经理
- **动作**：任务箱一键确认"本期服务正常" → 写 `accepted_by` / `accepted_at`
- **闸门**：`recurring` 协议的发票要建 PA，其排期行**必须已确认**，否则 `POST /pa` 返回 422 并说明原因

命名 `confirm_period` 沿用现有 `confirm_settlement` 的构词；与 approval-api 的 `mil` / `cfm` 不冲突（那两个是 OA 的 expense milestone / confirmation，见原设计 §5.3 的命名冲突提醒）。

这是个**普通任务**，不是审批流——不进 `workflow_defs`，不需要新的 action key。

### 4.5 缺票逾期告警

挂在 epms-api 已有的每日后台循环上（`app/tasks/daily_followup.py` 的同一模式，lifespan 启动）：

- **状态扫描**（无条件跑）：`status='pending'` 且 `expected_date + overdue_after_days < today` 的行置 `overdue`
- **通知**：向协议 `owner_id` 发缺票提醒，受新开关 `notification_settings.agreement_overdue_enabled` 控制，**默认 `true`**

> 默认值是刻意的。`daily_followup_enabled` 默认 OFF 导致上线后没人知道要去 admin 打开、提醒一直没发（见 `project_uniops_daily_followup_toggle`）。缺票告警是本期功能自带的核心价值，默认关等于白做。开关的作用是"吵了可以关掉"，不是"要用得先找到它"。

---

## 5. Milestone 本期边界

**做**：阶段清单录入（Create/Edit 页可增删行）、详情页展示进度。字段 = `milestone_name` / `sequence` / `expected_amount` 或 `amount_pct` / `expected_date` / `trigger_condition`。

阶段行**随协议一同保存**（draft 阶段就落库），不像 period 行那样等审批通过才生成——阶段清单本身就是要送审的内容之一。

`amount_pct` 与 `expected_amount` 二选一录入、另一个推算，**推算基数是 `not_to_exceed`**（协议上唯一的总额字段）。未填 `not_to_exceed` 时 `amount_pct` 不可用，只能录绝对金额——这条要在 UI 上直接体现（NTE 为空时百分比输入框禁用并给出原因），不要让用户填完才报错。

**不做**（留到第三期，原设计已定）：阶段验收确认（`accepted_by` 写入）、E8"未验收不许认领"拦截、与现有 PA `prepayment` / `settlement` 机制的映射核对。

**因此**：`milestone` 协议的发票走与 `house_account` 相同的通用路径——挂到协议、不挂排期行、不写 `schedule_id`。阶段行本期纯粹是**计划的记录与展示**，不参与任何匹配或闸门判定。这一点必须在 UI 上说清楚（阶段区块加一句说明），否则用户会以为建了阶段就有阶段控制。

---

## 6. 权限与审批

- **无新权限键**。排期行与附件的读写跟随 `epms.agreement.read` / `epms.agreement.write`
- **不动 `agr` 审批流**（仍是 dept_manager → procurement_manager → finance_manager）
- `confirm_period` 是普通任务，执行者 = 被派人本人

---

## 7. 迁移与部署

**一个迁移**（epms-api，`ag03`，`down_revision = ag02`）：建 2 张新表、`purchase_agreements` 加 7 列、`invoices` 加 1 列。

**无 identity 迁移**（无新权限键）。**无 approval-api 改动**。

**部署顺序无约束**（§1.4 已论证）。需重建的镜像：`epms-api`、`epms-web`。

`agreement_overdue_enabled` 走 `notification_settings` JSONB，**不需要迁移**（与 `daily_followup_enabled` 同一机制）。

---

## 8. 测试要点

**排期生成**（最容易错的部分，逐条覆盖）

- 四种 `recurring_type` 各生成正确期数与 `expected_date`
- `anchor_month` = 2 的 quarterly → 2/5/8/11 月，**且与 `valid_from` 月份不同**（这是决策 7 的核心场景，必须专门有一例）
- 首期 `expected_date` < `valid_from` → 跳过
- 期起始日 ≤ `valid_to` 但 `expected_date` > `valid_to` → 仍生成
- `expected_invoice_day=31` 遇 2 月 → 钳到 2/28（含闰年 2/29 一例）
- 超 500 行 → 拒绝
- 重复审批不重复生成（幂等）

**认领**：容差内自动认领、超容差不认领并停在 `match_review`、无候选行、FIFO 顺序正确、`expected_amount` 为空时跳过金额校验。

**履约确认闸门**：未确认的 recurring 发票建 PA → 422；已确认 → 放行。

**逾期扫描**：边界日（正好等于 `expected_date + overdue_after_days` 当天不算逾期）、开关 OFF 时不发通知但**状态照样扫**。

**Milestone 录入**：`amount_pct` ↔ `expected_amount` 互算正确；`not_to_exceed` 为空时 `amount_pct` 不可用；阶段行在 draft 状态即落库；milestone 协议的发票**不**写 `schedule_id`。

**回归基线**（动手前自己量，别照抄）：epms-api 套件失败集合须与基线逐条相同；`epms` 前端 `npx tsc -p tsconfig.app.json` 错误数须与基线一致。

---

## 9. 已知风险与未决

- **每日循环是单实例假设**。epms-api 若扩到多副本，逾期扫描会重复跑。这是 `daily_followup` 既有的问题，本期沿用同一模式，不新增也不解决。
- **FIFO 认领在发票乱序到达时会认错**（供应商补开上上个月的票）。兜底是人工在 `match_review` 指定期次。若实际运行中乱序频繁，再考虑从 OCR 抓账单周期。
- **Milestone 半成品的表达风险**。阶段行建了但不参与控制，UI 说明不到位会让用户误以为有阶段闸门。
- **`accepted_by` 双语义**。复用两列服务 period 履约确认与 milestone 阶段验收，读代码必须先看 `schedule_type`。已在模型与 schema 注释中写明，但仍是认知负担。
- **分支距离**。本期建立在 `feature/purchase-agreement`（25 提交）之上，而 `origin/main` 已前进 167 提交。再叠一期功能会让最终 rebase 更难。节奏问题，非设计问题——若决定先落地 1A 再开新分支做本期，本设计不受影响。

---

## 相关

- [2026-08-06 采购协议设计](2026-08-06-purchase-agreement-design.md) —— §5.3 排期表原始结构、§第二期/第三期分期依据、补偿控制清单
- Phase 1A 实施计划：`docs/superpowers/plans/2026-08-06-purchase-agreement-phase1a.md`
