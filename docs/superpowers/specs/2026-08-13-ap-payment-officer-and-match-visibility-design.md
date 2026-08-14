# AP 减负与匹配差异可见性 — Payment Officer 角色 / 通知补全 / PA 差异面板

- 日期：2026-08-13
- 分支：`feature/ap-payment-officer-and-match-visibility`
- 基线：`origin/main` = `2cbf817`（协议采购上线版，= 生产）
- Worktree：`C:/Project/uniops-payofficer`

## 1. 背景

EPMS 发票匹配上线一段时间后，AP 反馈两类问题：

1. **责任问题**：发票 match PO 后金额不一致会给 AP 一个 review 任务，AP 不愿为差异承担批准责任，希望由管理者确认。
2. **积压问题**：AP Clerk 同时承担「AP Review」和「Payment」两类任务，Task Inbox 被两种任务淹没。

调研后确认，第 1 点的前提与实际实现不符（见 §2），因此**不新增审批流程**，而是通过澄清职责表述 + 补齐差异可见性来解决；第 2 点通过拆出专职 Payment 角色解决。

## 2. 调研结论（决定设计走向的事实）

以下均在 `2cbf817` 代码上核实。

### 2.1 `match_review` 不是「金额差异审批」

触发条件是 `require_review = not (is_ap or is_uploader)`（`epms-api/app/api/v1/invoices.py:396`），即**被指派的代理人**替 AP 做了 match 且存在非零差异。AP 本人或发票上传人做的 match，无论差多少都**不会**进入 review。

它的本质是**代填复核**（这条匹配链路填得对不对），不是付款授权。真正的付款批准发生在下游 PA 审批链（GM / Finance Manager）。

**推论**：AP 的顾虑主要是表述造成的认知问题，正确的解法是改文案 + 让差异在 PA 审批环节可见，而不是新增审批节点。

### 2.2 超容差走的是另一条路，且无第二双眼睛

差异超过 `invoice_match_tolerance_pct`（默认 5%）时状态是 `exception`，由 AP 调用 `resolve_exception` 单方面放行为 `matched`（`epms-api/app/crud/invoice.py:1296`），全程没有任何复核。客观风险高于 `match_review`。

**本次不改其授权**（用户未要求），但补齐它的任务与通知（见 §4.2）。

### 2.3 Match Review 通知发错人

建任务时同时写了 `assigned_role="ap_clerk"` 和 `assigned_user_id=my_task.created_by`（派单人本人，`invoices.py:535`）。而通知收件人解析逻辑中，只要 `assigned_user_id` 非空就**只发这一个人**，不走角色池；共享邮箱也**仅在 `assigned_user_id` 为空时**才生效（`app/services/notification.py:186`）。

**这是「整组 AP 收不到」的直接原因。**

### 2.4 Exception 根本不创建任务

代码库中 `exception` 状态不产生任何 `Task`。AP 收不到提醒不是通知丢失，而是**任务不存在**，Task Inbox 中也不可见。

### 2.5 `fire_and_forget_notify` 在 commit 之前触发（系统性）

该函数要求调用前先 commit（其 docstring 明示），因为后台协程会**另开 session** 按 id 查任务，查不到就静默 return。而 `get_session` 依赖在**端点返回后**才 commit（`app/db/session.py:26-34`）。

逐函数审计 12 个调用点，**10 个在所属函数内没有前置 commit**，只有 `decline_match` / `assign_match` 修过：

| 文件 | 行 | 所属函数 | 前置 commit |
|---|---|---|---|
| `api/v1/gr.py` | 100 | `create_gr` | ✗ |
| `api/v1/gr.py` | 138 | `gr_action` | ✗ |
| `api/v1/invoices.py` | 172 / 190 | `_create_or_renotify_create_pa` | ✗ |
| `api/v1/invoices.py` | 205 / 223 | `_create_or_renotify_confirm_receipt` | ✗ |
| `api/v1/invoices.py` | 545 | `match_invoice` | ✗ ← 本次范围 |
| `api/v1/invoices.py` | 810 | `decline_match` | ✓ |
| `api/v1/invoices.py` | 907 | `assign_match` | ✓ |
| `api/v1/invoices.py` | 964 | `match_review` | ✗ |
| `api/v1/po.py` | 239 | `po_action` | ✗ |
| `api/v1/pr.py` | 234 | `pr_action` | ✗ |

