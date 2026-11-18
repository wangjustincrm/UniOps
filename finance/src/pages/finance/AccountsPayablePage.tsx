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
import { useLayoutEffect, useRef, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Upload, AlertTriangle } from 'lucide-react'
import { SortTh, sortParams, useSort, useSortableRows, useTabStoreApi, Checkbox, Input, Select, TBody, THead, Table, Td, Th, Tr, Button, Badge, type BadgeTone } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi, EPMS_URL, OA_URL, encodeSession } from '@/lib/api'
import { cn, formatDate } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

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
  /** On a PO imported from NC — NC makes this payable from its purchase invoice; never pushed. */
  nc_flow: boolean
  /** Budget account code(s) of the invoice's PO(s); null until it is on a PO. */
  gl_code: string | null
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
interface PushResult { ap_id: string; ap_number: string; ok: boolean; message: string; nc_bill_no: string | null; warning?: string | null }
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
// "posted" means the invoice is matched and accrued in UniOps — not that it is in
// NC (that is the NC AP Doc column), so the page calls it Matched.
const STATUS_LABEL: Record<string, string> = { posted: 'matched' }
const statusLabel = (s: string) => STATUS_LABEL[s] ?? s.replace('_', ' ')
const SOURCES = ['epms', 'oa'] as const

function fmtMoney(v: string, ccy?: string): string {
  const s = Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return ccy ? `${s} ${ccy}` : s
}
function StatusPill({ status }: { status: string }) {
  // void is the lighter of two greys (draft is the other) → outline; paid was already the dark fill
  const tone: Record<string, BadgeTone> = {
    draft: 'neutral', posted: 'blue',
    partially_paid: 'warning', paid: 'dark',
    void: 'outline',
  }
  return <Badge variant={tone[status] ?? 'neutral'}>{statusLabel(status)}</Badge>
}

const NC_STATE: Record<NcDoc['state'], { label: string; tone: BadgeTone; tip: string }> = {
  saved: { label: 'Saved', tone: 'warning', tip: 'In NC, not yet approved' },
  approved: { label: 'Approved', tone: 'success', tip: 'Approved in NC' },
  pushed: { label: 'Pushed', tone: 'blue', tip: 'Pushed by UniOps; NC state shows after the next NC sync (hourly)' },
}

const NcFlowTag = () => (
  <Badge size="sm" className="w-fit" title="PO imported from NC — NC creates this payable from its purchase invoice. Not pushed from UniOps.">
    NC flow
  </Badge>
)

