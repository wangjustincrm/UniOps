# PA Document Chain — Attachment Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a button in the PA Detail "Document Chain" header that opens a slide-over panel aggregating every attachment along the PA's direct lineage (PR, PO, GR, INV, PA), with single/bulk download (ZIP) and single/bulk print.

**Architecture:** Frontend-only composition in the epms app. A hook resolves the lineage (reusing existing single-doc hooks) and fetches each doc's attachment list from epms-api (PR/PO/GR/PA) and expense-api (INV), normalizing to one shape. A pure helper module fetches blobs (bounded concurrency), builds a ZIP with JSZip, and rasterizes PDFs/images with pdf.js into a single hidden-iframe print document. No backend, no migration, no backend image rebuild.

**Tech Stack:** React 19 + TypeScript 5.9, TanStack Query, `pdfjs-dist` (already present), `jszip` (new), Tailwind.

**Spec:** `docs/superpowers/specs/2026-07-24-pa-chain-attachments-design.md`

---

## ⚠️ VERIFICATION GATES (READ FIRST — supersedes any `npm run build`/`npm run lint` wording below)

This app's `npm run build` (`tsc -b && vite build`) and `npm run lint` (`eslint .`) are **RED on
the baseline** (main): **59 pre-existing `tsc` errors** and **~220 pre-existing eslint problems**.
So they are NOT clean gates. Use baseline-relative checks instead:

1. **Typecheck (primary gate):**
   `npx tsc -p tsconfig.app.json --noEmit`
   Baseline = **59 errors**. Requirement: total stays ≤ 59 AND **zero** errors reference any file
   you created/modified. Quick check:
   `npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -E 'attachmentBundle|useChainAttachments|ChainAttachmentsPanel|DocumentChainTree|lib/utils|services/invoices'`
   → must print **nothing**.
   (Do NOT use `npm run build`; its `tsc -b` variant and `vite build` are not the gate here.)

2. **Lint (your files only):**
   `npx eslint <each file you created/modified>`
   → must be clean. Do NOT run `eslint .` (220 pre-existing problems drown the signal).

3. **Manual QA:** dev server (`npm run dev`) per Task 5.

Files touched by this feature all have **0 baseline tsc errors** (`DocumentChainTree.tsx`,
`lib/utils.ts`, `services/invoices.ts` are clean at baseline), so any error mentioning them is
one you introduced — fix it.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `epms/src/lib/attachmentBundle.ts` (create) | Pure/util layer: authed blob fetch with concurrency cap, single download, ZIP build (folder-per-doc, collision suffix), pdf.js rasterization, single+bulk print into one hidden iframe |
| `epms/src/hooks/useChainAttachments.ts` (create) | Resolve PA direct lineage → fetch every doc's attachment list → normalized grouped model + `total`/`isLoading`/`error` |
| `epms/src/components/shared/ChainAttachmentsPanel.tsx` (create) | Slide-over drawer: grouped list, single download/print per row, bulk action bar with progress |
| `epms/src/components/shared/DocumentChainTree.tsx` (modify) | Header becomes a flex row; render trigger button (PA only) + own panel open/close state |
| `epms/package.json` (modify) | Add `jszip` dependency |

**Type contracts (defined once, referenced everywhere):**

```ts
// attachmentBundle.ts
export interface BundleFile {
  filename: string
  folder: string          // ZIP folder / print caption prefix, e.g. "PO-00123"
  fetchUrl: string        // full single-file endpoint URL
  contentType: string
  printable: boolean      // application/pdf or image/*
}

// useChainAttachments.ts
export interface ChainAttachment {
  id: string
  filename: string
  contentType: string
  sizeBytes: number
  fetchUrl: string
  printable: boolean
}
export interface ChainDocGroup {
  docType: 'PR' | 'PO' | 'GR' | 'INV' | 'PA'
  docNumber: string
  docId: string
  attachments: ChainAttachment[]
}
```

---

## Task 0: Branch, worktree, and dependency

**Files:**
- Modify: `epms/package.json`

- [ ] **Step 1: Create an isolated worktree + branch** (multi-session discipline R1/R2 — do not touch `main`)

Run (Git Bash) from the uniops repo root `c:/Project/uniops`:

```bash
git worktree add ../uniops-pa-attach -b feature/pa-chain-attachments
cd ../uniops-pa-attach
```

All subsequent paths in this plan are relative to the epms package inside this worktree
(`c:/Project/uniops-pa-attach/epms`).

- [ ] **Step 2: Add the JSZip dependency**

Run:

```bash
cd epms
npm install jszip@^3.10.1
npm install --save-dev @types/jszip@^3.4.1
```

