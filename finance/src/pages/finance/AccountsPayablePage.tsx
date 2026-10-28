/**
 * Accounts Payable workbench (Portal Finance)
 *
 * Read-only view of finance-owned AP invoices (the source of truth that EPMS
 * purchase invoices + OA uploaded invoices sync into) plus AP aging.
 * - Invoices: filter by source (EPMS/OA) and status; shows total/paid/outstanding.
 * - Aging: vendor × currency, 5 buckets, outstanding-based (by due date).
 *
 * AP invoices are created/matched in EPMS/OA and paid via Payment Applications —
 * this page is a finance-side viewer, not an editor. Styling follows
 * AccountsReceivablePage / CoaConfigPage.
 *
 * Push to NC (2026-10-27, replaces the Excel "Export to NC"): the selected AP
 * invoices go to NC as SAVED payable bills through NC's XChange; the clicker
 * is the NC bill maker. "NC AP Doc" shows the NC bill(s) holding each AP —
 * our push result, or the NC AP mirror match for bills entered by hand.
 */
import { useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Upload, AlertTriangle } from 'lucide-react'
import { SortTh, sortParams, useSort, useSortableRows, useTabStoreApi } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi, EPMS_URL, OA_URL, encodeSession } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const PAGE_SIZE = 50

interface ApInvoice {
  id: string; ap_invoice_number: string; source: string
  source_invoice_id: string; source_ref: string | null
  vendor_id: string | null; vendor_name: string | null; vendor_invoice_number: string | null
  amount: string; tax_amount: string; total_amount: string; paid_amount: string
  currency: string; invoice_date: string; due_date: string | null
  status: string; source_status: string | null
  po_id: string | null; po_number: string | null
  nc_exported_at: string | null
  nc_bill_no: string | null
  nc_push_status: 'pushed' | 'failed' | null
  nc_push_error: string | null
  nc_docs: NcDoc[]
}
interface NcDoc {
  bill_no: string
  state: 'saved' | 'approved' | 'pushed'
  pushed: boolean
  bill_date: string | null
  money: string | null
  currency: string | null
  /** Matched only after dropping hyphens ("IN-22-1014797" vs NC "IN-221014797"). */
  hyphen_match: boolean
  nc_invoice_no: string | null
}
interface PushResult { ap_id: string; ap_number: string; ok: boolean; message: string; nc_bill_no: string | null }
interface ApListResp {
  total: number
  items: ApInvoice[]
}
interface AgingRow {
  vendor_id: string | null; vendor_name: string; currency: string
  current: string; d1_30: string; d31_60: string; d61_90: string; d90_plus: string; total: string
}

/** GET /ap/invoices sort keys (finance-api ap_invoices._INVOICE_SORT). */
type ApSortKey = 'ap_invoice_number' | 'source' | 'vendor_name' | 'vendor_invoice_number'
  | 'invoice_date' | 'due_date' | 'total_amount' | 'outstanding' | 'status' | 'nc_bill_no'

const STATUSES = ['draft', 'posted', 'partially_paid', 'paid', 'void'] as const
const SOURCES = ['epms', 'oa'] as const

function fmtMoney(v: string, ccy?: string): string {
  const s = Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return ccy ? `${s} ${ccy}` : s
}
function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    draft: 'bg-neutral-100 text-neutral-600', posted: 'bg-blue-50 text-blue-700',
    partially_paid: 'bg-amber-50 text-amber-700', paid: 'bg-green-50 text-green-700',
    void: 'bg-neutral-100 text-neutral-400',
  }
  return <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', map[status] ?? 'bg-neutral-100 text-neutral-600')}>{status.replace('_', ' ')}</span>
}

const NC_STATE: Record<NcDoc['state'], { label: string; cls: string; tip: string }> = {
  saved: { label: 'Saved', cls: 'bg-amber-50 text-amber-700', tip: 'In NC, not yet approved' },
  approved: { label: 'Approved', cls: 'bg-green-50 text-green-700', tip: 'Approved in NC' },
  pushed: { label: 'Pushed', cls: 'bg-blue-50 text-blue-700', tip: 'Pushed by UniOps; NC state shows after the next NC sync (hourly)' },
}

