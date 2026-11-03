import { useState, type MouseEvent, type ReactNode } from 'react'
import { IconButton } from '../primitives/IconButton'
import { ConfirmAction } from './ConfirmAction'
import { useCan } from './permissions'

export interface RowAction {
  key: string
  /** The tooltip and the accessible name. */
  label: string
  icon: ReactNode
  /** Shown only to users who hold it. */
  permission?: string
  hidden?: boolean
  disabled?: boolean
  /** The icon spins and is blocked until the returned promise settles. */
  onAction: () => unknown | Promise<unknown>
  /** Asked first when given — or decided at the click (e.g. a resume outside the active hours, which depends on the time). */
  confirm?: ConfirmSpec | (() => ConfirmSpec | null) | null
}

export interface ConfirmSpec { title: string; description?: string; confirmLabel?: string }

function One({ a }: { a: RowAction }) {
  const can = useCan(a.permission)
  const [pending, setPending] = useState(false)
  const [asking, setAsking] = useState<ConfirmSpec | null>(null)
  if (a.hidden || (a.permission && !can)) return null
  const run = async () => {
    setPending(true)
    try { await a.onAction() } finally { setPending(false) }
  }
  const click = (e: MouseEvent) => {
    e.stopPropagation()  // an action never opens the row
    const ask = typeof a.confirm === 'function' ? a.confirm() : a.confirm
    if (ask) setAsking(ask)
    else void run().catch(() => {})
  }
  return (
    <>
      <IconButton label={a.label} icon={a.icon} onClick={click} disabled={a.disabled} pending={pending} />
      {asking && (
        <ConfirmAction open onOpenChange={(o) => { if (!o) setAsking(null) }} title={asking.title} description={asking.description}
          confirmLabel={asking.confirmLabel ?? a.label} onConfirm={run} />
      )}
    </>
  )
}

/** A row's actions as compact icon buttons (ListPage `rowActions`, a
 *  LocalTable cell): each shown only with its permission, spinning while it
 *  runs, and never opening the row. */
export function RowActions({ actions }: { actions: RowAction[] }) {
  return (
    <div className="flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
      {actions.map((a) => <One key={a.key} a={a} />)}
    </div>
  )
}