Expected: `jszip` appears under `dependencies` and `@types/jszip` under `devDependencies` in `epms/package.json`; `package-lock.json` updated.

- [ ] **Step 3: Verify install builds**

Run: `npm run build`
Expected: PASS (no new type/build errors introduced by the dependency).

- [ ] **Step 4: Move the spec + this plan into the worktree if not already present, then commit**

The spec (`docs/superpowers/specs/2026-07-24-pa-chain-attachments-design.md`) and this plan
(`docs/superpowers/plans/2026-07-24-pa-chain-attachments.md`) belong on this branch.

```bash
cd ..
git add docs/superpowers/specs/2026-07-24-pa-chain-attachments-design.md \
        docs/superpowers/plans/2026-07-24-pa-chain-attachments.md \
        epms/package.json epms/package-lock.json
git commit -m "chore(epms): scaffold PA chain-attachments feature (spec, plan, jszip dep)"
```

---

## Task 1: `attachmentBundle.ts` — fetch / ZIP / print utilities

**Files:**
- Create: `epms/src/lib/attachmentBundle.ts`

This module is pure/side-effect-isolated: no React, no store reads. The auth token is passed
in by the caller. It owns all the tricky logic (concurrency, collision suffixing, pdf.js
rasterization, one-iframe print).

- [ ] **Step 1: Write the module**

Create `epms/src/lib/attachmentBundle.ts`:

```ts
/**
 * attachmentBundle — download/zip/print helpers for the PA Document-Chain
 * attachment panel. Pure utility layer: no React, no store access. The caller
 * passes the bearer token. PDFs are rasterized to page images with pdf.js and
 * printed as <img> (never as an embedded PDF) because corporate policy blocks
 * native PDF rendering in iframes — the same reason FilePreviewPanel uses a
 * pdf.js canvas.
 */
import JSZip from 'jszip'
import * as pdfjsLib from 'pdfjs-dist'

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

export interface BundleFile {
  filename: string
  folder: string
  fetchUrl: string
  contentType: string
  printable: boolean
}

const CONCURRENCY = 4

/** Fetch a single file as a blob with the bearer token. Throws on non-2xx. */
export async function fetchAuthedBlob(url: string, token: string | null): Promise<Blob> {
  const res = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) throw new Error(`Fetch failed (${res.status}) for ${url}`)
  return res.blob()
}

/** Trigger a browser download for an already-fetched blob. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/** Map over items with a bounded number of concurrent workers, preserving order. */
async function mapConcurrent<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length)
  let next = 0
  async function worker() {
    while (next < items.length) {
      const i = next++
      results[i] = await fn(items[i], i)
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker))
  return results
}

/** Ensure unique names within a ZIP folder by suffixing " (2)", " (3)", … */
function makeNamer() {
  const seen = new Map<string, number>()
  return (folder: string, filename: string): string => {
    const key = `${folder}/${filename}`
    const n = seen.get(key) ?? 0
    seen.set(key, n + 1)
    if (n === 0) return key
    const dot = filename.lastIndexOf('.')
    const stem = dot > 0 ? filename.slice(0, dot) : filename
    const ext = dot > 0 ? filename.slice(dot) : ''
    return `${folder}/${stem} (${n + 1})${ext}`
  }
}

export interface BulkResult {
  ok: number
  skipped: number
}

/**
 * Download every file, bundle into a single ZIP (folder per document), and save it.
 * Files that fail to fetch are skipped (counted in result.skipped).
 */
export async function downloadAllAsZip(
  files: BundleFile[],
  token: string | null,
  zipName: string,
  onProgress?: (done: number, total: number) => void,
): Promise<BulkResult> {
  const zip = new JSZip()
  const namer = makeNamer()
  let done = 0
  let skipped = 0
  await mapConcurrent(files, CONCURRENCY, async (f) => {
    try {
      const blob = await fetchAuthedBlob(f.fetchUrl, token)
      zip.file(namer(f.folder, f.filename), blob)
    } catch {
      skipped++
    } finally {
      done++
      onProgress?.(done, files.length)
    }
  })
  const blob = await zip.generateAsync({ type: 'blob' })
  saveBlob(blob, zipName)
  return { ok: files.length - skipped, skipped }
}

async function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = () => reject(r.error)
    r.readAsDataURL(blob)
  })
}

/** Rasterize a PDF blob to one JPEG data-URL per page (memory-bounded). */
async function rasterizePdf(blob: Blob): Promise<string[]> {
  const buf = await blob.arrayBuffer()
  const pdf = await pdfjsLib.getDocument({ data: buf }).promise
  const pages: string[] = []
  try {
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n)
      const base = page.getViewport({ scale: 1 })
      // Cap the longest side at ~2000px to balance sharpness against memory.
      const scale = Math.min(2, 2000 / Math.max(base.width, base.height))
      const viewport = page.getViewport({ scale })
      const canvas = document.createElement('canvas')
      canvas.width = Math.ceil(viewport.width)
      canvas.height = Math.ceil(viewport.height)
      const ctx = canvas.getContext('2d')!
      await page.render({ canvas, canvasContext: ctx, viewport }).promise
      pages.push(canvas.toDataURL('image/jpeg', 0.85))
      canvas.width = 0        // release backing store immediately
      canvas.height = 0
      page.cleanup()
    }
  } finally {
    await pdf.destroy()
  }
  return pages
}

/** Convert one bundle file into an ordered list of printable page image data-URLs. */
async function fileToPageImages(f: BundleFile, token: string | null): Promise<string[]> {
  const blob = await fetchAuthedBlob(f.fetchUrl, token)
  if (f.contentType === 'application/pdf') return rasterizePdf(blob)
  if (f.contentType.startsWith('image/')) return [await blobToDataUrl(blob)]
  return [] // non-printable — should be filtered out before here
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string
  ))
}

/**
 * Print one or many files as a SINGLE browser print job. PDFs and images are
 * rasterized to page images and laid out one page per printed sheet. Used for
 * both single-file print (pass one file) and print-all.
 */
export async function printFiles(
  files: BundleFile[],
  token: string | null,
  onProgress?: (done: number, total: number) => void,
): Promise<BulkResult> {
  const printable = files.filter((f) => f.printable)
  const skipped = files.length - printable.length

  const sections: string[] = []
  let done = 0
  for (const f of printable) {
    let pages: string[] = []
    try {
      pages = await fileToPageImages(f, token)
    } catch {
      // treat a fetch/parse failure as skipped
      done++
      onProgress?.(done, printable.length)
      continue
    }
    const caption = escapeHtml(`${f.folder} · ${f.filename}`)
    for (const src of pages) {
      sections.push(
        `<figure class="pg"><img src="${src}"/><figcaption>${caption}</figcaption></figure>`,
      )
    }
    done++
    onProgress?.(done, printable.length)
  }

  if (sections.length === 0) return { ok: 0, skipped }

  const html =
    `<!doctype html><html><head><meta charset="utf-8"><style>` +
    `@page{margin:12mm}` +
    `*{margin:0;padding:0;box-sizing:border-box}` +
    `.pg{break-after:page;text-align:center}` +
    `.pg:last-child{break-after:auto}` +
    `.pg img{max-width:100%;max-height:250mm;object-fit:contain}` +
    `.pg figcaption{font:10px/1.4 sans-serif;color:#666;padding-top:4px}` +
    `</style></head><body>${sections.join('')}</body></html>`

  await printHtmlInIframe(html)
  return { ok: printable.length, skipped }
}

