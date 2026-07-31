# EPMS — Create PA 收货闸门 + 未收货催收货 设计

**日期**: 2026-07-30
**范围**: epms-api（后端主逻辑）+ epms 前端（Create PA / New GR / 邮件深链）
**背景问题**: 目前 PO 只要"收到发票并 Match to PO"就能 Create PA，跳过了收货（GR/SR）环节，违反最初"三单匹配（PO + 收货 + 发票）后才付款"的约定。

---

## 1. 现状诊断（根因）

Create PA 的实际触发/校验链路：

1. **触发**: 发票 `Matched to PO` 后，[`invoices.py:_notify_requester_create_pa`](../../../epms-api/app/api/v1/invoices.py) 直接给 requester 建 `create_pa` 任务并发邮件 —— **不查任何 GR**，只要 `invoice.po_id` 存在就发。
2. **建 PA**: [`pa.py:create()`](../../../epms-api/app/crud/pa.py) 接受 `gr_ids`，但**既不要求非空、也不校验收货数量**。API 端 [`api/v1/pa.py:create_pa`](../../../epms-api/app/api/v1/pa.py) 有预付/结算 guard，但**没有收货 guard**。
3. **死代码**: [`gr.py:_create_pa_task`](../../../epms-api/app/crud/gr.py)（收货完成后才建 PA 任务）定义了却**从未被调用** —— 本该"收货驱动"的路径没接上，实际全靠"发票匹配驱动"。

**结论**: 收货这道闸门在后端完全缺失。

---

## 2. 目标行为

1. **非预付类型的 PA 必须有 3-way matched 发票才能创建**（`matched` 且挂 GR；硬拦，后端 422），预付款豁免；授权角色可显式 override。
2. **发票已匹配到 PO 但还没 GR 时**：不再"静默不催"，而是**主动催收货** —— 给应收货方（物理→仓库、服务→Requester）发提醒任务/邮件，每日重发直到出现 3-way matched 发票。
3. **GR 一创建**（发票达成 3-way）自动把催办过渡为 `create_pa` 提示。

---

## 3. 决策记录（已与用户确认）

| # | 决策 | 选择 |
|---|------|------|
| D1 | 闸门口径 | **Invoice 3-way matched**（`invoice.status=="matched"` 且 `gr_id` 非空，即已挂 GR）。GR **创建**即生效，不要求收货确认 |
| D2 | 拦截强度 | **硬拦 + 显式 override**（授权角色 + 必填理由，落 PA 审计） |
| D3 | override 授权 | **Access Control 矩阵新增权限 `pa_override_receipt`**（默认 finance/procurement，requester 无） |
| D4 | pa_type 范围 | `regular / settlement / balance` 纳入闸门；**`prepayment` 豁免** |
| D5 | 任务时机 | create_pa 任务与闸门对齐（有收货才发；重连 GR 死代码补发） |
| D6 | 未收货催办 | 物理→`warehouse_staff` 角色池；服务/项目→Requester（PR 创建人） |
| D7 | 催办节奏 | **每日重发**，停止条件 = PO 出现 3-way matched 发票（`matched` 且 `gr_id` 非空）—— 与 D1 同一条件 |
| D8 | 催办深链 | 点邮件/任务 → New GR 页，`?poId=` 预填 PO 默认值 |

---

## 4. 3-way matched 信号（单一真相源，贯穿触发/闸门/催办）

**一张发票 3-way matched ⇔ `invoice.status == "matched"` 且 `invoice.gr_id IS NOT NULL`**（已挂 GR）。

- `status == "matched"`：发票 vs PO 变价在容差内（[`invoice.py:match()`](../../../epms-api/app/crud/invoice.py) 已产出该状态；`exception`/`match_review` 不算，需人工处理）。
- `gr_id 非空`：发票已关联 GR。`match()` 传入 `gr_ids` 时会写 `gr_id`；[`_autofill_gr_to_matched_invoices`](../../../epms-api/app/crud/gr.py) 在 **GR 创建**时把 GR 反挂到已匹配发票并写 `gr_id`。→ **GR 一创建即达成 3-way，不要求 ack/收货确认**（D1）。
- 用标量 `gr_id`（而非 `gr_ids` 数组）判空，SQL 简单、跨列类型稳。

