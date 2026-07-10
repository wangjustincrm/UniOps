# Invoice Tax Line 自动预填 + Match PO 指派与偏差复核 — 设计

**Date**: 2026-07-09
**Status**: Approved by user (brainstorm session)
**Scope**: EPMS Invoice(epms + epms-api);两个独立功能,分两个 commit 交付

---

## Feature 1 — Tax Line 自动预填

### 问题

发票天然带税额(header `tax_amount`,OCR 自动提取),但 ITC 申报需要按税码拆分的
`invoice_tax_lines`。目前每张发票都要财务在详情页手动 Add Line 选税码,没拆的进
ITC uncoded 异常清单。绝大多数发票是单一税码、全额应税 — 应该自动填好,财务只在
发现问题时修改。

### 方案:服务端创建时按有效税率反推

在 `epms-api invoice_crud.create()` 成功落库后(同事务):

1. 前置条件:`tax_amount > 0` 且 `amount > 0`(免税/零税发票不处理,维持现状)
2. 有效税率 `eff = tax_amount / amount`
3. 从 mdm-api 拉取激活税码(复用/新建轻量 http client),筛选
   `|rate − eff| ≤ 0.005`(±0.5 个百分点)的候选
4. **唯一命中** → 插入一条 `InvoiceTaxLine`:
   - `tax_code` = 命中税码,`taxable_amount` = 发票 pre-tax 全额
   - `tax_amount` = 发票 header 税额原值(非 base×rate 重算,保留供应商的分币)
   - `recoverable` = 税码的 recoverable
   - `line_no` = 1
5. 零命中或 ≥2 个命中(容差内并列,如 GST 5% vs 某 4.8% 税码同时落在容差内)→
   **不预填**,照旧留空进 uncoded 清单(保守,宁缺勿错)

### 边界与决策

- **mdm 调用失败 fail-open**:捕获一切异常记 warning,发票创建不受影响(与
  finance_sync 同风格)
- **仅创建时触发**:后续 PATCH 改 header 税额/金额不自动重算 — 有人改 header
  说明已在人工介入,自动重算可能覆盖财务的手工拆分
- **不回填存量**:PMS 导入直写 DB 不经过此路径;历史发票不动
- OA PA-DIR 发票不在本期范围(它走 expense-api,另一套)

### 影响面

- `epms-api/app/crud/invoice.py` create() 尾部 + 新 `services/tax_infer.py`(或并入
  现有 mdm client)
- 无迁移(`invoice_tax_lines` 表已存在),无前端改动(InvoiceTaxSection 原样展示/编辑)

---

## Feature 2 — Match PO 指派 + 偏差复核

### 问题

Match PO 工作量大且集中在 AP。AP clerk 需要能把某张发票的 match 工作指派给最了解
该采购的人(任何用户,例如 PR requester),被指派人收邮件提醒;为控风险,被指派人
match 出**非零金额偏差**时需发起指派的 AP 复核,零偏差直接生效。

### 指派(assign-match)

- 新端点 `POST /invoices/{id}/assign-match {user_id}`,仅 AP 角色
  (system_admin/ap_clerk/finance_manager/finance_bp)可调;发票须处于
  `unmatched`/`exception`
- 创建 Task:`type="match_invoice"`, `document_type="invoice"`,
  `document_id=invoice.id`, `assigned_user_id=被指派人`,任务上记录 assigner
  (复核人),title/description 含发票号、vendor、金额
- 已存在未完成的 match_invoice 任务 → **改派**:更新 assigned_user_id 并重新通知
- 通知复用 `dispatch_task_notification`(email/Teams 按用户偏好,新模板 key
  `match_invoice`,Company Settings 可改文案),任务进 Task Inbox,链接直达发票

### 按任务授权(权限开口)

- `POST /invoices/{id}/match` 的守卫从 "仅 AP 角色" 放宽为:
  **AP 角色,或对该发票持有未完成 `match_invoice` 任务的用户**
- 发票可见性:复用 access_scope 既有的"自己有 open task 的单据可见"机制,确保被
  指派人能看到发票列表项和详情(含附件预览)