/** Write HTML into a hidden iframe, wait for all images to load, then print. */
async function printHtmlInIframe(html: string): Promise<void> {
  const iframe = document.createElement('iframe')
  iframe.style.position = 'fixed'
  iframe.style.right = '0'
  iframe.style.bottom = '0'
  iframe.style.width = '0'
  iframe.style.height = '0'
  iframe.style.border = '0'
  document.body.appendChild(iframe)

  const doc = iframe.contentDocument!
  doc.open()
  doc.write(html)
  doc.close()

  // Wait for every <img> to finish decoding, else blank pages print.
  const imgs = Array.from(doc.images)
  await Promise.all(
    imgs.map((img) =>
      img.complete
        ? Promise.resolve()
        : new Promise<void>((res) => {
            img.onload = () => res()
            img.onerror = () => res()
          }),
    ),
  )

  iframe.contentWindow!.focus()
  iframe.contentWindow!.print()

  // Remove the iframe after the print dialog has had time to open.
  window.setTimeout(() => document.body.removeChild(iframe), 60_000)
}
```

- [ ] **Step 2: Type-check**

Run: `npm run build`
Expected: PASS. If pdf.js render types complain about the `canvas` prop, confirm the call
matches `FilePreviewPanel.tsx:64` (`page.render({ canvas, canvasContext: ctx, viewport })`) —
it is the same signature.

- [ ] **Step 3: Lint**

Run: `npm run lint`
Expected: clean (no unused vars, no `any` lint errors — the only `any`-ish cast is the
FileReader result which is typed as `string`).

- [ ] **Step 4: Commit**

```bash
git add epms/src/lib/attachmentBundle.ts
git commit -m "feat(epms): attachmentBundle util (authed fetch, zip, pdf.js print)"
```

---

## Task 2: `useChainAttachments.ts` — lineage resolution + attachment fetch

**Files:**
- Create: `epms/src/hooks/useChainAttachments.ts`

Reuses existing single-doc hooks: `usePa`, `usePo`, `usePr`, `useInvoice`, `useGr`. Because
the number of invoices/GRs is dynamic, invoice and GR lists are fetched with `useQueries`.

- [ ] **Step 1: Write the hook**

Create `epms/src/hooks/useChainAttachments.ts`:

```ts
/**
 * useChainAttachments — resolve a PA's DIRECT document lineage (PR, PO, GR, INV,
 * PA) and aggregate every document's attachments into one grouped model.
 *
 * Lineage (see spec §2): PA → its invoices (pa.invoice_ids) → those invoices'
 * GRs (invoice.gr_ids, de-duplicated) → PA's PO (pa.po_id) → PO's PR (po.pr_id).
 * A prepayment PA with no invoices collapses to PA + PO + PR.
 *
 * Attachment sources differ by service:
 *   PR/PO/GR/PA → epms-api  /{type}/{id}/attachments        (+ /{attId}/download)
 *   INV         → expense-api /invoice-attachments?...       (+ /{attId}/file)
 */
