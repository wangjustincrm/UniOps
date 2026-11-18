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
import { AlertTriangle, ChevronLeft, ChevronRight, Download, Loader2, Mail, RefreshCw } from 'lucide-react'
import { Button, Input, Modal as ShellModal, SortTh, TBody, THead, Table, Tabs, Td, Th, Tr, sortParams, useSort, Card } from '@uniops/shell'
import { qboApi, ENTITY_TABS, type QboBackfillResult, type QboDetail } from '@/services/qboApi'
import { financeDownload } from '@/lib/api'
import { cn, formatDate, formatDateTime } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const PAGE_SIZE = 50
// After a sync trigger, keep polling for this long even if `current_run` is
// still null in the response we already have in cache — the QboSyncRun row is
// created on the worker thread, slightly after the 202 response, so an
// immediate poll can race it and see current_run: null (which would otherwise
// turn refetchInterval off before the row ever appears).
const POST_TRIGGER_POLL_MS = 20000


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
        <Card inset as="section" className="space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-3">
            {/* Sync and full reload are System Admin only (ISS-0164) — the
                server refuses anyone else and reports it as can_sync. */}
            {status?.can_sync ? (
              <>
                <Button size="md"
                  
                  disabled={!!running || !status?.configured}
                  onClick={() => trigger('incremental')}
                >
                  <RefreshCw className="h-4 w-4" /> Incremental sync
                </Button>
                <Button variant="secondary" size="md"
                  
                  disabled={!!running || !status?.configured}
                  onClick={() => setConfirming(true)}
                >
                  Full reload…
                </Button>
              </>
            ) : status && (
              <span className="text-sm text-fg-muted">Syncing with QuickBooks is limited to System Admin.</span>
            )}
            {status?.can_sync && !status?.configured && (
              <span className="text-sm text-danger-fg">QBO connection is not configured</span>
            )}
          </div>

          {running && (
            <div className="rounded-lg border border-line px-3 py-3 text-sm">
              <div className="flex items-center gap-2 font-medium text-fg-secondary">
                <Loader2 className="h-4 w-4 animate-spin" /> Running ({running.mode})…
              </div>
              <ul className="mt-2 grid grid-cols-2 gap-x-6 gap-y-0.5 font-mono text-xs text-fg-muted md:grid-cols-3">
                {Object.entries(running.counters).map(([k, v]) => (
                  <li key={k}>
                    {k}: +{v.inserted} ~{v.updated}{v.deleted ? ` -${v.deleted}` : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!running && status?.last_run && (
            <div className="text-sm text-fg-secondary">
              Last sync: {status.last_run.mode} · {status.last_run.status}
              {status.last_run.finished_at ? ` · ${formatDateTime(status.last_run.finished_at)}` : ''}
              {status.last_run.error ? <span className="text-danger-fg"> · {status.last_run.error}</span> : null}
            </div>
          )}
        </Card>

        {confirming && (
          <ShellModal open onClose={() => setConfirming(false)} title="Full reload" closeOnBackdrop closeOnEscape={false}
            icon={<AlertTriangle className="h-4 w-4 text-danger-fg" />} size="full" className="max-w-md"
            footer={<>
              <Button variant="secondary" size="sm" onClick={() => setConfirming(false)}>Cancel</Button>
              <Button variant="danger" size="sm" onClick={() => trigger('full')}>Full reload</Button>
            </>}>
            <p className="text-sm text-fg-secondary">
              Re-pulls every entity from QuickBooks. This can take many minutes. Continue?
            </p>
          </ShellModal>
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
  return formatDate(String(v).slice(0, 10))
}

/** Render a cell value by column type: amount → 2dp, date/time → YYYY-MM-DD, else raw. */
function cell(c: string, v: unknown): string {
  if (isAmountCol(c)) return num(v)
  if (isDateCol(c)) return fmtDate(v)
  return String(v ?? '')
}

function QboTabs() {
  // Fill Remittance Emails writes vendor master data: System Admin only, like
  // the sync (ISS-0164). Same cached status query as the page header.
  const canSync = useQuery({ queryKey: ['qbo-status'], queryFn: qboApi.status }).data?.can_sync ?? false
  const [tab, setTab] = useState(ENTITY_TABS[0].slug)
  const [q, setQ] = useState('')
  const [qInput, setQInput] = useState('')
  const [page, setPage] = useState(1)
  const [openId, setOpenId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  // Columns are the mirror table's own (every one but `raw`); the API takes
  // any of them as a sort key and pages the result, so it must sort.
  const [sort, onSort, setSort] = useSort<string>(null, () => setPage(1))

  const { data, isFetching } = useQuery({
    queryKey: ['qbo-browse', tab, q, page, sort],
    queryFn: () => qboApi.browse(tab, { q, page, page_size: PAGE_SIZE, ...sortParams(sort) }),
  })
  const items = data?.items ?? []
  const cols = items[0] ? Object.keys(items[0]).filter((c) => c !== 'raw') : []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const switchTab = (slug: string) => {
    setTab(slug); setQ(''); setQInput(''); setPage(1); setOpenId(null); setExportError(null)
    setSort(null)   // each entity has its own columns
  }

  return (
    <section className="space-y-3">
      <Tabs items={ENTITY_TABS.map((t) => ({ key: t.slug, label: t.label }))} value={tab} onChange={switchTab} />

      <div className="flex flex-wrap items-center gap-2">
        <form className="flex items-center gap-1"
          onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); setPage(1) }}>
          <Input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Search name / doc #"
            className="w-56"
          />
          <Button variant="secondary" size="md" type="submit">
            Search
          </Button>
        </form>
        <Button variant="secondary" size="md"
          type="button"
          
          disabled={exporting}
          onClick={async () => {
            setExporting(true)
            setExportError(null)
            try {
              await financeDownload(qboApi.exportPath(tab, q), `qbo-${tab}.csv`)
            } catch (e) {
              setExportError(e instanceof Error ? e.message : 'Export failed')
            } finally {
              setExporting(false)
            }
          }}
        >
          {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          Export CSV
        </Button>
        {tab === 'vendors' && canSync && <VendorEmailBackfill />}
      </div>

      {exportError && (
        <p className="text-sm text-danger-fg">{exportError}</p>
      )}

      {tab === 'vendor-credits' && (
        <p className="rounded-lg border border-line bg-surface-subtle px-3 py-2 text-xs text-fg-secondary">
          This is the permanent record of QuickBooks vendor credits and is kept
          after QuickBooks is retired. <strong>balance</strong> is the unapplied
          remainder, so <strong>0.00</strong> means the credit was already fully
          applied inside QuickBooks. Only credits with a balance above zero were
          brought into UniOps as spendable vendor credit.
        </p>
      )}

      <div className="overflow-auto rounded-lg border border-line max-h-[70vh]">
        <Table density="compact">
            <THead className="sticky top-0 z-10">
              <Tr>
                {cols.map((c) => (
                  <SortTh key={c} label={c} sortKey={c} sort={sort} onSort={onSort}
                          align={isAmountCol(c) ? 'right' : 'left'}
                          className="whitespace-nowrap border-b border-line bg-surface-subtle px-3 py-2 font-medium" />
                ))}
              </Tr>
            </THead>
            <TBody>
              {isFetching && (
                <Tr><Td align="center" colSpan={cols.length || 1} className="text-fg-muted">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" /></Td></Tr>
              )}
              {!isFetching && items.length === 0 && (
                <Tr><Td align="center" colSpan={cols.length || 1} className="text-fg-muted">No rows.</Td></Tr>
              )}
              {items.map((row, i) => (
                <Tr
                  key={String(row.qbo_id)}
                  onClick={() => setOpenId(String(row.qbo_id))}
                  title="Open detail"
                  className={cn('cursor-pointer border-t border-line-subtle hover:bg-surface-sunken', i % 2 && 'bg-surface-subtle/40')}
                >
                  {cols.map((c) => (
                    <Td key={c} className={cn('whitespace-nowrap px-3 py-2', isAmountCol(c) && 'text-right')}>
                      {cell(c, row[c])}
                    </Td>
                  ))}
                </Tr>
              ))}
            </TBody>
        </Table>
      </div>

      <div className="mt-3 flex items-center justify-between text-sm text-fg-muted">
        <span>{total.toLocaleString()} row{total === 1 ? '' : 's'}</span>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="md" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
            <ChevronLeft className="h-4 w-4" /> Prev
          </Button>
          <span>Page {page} / {pageCount}</span>
          <Button variant="secondary" size="md" onClick={() => setPage((p) => Math.min(pageCount, p + 1))} disabled={page >= pageCount}>
            Next <ChevronRight className="h-4 w-4" />
          </Button>
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
    <ShellModal open onClose={onClose} title={<>{entity} · {id}</>} closeOnBackdrop closeOnEscape={false} size="full" className="max-w-3xl">
      {!data && (
        <div className="py-8 text-center text-fg-muted"><Loader2 className="mx-auto h-5 w-5 animate-spin" /></div>
      )}

      {data && (
        <>
          <Table className="mb-4">
            <TBody>
              {Object.entries(data.header).filter(([k]) => k !== 'raw').map(([k, v]) => (
                <Tr key={k}>
                  <Td className="pr-4 align-top text-fg-muted">{k}</Td>
                  <Td className="text-fg">{cell(k, v)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>

          {data.lines.length > 0 && (
            <div className="mb-4">
              <h3 className="mb-1 text-sm font-semibold text-fg-secondary">Lines</h3>
              <div className="overflow-hidden rounded-lg border border-line">
                <div className="overflow-x-auto">
                  <Table density="compact">
                    <THead>
                      <Tr>
                        {Object.keys(data.lines[0]).filter((c) => c !== 'raw').map((c) => (
                          <Th key={c}>{c}</Th>
                        ))}
                      </Tr>
                    </THead>
                    <TBody>
                      {data.lines.map((ln, i) => (
                        <Tr key={i}>
                          {Object.keys(data.lines[0]).filter((c) => c !== 'raw').map((c) => (
                            <Td key={c} className={cn('whitespace-nowrap px-3 py-2', isAmountCol(c) && 'text-right')}>
                              {cell(c, ln[c])}
                            </Td>
                          ))}
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                </div>
              </div>
            </div>
          )}

          {data.attachments.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold text-fg-secondary">Attachments</h3>
              <ul className="space-y-0.5 text-sm">
                {data.attachments.map((a) => (
                  <li key={a.attachment_qbo_id}>
                    <Button variant="link"
                      type="button"
                      disabled={downloadingId === a.attachment_qbo_id}
                      onClick={() => downloadAttachment(a.attachment_qbo_id)}
                     
                    >
                      {downloadingId === a.attachment_qbo_id && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                      {a.attachment_qbo_id}
                    </Button>
                  </li>
                ))}
              </ul>
              {downloadErr && <p className="mt-1 text-xs text-danger-fg">{downloadErr}</p>}
            </div>
          )}
        </>
      )}
    </ShellModal>,
    document.body,
  )
}

function VendorEmailBackfill() {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<QboBackfillResult | null>(null)

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const r = await qboApi.backfillVendorEmails()
      setResult(r)
      setConfirming(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button variant="secondary" size="md" onClick={() => { setError(null); setConfirming(true) }}>
        <Mail className="h-4 w-4" /> Fill Remittance Emails
      </Button>

      {confirming && (
        <ShellModal open onClose={() => setConfirming(false)} busy={busy} title="Fill Remittance Emails" closeOnBackdrop closeOnEscape={false}
          icon={<Mail className="h-4 w-4" />} size="full" className="max-w-md"
          footer={<>
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => setConfirming(false)}>Cancel</Button>
            <Button size="sm" disabled={busy} onClick={run}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />} Fill emails
            </Button>
          </>}>
          <div className="space-y-3">
            <p className="text-sm text-fg-secondary">
              Copies each QuickBooks vendor email into the matching EPMS vendor's
              Remittance Email — only where it is currently empty. Existing values
              are never overwritten.
            </p>
            {error && <p className="text-sm text-danger-fg">{error}</p>}
          </div>
        </ShellModal>
      )}

      {result && (
        <ShellModal open onClose={() => setResult(null)} title="Backfill result" closeOnBackdrop closeOnEscape={false} size="full" className="max-w-2xl">
          <p className="text-sm text-fg-secondary">
            {result.updated.length} filled · {result.skipped_has_value} already had a value ·{' '}
            {result.ambiguous.length} ambiguous · {result.unmatched_qbo.length} unmatched
          </p>

          {result.updated.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold text-fg-secondary">Filled</h3>
              <div className="overflow-hidden rounded-lg border border-line">
                <Table density="compact">
                  <THead>
                    <Tr>
                      <Th>Code</Th>
                      <Th>Vendor</Th>
                      <Th>Email</Th>
                    </Tr>
                  </THead>
                  <TBody>
                    {result.updated.map((u) => (
                      <Tr key={u.code}>
                        <Td className="whitespace-nowrap">{u.code}</Td>
                        <Td>{u.name}</Td>
                        <Td>{u.email}</Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </div>
            </div>
          )}

          {result.ambiguous.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold text-fg-secondary">
                Ambiguous names (skipped — resolve manually)
              </h3>
              <ul className="max-h-40 space-y-0.5 overflow-auto text-sm text-fg-secondary">
                {result.ambiguous.map((a, i) => (
                  <li key={i}>{a.name} <span className="text-xs text-fg-muted">({a.side === 'qbo' ? 'duplicate in QuickBooks' : 'duplicate in EPMS'})</span></li>
                ))}
              </ul>
            </div>
          )}

          {result.unmatched_qbo.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-semibold text-fg-secondary">
                No matching EPMS vendor
              </h3>
              <ul className="max-h-40 space-y-0.5 overflow-auto text-sm text-fg-secondary">
                {result.unmatched_qbo.map((n) => <li key={n}>{n}</li>)}
              </ul>
            </div>
          )}
        </ShellModal>
      )}
    </>
  )
}
