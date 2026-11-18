import { useState } from 'react'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { cn } from '../lib/cn'
import { Chip } from '../primitives/Chip'
import type { FieldMeta } from '../resource/listMeta'
import { flipGroup, flipSort, moveGroup, removeFilterAt, removeGroup, removeSort, type QuerySpec } from '../resource/querySpec'
import { filterText } from './listView'
import { TourMark } from '../help'

export interface FilterChipsProps {
  spec: QuerySpec
  onSpecChange: (spec: QuerySpec) => void
  fields: Map<string, FieldMeta>
  /** Filters the page sets itself (e.g. the Review list's status): shown, not removable. */
  locked?: string[]
  /** A label for a field /meta does not describe (falls back to the field name). */
  labelOf?: (field: string) => string
  /** Grouping by drag and drop: while a column header is dragged (`dragging`), the Grouping row is a
   *  drop target; `refuse` says why a column cannot be grouped now (null: it can); `onGroup` groups by it.
   *  Its chips can be dragged to another level, or onto "Drop here to remove" to stop grouping by them
   *  (a drag that ends anywhere else changes nothing). */
  groupDrop?: { dragging: string | null; refuse: (field: string) => string | null; onGroup: (field: string) => void }
}

const GROUP_CHIP = 'text/x-group'

/** A drop target that holds while a drag passes over its children (dragleave fires for each one). */
function useDropHover() {
  const [depth, setDepth] = useState(0)
  return {
    over: depth > 0,
    reset: () => setDepth(0),
    bind: { onDragEnter: () => setDepth((d) => d + 1), onDragLeave: () => setDepth((d) => Math.max(0, d - 1)) },
  }
}

/** What shapes the list right now, one group per kind: SORTING (chip order is
 *  the priority; click flips the direction), GROUPING (chip order is the
 *  nesting), SEARCH (one chip per filter, and
 *  the quick search). Nothing active → nothing rendered. */
export function FilterChips({ spec, onSpecChange, fields, locked = [], labelOf, groupDrop }: FilterChipsProps) {
  const hover = useDropHover()
  const [movingGroup, setMovingGroup] = useState<string | null>(null)
  const label = (f: string) => fields.get(f)?.label ?? labelOf?.(f) ?? f
  const sort = spec.sort ?? []
  const filters = (spec.filters ?? []).map((f, i) => ({ f, i }))
  const search = spec.search?.trim()
  const group = spec.group ?? []
  const dragging = groupDrop?.dragging ?? null
  const refusal = dragging ? groupDrop!.refuse(dragging) : null
  if (sort.length === 0 && group.length === 0 && filters.length === 0 && !search && !dragging) return null
  return (
    <TourMark as="div" role="group" aria-label="Active sort, grouping and search" help="list.chips" slot="list.chips" className="flex flex-col gap-1.5 text-xs">
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
      {(group.length > 0 || dragging) && (
        <div
          data-group-drop={dragging ? (refusal ? 'refused' : 'open') : undefined}
          className={cn('flex flex-wrap items-center gap-1.5 rounded',
            dragging && 'border border-dashed px-1.5 py-1',
            dragging && !refusal && (hover.over ? 'border-primary-600 bg-primary-50' : 'border-primary-300'),
            dragging && refusal && 'border-neutral-300 bg-neutral-50')}
          {...(groupDrop ? hover.bind : {})}
          onDragOver={groupDrop ? (e) => {
            // A header's column: droppable only when it can be grouped now; a grouping chip: always (to move it).
            const chip = e.dataTransfer.types.includes(GROUP_CHIP)
            if (chip || (dragging && !refusal)) { e.preventDefault(); e.dataTransfer.dropEffect = 'move' } else { e.dataTransfer.dropEffect = 'none' }
          } : undefined}
          onDrop={groupDrop ? (e) => {
            e.preventDefault()
            e.stopPropagation()
            hover.reset()
            const chip = e.dataTransfer.getData(GROUP_CHIP)
            if (chip) onSpecChange(moveGroup(spec, chip, group.length))  // dropped on the row itself: the innermost level
            else if (dragging && !refusal) groupDrop.onGroup(dragging)
          } : undefined}
        >
          <span className="w-16 font-medium uppercase tracking-wide text-neutral-400">Grouping</span>
          {group.map((g, level) => (
            <span
              key={g.field}
              draggable={!!groupDrop}
              data-group-chip={g.field}
              className={cn('inline-flex', movingGroup === g.field && 'opacity-40', groupDrop && 'cursor-grab active:cursor-grabbing')}
              onDragStart={groupDrop ? (e) => { e.dataTransfer.setData(GROUP_CHIP, g.field); e.dataTransfer.effectAllowed = 'move'; setMovingGroup(g.field) } : undefined}
              onDragEnd={groupDrop ? () => { setMovingGroup(null); hover.reset() } : undefined}  // no drop (Esc, a slip): nothing changes
              onDragOver={groupDrop ? (e) => { if (e.dataTransfer.types.includes(GROUP_CHIP)) e.preventDefault() } : undefined}
              onDrop={groupDrop ? (e) => {
                const chip = e.dataTransfer.getData(GROUP_CHIP)
                if (!chip) return  // a header's column: the row takes it
                e.preventDefault()
                e.stopPropagation()
                hover.reset()
                if (chip !== g.field) onSpecChange(moveGroup(spec, chip, level))
              } : undefined}
            >
              <Chip
                onClick={() => onSpecChange(flipGroup(spec, g.field))}
                onRemove={() => onSpecChange(removeGroup(spec, g.field))}
                removeLabel={`Remove grouping by ${label(g.field)}`}
              >
                <span className="inline-flex items-center gap-1">
                  {label(g.field)} {g.desc ? <ArrowDown aria-label="descending" className="h-3 w-3" /> : <ArrowUp aria-label="ascending" className="h-3 w-3" />}
                </span>
              </Chip>
            </span>
          ))}
          {movingGroup && (
            <span
              data-group-remove
              className="rounded border border-dashed border-danger-200 px-2 py-0.5 text-xs text-danger-700"
              onDragOver={(e) => { if (e.dataTransfer.types.includes(GROUP_CHIP)) { e.preventDefault(); e.dataTransfer.dropEffect = 'move' } }}
              onDrop={(e) => {
                const chip = e.dataTransfer.getData(GROUP_CHIP)
                if (!chip) return
                e.preventDefault()
                e.stopPropagation()
                hover.reset()
                setMovingGroup(null)
                onSpecChange(removeGroup(spec, chip))
              }}
            >
              Drop here to remove
            </span>
          )}
          {dragging && (
            <span role="status" className={cn('text-xs', refusal ? 'text-neutral-500' : 'text-primary-700')}>
              {refusal ?? `Drop here to group by ${label(dragging)}`}
            </span>
          )}
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
            const text = f.label ?? filterText(f, fields.get(f.field) ?? { name: f.field, label: label(f.field), type: 'text', enum_values: [], filterable: true, sortable: false, groupable: false, operators: [] })
            return locked.includes(f.field)
              ? <Chip key={i}>{text}</Chip>
              : <Chip key={i} onRemove={() => onSpecChange(removeFilterAt(spec, i))} removeLabel={`Remove ${text}`}>{text}</Chip>
          })}
        </div>
      )}
    </TourMark>
  )
}
