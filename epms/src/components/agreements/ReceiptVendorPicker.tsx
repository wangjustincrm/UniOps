import { useEffect, useRef, useState } from 'react'
import { Search, X } from 'lucide-react'
import { useVendors } from '@/hooks/useVendors'
import { AiBadge } from '@/components/ui/AiBadge'
import { DropdownPortal, useAnchorRect } from '@/components/ui/DropdownPortal'
import { cn } from '@/lib/utils'
import type { ApiVendor } from '@/services/vendors'

// The merchant on a receipt, as the two things it actually is: a row in the
// vendor master (or not), and the text that gets stored either way.
export interface ReceiptVendorValue {
  // null = not in the vendor list. A first-class, routine outcome — see below.
  vendorId: string | null
  vendorName: string
}

// A name OCR read off the slip, handed over for a match attempt. An OBJECT,
// not a bare string, so re-reading a photo of the same shop re-triggers the
// attempt: identity changes even when the text doesn't.
export interface ReceiptVendorSuggestion {
  text: string
}

interface ReceiptVendorPickerProps {
  value: ReceiptVendorValue
  onChange: (next: ReceiptVendorValue) => void
  // Set by the caller when OCR returns a merchant name; the picker searches
  // for it and binds it automatically when exactly one vendor is unmistakably
  // that merchant, otherwise opens with the candidates listed.
  suggestion?: ReceiptVendorSuggestion | null
  disabled?: boolean
  inputId?: string
}

// ─── Matching an OCR string to the vendor list ──────────────────────────────
//
// ⚠️ These helpers exist ONLY to pre-select something a human then confirms on
// screen. They are NOT the mismatch verdict — that stays server-side and is
// now an id comparison (epms-api schemas/agreement_receipt.py::
// receipt_vendor_mismatch). Nothing here decides whether a receipt is on the
// wrong house account; it decides which row to offer the recorder.

function vendorWords(name: string): string[] {
  return name.toLowerCase().split(/[^a-z0-9]+/i).filter(Boolean)
}

// What to ask the API for. The `search` filter is a plain ILIKE, so handing it
// the raw till header ("PRINCESS AUTO #12") finds nothing — the store number
// is in the middle of the pattern. Dropping all-digit words leaves the part
// that is actually the merchant's name.
function vendorSearchStem(name: string): string {
  const words = vendorWords(name).filter((w) => !/^\d+$/.test(w))
  return words.length > 0 ? words.join(' ') : name.trim()
}

function normalizeVendorName(name: string): string {
  return vendorWords(name).filter((w) => !/^\d+$/.test(w)).join('')
}

// Shortest overlap we will auto-bind on. Below this, containment stops meaning
// "the same merchant, spelled differently" and starts meaning "these two names
// happen to share a syllable" — and an auto-binding that is wrong is worse
// than none, because the recorder has nothing on screen prompting them to look.
const MIN_CONFIDENT_OVERLAP = 4

function uniqueVendorMatch(options: ApiVendor[], text: string): ApiVendor | null {
  const target = normalizeVendorName(text)
  if (target.length < MIN_CONFIDENT_OVERLAP) return null
  const hits = options.filter((v) => {
    const name = normalizeVendorName(v.name)
    if (name.length < MIN_CONFIDENT_OVERLAP) return false
    if (name === target) return true
    // Two-way containment, for the same reason the backend's text rule uses
    // it: the till adds a store number, the master data adds "Ltd", and which
    // side is longer is not predictable.
    return name.includes(target) || target.includes(name)
  })
  // EXACTLY one, deliberately. Two plausible candidates is precisely when a
  // person has to choose — auto-picking the first would be a coin flip made
  // silently.
  return hits.length === 1 ? hits[0] : null
}

/**
 * Vendor field for an agreement receipt: bound to master data when it can be,
 * plain text when it can't.
 *
 * Unlike the invoice upload form's vendor search, selecting a vendor here is
 * OPTIONAL and nothing is blocked when nothing is selected — no required
 * asterisk, no disabled submit. Counter slips routinely come from one-off
 * merchants that are not in the vendor master and have no business being added
 * to it so a $14 slip can be filed; forcing a master-data record first would
 * stop the recorder at the counter (the user's own ruling on this task).
 *
 * The two states are visibly different, but the unbound one is NOT an error
 * colour: "not in the vendor list" is the normal outcome for half these
 * receipts, and colouring it like a mistake would teach people to ignore the
 * one colour that does mean something on this page (the vendor-mismatch
 * warning).
 */
