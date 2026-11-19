import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { cn } from '../lib/cn'

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100]

interface PaginationProps {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
}

export function Pagination({ page, pageSize, total, onPageChange, onPageSizeChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1
  const to = Math.min(page * pageSize, total)

  // Build page number array: always show first, last, current ± 1, with ellipsis
  const pages: (number | '...')[] = []
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pages.push(i)
  } else {
    pages.push(1)
    if (page > 3) pages.push('...')
    for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) pages.push(i)
    if (page < totalPages - 2) pages.push('...')
    pages.push(totalPages)
  }

  return (
    <div className="flex flex-col sm:flex-row items-center justify-between gap-3 border-t border-line px-4 py-3">
      {/* Left: count + per-page selector */}
      <div className="flex items-center gap-3 text-sm text-fg-muted">
        <span>
          {total === 0 ? 'No results' : `${from}–${to} of ${total}`}
        </span>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-fg-muted">Per page:</span>
          <select
            value={pageSize}
            onChange={(e) => { onPageSizeChange(Number(e.target.value)); onPageChange(1) }}
            className="h-7 rounded border border-line bg-surface px-1.5 text-xs text-fg-secondary focus:outline-none focus:ring-1 focus:ring-accent"
          >
            {PAGE_SIZE_OPTIONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Right: page controls */}
      <div className="flex items-center gap-1">
        <PageBtn onClick={() => onPageChange(1)} disabled={page === 1} title="First page">
          <ChevronsLeft className="h-3.5 w-3.5" />
        </PageBtn>
        <PageBtn onClick={() => onPageChange(page - 1)} disabled={page === 1} title="Previous page">
          <ChevronLeft className="h-3.5 w-3.5" />
        </PageBtn>

        {pages.map((p, i) =>
          p === '...'
            ? <span key={`ellipsis-${i}`} className="px-1 text-xs text-fg-muted">…</span>
            : (
              <button
                key={p}
                onClick={() => onPageChange(p)}
                className={cn(
                  'flex h-7 w-7 items-center justify-center rounded text-xs font-medium transition-colors',
                  p === page
                    ? 'bg-accent text-fg-on-accent'
                    : 'text-fg-secondary hover:bg-surface-sunken',
                )}
              >
                {p}
              </button>
            )
        )}

        <PageBtn onClick={() => onPageChange(page + 1)} disabled={page >= totalPages} title="Next page">
          <ChevronRight className="h-3.5 w-3.5" />
        </PageBtn>
        <PageBtn onClick={() => onPageChange(totalPages)} disabled={page >= totalPages} title="Last page">
          <ChevronsRight className="h-3.5 w-3.5" />
        </PageBtn>
      </div>
    </div>
  )
}

function PageBtn({ onClick, disabled, title, children }: {
  onClick: () => void
  disabled: boolean
  title: string
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="flex h-7 w-7 items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-30"
    >
      {children}
    </button>
  )
}
