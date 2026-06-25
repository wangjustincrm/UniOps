import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Ban, X } from 'lucide-react'
import { mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

/**
 * Tax Settings — Finance master data (mdm-api tax_codes).
 *
 * Single source of truth for every tax-consuming module: EPMS PO/PA tax
 * dropdowns, OA expense, and invoice tax lines all read GET /tax/codes from
 * here. Rates are stored as fractions (0.13 = 13%) and shown as percentages.
 * Codes are effective-dated: a rate change is a NEW row with a later
 * effective_from, not an edit — so code + effective date are immutable on edit.
 */

const TAX_TYPES = ['GST', 'HST', 'PST', 'RST', 'QST', 'NONE'] as const
type TaxType = (typeof TAX_TYPES)[number]

interface ApiTaxCode {
  id: string
  code: string
  name: string
  tax_type: TaxType
  province: string | null
  rate: string          // Decimal arrives as string — fraction (0.13)
  recoverable: boolean
  effective_from: string
  effective_to: string | null
  active: boolean
}

interface FormState {
  code: string
  name: string
  tax_type: TaxType
  province: string
  ratePct: string       // percentage in the form (13), converted to fraction on save
  recoverable: boolean
  effective_from: string
  effective_to: string
  active: boolean
}

const emptyForm = (): FormState => ({
  code: '', name: '', tax_type: 'HST', province: '', ratePct: '',
  recoverable: true, effective_from: new Date().toISOString().slice(0, 10),
  effective_to: '', active: true,
})

const pct = (fraction: string) => `${(Number(fraction) * 100).toFixed(3).replace(/\.?0+$/, '')}%`

export default function TaxSettingsPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery<ApiTaxCode[]>({
    queryKey: ['portal-tax-codes'],
    // ?all=true → every code/version, not just active-on-date (admin view)
    queryFn: () => mdmApi.get<ApiTaxCode[]>('/tax/codes?all=true'),
  })
  const [modal, setModal] = useState<{ mode: 'create' | 'edit'; row?: ApiTaxCode } | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const openCreate = () => { setForm(emptyForm()); setError(''); setModal({ mode: 'create' }) }
  const openEdit = (r: ApiTaxCode) => {
    setForm({
      code: r.code, name: r.name, tax_type: r.tax_type, province: r.province ?? '',
      ratePct: (Number(r.rate) * 100).toString(), recoverable: r.recoverable,
      effective_from: r.effective_from, effective_to: r.effective_to ?? '', active: r.active,
    })
    setError(''); setModal({ mode: 'edit', row: r })
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault(); setError(''); setSaving(true)
    const rate = (Number(form.ratePct) / 100)
    if (Number.isNaN(rate) || rate < 0 || rate > 1) {
      setError('Rate must be a percentage between 0 and 100'); setSaving(false); return
    }
    try {
      if (modal?.mode === 'create') {
        await mdmApi.post('/tax/codes', {
          code: form.code.trim(),
          name: form.name.trim(),
          tax_type: form.tax_type,
          province: form.province.trim() ? form.province.trim().toUpperCase() : null,
          rate,
          recoverable: form.recoverable,
          effective_from: form.effective_from,
          effective_to: form.effective_to || null,
          active: form.active,
        })
      } else {
        // code + effective_from are immutable; send the editable fields only.
        await mdmApi.patch(`/tax/codes/${modal?.row?.id}`, {
          name: form.name.trim(),
          tax_type: form.tax_type,
          province: form.province.trim() ? form.province.trim().toUpperCase() : null,
          rate,
          recoverable: form.recoverable,
          effective_to: form.effective_to || null,
          active: form.active,
        })
      }
      qc.invalidateQueries({ queryKey: ['portal-tax-codes'] })
      setModal(null)
    } catch (err: any) { setError(err.message) } finally { setSaving(false) }
  }

  const handleDeactivate = async (r: ApiTaxCode) => {
    if (!confirm(`Deactivate tax code "${r.code}" (effective ${r.effective_from})? Existing documents keep their saved rate.`)) return
    try {
      await mdmApi.delete(`/tax/codes/${r.id}`)
      qc.invalidateQueries({ queryKey: ['portal-tax-codes'] })
    } catch (err: any) { alert(err.message) }
  }

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/tax"
      title="Tax Settings"
      subtitle="Canadian sales-tax master data — feeds every tax picker across EPMS (PO/PA), OA expenses, and invoice tax lines. Rates are effective-dated: change a rate by adding a new version, not editing the old one."
    >
      <div className="mx-auto max-w-6xl">
      <div className="mb-4 flex justify-end">
        <button onClick={openCreate}
          className="flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A]">
          <Plus className="h-4 w-4" />Add Tax Code
        </button>
      </div>
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
        ) : !data?.length ? (
          <div className="py-10 text-center text-sm text-neutral-400">No tax codes yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-neutral-100 bg-neutral-50">
              <tr>
                {['Code', 'Name', 'Type', 'Prov', 'Rate', 'ITC', 'Effective', 'Status', ''].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-neutral-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((r, i) => (
                <tr key={r.id} className={cn('border-b border-neutral-100', i === data.length - 1 && 'border-b-0')}>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-700">{r.code}</td>
                  <td className="px-4 py-3 text-neutral-800">{r.name}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{r.tax_type}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{r.province ?? '—'}</td>
                  <td className="px-4 py-3 font-mono text-xs text-neutral-700">{pct(r.rate)}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">{r.recoverable ? 'Yes' : 'No'}</td>
                  <td className="px-4 py-3 text-xs text-neutral-500">
                    {r.effective_from}{r.effective_to ? ` → ${r.effective_to}` : ''}
                  </td>
                  <td className="px-4 py-3">
                    <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium', r.active ? 'bg-green-50 text-green-700' : 'bg-neutral-100 text-neutral-500')}>
                      {r.active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => openEdit(r)} className="rounded p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700" title="Edit"><Pencil className="h-3.5 w-3.5" /></button>
                      {r.active && (
                        <button onClick={() => handleDeactivate(r)} className="rounded p-1.5 text-neutral-400 hover:bg-amber-50 hover:text-amber-600" title="Deactivate"><Ban className="h-3.5 w-3.5" /></button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {modal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl border border-neutral-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-4">
              <h3 className="font-semibold text-neutral-900">{modal.mode === 'create' ? 'Add Tax Code' : 'Edit Tax Code'}</h3>
              <button onClick={() => setModal(null)} className="rounded p-1 text-neutral-400 hover:bg-neutral-100"><X className="h-4 w-4" /></button>
            </div>
            <form onSubmit={handleSave} className="flex flex-col gap-4 p-5">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-600">Code</label>
                  <input value={form.code} onChange={(e) => setForm((p) => ({ ...p, code: e.target.value }))}
                    disabled={modal.mode === 'edit'}
                    className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 disabled:bg-neutral-50 disabled:text-neutral-500"
                    placeholder="HST_ON" required />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-600">Type</label>
                  <select value={form.tax_type} onChange={(e) => setForm((p) => ({ ...p, tax_type: e.target.value as TaxType }))}
                    className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400">
                    {TAX_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-neutral-600">Name</label>
                <input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                  className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
                  placeholder="HST 13% Ontario" required />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-600">Province</label>
                  <input value={form.province} onChange={(e) => setForm((p) => ({ ...p, province: e.target.value }))}
                    maxLength={2}
                    className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm uppercase focus:outline-none focus:border-primary-400"
                    placeholder="ON (blank = federal)" />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-600">Rate (%)</label>
                  <input type="number" step="0.001" min="0" max="100" value={form.ratePct}
                    onChange={(e) => setForm((p) => ({ ...p, ratePct: e.target.value }))}
                    className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
                    placeholder="13" required />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-600">Effective From</label>
                  <input type="date" value={form.effective_from}
                    onChange={(e) => setForm((p) => ({ ...p, effective_from: e.target.value }))}
                    disabled={modal.mode === 'edit'}
                    className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400 disabled:bg-neutral-50 disabled:text-neutral-500"
                    required />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-neutral-600">Effective To</label>
                  <input type="date" value={form.effective_to}
                    onChange={(e) => setForm((p) => ({ ...p, effective_to: e.target.value }))}
                    className="w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:outline-none focus:border-primary-400"
                    placeholder="blank = open-ended" />
                </div>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-neutral-700">Recoverable (ITC)</span>
                <button type="button" onClick={() => setForm((p) => ({ ...p, recoverable: !p.recoverable }))}
                  className={cn('relative inline-flex h-5 w-9 items-center rounded-full transition-colors', form.recoverable ? 'bg-primary-600' : 'bg-neutral-200')}>
                  <span className={cn('inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform', form.recoverable ? 'translate-x-4.5' : 'translate-x-0.5')} />
                </button>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-neutral-700">Active</span>
                <button type="button" onClick={() => setForm((p) => ({ ...p, active: !p.active }))}
                  className={cn('relative inline-flex h-5 w-9 items-center rounded-full transition-colors', form.active ? 'bg-primary-600' : 'bg-neutral-200')}>
                  <span className={cn('inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform', form.active ? 'translate-x-4.5' : 'translate-x-0.5')} />
                </button>
              </div>
              {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
              <div className="flex justify-end gap-2 border-t border-neutral-100 pt-3">
                <button type="button" onClick={() => setModal(null)} className="rounded-lg border border-neutral-200 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50">Cancel</button>
                <button type="submit" disabled={saving}
                  className="rounded-lg bg-[#085E5E] px-4 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-60">
                  {saving ? 'Saving…' : 'Save Changes'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
      </div>
    </PortalChromeLayout>
  )
}
