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
  onClose, onGenerate, busy, error,
}: {
  onClose: () => void
  onGenerate: (anchorMonth: string, horizonMonths: number) => void
  busy: boolean
  error: string | null
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
          <button type="button" onClick={onClose} aria-label="Close" className="text-neutral-400 hover:text-neutral-600">
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
