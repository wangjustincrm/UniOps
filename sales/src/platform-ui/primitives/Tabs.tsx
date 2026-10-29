import * as RTabs from '@radix-ui/react-tabs'
import type { ReactNode } from 'react'
import { cn } from '../lib/cn'

export interface TabItem {
  value: string
  label: ReactNode
  content: ReactNode
  disabled?: boolean
}

/** In-page tabs (sections of one record). Module-level navigation stays with
 *  the shell's tab host. */
export function Tabs({
  items, value, defaultValue, onValueChange, className,
}: {
  items: TabItem[]
  value?: string
  defaultValue?: string
  onValueChange?: (v: string) => void
  className?: string
}) {
  return (
    <RTabs.Root
      value={value}
      defaultValue={defaultValue ?? items[0]?.value}
      onValueChange={onValueChange}
      className={className}
    >
      <RTabs.List className="mb-4 flex gap-1 border-b border-neutral-200">
        {items.map((t) => (
          <RTabs.Trigger
            key={t.value}
            value={t.value}
            disabled={t.disabled}
            className={cn(
              '-mb-px border-b-2 border-transparent px-3 py-2 text-sm text-neutral-500 hover:text-neutral-800',
              'data-[state=active]:border-primary-600 data-[state=active]:font-medium data-[state=active]:text-primary-700',
              'disabled:opacity-50',
            )}
          >
            {t.label}
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
