import { useState, type ChangeEvent } from 'react'
import { Plus, Pencil, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useDepartments } from '@/hooks/useDepartments'
import { useCostCenters } from '@/hooks/useCostCenters'
import { useVendors } from '@/hooks/useVendors'
import { useFreightCarriersAll, useFreightSettings, useSaveCarrier, useSaveFreightSettings } from '@/hooks/useFreight'
import type { FreightCarrier } from '@/services/freight'
import { CostCenterSelect, Modal, SELECT_CLS, TH_CLS, errorMessage } from './freightUi'
import { SortTh, useSortableRows, Checkbox, Select, TBody, THead, Table, Td, Th, Tr } from '@uniops/shell'

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
    <div className="flex flex-col gap-4">
    <div className="rounded-xl border border-line bg-surface shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-line-subtle px-5 py-4">
        <p className="text-sm text-fg-muted">
          Only these vendors can be paid by a Freight PA. Charges without a tracking number
          (pickups, late fees) go to the carrier&apos;s default cost center.
        </p>
        <Button onClick={() => setEditing('new')} className="gap-2 shrink-0">
          <Plus className="h-4 w-4" /> Add Carrier
        </Button>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-fg-faint" /></div>
      ) : carriers.length === 0 ? (
        <p className="py-12 text-center text-sm text-fg-muted">No carriers yet. Add UPS, FedEx, Purolator…</p>
      ) : (
        <div className="overflow-x-auto">
          <Table density="compact">
            <THead>
              <Tr>
                <SortTh label="Code" sortKey="code" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Name" sortKey="name" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Tracking pattern" sortKey="tracking_pattern" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Default cost center" sortKey="default_cost_center" sort={sort} onSort={onSort} className={TH_CLS} />
                <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className={TH_CLS} />
                <Th className={TH_CLS} />
              </Tr>
            </THead>
            <TBody>
              {carriers.map((c) => (
                <Tr key={c.id}>
                  <Td className="font-medium text-fg">{c.code}</Td>
                  <Td>{c.name}</Td>
                  <Td className="text-fg-secondary">{c.vendor_name ?? '—'}</Td>
                  <Td className="font-mono text-xs text-fg-secondary">{c.tracking_pattern ?? '—'}</Td>
                  <Td className="text-fg-secondary">{ccLabel(c.default_cost_center_id)}</Td>
                  <Td>
                    <span className={c.is_active ? 'text-success-fg' : 'text-fg-muted'}>{c.is_active ? 'Active' : 'Inactive'}</span>
                  </Td>
                  <Td align="right">
                    <Button variant="link" onClick={() => setEditing(c)} className="text-xs">
                      <Pencil className="h-3.5 w-3.5" /> Edit
                    </Button>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
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
    <ProjectFreightSetting costCenters={costCenters} departments={departments} />
    </div>
  )
}

/** The cost center pre-filled on a courier invoice line that bills a
 *  "Project related" shipment (FRT-019); the matcher confirms or changes it. */
function ProjectFreightSetting({ costCenters, departments }: {
  costCenters: Parameters<typeof CostCenterSelect>[0]['costCenters']
  departments: Parameters<typeof CostCenterSelect>[0]['departments']
}) {
  const { data: settings, isPending } = useFreightSettings()
  const save = useSaveFreightSettings()
  const [error, setError] = useState<string | null>(null)
  const change = (id: string) => {
    if (save.isPending) return
    setError(null)
    save.mutate(id || null, { onError: (e) => setError(errorMessage(e, 'Could not save the setting')) })
  }
  return (
    <div className="rounded-xl border border-line bg-surface px-5 py-4 shadow-sm">
      <h3 className="text-sm font-semibold text-fg">Project freight</h3>
      <p className="mt-0.5 text-xs text-fg-muted">
        A shipment registered as <em>Project related</em> has no cost center. When the carrier&apos;s invoice is
        matched, its line is pre-filled with this cost center and the person matching confirms or changes it.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="text-xs font-medium text-fg-secondary">Default cost center</label>
        {isPending ? <Loader2 className="h-4 w-4 animate-spin text-fg-faint" /> : (
          <CostCenterSelect
            value={settings?.project_cost_center_id ?? ''} onChange={change}
            costCenters={costCenters} departments={departments}
            disabled={save.isPending} placeholder="None — the matcher picks one"
            className={`${SELECT_CLS} min-w-72`}
          />
        )}
        {save.isPending && <span className="text-xs text-fg-muted">Saving…</span>}
      </div>
      {error && <p className="mt-2 text-xs text-danger-fg">{error}</p>}
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
            <label className="text-xs font-medium text-fg-secondary">Vendor *</label>
            <Input value={vendorSearch} placeholder="Search vendor name…"
              onChange={(e: ChangeEvent<HTMLInputElement>) => setVendorSearch(e.target.value)} />
            <Select value={vendorId} onChange={(e) => setVendorId(e.target.value)}>
              <option value="">Select vendor…</option>
              {(vendors?.items ?? []).slice(0, 200).map((v) => <option key={v.id} value={v.id}>{v.name} ({v.code})</option>)}
            </Select>
          </div>
        )}
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Code *</label>
            <Input value={code} disabled={isEdit} placeholder="UPS"
              onChange={(e: ChangeEvent<HTMLInputElement>) => setCode(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-fg-secondary">Name *</label>
            <Input value={name} placeholder="UPS"
              onChange={(e: ChangeEvent<HTMLInputElement>) => setName(e.target.value)} />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Tracking number pattern</label>
          <Input value={pattern} className="font-mono" placeholder="e.g. 1Z[0-9A-Z]{16} (optional)"
            onChange={(e: ChangeEvent<HTMLInputElement>) => setPattern(e.target.value)} />
          <p className="text-xs text-fg-muted">A regular expression. Tracking numbers that do not match are refused when registered.</p>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-fg-secondary">Default cost center</label>
          <CostCenterSelect value={defaultCc} onChange={setDefaultCc} costCenters={costCenters}
            departments={departments} placeholder="None — assign by hand" />
          <p className="text-xs text-fg-muted">For pickups and fees that have no tracking number.</p>
        </div>
        {isEdit && (
          <label className="flex items-center gap-2 text-sm text-fg-secondary">
            <Checkbox checked={active} onChange={(e) => setActive(e.target.checked)} /> Active
          </label>
        )}
        {error && <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={submit} disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    </Modal>
  )
}
