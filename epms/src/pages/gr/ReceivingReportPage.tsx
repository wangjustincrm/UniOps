import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Download, Loader2, PackageSearch, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatDate } from '@/lib/utils'
import { useDepartments } from '@/hooks/useDepartments'
import { useReceivingReport } from '@/hooks/useGrReport'
import { grReportService, type ReceivingReportRow } from '@/services/grReport'

// ─── Date window ──────────────────────────────────────────────────────────────

/** Local calendar day as YYYY-MM-DD — `toISOString()` would shift it to UTC. */
function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

type Preset = 'this_month' | 'last_30' | 'this_quarter' | 'this_year' | 'all' | 'custom'

const PRESETS: { value: Preset; label: string }[] = [
  { value: 'this_month',   label: 'This Month' },
  { value: 'last_30',      label: 'Last 30 Days' },
  { value: 'this_quarter', label: 'This Quarter' },
  { value: 'this_year',    label: 'This Year' },
  { value: 'all',          label: 'All Time' },
]

function presetRange(preset: Preset): { from: string; to: string } {
  const today = new Date()
  const to = isoDay(today)
  switch (preset) {
    case 'last_30': {
      const start = new Date(today)
      start.setDate(start.getDate() - 29)
      return { from: isoDay(start), to }
    }
    case 'this_quarter': {
      const q = Math.floor(today.getMonth() / 3)
      return { from: isoDay(new Date(today.getFullYear(), q * 3, 1)), to }
    }
    case 'this_year':
      return { from: isoDay(new Date(today.getFullYear(), 0, 1)), to }
    case 'all':
      return { from: '', to: '' }
    case 'this_month':
    default:
      return { from: isoDay(new Date(today.getFullYear(), today.getMonth(), 1)), to }
  }
}

// ─── Page ─────────────────────────────────────────────────────────────────────

const inputCls = 'h-9 w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm text-neutral-800 focus:outline-none focus:ring-2 focus:ring-primary-600'

