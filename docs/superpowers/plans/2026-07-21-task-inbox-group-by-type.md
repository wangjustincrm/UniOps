# Task Inbox 按任务类型分组显示 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 EPMS / OA / VMS / Portal 四处 Task Inbox 从"扁平混合列表"改为"按任务类型分组显示"（Portal 为两级：模块 → 类型）。

**Architecture:** 纯前端展示改造，无后端改动。每个 app 新增一个通用纯函数 `groupTasks()`（本地实现，不抽共享包），各页在现有过滤结果之上按类型分桶、按固定顺序渲染"标题+计数"区块，复用现有卡片组件。

**Tech Stack:** React + TypeScript + Vite + Tailwind（四个独立前端 app：`epms/` `oa/` `vms/` `portal/`）。

**Spec:** `docs/superpowers/specs/2026-07-21-task-inbox-group-by-type-design.md`

---

## 验证策略（重要 — 读一次）

- **无前端测试框架**：四个 app 均无 vitest/jest，且无既有组件测试。**不引入测试框架**（YAGNI，非本次范围）。
- **每个任务的验证 = 类型检查 + 目视 QA**：
  - **类型检查**（自动）：在对应 app 目录跑
    `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
    注意 **epms 前端有约 69 个存量 TS 错误、其它 app 各有存量**，所以判据不是"零错误"，而是**"我们改动的文件不新增错误"**。用 grep 过滤到本任务文件确认无输出。
  - **目视 QA**（手动）：在 worktree 里直接跑该 app 的 Vite dev server（`npm run dev`）连到 dev 后端，或在最后统一 QA 任务里集中验证。dev docker 栈挂的是主 checkout、看不到 worktree，故用 `npm run dev` 本地起。
- `groupTasks()` 保持为**纯函数**（便于将来加测试），但本次不写测试。

---

## 文件结构

| App | 新建 | 修改 |
|-----|------|------|
| epms | `epms/src/lib/groupTasks.ts` | `epms/src/pages/tasks/TaskInboxPage.tsx` |
| oa | `oa/src/lib/groupTasks.ts` | `oa/src/pages/tasks/TaskListPage.tsx` |
| vms | `vms/src/lib/groupTasks.ts` | `vms/src/pages/TaskInboxPage.tsx` |
| portal | `portal/src/lib/groupTasks.ts` | `portal/src/pages/PortalHome.tsx` |

`groupTasks.ts` 四份内容**完全相同**（每个 app 独立一份，避免跨 app 依赖 / `@uniops/shell` install-links 陈旧坑）。

---

## Task 0: 建分支 / worktree（多会话纪律）

**Files:** 无代码改动。

- [ ] **Step 1: 确认当前状态**

Run:
```bash
cd /c/Project/uniops && git branch --show-current
```
Expected: `main`（本仓库当前在 main，工作区有未跟踪的 spec 文件等）

- [ ] **Step 2: 建特性分支（在独立 worktree）**

Run:
```bash
cd /c/Project/uniops && git worktree add -b feature/task-inbox-group-by-type ../uniops-task-inbox main
```
Expected: 新建 worktree 目录 `../uniops-task-inbox`，分支 `feature/task-inbox-group-by-type` 基于 `main`。
后续所有改动在该 worktree 目录内进行。

- [ ] **Step 3: 把已写好的 spec 带过去**

spec 已在主 checkout 的工作区（未提交）。在 worktree 里从主 checkout 拷入并提交，或直接在 worktree 内重建。最简单：
```bash
cp "/c/Project/uniops/docs/superpowers/specs/2026-07-21-task-inbox-group-by-type-design.md" "../uniops-task-inbox/docs/superpowers/specs/"
cp "/c/Project/uniops/docs/superpowers/plans/2026-07-21-task-inbox-group-by-type.md" "../uniops-task-inbox/docs/superpowers/plans/"
cd ../uniops-task-inbox && git add docs/superpowers/specs/2026-07-21-task-inbox-group-by-type-design.md docs/superpowers/plans/2026-07-21-task-inbox-group-by-type.md && git commit -m "docs: task inbox group-by-type spec + plan"
```
Expected: 提交成功。之后 `cd ../uniops-task-inbox` 作为工作目录。

---

## Task 1: EPMS — 分组渲染 + 去掉类型下拉

**Files:**
- Create: `epms/src/lib/groupTasks.ts`
- Modify: `epms/src/pages/tasks/TaskInboxPage.tsx`

- [ ] **Step 1: 新建通用分组纯函数**

Create `epms/src/lib/groupTasks.ts`:
```ts
// Generic task bucketer. Groups items by a key, emits groups in a fixed order.
// Keys not listed in `order` are appended after the ordered ones in first-seen
// order. Empty buckets are omitted. Pure — no React, no side effects.

