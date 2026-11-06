import { useState, type ChangeEvent } from 'react'
import { Plus, Pencil, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useDepartments } from '@/hooks/useDepartments'
import { useCostCenters } from '@/hooks/useCostCenters'
import { useVendors } from '@/hooks/useVendors'
import { useFreightCarriersAll, useSaveCarrier } from '@/hooks/useFreight'
import type { FreightCarrier } from '@/services/freight'
import { CostCenterSelect, Modal, SELECT_CLS, TH_CLS, errorMessage } from './freightUi'
import { SortTh, useSortableRows } from '@uniops/shell'

/** Couriers paid through Freight. Doubles as the whitelist: only a vendor set
 *  up here can be paid by a Freight PA (design §1.1). epms.freight.admin only. */
export function CarriersTab() {
  const { data: carrierData, isLoading } = useFreightCarriersAll()
  const { data: deptData } = useDepartments()
  const { data: costCenters = [] } = useCostCenters({ active_only: true })
  const departments = deptData?.items ?? []
  const [editing, setEditing] = useState<FreightCarrier | 'new' | null>(null)
  const ccLabel = (id: string | null) => {
    const c = costCenters.find((x) => x.id === id)
    return c ? `${c.code} — ${c.name}` : '—'
  }
  // The carrier list is loaded whole, so sorting the rows here is exact.
  const { rows: carriers, sort, onSort } = useSortableRows(carrierData, {
    code: (c) => c.code,
    name: (c) => c.name,
    vendor_name: (c) => c.vendor_name,
    tracking_pattern: (c) => c.tracking_pattern,
    default_cost_center: (c) => (c.default_cost_center_id ? ccLabel(c.default_cost_center_id) : null),
    status: (c) => (c.is_active ? 'Active' : 'Inactive'),
  })

  return (
    <div className="rounded-xl border border-neutral-200 bg-white shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-neutral-100 px-5 py-4">
        <p className="text-sm text-neutral-500">
          Only these vendors can be paid by a Freight PA. Charges without a tracking number
          (pickups, late fees) go to the carrier&apos;s default cost center.
        </p>
        <Button onClick={() => setEditing('new')} className="gap-2 shrink-0">
          <Plus className="h-4 w-4" /> Add Carrier
        </Button>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-neutral-300" /></div>
      ) : carriers.length === 0 ? (
        <p className="py-12 text-center text-sm text-neutral-400">No carriers yet. Add UPS, FedEx, Purolator…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50">
              <tr>
                <SortTh label="Code" sortKey="code" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Name" sortKey="name" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Tracking pattern" sortKey="tracking_pattern" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Default cost center" sortKey="default_cost_center" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className={TH_CLS} />
                <th className={TH_CLS} />
              </tr>
            </thead>
            <tbody>
              {carriers.map((c) => (
                <tr key={c.id} className="border-t border-neutral-100">
                  <td className="px-3 py-2.5 font-medium text-neutral-900">{c.code}</td>
                  <td className="px-3 py-2.5">{c.name}</td>
                  <td className="px-3 py-2.5 text-neutral-600">{c.vendor_name ?? '—'}</td>
                  <td className="px-3 py-2.5 font-mono text-xs text-neutral-600">{c.tracking_pattern ?? '—'}</td>
                  <td className="px-3 py-2.5 text-neutral-600">{ccLabel(c.default_cost_center_id)}</td>
                  <td className="px-3 py-2.5">
                    <span className={c.is_active ? 'text-success-700' : 'text-neutral-400'}>{c.is_active ? 'Active' : 'Inactive'}</span>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <button onClick={() => setEditing(c)} className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-800">
                      <Pencil className="h-3.5 w-3.5" /> Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <CarrierDialog
          carrier={editing === 'new' ? null : editing}
          costCenters={costCenters}
          departments={departments}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

function CarrierDialog({ carrier, costCenters, departments, onClose }: {
  carrier: FreightCarrier | null
  costCenters: Parameters<typeof CostCenterSelect>[0]['costCenters']
  departments: Parameters<typeof CostCenterSelect>[0]['departments']
  onClose: () => void
}) {
  const isEdit = !!carrier
  const [vendorSearch, setVendorSearch] = useState('')
  const { data: vendors } = useVendors({ search: vendorSearch || undefined, active_only: true })
  const [vendorId, setVendorId] = useState(carrier?.vendor_id ?? '')
  const [code, setCode] = useState(carrier?.code ?? '')
  const [name, setName] = useState(carrier?.name ?? '')
  const [pattern, setPattern] = useState(carrier?.tracking_pattern ?? '')
  const [defaultCc, setDefaultCc] = useState(carrier?.default_cost_center_id ?? '')
  const [active, setActive] = useState(carrier?.is_active ?? true)
  const [error, setError] = useState<string | null>(null)
  const save = useSaveCarrier()

  const submit = async () => {
    if (save.isPending) return
    if (!vendorId || !code.trim() || !name.trim()) { setError('Vendor, code and name are required.'); return }
    setError(null)
    try {
      await save.mutateAsync({ id: carrier?.id, body: {
        vendor_id: vendorId, code: code.trim().toUpperCase(), name: name.trim(),
        tracking_pattern: pattern.trim() || null, default_cost_center_id: defaultCc || null,
        is_active: active,
      } })
      onClose()
    } catch (e) {
      setError(errorMessage(e, 'Could not save the carrier'))
    }
  }

  return (
    <Modal title={isEdit ? `Edit ${carrier!.name}` : 'Add Carrier'} subtitle="A courier paid through Freight" onClose={onClose}>
      <div className="flex flex-col gap-4">
        {!isEdit && (
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Vendor *</label>
            <Input value={vendorSearch} placeholder="Search vendor name…"
              onChange={(e: ChangeEvent<HTMLInputElement>) => setVendorSearch(e.target.value)} />
            <select value={vendorId} onChange={(e) => setVendorId(e.target.value)} className={SELECT_CLS}>
              <option value="">Select vendor…</option>
              {(vendors?.items ?? []).slice(0, 200).map((v) => <option key={v.id} value={v.id}>{v.name} ({v.code})</option>)}
            </select>
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Code *</label>
            <Input value={code} disabled={isEdit} placeholder="UPS"
              onChange={(e: ChangeEvent<HTMLInputElement>) => setCode(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Name *</label>
            <Input value={name} placeholder="UPS"
              onChange={(e: ChangeEvent<HTMLInputElement>) => setName(e.target.value)} />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Tracking number pattern</label>
          <Input value={pattern} className="font-mono" placeholder="e.g. 1Z[0-9A-Z]{16} (optional)"
            onChange={(e: ChangeEvent<HTMLInputElement>) => setPattern(e.target.value)} />
          <p className="text-xs text-neutral-400">A regular expression. Tracking numbers that do not match are refused when registered.</p>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Default cost center</label>
          <CostCenterSelect value={defaultCc} onChange={setDefaultCc} costCenters={costCenters}
            departments={departments} placeholder="None — assign by hand" />
          <p className="text-xs text-neutral-400">For pickups and fees that have no tracking number.</p>
        </div>
        {isEdit && (
          <label className="flex items-center gap-2 text-sm text-neutral-700">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Active
          </label>
        )}
        {error && <p className="rounded-lg bg-danger-50 px-3 py-2 text-sm text-danger-700">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={submit} disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    </Modal>
  )
}
