import type { ReactNode } from 'react'

export type DetailItem = [label: string, value: ReactNode]

/** Label / value pairs of one record (a detail sheet). An empty value shows "—". Follows its own
 *  box's width: the label above its value when very narrow, a narrower label column on a phone. */
export function DetailList({ items, className }: { items: DetailItem[]; className?: string }) {
  return (
    <div data-responsive className="@container min-w-0">
    <dl className={`grid grid-cols-1 gap-x-3 gap-y-1 text-sm @xs:grid-cols-[minmax(6rem,40%)_minmax(0,1fr)] @md:grid-cols-[10rem_minmax(0,1fr)] ${className ?? ''}`}>
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="min-w-0 text-neutral-500 wrap-anywhere">{k}</dt>
          <dd className="min-w-0 break-words @max-xs:mb-1.5">{v === null || v === undefined || v === '' ? '—' : v}</dd>
        </div>
      ))}
    </dl>
    </div>
  )
}
