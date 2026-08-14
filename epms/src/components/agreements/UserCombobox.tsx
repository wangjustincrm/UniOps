import { useRef, useState, type JSX } from 'react'
import { Search, X } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { DropdownPortal, useAnchorRect } from '@/components/ui/DropdownPortal'
import { userService, type ApiUserBrief } from '@/services/users'

/**
 * Type-to-search picker for a person, replacing a bare `<select>` of the whole
 * directory.
 *
 * A `<select>` is fine for five options and unusable for a company: the
 * operator has to know where a name falls alphabetically, and on the receipt
 * form the list is every active employee. This is the same shape the Agreement
 * pages' Owner field has always had — an input that searches server-side, a
 * portalled result list, and a clear button — lifted into one component so the
 * receipt pickers behave identically instead of approximately.
 *
 * Searching is SERVER-side (GET /users/directory?search=), not a filter over a
 * pre-fetched list: the directory is paginated (default 20 rows), so filtering
 * locally would silently search only the first page — the same trap
 * useUserDirectory's listAll exists to avoid, and the reason this component
 * does not take a `users` prop.
 *
 * Portalled to the body via DropdownPortal because the receipt form sits inside
 * scrolling cards; an in-flow absolute dropdown is clipped by the first
 * ancestor with overflow.
 */
export function UserCombobox(props: {
  /** Currently selected user id, or '' for none. */
  value: string
  onChange: (userId: string) => void
  /** Resolves `value` into a name without waiting for a search — pass the
   *  directory the page already holds, when it has one. */
  knownUsers?: ApiUserBrief[]
  inputId?: string
  placeholder?: string
  disabled?: boolean
  invalid?: boolean
}): JSX.Element {
  const { value, onChange, knownUsers, inputId, placeholder = 'Search people by name…', disabled, invalid } = props

  const anchorRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const rect = useAnchorRect(open, anchorRef)
  const [query, setQuery] = useState('')

  // `enabled: open` — the directory is not fetched until the field is actually
  // used, so a form with this on it costs nothing to render.
  const { data } = useQuery({
    queryKey: ['users', 'directory', query],
    queryFn: () => userService.directory({ search: query || undefined }),
    enabled: open,
    staleTime: 30_000,
  })
  const results = data?.items ?? []

  // The selected person's name comes from whichever source has it: the page's
  // own directory (present on mount, so an existing selection reads correctly
  // before this field is ever opened) or the current search results (covers a
  // person the page's list doesn't carry).
  const selected =
    knownUsers?.find((u) => u.id === value) ?? results.find((u) => u.id === value) ?? null

  return (
    <div ref={anchorRef} className="flex flex-col gap-1.5">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
        <input
          id={inputId}
          type="text"
          autoComplete="off"
          disabled={disabled}
          placeholder={placeholder}
          // While a person is selected the input shows their name; typing
          // clears the selection and turns the field back into a search box.
          value={selected ? selected.full_name : query}
          onFocus={() => { setOpen(true); if (selected) setQuery('') }}
          onChange={(e) => { setQuery(e.target.value); onChange(''); setOpen(true) }}
          className={`h-10 w-full rounded-md border bg-white pl-9 pr-9 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400 ${
            invalid ? 'border-danger-400' : 'border-neutral-300'
          }`}
        />
        {value !== '' && !disabled && (
          <button
            type="button"
            aria-label="Clear selection"
            onClick={() => { onChange(''); setQuery(''); setOpen(false) }}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {open && !selected && rect && (
        <DropdownPortal anchorRect={rect} onClose={() => setOpen(false)}>
          {results.map((u) => (
            <button
              key={u.id}
              type="button"
              className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-primary-50"
              onClick={() => { onChange(u.id); setOpen(false); setQuery('') }}
            >
              <span>{u.full_name}</span>
              {u.department_name && <span className="text-xs text-neutral-400">{u.department_name}</span>}
            </button>
          ))}
          {results.length === 0 && (
            <p className="px-3 py-2 text-sm text-neutral-400">No people found</p>
          )}
        </DropdownPortal>
      )}
    </div>
  )
}
