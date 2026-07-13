/**
 * Journal Voucher Center (凭证中心) — Plan 4.
 * List with period/status/search filters + pagination, row selection with
 * batch Review / batch Post, and the shared JvDetailModal for detail +
 * single-voucher lifecycle actions. Styling follows GeneralLedgerPage.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronLeft, ChevronRight, DatabaseZap, Loader2, Search, Send } from 'lucide-react'
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

interface JvList { total: number; items: JvHeader[] }

function money(v: string) {
  const n = Number(v)
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

export default function JournalVouchersPage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [period, setPeriod] = useState(thisMonth())
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [qInput, setQInput] = useState('')
  const [page, setPage] = useState(0)
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

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) })
    if (period) p.set('period', period)
    if (status) p.set('status', status)
    if (q) p.set('q', q)
    return p.toString()
  }, [period, status, q, page])

  const list = useQuery({
    queryKey: ['jv-list', params],
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

  const toggle = (id: string) => setSelected((p) => {
    const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n
  })
  const selectable = items.filter((v) => v.status === 'draft' || v.status === 'reviewed')
  const allSelected = selectable.length > 0 && selectable.every((v) => selected.has(v.id))
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(selectable.map((v) => v.id)))

  const runBatch = async (action: 'review' | 'post') => {
    const eligible = items.filter((v) => selected.has(v.id) &&
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
          <input type="month" value={period}
                 onChange={(e) => { setPeriod(e.target.value); resetPage() }}
                 className={cn(inputCls, 'w-40')} />
          <select value={status} onChange={(e) => { setStatus(e.target.value); resetPage() }} className={inputCls}>
            {STATUSES.map((s) => <option key={s} value={s}>{s ? s[0].toUpperCase() + s.slice(1) : 'All statuses'}</option>)}
          </select>
          <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); resetPage() }}>
            <input value={qInput} onChange={(e) => setQInput(e.target.value)}
                   placeholder="Voucher no. / summary…" className={cn(inputCls, 'w-56')} />
            <button type="submit" className={secondaryBtn}><Search className="h-4 w-4" /></button>
          </form>
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
            <div className="overflow-hidden rounded-lg border border-neutral-200">
              <table className="w-full text-sm">
                <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                  <tr>
                    {canAct && (
                      <th className="w-9 px-3 py-2">
                        <input type="checkbox" checked={allSelected} onChange={toggleAll} />
                      </th>
                    )}
                    <th className="px-3 py-2 w-36">Voucher No.</th>
                    <th className="px-3 py-2 w-24">Date</th>
                    <th className="px-3 py-2">Summary</th>
                    <th className="px-3 py-2 w-32">Source</th>
                    <th className="px-3 py-2 w-32 text-right">Debit (CAD)</th>
                    <th className="px-3 py-2 w-24">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {items.length === 0 && (
                    <tr><td colSpan={canAct ? 7 : 6} className="px-3 py-6 text-center text-neutral-400">No vouchers match the filters.</td></tr>
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
                      <td className="px-3 py-2 font-mono text-xs">{v.voucher_word} · {v.jv_number}</td>
                      <td className="px-3 py-2 font-mono text-xs text-neutral-600">{v.voucher_date}</td>
                      <td className="px-3 py-2 text-neutral-700">{v.summary || '—'}</td>
                      <td className="px-3 py-2 text-xs text-neutral-500">
                        {v.source_doc_type ? `${v.source_doc_type} · ${v.source_doc_number ?? ''}` : '—'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">{money(v.total_local_debit)}</td>
                      <td className="px-3 py-2"><JvStatusBadge status={v.status} /></td>
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
