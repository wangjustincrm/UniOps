import { Button } from '@uniops/shell'

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
  return (
    <div className={className ?? 'mt-3 flex items-center justify-between text-sm text-fg-muted'}>
      <span>{total.toLocaleString()} {noun}</span>
      {pageCount > 1 && (
        <div className="flex items-center gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1}>Prev</Button>
          <span>Page {page} / {pageCount}</span>
          <Button type="button" variant="secondary" size="sm" onClick={() => onPage(Math.min(pageCount, page + 1))}
                  disabled={page >= pageCount}>Next</Button>
        </div>
      )}
    </div>
  )
}
