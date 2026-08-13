// "Bind to material code" — attaches an intent product's placeholder
// INTENT-xxxxxxxx forecast rows to a real ERP material code once one
// exists. mrp-api's bind endpoint moves every series cell + change-log row
// from the placeholder onto the real code in one transaction (see
// intentApi.ts's header) and 409s if the target already carries forecast
// rows or the intent product isn't 'active' anymore — that 409's detail is
// a deliberately human-readable sentence (design decision D11) and must
// reach the planner verbatim, not get swallowed into "bind failed".
//
// There is no dry-run/preview endpoint, so the "this will move N months /
// X t" line below is computed client-side from the page's own `committed`
// cell map for this intent code (SalesForecastPage already holds it — it's
// the same continuous grid the intent row lives on) rather than from the
// server. It can under-count by a few seconds' worth of not-yet-autosaved
// edits; see task-5-report.md for why that's an accepted, documented gap
// rather than a second network round trip.
import { useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Link2, Loader2, X as XIcon } from 'lucide-react'
import { Button, FormField } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { MaterialPicker } from '@/pages/consignment/MaterialPicker'
import type { MaterialOption } from '@/lib/materials'
import { intentApi, type IntentProduct } from './intentApi'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function formatTonnes(kg: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(kg / 1000)
}

export function BindIntentModal({
  intent, monthsWithData, totalQtyKg, onClose, onBound, notifySuccess, notifyError,
}: {
  intent: IntentProduct
  /** Client-side preview inputs — see this file's header. */
  monthsWithData: number
  totalQtyKg: number
  onClose: () => void
  /** Called after a successful bind, before onClose. Takes no arguments —
   *  the page doesn't need to know which material was picked, only that a
   *  bind happened: mrp-api's bind endpoint already moved the data
   *  server-side, so the page's only job is to refetch and remount its
   *  grid (see SalesForecastPage's handleIntentBound for why a remount,
   *  not a client-side patch, is the only correct way to reflect this). */
  onBound: () => void
  notifySuccess: (message: string) => void
  notifyError: (message: string) => void
}) {
  const [material, setMaterial] = useState<MaterialOption | null>(null)
  const [pickError, setPickError] = useState<string | undefined>(undefined)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!material) {
      setPickError('Select a material code.')
      return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const result = await intentApi.bind(intent.id, material.code)
      notifySuccess(
        `Bound ${intent.name} to ${material.code} — moved ${result.moved_months} month(s), `
        + `${formatTonnes(Number(result.moved_qty))} t.`,
      )
      onBound()
      onClose()
    } catch (err) {
      // Verbatim: the 409 body here is exactly what decision D11 wants a
      // human to read (e.g. "<code> already has forecast rows — merge them
      // by hand first") — errMsg/ApiError already carry FastAPI's `detail`
      // through as-is (see lib/api.ts's detailToMessage), so this is not a
      // generic "bind failed" fallback overwriting it.
      const msg = errMsg(err, 'Could not bind this intent product — please retry.')
      setSubmitError(msg)
      notifyError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-4">
      <div role="dialog" aria-modal="true" aria-label={`Bind ${intent.name} to a material code`} className="w-full max-w-md rounded-xl border border-neutral-200 bg-white shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-neutral-200">
          <div>
            <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-900">
              <Link2 className="h-4 w-4 text-primary-600" /> Bind to Material Code
            </h2>
            <p className="font-mono text-xs text-neutral-500">{intent.code} · {intent.name}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-m-2.5 flex min-h-[44px] min-w-[44px] items-center justify-center text-neutral-400 hover:text-neutral-600"
          >
            <XIcon className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="px-5 py-4 space-y-4">
            <FormField label="Material code" required error={pickError}>
              <MaterialPicker
                value={material ? (material.name ? `${material.code} — ${material.name}` : material.code) : ''}
                onSelect={(m) => { setMaterial(m); setPickError(undefined) }}
                onClear={() => setMaterial(null)}
                hasError={!!pickError}
                disabled={submitting}
                placeholder="Search finished goods…"
              />
            </FormField>

            {material && (
              <p role="status" className="rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-800">
                This will move <strong>{monthsWithData}</strong> month{monthsWithData === 1 ? '' : 's'} /{' '}
                <strong>{formatTonnes(totalQtyKg)} t</strong> to <strong>{material.code}</strong>.
              </p>
            )}

            {submitError && (
              <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                <AlertTriangle className="h-4 w-4 shrink-0" /> {submitError}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2 px-5 py-4 border-t border-neutral-200">
            <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={submitting}>Cancel</Button>
            <Button type="submit" size="sm" disabled={submitting || !material}>
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Confirm
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  )
}
