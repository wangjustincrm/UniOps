# 风格审计:EPMS vs OA-Expense 前端

- **基准**:`main = 5c8a98f`
- **范围**:`epms/`(采购,161 前端文件) vs `oa/`(费用/差旅,36 前端文件)
- **共享包**:`packages/shell`(`@uniops/shell`)—— 设计令牌 + UI 原语 + 多页签外壳的正源
- **结论一句话**:地基共享(tokens、shell、框架栈一致),但 **EPMS 用 shell `Button` 原语(全库 189 次),OA 零次用原语、98 个裸 `<button>` 全手搓**;审批弹窗与表单提交在两模块乃至各自内部都各写各的。

> **收敛原则(用户定)**:**EPMS 作为模板,OA 所有样式一律参照 EPMS。** 下文所有"漂移"以 EPMS 为正,OA 向 EPMS 看齐(采用 shell `Button` 等原语 + EPMS 的弹窗/表单/动作栏模式)。此原则仅指 UI 样式;工具链版本(TS/ESLint)另论。

---

## 0. 已统一的部分 ✅

| 维度 | 状态 |
|---|---|
| 设计令牌 | 两边 `index.css` 均 `@import "@uniops/shell/tokens.css"`,完全一致 |
| 框架栈 | React 19 / Vite 8 / react-router 7 / zustand 5 / @tanstack/react-query |
| 多页签外壳 | 都用 shell 的 `TabHost/TabBar/TabRouterSync/useReplaceTab` |
| Badge 原语 | 都来自 shell,各自包一层 `StatusBadge` |
| `cn` 逻辑 | 一致(clsx + twMerge) |
| 审批意见必填规则 | 一致:approve 可选、reject/return 必填 |

---

## 1. 工具链 / 依赖漂移 🟠

| | EPMS | OA |
|---|---|---|
| TypeScript | `~5.9.3` | `~6.0.2` |
| ESLint | `^9.39.4` | `^10.2.1` |
| lucide-react | `^0.577.0` | `^1.14.0`(**大版本差**,图标名/props 可能不兼容) |
| @anthropic-ai/sdk | `^0.80.0` | `^0.92.0` |
| radix-ui | 全套(dialog/select/popover…) | **完全没有** |
| 表单库 | react-hook-form + zod(仅 2 页用) | 无 |

OA 已对齐 Portal(TS6),EPMS 落后。类型严格度、lint 规则、图标 API 两边不一致。

---

## 2. UI 原语采用 🔴

- shell 导出整套原语(`Button/Card/Input/FormField/Label/Skeleton/Badge/Pagination`)。
- **EPMS**:在 `components/ui/*` 薄再导出 shell 并使用;全库 `<Button>` 189 次(但页面里仍有 ~220 处裸 `<button>`)。
- **OA**:只从 shell 拿 `Badge` + tab;**从不导入 `Button/Card/Input`**;全库 `<Button>` = **0**、裸 `<button>` = **98**。
- 硬编码 `bg-primary-700` 主按钮:OA 16 个文件 / EPMS 9 个文件——语义主色未走原语,规范一改不跟随。

**后果**:同一套 tokens,按钮/卡片/输入框的 padding、圆角、hover、focus ring 在两模块各写各的 → 视觉与交互漂移;shell 的 Button 在 OA 侧形同摆设。

---

## 3. 审批操作 UI 🔴