function NcDocCell({ inv }: { inv: ApInvoice }) {
  const docs = inv.nc_docs ?? []
  if (docs.length === 0) {
    if (inv.nc_push_status === 'failed') {
      return <span className="flex items-center gap-1 text-xs text-danger-fg" title={inv.nc_push_error ?? ''}>
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" /> Push failed
      </span>
    }
    if (inv.nc_flow) return <NcFlowTag />
    return <span className="text-xs text-fg-muted">—</span>
  }
  return (
    <div className="flex flex-col gap-0.5">
      {docs.map((d) => {
        const st = NC_STATE[d.state]
        const tip = [st.tip, d.pushed ? 'Created by Push to NC' : 'Entered in NC by hand',
                     d.bill_date ? `Bill date ${formatDate(d.bill_date.slice(0, 10))}` : null,
                     d.money ? `${fmtMoney(d.money, d.currency ?? undefined)}` : null].filter(Boolean).join(' · ')
        const hyphenTip = `Matched ignoring hyphens: NC has invoice no. ${d.nc_invoice_no ?? '?'}, `
          + `this AP has ${inv.vendor_invoice_number ?? '?'}. Check it is the same invoice.`
        return (
          <span key={d.bill_no} className="flex flex-col">
            <span className="flex items-center gap-1" title={tip}>
              <span className="font-mono text-xs text-fg-secondary">{d.bill_no}</span>
              <Badge size="sm" variant={st.tone}>{st.label}</Badge>
            </span>
            {d.hyphen_match && (
              <span className="flex items-center gap-1 text-2xs text-warning-fg" title={hyphenTip}>
                <AlertTriangle className="h-3 w-3 shrink-0" /> Hyphens ignored · NC: {d.nc_invoice_no}
              </span>
            )}
          </span>
        )
      })}
      {inv.nc_flow && <NcFlowTag />}
      {inv.nc_push_error && (
        <span className="flex items-center gap-1 text-2xs text-danger-fg" title={inv.nc_push_error}>
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
  const [banner, setBanner] = useState<{ kind: 'ok' | 'warn' | 'err'; text: string; lines?: string[] } | null>(null)

  const resetPage = () => { setPage(0); setSelected(new Set()) }
  // Server-sorted: the API pages this list, so it must also order it.
  const [sort, onSort] = useSort<ApSortKey>(null, resetPage)
  const gotoPage = (n: number) => { setPage(n); setSelected(new Set()) }

  const pushable = (inv: ApInvoice) => inv.status !== 'draft' && inv.status !== 'void' && !inv.nc_flow
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
      const warned = ok.filter((x) => x.warning)
      setBanner({
        kind: bad.length ? 'err' : warned.length ? 'warn' : 'ok',
        text: `${ok.length} pushed to NC${bad.length ? `, ${bad.length} not pushed` : ''}.`
          + (warned.length ? ` ${warned.length} need the tax corrected in NC.` : ''),
        lines: [...ok.map((x) => `${x.ap_number}: ${x.message}${x.warning ? ` — ${x.warning}` : ''}`),
                ...bad.map((x) => `${x.ap_number}: ${x.message}`)],
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

  // Toolbar + table header stay on screen while a long page scrolls, so the
  // Push button is in reach after ticking a row. The header sits right under
  // the toolbar, whose height changes when it wraps or a banner shows.
  const barRef = useRef<HTMLDivElement>(null)
  const [barH, setBarH] = useState(0)
  useLayoutEffect(() => {
    const el = barRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setBarH(el.offsetHeight))
    ro.observe(el)
    return () => ro.disconnect()
  }, [tab])

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
            <Button key={t} onClick={() => setTab(t)}
                    variant={tab === t ? 'primary' : 'secondary'} size="md" aria-pressed={tab === t}>
              {label}
            </Button>
          ))}
        </div>

        {tab === 'invoices' ? (
          <>
            <div ref={barRef} className="sticky top-0 z-20 -mx-1 bg-canvas px-1 pb-3 pt-1">
            <div className="flex flex-wrap items-center gap-3">
              <Select value={source} onChange={(e) => { setSource(e.target.value); resetPage() }} className="w-40">
                <option value="">All sources</option>
                {SOURCES.map((s) => <option key={s} value={s}>{s.toUpperCase()}</option>)}
              </Select>
              <Select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); resetPage() }} className="w-44">
                <option value="">All statuses</option>
                {STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
              </Select>
              <form className="flex items-center gap-1"
                    onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); resetPage() }}>
                <Input value={qInput} onChange={(e) => setQInput(e.target.value)}
                       placeholder="Vendor / vendor inv #…" className="w-52" />
                <Button variant="secondary" size="md" type="submit">Search</Button>
              </form>
              <label className="flex items-center gap-1.5 text-sm text-fg-secondary">
                <Checkbox checked={hideInNc}
                       onChange={(e) => { setHideInNc(e.target.checked); resetPage() }} />
                Hide in NC
              </label>
              <Button size="md" onClick={runPush} disabled={pushing || pickedCount === 0}
                      title="Create saved payable bills in NC for the selected invoices"
                      className="ml-auto">
                {pushing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                Push to NC{pickedCount > 0 ? ` (${pickedCount})` : ''}
              </Button>
            </div>

            {banner && (
              <div className={cn('mt-3 rounded-md px-3 py-2 text-sm',
                banner.kind === 'err' ? 'bg-danger-soft text-danger-fg'
                  : banner.kind === 'warn' ? 'bg-warning-soft text-warning-fg' : 'bg-success-soft text-success-fg')}>
                <div>{banner.text}</div>
                {banner.lines && banner.lines.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-xs">
                    {banner.lines.map((l) => <li key={l}>{l}</li>)}
                  </ul>
                )}
              </div>
            )}
            </div>

            <div className="overflow-clip rounded-lg border border-line">
              <Table density="compact">
                <THead
                       style={{ top: barH }}>
                  <Tr>
                    <Th className="w-9" />
                    <SortTh label="AP No." sortKey="ap_invoice_number" sort={sort} onSort={onSort} className="px-3 py-2 w-40" />
                    <SortTh label="Source" sortKey="source" sort={sort} onSort={onSort} className="px-3 py-2 w-16" />
                    <SortTh label="Vendor" sortKey="vendor_name" sort={sort} onSort={onSort} className="px-3 py-2" />
                    <SortTh label="Vendor Inv #" sortKey="vendor_invoice_number" sort={sort} onSort={onSort} className="px-3 py-2 w-32" />
                    <SortTh label="Issue" sortKey="invoice_date" sort={sort} onSort={onSort} className="px-3 py-2 w-24" />
                    <SortTh label="Due" sortKey="due_date" sort={sort} onSort={onSort} className="px-3 py-2 w-24" />
                    <SortTh label="Total" sortKey="total_amount" sort={sort} onSort={onSort} align="right" className="px-3 py-2 w-32" />
                    <SortTh label="Outstanding" sortKey="outstanding" sort={sort} onSort={onSort} align="right" className="px-3 py-2 w-32" />
                    <Th className="w-28" title="Budget account code — from the PO; for courier / Direct / agreement invoices, the one the NC push uses">GL Code</Th>
                    <SortTh label="Status" sortKey="status" sort={sort} onSort={onSort} className="px-3 py-2 w-28" />
                    <SortTh label="NC AP Doc" sortKey="nc_bill_no" sort={sort} onSort={onSort} className="px-3 py-2 w-48" />
                  </Tr>
                </THead>
                <TBody>
                  {isFetching && (
                    <Tr><Td align="center" colSpan={12} className="text-fg-muted">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin" /></Td></Tr>
                  )}
                  {!isFetching && invoices.length === 0 && (
                    <Tr><Td align="center" colSpan={12} className="text-fg-muted">No AP invoices.</Td></Tr>
                  )}
                  {invoices.map((inv, i) => {
                    const outstanding = Number(inv.total_amount) - Number(inv.paid_amount)
                    const open = inv.status === 'posted' || inv.status === 'partially_paid'
                    return (
                      <Tr key={inv.id}
                          onClick={() => openInvoiceTab(inv)}
                          title="Open invoice detail in a tab"
                          className={cn('cursor-pointer border-t border-line-subtle hover:bg-surface-sunken', i % 2 && 'bg-surface-subtle/40')}>
                        <Td onClick={(e) => e.stopPropagation()}>
                          {pushable(inv) && (
                            <Checkbox checked={selected.has(inv.id)}
                                   onChange={() => toggle(inv.id)} />
                          )}
                        </Td>
                        <Td className="font-mono text-xs text-primary-700 underline-offset-2 hover:underline">{inv.ap_invoice_number}</Td>
                        <Td className="text-xs uppercase text-fg-muted">{inv.source}</Td>
                        <Td>{inv.vendor_name ?? '—'}</Td>
                        <Td className="text-xs text-fg-secondary">{inv.vendor_invoice_number ?? '—'}</Td>
                        <Td className="text-xs text-fg-secondary">{formatDate(inv.invoice_date)}</Td>
                        <Td className="text-xs text-fg-secondary">{formatDate(inv.due_date)}</Td>
                        <Td align="right" className="font-mono">{fmtMoney(inv.total_amount, inv.currency)}</Td>
                        <Td align="right" className="font-mono">{open ? fmtMoney(String(outstanding)) : '—'}</Td>
                        <Td className="font-mono text-xs text-fg-secondary">{inv.gl_code ?? ''}</Td>
                        <Td><StatusPill status={inv.status} /></Td>
                        <Td onClick={(e) => e.stopPropagation()}>
                          <NcDocCell inv={inv} />
                        </Td>
                      </Tr>
                    )
                  })}
                </TBody>
              </Table>
            </div>

            <div className="mt-3 flex items-center justify-between text-sm text-fg-muted">
              <span>{total.toLocaleString()} invoice{total === 1 ? '' : 's'}</span>
              <div className="flex items-center gap-2">
                <Button variant="secondary" size="sm" onClick={() => gotoPage(Math.max(0, page - 1))} disabled={page === 0}
                       >Prev</Button>
                <span>Page {page + 1} / {pageCount}</span>
                <Button variant="secondary" size="sm" onClick={() => gotoPage(Math.min(pageCount - 1, page + 1))}
                        disabled={page >= pageCount - 1}
                       >Next</Button>
              </div>
            </div>
          </>
        ) : (
          <div className="overflow-hidden rounded-lg border border-line">
            <Table density="compact">
              <THead>
                <Tr>
                  <SortTh label="Vendor" sortKey="vendor" sort={agingSort.sort} onSort={agingSort.onSort} className="px-3 py-2" />
                  <SortTh label="Ccy" sortKey="currency" sort={agingSort.sort} onSort={agingSort.onSort} className="px-3 py-2 w-16" />
                  <SortTh label="Current" sortKey="current" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="1-30" sortKey="d1_30" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="31-60" sortKey="d31_60" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="61-90" sortKey="d61_90" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="90+" sortKey="d90_plus" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2" />
                  <SortTh label="Total" sortKey="total" sort={agingSort.sort} onSort={agingSort.onSort} align="right" className="px-3 py-2 font-semibold" />
                </Tr>
              </THead>
              <TBody>
                {aging.length === 0 && (
                  <Tr><Td align="center" colSpan={8} className="text-fg-muted">No open payables.</Td></Tr>
                )}
                {agingSort.rows.map((r, i) => (
                  <Tr key={`${r.vendor_id ?? 'none'}-${r.currency}-${i}`} className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                    <Td>{r.vendor_name || '—'}</Td>
                    <Td className="text-xs text-fg-muted">{r.currency}</Td>
                    <Td align="right" className="font-mono">{fmtMoney(r.current)}</Td>
                    <Td align="right" className="font-mono">{fmtMoney(r.d1_30)}</Td>
                    <Td align="right" className="font-mono">{fmtMoney(r.d31_60)}</Td>
                    <Td align="right" className="font-mono">{fmtMoney(r.d61_90)}</Td>
                    <Td className={cn('px-3 py-2 text-right font-mono', Number(r.d90_plus) > 0 && 'text-danger-fg')}>{fmtMoney(r.d90_plus)}</Td>
                    <Td align="right" className="font-mono font-semibold">{fmtMoney(r.total, r.currency)}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
        )}
      </div>
    </PortalChromeLayout>
  )
}