import { useMemo } from 'react'
import { useQueries } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth.store'
import { EXPENSE_BASE } from '@/lib/api'
import { usePa } from '@/hooks/usePas'
import { usePo } from '@/hooks/usePos'
import { usePr } from '@/hooks/usePrs'
import { poAttachmentService } from '@/services/poAttachments'
import { prAttachmentService } from '@/services/prAttachments'
import { grAttachmentService } from '@/services/grAttachments'
import { paAttachmentService } from '@/services/paAttachments'
import { invoiceService } from '@/services/invoices'
import { grService } from '@/services/gr'

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1'

export interface ChainAttachment {
  id: string
  filename: string
  contentType: string
  sizeBytes: number
  fetchUrl: string
  printable: boolean
}
export interface ChainDocGroup {
  docType: 'PR' | 'PO' | 'GR' | 'INV' | 'PA'
  docNumber: string
  docId: string
  attachments: ChainAttachment[]
}

function isPrintable(ct: string): boolean {
  return ct === 'application/pdf' || ct.startsWith('image/')
}

/** epms-api attachment meta → ChainAttachment. `seg` is the URL path segment. */
function mapEpmsAtt(
  seg: 'pr' | 'po' | 'gr' | 'pa',
  docId: string,
  a: { id: string; filename: string; content_type: string; file_size: number },
): ChainAttachment {
  return {
    id: a.id,
    filename: a.filename,
    contentType: a.content_type,
    sizeBytes: a.file_size,
    fetchUrl: `${API_BASE}/${seg}/${docId}/attachments/${a.id}/download`,
    printable: isPrintable(a.content_type),
  }
}