### 3.1 动作按钮布局 —— 3 页 3 样
| 页面 | 布局 | 控件 | 图标 |
|---|---|---|---|
| EPMS `PaDetailPage`/`PrDetailPage` | 主按钮 Approve + `More(…)` 下拉藏 Return/Reject | shell `Button` | 有 |
| OA `pages/pa/PaDetailPage.tsx:151` | Approve/Return/Reject 平铺 | 裸 button | 有 |
| OA `pages/expenses/ExpenseDetailPage.tsx:491` | Return/Reject/**Approve** 平铺(Approve 最右) | 裸 button | **无** |

连 OA 自己两个详情页都不一致(图标有无、Approve 左右位置)。

### 3.2 审批意见弹窗 —— 至少 4 份重复,样式全漂移
- EPMS:`PaDetailPage`、`PrDetailPage` 各内联一份 `createPortal` 弹窗。
- OA:`PaDetailPage:203`、`ExpenseDetailPage` 各定义一份同名 `ActionModal`。

| 细节 | EPMS 弹窗 | OA `ActionModal` |
|---|---|---|
| 渲染 | `createPortal`(合规) | 直接 `fixed` div,**无 portal** |
| 遮罩 | `bg-neutral-900/40 backdrop-blur-sm` | `bg-black/40`(无模糊) |
| 卡片 | `max-w-md rounded-2xl shadow-2xl` | `max-w-sm rounded-xl border shadow-xl` |
| 头部 | 圆形图标 + 标题 + 单号 + 分隔线 | 只有一个 `h3` 标题 |
| 意见框 | 内嵌图标 + `focus:ring-2 ring-primary-600` + `autoFocus` | 无图标 + `focus:border-primary-400` |

---

## 4. Form 提交样式 🔴

### 4.1 提交机制 —— EPMS 内部就三套
| 页面 | 表单机制 | 提交控件 |
|---|---|---|
| EPMS `PrCreatePage` | react-hook-form + `<form onSubmit={handleSubmit()}>` | `<Button type="submit">` |
| EPMS `PoCreatePage` | 自建 `handleSubmit(status)`,无 `<form>` | `<Button onClick>` |
| EPMS `PaCreatePage` | 自建 `handleSubmit`,无 `<form>` | `<Button onClick>` |
| OA `ExpenseCreatePage` | 原生 `<form onSubmit>` + react-query mutation | **裸 `<button type="submit">`** |
| OA `TrvCreatePage` | 原生 `<form onSubmit>` | 裸 `<button type="submit">` |
| OA `PaDirectCreatePage` | 原生 `<form onSubmit>` | 裸 `<button type="submit">` |

OA 机制统一但控件全裸;EPMS 控件统一(`Button`)但机制三套。两个 app 各在不同轴上不一致。

### 4.2 提交按钮样式 —— OA 硬编码主色,尺寸还不齐
- OA `ExpenseCreatePage:462`:`bg-primary-700 hover:bg-primary-800 px-4 py-2`。
- OA `TrvCreatePage:520`:`bg-primary-700 … px-6 py-2.5 self-start`——padding 与 Expense 页不同,且无 Cancel、无动作栏。

### 4.3 动作栏布局
- EPMS:卡片式 action bar,Cancel 左 / Save Draft + Submit 右。
- OA Expense:卡片式,Cancel + 主按钮。
- OA Trv:无卡片、无 Cancel,单个 `self-start` 按钮贴左。

### 4.4 校验错误展示
- EPMS `PrCreatePage`:RHF 字段级红字。
- OA:顶部 `ErrorBanner`(`danger-50/200/700 + AlertTriangle`),且该样式在 `TrvCreatePage` 里又内联手写一遍。
→ 一个字段级、一个顶部横幅,反馈层级不同。

---

## 5. 提交流程差异:OA 两步 vs EPMS 一步(⚠️ 业务规则导致,结论:暂不改)

**现象**:OA 所有 Create 页只有一个「Save as Draft」,建成后跳详情页才出现「Submit」;EPMS 是「Save as Draft」+「Submit for Approval」并列两个按钮。

**根因(非前端疏忽,是后端强制)**:
1. OA `POST /expenses` 永远建成 **draft**,不收 status([expense-api/app/api/v1/expenses.py:243](../expense-api/app/api/v1/expenses.py#L243));提交是独立的 `POST /{id}/action {action:'submit'}`,委派 approval-api。
2. **EXP-007/TRV-008 闸门**([expenses.py:473](../expense-api/app/api/v1/expenses.py#L473)):EXP/TRV 提交时若 0 张收据附件 → **409 拒绝**。
3. 附件**只能加到 draft**([expense_attachments.py:65](../expense-api/app/api/v1/expense_attachments.py#L65):"Cannot add attachments to a submitted claim")。

三条合起来 → 物理上必须「建草稿 → 给草稿传收据 → 才能提交」。EPMS 的 PR/PO 创建接口直接吃 `status:'draft'|'submitted'` 且无"提交前必须有附件"硬闸,所以能一步到位。

**结论**:这是「报销必须有收据」业务规则逼出来的**合理差异**,暂不改。
**若未来要统一**为 EPMS 式并列双按钮,推荐方案:创建页加收据上传,点 Submit 时前端链式执行 `建草稿 → 传收据 → 提交`;EXP/TRV 若未传收据则禁用 Submit 并提示。后端基本不动。

---

## 6. 其他次要项 🟡

- `cn()` 各写一份 —— shell 已导出 `lib/cn.ts`,两 app 都在本地 `lib/utils.ts` 重新实现,没人用 shell 的。
- 货币格式化两套:EPMS `formatCAD(number)`(对 Decimal-as-string 无防护) vs OA `formatAmount(number|string, currency)`(处理 NaN→'—',更稳)。
- `StatusBadge`:两份 `STATUS_CONFIG` 靠注释手动 mirror,fallback 逻辑不一致(OA 有 `titleCase` 兜底,EPMS 落 neutral)。
- `AppLayout` 各自实现:EPMS 94 行 vs OA 377 行。
- `api.ts` 各写一份:EPMS 337 行 / OA 209 行,均 fetch+Bearer+401 刷新,逻辑重叠。
- BASE 默认值约定不同:EPMS `VITE_API_URL || ''`;OA `VITE_API_URL || '/api/v1'`。

---

## 7. 收敛建议(方向:OA → EPMS;均为待办,未实施)

> 原则:**EPMS 为模板,OA 参照 EPMS。** 以下让 OA 的样式与 EPMS 对齐。

1. **OA 接入 shell `Button`(EPMS 同款)** —— 98 个裸 button → shell `Button` 原语,干掉 16 处硬编码 `bg-primary-700`,主色/hover/禁用态随 EPMS 走原语。
2. **OA 审批弹窗改用 EPMS 模式** —— 采用 EPMS 的 `createPortal` + `rounded-2xl` + 图标头 + focus ring;进一步可把 EPMS 的弹窗抽成 `@uniops/shell` 的 `<ApprovalActionModal>`,两 app 共用。
3. **OA 审批动作按钮布局对齐 EPMS** —— 统一按钮顺序/图标(EPMS:主 Approve + `More(…)` 藏 Return/Reject),消掉 OA 两个详情页自相矛盾。
4. **OA 表单动作栏对齐 EPMS** —— 卡片式 action bar,Cancel 左 / Save Draft + Submit 右;padding/对齐照 EPMS,消除 Expense/Trv 之间差异。
5. **OA 校验错误对齐 EPMS** —— 字段级红字(EPMS PrCreate 的 RHF `FormField` 模式)为准。
6. **消重到 shell**:`cn`(直接用 shell 的)、`StatusBadge` 通用基表。货币格式化取 OA 那版的健壮性(处理 Decimal 字符串/NaN),但对外统一命名/签名。

> - 第 5 节(两步提交)已决定暂不改(业务规则导致的合理差异)。
> - 工具链版本(TS/ESLint/lucide)不属"样式",单列第 2 节,按需另行处理。
