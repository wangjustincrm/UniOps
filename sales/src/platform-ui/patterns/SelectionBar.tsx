import { useMemo, useState, type ReactNode } from 'react'
import { DropdownMenu } from '../primitives/DropdownMenu'
import { Button } from '../primitives/shell'
import { ActionButton } from './ActionButton'
import { RecordActionDialog, type ActionTarget } from './RecordActionDialog'
import { fits, type ActionHost, type RecordAction } from './recordActions'

const SHOWN_ACTIONS = 3  // the rest in More
type Row = { id: number; version: number }

/** "12 selected": the group actions with how many of the selection each fits (the server decides on Confirm). */
export function SelectionBar<T extends Row>({ count, actions, rows, host, target, onClear, selectAll }: {
  count: number; actions: RecordAction<T>[]; rows: T[]; host: ActionHost<T>; target: ActionTarget<T>
  onClear: () => void; selectAll?: { total: number; run: () => void }
}) {
  const [running, setRunning] = useState<RecordAction<T> | null>(null)
  const counts = useMemo(() => new Map(actions.map((a) => [a.id, rows.filter((r) => fits(a, r) === true).length])), [actions, rows])
  const all = 'query' in target
  const button = (a: RecordAction<T>): ReactNode => {
    const n = counts.get(a.id) ?? 0
    const why = !all && n === 0 ? (rows.map((r) => fits(a, r)).find((x) => x !== true) as string | undefined) ?? 'It fits none of them' : undefined
    return (
      <ActionButton key={a.id} size="sm" variant={a.tone === 'destructive' ? 'secondary' : undefined} permission={a.permission}
        disabled={!!why} disabledReason={why} onAction={() => setRunning(a)}>
        {a.label}{all ? '' : ` (${n} of ${count})`}
      </ActionButton>
    )
  }
  return (
    <div role="toolbar" aria-label="Selected records"
      className="flex w-full flex-wrap items-center gap-2 max-sm:fixed max-sm:inset-x-0 max-sm:bottom-0 max-sm:z-30 max-sm:border-t max-sm:border-neutral-200 max-sm:bg-white max-sm:p-3 max-sm:shadow-lg"
      onKeyDown={(e) => { if (e.key === 'Escape') onClear() }}>
      <span className="text-sm font-medium text-neutral-900">{all ? `All ${count} matching` : `${count} selected`}</span>
      {selectAll && <Button size="sm" variant="ghost" onClick={selectAll.run}>Select all {selectAll.total} matching</Button>}
      <Button size="sm" variant="ghost" onClick={onClear}>Clear</Button>
      <span className="flex flex-wrap items-center gap-2 sm:ml-auto">
        {actions.slice(0, SHOWN_ACTIONS).map(button)}
        {actions.length > SHOWN_ACTIONS && (
          <DropdownMenu label="More actions" align="end" trigger={<Button size="sm" variant="secondary">More</Button>}
            items={actions.slice(SHOWN_ACTIONS).map((a) => ({ label: a.label, opensDialog: true, onSelect: () => setRunning(a) }))} />
        )}
      </span>
      {running && (
        <RecordActionDialog action={running} target={target} host={host} open
          onOpenChange={(v) => { if (!v) setRunning(null) }} onDone={onClear} />
      )}
    </div>
  )
}
