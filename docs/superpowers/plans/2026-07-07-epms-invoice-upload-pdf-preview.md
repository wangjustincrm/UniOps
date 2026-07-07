# EPMS Upload Invoice — PDF Preview Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a live preview of the uploaded invoice file (PDF/image) to the left of the form in the EPMS Upload Invoice modal, so users can verify AI-parsed fields against the source document.

**Architecture:** Frontend-only change to the `UploadModal` in `epms/src/pages/invoices/InvoiceListPage.tsx`. A new presentational component `FilePreviewPanel` owns the blob-URL lifecycle and renders PDFs via the browser's native viewer (`<iframe>`) and images via `<img>` — zero new dependencies. The modal widens from `max-w-xl` to `max-w-6xl` (and takes a fixed `h-[92vh]`) only when a file is selected; with no file it looks exactly as today.

**Tech Stack:** React 19 + TypeScript, Tailwind CSS v4, lucide-react icons. No test runner exists in the epms frontend — verification is typecheck + lint + manual browser check.

**Spec:** `docs/superpowers/specs/2026-07-07-epms-invoice-upload-pdf-preview-design.md`

## Global Constraints

- All user-facing strings in English (project convention).
- No new npm dependencies (native browser PDF rendering; do NOT add react-pdf).
- No changes to parsing (`parseInvoiceFile`), submit, attachment-upload, or PO-matching logic. No backend changes.
- Typecheck command (TS 6.0 — `tsc -b` fails on deprecated `baseUrl`): run from `epms/`: `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
- Working tree has unrelated uncommitted changes — `git add` ONLY the files listed in each task. Never `git add -A`.

---

### Task 1: `FilePreviewPanel` component

**Files:**
- Create: `epms/src/pages/invoices/FilePreviewPanel.tsx`

**Interfaces:**
- Consumes: nothing from other tasks (only `lucide-react` icons already in deps).
- Produces: `export function FilePreviewPanel({ file }: { file: File }): JSX.Element` — Task 2 imports this. The component must fill its parent's height (`h-full` root), so the parent must give it a definite height.

- [ ] **Step 1: Create the component file**

Create `epms/src/pages/invoices/FilePreviewPanel.tsx` with exactly:

```tsx
import { useEffect, useState } from 'react'
import { ExternalLink, FileText } from 'lucide-react'

