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
import { useNavigate } from 'react-router-dom'
import {
  Keyboard, Camera, Loader2, AlertCircle, ArrowLeft, Search,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useCheckOutVisit, useVisitor, useUserBrief, type Visit } from '@/services/api'
import { QrScanner } from '@/components/QrScanner'
import { CheckOutConfirm } from '@/components/CheckOutConfirm'

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
  const navigate = useNavigate()

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
      <button
        onClick={() => navigate('/')}
        className="inline-flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-700"
      >
        <ArrowLeft className="h-3 w-3" />
        Back
      </button>

      <h1 className="mt-2 text-2xl font-bold text-neutral-900">Check out</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Scan the QR code on the visitor badge — or paste the visit ID — to record their departure.
      </p>

      {/* Mode toggle */}
      <div className="mt-5 inline-flex rounded-md border border-neutral-200 bg-white p-0.5 text-sm">
        <button
          onClick={() => setMode('scan')}
          className={
            'inline-flex items-center gap-1.5 rounded px-3 py-1.5 ' +
            (mode === 'scan' ? 'bg-primary-600 text-white' : 'text-neutral-600 hover:bg-neutral-50')
          }
        >
          <Camera className="h-4 w-4" />
          Camera
        </button>
        <button
          onClick={() => setMode('input')}
          className={
            'inline-flex items-center gap-1.5 rounded px-3 py-1.5 ' +
            (mode === 'input' ? 'bg-primary-600 text-white' : 'text-neutral-600 hover:bg-neutral-50')
          }
        >
          <Keyboard className="h-4 w-4" />
          Scanner / type
        </button>
      </div>

      {/* Mode body */}
      <div className="mt-4">
        {mode === 'scan' ? (
          <QrScanner onDecode={onDecoded} />
        ) : (
          <form
            onSubmit={(e) => { e.preventDefault(); void lookupVisit(input) }}
            className="rounded-md border border-neutral-200 bg-white p-4"
          >
            <label className="block text-sm font-medium text-neutral-700">
              Visit ID (paste / scan into this field)
            </label>
            <div className="mt-1 flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="e.g. 8a7b3f8e-…"
                autoFocus
                className="flex-1 rounded-md border border-neutral-300 px-3 py-2 text-sm font-mono outline-none focus:border-primary-500 focus:ring-1 focus:ring-primary-500"
              />
              <button
                type="submit"
                disabled={lookingUp || !input.trim()}
                className="inline-flex items-center gap-1 rounded-md bg-primary-600 px-3 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
              >
                {lookingUp ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                Look up
              </button>
            </div>
            <p className="mt-2 text-xs text-neutral-500">
              A USB barcode scanner sends the QR contents as keyboard input — focus this field and
              scan to look up automatically.
            </p>
          </form>
        )}
      </div>

      {/* Lookup error */}
      {lookupError && (
        <div className="mt-4 flex items-start gap-2 rounded-md border border-red-200 bg-danger-50 px-3 py-2 text-sm text-danger-600">
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
