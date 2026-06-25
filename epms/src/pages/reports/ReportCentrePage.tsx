import { useState } from 'react'
import {
  FileText, ShoppingCart, PackageCheck, Receipt, CreditCard,
  PiggyBank, Store, Download, Loader2, AlertCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { downloadCsv } from '@/lib/api'
import { useVendors } from '@/hooks/useVendors'
import { useCostCenters } from '@/hooks/useCostCenters'

// ─── Shared filter state types ────────────────────────────────────────────────

interface DateRange { date_from: string; date_to: string }
type Params = Record<string, string | number | boolean | null | undefined>

// ─── Status options per domain ────────────────────────────────────────────────

const PR_STATUSES = [
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved', label: 'Approved' },
  { value: 'returned', label: 'Returned' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'paid', label: 'Paid / Closed' },
]

const PO_STATUSES = [
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved', label: 'Approved' },
  { value: 'issued', label: 'Issued' },
  { value: 'closed', label: 'Closed' },
  { value: 'cancelled', label: 'Cancelled' },
]

const GR_STATUSES = [
  { value: 'pending_ack', label: 'Pending Acknowledgement' },
  { value: 'collection_pending', label: 'Collection Pending' },
  { value: 'collected', label: 'Collected' },
  { value: 'confirmed', label: 'Confirmed' },
  { value: 'discrepancy', label: 'Discrepancy' },
]

const INVOICE_STATUSES = [
  { value: 'unmatched', label: 'Unmatched' },
  { value: 'matched', label: 'Matched' },
  { value: 'exception', label: 'Exception' },
  { value: 'approved', label: 'Approved' },
  { value: 'paid', label: 'Paid' },
]

const PA_STATUSES = [
  { value: 'draft', label: 'Draft' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'in_review', label: 'In Review' },
  { value: 'approved', label: 'Approved' },
  { value: 'processed', label: 'Processed' },
  { value: 'cancelled', label: 'Cancelled' },
]

const PO_TYPES = [
  { value: '1', label: 'Raw Mat. / Packaging' },
  { value: '2', label: 'Consumables' },
  { value: '3', label: 'Spare Parts' },
  { value: '4', label: 'Service' },
  { value: '5', label: 'Fixed Asset' },
  { value: '6', label: 'Project' },
]

// ─── Shared UI helpers ────────────────────────────────────────────────────────

const selectCls = 'h-9 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-800 focus:outline-none focus:ring-2 focus:ring-primary-600'
const inputCls  = 'h-9 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm text-neutral-800 focus:outline-none focus:ring-2 focus:ring-primary-600'

function DateRangeRow({ value, onChange }: { value: DateRange; onChange: (v: DateRange) => void }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-neutral-500">From</label>
        <input type="date" className={inputCls} value={value.date_from}
          onChange={(e) => onChange({ ...value, date_from: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-neutral-500">To</label>
        <input type="date" className={inputCls} value={value.date_to}
          onChange={(e) => onChange({ ...value, date_to: e.target.value })} />
      </div>
    </div>
  )
}

function StatusSelect({ options, value, onChange }: {
  options: { value: string; label: string }[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-neutral-500">Status</label>
      <select className={selectCls} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All Statuses</option>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  )
}

// ─── Single Report Card ───────────────────────────────────────────────────────

function ReportCard({
  icon: Icon,
  title,
  description,
  children,
  onDownload,
  loading,
  error,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  children?: React.ReactNode
  onDownload: () => void
  loading: boolean
  error: string | null
}) {
  return (
    <div className="flex flex-col rounded-2xl border border-neutral-200 bg-white shadow-[0_1px_3px_rgba(0,0,0,0.06)] overflow-hidden">
      {/* Header */}
      <div className="flex items-start gap-3 px-5 py-4 border-b border-neutral-100">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary-50">
          <Icon className="h-4.5 w-4.5 text-primary-600" />
        </div>
        <div>
          <p className="text-sm font-semibold text-neutral-900">{title}</p>
          <p className="text-xs text-neutral-500 mt-0.5">{description}</p>
        </div>
      </div>

      {/* Filters */}
      {children && (
        <div className="flex flex-col gap-3 px-5 py-4 bg-neutral-50 border-b border-neutral-100">
          {children}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between gap-3 px-5 py-3">
        {error ? (
          <span className="flex items-center gap-1.5 text-xs text-danger-600">
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />{error}
          </span>
        ) : <span />}
        <Button size="sm" onClick={onDownload} disabled={loading} className="ml-auto">
          {loading
            ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />Exporting…</>
            : <><Download className="h-3.5 w-3.5" />Export CSV</>}
        </Button>
      </div>
    </div>
  )
}

// ─── Individual report panels ─────────────────────────────────────────────────

function PrReport() {
  const [dates, setDates] = useState<DateRange>({ date_from: '', date_to: '' })
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { data: ccData } = useCostCenters()
  const costCenters = ccData ?? []
  const [costCenterId, setCostCenterId] = useState('')

  const handleDownload = async () => {
    setLoading(true); setError(null)
    try {
      const params: Params = { ...dates, status: status || undefined, cost_center_id: costCenterId || undefined }
      await downloadCsv('/reports/pr', params, `pr-report-${new Date().toISOString().slice(0,10)}.csv`)
    } catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  return (
    <ReportCard icon={FileText} title="Purchase Requisitions" description="All PRs with line items, approval history, and cost center breakdown." onDownload={handleDownload} loading={loading} error={error}>
      <DateRangeRow value={dates} onChange={setDates} />
      <div className="grid grid-cols-2 gap-2">
        <StatusSelect options={PR_STATUSES} value={status} onChange={setStatus} />
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-500">Cost Center</label>
          <select className={selectCls} value={costCenterId} onChange={(e) => setCostCenterId(e.target.value)}>
            <option value="">All Cost Centers</option>
            {costCenters.map((cc) => <option key={cc.id} value={cc.id}>{cc.code} — {cc.name}</option>)}
          </select>
        </div>
      </div>
    </ReportCard>
  )
}

function PoReport() {
  const [dates, setDates] = useState<DateRange>({ date_from: '', date_to: '' })
  const [status, setStatus] = useState('')
  const [poType, setPoType] = useState('')
  const [vendorId, setVendorId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { data: vendorData } = useVendors()
  const vendors = vendorData?.items ?? []

  const handleDownload = async () => {
    setLoading(true); setError(null)
    try {
      const params: Params = { ...dates, status: status || undefined, po_type: poType ? Number(poType) : undefined, vendor_id: vendorId || undefined }
      await downloadCsv('/reports/po', params, `po-report-${new Date().toISOString().slice(0,10)}.csv`)
    } catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  return (
    <ReportCard icon={ShoppingCart} title="Purchase Orders" description="All POs with vendor, line items, type, and status history." onDownload={handleDownload} loading={loading} error={error}>
      <DateRangeRow value={dates} onChange={setDates} />
      <div className="grid grid-cols-2 gap-2">
        <StatusSelect options={PO_STATUSES} value={status} onChange={setStatus} />
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-500">PO Type</label>
          <select className={selectCls} value={poType} onChange={(e) => setPoType(e.target.value)}>
            <option value="">All Types</option>
            {PO_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-neutral-500">Vendor</label>
        <select className={selectCls} value={vendorId} onChange={(e) => setVendorId(e.target.value)}>
          <option value="">All Vendors</option>
          {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
      </div>
    </ReportCard>
  )
}

function GrReport() {
  const [dates, setDates] = useState<DateRange>({ date_from: '', date_to: '' })
  const [status, setStatus] = useState('')
  const [grType, setGrType] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleDownload = async () => {
    setLoading(true); setError(null)
    try {
      const params: Params = { ...dates, status: status || undefined, gr_type: grType || undefined }
      await downloadCsv('/reports/gr', params, `gr-report-${new Date().toISOString().slice(0,10)}.csv`)
    } catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  return (
    <ReportCard icon={PackageCheck} title="Goods Receipts" description="All GRs with type, status, discrepancies, and collection records." onDownload={handleDownload} loading={loading} error={error}>
      <DateRangeRow value={dates} onChange={setDates} />
      <div className="grid grid-cols-2 gap-2">
        <StatusSelect options={GR_STATUSES} value={status} onChange={setStatus} />
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-500">GR Type</label>
          <select className={selectCls} value={grType} onChange={(e) => setGrType(e.target.value)}>
            <option value="">All Types</option>
            <option value="physical">Physical</option>
            <option value="service">Service</option>
          </select>
        </div>
      </div>
    </ReportCard>
  )
}

function InvoiceReport() {
  const [dates, setDates] = useState<DateRange>({ date_from: '', date_to: '' })
  const [status, setStatus] = useState('')
  const [vendorId, setVendorId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { data: vendorData } = useVendors()
  const vendors = vendorData?.items ?? []

  const handleDownload = async () => {
    setLoading(true); setError(null)
    try {
      const params: Params = { ...dates, status: status || undefined, vendor_id: vendorId || undefined }
      await downloadCsv('/reports/invoices', params, `invoice-report-${new Date().toISOString().slice(0,10)}.csv`)
    } catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  return (
    <ReportCard icon={Receipt} title="Invoices" description="All supplier invoices with matching status, amounts, and tax." onDownload={handleDownload} loading={loading} error={error}>
      <DateRangeRow value={dates} onChange={setDates} />
      <StatusSelect options={INVOICE_STATUSES} value={status} onChange={setStatus} />
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-neutral-500">Vendor</label>
        <select className={selectCls} value={vendorId} onChange={(e) => setVendorId(e.target.value)}>
          <option value="">All Vendors</option>
          {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
      </div>
    </ReportCard>
  )
}

function PaReport() {
  const [dates, setDates] = useState<DateRange>({ date_from: '', date_to: '' })
  const [status, setStatus] = useState('')
  const [paType, setPaType] = useState('')
  const [vendorId, setVendorId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { data: vendorData } = useVendors()
  const vendors = vendorData?.items ?? []

  const handleDownload = async () => {
    setLoading(true); setError(null)
    try {
      const params: Params = { ...dates, status: status || undefined, pa_type: paType || undefined, vendor_id: vendorId || undefined }
      await downloadCsv('/reports/pa', params, `pa-report-${new Date().toISOString().slice(0,10)}.csv`)
    } catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  return (
    <ReportCard icon={CreditCard} title="Payment Applications" description="All PAs with prepayment settlements, approval chain, and amounts." onDownload={handleDownload} loading={loading} error={error}>
      <DateRangeRow value={dates} onChange={setDates} />
      <div className="grid grid-cols-2 gap-2">
        <StatusSelect options={PA_STATUSES} value={status} onChange={setStatus} />
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-500">PA Type</label>
          <select className={selectCls} value={paType} onChange={(e) => setPaType(e.target.value)}>
            <option value="">All Types</option>
            <option value="regular">Regular</option>
            <option value="prepayment">Prepayment</option>
          </select>
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-neutral-500">Vendor</label>
        <select className={selectCls} value={vendorId} onChange={(e) => setVendorId(e.target.value)}>
          <option value="">All Vendors</option>
          {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select>
      </div>
    </ReportCard>
  )
}

function BudgetReport() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleDownload = async () => {
    setLoading(true); setError(null)
    try {
      await downloadCsv('/reports/budget', undefined, `budget-report-${new Date().toISOString().slice(0,10)}.csv`)
    } catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  return (
    <ReportCard icon={PiggyBank} title="Budget Accounts" description="Full budget account hierarchy — L1 groups, L2 accounts, annual budget, committed, and actual spend." onDownload={handleDownload} loading={loading} error={error} />
  )
}

function VendorReport() {
  const [category, setCategory] = useState('')
  const [activeOnly, setActiveOnly] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const VENDOR_CATEGORIES = [
    'Supplier', 'Service Provider', 'Contractor', 'Consultant',
    'Logistics', 'IT', 'Utilities', 'Other',
  ]

  const handleDownload = async () => {
    setLoading(true); setError(null)
    try {
      const params: Params = { category: category || undefined, active_only: activeOnly || undefined }
      await downloadCsv('/reports/vendors', params, `vendor-report-${new Date().toISOString().slice(0,10)}.csv`)
    } catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  return (
    <ReportCard icon={Store} title="Vendors" description="Vendor master list with category, contact info, bank details, and active status." onDownload={handleDownload} loading={loading} error={error}>
      <div className="grid grid-cols-2 gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-500">Category</label>
          <select className={selectCls} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All Categories</option>
            {VENDOR_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-neutral-500">Active Filter</label>
          <label className="flex h-9 items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} className="h-4 w-4 rounded border-neutral-300 accent-primary-600" />
            <span className="text-sm text-neutral-700">Active vendors only</span>
          </label>
        </div>
      </div>
    </ReportCard>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function ReportCentrePage() {
  return (
    <div className="flex flex-col gap-6 p-6 max-w-5xl mx-auto">
      <div>
        <h1 className="text-xl font-bold text-neutral-900">Report Centre</h1>
        <p className="text-sm text-neutral-500 mt-1">Export data as CSV for each module. Apply filters to narrow the export scope.</p>
      </div>

      <div className={cn('grid gap-5', 'grid-cols-1 lg:grid-cols-2')}>
        <PrReport />
        <PoReport />
        <GrReport />
        <InvoiceReport />
        <PaReport />
        <BudgetReport />
        <VendorReport />
      </div>
    </div>
  )
}