新增 helper（epms-api，放 `app/crud/po.py`，供 pa/invoices/gr/task 复用）：
```python
async def po_has_three_way_matched_invoice(db, po_id: uuid.UUID) -> bool:
    """True 当 PO 有任一张 3-way matched 发票（matched 且已挂 GR）。"""
    return (await db.execute(
        select(Invoice.id).where(
            Invoice.po_id == po_id,
            Invoice.status == "matched",
            Invoice.gr_id.is_not(None),
        ).limit(1)
    )).scalar_one_or_none() is not None
```

> **说明**: 收货真正"确认"（collected/confirmed、`received_qty` 入账）仍是 GR 自身流程的一环，但**不再是** PA 闸门的判据 —— 按 D1，挂上 GR（3-way）即放行。

---

## 5. 后端闸门（唯一权威）

位置: [`api/v1/pa.py:create_pa`](../../../epms-api/app/api/v1/pa.py)，在现有 prepayment/settlement guard **之后**、`pa_crud.create()` **之前**插入：

```python
# 收货闸门 —— 预付款先付后收,豁免;其余类型必须有 3-way matched 发票
if body.pa_type != "prepayment":
    if not await po_crud.po_has_three_way_matched_invoice(db, body.po_id):
        if not body.receipt_override:
            raise HTTPException(
                status_code=422,
                detail="No 3-way matched invoice for this PO (a matched invoice "
                       "with a linked goods receipt). Create a goods receipt "
                       "first, or override with a reason.",
            )
        # override 需授权
        if not scope["perms"].get("pa_override_receipt", False):
            raise HTTPException(
                status_code=403,
                detail="You are not authorized to create a payment without goods receipt.",
            )
        if not (body.receipt_override_reason or "").strip():
            raise HTTPException(
                status_code=422,
                detail="A reason is required to override the goods-receipt requirement.",
            )
```

- 授权判定复用 [`build_scope`](../../../epms-api/app/core/access_scope.py) 的 `perms`（现 `create_pa` 端点尚未取 scope，需补 `scope = await build_scope(db, user)`）。
- override 字段透传给 `pa_crud.create()` 落库。

**为何在 API 层而非 crud**: 需要当前用户角色/权限做 override 授权判定；与现有 prepayment guard 同层，风格一致。

---

## 6. 数据模型（迁移）

`payment_applications` 加列（epms-api 新 alembic，`down_revision` 挂**真实 heads**，先 `alembic heads` 核实）：

| 列 | 类型 | 说明 |
|----|------|------|
| `receipt_override` | `BOOLEAN NOT NULL DEFAULT false` | 是否无收货强建 |
| `receipt_override_reason` | `TEXT NULL` | override 理由（审计） |
| `receipt_override_by` | `UUID NULL` | override 操作人（= created_by，冗余便于审计） |

Schema [`schemas/pa.py:PaCreate`](../../../epms-api/app/schemas/pa.py) 加：
```python
receipt_override: bool = False
receipt_override_reason: str | None = None
```
`PaResponse` 增补三个只读字段（PA Detail 展示"⚠ 无收货 override，理由: …"）。

`pa_crud.create()` 写入 `receipt_override / receipt_override_reason / receipt_override_by=created_by`。

---

## 7. Access Control 矩阵新增权限

新增 `pa_override_receipt`（EPMS 权限键）：

- 默认赋予（config.py `_ROLE_PERMISSIONS` / `_P(...)`）：`finance_bp`, `finance_manager`, `cfo`, `procurement_officer`, `procurement_manager`, `system_admin`。
- `requester` 等不赋予。
- 前端 Access Control 矩阵 UI 自动出现该开关（沿用矩阵渲染，无需单独改）。
- **注意**: 生产上矩阵可能已被 company_config 覆盖，部署后需在后台确认该权限对上述角色为 true（参照角色池共享邮箱上线检查经验）。

---

## 8. 任务/通知时机（重构 + 重连死代码）

### 8.1 发票匹配后分流（[`invoices.py:_notify_requester_create_pa`](../../../epms-api/app/api/v1/invoices.py)）

改为一个分派器 `_on_invoice_matched(db, invoice)`（发票 `match` 成功后调用）：

