import { MoreHorizontal } from 'lucide-react'
import { DropdownMenu } from '../primitives/DropdownMenu'
import { RecordActionDialog } from './RecordActionDialog'
import { fits, showsFor, useActionHost, useActionsFor, type RecordAction } from './recordActions'
import { useOpenAction } from './useOpenAction'

/** A row's "⋯" menu: the actions placed on 'row' this person holds; one that does not fit the record is greyed with
 *  why (or not shown, as it declares). Nothing (no column content) when the person holds none. */
export function RowActionMenu<T extends { id: number; version: number }>({ row, actions }: { row: T; actions: RecordAction<T>[] }) {
  const mine = useActionsFor(actions, 'row').filter((a) => showsFor(a, row))
  const host = useActionHost<T>()
  const { open, start, close } = useOpenAction<T>()
  if (!mine.length || !host) return null
  return (
    <>
      <DropdownMenu label={`Actions for ${host.name(row)}`} align="end"
        items={mine.map((a) => {
          const ok = fits(a, row)
          return { label: a.label, opensDialog: true, disabled: ok !== true, disabledReason: ok === true ? undefined : ok,
            tone: a.tone === 'destructive' ? 'destructive' as const : undefined, onSelect: () => start(a, [row]) }
        })}
        trigger={
          <button type="button" aria-label={`Actions for ${host.name(row)}`}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
            <MoreHorizontal aria-hidden className="h-4 w-4" />
          </button>
        } />
      {open && <RecordActionDialog action={open.action} target={{ rows: open.rows }} host={host} open onOpenChange={(v) => { if (!v) close() }} />}
    </>
  )
}
