/** QR-driven check-out (PRD §2.4 / §6.5.2).
 *
 * Two input modes share one confirmation flow:
 *
 *   - **Desktop**: USB barcode scanner (keyboard-emulation) or manual
 *     paste/typing into the input. Press Enter / click Look Up.
 *   - **Mobile**: live camera scan via `QrScanner` (getUserMedia).
 *
 * Mode auto-detects from screen width but the user can toggle.
 * Once a UUID resolves to a Visit, the CheckOutConfirm modal collects
 * badge_returned / PPE / notes and POSTs check-out.
 */
import { useState } from 'react'
import { BackLink } from '@/components/BackLink'
import {
  Keyboard, Camera, Loader2, AlertCircle, ArrowLeft, Search,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useCheckOutVisit, useVisitor, useUserBrief, type Visit } from '@/services/api'
import { QrScanner } from '@/components/QrScanner'
import { CheckOutConfirm } from '@/components/CheckOutConfirm'

import { Button, Input, Segmented, cardClass } from '@uniops/shell'
import { cn } from '@/lib/utils'
type Mode = 'scan' | 'input'

// UUID v4-ish — the API returns the proper format, but we want to accept any
// 8-4-4-4-12 hex pattern so the user can type a partial QR they read off the
// printed badge.
const UUID_RE = /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/

function extractUuid(text: string): string | null {
  const m = text.match(UUID_RE)
  return m ? m[0].toLowerCase() : null
}

export default function CheckOutPage() {

  // Auto-pick the default mode by viewport, but let the user override.
  const [mode, setMode] = useState<Mode>(() => {
    if (typeof window !== 'undefined' && window.innerWidth < 768) return 'scan'
    return 'input'
  })

  // Input flow state
  const [input, setInput] = useState('')

  // Resolved visit (the modal is rendered when this is set).
  const [pending, setPending] = useState<Visit | null>(null)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const [lookingUp, setLookingUp] = useState(false)

  const visitorQ = useVisitor(pending?.visitor_id)
  const hostQ    = useUserBrief(pending?.host_id)

  const checkOut = useCheckOutVisit(pending?.id)

  const lookupVisit = async (raw: string) => {
    const id = extractUuid(raw.trim())
    if (!id) {
      setLookupError('Not a valid visit ID — scan the QR code on the badge.')
      return
    }
    setLookupError(null)
    setLookingUp(true)
    try {
      const v = await api.get<Visit>(`/api/v1/visits/${id}`)
      if (v.status !== 'checked_in') {
        setLookupError(
          v.status === 'checked_out'
            ? 'This visitor has already checked out.'
            : `Visit cannot be checked out (status: ${v.status}).`,
        )
        return
      }
      setPending(v)
    } catch (err) {
      setLookupError(err instanceof Error ? err.message : 'Visit not found.')
    } finally {
      setLookingUp(false)
    }
  }

  // Camera path → just hand the decoded text to the lookup pipeline.
  const onDecoded = (text: string) => {
    if (lookingUp || pending) return
    void lookupVisit(text)
  }

  // After a successful checkout, reset state so the operator can scan the
  // next badge without leaving the page. We call this from the mutation's
  // onSuccess (not from a useEffect on `isSuccess`) to avoid cascading
  // renders.
  const resetForNextScan = () => {
    setPending(null)
    setInput('')
    checkOut.reset()
  }

  return (
    <div className="max-w-2xl">
      <BackLink
        to="/"
        className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg-secondary"
      >
        <ArrowLeft className="h-3 w-3" />
        Back
      </BackLink>

      <h1 className="mt-2 text-2xl font-semibold text-fg">Check out</h1>
      <p className="mt-1 text-sm text-fg-muted">
        Scan the QR code on the visitor badge — or paste the visit ID — to record their departure.
      </p>

      {/* Mode toggle */}
      <Segmented<Mode>
        size="md"
        className="mt-5"
        items={[
          { key: 'scan', label: <span className="inline-flex items-center gap-1.5"><Camera className="h-4 w-4" />Camera</span> },
          { key: 'input', label: <span className="inline-flex items-center gap-1.5"><Keyboard className="h-4 w-4" />Scanner / type</span> },
        ]}
        value={mode}
        onChange={setMode}
      />

      {/* Mode body */}
      <div className="mt-4">
        {mode === 'scan' ? (
          <QrScanner onDecode={onDecoded} />
        ) : (
          <form
            onSubmit={(e) => { e.preventDefault(); void lookupVisit(input) }}
            className={cn(cardClass({ inset: true }), 'p-4')}
          >
            <label className="block text-sm font-medium text-fg-secondary">
              Visit ID (paste / scan into this field)
            </label>
            <div className="mt-1 flex gap-2">
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="e.g. 8a7b3f8e-…"
                autoFocus
                className="flex-1 font-mono"
              />
              <Button size="md"
                type="submit"
                disabled={lookingUp || !input.trim()}
               
              >
                {lookingUp ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                Look up
              </Button>
            </div>
            <p className="mt-2 text-xs text-fg-muted">
              A USB barcode scanner sends the QR contents as keyboard input — focus this field and
              scan to look up automatically.
            </p>
          </form>
        )}
      </div>

      {/* Lookup error */}
      {lookupError && (
        <div className="mt-4 flex items-start gap-2 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          {lookupError}
        </div>
      )}

      {/* Confirmation modal */}
      {pending && (
        <CheckOutConfirm
          visit={pending}
          visitor={visitorQ.data}
          host={hostQ.data}
          isPending={checkOut.isPending}
          error={checkOut.error}
          onCancel={() => { setPending(null); checkOut.reset() }}
          onConfirm={(payload) =>
            checkOut.mutate(payload, { onSuccess: resetForNextScan })
          }
        />
      )}
    </div>
  )
}