export interface TaskGroup<T> {
  key: string
  label: string
  items: T[]
}

export function groupTasks<T>(
  items: T[],
  keyOf: (item: T) => string,
  labelOf: (key: string, sample: T) => string,
  order: readonly string[],
): TaskGroup<T>[] {
  const buckets = new Map<string, T[]>()
  for (const item of items) {
    const k = keyOf(item)
    const arr = buckets.get(k)
    if (arr) arr.push(item)
    else buckets.set(k, [item])
  }
  const ordered = order.filter((k) => buckets.has(k))
  const rest = [...buckets.keys()].filter((k) => !order.includes(k))
  return [...ordered, ...rest].map((key) => {
    const bucket = buckets.get(key)!
    return { key, label: labelOf(key, bucket[0]), items: bucket }
  })
}
```

- [ ] **Step 2: 在 TaskInboxPage 引入分组、去掉类型下拉**

In `epms/src/pages/tasks/TaskInboxPage.tsx`:

(a) 顶部 import 增加 groupTasks，保留现有 taskTypes import：
```tsx
import { TASK_TYPE_LABELS, taskHref } from '@/lib/taskTypes'
import { groupTasks } from '@/lib/groupTasks'
```
（删除现在从 `taskTypes` 里 import 的 `ALL_TASK_TYPES`——它只被类型下拉用，下一步会删下拉。）

(b) 在 `TABS` 常量下方新增分组顺序常量：
```tsx
// Fixed display order for task-type groups (approvals → order → creates →
// match → settlement). Types not listed fall to the end.
const GROUP_ORDER: TaskType[] = [
  'approve_pr', 'approve_po', 'approve_pa',
  'place_order',
  'create_pr', 'create_po', 'create_pa', 'create_prepayment_pa',
  'review_match', 'match_invoice',
  'confirm_settlement',
]
```

(c) 删除类型筛选 state（原第 171 行）：
```tsx
const [typeFilter, setTypeFilter] = useState<TaskType | 'all'>('all')
```
删掉这一行。

(d) 删除按类型过滤的 `filtered`（原 199-200 行）：
```tsx
const filtered =
  typeFilter === 'all' ? tabFiltered : tabFiltered.filter((t) => t.type === typeFilter)
