// Add / edit one supply-parameter row.
//
// Both material and supplier are PICKED, never typed: these are the two keys
// the row is stored under, and a mistyped code produces a row that resolves
// for nobody — the purchase suggestion then reports "no supplier" for a
// material somebody believes they configured.
//
// ★ The material picker runs with `finishedGoodsOnly={false}` on purpose.
// Supply parameters are for what gets BOUGHT — raw materials (CR),
// packaging (CP), semi-finished powder (CW/CS) — none of which are finished
// goods, so the default would have offered an empty list.
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Loader2, X as XIcon } from 'lucide-react'
import { Button, FormField, Input } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { MaterialPicker } from '@/pages/consignment/MaterialPicker'
import { SupplierPicker } from './SupplierPicker'
import { supplyApi, type BulkRow, type MaterialSupplier } from './supplyApi'

interface FormState {
  material_code: string
  partner_code: string
  lead_time_days: string
  moq: string
  order_multiple: string
  is_primary: boolean
  notes: string
}

function initialState(row: MaterialSupplier | null): FormState {
  if (!row) {
    return {
      material_code: '', partner_code: '', lead_time_days: '',
      moq: '', order_multiple: '', is_primary: false, notes: '',
    }
  }
  return {
    material_code: row.material_code,
    partner_code: row.partner_code,
    lead_time_days: row.lead_time_days === null ? '' : String(row.lead_time_days),
    moq: row.moq ?? '',
    order_multiple: row.order_multiple ?? '',
    is_primary: row.is_primary,
    notes: row.notes ?? '',
  }
}

function optionalNumber(raw: string): string | null {
  return raw.trim() === '' ? null : raw.trim()
}

