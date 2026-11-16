/**
 * ★ UAT 14: several staff, picked one at a time — "a staff picker to allow
 * additional staff to be selected as they helped with the hazard assessment."
 * Each pick becomes a chip with a way to take it off; only people on the staff
 * list can be added (the server refuses anybody else).
 */
import { useState } from 'react'
import { X } from 'lucide-react'
import { EMPTY_PERSON, PersonPicker, type PersonValue } from '@/components/PersonPicker'

export interface Picked { id: string; name: string }

export default function PeopleList({ value, onChange, placeholder }: {
  value: Picked[]; onChange: (next: Picked[]) => void; placeholder?: string
}) {
  // The picker is reset after each pick by remounting it.
  const [round, setRound] = useState(0)
  const add = (p: PersonValue) => {
    if (!p.userId) return
    if (!value.some((v) => v.id === p.userId)) onChange([...value, { id: p.userId, name: p.name }])
    setRound((n) => n + 1)
  }
  return (
    <div>
      {value.length > 0 && (
        <ul className="mb-1.5 flex flex-wrap gap-1.5">
          {value.map((p) => (
            <li key={p.id} className="inline-flex items-center gap-1 rounded-full bg-primary-50 py-0.5 pl-2.5 pr-1 text-xs font-medium text-primary-800">
              {p.name}
              <button type="button" aria-label={`Take off ${p.name}`}
                      onClick={() => onChange(value.filter((v) => v.id !== p.id))}
                      className="rounded-full p-0.5 text-primary-600 hover:bg-primary-100">
                <X className="h-3 w-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      <PersonPicker key={round} value={EMPTY_PERSON} onChange={add} allowExternal={false}
                    placeholder={placeholder ?? 'Search for a person to add'} />
    </div>
  )
}