- 前端 Match 入口的 `MATCH_ROLES` 门禁同步放宽:AP 角色,或当前用户对该发票有
  未完成 match 任务

### 偏差复核(仅被指派人)

判定主体:match 调用者**不属于 AP 角色**(即纯任务授权进来的)→ 走复核规则;
AP 角色(含被指派的 AP)维持现状(容差内 matched / 容差外 exception)。

- 被指派人提交 allocations:
  - **全部分摊行 variance == 0** → 直接 `matched`,match 任务完成(现状语义)
  - **任一行 variance ≠ 0** → 发票进入新状态 **`match_review`**:分摊行、po_id、
    variance 等照常落库,但不算 matched;match 任务完成;给 assigner 创建
    `review_match` 任务 + 通知
- 新端点 `POST /invoices/{id}/match-review {action: "approve"|"reject", note?}`,
  复核任务持有人或任何 AP 可调,发票须处于 `match_review`:
  - `approve` → 按现有容差规则落定:容差内 `matched`(走 matched 的全部后置:
    notify requester 建 PA、finance 同步 posted),容差外 `exception`(走既有
    exception resolve 流程)
  - `reject`(note 必填)→ 发票回 `unmatched`,保留分摊数据供参考;重新创建
    match_invoice 任务给原被指派人(description 附驳回原因)+ 通知;review 任务完成

### 新状态 `match_review` 的影响面

| 位置 | 处理 |
|---|---|
| 前端 STATUS_CFG 徽章 | 新增 `match_review`(info 蓝,"Pending Review") |
| 列表筛选/queue 分区 | Unmatched 队列不含 match_review;复核人在详情页操作,列表加筛选项 |
| finance 同步 `_ap_status` | match_review → draft(未生效,不 posted) |
| 删除限制 | match_review 不可删(与 matched 同) |
| match 端点状态门 | 仍只接受 unmatched/exception(match_review 锁定,只能走 review 端点) |
| PA 创建资格 | 不受影响(PA 依赖 matched,match_review 不触发建 PA 任务) |

### 前端入口

1. **Upload 第二步**(分摊面板旁):"Assign to someone instead" — 选人后跳过自己
   分摊,直接指派并关闭弹窗
2. **Unmatched 列表行**:Assign/Reassign 按钮,显示当前被指派人(来自未完成任务)
3. **详情页**:同上;`match_review` 状态时,复核人/AP 看到 Review 面板
   (分摊+variance 摘要、Approve / Reject+note)
4. 用户选择器复用现有 userService.listAll(注意分页陷阱,用 listAll 不用默认 20 条)

### 通知模板

新增两个模板 key(默认文案内置,Company Settings notification_settings 可覆盖):
- `match_invoice`:"你被指派为发票 {invoice_number}({vendor},{amount})做 PO 匹配…"
- `review_match`:"{assignee} 已完成发票 {invoice_number} 的匹配,存在金额偏差
  {variance},请复核…"

(实际文案为英文,遵循 UI 全英文约定。)

---

## 测试计划(TDD)

### Feature 1(epms-api)
- 13% 税额 → 自动生成 HST-ON tax line(base/tax/recoverable 正确)
- 税额为 0 → 不生成
- 有效率不在任何税码容差内 → 不生成
- 容差内多个税码并列 → 不生成
- mdm 不可达 → 发票创建成功、无 tax line、无异常

### Feature 2(epms-api)
- assign-match:AP 可调、非 AP 403、重复指派=改派、任务与通知创建
- 任务授权:被指派人可调 match(原本 403);无任务的普通用户仍 403;任务完成后失效
- 被指派人零偏差 match → matched + 任务完成
- 被指派人非零偏差 match → match_review + review 任务给 assigner
- approve(容差内/外)→ matched / exception;reject → unmatched + 新 match 任务附 note
- AP 自己 match 不进 review(现状回归)
- match_review 状态下:match 端点 409、删除 409

### 前端
- typecheck 零新增错误;本地 dev 人工走查两条流程

## 交付

两个独立 commit(main,走 worktree):
1. `feat(epms-api): auto-prefill invoice tax line by effective-rate inference`
2. `feat(epms): assignable PO matching with variance review`

发布按标准流程(13 镜像全量 + app server 用户手动)。