这是竞态而非必现（后台协程取连接的耗时通常长于请求提交），会导致通知时灵时不灵。

**范围裁定**：本次只修范围内的 2 处（`match_invoice` 的 review 任务、新增的 exception 任务）。其余 8 处覆盖 PR/PO 审批、GR、create_pa 通知，**单独立条、单独发布** —— 每加一个 `commit()` 都会改变事务边界（其后代码抛错时先前写入不再回滚），需要逐个确认落点，不应夹带在一个 UI 需求里。

### 2.6 协议凭证接口对 PA 审批人 403

`GET /invoices/{id}/agreements/{aid}/receipts` 的门禁是 `_require_invoice_match_access` = AP / 发票上传人 / 持有 match 任务者（`invoices.py:648-686`）。**PA 审批人（GM、Finance Manager、部门经理）不在其中，会 403**，而他们正是差异面板的目标读者。此外该路由只在 `candidates_for_vendor`（在途协议）中查找，已关闭的历史协议会 404。

**推论**：House Account 差异展示**不能**是纯前端改动，须在 PA 审批人可读的响应上补字段（见 §5.2）。

### 2.7 付款权限只有一个权威落点

EPMS 的 `process` 动作不自行判权，直接转发 finance-api 的 `execute_payment`（`epms-api/app/api/v1/pa.py:524`）；三个付款入口（EPMS PA / OA Direct PA / Finance 批次）都汇聚到那里。因此 SoD 只需改 `finance-api/app/crud/payment_execute.py::_PAY_ROLES` 一处。

`_user_role_codes` 已做「主角色 ∪ 附加角色」并集查询，**附加角色开箱即用**。

## 3. 范围

### 做

- **W1** 新增 `payment_officer` 附加角色，`process_pa` 任务改派，付款权限真 SoD
- **W2** 补齐 Match Review / Exception Resolve 两类任务与通知
- **W3** PA Detail 差异面板（PO 路由行级对比 + House Account 凭证差异）
- **W4** Review 任务与弹窗文案改写

### 实施分期

W1 与 W2/W3/W4 之间没有代码依赖（前者是角色与付款授权，后者是发票匹配可见性），仅共用 epms-api 与同一次发布。实现计划据此分两阶段推进、各自独立验证：

- **Phase 1**：W2 + W3 + W4（发票匹配可见性与通知，风险低、可独立点验）
- **Phase 2**：W1（跨服务角色与 SoD，含迁移与存量改派）

两阶段合并为一次发布，Phase 2 的迁移顺序约束见 §7。

### 不做（明确排除）

- 不新增任何审批流程节点、单据状态或审批链
- 不调整 `invoice_match_tolerance_pct` 容差值（用户本轮未要求）
- 不改 `resolve_exception` 的授权（谁能放行超容差发票，维持现状）
- 不修 §2.5 中另外 8 处 commit 顺序缺陷（单独立条）
- 协议 PA 的 recurring / milestone 类型不展示差异（仅 House Account）

## 4. W1 — Payment Officer 角色

### 4.1 决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 角色形态 | **附加角色**（`user_roles`） | 不动任何人的主角色与既有作用域；Portal Admin 勾选即可授予/回收。沿用 `erp_pa_officer` 先例 |
| 付款权限 | **真 SoD**：从 `_PAY_ROLES` 移除 `ap_clerk` | 职责隔离彻底、审计干净 |
| 存量任务 | **脚本改派** | AP 积压当场清空 |

**SoD 的可用性风险已被自然兜住**：`_PAY_ROLES` 移除 `ap_clerk` 后仍保留 `finance_manager` / `finance_bp` / `system_admin`，新角色持有人休假不会导致付款卡死，无需额外后备机制。

### 4.2 改动清单

**identity-api** — 新迁移 `0008_payment_officer_role.py`，结构照抄 `0004_erp_pa_officer_role.py`：