export function useChainAttachments(paId: string) {
  const token = useAuthStore((s) => s.token)

  const { data: pa, isLoading: paLoading } = usePa(paId)
  const poId = pa?.po_id ?? ''
  const invoiceIds = useMemo(() => pa?.invoice_ids ?? [], [pa])

  const { data: po, isLoading: poLoading } = usePo(poId)
  const prId = po?.pr_id ?? ''
  const { data: pr, isLoading: prLoading } = usePr(prId)

  // Invoices (each invoice → its detail, so we can read gr_ids + number)
  const invoiceQueries = useQueries({
    queries: invoiceIds.map((id) => ({
      queryKey: ['invoices', id],
      queryFn: () => invoiceService.get(id),
      enabled: !!id,
      staleTime: 30_000,
    })),
  })
  const invoices = invoiceQueries.map((q) => q.data).filter(Boolean) as NonNullable<
    (typeof invoiceQueries)[number]['data']
  >[]

  // GR ids: union of gr_ids across the resolved invoices, de-duplicated.
  const grIds = useMemo(() => {
    const s = new Set<string>()
    for (const inv of invoices) for (const g of inv.gr_ids ?? []) s.add(g)
    return [...s]
  }, [invoices])

  const grQueries = useQueries({
    queries: grIds.map((id) => ({
      queryKey: ['gr', id],
      queryFn: () => grService.get(id),
      enabled: !!id,
      staleTime: 30_000,
    })),
  })
  const grs = grQueries.map((q) => q.data).filter(Boolean) as NonNullable<
    (typeof grQueries)[number]['data']
  >[]

  // ── Attachment-list queries, one per resolved doc ──────────────────────────
  const prAtt = useQueries({
    queries: pr ? [{ queryKey: ['pr-att', pr.id], queryFn: () => prAttachmentService.list(pr.id), staleTime: 30_000 }] : [],
  })
  const poAtt = useQueries({
    queries: po ? [{ queryKey: ['po-att', po.id], queryFn: () => poAttachmentService.list(po.id), staleTime: 30_000 }] : [],
  })
  const paAtt = useQueries({
    queries: pa ? [{ queryKey: ['pa-att', pa.id], queryFn: () => paAttachmentService.list(pa.id), staleTime: 30_000 }] : [],
  })
  const grAtt = useQueries({
    queries: grs.map((g) => ({ queryKey: ['gr-att', g.id], queryFn: () => grAttachmentService.list(g.id), staleTime: 30_000 })),
  })
  const invAtt = useQueries({
    queries: invoices.map((inv) => ({
      queryKey: ['inv-att', inv.id],
      queryFn: async (): Promise<ChainAttachment[]> => {
        const res = await fetch(
          `${EXPENSE_BASE}/api/v1/invoice-attachments?invoice_id=${inv.id}&invoice_source=epms`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} },
        )
        if (!res.ok) return []
        const raw = (await res.json()) as Array<{
          id: string; file_name: string; content_type: string; file_size_bytes: number
        }>
        return raw.map((a) => ({
          id: a.id,
          filename: a.file_name,
          contentType: a.content_type,
          sizeBytes: a.file_size_bytes,
          fetchUrl: `${EXPENSE_BASE}/api/v1/invoice-attachments/${a.id}/file`,
          printable: isPrintable(a.content_type),
        }))
      },
      staleTime: 30_000,
    })),
  })

  // ── Assemble grouped model in paper-trail order: PR, PO, GR(s), INV(s), PA ──
  const groups = useMemo<ChainDocGroup[]>(() => {
    const out: ChainDocGroup[] = []
    if (pr) out.push({ docType: 'PR', docNumber: pr.number, docId: pr.id, attachments: (prAtt[0]?.data ?? []).map((a) => mapEpmsAtt('pr', pr.id, a)) })
    if (po) out.push({ docType: 'PO', docNumber: po.number, docId: po.id, attachments: (poAtt[0]?.data ?? []).map((a) => mapEpmsAtt('po', po.id, a)) })
    grs.forEach((g, i) => out.push({ docType: 'GR', docNumber: g.number, docId: g.id, attachments: (grAtt[i]?.data ?? []).map((a) => mapEpmsAtt('gr', g.id, a)) }))
    invoices.forEach((inv, i) => out.push({ docType: 'INV', docNumber: inv.internal_ref, docId: inv.id, attachments: invAtt[i]?.data ?? [] }))
    if (pa) out.push({ docType: 'PA', docNumber: pa.pa_number, docId: pa.id, attachments: (paAtt[0]?.data ?? []).map((a) => mapEpmsAtt('pa', pa.id, a)) })
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pr, po, pa, grs, invoices, prAtt[0]?.data, poAtt[0]?.data, paAtt[0]?.data,
      grAtt.map((q) => q.data).join(','), invAtt.map((q) => q.data).join(',')])

  const total = useMemo(() => groups.reduce((n, g) => n + g.attachments.length, 0), [groups])

  const isLoading =
    paLoading || poLoading || prLoading ||
    invoiceQueries.some((q) => q.isLoading) || grQueries.some((q) => q.isLoading) ||
    prAtt.some((q) => q.isLoading) || poAtt.some((q) => q.isLoading) ||
    paAtt.some((q) => q.isLoading) || grAtt.some((q) => q.isLoading) ||
    invAtt.some((q) => q.isLoading)

  const error =
    [...prAtt, ...poAtt, ...paAtt, ...grAtt, ...invAtt].some((q) => q.isError)

  return { groups, total, isLoading, error }
}
```

- [ ] **Step 2: Verify the reused service/hook APIs exist as referenced**

Confirm before building (they are used above):
- `invoiceService.get(id)` returns an object with `internal_ref` and `gr_ids?: string[]`
  (`epms/src/services/invoices.ts`). If the single-invoice type omits `gr_ids`, add it to that
  interface (it exists on the model and list type).
- `grService.get(id)` returns an object with `number` (`epms/src/services/gr.ts`).
- `pa.pa_number`, `pa.po_id`, `pa.invoice_ids` exist on the PA type (`epms/src/services/pa.ts`
  lines 44/62/64 — confirmed).
- `po.number`, `po.pr_id`; `pr.number`.

Run: `npm run build`
Expected: PASS. Fix any missing field on the relevant service interface (e.g. add
`gr_ids?: string[]` / `internal_ref` to the single-doc invoice type if absent).

- [ ] **Step 3: Lint**

Run: `npm run lint`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add epms/src/hooks/useChainAttachments.ts epms/src/services/invoices.ts
git commit -m "feat(epms): useChainAttachments — resolve PA lineage + aggregate attachments"
```