export function ReceiptVendorPicker({
  value, onChange, suggestion, disabled, inputId,
}: ReceiptVendorPickerProps) {
  const anchorRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const anchorRect = useAnchorRect(open, anchorRef)

  // What we ASK the API for, which is not always what is in the box: after
  // OCR the box holds the till header verbatim (that string is what gets
  // saved if no vendor matches) while the search runs on its searchable stem.
  const [query, setQuery] = useState('')
  // The OCR text still waiting for its candidate list to arrive.
  const [pending, setPending] = useState<string | null>(null)
  const [aiText, setAiText] = useState<string | null>(null)
  const [aiBound, setAiBound] = useState(false)

  const { data, isFetching } = useVendors({
    search: query || undefined, active_only: true, page_size: 50,
  })
  const options = data?.items ?? []

  // A new suggestion: park the raw text in the field (it is what gets stored
  // if nothing matches), and start a match attempt.
  useEffect(() => {
    const text = suggestion?.text.trim()
    if (!text) return
    onChange({ vendorId: null, vendorName: text })
    setQuery(vendorSearchStem(text))
    setAiText(text)
    setAiBound(false)
    setPending(text)
  }, [suggestion]) // eslint-disable-line react-hooks/exhaustive-deps

  // …and resolve it once that query's results have actually landed. Split from
  // the effect above because the candidates are fetched asynchronously: judging
  // the match against whatever list happened to be in the cache at OCR time
  // would auto-bind, or fail to, based on a race.
  useEffect(() => {
    if (pending === null || isFetching || data === undefined) return
    const hit = uniqueVendorMatch(options, pending)
    if (hit) {
      onChange({ vendorId: hit.id, vendorName: hit.name })
      setQuery(hit.name)
      setAiBound(true)
      setOpen(false)
    } else {
      // No unmistakable match — show the candidates rather than deciding.
      setOpen(true)
    }
    setPending(null)
  }, [pending, isFetching, data]) // eslint-disable-line react-hooks/exhaustive-deps

  const typed = (text: string) => {
    // Typing over a bound vendor unbinds it: the text and the id must never
    // describe two different merchants, since vendor_name is a snapshot of the
    // bound row, not an independent field.
    onChange({ vendorId: null, vendorName: text })
    setQuery(text)
    setAiText(null)
    setAiBound(false)
    setOpen(true)
  }

  const pick = (vendor: ApiVendor) => {
    onChange({ vendorId: vendor.id, vendorName: vendor.name })
    setQuery(vendor.name)
    setAiText(null)
    setAiBound(false)
    setOpen(false)
  }

  const clear = () => {
    onChange({ vendorId: null, vendorName: '' })
    setQuery('')
    setAiText(null)
    setAiBound(false)
  }

  return (
    <div ref={anchorRef} className="flex flex-col gap-1">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
        <input
          id={inputId}
          type="text"
          disabled={disabled}
          // String(255) on the column — a paste that overflows it reaches the
          // API as a DataError (not an IntegrityError), which the receipt
          // routes' friendly 409 handler does not catch, so it lands as a bare
          // 500. maxLength stops it before the request.
          maxLength={255}
          placeholder="Search the vendor list, or type what the receipt says…"
          value={value.vendorName}
          onFocus={() => { if (!disabled) setOpen(true) }}
          onChange={(e) => typed(e.target.value)}
          className={cn(
            'h-10 w-full rounded-md border bg-white pl-9 pr-9 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600',
            'border-neutral-300 disabled:cursor-not-allowed disabled:bg-neutral-50 disabled:text-neutral-500',
          )}
        />
        {value.vendorName !== '' && !disabled && (
          <button
            type="button"
            onClick={clear}
            aria-label="Clear vendor"
            className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {open && !disabled && anchorRect && (
        <DropdownPortal anchorRect={anchorRect} onClose={() => setOpen(false)}>
          {options.map((v) => (
            <button
              key={v.id}
              type="button"
              className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left"
              onClick={() => pick(v)}
            >
              <span className="font-mono text-xs rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-600">{v.code}</span>
              {v.name}
            </button>
          ))}
          {options.length === 0 && (
            <p className="px-3 py-2 text-sm text-neutral-400">
              No vendor found — the text above is saved with the receipt as it is
            </p>
          )}
        </DropdownPortal>
      )}

      {/* Bound to master data, or just a string? Different words, deliberately
          neither of them an error colour. */}
      {value.vendorId ? (
        <p className="flex items-center text-xs text-success-600">
          Matched to the vendor list{aiBound && <AiBadge />}
        </p>
      ) : value.vendorName.trim() ? (
        <p className="flex items-center text-xs text-neutral-500">
          Not in the vendor list — saved as the text printed on the receipt
          {aiText && <AiBadge />}
        </p>
      ) : null}
      {aiText && !value.vendorId && (
        <p className="text-xs text-primary-500">AI extracted: "{aiText}"</p>
      )}
    </div>
  )
}
