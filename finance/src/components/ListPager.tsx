/**
 * The Prev / "Page x / y" / Next footer the finance server-paged lists use
 * (same markup as AccountsPayablePage's invoice list). `page` is 1-based.
 */
export function ListPager({ page, pageSize, total, noun, onPage, className }: {
  page: number; pageSize: number; total: number
  /** Plural noun for the count, e.g. "invoices". */
  noun: string
  onPage: (page: number) => void
  className?: string
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const btn = 'rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-sm text-fg-secondary hover:bg-surface-subtle disabled:opacity-50'
  return (
    <div className={className ?? 'mt-3 flex items-center justify-between text-sm text-fg-muted'}>
      <span>{total.toLocaleString()} {noun}</span>
      {pageCount > 1 && (
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1}
                  className={btn}>Prev</button>
          <span>Page {page} / {pageCount}</span>
          <button type="button" onClick={() => onPage(Math.min(pageCount, page + 1))}
                  disabled={page >= pageCount} className={btn}>Next</button>
        </div>
      )}
    </div>
  )
}
