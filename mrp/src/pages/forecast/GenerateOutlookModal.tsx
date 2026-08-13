// "Generate Outlook" — freezes the continuous demand series into an
// immutable ForecastVersion snapshot covering [anchor_month, anchor_month +
// horizon_months) that Production Plan's MPS run consumes (see task brief:
// the 18-month window is a read-only View over the living table; this is
// what actually confirms one). The live grid on SalesForecastPage keeps
// going after this — generating an outlook does not lock the series itself,
// only produces a point-in-time copy. Portal modal, same shape as
// NewVersionModal.tsx.
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2, Sparkles, X as XIcon } from 'lucide-react'
import { Button, Input, FormField } from '@uniops/shell'

function currentMonthDefault(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function GenerateOutlookModal({
  onClose, onGenerate, busy, error, intentCount = 0,
}: {
  onClose: () => void
  onGenerate: (anchorMonth: string, horizonMonths: number) => void
  busy: boolean
  error: string | null
  /** Count of intent-product rows (planned SKUs with no ERP material code
   *  yet) currently on the grid — see SalesForecastPage's `intentRowIds`.
   *  Deliberately grid-wide rather than scoped to this modal's own
   *  anchor/horizon fields below: there's no cheap way to recompute "which
   *  intent codes have data in exactly this window" without duplicating
   *  the page's month-range math in here, and the modal's own defaults
   *  already match the page's highlighted outlook window in the common
   *  case. Intent rows DO still flow into the snapshot (they're ordinary
   *  mrp_demand_series rows, just flagged) — this is purely a heads-up
   *  that MPS will never schedule them, not a filter. */
  intentCount?: number
}) {
  const [anchorMonth, setAnchorMonth] = useState(currentMonthDefault())
  const [horizonMonths, setHorizonMonths] = useState(18)

  const monthValid = /^\d{4}-(0[1-9]|1[0-2])$/.test(anchorMonth)
  const horizonValid = horizonMonths >= 1 && horizonMonths <= 36

  function submit() {
    if (!monthValid || !horizonValid) return
    onGenerate(anchorMonth, horizonMonths)
  }

  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl border border-neutral-200 bg-white shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-neutral-200">
          <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-900">
            <Sparkles className="h-4 w-4 text-primary-600" /> Generate Outlook
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-m-2.5 flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400 hover:text-neutral-600"
          >
            <XIcon className="h-5 w-5" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <p className="text-sm text-neutral-600">
            Freezes a read-only snapshot of the forecast for Production Plan to run MPS against. The live grid keeps
            going afterward — this doesn't lock it.
          </p>

          <FormField label="Anchor month" htmlFor="outlook-anchor" error={!monthValid ? 'Enter a valid year-month.' : undefined}>
            <Input
              id="outlook-anchor"
              type="month"
              value={anchorMonth}
              onChange={(e) => setAnchorMonth(e.target.value)}
              aria-invalid={!monthValid}
            />
          </FormField>

          <FormField label="Horizon (months)" htmlFor="outlook-horizon" error={!horizonValid ? 'Enter 1–36 months.' : undefined}>
            <Input
              id="outlook-horizon"
              type="number"
              min={1}
              max={36}
              value={horizonMonths}
              onChange={(e) => setHorizonMonths(Number(e.target.value) || 1)}
              aria-invalid={!horizonValid}
            />
          </FormField>

          {intentCount > 0 && (
            <p role="status" className="rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-800">
              This snapshot includes {intentCount} intent product{intentCount === 1 ? '' : 's'}. They are recorded
              but never scheduled.
            </p>
          )}

          {error && (
            <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-neutral-200">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="button" size="sm" onClick={submit} disabled={busy || !monthValid || !horizonValid}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Generate
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
