import { ArrowDown, ArrowUp } from 'lucide-react'
import { Chip } from '../primitives/Chip'
import type { FieldMeta } from '../resource/listMeta'
import { flipSort, removeFilterAt, removeSort, type QuerySpec } from '../resource/querySpec'
import { filterText } from './listView'

export interface FilterChipsProps {
  spec: QuerySpec
  onSpecChange: (spec: QuerySpec) => void
  fields: Map<string, FieldMeta>
  /** Filters the page sets itself (e.g. the Review list's status): shown, not removable. */
  locked?: string[]
  /** A label for a field /meta does not describe (falls back to the field name). */
  labelOf?: (field: string) => string
}

/** What shapes the list right now, one group per kind: SORTING (chip order is
 *  the priority; click flips the direction), SEARCH (one chip per filter, and
 *  the quick search). Nothing active → nothing rendered. */
export function FilterChips({ spec, onSpecChange, fields, locked = [], labelOf }: FilterChipsProps) {
  const label = (f: string) => fields.get(f)?.label ?? labelOf?.(f) ?? f
  const sort = spec.sort ?? []
  const filters = (spec.filters ?? []).map((f, i) => ({ f, i }))
  const search = spec.search?.trim()
  if (sort.length === 0 && filters.length === 0 && !search) return null
  return (
    <div role="group" aria-label="Active sort and search" className="flex flex-col gap-1.5 text-xs">
      {sort.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-16 font-medium uppercase tracking-wide text-neutral-400">Sorting</span>
          {sort.map((s) => (
            <Chip
              key={s.field}
              onClick={() => onSpecChange(flipSort(spec, s.field))}
              onRemove={() => onSpecChange(removeSort(spec, s.field))}
              removeLabel={`Remove sort by ${label(s.field)}`}
            >
              <span className="inline-flex items-center gap-1">
                {label(s.field)} {s.desc ? <ArrowDown aria-label="descending" className="h-3 w-3" /> : <ArrowUp aria-label="ascending" className="h-3 w-3" />}
              </span>
            </Chip>
          ))}
        </div>
      )}
      {(filters.length > 0 || search) && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-16 font-medium uppercase tracking-wide text-neutral-400">Search</span>
          {search && (
            <Chip onRemove={() => onSpecChange({ ...spec, page: 1, search: undefined })} removeLabel="Clear the quick search">
              Any field contains "{search}"
            </Chip>
          )}
          {filters.map(({ f, i }) => {
            const text = filterText(f, fields.get(f.field) ?? { name: f.field, label: label(f.field), type: 'text', enum_values: [], filterable: true, sortable: false, groupable: false, operators: [] })
            return locked.includes(f.field)
              ? <Chip key={i}>{text}</Chip>
              : <Chip key={i} onRemove={() => onSpecChange(removeFilterAt(spec, i))} removeLabel={`Remove ${text}`}>{text}</Chip>
          })}
        </div>
      )}
    </div>
  )
}
