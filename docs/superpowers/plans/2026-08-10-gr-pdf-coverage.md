# GR PDF 覆盖缺口与 Regenerate 按钮 — 实施计划

> 追加到 `feature/pdf-signatories`（用户决定并入当前分支）。分支因此从纯后端变为后端 + 前端，发布时需同时重建 `epms-api` 与 `epms` 前端镜像。

**Goal:** Service GR 也能生成 PDF；GR 附件区补上 Regenerate PDF 按钮；GR PDF 补 `Collected By` 行。

## 现状与根因（已实测）

`_attach_gr_pdf` 在整个仓库里**只有一个调用点** —— `app/crud/gr.py::action` 的 `acknowledge` 分支。

而 `confirm` 分支显式允许 service GR **从 `pending_ack` 直接确认**（注释原文：`collapses acknowledge + confirm into one step`），整条 acknowledge 分支被绕开，PDF 永远不生成。

dev 库（生产快照）实测：

| gr_type | status | 张数 | 有 PDF |
|---|---|---|---|
| physical | collected | 771 | 52 |
| physical | pending_ack | 5 | 0 |
| service | confirmed | **20** | **0** |
| service | pending_ack | 1 | 0 |
| service | cancelled | 1 | 0 |

进一步佐证：`status IN ('confirmed','collected')` 的单据里，**service 20/20、physical 719/771 的 `acknowledged_at` 等于 `collected_at`** —— 即绝大多数 GR 都走了合并路径。所以这不只是 service 的问题，physical 也大面积漏生成；只是 service 是 100% 漏。

## 用户裁决

1. **生成时机：只补 `confirm` 分支。** 不改 `collect` 分支，physical 那 719 张存量靠 Regenerate 按钮补。
2. **`Collected By`：加。** 理由是 confirm 分支生成时 `collected_by` 已赋值，且有了 Regenerate 按钮后终态单据总能拿到值。physical 走 acknowledge 生成时它仍为空 —— 现有渲染逻辑会自动省略空行，无需特判。
3. **并入 `feature/pdf-signatories`**，不另开分支。

## 范围外

- `collect` 分支不加生成（用户明确"只补 confirm"）。
- 不改 GR 元信息格的存量列宽溢出（`Storage Location` / `Date Received` / `Acknowledged`）—— 用户先前已裁决不动。
- 附件下载失败被静默吞掉的问题（`if (!res.ok) return`，四个 service 全中）另议，不在本计划内。

---

## Task A（后端）：confirm 生成 PDF + Regenerate 端点 + Collected By

**Files**
- Modify `epms-api/app/crud/gr.py` — `confirm` 分支调用 `_attach_gr_pdf`；`_attach_gr_pdf` 改为可替换同名旧附件
- Modify `epms-api/app/api/v1/gr_attachments.py` — 新增 `POST /regenerate-pdf`
- Modify `epms-api/app/services/pdf_gr.py` — Signatures 小节加 `Collected By`
- Test `epms-api/tests/test_gr_pdf_coverage.py`（新建）

**要点**

1. `_attach_gr_pdf` 目前只 `db.add(...)`，重复调用会产生**两条同名附件**。改为先删同名旧行（有 `storage_key` 的同时删文件服务器上的文件，照抄 `pr_attachments.regenerate_pdf` 的替换逻辑），再写新的。这是 confirm 分支能安全调用的前提 —— 否则走过 acknowledge 的 physical GR 会拿到两份 PDF。
2. `confirm` 分支里，`_attach_gr_pdf` 必须放在 `gr.collected_by = ...` **之后**，否则 Collected By 仍是空的。
3. 沿用 acknowledge 分支既有的 best-effort 包装（`try` 上传失败回落 `token=None` 内联存储），GR 确认不能因为文件服务器挂了而失败。
4. Regenerate 端点镜像 `pr_attachments.py:85`，允许的状态集：`{"collection_pending", "collected", "confirmed", "discrepancy"}`（即 acknowledge 之后的所有非终止取消态）。
5. `pdf_gr.py` 的 `generate_gr_pdf` 追加第 8 个可选位置参数 `collected_by`（`run_in_executor` 只吃位置参数，必须在末尾），渲染顺序 Created → Received → Acknowledged → Collected，空值省略行。

**测试**
- service GR 从 `pending_ack` 直接 `confirm` → 产生恰好 1 条 `<number>.pdf` 附件
- physical GR 先 `acknowledge` 再 `confirm` → 仍然只有 **1** 条同名附件（证明替换而非追加）
- Regenerate 端点在 `confirmed` 上返回 200 且替换；在 `pending_ack` 上返回 409
- PDF 内容含 `Collected By` 与该姓名

## Task B（前端）：GR Regenerate PDF 按钮

**Files**
- Modify `epms/src/services/grAttachments.ts` — 加 `regeneratePdf`
- Modify `epms/src/hooks/useGrAttachments.ts` — 加 `useRegenerateGrPdf`
- Modify `epms/src/pages/gr/GrDetailPage.tsx` — Attachments 区加按钮

照抄 `prAttachments.ts:19` / `usePrAttachments.ts:28` / `PrDetailPage.tsx:512-523` 三处的现成写法，包括 `RotateCcw` 图标与 `animate-spin` 的 pending 态。按钮的显示条件用 GR 的状态集（与后端端点一致），不要照抄 PR 的 `status === 'approved'`。

**门禁**：`npx tsc -p tsconfig.app.json`（epms 前端）。基线随代码漂移，**动手前先自己量当前基线错误数**，不要照抄历史数字。