1. `permission_defs` 幂等补齐所引用的键
2. `role_defs` 插入 `payment_officer` / "Payment Officer" / sort 851 / `is_active=true`
3. `role_permissions` 授予：`view_pa`、`view_invoice`、`view_po`
4. `role_permission_locks` 锁定 `view_pa`（无此可见性角色即失能）

全部 `ON CONFLICT DO NOTHING`。`down_revision` 挂当前链尾（**动手前须 `alembic heads` 现场核实**，不照抄 `0007`）。

**epms-api**

| 文件 | 改动 |
|---|---|
| `app/crud/config.py` | `BUILT_IN_ROLES` 加入；`_BUILTIN_ROLE_NAMES` 加入标签（顺带补 `erp_pa_officer` 的既有缺失）；`LOCKED_PERMISSIONS` 加 `{"view_pa"}`；`_ROLE_DEFAULTS` 加默认授权行 |
| `app/crud/current_step.py` | 角色显示名映射加入 |
| ~~`app/crud/pa.py:413`~~ | **原稿错误,已更正**:该函数是**死代码**(epms-api 内无任何调用者,2026-08-13 grep 证实)。真正产生 `process_pa` 任务的是 **approval-api** —— 见下 |

**★ 真正的改动点在 approval-api,不在 epms-api**(原稿指错文件,照原样实施则生产上任务仍全部派给 `ap_clerk`,新角色拿不到任何工作):

| 文件 | 站点 | 路线 |
|---|---|---|
| `approval-api/app/crud/engine.py` | `_post_approve_pa` | PA-PO(EPMS 采购付款) |
| `approval-api/app/crud/engine.py` | `_post_approve_pa_dir` | **PA-DIR(OA Direct PA)** —— 原稿完全没提这条路线 |

两处都硬编码 `assigned_role="ap_clerk"`,都要改。这是「数清消费者」教训的第五次复发:只看了 epms-api,漏掉整个 approval-api 服务树。
| `app/crud/dashboard.py` | 新增 `build_payment_officer`（以 `build_ap_clerk` 为蓝本，仅保留付款视图）+ 路由分支 |

**`app/schemas/user.py` 的 `VALID_ROLES` 不加**（原稿写错，已更正）：该集合校验的是**主登录角色**，而 `payment_officer` 是附加角色。既有的 `erp_pa_officer` 正是如此 —— 在 `BUILT_IN_ROLES`（授权矩阵）里，但不在 `VALID_ROLES` 里。加进去会让它错误地可被设为某人的主角色。

**注意**：`app/api/v1/invoices.py:49` 的 `_AP_ROLES` **不加** `payment_officer` —— 那是发票匹配授权，与付款职责无关；加入会反向扩权，与 SoD 目标冲突。

**finance-api**

| 文件 | 改动 |
|---|---|
| `app/crud/payment_execute.py:36` | `_PAY_ROLES`：移除 `ap_clerk`，加入 `payment_officer` |
| `app/core/deps.py:9` | `_FINANCE_ROLES` 加入 `payment_officer`（否则读不到付款相关端点） |
| `app/core/budget_scope.py:23` | 按需加入（付款视图若需预算作用域） |

`api/v1/payments.py::can_pay` 复用 `_check_can_pay`，无需单独改。

**前端**

- Portal Admin 角色勾选列表加入新角色
- Access Control 矩阵渲染新角色行（若为数据驱动则无需改代码，须实测确认）
- epms 中按 `ap_clerk` 硬编码的付款相关判定改为权限/角色并集驱动

### 4.3 存量任务改派

脚本 `epms-api/scripts/reassign_process_pa_tasks.py`：

```
UPDATE tasks SET assigned_role = 'payment_officer'
WHERE type = 'process_pa' AND is_completed = false AND assigned_role = 'ap_clerk';
```

要求：
- **先只读盘点**待改派数量，记录数字
- 改派后**正面核对**：新角色下的计数 == 盘点数，且原条件查询返回 0 行
- 不依赖「无输出即通过」

### 4.4 用户可见的行为变化（须提前告知）

`ap_clerk` 从 `_PAY_ROLES` 移除后，Payment Batches 页的 `GET /payments/can-pay` 对 AP 返回 `false`，**Create / Execute 按钮消失**。这是预期结果，但若不提前告知会被当作故障上报。

