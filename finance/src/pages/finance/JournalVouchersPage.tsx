/**
 * Journal Voucher Center (凭证中心) — Plan 4.
 * List with period/status/search filters + pagination, row selection with
 * batch Review / batch Post, and the shared JvDetailModal for detail +
 * single-voucher lifecycle actions. Styling follows GeneralLedgerPage.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronLeft, ChevronRight, DatabaseZap, Loader2, Search, Send, SlidersHorizontal, X } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { JvDetailModal, JvStatusBadge, type JvHeader } from './JvDetailModal'
import { NcSyncModal, type NcSyncStatus } from './NcSyncModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

const PAGE_SIZE = 50
const STATUSES = ['', 'draft', 'reviewed', 'posted', 'reversed'] as const
// NC's 凭证状态: 正常 / 错误 / 作废 / 暂存. Only `normal` ever reaches posted —
// the other three are visible here precisely because they used to be invisible
// (a discarded voucher was dropped at sync time and could not be reported at all).
const VOUCHER_STATES = [
  ['', 'All voucher states'], ['normal', 'Normal'], ['error', 'Error'],
  ['discarded', 'Discarded'], ['tempsave', 'Temp-saved'],
] as const
const SUBSYSTEMS = [
  ['GL', 'General Ledger'], ['AP', 'Accounts Payable'], ['AR', 'Accounts Receivable'],
  ['FA', 'Fixed Assets'], ['CM', 'Cash Management'], ['IA', 'Inventory Accounting'],
  ['EGL', 'Exchange Gain/Loss'], ['PLCF', 'Gain/Loss Carry-Forward'], ['OT', 'OT'],
] as const

interface JvList { total: number; items: JvHeader[] }

function money(v: string) {
  const n = Number(v)
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

// NC's auxiliary names, keyed by the dim_code the sync writes. Anything not
// listed shows its raw code rather than a guess.
const DIM_LABELS: Record<string, string> = {
  department: 'Department', cost_center: 'Cost Center',
  income_expense_item: 'Income/Expense Item', supplier: 'Supplier',
  customer: 'Customer', partner: 'Partner', bank_account: 'Bank Account',
  item: 'Item', item_category: 'Item Category', project: 'Project',
  employee: 'Employee',
}
export function dimLabel(code: string) { return DIM_LABELS[code] ?? code }

function AdvField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-neutral-500">{label}</span>
      {children}
    </label>
  )
}

function NameSelect({ value, options, onChange }:
  { value?: string; options?: string[]; onChange: (v: string) => void }) {
  return (
    <select value={value ?? ''} onChange={(e) => onChange(e.target.value)}
            className={cn(inputCls, 'w-full')}>
      <option value="">Anyone</option>
      {(options ?? []).map((n) => <option key={n} value={n}>{n}</option>)}
    </select>
  )
}

export default function JournalVouchersPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  // A RANGE, defaulting to the current month on both ends — the old single
  // `period` could not express "June through August" at all, which is what the
  // user hit (2026-09-22).
  const [periodFrom, setPeriodFrom] = useState(thisMonth())
  const [periodTo, setPeriodTo] = useState(thisMonth())
  const [status, setStatus] = useState('')
  const [subsystem, setSubsystem] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [adv, setAdv] = useState<Record<string, string>>({})
  const setAdvField = (k: string, v: string) =>
    setAdv((p) => { const n = { ...p }; if (v) n[k] = v; else delete n[k]; return n })
  const [q, setQ] = useState('')
  const [qInput, setQInput] = useState('')
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<{ col: string; dir: 'asc' | 'desc' }>({ col: 'voucher_date', dir: 'desc' })
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [detailId, setDetailId] = useState<string | null>(null)
  const [banner, setBanner] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState<'review' | 'post' | null>(null)
  const [showNcSync, setShowNcSync] = useState(false)
  const { data: ncStatus } = useQuery({
    queryKey: ['nc-sync-status'],
    queryFn: () => financeApi.get<NcSyncStatus>('/nc-sync/status'),
  })

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })
  const canAct = perms?.can_act ?? false

  // Offered values come from the data, not a hard-coded list: the preparers are
  // whoever NC actually recorded, and the auxiliaries are the ones this book
  // really uses (NC's catalog carries far more than any single book touches).
  const { data: opts } = useQuery({
    queryKey: ['jv-filter-options'],
    queryFn: () => financeApi.get<{
      prepared_by: string[]; checked_by: string[]; manager: string[]
      aux_codes: string[]; voucher_kinds: { value: number; label: string }[]
    }>('/journal-vouchers/filter-options'),
  })
  const { data: auxValues } = useQuery({
    queryKey: ['jv-aux-values', adv.aux_code],
    enabled: !!adv.aux_code,
    queryFn: () => financeApi.get<{ items: string[] }>(
      `/journal-vouchers/aux-values?dim_code=${encodeURIComponent(adv.aux_code)}`),
  })
  const advCount = Object.keys(adv).filter((k) => k !== 'aux_value' || adv.aux_code).length

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) })
    if (periodFrom) p.set('period_from', periodFrom)
    if (periodTo) p.set('period_to', periodTo)
    if (status) p.set('status', status)
    if (subsystem) p.set('source_subsystem', subsystem)
    if (q) p.set('q', q)
    // aux_value alone means nothing — the same text can exist under several
    // dimensions, so it is only ever sent together with its aux_code.
    for (const [k, v] of Object.entries(adv)) {
      if (k === 'aux_value' && !adv.aux_code) continue
      p.set(k, v)
    }
    p.set('sort', sort.col); p.set('dir', sort.dir)
    return p.toString()
  }, [periodFrom, periodTo, status, subsystem, q, page, sort, adv])

  const list = useQuery({
    queryKey: ['jv-list', params, sort],
    queryFn: () => financeApi.get<JvList>(`/journal-vouchers?${params}`),
  })
  const items = list.data?.items ?? []
  const total = list.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const flash = (kind: 'ok' | 'err', text: string) => {
    setBanner({ kind, text }); setTimeout(() => setBanner(null), 6000)
  }
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['jv-list'] })
    setSelected(new Set())
  }
  const resetPage = () => { setPage(0); setSelected(new Set()) }
  const gotoPage = (n: number) => { setPage(n); setSelected(new Set()) }
  const toggleSort = (col: string) => {
    setPage(0)
    setSort((s) => s.col === col ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col, dir: 'desc' })
  }

  const toggle = (id: string) => setSelected((p) => {
    const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n
  })
  // NC mirrors are excluded: their status follows NC's tally and the backend
  // rejects any hand-change, so batching them in would only ever fail. The 271
  // vouchers NC has not tallied yet arrive here as drafts and would otherwise
  // look like the obvious thing to select.
  const selectable = items.filter((v) => !v.nc_source_pk
    && (v.status === 'draft' || v.status === 'reviewed'))
  const allSelected = selectable.length > 0 && selectable.every((v) => selected.has(v.id))
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(selectable.map((v) => v.id)))

  const runBatch = async (action: 'review' | 'post') => {
    const eligible = items.filter((v) => selected.has(v.id) && !v.nc_source_pk &&
      (action === 'review' ? v.status === 'draft' : v.status === 'reviewed'))
    if (eligible.length === 0) return flash('err', `No selected ${action === 'review' ? 'draft' : 'reviewed'} vouchers`)
    setBusy(action)
    try {
      const res = await financeApi.post<{ ok: boolean; error?: string }[]>(
        `/journal-vouchers/${action}-batch`, { ids: eligible.map((v) => v.id) })
      const ok = res.filter((r) => r.ok).length
      const failed = res.length - ok
      flash(failed ? 'err' : 'ok',
        failed ? `${action === 'review' ? 'Reviewed' : 'Posted'} ${ok}, failed ${failed} (SoD / closed period / state)`
               : `${action === 'review' ? 'Reviewed' : 'Posted'} ${ok} voucher${ok === 1 ? '' : 's'}`)
      refresh()
    } catch (e) { flash('err', (e as Error).message) } finally { setBusy(null) }
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/journal-vouchers"
      title="Journal Vouchers"
      subtitle="Voucher center — review, post, and trace formal GL journal vouchers"
      headerActions={ncStatus?.configured && ncStatus?.can_sync ? (
        <button onClick={() => setShowNcSync(true)}
                className="flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50">
          <DatabaseZap className="h-4 w-4" /> NC Sync
        </button>
      ) : undefined}
    >
      <div className="mx-auto max-w-7xl">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            <input type="month" value={periodFrom} aria-label="Period from"
                   onChange={(e) => { setPeriodFrom(e.target.value); resetPage() }}
                   className={cn(inputCls, 'w-36')} />
            <span className="text-sm text-neutral-400">–</span>
            <input type="month" value={periodTo} aria-label="Period to"
                   onChange={(e) => { setPeriodTo(e.target.value); resetPage() }}
                   className={cn(inputCls, 'w-36')} />
          </div>
          <select value={status} onChange={(e) => { setStatus(e.target.value); resetPage() }} className={inputCls}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? s[0].toUpperCase() + s.slice(1) : 'All statuses'}</option>)}
          </select>
          <select value={subsystem} onChange={(e) => { setPage(0); setSubsystem(e.target.value) }}
                  className={inputCls}>
            <option value="">All systems</option>
            {SUBSYSTEMS.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
          </select>
          <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); resetPage() }}>
            <input value={qInput} onChange={(e) => setQInput(e.target.value)}
                   placeholder="Voucher no. / summary…" className={cn(inputCls, 'w-56')} />
            <button type="submit" className={secondaryBtn}><Search className="h-4 w-4" /></button>
          </form>
          <button type="button" onClick={() => setShowAdvanced((v) => !v)}
                  className={cn(secondaryBtn, advCount > 0 && 'border-primary-600 text-primary-700')}>
            <SlidersHorizontal className="h-4 w-4" />
            More filters{advCount > 0 ? ` (${advCount})` : ''}
          </button>
          {advCount > 0 && (
            <button type="button" onClick={() => { setAdv({}); resetPage() }}
                    className="flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-700">
              <X className="h-3.5 w-3.5" /> Clear
            </button>
          )}
          {canAct && (
            <div className="ml-auto flex items-center gap-2">
              <button onClick={() => runBatch('review')} disabled={!!busy || selected.size === 0} className={secondaryBtn}>
                {busy === 'review' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                Review Selected
              </button>
              <button onClick={() => runBatch('post')} disabled={!!busy || selected.size === 0} className={primaryBtn}>
                {busy === 'post' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                Post Selected
              </button>
            </div>
          )}
        </div>

        {/* Everything NC's own 凭证查询 offers beyond period and free text. Kept
            behind a toggle so the common case stays a two-control bar, but the
            active count is on the button so a filter can never be silently on. */}
        {showAdvanced && (
          <div className="mb-4 grid grid-cols-1 gap-3 rounded-lg border border-neutral-200 bg-neutral-50/60 p-3 sm:grid-cols-2 lg:grid-cols-4">
            <AdvField label="Date from">
              <input type="date" value={adv.date_from ?? ''} className={cn(inputCls, 'w-full')}
                     onChange={(e) => { setAdvField('date_from', e.target.value); resetPage() }} />
            </AdvField>
            <AdvField label="Date to">
              <input type="date" value={adv.date_to ?? ''} className={cn(inputCls, 'w-full')}
                     onChange={(e) => { setAdvField('date_to', e.target.value); resetPage() }} />
            </AdvField>
            <AdvField label="Voucher no. from">
              <input type="number" value={adv.num_from ?? ''} className={cn(inputCls, 'w-full')}
                     onChange={(e) => { setAdvField('num_from', e.target.value); resetPage() }} />
            </AdvField>
            <AdvField label="Voucher no. to">
              <input type="number" value={adv.num_to ?? ''} className={cn(inputCls, 'w-full')}
                     onChange={(e) => { setAdvField('num_to', e.target.value); resetPage() }} />
            </AdvField>
            <AdvField label="Voucher state">
              <select value={adv.voucher_state ?? ''} className={cn(inputCls, 'w-full')}
                      onChange={(e) => { setAdvField('voucher_state', e.target.value); resetPage() }}>
                {VOUCHER_STATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </AdvField>
            <AdvField label="Voucher kind">
              <select value={adv.voucher_kind ?? ''} className={cn(inputCls, 'w-full')}
                      onChange={(e) => { setAdvField('voucher_kind', e.target.value); resetPage() }}>
                <option value="">All kinds</option>
                {(opts?.voucher_kinds ?? []).map((k) => (
                  <option key={k.value} value={k.value}>{k.label}</option>
                ))}
              </select>
            </AdvField>
            <AdvField label="Prepared by">
              <NameSelect value={adv.prepared_by} options={opts?.prepared_by}
                          onChange={(v) => { setAdvField('prepared_by', v); resetPage() }} />
            </AdvField>
            <AdvField label="Checked by">
              <NameSelect value={adv.checked_by} options={opts?.checked_by}
                          onChange={(v) => { setAdvField('checked_by', v); resetPage() }} />
            </AdvField>
            <AdvField label="Posted by">
              <NameSelect value={adv.manager} options={opts?.manager}
                          onChange={(v) => { setAdvField('manager', v); resetPage() }} />
            </AdvField>
            <AdvField label="Account code">
              <input value={adv.account_code ?? ''} placeholder="e.g. 5101" className={cn(inputCls, 'w-full')}
                     onChange={(e) => { setAdvField('account_code', e.target.value); resetPage() }} />
            </AdvField>
            <AdvField label="Contra account">
              <input value={adv.opposite_subject ?? ''} className={cn(inputCls, 'w-full')}
                     onChange={(e) => { setAdvField('opposite_subject', e.target.value); resetPage() }} />
            </AdvField>
            <AdvField label="Currency">
              <input value={adv.currency ?? ''} placeholder="CAD / USD" className={cn(inputCls, 'w-full')}
                     onChange={(e) => { setAdvField('currency', e.target.value.toUpperCase()); resetPage() }} />
            </AdvField>
            <AdvField label="Amount min (CAD)">
              <input type="number" value={adv.amount_min ?? ''} className={cn(inputCls, 'w-full')}
                     onChange={(e) => { setAdvField('amount_min', e.target.value); resetPage() }} />
            </AdvField>
            <AdvField label="Amount max (CAD)">
              <input type="number" value={adv.amount_max ?? ''} className={cn(inputCls, 'w-full')}
                     onChange={(e) => { setAdvField('amount_max', e.target.value); resetPage() }} />
            </AdvField>
            <AdvField label="Auxiliary">
              <select value={adv.aux_code ?? ''} className={cn(inputCls, 'w-full')}
                      onChange={(e) => {
                        setAdvField('aux_code', e.target.value)
                        setAdvField('aux_value', '')   // a value is meaningless under a different dimension
                        resetPage()
                      }}>
                <option value="">Any auxiliary</option>
                {(opts?.aux_codes ?? []).map((c) => <option key={c} value={c}>{dimLabel(c)}</option>)}
              </select>
            </AdvField>
            <AdvField label="Auxiliary value">
              <input list="jv-aux-values" value={adv.aux_value ?? ''} disabled={!adv.aux_code}
                     className={cn(inputCls, 'w-full disabled:bg-neutral-100')}
                     onChange={(e) => { setAdvField('aux_value', e.target.value); resetPage() }} />
              <datalist id="jv-aux-values">
                {(auxValues?.items ?? []).map((v) => <option key={v} value={v} />)}
              </datalist>
            </AdvField>
          </div>
        )}

        {banner && (
          <div className={cn('mb-3 rounded-md px-3 py-2 text-sm',
            banner.kind === 'err' ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700')}>
            {banner.text}
          </div>
        )}

        {list.isFetching && !list.data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <>
            {/* table-fixed, and every column but Summary carries a width.
                Under table-auto the browser re-derives widths from content, so
                the unconstrained Summary took the whole row and squeezed the
                fixed-width columns down to their longest word — "JV · JV-2026
                09-0276" wrapped onto three lines while Summary had space to
                spare. Fixed layout makes the w-* classes authoritative and
                leaves Summary exactly the remainder. */}
            <div className="overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full min-w-[80rem] table-fixed text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    {canAct && (
                      <th className="w-9 px-3 py-2">
                        <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                      </th>
                    )}
                    <th className="px-3 py-2 w-44 cursor-pointer select-none whitespace-nowrap" onClick={() => toggleSort('jv_number')}>
                      Voucher No.{sort.col === 'jv_number' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
                    <th className="px-3 py-2 w-28 cursor-pointer select-none whitespace-nowrap" onClick={() => toggleSort('voucher_date')}>
                      Date{sort.col === 'voucher_date' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
                    <th className="px-3 py-2 cursor-pointer select-none whitespace-nowrap" onClick={() => toggleSort('summary')}>
                      Summary{sort.col === 'summary' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
                    <th className="px-3 py-2 w-48 whitespace-nowrap">Source</th>
                    {/* NC's 制单 / 审核 / 记账, stacked rather than three columns:
                        three more w-28 columns cost 21rem of a row whose useful
                        content is Summary, and the checker and poster are the
                        same person on most of this book's vouchers. */}
                    <th className="px-3 py-2 w-40 whitespace-nowrap">Prepared / Checked</th>
                    <th className="px-3 py-2 w-20 cursor-pointer select-none whitespace-nowrap" onClick={() => toggleSort('source_subsystem')}>
                      System{sort.col === 'source_subsystem' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
                    <th className="px-3 py-2 w-32 text-right cursor-pointer select-none whitespace-nowrap" onClick={() => toggleSort('total_debit')}>
                      Debit (CAD){sort.col === 'total_debit' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
                    <th className="px-3 py-2 w-28 cursor-pointer select-none whitespace-nowrap" onClick={() => toggleSort('status')}>
                      Status{sort.col === 'status' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 && (
                    <tr><td colSpan={canAct ? 9 : 8} className="px-3 py-6 text-center text-neutral-400">No vouchers match the filters.</td></tr>
                  )}
                  {items.map((v, i) => (
                    <tr key={v.id} onClick={() => setDetailId(v.id)}
                        className={cn('cursor-pointer border-t border-neutral-100 hover:bg-primary-50/40', i % 2 && 'bg-neutral-50/40')}>
                      {canAct && (
                        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                          {(v.status === 'draft' || v.status === 'reviewed') && (
                            <input type="checkbox" checked={selected.has(v.id)} onChange={() => toggle(v.id)} />
                          )}
                        </td>
                      )}
                      <td className="px-3 py-2 font-mono text-xs whitespace-nowrap">{v.voucher_word} · {v.jv_number}</td>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-600 whitespace-nowrap">{v.voucher_date}</td>
                      {/* The only column allowed to wrap; break-words so a long
                          unbroken token cannot push the row wider than its share. */}
                      <td className="px-3 py-2 text-neutral-700 break-words">{v.summary || '—'}</td>
                      {/* Doc number on its own line: "ap_invoice · AP-20260916-0010"
                          on one line needs ~28 mono chars, and the number is the
                          half people scan for. */}
                      <td className="px-3 py-2 text-xs text-neutral-500">
                        {v.source_doc_type ? (
                          <>
                            <span className="block font-mono text-neutral-600 truncate"
                                  title={v.source_doc_number ?? undefined}>
                              {v.source_doc_number || '—'}
                            </span>
                            <span className="block truncate" title={v.source_doc_type}>
                              {v.source_doc_type}
                            </span>
                          </>
                        ) : '—'}
                      </td>
                      {/* The NC subsystem CODE, with the full name on hover:
                          "Gain/Loss Carry-Forward" spelled out costs 160px of a
                          row whose useful content is Summary, and AP / AR / GL
                          is what finance reads anyway. */}
                      <td className="px-3 py-2 text-xs text-neutral-500">
                        {v.nc_prepared_name || v.nc_checked_name ? (
                          <>
                            <span className="block truncate text-neutral-700"
                                  title={v.nc_prepared_name ?? undefined}>
                              {v.nc_prepared_name || '—'}
                            </span>
                            <span className="block truncate"
                                  title={[v.nc_checked_name, v.nc_manager_name]
                                    .filter(Boolean).join(' · ') || undefined}>
                              {v.nc_checked_name === v.nc_manager_name
                                ? (v.nc_checked_name || '—')
                                : [v.nc_checked_name, v.nc_manager_name].filter(Boolean).join(' · ') || '—'}
                            </span>
                          </>
                        ) : '—'}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-600"
                          title={v.source_subsystem_label ?? undefined}>
                        {v.source_subsystem ?? '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">{money(v.total_local_debit)}</td>
                      <td className="px-3 py-2">
                        <JvStatusBadge status={v.status} />
                        {/* Only ever rendered for a non-normal voucher: 41,334 of
                            this book's 41,335 are normal, so a badge on every row
                            would be pure noise — but a discarded one must be
                            impossible to miss, since it used to be invisible. */}
                        {v.nc_voucher_state && v.nc_voucher_state !== 'normal' && (
                          <span className="mt-1 block w-fit rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800">
                            {v.nc_voucher_state === 'discarded' ? 'Discarded'
                              : v.nc_voucher_state === 'tempsave' ? 'Temp-saved' : 'Error'}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-3 flex items-center justify-between text-sm text-neutral-500">
              <span>{total.toLocaleString()} voucher{total === 1 ? '' : 's'}</span>
              <div className="flex items-center gap-2">
                <button onClick={() => gotoPage(Math.max(0, page - 1))} disabled={page === 0} className={secondaryBtn}>
                  <ChevronLeft className="h-4 w-4" /> Prev
                </button>
                <span>Page {page + 1} / {pageCount}</span>
                <button onClick={() => gotoPage(Math.min(pageCount - 1, page + 1))} disabled={page >= pageCount - 1} className={secondaryBtn}>
                  Next <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {detailId && (
        <JvDetailModal jvId={detailId} canAct={canAct}
                       onClose={() => setDetailId(null)} onActed={refresh} />
      )}
      {showNcSync && (
        <NcSyncModal onClose={() => setShowNcSync(false)} onSynced={refresh} />
      )}
    </PortalChromeLayout>
  )
}
