import { useContext, useState } from 'react'
import { MoreHorizontal } from 'lucide-react'
import { DropdownMenu } from '../primitives/DropdownMenu'
import { Button } from '../primitives/shell'
import { useMediaQuery, BREAKPOINTS } from '../primitives/useResponsive'
import { cn } from '../lib/cn'
import { arrange, type BarItem } from './actionBar'
import { PermissionContext } from './permissions'
import { ReadOnlyContext } from './recordNav'
import { useAction } from './useAction'

const PHONE = `(max-width: ${BREAKPOINTS.sm - 0.02}px)`

/** THE action bar of a record (actionBar.ts): at most two buttons — the most important actions that fit now —
 *  and "⋯" with the rest (the record's own, a line, the built-in ones); on a phone everything in "⋯". Each runs
 *  as an action: its failure, and its `done`, are said in the status row of the sheet or page it is in. */
export function RecordActionBar({ items, label = 'Record actions', className }: { items: BarItem[]; label?: string; className?: string }) {
  const can = useContext(PermissionContext)
  const readOnly = useContext(ReadOnlyContext) !== null
  const phone = useMediaQuery(PHONE)
  const action = useAction()
  const [running, setRunning] = useState<string | null>(null)
  const { buttons, menu } = arrange(items, { can, phone, readOnly })
  if (!buttons.length && !menu.length) return null
  const run = (i: BarItem) => {
    setRunning(i.id)
    void action.run(i.onSelect, { done: i.done, errorMessages: i.errorMessages, failPrefix: i.failPrefix, invalidates: i.invalidates,
      after: i.after, refreshes: i.refreshes }).finally(() => setRunning(null))
  }
  return (
    <div role="toolbar" aria-label={label} className={cn('flex shrink-0 items-center gap-1.5', className)}>
      {buttons.map((i, n) => (
        <Button key={i.id} type="button" size="sm" variant={n === 0 ? undefined : 'secondary'}
          title={i.sends ? `Sends ${i.sends}` : i.hint} aria-busy={running === i.id || undefined} aria-disabled={running === i.id || undefined}
          className={cn(running === i.id && 'cursor-wait opacity-70')}
          onClick={() => { if (running !== i.id) run(i) }}>{i.label}</Button>
      ))}
      {menu.length > 0 && (
        <DropdownMenu label={buttons.length ? 'More actions' : label} align="end"
          items={menu.map((i) => ({ label: i.label, hint: i.sends ? `Sends ${i.sends}` : i.hint, tone: i.tone === 'destructive' ? 'destructive' : undefined,
            separated: i.separated, disabled: !!i.disabledReason, disabledReason: i.disabledReason, opensDialog: i.opensDialog,
            onSelect: () => run(i) }))}
          trigger={
            <Button type="button" size="sm" variant="secondary" aria-label="More actions" aria-busy={(running !== null && !buttons.some((b) => b.id === running)) || undefined}>
              <MoreHorizontal aria-hidden className="h-4 w-4" />
            </Button>
          } />
      )}
    </div>
  )
}
