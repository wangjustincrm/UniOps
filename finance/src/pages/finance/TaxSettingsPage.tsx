import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Ban } from 'lucide-react'
import { SortTh, useSortableRows, Input, Select, TBody, THead, Table, Td, Th, Tr, Button, Modal as ShellModal } from '@uniops/shell'
import { mdmApi } from '@/lib/api'
import { cn, formatDate, todayISO } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { useAuthStore } from '@/store/auth'

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
  recoverable: true, effective_from: todayISO(),
  effective_to: '', active: true,
})

const pct = (fraction: string) => `${(Number(fraction) * 100).toFixed(3).replace(/\.?0+$/, '')}%`

export default function TaxSettingsPage() {
  // Adding, editing and deactivating tax codes is System Admin only (ISS-0164);
  // mdm-api refuses everyone else. Others see the list read-only.
  const isAdmin = useAuthStore().user?.role === 'system_admin'
  const qc = useQueryClient()
  const { data, isLoading } = useQuery<ApiTaxCode[]>({
    queryKey: ['portal-tax-codes'],
    // ?all=true → every code/version, not just active-on-date (admin view)
    queryFn: () => mdmApi.get<ApiTaxCode[]>('/tax/codes?all=true'),
  })
  // Loaded whole; Edit opens a modal on the row object, so order is free.
  const { rows, sort, onSort } = useSortableRows(data, {
    code: (r) => r.code, name: (r) => r.name, type: (r) => r.tax_type, prov: (r) => r.province,
    rate: (r) => Number(r.rate), itc: (r) => (r.recoverable ? 'Yes' : 'No'),
    effective: (r) => r.effective_from, status: (r) => (r.active ? 'Active' : 'Inactive'),
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
    if (!confirm(`Deactivate tax code "${r.code}" (effective ${formatDate(r.effective_from)})? Existing documents keep their saved rate.`)) return
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
        {isAdmin ? (
          <Button size="md" onClick={openCreate}
           >
            <Plus className="h-4 w-4" />Add Tax Code
          </Button>
        ) : (
          <span className="text-sm text-fg-muted">Read-only — tax codes are maintained by System Admin.</span>
        )}
      </div>
      <div className="rounded-xl border border-line bg-surface overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-fg-muted">Loading…</div>
        ) : !data?.length ? (
          <div className="py-10 text-center text-sm text-fg-muted">No tax codes yet.</div>
        ) : (
          <Table>
            <THead>
              <Tr>
                <SortTh label="Code" sortKey="code" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="Name" sortKey="name" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="Type" sortKey="type" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="Prov" sortKey="prov" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="Rate" sortKey="rate" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="ITC" sortKey="itc" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="Effective" sortKey="effective" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-4 py-3 text-left text-xs font-medium text-fg-muted" />
                <Th />
              </Tr>
            </THead>
            <TBody>
              {rows.map((r, i) => (
                <Tr key={r.id} className={cn('border-b border-line-subtle', i === rows.length - 1 && 'border-b-0')}>
                  <Td className="font-mono text-xs text-fg-secondary">{r.code}</Td>
                  <Td className="text-fg">{r.name}</Td>
                  <Td className="text-xs text-fg-muted">{r.tax_type}</Td>
                  <Td className="text-xs text-fg-muted">{r.province ?? '—'}</Td>
                  <Td className="font-mono text-xs text-fg-secondary">{pct(r.rate)}</Td>
                  <Td className="text-xs text-fg-muted">{r.recoverable ? 'Yes' : 'No'}</Td>
                  <Td className="text-xs text-fg-muted">
                    {formatDate(r.effective_from)}{r.effective_to ? ` → ${formatDate(r.effective_to)}` : ''}
                  </Td>
                  <Td>
                    <span className={cn('rounded-full px-2 py-0.5 text-2xs font-medium', r.active ? 'bg-success-soft text-success-fg' : 'bg-surface-sunken text-fg-muted')}>
                      {r.active ? 'Active' : 'Inactive'}
                    </span>
                  </Td>
                  <Td>
                    {isAdmin && <div className="flex items-center justify-end gap-1">
                      <Button aria-label="Edit" variant="subtle" size="icon-sm" onClick={() => openEdit(r)} title="Edit"><Pencil className="h-3.5 w-3.5" /></Button>
                      {r.active && (
                        <Button aria-label="Deactivate" variant="subtle" size="icon-sm" onClick={() => handleDeactivate(r)} title="Deactivate"><Ban className="h-3.5 w-3.5" /></Button>
                      )}
                    </div>}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </div>

      {modal && (
        <ShellModal open onClose={() => setModal(null)} title={modal.mode === 'create' ? 'Add Tax Code' : 'Edit Tax Code'} closeOnEscape={false} size="full" className="max-w-md">
          <form onSubmit={handleSave} className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-fg-secondary">Code</label>
                <Input value={form.code} onChange={(e) => setForm((p) => ({ ...p, code: e.target.value }))}
                  disabled={modal.mode === 'edit'}
                  className="w-full"
                  placeholder="HST_ON" required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-fg-secondary">Type</label>
                <Select value={form.tax_type} onChange={(e) => setForm((p) => ({ ...p, tax_type: e.target.value as TaxType }))}
                  className="w-full">
                  {TAX_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </Select>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-fg-secondary">Name</label>
              <Input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                className="w-full"
                placeholder="HST 13% Ontario" required />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-fg-secondary">Province</label>
                <Input value={form.province} onChange={(e) => setForm((p) => ({ ...p, province: e.target.value }))}
                  maxLength={2}
                  className="w-full uppercase"
                  placeholder="ON (blank = federal)" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-fg-secondary">Rate (%)</label>
                <Input type="number" step="0.001" min="0" max="100" value={form.ratePct}
                  onChange={(e) => setForm((p) => ({ ...p, ratePct: e.target.value }))}
                  className="w-full"
                  placeholder="13" required />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-fg-secondary">Effective From</label>
                <Input type="date" value={form.effective_from}
                  onChange={(e) => setForm((p) => ({ ...p, effective_from: e.target.value }))}
                  disabled={modal.mode === 'edit'}
                  className="w-full"
                  required />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-fg-secondary">Effective To</label>
                <Input type="date" value={form.effective_to}
                  onChange={(e) => setForm((p) => ({ ...p, effective_to: e.target.value }))}
                  className="w-full"
                  placeholder="blank = open-ended" />
              </div>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-fg-secondary">Recoverable (ITC)</span>
              <button type="button" onClick={() => setForm((p) => ({ ...p, recoverable: !p.recoverable }))}
                className={cn('relative inline-flex h-5 w-9 items-center rounded-full transition-colors', form.recoverable ? 'bg-primary-600' : 'bg-surface-strong')}>
                <span className={cn('inline-block h-3.5 w-3.5 rounded-full bg-surface shadow transition-transform', form.recoverable ? 'translate-x-4.5' : 'translate-x-0.5')} />
              </button>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-fg-secondary">Active</span>
              <button type="button" onClick={() => setForm((p) => ({ ...p, active: !p.active }))}
                className={cn('relative inline-flex h-5 w-9 items-center rounded-full transition-colors', form.active ? 'bg-primary-600' : 'bg-surface-strong')}>
                <span className={cn('inline-block h-3.5 w-3.5 rounded-full bg-surface shadow transition-transform', form.active ? 'translate-x-4.5' : 'translate-x-0.5')} />
              </button>
            </div>
            {error && <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">{error}</p>}
            <div className="flex justify-end gap-2 border-t border-line-subtle pt-3">
              <Button variant="secondary" size="md" type="button" onClick={() => setModal(null)}>Cancel</Button>
              <Button size="md" type="submit" disabled={saving}
               >
                {saving ? 'Saving…' : 'Save Changes'}
              </Button>
            </div>
          </form>
        </ShellModal>
      )}
      </div>
    </PortalChromeLayout>
  )
}