// Live preview of the uploaded invoice file so the user can verify AI-parsed
// fields against the source document. PDFs render in the browser's native
// viewer (iframe — has its own paging/zoom/search toolbar); images in <img>.
// Owns the blob-URL lifecycle: revoked on file change and unmount.
export function FilePreviewPanel({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let objectUrl: string | null = null
    try { objectUrl = URL.createObjectURL(file) } catch { objectUrl = null }
    setUrl(objectUrl)
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [file])

  const kind = file.type === 'application/pdf'
    ? 'pdf'
    : file.type.startsWith('image/') ? 'image' : 'unsupported'

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-neutral-200 bg-neutral-800">
      {/* Toolbar: file name + open in new tab */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-neutral-700 bg-neutral-900 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <FileText className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
          <span className="truncate text-xs font-medium text-neutral-200">{file.name}</span>
        </div>
        {url && (
          <a
            href={url} target="_blank" rel="noreferrer"
            className="inline-flex shrink-0 items-center gap-1 text-xs text-neutral-400 hover:text-white"
          >
            <ExternalLink className="h-3 w-3" />
            Open in new tab
          </a>
        )}
      </div>
      {url && kind === 'pdf' && (
        <iframe src={url} title="Invoice preview" className="w-full flex-1 border-0" />
      )}
      {url && kind === 'image' && (
        <div className="flex-1 overflow-auto p-3">
          <img src={url} alt="Invoice preview" className="mx-auto max-w-full object-contain" />
        </div>
      )}
      {(!url || kind === 'unsupported') && (
        <div className="flex flex-1 items-center justify-center text-sm text-neutral-400">
          Preview not available
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Typecheck**

Run from `epms/`:
```
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```
Expected: exits 0, no errors mentioning `FilePreviewPanel.tsx`. (If pre-existing errors in OTHER files appear, they are out of scope — only confirm this file is clean.)

- [ ] **Step 3: Commit**

```bash
git add epms/src/pages/invoices/FilePreviewPanel.tsx docs/superpowers/specs/2026-07-07-epms-invoice-upload-pdf-preview-design.md docs/superpowers/plans/2026-07-07-epms-invoice-upload-pdf-preview.md
git commit -m "feat(epms): add FilePreviewPanel for invoice upload preview"
```

---

### Task 2: Two-column layout in `UploadModal`

**Files:**
- Modify: `epms/src/pages/invoices/InvoiceListPage.tsx` (the `UploadModal` function, currently ~lines 97–759)

**Interfaces:**
- Consumes: `FilePreviewPanel` from Task 1 (`import { FilePreviewPanel } from './FilePreviewPanel'`).
- Produces: nothing consumed by later tasks.

Layout contract (from the approved mockup): drop zone stays full-width at top (collapses to the file chip once a file is chosen, as today). Below it, when a file is selected: left column = preview (~55%), right column = the entire existing form, independently scrollable. No file = exactly today's narrow single-column modal.

- [ ] **Step 1: Add the import**

At the top of `InvoiceListPage.tsx`, next to the existing local import:

```tsx
import { InvoiceAllocationPanel } from './InvoiceAllocationPanel'
```

add:

```tsx
import { FilePreviewPanel } from './FilePreviewPanel'
```

- [ ] **Step 2: Widen the modal when a file is selected**

In `UploadModal`'s return, find the modal container (currently line ~287):

```tsx
      <div className="w-full max-w-xl rounded-2xl bg-white shadow-2xl flex flex-col max-h-[92vh]">
```

replace with (fixed `h-[92vh]` in wide mode gives the iframe a definite height through the flex chain):

```tsx
      <div className={cn(
        'w-full rounded-2xl bg-white shadow-2xl flex flex-col',
        file ? 'max-w-6xl h-[92vh]' : 'max-w-xl max-h-[92vh]'
      )}>
```

- [ ] **Step 3: Make the modal body a non-scrolling flex container in wide mode**

Find the body wrapper (currently line ~304):

```tsx
        <div className="overflow-y-auto flex-1 px-6 py-5 flex flex-col gap-4">
```

replace with (in wide mode the RIGHT COLUMN scrolls, not the whole body; `min-h-0` lets nested flex children shrink so overflow works):

```tsx
        <div className={cn('flex-1 min-h-0 px-6 py-5 flex flex-col gap-4', !file && 'overflow-y-auto')}>
```

- [ ] **Step 4: Pin the drop zone and parse-error banner (no shrink)**

(a) In the drop-zone `div` (the one with `onDragOver`/`onDrop`, currently line ~306), the `cn(...)` first argument is:

```
'flex flex-col items-center gap-2 rounded-xl border-2 border-dashed py-6 cursor-pointer transition-colors',
```

append ` shrink-0`:

```
'flex flex-col items-center gap-2 rounded-xl border-2 border-dashed py-6 cursor-pointer transition-colors shrink-0',
```

(b) In the AI parse-error banner (currently line ~349):

```tsx
            <div className="flex items-center gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700">
```

replace with:

```tsx
            <div className="flex shrink-0 items-center gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-warning-700">
```

- [ ] **Step 5: Wrap the form fields in the two-column split**

The form fields are the contiguous JSX from the `{/* Vendor info */}` comment (currently line ~355) through the closing `</div>` of the Notes block (currently line ~736 — the `div` containing `<label ...>Notes (optional)</label>` and the `<textarea>`). Do NOT change any of that JSX — only wrap it.

Insert BEFORE `{/* Vendor info */}`:

```tsx
          {/* File selected: preview (left) + form (right). No file: `contents`
              makes both wrappers transparent so the layout is exactly as before. */}
          <div className={cn(file ? 'flex flex-1 min-h-0 gap-5' : 'contents')}>
            {file && (
              <div className="w-[55%] shrink-0">
                <FilePreviewPanel file={file.raw} />
              </div>
            )}
            <div className={cn(file ? 'flex-1 min-w-0 overflow-y-auto flex flex-col gap-4 pr-1' : 'contents')}>
```

Insert AFTER the Notes block's closing `</div>` (i.e. just before the body wrapper's own closing `</div>` that precedes the `{/* Footer */}` comment):

```tsx
            </div>
          </div>
```

Then re-indent the wrapped form-field JSX by one level if trivial to do; if the diff noise outweighs the benefit, leaving indentation as-is is acceptable (project has no enforced JSX indent lint).

- [ ] **Step 6: Typecheck + lint**

Run from `epms/`:
```
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
npm run lint
```
Expected: exit 0 / no NEW errors in `InvoiceListPage.tsx` or `FilePreviewPanel.tsx` (pre-existing lint findings in other files are out of scope).

- [ ] **Step 7: Manual verification in the local dev stack**

Start the local dev stack (self-contained, local postgres) and open the EPMS frontend. In Invoices → Upload Invoice:

1. Modal opens narrow (unchanged) with no file.
2. Drop a multi-page PDF → modal widens; left panel shows the PDF in the native viewer (paging/zoom toolbar present); AI parsing fills the right-hand form; both columns scroll independently.
3. "Open in new tab" opens the blob URL in a full browser tab.
4. Drop a JPG/PNG instead → image preview renders (`object-contain`, scrollable).
5. Click × on the file chip → modal collapses back to narrow single column; form values remain.
6. Re-select a different file → preview swaps (old blob URL revoked — verify no accumulation via `performance`/memory tab spot-check or just confirm swap works).
7. Sanity-check at ~1280 px viewport width: two columns still usable.

Expected: all 7 pass. If the iframe shows a download prompt instead of inline PDF (some browser configs), that is browser-level behavior — note it, don't work around it in code.

- [ ] **Step 8: Commit**

```bash
git add epms/src/pages/invoices/InvoiceListPage.tsx
git commit -m "feat(epms): side-by-side file preview in Upload Invoice modal"
```

---

## Self-Review Notes

- Spec coverage: layout behavior (Task 2 Steps 2–5), preview rendering + blob lifecycle + toolbar + unsupported fallback (Task 1), error handling (Task 1 try/catch + fallback branch), English-only copy (both), no-backend/no-logic-change (Global Constraints), verification (Task 2 Steps 6–7). Spec's "vitest suite passes" line is superseded here: epms has no test runner; typecheck + lint + manual stand in.
- The `contents` display trick keeps the no-file DOM layout byte-identical to today, avoiding regression risk in the narrow modal.