## 5. W2/W3 — 通知与差异可见性

### 5.1 通知补全

**Match Review 改为角色池**：`invoices.py:535` 去掉 `assigned_user_id=reviewer_id`，只保留 `assigned_role="ap_clerk"`。同时解决「只发一个人」与「绕过 AP 共享邮箱」。

**Exception 新增任务**：invoice 落入 `exception` 时创建 `type="resolve_exception"` 任务，`assigned_role="ap_clerk"`，`assigned_user_id` 留空（角色池 + 共享邮箱可命中）。`resolve_exception` 端点成功后关闭该任务。需在 `app/models/task.py` 的类型注释、`notification.py::_infer_template` 映射、以及 `crud/config.py` 的邮件模板默认值中登记新类型。

**commit 顺序**：上述两处 notify 前显式 `await db.commit()`，落点选在该端点全部写操作完成之后，照 `assign_match` 既有先例。

### 5.2 PA Detail 差异面板

新组件 `epms/src/components/invoices/InvoiceMatchVariancePanel.tsx`，挂在 PA Detail 的 Linked Documents 下方，按 PA 路由分叉。

**PO-related PA**（`pa.po_id != null`）

- 折叠标题：`INV-xxxx · Total variance +$1,234.56 (+2.3%)`，颜色按 `invoice_match_tolerance_pct` 分档（0 = 绿 / 容差内 = 黄 / 超容差 = 红）
- 展开视图按匹配模式分叉，判据为 allocation 的 `po_line_id`：
  - **by-line**（`po_line_id != null`）：三列对照 —— Invoice Line（描述/数量/单价/金额）↔ PO Line（由已 fetch 的 `po` 按 `po_line_id` 取数量/单价/金额）↔ 行差异
  - **by-amount / 总值模式**（`po_line_id == null`）：只列 Invoice Line Items 与分摊到各 PO 的金额，**不渲染行对应关系**（该关系不存在，渲染即编造）

数据已由 `InvoiceResponse.allocations[]`（含 `po_line_id`、`po_line_description`、`variance`、`variance_pct`）与 `line_items[]` 提供，**此分支无需后端改动**。

**House Account PA**（`agreement_type == "house_account"`）

后端在 `InvoiceResponse` 新增一个计算字段：

- `claimed_receipts: list[{id, receipt_ref, receipt_date, receipt_type, total_amount, vendor_name}] | None`
  （`total_amount` 可空 —— delivery / service 类凭证本就没有金额）

由 `invoice.receipt_ids` 关联 `agreement_receipts` 求得，随发票读权限（`view_invoice` 矩阵）返回，**不复用** §2.6 中会 403 的 `_require_invoice_match_access` 路由。无迁移（纯计算字段）。

**刻意不提供后端汇总字段 `receipt_total`**：汇总必须遵守下方的「排除无金额凭证」约定，若后端再算一份，就会与前端 `InvoiceReceiptsPanel` 的既有约定形成两个真相来源，日后必然漂移。后端只给明细，汇总统一在前端用同一个 helper 完成。

**比较口径必须复用 `InvoiceReceiptsPanel.tsx:186-200` 的既有约定，不得另立一套**：

1. 比较基准是发票的 **`total_amount`（含税）**，**不是** `amount`（税前）。注意这与 PO 分摊路线的税前口径**正好相反** —— 柜台小票是含税的，两条路线口径不同是正确的，不是笔误。
2. **只有携带金额的凭证参与比较**。delivery / service 两类凭证没有金额（`ag09` 起放开可空），必须排除。
3. 若所选凭证**全都没有金额**（`hasPricedSelection == false`），视为**零差异**（因而不展示）。否则会把「差异 = 整张发票金额」渲染出来，是纯误报。
4. 用既有的 `centsEqual` 做分位比较，**不要**用 `!== 0` —— 浮点噪声会把零差异渲染成差异。该 helper 的注释已明示它是共享约定，应抽出复用而非平行复制。

综上，渲染条件为：`hasPricedSelection && !centsEqual(receiptTotal, invoice.total_amount)`。折叠标题显示差异总额，展开列出已认领凭证明细。

