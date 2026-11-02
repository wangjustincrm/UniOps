import type { ReactNode } from 'react'

export type DetailItem = [label: string, value: ReactNode]

/** Label / value pairs of one record (a detail sheet). An empty value shows "—". */
export function DetailList({ items, className }: { items: DetailItem[]; className?: string }) {
  return (
    <dl className={`grid grid-cols-[10rem_1fr] gap-x-3 gap-y-1 text-sm ${className ?? ''}`}>
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-neutral-500">{k}</dt>
          <dd className="break-words">{v === null || v === undefined || v === '' ? '—' : v}</dd>
        </div>
      ))}
    </dl>
  )
}
