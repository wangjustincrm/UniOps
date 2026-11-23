import { useState } from 'react'
import { MoreHorizontal } from 'lucide-react'
import { DropdownMenu } from '../primitives/DropdownMenu'
import { ActionButton } from './ActionButton'
import { RecordActionDialog } from './RecordActionDialog'
import { fits, useActionHost, useActionsFor, type ActionHost, type RecordAction } from './recordActions'

/** The open action and its record (one dialog per menu or sheet). */
function useOpenAction<T>() {
  const [open, setOpen] = useState<{ action: RecordAction<T>; rows: T[] } | null>(null)
  return { open, start: (action: RecordAction<T>, rows: T[]) => setOpen({ action, rows }), close: () => setOpen(null) }
}

/** A row's "⋯" menu: the actions placed on 'row' this person holds; one that does not fit the record is greyed with
 *  why. Nothing (no column content) when the person holds none. */
export function RowActionMenu<T extends { id: number; version: number }>({ row, actions }: { row: T; actions: RecordAction<T>[] }) {
  const mine = useActionsFor(actions, 'row')
  const host = useActionHost<T>()
  const { open, start, close } = useOpenAction<T>()
  if (!mine.length || !host) return null
  return (
    <>
      <DropdownMenu label={`Actions for ${host.name(row)}`} align="end"
        items={mine.map((a) => {
          const ok = fits(a, row)
          return { label: a.label, opensDialog: true, disabled: ok !== true, disabledReason: ok === true ? undefined : ok,
            onSelect: () => start(a, [row]) }
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

/** A record sheet's buttons for the actions placed on 'sheet' (beside its own): one that does not fit is greyed
 *  with why. `host`: how this record is run and named (the sheet's list gives one; a sheet on its own passes it). */
export function RecordActionButtons<T extends { id: number; version: number }>({ record, actions, host: own }: {
  record: T; actions: RecordAction<T>[]; host?: ActionHost<T>
}) {
  const mine = useActionsFor(actions, 'sheet')
  const fromList = useActionHost<T>()
  const host = own ?? fromList
  const { open, start, close } = useOpenAction<T>()
  if (!mine.length || !host) return null
  return (
    <>
      {mine.map((a) => {
        const ok = fits(a, record)
        return (
          <ActionButton key={a.id} size="sm" variant="secondary" permission={a.permission} disabled={ok !== true}
            disabledReason={ok === true ? undefined : ok} onAction={() => start(a, [record])}>{a.label}</ActionButton>
        )
      })}
      {open && <RecordActionDialog action={open.action} target={{ rows: open.rows }} host={host} open onOpenChange={(v) => { if (!v) close() }} />}
    </>
  )
}
