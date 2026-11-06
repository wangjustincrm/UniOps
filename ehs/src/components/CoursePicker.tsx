/**
 * Choosing a course by typing.
 *
 * ★ Round 11: "allow words to be typed and return the best matching course
 * based on what is typed regardless of what page it is on." The list sits in
 * the page rather than floating over it, so a dialog's scroll cannot clip it.
 */
import { useMemo, useState } from 'react'
import { inputClass } from '@/components/ui'

export interface PickableCourse { id: string; name: string }

/** Starts with the words first, then a word that starts with them, then anywhere. */
function rank(name: string, term: string): number {
  const n = name.toLowerCase()
  if (n.startsWith(term)) return 0
  if (n.split(/\s+/).some((w) => w.startsWith(term))) return 1
  return n.includes(term) ? 2 : 3
}

export function CoursePicker({ courses, value, onChange }: {
  courses: PickableCourse[]
  value: string
  onChange: (id: string) => void
}) {
  const chosen = courses.find((c) => c.id === value)
  const [term, setTerm] = useState('')
  const [open, setOpen] = useState(false)
  const matches = useMemo(() => {
    const t = term.trim().toLowerCase()
    if (!t) return courses
    return courses
      .map((c) => ({ c, r: rank(c.name, t) }))
      .filter((x) => x.r < 3)
      .sort((a, b) => a.r - b.r || a.c.name.localeCompare(b.c.name))
      .map((x) => x.c)
  }, [courses, term])
  const pick = (id: string) => { onChange(id); setTerm(''); setOpen(false) }
  return (
    <div>
      <input
        className={inputClass}
        role="combobox"
        aria-expanded={open}
        aria-controls="course-picker-list"
        aria-label="Course"
        placeholder="Type to find a course"
        value={open ? term : chosen?.name ?? ''}
        onFocus={() => setOpen(true)}
        onChange={(e) => { setTerm(e.target.value); setOpen(true) }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && open && matches[0]) { e.preventDefault(); pick(matches[0].id) }
          if (e.key === 'Escape') setOpen(false)
        }}
      />
      {open && (
        <ul id="course-picker-list" role="listbox" aria-label="Courses"
            className="mt-1 max-h-48 overflow-y-auto rounded-lg border border-neutral-200 bg-white">
          {matches.map((c) => (
            <li key={c.id} role="option" aria-selected={c.id === value}>
              <button type="button" onClick={() => pick(c.id)}
                      className="block min-h-[40px] w-full px-3 py-2 text-left text-sm text-neutral-800 hover:bg-neutral-50">
                {c.name}
              </button>
            </li>
          ))}
          {matches.length === 0 && (
            <li className="px-3 py-2 text-sm text-neutral-500">No course matches that.</li>
          )}
        </ul>
      )}
    </div>
  )
}