---

## Task 3: `ChainAttachmentsPanel.tsx` — drawer, list, single actions

**Files:**
- Create: `epms/src/components/shared/ChainAttachmentsPanel.tsx`

- [ ] **Step 1: Write the panel with grouped list + single download/print**

Create `epms/src/components/shared/ChainAttachmentsPanel.tsx`:

```tsx
/**
 * ChainAttachmentsPanel — right-side slide-over listing every attachment across
 * a PA's document chain (PR/PO/GR/INV/PA), with single + bulk download/print.
 */
import { useState } from 'react'
import {
  X, Download, Printer, FileText, Package, Warehouse, Receipt, CreditCard, Loader2,
} from 'lucide-react'
import { useAuthStore } from '@/stores/auth.store'
import { useChainAttachments, type ChainDocGroup, type ChainAttachment } from '@/hooks/useChainAttachments'
import {
  fetchAuthedBlob, saveBlob, downloadAllAsZip, printFiles, type BundleFile,
} from '@/lib/attachmentBundle'
import { formatBytes } from '@/lib/utils'

const DOC_ICON: Record<ChainDocGroup['docType'], React.ReactNode> = {
  PR: <FileText className="h-3.5 w-3.5" />,
  PO: <Package className="h-3.5 w-3.5" />,
  GR: <Warehouse className="h-3.5 w-3.5" />,
  INV: <Receipt className="h-3.5 w-3.5" />,
  PA: <CreditCard className="h-3.5 w-3.5" />,
}

function toBundle(g: ChainDocGroup, a: ChainAttachment): BundleFile {
  return {
    filename: a.filename,
    folder: `${g.docType}-${g.docNumber}`,
    fetchUrl: a.fetchUrl,
    contentType: a.contentType,
    printable: a.printable,
  }
}

export function ChainAttachmentsPanel({
  paId, paNumber, onClose,
}: {
  paId: string
  paNumber: string
  onClose: () => void
}) {
  const token = useAuthStore((s) => s.token)
  const { groups, total, isLoading, error } = useChainAttachments(paId)
  const [busy, setBusy] = useState<string | null>(null) // progress label or null
  const [rowBusy, setRowBusy] = useState<string | null>(null) // attachment id

  const allFiles: BundleFile[] = groups.flatMap((g) => g.attachments.map((a) => toBundle(g, a)))

  const handleSingleDownload = async (g: ChainDocGroup, a: ChainAttachment) => {
    setRowBusy(a.id)
    try {
      const blob = await fetchAuthedBlob(a.fetchUrl, token)
      saveBlob(blob, a.filename)
    } catch { /* surfaced via alert below */ alert(`Download failed: ${a.filename}`) }
    finally { setRowBusy(null) }
  }

  const handleSinglePrint = async (g: ChainDocGroup, a: ChainAttachment) => {
    setRowBusy(a.id)
    try {
      const r = await printFiles([toBundle(g, a)], token)
      if (r.ok === 0) alert('Nothing printable in this file.')
    } catch { alert(`Print failed: ${a.filename}`) }
    finally { setRowBusy(null) }
  }

  const handleDownloadAll = async () => {
    setBusy('Zipping…')
    try {
      const r = await downloadAllAsZip(
        allFiles, token, `PA-${paNumber}-attachments.zip`,
        (done, t) => setBusy(`Zipping ${done}/${t}…`),
      )
      if (r.skipped) alert(`Downloaded ${r.ok} file(s). Skipped ${r.skipped} that failed.`)
    } catch { alert('Download-all failed.') }
    finally { setBusy(null) }
  }

  const handlePrintAll = async () => {
    setBusy('Preparing print…')
    try {
      const r = await printFiles(
        allFiles, token,
        (done, t) => setBusy(`Preparing print ${done}/${t}…`),
      )
      if (r.ok === 0) alert('No printable files in this chain.')
      else if (r.skipped) alert(`Printing ${r.ok} file(s). Skipped ${r.skipped} non-printable.`)
    } catch { alert('Print-all failed.') }
    finally { setBusy(null) }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* backdrop */}
      <div className="absolute inset-0 bg-black/30" onClick={busy ? undefined : onClose} />
      {/* drawer */}
      <div className="relative flex h-full w-full max-w-md flex-col bg-white shadow-xl">
        {/* header */}
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <h2 className="text-sm font-semibold text-neutral-700">
            Chain Attachments{total > 0 && ` (${total})`}
          </h2>
          <button onClick={onClose} disabled={!!busy}
            className="rounded p-1 text-neutral-400 hover:bg-neutral-100 disabled:opacity-40">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* bulk action bar */}
        <div className="flex items-center gap-2 border-b border-neutral-100 bg-neutral-50 px-4 py-2">
          <button onClick={handleDownloadAll} disabled={total === 0 || !!busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 bg-white px-2.5 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-40">
            <Download className="h-3.5 w-3.5" /> Download all (ZIP)
          </button>
          <button onClick={handlePrintAll} disabled={total === 0 || !!busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-2.5 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100 disabled:opacity-40">
            <Printer className="h-3.5 w-3.5" /> Print all
          </button>
          {busy && (
            <span className="ml-auto inline-flex items-center gap-1 text-xs text-neutral-500">
              <Loader2 className="h-3 w-3 animate-spin" />{busy}
            </span>
          )}
        </div>

        {/* body */}
        <div className="flex-1 overflow-auto px-4 py-3">
          {isLoading ? (
            <p className="flex items-center gap-2 text-xs text-neutral-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading attachments…
            </p>
          ) : error ? (
            <p className="text-xs text-danger-600">Some attachments could not be loaded.</p>
          ) : total === 0 ? (
            <p className="text-xs text-neutral-400 italic">No attachments found in this document chain.</p>
          ) : (
            <div className="space-y-4">
              {groups.filter((g) => g.attachments.length > 0).map((g) => (
                <div key={`${g.docType}-${g.docId}`}>
                  <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-neutral-500">
                    <span className="text-neutral-400">{DOC_ICON[g.docType]}</span>
                    {g.docNumber}
                    <span className="font-normal text-neutral-400">· {g.attachments.length} file{g.attachments.length > 1 ? 's' : ''}</span>
                  </div>
                  <ul className="space-y-1">
                    {g.attachments.map((a) => (
                      <li key={a.id}
                        className="flex items-center gap-2 rounded-lg border border-neutral-200 px-2.5 py-2 text-xs">
                        <FileText className="h-4 w-4 shrink-0 text-neutral-400" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium text-neutral-700">{a.filename}</p>
                          <p className="text-[10px] text-neutral-400">{formatBytes(a.sizeBytes)}</p>
                        </div>
                        {rowBusy === a.id && <Loader2 className="h-3.5 w-3.5 animate-spin text-neutral-400" />}
                        <button title="Print" onClick={() => handleSinglePrint(g, a)}
                          disabled={!a.printable || !!rowBusy}
                          className="rounded p-1 text-neutral-500 hover:bg-neutral-100 disabled:opacity-30"
                          {...(!a.printable ? { title: 'Preview/print not supported — download instead' } : {})}>
                          <Printer className="h-3.5 w-3.5" />
                        </button>
                        <button title="Download" onClick={() => handleSingleDownload(g, a)}
                          disabled={!!rowBusy}
                          className="rounded p-1 text-primary-600 hover:bg-primary-50 disabled:opacity-30">
                          <Download className="h-3.5 w-3.5" />
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Ensure `formatBytes` exists in `@/lib/utils`**

Check `epms/src/lib/utils.ts` for a byte formatter. If none exists, add:

```ts
export function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / (1024 * 1024)).toFixed(1)} MB`
}
```