function NcDocCell({ inv }: { inv: ApInvoice }) {
  const docs = inv.nc_docs ?? []
  if (docs.length === 0) {
    if (inv.nc_push_status === 'failed') {
      return <span className="flex items-center gap-1 text-xs text-red-600" title={inv.nc_push_error ?? ''}>
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" /> Push failed
      </span>
    }
    return <span className="text-xs text-neutral-400">—</span>
  }
  return (
    <div className="flex flex-col gap-0.5">
      {docs.map((d) => {
        const st = NC_STATE[d.state]
        const tip = [st.tip, d.pushed ? 'Created by Push to NC' : 'Entered in NC by hand',
                     d.bill_date ? `Bill date ${d.bill_date.slice(0, 10)}` : null,
                     d.money ? `${fmtMoney(d.money, d.currency ?? undefined)}` : null].filter(Boolean).join(' · ')
        const hyphenTip = `Matched ignoring hyphens: NC has invoice no. ${d.nc_invoice_no ?? '?'}, `
          + `this AP has ${inv.vendor_invoice_number ?? '?'}. Check it is the same invoice.`
        return (
          <span key={d.bill_no} className="flex flex-col">
            <span className="flex items-center gap-1" title={tip}>
              <span className="font-mono text-xs text-neutral-700">{d.bill_no}</span>
              <span className={cn('rounded-full px-1.5 py-0.5 text-[10px] font-medium', st.cls)}>{st.label}</span>
            </span>
            {d.hyphen_match && (
              <span className="flex items-center gap-1 text-[11px] text-amber-700" title={hyphenTip}>
                <AlertTriangle className="h-3 w-3 shrink-0" /> Hyphens ignored · NC: {d.nc_invoice_no}
              </span>
            )}
          </span>
        )
      })}
      {inv.nc_push_error && (
        <span className="flex items-center gap-1 text-[11px] text-red-600" title={inv.nc_push_error}>
          <AlertTriangle className="h-3 w-3 shrink-0" /> Last push failed
        </span>
      )}
    </div>
  )
}