- PO 无 `pr` 或无效 → return（同现状）。
- **本发票已 3-way matched**（`status=="matched"` 且 `gr_id` 非空，即 GR 已在发票之前建好）→ 建/re-notify `create_pa` 任务给 Requester（同现状逻辑）。
- **本发票 matched 但 `gr_id` 为空**（GR 未建）→ 建/re-notify `confirm_receipt` 提醒任务：
  - `is_physical(po.type)`（type ∉ {4,6}）→ `assigned_role="warehouse_staff"`, `assigned_user_id=None`（角色池 → 共享邮箱）。
  - 服务/项目（type ∈ {4,6}）→ `assigned_role="requester"`, `assigned_user_id=PR.created_by`。
  - `document_type="po"`, `document_id=po.id`, `action_url=/gr/new?poId=<po.id>`。
  - 文案: "Invoice {internal_ref} has been matched to PO {po.number} but the goods/service has not been received. Please confirm receipt and create a GR."
  - 去重: 每 PO 一条未完成 `confirm_receipt`；已存在则用新发票号 re-notify（复用 create_pa 的 re-notify 模式）。

### 8.2 GR 创建 → 达成 3-way → 交接（重连 [`gr.py:_create_pa_task`](../../../epms-api/app/crud/gr.py)）

钩子放在 **GR 创建**处：`gr.create()` 里 [`_autofill_gr_to_matched_invoices`](../../../epms-api/app/crud/gr.py) **之后**（此刻已匹配发票的 `gr_id` 刚被写上 = 达成 3-way），调用新 `_on_three_way_reached(db, po_id)`：

1. 关闭该 PO 所有未完成 `confirm_receipt` 提醒任务。
2. **若** PO 现有 3-way matched 发票（`po_has_three_way_matched_invoice`）**且** 无 PA **且** 无未完成 `create_pa` 任务 → 建 `create_pa` 任务给 Requester（复用 `_create_pa_task`，锚在 PO 上）。

> 时序覆盖：
> - **先发票后 GR**：发票 match 时 `gr_id` 空 → 8.1 发 `confirm_receipt`；随后 GR 创建 → 8.2 补发 `create_pa` 并停催办。
> - **先 GR 后发票**：发票 match 时 `_autofill` 早已让其它发票挂 GR；本发票 match 若带 `gr_ids` 则直接 3-way → 8.1 直接发 `create_pa`。
>
> 因 3-way 在 **GR 创建**即达成，交接不依赖 collect/confirm；收货确认与否只影响 GR 自身流程，不影响 PA 触发。

### 8.3 每日重发 —— 复用现有 `daily_followup_loop`（零新机制）

epms-api **已有**每日定时循环 [`app/tasks/daily_followup.py:daily_followup_loop`](../../../epms-api/app/tasks/daily_followup.py)（每天 08:00 UTC，main.py lifespan 启动）：扫**所有未完成 Task**，逐条 `dispatch_task_notification(task, db, is_followup=True)` 发跟进邮件。这正是 admin panel 里 GR/Service SLA 时间设定所依赖的同一套机制（注：那些 `manager_escalation_days`/`gm_opm_escalation_days` **分级升级目前尚无消费方**，daily loop 只对任务持有人重发提醒、不做按天分级升级 —— 与本需求无关，不扩展）。

因此 `confirm_receipt` 的"每日重发"**无需任何新代码/新字段/新 scheduler**：

- **重发**：`confirm_receipt` 作为未完成任务被 daily loop 自动每天重发；`is_followup=True` 统一走 `daily_pending_reminder` 通用模板（含任务标题 + `action_url`）。→ **Task Inbox 不新增任务，仅同一条任务每天发一封邮件**（回应用户关切）。
- **首发**：§8.1 建任务时的即时通知走专属模板 `confirm_receipt`（见 §8.5），带 `/gr/new?poId=` 深链。
- **停发（D7 = D1）**：§8.2 在 GR 创建（达成 3-way）时把该 PO 的 `confirm_receipt` 置 `is_completed=True` → daily loop 的 `is_completed==False` 过滤自动不再发。无需额外停发逻辑。

**待落实（plan 阶段）—— 深链路由**：`daily_pending_reminder` 模板用 `{link}` 变量，由 `dispatch_task_notification` 按任务推算。需确认其 link 推算逻辑，让 `confirm_receipt` 任务的 `{link}` 指向 `/gr/new?poId=<po_id>`（而非默认 PO 详情页），使**首封与每日重发**都能点进 New GR 页。首封 `confirm_receipt` 专属模板可直接内联该深链。

### 8.4 backfill 收紧（[`task.py:_backfill_create_pa_tasks`](../../../epms-api/app/crud/task.py)）

候选发票条件从 `status=="matched"` 收紧为 **`status=="matched"` 且 `gr_id` 非空**（3-way matched），避免回填给"仅 2-way 匹配、还没 GR"的 PO 造出 create_pa 提示。相应地，仅 2-way 的历史 PO 应改由 §8.3 的 `confirm_receipt` 催办覆盖（可选：backfill 也为这些 PO 补 `confirm_receipt`，留待 plan 定）。

