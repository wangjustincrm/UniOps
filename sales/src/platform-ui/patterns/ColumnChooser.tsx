import { Eye } from 'lucide-react'
import { Checkbox } from '../primitives/Checkbox'
import { Popover } from '../primitives/Popover'

export interface ColumnChoice {
  id: string
  label: string
}

/** The eye at the left of the header: show or hide any column. A hidden
 *  column can still be searched and sorted on (chips, search panel). */
export function ColumnChooser({ columns, hidden, onChange }: {
  columns: ColumnChoice[]
  hidden: string[]
  onChange: (hidden: string[]) => void
}) {
  const visibleCount = columns.filter((c) => !hidden.includes(c.id)).length
  return (
    <Popover
      align="start"
      className="max-h-80 w-60 overflow-y-auto"
      trigger={
        <button type="button" aria-label="Show or hide columns" className="rounded p-0.5 text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700">
          <Eye className="h-3.5 w-3.5" />
        </button>
      }
    >
      <p className="px-1 pb-1 text-xs font-medium normal-case tracking-normal text-neutral-500">Columns</p>
      <ul className="flex flex-col text-sm normal-case tracking-normal text-neutral-700">
        {columns.map((c) => {
          const shown = !hidden.includes(c.id)
          return (
            <li key={c.id} className="rounded px-1 py-1 hover:bg-neutral-50">
              <Checkbox
                label={c.label}
                checked={shown}
                disabled={shown && visibleCount === 1}  /* never hide the last column */
                onCheckedChange={() => onChange(shown ? [...hidden, c.id] : hidden.filter((h) => h !== c.id))}
              />
            </li>
          )
        })}
      </ul>
    </Popover>
  )
}
