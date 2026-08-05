// "New Version" form — portal modal (see ConfirmDialog / RoomImportModal
// pattern). horizon_months defaults to 18 per design spec; copy_from is
// optional and lets a planner roll a new month's sheet forward from an
// existing one instead of starting blank (see forecast.py module docstring:
// a blank version has zero grid rows until copied/imported).
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2, X as XIcon } from 'lucide-react'
import { Button, Input, FormField } from '@uniops/shell'
import type { ForecastVersion, CreateVersionBody } from './forecastApi'

function nextMonthDefault(): string {
  const d = new Date()
  d.setMonth(d.getMonth() + 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function NewVersionModal({
  versions, onClose, onCreate, busy, error,
}: {
  versions: ForecastVersion[]
  onClose: () => void
  onCreate: (body: CreateVersionBody) => void
  busy: boolean
  error: string | null
}) {
  const [horizonStart, setHorizonStart] = useState(nextMonthDefault())
  const [horizonMonths, setHorizonMonths] = useState(18)
  const [note, setNote] = useState('')
  const [copyFrom, setCopyFrom] = useState('')

  const monthValid = /^\d{4}-(0[1-9]|1[0-2])$/.test(horizonStart)

  function submit() {
    if (!monthValid || horizonMonths < 1) return
    onCreate({
      horizon_start_month: horizonStart,
      horizon_months: horizonMonths,
      note: note.trim() || null,
      copy_from_version_id: copyFrom || null,
    })
  }

  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl border border-neutral-200 bg-white shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-neutral-200">
          <h2 className="text-base font-semibold text-neutral-900">New Forecast Version</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-neutral-400 hover:text-neutral-600">
            <XIcon className="h-5 w-5" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <FormField label="Horizon start month" htmlFor="horizon-start" error={!monthValid ? 'Enter a valid year-month.' : undefined}>
            <Input
              id="horizon-start"
              type="month"
              value={horizonStart}
              onChange={(e) => setHorizonStart(e.target.value)}
              aria-invalid={!monthValid}
            />
          </FormField>

          <FormField label="Horizon length (months)" htmlFor="horizon-months">
            <Input
              id="horizon-months"
              type="number"
              min={1}
              max={36}
              value={horizonMonths}
              onChange={(e) => setHorizonMonths(Number(e.target.value) || 1)}
            />
          </FormField>

          <FormField
            label="Copy lines from (optional)"
            htmlFor="copy-from"
            hint="Copies matching (material, month) cells as-is — months outside the new horizon are dropped, not shifted."
          >
            <select
              id="copy-from"
              value={copyFrom}
              onChange={(e) => setCopyFrom(e.target.value)}
              className="flex h-10 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary-500"
            >
              <option value="">Start blank</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>{v.version_no} ({v.status})</option>
              ))}
            </select>
          </FormField>

          <FormField label="Note (optional)" htmlFor="version-note">
            <Input id="version-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Q4 rolling update" />
          </FormField>

          {error && (
            <p role="alert" className="rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-neutral-200">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="button" size="sm" onClick={submit} disabled={busy || !monthValid}>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Create Version
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
