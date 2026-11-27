import type { ReactNode } from 'react'
import type { BarItem } from './actionBar'
import { RecordActionDialog } from './RecordActionDialog'
import { fits, useActionHost, useActionsFor, type ActionHost, type RecordAction } from './recordActions'
import { useOpenAction } from './useOpenAction'

/** A record's declared actions placed on 'header', as action-bar items (RecordActionBar), and their dialog. `host`:
 *  how this record is run and named (the sheet's list gives one; a record's own page passes it). */
export function useRecordActionItems<T extends { id: number; version: number }>(record: T | undefined, actions: RecordAction<T>[], own?: ActionHost<T>): {
  items: BarItem[]; dialog: ReactNode
} {
  const mine = useActionsFor(actions, 'header')
  const fromList = useActionHost<T>()
  const host = own ?? fromList
  const { open, start, close } = useOpenAction<T>()
  if (!record || !host) return { items: [], dialog: null }
  const items = mine.map((a): BarItem => ({
    id: `action:${a.id}`, label: a.label, primary: a.primary, order: a.order, tone: a.tone, permission: a.permission,
    fit: fits(a, record), whenNotFit: a.whenNotFit, sends: host.sends?.(a.id), opensDialog: true, refreshes: false,
    onSelect: () => start(a, [record]),
  }))
  const dialog = open && <RecordActionDialog action={open.action} target={{ rows: open.rows }} host={host} open onOpenChange={(v) => { if (!v) close() }} />
  return { items, dialog }
}
