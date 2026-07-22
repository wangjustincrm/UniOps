# Task Inbox 按任务类型分组显示 — 设计文档

**日期**: 2026-07-21
**范围**: EPMS / OA / VMS 三个模块的 Task Inbox 页 + Portal 首页统一收件箱
**类型**: 前端展示改造，无后端改动

---

## 1. 背景与目标

当前四处 Task Inbox 都把不同类型的任务**混在一个扁平列表**里显示，"分类"只体现为一个"按类型筛选"的下拉（一次只看一类）。

用户希望：**同屏按任务类型分组显示** —— 每种任务类型自成一个带标题计数的区块，全部类型一屏纵向排开，一眼看清"哪类有几件"。

### 已确认的决策（来自 brainstorming）

| 决策点 | 结论 |
|--------|------|
| 范围 | 全部四个：EPMS / OA / VMS / Portal 统一收件箱 |
| 分组维度 | 按**每个原始 task 类型**（最细粒度），不是语义大类 |
| Portal 特例 | 统一收件箱做**两级**：外层按模块（EPMS/Expense/Visitor），内层按 task 类型 |
| 呈现方式 | **分组区块 + 标题计数**（不可折叠、不做分类子标签页；单页纵向滚动） |
| EPMS 类型下拉 | **去掉**（分组后每类都独立成块，筛选下拉冗余） |
| OA doc-type 下拉 | **保留**（单据种类 Expense/Mileage/Travel/PA 与"任务类型"是正交维度，不冗余） |
| 分组顺序 | 按业务流程**固定顺序**，不按数量排（保证刷新后布局稳定） |
| 空分组 | **隐藏**（只渲染有任务的分组） |

---

## 2. 核心交互模式（四处统一）

在"当前本应显示的列表"之上，插入一层按 task 类型的分桶：

```
▾ Approve Purchase Request · 3
  ┌────────────────────────────┐
  │ PR-0012 · Vendor · $1,200  │   ← 现有卡片，样式不变
  │ PR-0031 · Vendor · $980    │
  │ PR-0044 · Vendor · $4,500  │
  └────────────────────────────┘
▾ Create Purchase Order · 2
  ┌────────────────────────────┐
  │ PO-0034 · …                │
  │ …                          │
  └────────────────────────────┘
▾ Confirm Settlement · 1
  ...
```

规则：
- **分组标题** = 类型显示名 + 计数徽章。视觉上是一个轻量小标题（非卡片），与下方卡片有明显层级区分。
- **卡片本身不变** —— 复用各页现有的卡片组件与样式（urgent 红色、done、金额、跳转等全部保留）。
- **组内排序** 保持各页现有排序（如 urgent 优先 / created_at 倒序）。
- **分组顺序** 由各页一个显式的 `GROUP_ORDER` 数组决定；数组里没列到的类型统一归到末尾（按出现顺序），保证新增 task 类型时不会消失。
- **空分组隐藏**；全部为空时显示各页原有的 empty state。
- 不做折叠、不做分类子标签页。

### 与各页现有 Tab / 筛选的关系

分组发生在"**当前 tab / 筛选选中后的结果集**"**之内**。即：先按现有 tab（如 EPMS 的 All/Urgent/Normal/Completed、OA 的 All/Needs My Action/My Submissions）和保留的筛选过滤，再对结果按类型分组。Tab 和统计条（stats bar）全部保留。

---

## 3. 各页具体改造

### 3.1 EPMS — `epms/src/pages/tasks/TaskInboxPage.tsx`

- **保留**：页头、三张统计卡（Urgent/Normal/Completed）、优先级 Tab 条（All/Urgent/Normal/Completed）。
- **去掉**：类型筛选下拉（`TaskInboxPage.tsx:281-292` 的 `<select> All Types`）及其 `typeFilter` state。
- **改造**：`filtered`（tab 过滤后的结果）不再直接 `.map`，而是先按 `task.type` 分桶，再按 `GROUP_ORDER` 渲染每个分组区块。
- **标签来源**：复用 `epms/src/lib/taskTypes.ts` 的 `TASK_TYPE_LABELS`。
- **分组顺序**（`GROUP_ORDER`，建议）：
  ```
  approve_pr, approve_po, approve_pa,      // 审批优先
  place_order,                             // 下单
  create_pr, create_po, create_pa, create_prepayment_pa,   // 创建
  review_match, match_invoice,             // 发票匹配
  confirm_settlement                       // 结算确认
  ```
  未列出的类型归末尾。
- **卡片**：继续用现有 `FullTaskCard`，不改。

### 3.2 OA — `oa/src/pages/tasks/TaskListPage.tsx`

- **保留**：页头、三张统计卡、Tab 条（All/Needs My Action/My Submissions）、**doc-type 下拉**（正交维度）。
- **改造**：`filtered`（tab + doc-type 过滤后）按 `task.task_type` 分桶后渲染分组区块。
- **标签/图标来源**：复用现有 `TASK_META`（已含每个 task_type 的 label/icon/配色）。分组标题可用 `TASK_META[type].label`。
- **分组顺序**（建议）：
  ```
  approve_expense, approve_pa,             // 审批
  revise_expense, revise_pa,               // 退回修改
  pay_expense, pay_pa,                     // 记录付款
  submitted_expense, submitted_pa          // 我提交的/在审
  ```
- **卡片**：继续用现有 `TaskCard`，不改。

### 3.3 VMS — `vms/src/pages/TaskInboxPage.tsx`