**其他协议类型**（recurring / milestone）不展示。

**注意**：`Decimal` 经 Pydantic 序列化为 JSON 字符串，前端须 `Number()` 转换后再比较/运算。

**顺带修复的两个既有缺陷**（不修则新面板在协议 PA 上无数据）

1. `PaDetailPage.tsx:228` 的 `useInvoices(pa?.po_id && ...)` 在 `po_id` 为 null 时整个跳过 fetch，导致协议 PA 的 Linked Documents 永远显示 "No invoices linked"。改为按 `agreement_id` 取（`DocumentChainTree.tsx:370` 已有先例）。
2. 同处使用默认 `page_size=20` 后再客户端按 `pa.invoice_ids` 过滤，繁忙 PO 上会截断漏显。显式传 `page_size: 200`。

### 5.3 W4 文案改写

`review_match` 任务的标题与描述、以及 review 弹窗文案，改为「确认发票与 PO/GR 的对应关系正确」，并明写「付款金额的批准在 Payment Application 审批链上完成」。

UI 文案一律英文（注释可中文）。

## 6. 测试策略

| 层 | 方式 |
|---|---|
| epms-api | 现场量基线后再改；比对**失败集合**而非总数；测试库用专属库名，串行执行，避免与其他会话撞库 |
| finance-api | 同上；`_PAY_ROLES` 变更须覆盖「ap_clerk 被拒 / payment_officer 通过 / finance_manager 仍通过」三例 |
| identity-api | 迁移 upgrade/downgrade 幂等性；`alembic heads` 无双头 |
| 前端 | `npx tsc -p tsconfig.app.json`（**基线现场量，不照抄记忆中的数字**；worktree 已执行 `npm ci`，否则计数为假阴性） |
| 纯函数 | allocation ↔ PO line ↔ invoice line 三方 join 抽为纯函数配单测 |

`payment_officer` 是真实 `role_defs` 码（非 `gm_or_opm` 那类伪角色），授权外键可正常解析。

## 7. 发布与回滚

**发布顺序硬约束**：identity `0008` 迁移必须在新镜像启动**之前**执行 —— 新代码会查询 `payment_officer` 的授权行。

其余按标准发布流程：改 TAG → build 真建 + retag 其余 → push 全量 `:<sha>` → 服务器 `git pull` → 改 `.env` TAG → `pull` → 有迁移先跑 `migrate-prod.sh` → `up -d`。

前端构建前须 `set -a && . ./.env.prod.example` 载入全部域名变量并**逐个断言非空**；构建后**验证烤入的 URL 集合**与线上镜像一致（该事故已复发四次）。

**回滚**：角色为附加角色，回滚时 Portal Admin 取消勾选即可；`_PAY_ROLES` 改回一行；存量任务改派脚本需要反向脚本（`payment_officer` → `ap_clerk`），一并提供。

## 8. 风险

| 风险 | 缓解 |
|---|---|
| AP 突然失去付款按钮被当故障上报 | 上线前告知；§4.4 已记录 |
| 新角色未在 Portal Admin 勾人 → `process_pa` 任务无人认领 | 上线检查清单纳入「至少一名 payment_officer 已授予」；未授予时任务仍对 `finance_manager` 等可见（角色池 + 后备） |
| 遗漏角色枚举登记点导致某处 500 | §4.2 已逐文件列举；实现后以 grep `ap_clerk` 全量复核，逐个判定是否需要平行加入 |
| `receipt_total` 字段增加发票读取的 N+1 查询 | 单次 `IN` 查询批量取凭证，不逐条查 |
| 与其他会话并行开发冲突 | 独立 worktree + 独立分支；不 `git stash`（仓库级共享，会弹出其他会话 WIP）；随手 commit 不留 WIP |

## 9. 待办与未决

- 存量 `process_pa` 任务数量：**实现阶段只读盘点后填入**
- 角色码 `payment_officer` / 标签 "Payment Officer" 若与公司内部叫法不符，改标签为一行改动
- §2.5 其余 8 处 commit 顺序缺陷：另立条目、另行发布
