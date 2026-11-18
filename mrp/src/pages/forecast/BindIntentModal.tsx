// "Bind to material code" — attaches an intent product's placeholder
// INTENT-xxxxxxxx forecast rows to a real ERP material code once one
// exists. Presentational only (same split as GenerateOutlookModal.tsx):
// the actual `POST /intent-products/{id}/bind` call — and everything that
// must happen immediately around it (cancel/flush any pending autosave,
// re-check dirtiness, refetch + remount the grid afterward) — lives in
// SalesForecastPage's `handleConfirmBind`. That state (`dirtyCells`,
// `saveTimer`, `flushRef`) only exists on the page, and fix round 2's
// Critical 3 needs it guarded at the exact moment the bind call fires, not
// one render earlier when this modal merely opened — see that function's
// own header comment (SalesForecastPage.tsx) for the full argument this
// split exists to support.
//
// There is no dry-run/preview endpoint, so the "this will move N months /
// X t" line below is a client-side estimate the page computes from its own
// `committed` cell map for this intent code, passed in as
// `monthsWithData`/`totalQtyKg` — see task-5-report.md for why that's an
// accepted, documented gap rather than a second network round trip.
import { useState, type FormEvent } from 'react'
import { AlertTriangle, Link2, Loader2 } from 'lucide-react'
import { Button, FormField, Modal } from '@uniops/shell'
import { MaterialPicker } from '@/pages/consignment/MaterialPicker'
import type { MaterialOption } from '@/lib/materials'
import type { IntentProduct } from './intentApi'

function formatTonnes(kg: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(kg / 1000)
}

export function BindIntentModal({
  intent, monthsWithData, totalQtyKg, busy, error, onClose, onConfirm,
}: {
  intent: IntentProduct
  /** Client-side preview inputs — see this file's header. */
  monthsWithData: number
  totalQtyKg: number
  /** True while the page's handleConfirmBind is running (guard checks,
   *  the bind call itself, and the post-bind refetch+remount) — the modal
   *  stays open and busy through all of it, not just the network call, so
   *  the grid never hands editing back to a soon-to-be-replaced instance
   *  mid-flight (see handleConfirmBind's own comment). */
  busy: boolean
  error: string | null
  onClose: () => void
  onConfirm: (materialCode: string) => void
}) {
  const [material, setMaterial] = useState<MaterialOption | null>(null)
  const [pickError, setPickError] = useState<string | undefined>(undefined)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!material) {
      setPickError('Select a material code.')
      return
    }
    onConfirm(material.code)
  }

  return (
    <Modal
      open
      onClose={onClose}
      busy={busy}
      title="Bind to Material Code"
      icon={<Link2 className="h-4 w-4" />}
      description={<span className="font-mono text-xs">{intent.code} · {intent.name}</span>}
      closeOnEscape={false}
      size="full"
      className="max-w-md"
      bodyClassName="p-0"
    >
        <form onSubmit={handleSubmit}>
          <div className="px-5 py-4 space-y-4">
            <FormField label="Material code" required error={pickError}>
              <MaterialPicker
                value={material ? (material.name ? `${material.code} — ${material.name}` : material.code) : ''}
                onSelect={(m) => { setMaterial(m); setPickError(undefined) }}
                onClear={() => setMaterial(null)}
                hasError={!!pickError}
                disabled={busy}
                placeholder="Search finished goods…"
              />
            </FormField>

            {material && (
              <p role="status" className="rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-800">
                This will move <strong>{monthsWithData}</strong> month{monthsWithData === 1 ? '' : 's'} /{' '}
                <strong>{formatTonnes(totalQtyKg)} t</strong> to <strong>{material.code}</strong>.
              </p>
            )}

            {error && (
              <p role="alert" className="flex items-center gap-1.5 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
                <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2 px-5 py-4 border-t border-line">
            <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
            <Button type="submit" size="sm" disabled={busy || !material}>
              {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Confirm
            </Button>
          </div>
        </form>
    </Modal>
  )
}