export default function ReceivingReportPage() {
  const [preset, setPreset] = useState<Preset>('this_month')
  const [range, setRange] = useState(() => presetRange('this_month'))
  const [departmentId, setDepartmentId] = useState('')
  const [search, setSearch] = useState('')
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const { data: departments } = useDepartments()

  const filters = useMemo(() => ({
    date_from: range.from || undefined,
    date_to: range.to || undefined,
    department_id: departmentId || undefined,
    search: search.trim() || undefined,
  }), [range.from, range.to, departmentId, search])

  const { data, isLoading, isError, error } = useReceivingReport(filters)
  // Memoised so the empty-result fallback is not a fresh array on every
  // render — the summary below depends on it.
  const rows = useMemo(() => data?.items ?? [], [data])

  const applyPreset = (value: Preset) => {
    setPreset(value)
    setRange(presetRange(value))
  }

  const setBound = (key: 'from' | 'to', value: string) => {
    setPreset('custom')
    setRange((r) => ({ ...r, [key]: value }))
  }

  const handleExport = async () => {
    if (exporting) return
    setExporting(true)
    setExportError(null)
    try {
      await grReportService.exportReceiving(filters)
    } catch (e) {
      setExportError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  // Summary of what is on screen. Lead time averages only over rows that have
  // one, so a window of orders with no order date does not read as "0 days".
  const summary = useMemo(() => {
    const withLead = rows.filter((r) => r.lead_time_days !== null)
    const avg = withLead.length
      ? Math.round(withLead.reduce((s, r) => s + (r.lead_time_days ?? 0), 0) / withLead.length)
      : null
    return {
      lines: rows.length,
      receipts: new Set(rows.map((r) => r.gr_number)).size,
      orders: new Set(rows.map((r) => r.po_number)).size,
      avgLead: avg,
    }
  }, [rows])

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Receiving Report</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Every physical line received, with the days each order took to arrive.
            Service confirmations and ERP-imported orders are excluded.
          </p>
        </div>
        <Button onClick={handleExport} disabled={exporting || rows.length === 0} className="gap-2">
          {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          {exporting ? 'Exporting…' : 'Export Excel'}
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-col gap-3 rounded-xl border border-neutral-200 bg-white p-4">
        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.value}
              onClick={() => applyPreset(p.value)}
              className={cn(
                'px-3 py-1 rounded-full text-xs font-medium transition-colors',
                preset === p.value
                  ? 'bg-primary-600 text-white'
                  : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200',
              )}
            >
              {p.label}
            </button>
          ))}
          {preset === 'custom' && (
            <span className="px-3 py-1 rounded-full text-xs font-medium bg-primary-600 text-white">
              Custom
            </span>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-neutral-500">Arrival From</label>
            <input type="date" className={inputCls} value={range.from}
              onChange={(e) => setBound('from', e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-neutral-500">Arrival To</label>
            <input type="date" className={inputCls} value={range.to}
              onChange={(e) => setBound('to', e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-neutral-500">Department</label>
            <select className={inputCls} value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}>
              <option value="">All Departments</option>
              {(departments?.items ?? []).map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-neutral-500">Search</label>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-neutral-400" />
              <input
                type="text"
                placeholder="Item, material ID, PO#, supplier…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className={cn(inputCls, 'pl-9')}
              />
            </div>
          </div>
        </div>
      </div>

      {exportError && (
        <div className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-2 text-sm text-danger-700">
          {exportError}
        </div>
      )}
      {data?.truncated && (
        <div className="rounded-lg border border-warning-200 bg-warning-50 px-4 py-2 text-sm text-warning-700">
          Showing the first {rows.length.toLocaleString()} lines only — narrow the date range to see the rest.
        </div>
      )}

      {/* Summary */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryCard label="Lines Received" value={summary.lines.toLocaleString()} />
        <SummaryCard label="Goods Receipts" value={summary.receipts.toLocaleString()} />
        <SummaryCard label="Purchase Orders" value={summary.orders.toLocaleString()} />
        <SummaryCard
          label="Average Lead Time"
          value={summary.avgLead === null ? '—' : `${summary.avgLead} days`}
        />
      </div>

      {/* Table */}
      <div className="rounded-xl border border-neutral-200 bg-white overflow-hidden">
        {isLoading || isError || rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <PackageSearch className="h-10 w-10 text-neutral-300 mb-3" />
            <p className="text-sm font-medium text-neutral-500">
              {isLoading ? 'Loading…'
                : isError ? (error instanceof Error ? error.message : 'Could not load the report')
                : 'No goods received in this window'}
            </p>
            {!isLoading && !isError && (
              <p className="text-xs text-neutral-400 mt-1">Try a wider date range or clear the filters</p>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm whitespace-nowrap">
              <thead>
                <tr className="border-b border-neutral-200 bg-neutral-50">
                  <Th>Material ID</Th>
                  <Th>Description</Th>
                  <Th>Manufacturer / Supplier</Th>
                  <Th>PO Number</Th>
                  <Th>UoM</Th>
                  <Th align="right">Quantity</Th>
                  <Th>Department</Th>
                  <Th>Requested By</Th>
                  <Th>Date Ordered</Th>
                  <Th>Arrival Date</Th>
                  <Th>Left Warehouse</Th>
                  <Th>Warehouse Receiver</Th>
                  <Th>Person Accepting</Th>
                  <Th align="right">Lead Time</Th>
                  <Th>GR Number</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, idx) => (
                  <ReportRow key={`${row.gr_id}-${idx}`} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-neutral-200 bg-white px-4 py-3">
      <p className="text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-neutral-900">{value}</p>
    </div>
  )
}

function Th({ children, align = 'left' }: { children: React.ReactNode; align?: 'left' | 'right' }) {
  return (
    <th className={cn(
      'px-3 py-3 text-xs font-semibold uppercase tracking-wide text-neutral-500 whitespace-nowrap',
      align === 'right' ? 'text-right' : 'text-left',
    )}>
      {children}
    </th>
  )
}

function ReportRow({ row }: { row: ReceivingReportRow }) {
  return (
    <tr className="border-b border-neutral-100 bg-white hover:bg-primary-50/60 transition-colors">
      <td className="px-3 py-2.5 font-mono text-xs text-neutral-700">{row.material_id || '—'}</td>
      <td className="px-3 py-2.5 text-neutral-800 max-w-md truncate" title={row.description}>
        {row.description}
      </td>
      <td className="px-3 py-2.5 text-neutral-700">{row.supplier}</td>
      <td className="px-3 py-2.5">
        <Link to={`/po/${row.po_id}`} className="text-primary-600 hover:underline font-mono text-xs">
          {row.po_number}
        </Link>
      </td>
      <td className="px-3 py-2.5 text-neutral-600">{row.unit}</td>
      <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-800">
        {Number(row.quantity).toLocaleString(undefined, { maximumFractionDigits: 4 })}
      </td>
      <td className="px-3 py-2.5 text-neutral-700">{row.department || '—'}</td>
      <td className="px-3 py-2.5 text-neutral-700">{row.requested_by || '—'}</td>
      <td className="px-3 py-2.5 text-neutral-600">{formatDate(row.date_ordered)}</td>
      <td className="px-3 py-2.5 text-neutral-600">{formatDate(row.arrival_date)}</td>
      <td className="px-3 py-2.5 text-neutral-600">{formatDate(row.left_warehouse_date)}</td>
      <td className="px-3 py-2.5 text-neutral-700">{row.warehouse_receiver || '—'}</td>
      <td className="px-3 py-2.5 text-neutral-700">{row.person_accepting || '—'}</td>
      <td className="px-3 py-2.5 text-right font-mono text-xs text-neutral-800">
        {row.lead_time_days === null ? '—' : `${row.lead_time_days}d`}
      </td>
      <td className="px-3 py-2.5">
        <Link to={`/gr/${row.gr_id}`} className="text-primary-600 hover:underline font-mono text-xs">
          {row.gr_number}
        </Link>
      </td>
    </tr>
  )
}