- 当前是完全扁平的 `<ul>` 列表，无 tab/筛选。
- **改造**：按 `document_type` 分桶（这是 VMS 里区分任务种类的字段，映射到三类）：
  | document_type | 分组标题 |
  |---------------|----------|
  | `vms_visit` | Visit Approvals |
  | `vms_train` | Training Confirmations |
  | `vms_ppe` | PPE Confirmations |
- **分组顺序**：`vms_visit, vms_train, vms_ppe`。
- **卡片**：保留现有 `<li>` 行的渲染（图标、标题、role、时间、urgent 徽章、跳转），只是把单一 `<ul>` 拆成"每组一个标题 + 一个 `<ul>`"。
- 实现时确认一下 VMS task 的原始 `type` 字段取值；若 `type` 比 `document_type` 更贴合"任务类型"，则改用 `type` 分组（默认用 `document_type`，因其干净对应三种可执行动作）。

### 3.4 Portal 统一收件箱 — `portal/src/pages/PortalHome.tsx`

Portal 是跨模块聚合，**做两级分组：外层按模块，内层按 task 类型**（模块是比任务类型更高的维度）。

- 当前 `allTasks`（跨模块合并去重后）是扁平列表，每张卡带模块徽章（EPMS/Expense/Visitor）。
- **改造**：`UnifiedTask` 模型新增两个字段 `groupKey: string` 与 `groupLabel: string`，在映射时计算（`module` 字段已有）：
  - EPMS 行 → `groupLabel = TASK_TYPE_LABELS[t.type]`（需把原始 `type` 透传进 `UnifiedTask`，当前被丢弃了）；
  - OA 行（来自 `/expenses/my-actions`，携带 status）→ `groupLabel = STATUS_LABEL[t.status]`（如 Pending Dept. Approval / Pending Finance Review / Approved — Awaiting Payment）；
  - VMS 行 → 按 doc-type 映射（Visit Approvals / Training Confirmations / PPE Confirmations）。
- **两级渲染结构**：

  ```
  ━━━━━ EPMS ━━━━━━━━━━━━━━━━━━━━━
    ▾ Approve Purchase Request · 2
        PR-0012 · … / PR-0031 · …
    ▾ Create Purchase Order · 1
        PO-0034 · …
  ━━━━━ Expense ━━━━━━━━━━━━━━━━━━
    ▾ Pending Finance Review · 3
        EXP-… / MIL-…
  ━━━━━ Visitor ━━━━━━━━━━━━━━━━━━
    ▾ Visit Approvals · 1
        …
  ```

  - **外层**：按 `task.module` 分区，模块区头（比类型小标题更强的视觉层级，复用 `MODULE_STYLE` 的配色/图标 + 该模块任务总数）。模块顺序固定：`EPMS → EXPENSE → VMS`。
  - **内层**：模块区内再按 `groupKey` 分桶，按 `GROUP_ORDER` 排序，渲染"类型小标题 + 计数 + 卡片"。
  - **空模块 / 空类型都隐藏**；全部为空时回落到原 empty state。
- 每张卡仍保留模块徽章（冗余但无害，与模块区头一致）。
- 保留右侧 Platform Health / Recent Activity 小组件不动。
- 顶部"You have N pending tasks"、"My Task Inbox"标题与计数徽章保留。

> 注：EPMS / OA / VMS 三个**模块内部**的独立 Task Inbox 页只做单级"按类型分组"（模块已确定，无需再分模块）。只有 Portal 这个跨模块聚合页做两级。

---

## 4. 实现方式（架构决策）

四个前端是**独立的 Vite 应用**，且**任务数据结构各不相同**（EPMS `ApiTask` / OA `OaTaskItem` / VMS `EpmsTask` / Portal `UnifiedTask`）。

**决定：分组逻辑在各页本地实现**，不抽到 `@uniops/shell` 共享包。

理由：
1. 四种 task 形状差异大，抽共享泛型组件反而要先做归一化，得不偿失；
2. `@uniops/shell` 的 `--install-links` 拷贝陈旧问题（见团队记忆）不值得为约 30 行分组逻辑引入；
3. 各页独立改动，互不耦合，回归半径最小。

每页新增两个本地小单元：
- `groupTasks(items, keyOf, order)` —— 纯函数，按 key 分桶并按给定顺序返回 `{ key, label, items }[]`（空桶不含）；
- 一个 `TaskGroupSection`（或内联的标题片段）—— 展示"标题 + 计数"并渲染该组卡片。

---

## 5. 明确不做（Out of scope）

- 不改任何后端 / API / 引擎；所有分组是纯前端、基于 API 已返回的数据。
- 不改任务的路由跳转、完成（mark done）、去重逻辑。
- 不做分组折叠、不做分类子标签页、不做"记住展开状态"等增强（YAGNI）。
- 不改各页的统计卡、Tab、健康度组件。

---

## 6. 测试要点

- 每页：多类型混合数据 → 正确分成多个区块，顺序符合 `GROUP_ORDER`，计数准确。
- 空分组不渲染；全空时回落到原 empty state。
- EPMS：切换 Urgent/Normal/Completed tab 时，分组基于该 tab 的结果集重算。
- OA：doc-type 下拉与类型分组叠加过滤正确。
- Portal：跨模块任务落入正确分组，卡片模块徽章仍在；去重逻辑不受影响。
- 未在 `GROUP_ORDER` 中登记的新类型：归到末尾而非消失。
