import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Download, Loader2, PackageSearch, Search } from 'lucide-react'
import { ChipGroup, Input, Select, SortTh, TBody, THead, Table, Td, Tr, sortParams, type Sort, useSort } from '@uniops/shell'
import { Button } from '@/components/ui/button'
import { Pagination } from '@/components/ui/Pagination'
import { cn, formatDate, todayISO, toLocalDate } from '@/lib/utils'
import { useDepartments } from '@/hooks/useDepartments'
import { useVendors } from '@/hooks/useVendors'
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
  // The plant's today (company time zone), as a local-midnight Date for the
  // calendar arithmetic below.
  const today = toLocalDate(todayISO())
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

// GET /gr/receiving-report sort keys (epms-api crud/gr_report.py SORTABLE).
type SortKey =
  | 'material_id' | 'description' | 'supplier' | 'po_number' | 'unit' | 'quantity'
  | 'department' | 'requested_by' | 'date_ordered' | 'arrival_date' | 'left_warehouse_date'
  | 'warehouse_receiver' | 'person_accepting' | 'lead_time_days' | 'gr_number'

export default function ReceivingReportPage() {
  const [preset, setPreset] = useState<Preset>('this_month')
  const [range, setRange] = useState(() => presetRange('this_month'))
  const [departmentId, setDepartmentId] = useState('')
  const [vendorId, setVendorId] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  // The report is paged by the API, so the API sorts (all pages, not the 50
  // on screen); a new order starts again at page 1.
  const [sort, onSort] = useSort<SortKey>(null, () => setPage(1))

  const { data: departments } = useDepartments()
  const { data: vendors } = useVendors()

  // What the window selects, independent of which page of it is on screen.
  // The export sends exactly this — a spreadsheet of page 3 of 7 would be a
  // trap, not a report.
  const windowFilters = useMemo(() => ({
    date_from: range.from || undefined,
    date_to: range.to || undefined,
    department_id: departmentId || undefined,
    vendor_id: vendorId || undefined,
    search: search.trim() || undefined,
  }), [range.from, range.to, departmentId, vendorId, search])

  const { data, isLoading, isFetching, isError, error } = useReceivingReport({
    ...windowFilters, ...sortParams(sort), page, page_size: pageSize,
  })
  const rows = useMemo(() => data?.items ?? [], [data])
  const total = data?.total ?? 0
  const summary = data?.summary

  // Any change to the window is a different result set; staying on page 7 of
  // the old one would show an empty table.
  useEffect(() => { setPage(1) }, [windowFilters])

  // A new page starts at its own first row, not wherever the last one was left.
  const scrollBox = useRef<HTMLDivElement>(null)
  useEffect(() => { scrollBox.current?.scrollTo({ top: 0 }) }, [page])

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
      // Exported in the order the table is sorted by.
      await grReportService.exportReceiving({ ...windowFilters, ...sortParams(sort) })
    } catch (e) {
      setExportError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    // Fixed height, so the table below scrolls inside its own box instead of
    // running off the bottom of the page: that is what keeps the horizontal
    // scrollbar — this table is far wider than any screen — and the pager in
    // view at all times. The min-height keeps it usable on a short window,
    // where the page itself scrolls again.
    <div className="flex flex-col gap-4 h-[calc(100vh-11rem)] min-h-[34rem]">
      {/* Page header */}
      <div className="flex flex-wrap items-start justify-between gap-3 shrink-0">
        <div>
          <h1 className="text-2xl font-semibold text-fg">Receiving Report</h1>
          <p className="mt-1 text-sm text-fg-muted">
            Every physical line received, with the days each order took to arrive.
            Service confirmations and ERP-imported orders are excluded.
          </p>
        </div>
        <Button onClick={handleExport} disabled={exporting || total === 0} className="gap-2">
          {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          {exporting ? 'Exporting…' : 'Export Excel'}
        </Button>
      </div>

      {/* Filters */}
      <div className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4 shrink-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <ChipGroup label="Period" items={PRESETS.map((p) => ({ key: p.value, label: p.label }))} value={preset} onChange={(k) => applyPreset(k as Preset)} />
          {preset === 'custom' && (
            <span className="px-3 py-1 rounded-full text-xs font-medium bg-primary-600 text-white">
              Custom
            </span>
          )}

          {/* Window totals, inline — the table below owns the vertical space. */}
          <div className="ml-auto flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-fg-muted">
            <Stat label="Lines" value={summary ? summary.lines.toLocaleString() : '—'} />
            <Stat label="Receipts" value={summary ? summary.receipts.toLocaleString() : '—'} />
            <Stat label="POs" value={summary ? summary.orders.toLocaleString() : '—'} />
            <Stat
              label="Avg lead time"
              value={summary?.avg_lead_days == null ? '—' : `${summary.avg_lead_days} days`}
            />
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-fg-muted">Arrival From</label>
            <Input type="date" value={range.from}
              onChange={(e) => setBound('from', e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-fg-muted">Arrival To</label>
            <Input type="date" value={range.to}
              onChange={(e) => setBound('to', e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-fg-muted">Department</label>
            <Select className="w-full" value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}>
              <option value="">All Departments</option>
              {(departments?.items ?? []).map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </Select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-fg-muted">Vendor</label>
            <Select className="w-full" value={vendorId}
              onChange={(e) => setVendorId(e.target.value)}>
              <option value="">All Vendors</option>
              {(vendors?.items ?? []).map((v) => (
                <option key={v.id} value={v.id}>{v.name}</option>
              ))}
            </Select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-fg-muted">Search</label>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-fg-muted" />
              <Input
                type="text"
                placeholder="Item, material ID, PO#, supplier…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9"
              />
            </div>
          </div>
        </div>
      </div>

      {exportError && (
        <div className="rounded-lg border border-danger-line bg-danger-soft px-4 py-2 text-sm text-danger-fg shrink-0">
          {exportError}
        </div>
      )}

      {/* Table — scrolls in both directions inside this box; header row and
          pager stay put. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-line bg-surface">
        {isLoading || isError || rows.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center py-20 text-center">
            <PackageSearch className="h-10 w-10 text-fg-faint mb-3" />
            <p className="text-sm font-medium text-fg-muted">
              {isLoading ? 'Loading…'
                : isError ? (error instanceof Error ? error.message : 'Could not load the report')
                : 'No goods received in this window'}
            </p>
            {!isLoading && !isError && (
              <p className="text-xs text-fg-muted mt-1">Try a wider date range or clear the filters</p>
            )}
          </div>
        ) : (
          <div
            ref={scrollBox}
            className={cn(
              'min-h-0 flex-1 overflow-auto transition-opacity',
              // The previous page stays put while the next loads; dimming it is
              // the only signal that it is not the answer yet.
              isFetching && 'opacity-60',
            )}
          >
            <Table className="whitespace-nowrap">
              <THead>
                <Tr>
                  <Th label="Material ID" sortKey="material_id" sort={sort} onSort={onSort} />
                  <Th label="Description" sortKey="description" sort={sort} onSort={onSort} />
                  <Th label="Manufacturer / Supplier" sortKey="supplier" sort={sort} onSort={onSort} />
                  <Th label="PO Number" sortKey="po_number" sort={sort} onSort={onSort} />
                  <Th label="UoM" sortKey="unit" sort={sort} onSort={onSort} />
                  <Th label="Quantity" sortKey="quantity" sort={sort} onSort={onSort} align="right" />
                  <Th label="Department" sortKey="department" sort={sort} onSort={onSort} />
                  <Th label="Requested By" sortKey="requested_by" sort={sort} onSort={onSort} />
                  <Th label="Date Ordered" sortKey="date_ordered" sort={sort} onSort={onSort} />
                  <Th label="Arrival Date" sortKey="arrival_date" sort={sort} onSort={onSort} />
                  <Th label="Left Warehouse" sortKey="left_warehouse_date" sort={sort} onSort={onSort} />
                  <Th label="Warehouse Receiver" sortKey="warehouse_receiver" sort={sort} onSort={onSort} />
                  <Th label="Person Accepting" sortKey="person_accepting" sort={sort} onSort={onSort} />
                  <Th label="Lead Time" sortKey="lead_time_days" sort={sort} onSort={onSort} align="right" />
                  <Th label="GR Number" sortKey="gr_number" sort={sort} onSort={onSort} />
                </Tr>
              </THead>
              <TBody>
                {rows.map((row, idx) => (
                  <ReportRow key={`${row.gr_id}-${idx}`} row={row} />
                ))}
              </TBody>
            </Table>
          </div>
        )}

        <div className="shrink-0 border-t border-line">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={setPage}
            onPageSizeChange={(size) => { setPageSize(size); setPage(1) }}
          />
        </div>
      </div>

      {data?.truncated && (
        <p className="shrink-0 text-xs text-warning-fg">
          This window holds {total.toLocaleString()} lines — the export stops at 20,000.
          Narrow the date range to get all of them.
        </p>
      )}
    </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="whitespace-nowrap">
      {label} <span className="ml-1 font-semibold text-fg">{value}</span>
    </span>
  )
}

function Th({ label, sortKey, sort, onSort, align = 'left' }: {
  label: string
  sortKey: SortKey
  sort: Sort<SortKey> | null
  onSort: (key: SortKey) => void
  align?: 'left' | 'right'
}) {
  return (
    // Sticky on the th rather than the thead: the row itself cannot carry the
    // background, and a transparent sticky header lets rows show through it.
    // The inset shadow draws the bottom rule, which a border on a sticky cell
    // scrolls away from.
    <SortTh
      label={label} sortKey={sortKey} sort={sort} onSort={onSort} align={align}
      // THead locks the header row and draws its rule; only the padding is local.
      className="px-3 py-3"
    />
  )
}

function ReportRow({ row }: { row: ReceivingReportRow }) {
  return (
    <Tr className="bg-surface hover:bg-primary-50/60 transition-colors">
      <Td className="font-mono text-xs text-fg-secondary">{row.material_id || '—'}</Td>
      <Td className="text-fg max-w-md truncate" title={row.description}>
        {row.description}
      </Td>
      <Td className="text-fg-secondary">{row.supplier}</Td>
      <Td>
        <Link to={`/po/${row.po_id}`} className="text-primary-600 hover:underline font-mono text-xs">
          {row.po_number}
        </Link>
      </Td>
      <Td className="text-fg-secondary">{row.unit}</Td>
      <Td align="right" className="font-mono text-xs text-fg">
        {Number(row.quantity).toLocaleString(undefined, { maximumFractionDigits: 4 })}
      </Td>
      <Td className="text-fg-secondary">{row.department || '—'}</Td>
      <Td className="text-fg-secondary">{row.requested_by || '—'}</Td>
      <Td className="text-fg-secondary">{formatDate(row.date_ordered)}</Td>
      <Td className="text-fg-secondary">{formatDate(row.arrival_date)}</Td>
      <Td className="text-fg-secondary">{formatDate(row.left_warehouse_date)}</Td>
      <Td className="text-fg-secondary">{row.warehouse_receiver || '—'}</Td>
      <Td className="text-fg-secondary">{row.person_accepting || '—'}</Td>
      <Td align="right" className="font-mono text-xs text-fg">
        {row.lead_time_days === null ? '—' : `${row.lead_time_days}d`}
      </Td>
      <Td>
        <Link to={`/gr/${row.gr_id}`} className="text-primary-600 hover:underline font-mono text-xs">
          {row.gr_number}
        </Link>
      </Td>
    </Tr>
  )
}
