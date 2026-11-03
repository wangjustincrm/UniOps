import { ArrowDown, ArrowUp, Eye } from 'lucide-react'
import { Checkbox } from './Checkbox'
import { Popover } from './Popover'

export interface ColumnChoice {
  id: string
  label: string
}

/** The eye at the left of the header: show or hide any column, and move it
 *  up or down (the keyboard way to reorder; the header can also be dragged).
 *  A hidden column can still be searched and sorted on. */
export function ColumnChooser({ columns, hidden, onChange, onMove }: {
  /** In display order. */
  columns: ColumnChoice[]
  hidden: string[]
  onChange: (hidden: string[]) => void
  onMove?: (id: string, delta: -1 | 1) => void
}) {
  const visibleCount = columns.filter((c) => !hidden.includes(c.id)).length
  return (
    <Popover
      align="start"
      className="max-h-96 w-72 overflow-y-auto"
      trigger={
        <button type="button" aria-label="Show, hide or move columns" className="rounded p-0.5 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700">
          <Eye className="h-3.5 w-3.5" />
        </button>
      }
    >
      <p className="px-1 pb-1 text-xs font-medium normal-case tracking-normal text-neutral-500">Columns</p>
      <ul aria-label="Columns" className="flex flex-col text-sm normal-case tracking-normal text-neutral-700">
        {columns.map((c, i) => {
          const shown = !hidden.includes(c.id)
          return (
            <li key={c.id} className="flex items-center gap-1 rounded px-1 py-1 hover:bg-neutral-50">
              <Checkbox
                label={c.label}
                className="min-w-0 flex-1"
                checked={shown}
                disabled={shown && visibleCount === 1}  /* never hide the last column */
                onCheckedChange={() => onChange(shown ? [...hidden, c.id] : hidden.filter((h) => h !== c.id))}
              />
              {onMove && (
                <>
                  <button type="button" aria-label={`Move ${c.label} up`} disabled={i === 0} onClick={() => onMove(c.id, -1)}
                    className="rounded p-0.5 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700 disabled:opacity-30">
                    <ArrowUp className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" aria-label={`Move ${c.label} down`} disabled={i === columns.length - 1} onClick={() => onMove(c.id, 1)}
                    className="rounded p-0.5 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700 disabled:opacity-30">
                    <ArrowDown className="h-3.5 w-3.5" />
                  </button>
                </>
              )}
            </li>
          )
        })}
      </ul>
    </Popover>
  )
}