export function SupplyRowDrawer({
  row, onClose, onSaved, notifyError,
}: {
  /** null = adding a new row. */
  row: MaterialSupplier | null
  onClose: () => void
  onSaved: (message: string) => void
  notifyError: (message: string) => void
}) {
  const isEdit = !!row
  const [form, setForm] = useState<FormState>(() => initialState(row))
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function validate(): string | null {
    if (!form.material_code.trim()) return 'Select a material.'
    if (!form.partner_code.trim()) return 'Select a supplier.'
    const numeric: [string, string][] = [
      ['Lead time', form.lead_time_days],
      ['Minimum order quantity', form.moq],
      ['Order multiple', form.order_multiple],
    ]
    for (const [label, raw] of numeric) {
      if (raw.trim() === '') continue
      const n = Number(raw)
      if (!Number.isFinite(n) || n < 0) return `${label} must be a number of 0 or more.`
    }
    if (form.order_multiple.trim() !== '' && Number(form.order_multiple) === 0) {
      return 'Order multiple must be greater than 0.'
    }
    return null
  }

  async function handleSubmit() {
    const problem = validate()
    if (problem) { setError(problem); return }
    setError(null)
    setSaving(true)
    try {
      const body: BulkRow = {
        material_code: form.material_code.trim(),
        partner_code: form.partner_code.trim(),
        lead_time_days: form.lead_time_days.trim() === '' ? null : Number(form.lead_time_days),
        moq: optionalNumber(form.moq),
        order_multiple: optionalNumber(form.order_multiple),
        is_primary: form.is_primary,
        notes: form.notes.trim() || null,
      }
      if (isEdit && row) {
        // The natural key is not editable: changing which material or
        // supplier a row is about makes it a different row, and silently
        // moving it would lose whatever the old pair was configured with.
        // Both pickers are locked in edit mode to say so.
        const updates = { ...body }
        delete (updates as Partial<BulkRow>).material_code
        delete (updates as Partial<BulkRow>).partner_code
        await supplyApi.update(row.id, updates)
      } else {
        await supplyApi.create(body)
      }
      onSaved(isEdit ? 'Updated.' : 'Added.')
    } catch (err) {
      // 409 = this material/supplier pair already exists, or another primary
      // is already set for the material. The backend words both cases;
      // passing it through beats a generic failure nobody can act on.
      const message = err instanceof ApiError ? err.message : 'Could not save this row.'
      setError(message)
      notifyError(message)
    } finally {
      setSaving(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[90] flex justify-end bg-black/30">
      <div className="flex h-full w-full max-w-md flex-col bg-white shadow-xl">
        <header className="flex items-center justify-between border-b border-neutral-200 px-5 py-3">
          <h2 className="text-sm font-semibold text-neutral-900">
            {isEdit ? 'Edit supply parameters' : 'Add supply parameters'}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close"
            className="text-neutral-400 hover:text-neutral-700">
            <XIcon className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <FormField label="Material" required htmlFor="supply-material"
            hint={isEdit ? 'Fixed — add a separate row for another material.' : undefined}>
            {isEdit ? (
              <Input id="supply-material" value={form.material_code} disabled />
            ) : (
              <MaterialPicker
                value={form.material_code}
                onSelect={(m) => set('material_code', m.code)}
                onClear={() => set('material_code', '')}
                disabled={saving}
                // Bought items are never finished goods — see this file's note.
                finishedGoodsOnly={false}
                placeholder="Search materials…"
              />
            )}
          </FormField>

          <FormField label="Supplier" required htmlFor="supply-partner"
            hint={isEdit ? 'Fixed — add a separate row for another supplier.' : undefined}>
            {isEdit ? (
              <Input id="supply-partner" value={form.partner_code} disabled />
            ) : (
              <SupplierPicker
                value={form.partner_code}
                onSelect={(p) => set('partner_code', p.code)}
                onClear={() => set('partner_code', '')}
                disabled={saving}
              />
            )}
          </FormField>

          <FormField label="Lead time (days)" htmlFor="supply-lead"
            hint="Days from placing the order to receiving it. Purchase suggestions work backwards from this; left blank, the suggestion says so rather than guessing.">
            <Input id="supply-lead" inputMode="numeric" value={form.lead_time_days}
              onChange={(e) => set('lead_time_days', e.target.value)} disabled={saving} />
          </FormField>

          <FormField label="Minimum order quantity" htmlFor="supply-moq"
            hint="A requirement below this is raised to it, and the suggestion shows how much was added.">
            <Input id="supply-moq" inputMode="decimal" value={form.moq}
              onChange={(e) => set('moq', e.target.value)} disabled={saving} />
          </FormField>

          <FormField label="Order multiple" htmlFor="supply-multiple"
            hint="Quantities are rounded up to a multiple of this, after the minimum is applied.">
            <Input id="supply-multiple" inputMode="decimal" value={form.order_multiple}
              onChange={(e) => set('order_multiple', e.target.value)} disabled={saving} />
          </FormField>

          <label className="flex items-start gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={form.is_primary}
              onChange={(e) => set('is_primary', e.target.checked)} disabled={saving}
              className="mt-0.5 h-4 w-4 rounded border-neutral-300" />
            <span>
              Primary supplier
              <span className="block text-xs text-neutral-500">
                Purchase suggestions use the primary supplier's lead time and order sizing.
                Only one per material.
              </span>
            </span>
          </label>

          <FormField label="Notes" htmlFor="supply-notes">
            <Input id="supply-notes" value={form.notes}
              onChange={(e) => set('notes', e.target.value)} disabled={saving} />
          </FormField>

          {error && (
            <p role="alert" className="flex items-start gap-1.5 rounded-md border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
            </p>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-neutral-200 px-5 py-3">
          <Button type="button" variant="secondary" size="sm" className="min-h-[44px]"
            onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="button" size="sm" className="min-h-[44px]"
            onClick={() => { void handleSubmit() }} disabled={saving}>
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {isEdit ? 'Save' : 'Add'}
          </Button>
        </footer>
      </div>
    </div>,
    document.body,
  )
}
