// Supplier picker — searches mdm-api's business partners.
//
// Supply parameters are keyed by `partner_code`, and a code typed by hand is
// a row that resolves to nobody: purchase suggestions then name a supplier
// that does not exist, or (worse) a different one. There are 733 partners in
// master data, so this searches rather than listing them all.
//
// Same portal/fixed overlay convention as MaterialPicker — a plain absolute
// panel gets clipped by the page's scroll containers.
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, Loader2, Search, X } from 'lucide-react'
import { mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'

export interface PartnerOption {
  code: string
  name: string
}

interface PartnerListResponse {
  items: { code: string; name: string }[]
  total: number
}

export function SupplierPicker({
  value, onSelect, onClear, hasError, disabled,
}: {
  /** Selected partner code, or '' for none. */
  value: string
  onSelect: (partner: PartnerOption) => void
  onClear: () => void
  hasError?: boolean
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const [q, setQ] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // Server-side search: 733 partners is too many to ship to the browser for
  // a field most people fill in twice.
  const searchQuery = useQuery({
    queryKey: ['partners', q],
    queryFn: () => mdmApi.get<PartnerListResponse>(
      `/mdm/v1/partners?role=supplier&page_size=25${q ? `&search=${encodeURIComponent(q)}` : ''}`),
    enabled: open,
  })
  const options = useMemo(() => searchQuery.data?.items ?? [], [searchQuery.data])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  return (
    <>
      <div className="flex items-center gap-1">
        <button
          type="button"
          disabled={disabled}
          onClick={(e) => {
            setAnchor(e.currentTarget.getBoundingClientRect())
            setOpen((v) => !v)
          }}
          className={cn(
            'flex h-11 flex-1 items-center justify-between gap-2 rounded-lg border bg-white px-3 text-left text-sm disabled:cursor-not-allowed disabled:bg-neutral-50',
            hasError ? 'border-danger-400' : 'border-neutral-300',
          )}
        >
          <span className={value ? 'font-mono text-xs' : 'text-neutral-400'}>
            {value || 'Search suppliers…'}
          </span>
          <ChevronDown aria-hidden className="h-4 w-4 shrink-0 text-neutral-400" />
        </button>
        {value && !disabled && (
          <button type="button" onClick={onClear} aria-label="Clear supplier"
            className="text-neutral-400 hover:text-danger-600">
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {open && anchor && createPortal(
        <>
          <div className="fixed inset-0 z-[95]" onClick={() => setOpen(false)} />
          <div
            className="fixed z-[96] w-80 rounded-lg border border-neutral-200 bg-white shadow-lg"
            style={{ top: anchor.bottom + 4, left: anchor.left }}
          >
            <div className="flex items-center gap-2 border-b border-neutral-100 px-3 py-2">
              <Search aria-hidden className="h-3.5 w-3.5 text-neutral-400" />
              <input
                ref={inputRef}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Code or name…"
                className="w-full text-sm focus:outline-none"
              />
              {searchQuery.isFetching && <Loader2 className="h-3.5 w-3.5 animate-spin text-neutral-400" />}
            </div>
            <div className="max-h-64 overflow-y-auto py-1">
              {options.length === 0 && !searchQuery.isFetching && (
                <p className="px-3 py-4 text-center text-xs text-neutral-400">
                  {q ? 'No supplier matches that.' : 'Type to search.'}
                </p>
              )}
              {options.map((partner) => (
                <button
                  key={partner.code}
                  type="button"
                  onClick={() => { onSelect(partner); setOpen(false); setQ('') }}
                  className="flex w-full items-baseline gap-2 px-3 py-2 text-left text-sm hover:bg-neutral-50"
                >
                  <span className="font-mono text-xs text-neutral-500">{partner.code}</span>
                  <span className="truncate">{partner.name}</span>
                </button>
              ))}
            </div>
          </div>
        </>,
        document.body,
      )}
    </>
  )
}
