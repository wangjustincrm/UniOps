import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Search, Loader2, Package, Pencil, Ban, ScanLine } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Pagination } from '@/components/ui/Pagination'
import { cn, formatDate } from '@/lib/utils'
import { useDepartments } from '@/hooks/useDepartments'
import { useCostCenters } from '@/hooks/useCostCenters'
import {
  useFreightCarriers, useFreightShipments, useCreateShipment, useUpdateShipment, useVoidShipment,
} from '@/hooks/useFreight'
import {
  freightService, readWaybill,
  type DefaultCostCenter, type Direction, type FreightShipment, type ShipmentStatus,
} from '@/services/freight'
import {
  CostCenterSelect, Modal, SELECT_CLS, ShipmentStatusBadge, TH_CLS, daysSince, errorMessage,
} from './freightUi'

const AGEING_DAYS = 60

export function ShipmentsTab({ canManage }: { canManage: boolean }) {
  const [status, setStatus] = useState<ShipmentStatus | 'all'>('pending')
  const [carrierId, setCarrierId] = useState('')
  const [search, setSearch] = useState('')
  const [mineOnly, setMineOnly] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [dialog, setDialog] = useState<{ mode: 'create' } | { mode: 'edit'; shipment: FreightShipment } | null>(null)

  const { data: carriers = [] } = useFreightCarriers()
  const { data, isPending } = useFreightShipments({
    status: status === 'all' ? undefined : status,
    carrier_id: carrierId || undefined,
    search: search.trim() || undefined,
    mine: canManage && mineOnly ? true : undefined,
    page,
    page_size: pageSize,
  })
  const rows = data?.items ?? []
  const total = data?.total ?? 0

  const voidShipment = useVoidShipment()
  const handleVoid = (s: FreightShipment) => {
    if (voidShipment.isPending) return
    if (!window.confirm(`Void shipment ${s.tracking_no}? It will no longer be matched to any invoice.`)) return
    voidShipment.mutate(s.id, { onError: (e) => alert(errorMessage(e, 'Could not void the shipment')) })
  }

  return (
    <>
      <div className="rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-neutral-200 px-5 py-3">
          <div className="relative flex-1 min-w-48">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
            <Input
              placeholder="Search tracking number…"
              value={search}
              onChange={(e: ChangeEvent<HTMLInputElement>) => { setSearch(e.target.value); setPage(1) }}
              className="pl-9"
            />
          </div>
          <select value={status} onChange={(e) => { setStatus(e.target.value as ShipmentStatus | 'all'); setPage(1) }} className={SELECT_CLS}>
            <option value="pending">Pending</option>
            <option value="billed">Billed</option>
            <option value="void">Void</option>
            <option value="all">All Statuses</option>
          </select>
          <select value={carrierId} onChange={(e) => { setCarrierId(e.target.value); setPage(1) }} className={SELECT_CLS}>
            <option value="">All Carriers</option>
            {carriers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {canManage && (
            <label className="flex items-center gap-2 text-sm text-neutral-600">
              <input type="checkbox" checked={mineOnly} onChange={(e) => { setMineOnly(e.target.checked); setPage(1) }} />
              Mine only
            </label>
          )}
          <span className="text-sm text-neutral-400">
            {isPending ? 'Loading…' : `${total} result${total !== 1 ? 's' : ''}`}
          </span>
          <Button onClick={() => setDialog({ mode: 'create' })} className="gap-2 ml-auto">
            <Plus className="h-4 w-4" /> New Shipment
          </Button>
        </div>

        {isPending ? (
          <div className="flex flex-col items-center justify-center py-16">
            <Loader2 className="h-8 w-8 text-neutral-300 mb-3 animate-spin" />
            <p className="text-sm text-neutral-400">Loading shipments…</p>
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <Package className="h-10 w-10 text-neutral-200 mb-3" />
            <p className="text-sm font-medium text-neutral-500">No shipments found</p>
            <p className="text-xs text-neutral-400 mt-1">Register a shipment when you hand a parcel to the courier</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-100 bg-neutral-50">
                  {['Carrier', 'Tracking No', 'Direction', 'Department / Cost Center', 'Registered By', 'Registered', 'Status', 'Invoice', ''].map((h) => (
                    <th key={h} className={TH_CLS}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => {
                  const age = daysSince(s.created_at)
                  const stale = s.status === 'pending' && age > AGEING_DAYS
                  return (
                    <tr key={s.id} className={cn('border-b border-neutral-100', stale ? 'bg-danger-50/60' : 'bg-white')}>
                      <td className="px-3 py-2.5 font-medium text-neutral-800">{s.carrier_code ?? '—'}</td>
                      <td className="px-3 py-2.5">
                        <span className="font-mono text-xs text-neutral-800">{s.tracking_no}</span>
                        {s.source === 'from_invoice' && (
                          <div className="text-[10px] text-neutral-400">Added from invoice</div>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-neutral-600 capitalize">{s.direction}</td>
                      <td className="px-3 py-2.5 text-xs">
                        <div className="text-neutral-800">{s.department_name ?? '—'}</div>
                        <div className="text-neutral-500">
                          {s.cost_center_code ? `${s.cost_center_code} — ${s.cost_center_name ?? ''}` : '—'}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-xs text-neutral-600">{s.created_by_name ?? '—'}</td>
                      <td className="px-3 py-2.5 text-xs text-neutral-500 whitespace-nowrap">
                        {formatDate(s.created_at)}
                        {stale && <div className="text-[10px] font-medium text-danger-600">{age} days, not billed yet</div>}
                      </td>
                      <td className="px-3 py-2.5"><ShipmentStatusBadge status={s.status} /></td>
                      <td className="px-3 py-2.5 text-xs">
                        {s.invoice_id ? (
                          <Link to={`/invoices/${s.invoice_id}`} className="font-mono text-primary-700 hover:underline">
                            {s.invoice_number ?? 'Invoice'}
                          </Link>
                        ) : <span className="text-neutral-300">—</span>}
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap text-right">
                        {s.status === 'pending' && (
                          <div className="inline-flex gap-1">
                            <button
                              onClick={() => setDialog({ mode: 'edit', shipment: s })}
                              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-primary-700 hover:bg-primary-50"
                            >
                              <Pencil className="h-3.5 w-3.5" /> Edit
                            </button>
                            <button
                              onClick={() => handleVoid(s)}
                              disabled={voidShipment.isPending}
                              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-danger-600 hover:bg-danger-50 disabled:opacity-40"
                            >
                              <Ban className="h-3.5 w-3.5" /> Void
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        <Pagination page={page} pageSize={pageSize} total={total} onPageChange={setPage} onPageSizeChange={(s) => { setPageSize(s); setPage(1) }} />
      </div>

      {dialog && (
        <ShipmentDialog
          key={dialog.mode === 'edit' ? dialog.shipment.id : 'new'}
          shipment={dialog.mode === 'edit' ? dialog.shipment : null}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  )
}

// ─── New / edit shipment ──────────────────────────────────────────────────────

function ShipmentDialog({ shipment, onClose }: { shipment: FreightShipment | null; onClose: () => void }) {
  const isEdit = !!shipment
  const { data: carriers = [] } = useFreightCarriers()
  const { data: deptData } = useDepartments()
  const departments = (deptData?.items ?? []).filter((d) => d.is_active || d.id === shipment?.department_id)
  const { data: costCenters = [] } = useCostCenters({ active_only: true })

  const [carrierId, setCarrierId] = useState(shipment?.carrier_id ?? '')
  const [tracking, setTracking] = useState(shipment?.tracking_no ?? '')
  const [departmentId, setDepartmentId] = useState(shipment?.department_id ?? '')
  const [costCenterId, setCostCenterId] = useState(shipment?.cost_center_id ?? '')
  const [direction, setDirection] = useState<Direction>(shipment?.direction ?? 'outbound')
  const [note, setNote] = useState(shipment?.note ?? '')
  const [defaultInfo, setDefaultInfo] = useState<DefaultCostCenter | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [savedCount, setSavedCount] = useState(0)

  const [scanning, setScanning] = useState(false)
  const [scanMsg, setScanMsg] = useState<string | null>(null)
  const [scanChoices, setScanChoices] = useState<string[]>([])
  const scanInput = useRef<HTMLInputElement>(null)
  const trackingInput = useRef<HTMLInputElement>(null)

  const createShipment = useCreateShipment()
  const updateShipment = useUpdateShipment()
  const busy = createShipment.isPending || updateShipment.isPending

  // Latest department the user picked — a slow default-cost-center response for
  // a department they have since changed away from must not overwrite anything.
  const deptRef = useRef(departmentId)

  const loadDefault = (deptId: string, applyDefault: boolean) => {
    deptRef.current = deptId
    setDefaultInfo(null)
    if (!deptId) return
    freightService.defaultCostCenter(deptId)
      .then((r) => {
        if (deptRef.current !== deptId) return
        setDefaultInfo(r)
        if (applyDefault) setCostCenterId(r.cost_center_id ?? '')
      })
      .catch(() => { /* no default — the user picks */ })
  }

  // Edit: load the budget info for the shipment's own department without
  // replacing the cost center it already has.
  useEffect(() => {
    if (shipment?.department_id) loadDefault(shipment.department_id, false)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const changeDepartment = (id: string) => {
    setDepartmentId(id)
    setCostCenterId('')
    loadDefault(id, true)
  }

  const handleScan = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || scanning) return
    setScanning(true)
    setScanMsg(null)
    setScanChoices([])
    const res = await readWaybill(file)
    setScanning(false)
    if (!res.ok) {
      setScanMsg(res.kind === 'service'
        ? 'The AI reading service is unavailable right now. Type the tracking number instead.'
        : `Could not read the waybill (${res.error}). Type the tracking number instead.`)
      return
    }
    const nums = res.data.tracking_numbers
    if (!carrierId && res.data.carrier) {
      const hit = carriers.find((c) => c.code.toUpperCase() === res.data.carrier?.toUpperCase())
      if (hit) setCarrierId(hit.id)
    }
    if (nums.length === 0) setScanMsg('No tracking number found on that image. Type it instead.')
    else if (nums.length === 1) setTracking(nums[0])
    else { setTracking(nums[0]); setScanChoices(nums) }
  }

  const ccOutsideBudget =
    !!costCenterId && !!defaultInfo && defaultInfo.budgeted_cost_center_ids.length > 0 &&
    !defaultInfo.budgeted_cost_center_ids.includes(costCenterId)

  const save = async (addAnother: boolean) => {
    if (busy) return
    setError(null)
    if (!carrierId) return setError('Choose the carrier.')
    if (!tracking.trim()) return setError('Enter the tracking number.')
    if (!costCenterId) return setError('Choose the cost center that pays for this shipment.')
    if (ccOutsideBudget && !window.confirm('This cost center has no freight budget — continue?')) return
    try {
      if (isEdit && shipment) {
        await updateShipment.mutateAsync({
          id: shipment.id,
          body: {
            tracking_no: tracking.trim() !== shipment.tracking_no ? tracking.trim() : undefined,
            cost_center_id: costCenterId !== shipment.cost_center_id ? costCenterId : undefined,
            direction: direction !== shipment.direction ? direction : undefined,
            note: (note.trim() || null) !== (shipment.note ?? null) ? (note.trim() || null) : undefined,
          },
        })
        onClose()
        return
      }
      await createShipment.mutateAsync({
        carrier_id: carrierId, tracking_no: tracking.trim(), cost_center_id: costCenterId,
        direction, note: note.trim() || null,
      })
      if (addAnother) {
        // Keep carrier / department / cost center / direction — the usual case
        // is several parcels to the same place at once.
        setSavedCount((n) => n + 1)
        setTracking('')
        setNote('')
        setScanChoices([])
        setScanMsg(null)
        trackingInput.current?.focus()
      } else {
        onClose()
      }
    } catch (e) {
      setError(errorMessage(e, 'Could not save the shipment'))
    }
  }

  const activeCarriers = carriers.filter((c) => c.is_active || c.id === carrierId)

  return (
    <Modal title={isEdit ? 'Edit Shipment' : 'New Shipment'} subtitle={isEdit ? shipment?.tracking_no : 'Register a parcel handed to the courier'} onClose={onClose}>
      <div className="flex flex-col gap-4">
        {savedCount > 0 && (
          <div className="rounded-lg border border-success-200 bg-success-50 px-3 py-2 text-xs text-success-700">
            {savedCount} shipment{savedCount !== 1 ? 's' : ''} saved. Enter the next one.
          </div>
        )}

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Carrier *</label>
          <select value={carrierId} onChange={(e) => setCarrierId(e.target.value)} disabled={isEdit} className={SELECT_CLS}>
            <option value="">Select carrier…</option>
            {activeCarriers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Tracking number *</label>
          <div className="flex gap-2">
            <Input ref={trackingInput} value={tracking} onChange={(e: ChangeEvent<HTMLInputElement>) => setTracking(e.target.value)}
              placeholder="e.g. 1Z999AA10123456784" className="font-mono flex-1" autoFocus />
            <input ref={scanInput} type="file" accept="image/*,application/pdf" className="sr-only" onChange={handleScan} />
            <Button type="button" variant="secondary" onClick={() => scanInput.current?.click()} disabled={scanning} className="gap-1.5 shrink-0">
              {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanLine className="h-4 w-4" />}
              {scanning ? 'Reading…' : 'Scan waybill'}
            </Button>
          </div>
          {scanChoices.length > 1 && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-neutral-500">Several numbers found — pick one:</span>
              {scanChoices.map((n) => (
                <button key={n} type="button" onClick={() => setTracking(n)}
                  className={cn('rounded-md border px-2 py-0.5 font-mono',
                    tracking === n ? 'border-primary-600 bg-primary-50 text-primary-700' : 'border-neutral-200 text-neutral-600 hover:bg-neutral-50')}>
                  {n}
                </button>
              ))}
            </div>
          )}
          {scanMsg && <p className="text-xs text-warning-600">{scanMsg}</p>}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Department *</label>
            <select value={departmentId} onChange={(e) => changeDepartment(e.target.value)} className={SELECT_CLS}>
              <option value="">Select department…</option>
              {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-neutral-700">Cost center *</label>
            <CostCenterSelect
              value={costCenterId} onChange={setCostCenterId}
              costCenters={costCenters} departments={departments}
              departmentId={departmentId || '__none__'} disabled={!departmentId}
            />
          </div>
        </div>
        {defaultInfo?.reason === 'freight_budget' && costCenterId === defaultInfo.cost_center_id && (
          <p className="-mt-2 text-xs text-neutral-500">Default: the department's freight budget cost center</p>
        )}
        {ccOutsideBudget && (
          <p className="-mt-2 text-xs text-warning-600">This cost center has no freight budget this year.</p>
        )}

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Direction</label>
          <div className="flex gap-4 text-sm text-neutral-700">
            {(['outbound', 'inbound'] as const).map((d) => (
              <label key={d} className="flex items-center gap-1.5">
                <input type="radio" name="freight-direction" checked={direction === d} onChange={() => setDirection(d)} />
                {d === 'outbound' ? 'Outbound (we send)' : 'Inbound (sent to us, we pay)'}
              </label>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-neutral-700">Note</label>
          <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="What was sent, to whom (optional)"
            className="w-full resize-none rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600" />
        </div>

        {error && <p className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-xs text-danger-600">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose}>{savedCount > 0 ? 'Done' : 'Cancel'}</Button>
          {!isEdit && (
            <Button variant="secondary" size="sm" onClick={() => save(true)} disabled={busy}>
              Save &amp; add another
            </Button>
          )}
          <Button size="sm" onClick={() => save(false)} disabled={busy}>
            {busy ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