(If a differently-named helper already exists, use it and drop the import above.)

- [ ] **Step 3: Type-check + lint**

Run: `npm run build && npm run lint`
Expected: PASS. Note the duplicate `title` attribute on the print button is intentional-looking
but invalid — remove the leading `title="Print"` and keep only the conditional tooltip logic:
render `title={a.printable ? 'Print' : 'Preview/print not supported — download instead'}` as a
single attribute instead of the spread. Fix before committing.

- [ ] **Step 4: Commit**

```bash
git add epms/src/components/shared/ChainAttachmentsPanel.tsx epms/src/lib/utils.ts
git commit -m "feat(epms): ChainAttachmentsPanel — grouped list + single/bulk download & print"
```

---

## Task 4: Wire the trigger button into the Document Chain header

**Files:**
- Modify: `epms/src/components/shared/DocumentChainTree.tsx`

- [ ] **Step 1: Add imports and panel state**

At the top of `DocumentChainTree.tsx`, add to the existing imports:

```tsx
import { useState } from 'react'
import { Paperclip } from 'lucide-react'
import { ChainAttachmentsPanel } from '@/components/shared/ChainAttachmentsPanel'
```

Inside `DocumentChainTree`, after the existing data hooks and before `return (`, add:

```tsx
  const [showAttachments, setShowAttachments] = useState(false)
  const isPa = currentType === 'pa'
```

- [ ] **Step 2: Replace the header `<h3>` with a flex row containing the button**

Find (around line 304):

```tsx
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500 mb-3">
        Document Chain
      </h3>
```

Replace with:

