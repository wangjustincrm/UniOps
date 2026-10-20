// Small pieces the three NC tabs share: error box, loading rows, pager,
// material cell. (Sortable headers: SortTh from @uniops/shell.)
import { AlertTriangle, ChevronLeft, ChevronRight } from 'lucide-react'
import { Skeleton } from '@uniops/shell'
import { ApiError } from '@/lib/api'

export function ErrorBox({ error, fallback }: { error: unknown; fallback: string }) {
  // An empty table and a failed request must never look the same.
  const message = error instanceof ApiError || error instanceof Error ? error.message : fallback
  return (
    <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-700">
      <AlertTriangle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      {message || fallback}
    </p>
  )
}

export function LoadingRows({ cols, rows = 6 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="border-t border-neutral-100">
          {Array.from({ length: cols }).map((__, c) => (
            <td key={c} className="px-3 py-2.5">
              <Skeleton className={c === 0 ? 'w-32' : 'w-16'} />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}

export function EmptyRow({ cols, children }: { cols: number; children: React.ReactNode }) {
  return (
    <tr>
      <td colSpan={cols} className="px-3 py-8 text-center text-sm text-neutral-400">{children}</td>
    </tr>
  )
}

export function Pager({ page, pageSize, total, busy, onPage }: {
  page: number
  pageSize: number
  total: number
  busy?: boolean
  onPage: (p: number) => void
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1
  const to = Math.min(page * pageSize, total)
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[11px] text-neutral-500">
        {from.toLocaleString('en-US')}–{to.toLocaleString('en-US')} of {total.toLocaleString('en-US')}
      </span>
      <div className="flex items-center gap-1">
        <button type="button" onClick={() => onPage(Math.max(1, page - 1))}
          disabled={page <= 1 || busy} aria-label="Previous page"
          className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
          <ChevronLeft className="h-4 w-4" />
        </button>
        <span className="text-[11px] text-neutral-500">Page {page} of {pageCount}</span>
        <button type="button" onClick={() => onPage(Math.min(pageCount, page + 1))}
          disabled={page >= pageCount || busy} aria-label="Next page"
          className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40">
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}

export function MaterialCell({ code, name, missingLabel = '(material not in NC mirror)' }: {
  code: string | null
  name: string | null
  missingLabel?: string
}) {
  if (!code) return <span className="text-xs italic text-neutral-400">{name ?? missingLabel}</span>
  return (
    <span className="min-w-0">
      <span className="font-mono text-xs">{code}</span>
      {name && <span className="ml-1.5 text-xs text-neutral-500">{name}</span>}
    </span>
  )
}

export function Uom({ uom }: { uom: string | null }) {
  if (!uom) return null
  return <span className="ml-1 font-sans text-xs text-neutral-400">{uom}</span>
}

export const thCls = 'bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500'
export const tableWrapCls = 'overflow-x-auto rounded-lg border border-neutral-200 bg-white'