```
删掉；后续直接对 `tabFiltered` 分组。

(e) 在 `handleComplete` 定义前，基于 `tabFiltered` 计算分组：
```tsx
const groups = groupTasks(
  tabFiltered,
  (t) => t.type,
  (k) => TASK_TYPE_LABELS[k as TaskType] ?? k,
  GROUP_ORDER,
)
```

(f) 删除筛选栏里的类型下拉（原 280-292 行的 `{/* Type filter */}` 到 `</select>` 整块）。删除后，包住 tab 条的那个 `<div className="mb-4 flex items-center justify-between ...">` 里只剩 tab 条——把 `justify-between` 保留即可（单个子元素靠左）。

(g) 替换任务列表渲染块（原 295-308 行 `{/* ── Task list ── */}` 那段）为分组渲染：
```tsx
{/* ── Task list (grouped by type) ── */}
{tabFiltered.length === 0 ? (
  <EmptyState tab={activeTab} />
) : (
  <div className="flex flex-col gap-6">
    {groups.map((group) => (
      <section key={group.key}>
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-sm font-semibold text-neutral-700">{group.label}</h3>
          <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-neutral-100 px-1.5 text-[11px] font-semibold text-neutral-500">
            {group.items.length}
          </span>
        </div>
        <div className="flex flex-col gap-3">
          {group.items.map((task) => (
            <FullTaskCard
              key={task.id}
              task={task}
              onComplete={() => handleComplete(task.id)}
            />
          ))}
        </div>
      </section>
    ))}
  </div>
)}
```

- [ ] **Step 3: 类型检查**

Run:
```bash
cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -E 'TaskInboxPage|groupTasks'
```
Expected: **无输出**（我们改动的两个文件不产生 TS 错误；存量其它错误不在这两个文件里）。

- [ ] **Step 4: 目视 QA**

Run `npm run dev`（epms 目录），登录一个有多种待办的采购员账号，打开 Task Inbox。
Expected:
- 顶部筛选栏只剩 All/Urgent/Normal/Completed，**没有** "All Types" 下拉。
- 列表按类型分块，每块有"类型名 + 计数"小标题；顺序符合 GROUP_ORDER；空类型不出现。
- 切 Urgent/Normal/Completed tab，分组基于该 tab 结果重算。
- 卡片 Mark Done / 跳转仍正常。

- [ ] **Step 5: Commit**

```bash
git add epms/src/lib/groupTasks.ts epms/src/pages/tasks/TaskInboxPage.tsx
git commit -m "feat(epms): group task inbox by task type, drop redundant type filter"
```

---

## Task 2: OA — 分组渲染（保留 doc-type 下拉）

**Files:**
- Create: `oa/src/lib/groupTasks.ts`
- Modify: `oa/src/pages/tasks/TaskListPage.tsx`

- [ ] **Step 1: 新建通用分组纯函数**

Create `oa/src/lib/groupTasks.ts` — 内容与 Task 1 Step 1 **完全相同**：
```ts
export interface TaskGroup<T> {
  key: string
  label: string
  items: T[]
}