```tsx
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Document Chain
        </h3>
        {isPa && currentPa && (
          <button
            type="button"
            onClick={() => setShowAttachments(true)}
            className="inline-flex items-center gap-1 rounded-md border border-neutral-200 px-2 py-1 text-[11px] font-medium text-neutral-600 hover:border-primary-300 hover:bg-primary-50 hover:text-primary-700 transition-colors"
          >
            <Paperclip className="h-3 w-3" /> Attachments
          </button>
        )}
      </div>

      {isPa && currentPa && showAttachments && (
        <ChainAttachmentsPanel
          paId={currentPa.id}
          paNumber={currentPa.pa_number}
          onClose={() => setShowAttachments(false)}
        />
      )}
```

(`currentPa` is already fetched at `DocumentChainTree.tsx:247`.)

- [ ] **Step 3: Type-check + lint**

Run: `npm run build && npm run lint`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add epms/src/components/shared/DocumentChainTree.tsx
git commit -m "feat(epms): add Attachments button to PA Document Chain header"
```

---

## Task 5: Manual QA + release prep

**Files:** none (verification only)

- [ ] **Step 1: Full build + lint gate**

Run: `npm run build && npm run lint`
Expected: both PASS with zero errors.

- [ ] **Step 2: Run the dev server against a PA with a rich chain**

Run: `npm run dev` and open a PA detail page that has a PR, PO, GR(s), invoice(s) with
attachments (use a PA created from an OCR invoice so the invoice has its source PDF).

Verify each:
- Button shows in the Document Chain header with a count.
- Panel lists groups in order PR → PO → GR → INV → PA, only non-empty groups shown.
- Single **download** saves the correct file.
- Single **print** opens the OS print dialog showing that file's page(s) (PDF rasterized, image direct).
- Non-printable file (upload a `.docx` to one doc): its print button is disabled with tooltip.
- **Download all (ZIP)** produces `PA-<number>-attachments.zip` with folder-per-doc structure and no name collisions.
- **Print all** opens ONE print dialog with every printable page, correct page breaks, and a
  skipped-count alert if a `.docx` was present.
- **Prepayment PA** (no invoice): panel shows PA + PO + PR only, still works.
- **Empty chain** (a PA whose chain has zero attachments): empty-state text, both bulk buttons disabled.

- [ ] **Step 3: Confirm no cross-service auth/URL issues**

In DevTools Network tab, confirm invoice attachment requests hit `EXPENSE_BASE` with an
`Authorization: Bearer` header and epms attachment requests hit `VITE_API_URL`. (Memory:
"Dockerfile 漏 build arg" — for the production image build, ensure `VITE_EXPENSE_API_URL` and
`VITE_API_URL` build args are injected, else invoice fetches fall back to `localhost:8006` and
hang "pending". This feature adds no new env var but relies on both existing ones.)

- [ ] **Step 4: Finish the branch**

Use superpowers:finishing-a-development-branch to choose merge/PR. Release note for the user:
this touches **only the epms frontend** — one image to rebuild (`epms` frontend) at the single
merge point; no migration, no backend deploy. New runtime dependency: `jszip`.

---

## Self-Review (completed)

**Spec coverage:**
- §1 button in Document Chain header → Task 4. ✓
- §2 direct-lineage resolution incl. invoice-linked GR dedup → Task 2. ✓
- §3 frontend-only, two attachment sources, new files, jszip → Tasks 0–4. ✓
- §4 normalized grouped model → Task 2. ✓
- §5 slide-over UI, bulk bar, grouped list, disabled non-printable, empty state → Task 3. ✓
- §6 single download/print (pdf.js images) → Tasks 1+3. ✓
- §7 ZIP (folder-per-doc, collision suffix, skip-on-fail) → Task 1. ✓
- §8 print-all one job, memory bounding, image-not-PDF, wait-for-load → Task 1. ✓
- §9 edge cases (prepayment, per-file fail, empty, dup names) → Tasks 1+3, QA in Task 5. ✓
- §10 no new authz → inherent (reuses endpoints). ✓
- §11 build+lint+manual QA → verification steps throughout + Task 5. ✓
- §12 release (epms only, jszip, no migration) → Task 5 Step 4. ✓

**Placeholder scan:** No TBD/TODO; every code step is complete. The one deliberate "fix before
commit" note (Task 3 Step 3, duplicate `title` attr) is an explicit correction with the exact
replacement given, not a placeholder.

**Type consistency:** `BundleFile` (attachmentBundle) and `ChainAttachment`/`ChainDocGroup`
(useChainAttachments) are defined once and imported; `toBundle` bridges them in the panel.
`printFiles`/`downloadAllAsZip`/`fetchAuthedBlob`/`saveBlob` signatures match their call sites.