### 8.5 邮件模板

- 新增模板键 `confirm_receipt`（[`crud/config.py`](../../../epms-api/app/crud/config.py) email templates）。
- 类型映射 [`services/notification.py`](../../../epms-api/app/services/notification.py)（`create_pa → create_pa_reminder` 附近）加 `confirm_receipt → confirm_receipt`。
- 模板变量: `po_number`, `invoice_number`, `vendor`, `action_url`。

---

## 9. 前端

### 9.1 Create PA 页
- 非预付 + PO 无 3-way matched 发票：显示阻断提示（"No goods receipt linked to a matched invoice yet."）。
- 无 `pa_override_receipt` 权限者：禁用 Submit。
- 有权限者：出现 `Override — proceed without goods receipt` 勾选 + 必填理由 textarea；勾上才放开 Submit，payload 带 `receipt_override` + `receipt_override_reason`。
- 3-way 信号来源：Create PA 页本就按 PO 拉取其发票/GR 关联来预填行项，可据此判断"是否存在 matched 且挂 GR 的发票"，无需额外请求。

### 9.2 New GR 深链
- [`GrCreatePage.tsx`](../../../epms/src/pages/gr/GrCreatePage.tsx) **已支持 `?poId=`**（预选 PO、自动带行项/存储位置）→ 仅需 `confirm_receipt` 任务/邮件 `action_url` 指向 `/gr/new?poId=<po_id>`（核实实际路由前缀）。零页面改动或极小改动。

### 9.3 PA Detail
- 展示 override 标记与理由（若 `receipt_override`）。

---

## 10. 边界与非目标

- **纯运费 / reference-only 发票**（照付、通常不建 PA）：若确需建且无 GR → 走 override。
- **settlement / balance**: 纳入闸门（结算时货应已到）；例外走 override。
- **触发/闸门/催办停止同口径**: 三者统一用"发票 3-way matched（`matched` 且 `gr_id` 非空）"，无时点差（GR 创建即达成）。
- **GR 建了但从未确认收货**: 按 D1 仍算 3-way，可建 PA。**业务依据**：公司规模小，GR 建单实际上等同于确认收货，两者差异可忽略，故以"GR 创建"为触发点即可，不再额外卡"确认收货"。收货确认仍是 GR 自身流程的完整性环节，但不作 PA 闸门判据（如需"必须实收才付"是更强口径，非本次范围）。
- **`exception` / `match_review` 发票**: 不算 3-way（未落到 `matched`），不触发、不放行；需人工处理成 `matched` 后再走。
- **非目标**: 不改三单匹配容差/金额校验；不改预付款 cap 逻辑；不引入新收货类型；不强制"实收确认"才付款。

---

## 11. 测试计划

**后端（epms-api，本地 docker 测试库 `epms_test`，串行跑）**:
- `regular` PA + PO 无 3-way 发票（matched 但 `gr_id` 空 / 或未 matched）→ 422。
- `regular` PA + PO 有 3-way matched 发票（matched 且挂 GR）→ 201。
- `prepayment` PA + PO 无 3-way → 201（豁免）。
- `settlement`/`balance` + 无 3-way → 422。
- 授权角色 override + 有理由 → 201，`receipt_override*` 落库。
- override 缺理由 → 422；无权限角色 override → 403。
- 发票 match 但无 GR → 建 `confirm_receipt`（物理→warehouse_staff / 服务→requester），**不建** `create_pa`。
- 发票 match 且已挂 GR（先建 GR）→ 建 `create_pa`。
- GR 创建使已匹配发票达成 3-way → `_autofill` 写 `gr_id` + `confirm_receipt` 关闭 + 建 `create_pa`。
- 每日重发: `notified_at` 超 24h 且 PO 尚无 3-way 发票 → 重发；出现 3-way → 停发并完成任务。

**前端**: tsc 门禁对齐 epms 基线（`tsc -p tsconfig.app.json --noEmit`，基线 59）。

---

## 12. 迁移/发布注意

- **有迁移** → app server 部署必跑 `migrate-prod.sh`。
- 新 alembic `down_revision` 挂真实链尾（先 `alembic heads`；勿双 head）。
- 遵循多会话纪律: 单会话单分支单 worktree，不碰 main；发布单点汇合。
- 部署后后台确认 `pa_override_receipt` 权限对 finance/procurement 为 true。
