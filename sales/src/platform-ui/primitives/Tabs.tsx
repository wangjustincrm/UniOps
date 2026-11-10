import * as RTabs from '@radix-ui/react-tabs'
import { useRef, type ReactNode } from 'react'
import { cn } from '../lib/cn'

export interface TabItem {
  value: string
  label: ReactNode
  content: ReactNode
  disabled?: boolean
  /** A count beside the label (e.g. how many documents). */
  count?: number
  /** A dot beside the label: something in the tab needs attention. */
  attention?: 'warning' | 'danger'
}

const DOT = { warning: 'bg-warning-500', danger: 'bg-danger-600' } as const

/** In-page tabs (sections of one record). Module-level navigation stays with
 *  the shell's tab host. Only the selected tab's content is rendered (lazy);
 *  ←/→, Home and End move between tabs (Radix). */
export function Tabs({
  items, value, defaultValue, onValueChange, className, listClassName, ariaLabel, canLeave,
}: {
  items: TabItem[]
  value?: string
  defaultValue?: string
  onValueChange?: (v: string) => void
  className?: string
  /** E.g. sticky under a sheet's header. */
  listClassName?: string
  ariaLabel?: string
  /** Asked before the shown tab is left (e.g. unsaved input in it); false keeps it. Asked once per
   *  click: Radix changes the tab on mouse-down and again on the focus that follows. */
  canLeave?: () => boolean
}) {
  const asked = useRef<string | null>(null)
  const change = (v: string) => {
    if (canLeave && v !== value) {
      if (asked.current === v) return
      asked.current = v
      setTimeout(() => { asked.current = null })
      if (!canLeave()) return
    }
    onValueChange?.(v)
  }
  return (
    <RTabs.Root
      value={value}
      defaultValue={defaultValue ?? items[0]?.value}
      onValueChange={change}
      className={className}
    >
      <RTabs.List aria-label={ariaLabel} className={cn('mb-4 flex gap-1 overflow-x-auto border-b border-neutral-200', listClassName)}>
        {items.map((t) => (
          <RTabs.Trigger
            key={t.value}
            value={t.value}
            disabled={t.disabled}
            className={cn(
              '-mb-px inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-sm text-neutral-500 hover:text-neutral-800',
              'data-[state=active]:border-primary-600 data-[state=active]:font-medium data-[state=active]:text-primary-700',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600 disabled:opacity-50',
            )}
          >
            {t.label}
            {t.count !== undefined && (
              <span className="rounded-full bg-neutral-100 px-1.5 text-xs font-medium tabular-nums text-neutral-600">{t.count}</span>
            )}
            {t.attention && (
              <span className={cn('h-1.5 w-1.5 rounded-full', DOT[t.attention])} aria-label="needs attention" role="img" />
            )}
          </RTabs.Trigger>
        ))}
      </RTabs.List>
      {items.map((t) => (
        <RTabs.Content key={t.value} value={t.value} className="focus:outline-none">
          {t.content}
        </RTabs.Content>
      ))}
    </RTabs.Root>
  )
}