export default function AccountsPayablePage() {
  const { user, token, refreshToken } = useAuthStore()
  const [tab, setTab] = useState<'invoices' | 'aging'>('invoices')
  const [source, setSource] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [hideInNc, setHideInNc] = useState(true)
  const [q, setQ] = useState('')
  const [qInput, setQInput] = useState('')
  const [page, setPage] = useState(0)

  const qc = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [pushing, setPushing] = useState(false)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string; lines?: string[] } | null>(null)

  const resetPage = () => { setPage(0); setSelected(new Set()) }
  // Server-sorted: the API pages this list, so it must also order it.
  const [sort, onSort] = useSort<ApSortKey>(null, resetPage)
  const gotoPage = (n: number) => { setPage(n); setSelected(new Set()) }

  const pushable = (inv: ApInvoice) => inv.status !== 'draft' && inv.status !== 'void'
  const toggle = (id: string) => setSelected((p) => {
    const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n
  })
  const runPush = async () => {
    if (pushing) return                       // second click while the first is in flight
    const picked = invoices.filter((i) => selected.has(i.id))
    if (picked.length === 0) return
    const again = picked.filter((i) => i.nc_bill_no).length
    const msg = `Push ${picked.length} invoice${picked.length === 1 ? '' : 's'} to NC as saved payable bills?\n`
      + `You will be the bill maker in NC.`
      + (again ? `\n\n${again} already pushed: NC replaces a bill that is still saved; an approved bill is refused.` : '')
    if (!window.confirm(msg)) return
    setPushing(true); setBanner(null)
    try {
      const r = await financeApi.post<{ results: PushResult[] }>('/ap/nc-push', { ap_ids: picked.map((i) => i.id) })
      const ok = r.results.filter((x) => x.ok)
      const bad = r.results.filter((x) => !x.ok)
      setBanner({
        kind: bad.length ? 'err' : 'ok',
        text: `${ok.length} pushed to NC${bad.length ? `, ${bad.length} not pushed` : ''}.`,
        lines: [...ok.map((x) => `${x.ap_number}: ${x.message}`), ...bad.map((x) => `${x.ap_number}: ${x.message}`)],
      })
      setSelected(new Set())
      qc.invalidateQueries({ queryKey: ['ap-invoices'] })
    } catch (e) {
      setBanner({ kind: 'err', text: (e as Error).message })
    } finally { setPushing(false) }
  }

  const { data, isFetching } = useQuery({
    queryKey: ['ap-invoices', source, statusFilter, q, hideInNc, page, sort],
    queryFn: () => {
      const qs = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE),
                                       ...sortParams(sort) })
      if (source) qs.set('source', source)
      if (statusFilter) qs.set('status', statusFilter)
      if (q) qs.set('q', q)
      if (hideInNc) qs.set('in_nc', 'false')
      return financeApi.get<ApListResp>(`/ap/invoices?${qs.toString()}`)
    },
  })
  const invoices = data?.items ?? []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const pickedCount = invoices.filter((i) => selected.has(i.id)).length
  const { data: aging = [] } = useQuery({
    queryKey: ['ap-aging'],
    queryFn: () => financeApi.get<AgingRow[]>('/ap/aging'),
    enabled: tab === 'aging',
  })
  // Aging is one plain row per vendor x currency (no subtotal rows), loaded whole.
  const agingSort = useSortableRows(aging, {
    vendor: (r) => r.vendor_name, currency: (r) => r.currency,
    current: (r) => Number(r.current), d1_30: (r) => Number(r.d1_30),
    d31_60: (r) => Number(r.d31_60), d61_90: (r) => Number(r.d61_90),
    d90_plus: (r) => Number(r.d90_plus), total: (r) => Number(r.total),
  })

  // Deep-link a row to the source system's invoice detail (with session handoff).
  // EPMS detail route: /invoices/:id ; OA detail route: /invoices/:source/:id
  const session = token && user ? encodeSession(token, refreshToken ?? '', user) : ''
  const detailHref = (inv: ApInvoice): string => {
    const base = inv.source === 'oa' ? OA_URL : EPMS_URL
    const path = inv.source === 'oa'
      ? `/invoices/oa/${inv.source_invoice_id}`
      : `/invoices/${inv.source_invoice_id}`
    return session ? `${base}${path}#__session=${session}` : `${base}${path}`
  }

  // Open the source-system invoice detail as an iframe tab inside Finance (the
  // embedded EPMS/OA page hides its own chrome), instead of a full-page jump.
  const tabApi = useTabStoreApi()
  const openInvoiceTab = (inv: ApInvoice) => {
    tabApi.getState().openTab({
      key: `ext:invoice:${inv.source}:${inv.source_invoice_id}`,
      title: inv.vendor_invoice_number ?? inv.ap_invoice_number,
      kind: 'iframe',
      src: detailHref(inv),
      icon: 'FileText',
      closable: true,
    })
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/ap"
      title="Accounts Payable"
      subtitle="Vendor invoices (EPMS + OA), owned by Finance, and aging"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          {([['invoices', 'Invoices'], ['aging', 'Aging']] as const).map(([t, label]) => (
            <button key={t} onClick={() => setTab(t)}
                    className={cn('rounded-lg px-4 py-2 text-sm font-medium',
                      tab === t ? 'bg-[#085E5E] text-white'
                                : 'border border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50')}>
              {label}
            </button>
          ))}
        </div>

        {tab === 'invoices' ? (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <select value={source} onChange={(e) => { setSource(e.target.value); resetPage() }} className={cn(inputCls, 'w-40')}>
                <option value="">All sources</option>
                {SOURCES.map((s) => <option key={s} value={s}>{s.toUpperCase()}</option>)}
              </select>
              <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); resetPage() }} className={cn(inputCls, 'w-44')}>
                <option value="">All statuses</option>
                {STATUSES.map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
              </select>
              <form className="flex items-center gap-1"
                    onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); resetPage() }}>
                <input value={qInput} onChange={(e) => setQInput(e.target.value)}
                       placeholder="Vendor / vendor inv #…" className={cn(inputCls, 'w-52')} />
                <button type="submit" className="rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50">Search</button>
              </form>
              <label className="flex items-center gap-1.5 text-sm text-neutral-600">
                <input type="checkbox" checked={hideInNc}
                       onChange={(e) => { setHideInNc(e.target.checked); resetPage() }} />
                Hide in NC
              </label>
              <button onClick={runPush} disabled={pushing || pickedCount === 0}
                      title="Create saved payable bills in NC for the selected invoices"
                      className="ml-auto flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50">
                {pushing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                Push to NC{pickedCount > 0 ? ` (${pickedCount})` : ''}
              </button>
            </div>

            {banner && (
              <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
                banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
                <div>{banner.text}</div>
                {banner.lines && banner.lines.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-xs">
                    {banner.lines.map((l) => <li key={l}>{l}</li>)}
                  </ul>
                )}
              </div>
            )}

            <div className="overflow-hidden rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    <th className="w-9 px-3 py-2" />
                    <SortTh label="AP No." sortKey="ap_invoice_number" sort={sort} onSort={onSort} className="px-3 py-2 w-40" />
                    <SortTh label="Source" sortKey="source" sort={sort} onSort={onSort} className="px-3 py-2 w-16" />
                    <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="px-3 py-2" />
                    <SortTh label="Vendor Inv #" sortKey="vendor_invoice_number" sort={sort} onSort={onSort} className="px-3 py-2 w-32" />
                    <SortTh label="Issue" sortKey="invoice_date" sort={sort} onSort={onSort} className="px-3 py-2 w-24" />
                    <SortTh label="Due" sortKey="due_date" sort={sort} onSort={onSort} className="px-3 py-2 w-24" />
                    <SortTh label="Total" sortKey="total_amount" sort={sort} onSort={onSort} align="right" className="px-3 py-2 w-32" />
                    <SortTh label="Outstanding" sortKey="outstanding" sort={sort} onSort={onSort} align="right" className="px-3 py-2 w-32" />
                    <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-3 py-2 w-28" />
                    <SortTh label="NC AP Doc" sortKey="nc_bill_no" sort={sort} onSort={onSort} className="px-3 py-2 w-48" />
                  </tr>
                </thead>
                <tbody>
                  {isFetching && (
                    <tr><td colSpan={11} className="px-3 py-6 text-center text-neutral-400">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin" /></td></tr>
                  )}
                  {!isFetching && invoices.length === 0 && (
                    <tr><td colSpan={11} className="px-3 py-6 text-center text-neutral-400">No AP invoices.</td></tr>
                  )}
                  {invoices.map((inv, i) => {
                    const outstanding = Number(inv.total_amount) - Number(inv.paid_amount)
                    const open = inv.status === 'posted' || inv.status === 'partially_paid'
                    return (
                      <tr key={inv.id}
                          onClick={() => openInvoiceTab(inv)}
                          title="Open invoice detail in a tab"
                          className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                          {pushable(inv) && (
                            <input type="checkbox" checked={selected.has(inv.id)}
                                   onChange={() => toggle(inv.id)} />
                          )}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-[#085E5E] underline-offset-2 hover:underline">{inv.ap_invoice_number}</td>
                        <td className="px-3 py-2 text-xs uppercase text-neutral-500">{inv.source}</td>
                        <td className="px-3 py-2">{inv.vendor_name ?? '—'}</td>
                        <td className="px-3 py-2 text-xs text-neutral-600">{inv.vendor_invoice_number ?? '—'}</td>
                        <td className="px-3 py-2 text-xs text-neutral-600">{inv.invoice_date}</td>
                        <td className="px-3 py-2 text-xs text-neutral-600">{inv.due_date ?? '—'}</td>
                        <td className="px-3 py-2 text-right font-mono">{fmtMoney(inv.total_amount, inv.currency)}</td>
                        <td className="px-3 py-2 text-right font-mono">{open ? fmtMoney(String(outstanding)) : '—'}</td>
                        <td className="px-3 py-2"><StatusPill status={inv.status} /></td>
                        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                          <NcDocCell inv={inv} />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            <div className="mt-3 flex items-center justify-between text-sm text-neutral-500">
              <span>{total.toLocaleString()} invoice{total === 1 ? '' : 's'}</span>
              <div className="flex items-center gap-2">
                <button onClick={() => gotoPage(Math.max(0, page - 1))} disabled={page === 0}
                        className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50">Prev</button>
                <span>Page {page + 1} / {pageCount}</span>
                <button onClick={() => gotoPage(Math.min(pageCount - 1, page + 1))}
                        disabled={page >= pageCount - 1}
                        className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50">Next</button>
              </div>
            </div>
          </>
        ) : (
          <div className="overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <SortTh label="Vendor" sortKey="vendor" sort={agingSort.sort} onSort={agingSort.onSort} className="px-3 py-2" />
                  <SortTh label="Ccy" sortKey="currency" sort={agingSort.sort} onSort={agingSort.onSort} className="px-3 py-2 w-16" />
                  <SortTh label="Current" sortKey="current" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="1-30" sortKey="d1_30" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="31-60" sortKey="d31_60" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="61-90" sortKey="d61_90" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="90+" sortKey="d90_plus" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="Total" sortKey="total" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2 font-semibold" />
                </tr>
              </thead>
              <tbody>
                {aging.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No open payables.</td></tr>
                )}
                {agingSort.rows.map((r, i) => (
                  <tr key={`${r.vendor_id ?? 'none'}-${r.currency}-${i}`} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                    <td className="px-3 py-2">{r.vendor_name || '—'}</td>
                    <td className="px-3 py-2 text-xs text-neutral-500">{r.currency}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.current)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d1_30)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d31_60)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMoney(r.d61_90)}</td>
                    <td className={cn('px-3 py-2 text-right font-mono', Number(r.d90_plus) > 0 && 'text-red-600')}>{fmtMoney(r.d90_plus)}</td>
                    <td className="px-3 py-2 text-right font-mono font-semibold">{fmtMoney(r.total, r.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </PortalChromeLayout>
  )
}