export function groupTasks<T>(
  items: T[],
  keyOf: (item: T) => string,
  labelOf: (key: string, sample: T) => string,
  order: readonly string[],
): TaskGroup<T>[] {
  const buckets = new Map<string, T[]>()
  for (const item of items) {
    const k = keyOf(item)
    const arr = buckets.get(k)
    if (arr) arr.push(item)
    else buckets.set(k, [item])
  }
  const ordered = order.filter((k) => buckets.has(k))
  const rest = [...buckets.keys()].filter((k) => !order.includes(k))
  return [...ordered, ...rest].map((key) => {
    const bucket = buckets.get(key)!
    return { key, label: labelOf(key, bucket[0]), items: bucket }
  })
}
```

- [ ] **Step 2: 在 TaskListPage 引入分组**

In `oa/src/pages/tasks/TaskListPage.tsx`:

(a) 顶部 import 增加：
```tsx
import { groupTasks } from '@/lib/groupTasks'
```

(b) 在 `ACTION_TYPES` 常量下方新增分组顺序（键 = OA task_type，标签复用 `TASK_META`）：
```tsx
// Fixed display order for task-type groups. Types not listed fall to the end.
const GROUP_ORDER = [
  'approve_expense', 'approve_pa',
  'revise_expense', 'revise_pa',
  'pay_expense', 'pay_pa',
  'submitted_expense', 'submitted_pa',
]
```

(c) 在现有 `filtered`（tab + docType 过滤后）之后计算分组：
```tsx
const groups = groupTasks(
  filtered,
  (t) => t.task_type,
  (k) => TASK_META[k]?.label ?? k,
  GROUP_ORDER,
)
```

(d) 替换 List 渲染块（原 353-368 行 `{/* List */}` 那段的非空分支）。整段替换为：
```tsx
{/* List (grouped by task type) */}
{isLoading ? (
  <div className="space-y-3">
    {[1, 2, 3].map(i => (
      <div key={i} className="h-24 animate-pulse rounded-xl bg-neutral-100" />
    ))}
  </div>
) : filtered.length === 0 ? (
  <EmptyState tab={tab} />
) : (
  <div className="flex flex-col gap-6">
    {groups.map(group => (
      <section key={group.key}>
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-sm font-semibold text-neutral-700">{group.label}</h3>
          <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-neutral-100 px-1.5 text-[11px] font-semibold text-neutral-500">
            {group.items.length}
          </span>
        </div>
        <div className="flex flex-col gap-3">
          {group.items.map(task => (
            <TaskCard key={task.id} task={task} />
          ))}
        </div>
      </section>
    ))}
  </div>
)}
```

- [ ] **Step 3: 类型检查**

Run:
```bash
cd oa && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -E 'TaskListPage|groupTasks'
```
Expected: **无输出**。

- [ ] **Step 4: 目视 QA**

`npm run dev`（oa 目录），打开 Task Inbox。
Expected:
- doc-type 下拉仍在、仍能过滤。
- 列表按 task_type 分块（Approve / Revise / Pay / In Review 等），每块带计数；顺序符合 GROUP_ORDER。
- All/Needs My Action/My Submissions tab 与 doc-type 下拉叠加过滤后再分组，结果正确。

- [ ] **Step 5: Commit**

```bash
git add oa/src/lib/groupTasks.ts oa/src/pages/tasks/TaskListPage.tsx
git commit -m "feat(oa): group task inbox by task type"
```

---

## Task 3: VMS — 扁平列表改为按 doc 类型分组

**Files:**
- Create: `vms/src/lib/groupTasks.ts`
- Modify: `vms/src/pages/TaskInboxPage.tsx`

- [ ] **Step 1: 新建通用分组纯函数**

Create `vms/src/lib/groupTasks.ts` — 内容与 Task 1 Step 1 **完全相同**（同上整段拷贝）。

- [ ] **Step 2: 在 VMS TaskInboxPage 引入分组**

In `vms/src/pages/TaskInboxPage.tsx`:

(a) 顶部 import 增加：
```tsx
import { groupTasks } from '@/lib/groupTasks'
```

(b) 在 `taskIcon` 函数下方新增顺序 + 标签映射：
```tsx
// Fixed display order + labels for VMS task groups (keyed by document_type).
const VMS_GROUP_ORDER = ['vms_visit', 'vms_train', 'vms_ppe']
const VMS_GROUP_LABELS: Record<string, string> = {
  vms_visit: 'Visit Approvals',
  vms_train: 'Training Confirmations',
  vms_ppe:   'PPE Confirmations',
}
```

(c) 在 `const tasks = data ?? []` 之后计算分组：
```tsx
const groups = groupTasks(
  tasks,
  (t) => t.document_type,
  (k) => VMS_GROUP_LABELS[k] ?? k,
  VMS_GROUP_ORDER,
)
```

(d) 替换非空列表渲染（原 56-89 行 `{!isLoading && tasks.length > 0 && ( ... )}` 整块）为按组渲染。注意原来单个大 `<ul>` 拆成"每组一个标题 + 一个 `<ul>`"，外层容器 `<div className="mt-6 overflow-hidden rounded-lg border ...">` 改为不带边框的分组容器，边框下沉到每组的 `<ul>`：

先把 loading / empty 两个分支保留在原来的外层容器里（原 44-54 行不动），仅替换"非空"分支。为避免边框结构错乱，将整个 `<div className="mt-6 overflow-hidden rounded-lg border border-neutral-200 bg-white">...</div>` 替换为下面结构：
```tsx
<div className="mt-6">
  {isLoading && (
    <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
      <p className="px-4 py-6 text-center text-sm text-neutral-400">Loading…</p>
    </div>
  )}

  {!isLoading && tasks.length === 0 && (
    <div className="overflow-hidden rounded-lg border border-neutral-200 bg-white">
      <div className="flex flex-col items-center px-4 py-16 text-center text-sm text-neutral-400">
        <Inbox className="h-8 w-8" />
        <p className="mt-2">All caught up — no pending visit approvals.</p>
      </div>
    </div>
  )}

  {!isLoading && tasks.length > 0 && (
    <div className="flex flex-col gap-6">
      {groups.map((group) => (
        <section key={group.key}>
          <div className="mb-2 flex items-center gap-2">
            <h3 className="text-sm font-semibold text-neutral-700">{group.label}</h3>
            <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-neutral-100 px-1.5 text-[11px] font-semibold text-neutral-500">
              {group.items.length}
            </span>
          </div>
          <ul className="divide-y divide-neutral-100 overflow-hidden rounded-lg border border-neutral-200 bg-white">
            {group.items.map((t) => (
              <li key={t.id}>
                <Link
                  to={taskHref(t.document_type, t.document_id)}
                  className="flex items-center justify-between gap-4 px-4 py-3 transition-colors hover:bg-primary-50/30"
                >
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="mt-0.5 shrink-0">{taskIcon(t.document_type)}</span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-neutral-900">
                        {t.title || `Visit ${t.document_number}`}
                      </p>
                      <p className="mt-0.5 flex items-center gap-3 text-xs text-neutral-500">
                        <span>{t.assigned_role.replace(/_/g, ' ')}</span>
                        <span className="inline-flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {timeAgo(t.created_at)}
                        </span>
                        {t.priority === 'urgent' && (
                          <span className="rounded-full bg-danger-50 px-1.5 py-0.5 text-[10px] font-semibold text-danger-600">
                            URGENT
                          </span>
                        )}
                      </p>
                    </div>
                  </div>
                  <ArrowRight className="h-4 w-4 shrink-0 text-neutral-300" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )}
</div>
```
（`timeAgo` import 已在文件顶部；`taskHref`/`taskIcon` 为文件内既有函数，保持不变。）

- [ ] **Step 3: 类型检查**

Run:
```bash
cd vms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -E 'TaskInboxPage|groupTasks'
```
Expected: **无输出**。

- [ ] **Step 4: 目视 QA**

`npm run dev`（vms 目录），用一个同时有 visit/train/ppe 待办的账号打开 Task Inbox。
Expected:
- 出现最多三块：Visit Approvals / Training Confirmations / PPE Confirmations，顺序固定，空块不出现。
- 每块带计数；行点击跳转与原来一致。
- 全空时仍显示 "All caught up"。

- [ ] **Step 5: Commit**

```bash
git add vms/src/lib/groupTasks.ts vms/src/pages/TaskInboxPage.tsx
git commit -m "feat(vms): group task inbox by document type"
```

---

## Task 4: Portal — 统一收件箱两级分组（模块 → 类型）

**Files:**
- Create: `portal/src/lib/groupTasks.ts`
- Modify: `portal/src/pages/PortalHome.tsx`

- [ ] **Step 1: 新建通用分组纯函数**

Create `portal/src/lib/groupTasks.ts` — 内容与 Task 1 Step 1 **完全相同**（同上整段拷贝）。

- [ ] **Step 2: UnifiedTask 增加分组字段**

In `portal/src/pages/PortalHome.tsx`, `UnifiedTask` 接口（原 48-63 行）增加两个字段：
```tsx
interface UnifiedTask {
  id: string
  module: 'EPMS' | 'EXPENSE' | 'VMS'
  title: string
  docNumber: string
  dedupKey: string
  amount: number | null
  currency: string
  urgent: boolean
  href: string
  createdAt: string
  /** Task-type bucket key within a module (raw type / status / doc_type). */
  groupKey: string
  /** Human label for the group header. */
  groupLabel: string
}
```

- [ ] **Step 3: 引入依赖与常量**

(a) 顶部 import 增加 groupTasks 和 EPMS 类型标签：
```tsx
import { groupTasks } from '@/lib/groupTasks'
```
（`TASK_TYPE_LABELS` 不在 portal app 内——portal 没有 taskTypes.ts。为避免跨 app 依赖，在本文件内内联一份 EPMS 类型标签映射，见下。）

(b) 在 `MODULE_STYLE` 常量（原 211 行）附近新增：模块顺序、模块表头文案、EPMS 类型标签、VMS 标签、以及组顺序。放在 `MODULE_STYLE` 定义之后：
```tsx
const MODULE_ORDER: UnifiedTask['module'][] = ['EPMS', 'EXPENSE', 'VMS']

const MODULE_HEADING: Record<UnifiedTask['module'], string> = {
  EPMS: 'EPMS', EXPENSE: 'Expense', VMS: 'Visitor',
}

// EPMS engine task types → display labels (inlined; portal has no taskTypes.ts).
const EPMS_TYPE_LABELS: Record<string, string> = {
  create_pr: 'Create Purchase Request',
  create_po: 'Create Purchase Order',
  create_pa: 'Create Payment Application',
  create_prepayment_pa: 'Create Prepayment PA',
  approve_pr: 'Approve Purchase Request',
  approve_po: 'Approve Purchase Order',
  approve_pa: 'Approve Payment Application',
  place_order: 'Place Order',
  confirm_settlement: 'Confirm Settlement',
  review_match: 'Review Invoice Match',
  match_invoice: 'Match Invoice to PO',
}

const VMS_DOC_LABELS: Record<string, string> = {
  vms_visit: 'Visit Approvals',
  vms_train: 'Training Confirmations',
  vms_ppe:   'PPE Confirmations',
}

// Within-module group order. Keys are scoped by module in practice (an OA status
// never collides with an EPMS type), so one flat list is unambiguous. Unlisted
// keys fall to the end.
const GROUP_ORDER = [
  // EPMS
  'approve_pr', 'approve_po', 'approve_pa', 'place_order',
  'create_pr', 'create_po', 'create_pa', 'create_prepayment_pa',
  'review_match', 'match_invoice', 'confirm_settlement',
  // Expense (OA my-actions statuses)
  'submitted', 'in_review', 'approved',
  // VMS
  'vms_visit', 'vms_train', 'vms_ppe',
]

function humanize(s: string): string {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
```

- [ ] **Step 4: 在映射里填 groupKey / groupLabel**

In the `allTasks` useMemo (原 417-488 行):

(a) `oaRows` 每行（原 422-433 行的对象）增加：
```tsx
groupKey: t.status,
groupLabel: STATUS_LABEL[t.status] ?? humanize(t.status),
```
（`STATUS_LABEL` 已在文件顶部定义，含 submitted/in_review/approved/pending_approval。）

(b) `epmsRows` 每行（原 463-474 行 return 的对象）增加。因为 epmsRows 可能落到 EPMS / EXPENSE / VMS 三种 module，需按最终 `module` 决定分组键：
先在该 return 之前（已算出 `module`、`path` 之后）计算：
```tsx
let groupKey: string
let groupLabel: string
if (module === 'VMS') {
  groupKey = t.document_type
  groupLabel = VMS_DOC_LABELS[t.document_type] ?? humanize(t.document_type)
} else {
  // EPMS-owned or OA-owned-surfaced-via-approval: bucket by the engine task type.
  groupKey = t.type
  groupLabel = EPMS_TYPE_LABELS[t.type] ?? humanize(t.type)
}
```
然后在 return 对象里加：
```tsx
groupKey,
groupLabel,
```

- [ ] **Step 5: 两级渲染**

替换 Task inbox 列表渲染（原 640-646 行的非空分支 `<div className="space-y-2">{allTasks.map(...)}</div>`）为两级结构：
```tsx
) : (
  <div className="space-y-6">
    {MODULE_ORDER.map((mod) => {
      const modTasks = allTasks.filter((t) => t.module === mod)
      if (modTasks.length === 0) return null
      const groups = groupTasks(
        modTasks,
        (t) => t.groupKey,
        (_k, sample) => sample.groupLabel,
        GROUP_ORDER,
      )
      const style = MODULE_STYLE[mod]
      return (
        <div key={mod} className="space-y-3">
          {/* Module header */}
          <div className="flex items-center gap-2 border-b border-neutral-200 pb-1.5">
            <span className={cn('text-xs font-bold uppercase tracking-wider', style.badge)}>
              {MODULE_HEADING[mod]}
            </span>
            <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-neutral-100 px-1.5 text-[10px] font-bold text-neutral-500">
              {modTasks.length}
            </span>
          </div>
          {/* Type sub-groups */}
          {groups.map((group) => (
            <section key={group.key}>
              <div className="mb-1.5 flex items-center gap-2 pl-0.5">
                <h3 className="text-xs font-semibold text-neutral-600">{group.label}</h3>
                <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-neutral-100 px-1 text-[10px] font-semibold text-neutral-400">
                  {group.items.length}
                </span>
              </div>
              <div className="space-y-2">
                {group.items.map((task) => (
                  <TaskRow key={task.id} task={task} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )
    })}
  </div>
)}
```
（`MODULE_STYLE`、`TaskRow`、`cn` 均为文件内既有；`RecentActivity` 仍用扁平 `allTasks`，不改。）

- [ ] **Step 6: 类型检查**

Run:
```bash
cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -E 'PortalHome|groupTasks'
```
Expected: **无输出**。

- [ ] **Step 7: 目视 QA**

`npm run dev`（portal 目录），用一个跨模块都有待办的账号打开 Portal 首页。
Expected:
- "My Task Inbox" 下先按模块分区（EPMS → Expense → Visitor，空模块不出现），每个模块表头带该模块总数。
- 模块区内再按类型分小组，每小组带计数；顺序符合 GROUP_ORDER。
- 每张卡仍带模块徽章、金额、跳转、urgent 标记；去重后不重复。
- 右侧 Platform Health / Recent Activity 不变。

- [ ] **Step 8: Commit**

```bash
git add portal/src/lib/groupTasks.ts portal/src/pages/PortalHome.tsx
git commit -m "feat(portal): two-level grouped task inbox (module then task type)"
```

---

## Task 5: 四端统一 QA + 收尾

**Files:** 无代码改动（除非发现 bug）。

- [ ] **Step 1: 四端类型检查汇总**

Run（逐个 app）：
```bash
for d in epms oa vms portal; do echo "=== $d ==="; (cd $d && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -E 'TaskInboxPage|TaskListPage|PortalHome|groupTasks'); done
```
Expected: 四个 app 都**无输出**（改动文件零新增错误）。

- [ ] **Step 2: 边界目视检查**

对每端确认：
- 只有一种类型时 → 仍显示该单组标题（不退化成无标题）。
- 全空 → 各端原 empty state 正常。
- 未在 GROUP_ORDER 登记的新类型（如后端将来加新 task_type）→ 落到末尾、不消失（可临时改一条 mock 数据验证，或代码走查确认 `rest` 分支）。

- [ ] **Step 3: 更新 spec 状态（可选）**

若实现与 spec 有偏差，回填到 `docs/superpowers/specs/2026-07-21-task-inbox-group-by-type-design.md`。

- [ ] **Step 4: 分支收尾**

按 superpowers:finishing-a-development-branch 决定合并/PR/清理。**发布须遵循多会话纪律**：分支 merge 进 main → 全 15 镜像同一 sha 构建部署（发布前先核实生产当前 TAG）。发布动作等用户明确指示，不自作主张 push/部署。

---

## Self-Review（已核对）

**Spec 覆盖**：
- 四处分组（§3.1-3.4）→ Task 1/2/3/4 ✓
- EPMS 去类型下拉（§3.1）→ Task 1 Step 2(f) ✓
- OA 保留 doc-type 下拉（§3.2）→ Task 2 未动下拉 ✓
- VMS 三组（§3.3）→ Task 3 ✓
- Portal 两级（§3.4）→ Task 4 ✓
- 固定顺序 / 空组隐藏 / 复用卡片（§2）→ 各 Task 的 GROUP_ORDER + `groupTasks` 空桶省略 + 复用 FullTaskCard/TaskCard/TaskRow ✓
- 本地实现不抽共享包（§4）→ 每 app 独立 groupTasks.ts ✓
- 纯前端无后端（§5）→ 无 api 改动 ✓

**占位符扫描**：无 TBD/TODO；所有步骤含完整代码。

**类型一致性**：`groupTasks(items, keyOf, labelOf, order)` 四处签名一致；`labelOf(key, sample)` 二参在 Portal（用 sample.groupLabel）与其它端（忽略 sample）均兼容；`TaskGroup<T>` 字段 `{key,label,items}` 全程一致。

**已知取舍**：Portal 内联 `EPMS_TYPE_LABELS`（不跨 app 依赖 epms/taskTypes.ts）——与 epms 源保持同步的成本换取 app 解耦；若将来类型频繁变动可再抽共享常量。
