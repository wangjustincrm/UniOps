import { useState } from 'react'
import { Command } from 'cmdk'
import { Check, PlusCircle } from 'lucide-react'
import { Popover } from './Popover'
import { cn } from '../lib/cn'

/** shadcn's data-table faceted filter (Popover + Command / cmdk): pick values of one field, with a search and each
 *  value's count when known. The selection is the caller's (a list's filter). */
export interface Facet { value: string; label?: string; count?: number }

export function FacetFilter({ title, options, selected, onChange }: {
  title: string; options: Facet[]; selected: string[]; onChange: (values: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const toggle = (v: string) => onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v])
  return (
    <Popover open={open} onOpenChange={setOpen} align="start"
      trigger={
        <button type="button" aria-label={`${title}${selected.length ? `: ${selected.length} chosen` : ''}`}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-dashed border-neutral-300 bg-white px-2.5 text-sm text-neutral-700 hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
          <PlusCircle aria-hidden className="h-4 w-4" />{title}
          {selected.length > 0 && <span className="rounded bg-neutral-100 px-1 text-xs">{selected.length}</span>}
        </button>
      }>
      <Command label={title} className="w-56 text-sm">
        <Command.Input placeholder={title} className="mb-1 h-8 w-full rounded-md border border-neutral-200 px-2 outline-none focus:border-primary-500" />
        <Command.List className="max-h-64 overflow-y-auto">
          <Command.Empty className="px-2 py-1.5 text-neutral-500">Nothing found</Command.Empty>
          {options.map((o) => {
            const on = selected.includes(o.value)
            return (
              <Command.Item key={o.value} value={o.label ?? o.value} onSelect={() => toggle(o.value)}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 data-[selected=true]:bg-neutral-100">
                <span className={cn('flex h-4 w-4 items-center justify-center rounded border', on ? 'border-primary-600 bg-primary-600 text-white' : 'border-neutral-300')}>
                  {on && <Check aria-hidden className="h-3 w-3" />}
                </span>
                <span className="flex-1">{o.label ?? o.value}</span>
                {o.count != null && <span className="font-mono text-xs text-neutral-500">{o.count}</span>}
              </Command.Item>
            )
          })}
        </Command.List>
        {selected.length > 0 && (
          <button type="button" className="mt-1 w-full rounded px-2 py-1 text-center text-xs text-primary-700 hover:bg-neutral-50" onClick={() => onChange([])}>
            Clear
          </button>
        )}
      </Command>
    </Popover>
  )
}
