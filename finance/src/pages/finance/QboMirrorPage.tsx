/**
 * QuickBooks Mirror (Finance) — read-only browser over the QBO mirror tables
 * synced by scripts/qbo_import. Sync panel (trigger full/incremental, watch
 * progress) + per-entity paged tables + a detail modal with lines/attachments.
 * Not a second book of record — view-only, gated client-side via the
 * `view_finance` nav permission (server is auth-only, see finance-api qbo.py).
 */
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronLeft, ChevronRight, Loader2, RefreshCw, X } from 'lucide-react'
import { qboApi, ENTITY_TABS, type QboDetail } from '@/services/qboApi'
import { financeDownload } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const PAGE_SIZE = 50
// After a sync trigger, keep polling for this long even if `current_run` is
// still null in the response we already have in cache — the QboSyncRun row is
// created on the worker thread, slightly after the 202 response, so an
// immediate poll can race it and see current_run: null (which would otherwise
// turn refetchInterval off before the row ever appears).
const POST_TRIGGER_POLL_MS = 20000

const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'
const dangerBtn = 'flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50'

export default function QboMirrorPage() {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [pollUntil, setPollUntil] = useState(0)
  const { data: status } = useQuery({
    queryKey: ['qbo-status'],
    queryFn: qboApi.status,
    refetchInterval: (q) => (q.state.data?.current_run || Date.now() < pollUntil ? 2000 : false),
  })
  const running = status?.current_run ?? null

  async function trigger(mode: 'full' | 'incremental') {
    await qboApi.triggerSync(mode, mode === 'full' ? 'RELOAD' : undefined)
    // The run row is created on a worker thread just after this 202 returns,
    // so an immediate refetch can still see current_run: null — keep polling
    // for a grace window regardless, until that row shows up.
    setPollUntil(Date.now() + POST_TRIGGER_POLL_MS)
    await qc.invalidateQueries({ queryKey: ['qbo-status'] })
    setConfirming(false)
  }

  return (
    <PortalChromeLayout
      title="QuickBooks Mirror"
      subtitle="Read-only copy of the QuickBooks Online company, synced into UniOps."
    >
      <div className="space-y-6">
        <section className="space-y-3 rounded-lg border border-neutral-200 bg-white p-4">
          <div className="flex flex-wrap items-center gap-3">
            <button
              className={primaryBtn}
              disabled={!!running || !status?.configured}
              onClick={() => trigger('incremental')}
            >
              <RefreshCw className="h-4 w-4" /> Incremental sync
            </button>
            <button
              className={secondaryBtn}
              disabled={!!running || !status?.configured}
              onClick={() => setConfirming(true)}
            >
              Full reload…
            </button>
            {!status?.configured && (
              <span className="text-sm text-red-600">QBO connection is not configured</span>
            )}
          </div>

          {running && (
            <div className="rounded-lg border border-neutral-200 px-3 py-3 text-sm">
              <div className="flex items-center gap-2 font-medium text-neutral-700">
                <Loader2 className="h-4 w-4 animate-spin" /> Running ({running.mode})…
              </div>
              <ul className="mt-2 grid grid-cols-2 gap-x-6 gap-y-0.5 font-mono text-xs text-neutral-500 md:grid-cols-3">
                {Object.entries(running.counters).map(([k, v]) => (
                  <li key={k}>
                    {k}: +{v.inserted} ~{v.updated}{v.deleted ? ` -${v.deleted}` : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!running && status?.last_run && (
            <div className="text-sm text-neutral-600">
              Last sync: {status.last_run.mode} · {status.last_run.status}
              {status.last_run.finished_at ? ` · ${new Date(status.last_run.finished_at).toLocaleString()}` : ''}
              {status.last_run.error ? <span className="text-red-600"> · {status.last_run.error}</span> : null}
            </div>
          )}
        </section>

        {confirming && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => setConfirming(false)}>
            <div className="w-full max-w-md space-y-3 rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
              <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-800">
                <AlertTriangle className="h-4 w-4 text-red-600" /> Full reload
              </h2>
              <p className="text-sm text-neutral-600">
                Re-pulls every entity from QuickBooks. This can take many minutes. Continue?
              </p>
              <div className="flex justify-end gap-2 border-t border-neutral-100 pt-3">
                <button className={secondaryBtn} onClick={() => setConfirming(false)}>Cancel</button>
                <button className={dangerBtn} onClick={() => trigger('full')}>Full reload</button>
              </div>
            </div>
          </div>
        )}

        {/* Entity tabs added in Task 7 */}
        <QboTabs />
      </div>
    </PortalChromeLayout>
  )
}

function num(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  return Number.isNaN(n) ? String(v) : n.toFixed(2)
}

/** Columns that hold Decimal-as-string amounts and should be Number()-coerced/formatted. */
function isAmountCol(c: string): boolean {
  return c.includes('amt') || c.includes('balance') || c === 'exchange_rate'
}

/** Date/timestamp columns (txn_date, due_date, created_at, updated_at, deleted_at, last_updated_time). */
function isDateCol(c: string): boolean {
  return c.endsWith('_at') || c.endsWith('_time') || c.endsWith('_date')
}

/** Show only the calendar date (YYYY-MM-DD). Works for both ISO datetimes and
 *  already-date strings; slicing avoids any timezone shift from re-parsing. */
function fmtDate(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  return String(v).slice(0, 10)
}

/** Render a cell value by column type: amount → 2dp, date/time → YYYY-MM-DD, else raw. */
function cell(c: string, v: unknown): string {
  if (isAmountCol(c)) return num(v)
  if (isDateCol(c)) return fmtDate(v)
  return String(v ?? '')
}

function QboTabs() {
  const [tab, setTab] = useState(ENTITY_TABS[0].slug)
  const [q, setQ] = useState('')
  const [qInput, setQInput] = useState('')
  const [page, setPage] = useState(1)
  const [openId, setOpenId] = useState<string | null>(null)

  const { data, isFetching } = useQuery({
    queryKey: ['qbo-browse', tab, q, page],
    queryFn: () => qboApi.browse(tab, { q, page, page_size: PAGE_SIZE }),
  })
  const items = data?.items ?? []
  const cols = items[0] ? Object.keys(items[0]).filter((c) => c !== 'raw') : []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const switchTab = (slug: string) => {
    setTab(slug); setQ(''); setQInput(''); setPage(1); setOpenId(null)
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap gap-1 border-b border-neutral-200">
        {ENTITY_TABS.map((t) => (
          <button
            key={t.slug}
            onClick={() => switchTab(t.slug)}
            className={cn(
              'px-3 py-1.5 text-sm',
              tab === t.slug
                ? 'border-b-2 border-[#085E5E] font-medium text-[#085E5E]'
                : 'text-neutral-500 hover:text-neutral-700',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <form className="flex items-center gap-1"
          onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); setPage(1) }}>
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Search name / doc #"
            className={cn(inputCls, 'w-56')}
          />
          <button type="submit" className="rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-700 hover:bg-neutral-50">
            Search
          </button>
        </form>
      </div>

      <div className="overflow-auto rounded-lg border border-neutral-200 max-h-[70vh]">
        <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 text-left text-xs text-neutral-500">
              <tr>
                {cols.map((c) => <th key={c} className="whitespace-nowrap border-b border-neutral-200 bg-neutral-50 px-3 py-2 font-medium">{c}</th>)}
              </tr>
            </thead>
            <tbody>
              {isFetching && (
                <tr><td colSpan={cols.length || 1} className="px-3 py-6 text-center text-neutral-400">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" /></td></tr>
              )}
              {!isFetching && items.length === 0 && (
                <tr><td colSpan={cols.length || 1} className="px-3 py-6 text-center text-neutral-400">No rows.</td></tr>
              )}
              {items.map((row, i) => (
                <tr
                  key={String(row.qbo_id)}
                  onClick={() => setOpenId(String(row.qbo_id))}
                  title="Open detail"
                  className={cn('cursor-pointer border-t border-neutral-100 hover:bg-neutral-100', i % 2 && 'bg-neutral-50/40')}
                >
                  {cols.map((c) => (
                    <td key={c} className={cn('whitespace-nowrap px-3 py-2', isAmountCol(c) && 'text-right')}>
                      {cell(c, row[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
        </table>
      </div>

      <div className="mt-3 flex items-center justify-between text-sm text-neutral-500">
        <span>{total.toLocaleString()} row{total === 1 ? '' : 's'}</span>
        <div className="flex items-center gap-2">
          <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} className={secondaryBtn}>
            <ChevronLeft className="h-4 w-4" /> Prev
          </button>
          <span>Page {page} / {pageCount}</span>
          <button onClick={() => setPage((p) => Math.min(pageCount, p + 1))} disabled={page >= pageCount} className={secondaryBtn}>
            Next <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {openId && <DetailModal entity={tab} id={openId} onClose={() => setOpenId(null)} />}
    </section>
  )
}

function DetailModal({ entity, id, onClose }: { entity: string; id: string; onClose: () => void }) {
  const { data } = useQuery<QboDetail>({
    queryKey: ['qbo-detail', entity, id],
    queryFn: () => qboApi.detail(entity, id),
  })
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const [downloadErr, setDownloadErr] = useState<string | null>(null)

  async function downloadAttachment(attachmentQboId: string) {
    setDownloadErr(null)
    setDownloadingId(attachmentQboId)
    try {
      // financeDownload does an authenticated fetch (Authorization: Bearer via
      // authHeaders()) -> blob -> synthetic download link. A bare <a href>
      // navigation can't carry the JWT header, so the CurrentUser-gated
      // attachment endpoint would 403 every click.
      await financeDownload(qboApi.fileUrl(attachmentQboId), attachmentQboId)
    } catch (e) {
      setDownloadErr((e as Error).message)
    } finally {
      setDownloadingId(null)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="max-h-[85vh] w-full max-w-3xl overflow-auto rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between border-b border-neutral-100 pb-3">
          <h2 className="text-base font-semibold text-neutral-800">{entity} · {id}</h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        {!data && (
          <div className="py-8 text-center text-neutral-400"><Loader2 className="mx-auto h-5 w-5 animate-spin" /></div>
        )}

        {data && (
          <>
            <table className="mb-4 text-sm">
              <tbody>
                {Object.entries(data.header).filter(([k]) => k !== 'raw').map(([k, v]) => (
                  <tr key={k}>
                    <td className="pr-4 align-top text-neutral-500">{k}</td>
                    <td className="text-neutral-800">{cell(k, v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {data.lines.length > 0 && (
              <div className="mb-4">
                <h3 className="mb-1 text-sm font-semibold text-neutral-700">Lines</h3>
                <div className="overflow-hidden rounded-lg border border-neutral-200">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                        <tr>
                          {Object.keys(data.lines[0]).filter((c) => c !== 'raw').map((c) => (
                            <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">{c}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {data.lines.map((ln, i) => (
                          <tr key={i} className="border-t border-neutral-100">
                            {Object.keys(data.lines[0]).filter((c) => c !== 'raw').map((c) => (
                              <td key={c} className={cn('whitespace-nowrap px-3 py-2', isAmountCol(c) && 'text-right')}>
                                {cell(c, ln[c])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

            {data.attachments.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-semibold text-neutral-700">Attachments</h3>
                <ul className="space-y-0.5 text-sm">
                  {data.attachments.map((a) => (
                    <li key={a.attachment_qbo_id}>
                      <button
                        type="button"
                        disabled={downloadingId === a.attachment_qbo_id}
                        onClick={() => downloadAttachment(a.attachment_qbo_id)}
                        className="flex items-center gap-1.5 text-[#085E5E] underline hover:no-underline disabled:opacity-50"
                      >
                        {downloadingId === a.attachment_qbo_id && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                        {a.attachment_qbo_id}
                      </button>
                    </li>
                  ))}
                </ul>
                {downloadErr && <p className="mt-1 text-xs text-red-600">{downloadErr}</p>}
              </div>
            )}
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
