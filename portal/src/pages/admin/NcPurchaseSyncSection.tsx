/**
 * NC Purchase Sync — admin section that triggers a full / incremental import
 * of NC65 purchase orders + goods receipts and watches its progress (polls
 * status while a run is live). Rendered only inside AdminPanel, which is
 * already gated to system_admin, so no extra role check here. English-only
 * copy per project convention.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, DatabaseZap, Loader2 } from 'lucide-react'
import { epmsApi } from '@/lib/api'
import { cn } from '@/lib/utils'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'

const FULL_CONFIRM = 'FULL RELOAD'

function SectionHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-6 border-b border-neutral-100 pb-4">
      <h2 className="text-lg font-semibold text-neutral-900">{title}</h2>
      <p className="mt-0.5 text-sm text-neutral-500">{description}</p>
    </div>
  )
}

export interface NcPurchaseSyncRun {
  id: string
  mode: string
  status: 'running' | 'success' | 'failed' | string
  started_at: string | null
  finished_at: string | null
  watermark_from: string | null
  watermark_to: string | null
  pos_upserted: number
  po_lines_upserted: number
  grs_upserted: number
  gr_lines_upserted: number
  skipped_no_vendor: number
  skipped_consumed: number
  /** Orders mirrored under a suffixed number because the ERP number was taken.
   *  Normal — NC issues the same vbillcode to genuinely different orders. */
  renamed_number_collision: number
  /** Orders that reached UniOps not at all. Should always be 0. */
  skipped_number_collision: number
  error: string | null
}

export interface NcPurchaseSyncStatus {
  configured: boolean
  can_sync: boolean
  can_set_cutover?: boolean
  cutover: string | null
  /** Minutes between automatic syncs; 0 = the schedule is off and this button
   *  is the only trigger. */
  interval_minutes: number
  /** When the scheduler will next pick it up. Null when the schedule is off. */
  next_due_at: string | null
  current_run: NcPurchaseSyncRun | null
  last_run: NcPurchaseSyncRun | null
}

function RunCounters({ run }: { run: NcPurchaseSyncRun }) {
  return (
    <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs text-neutral-500 sm:grid-cols-3">
      <span>{Number(run.pos_upserted).toLocaleString()} POs</span>
      <span>{Number(run.po_lines_upserted).toLocaleString()} PO lines</span>
      <span>{Number(run.grs_upserted).toLocaleString()} GRs</span>
      <span>{Number(run.gr_lines_upserted).toLocaleString()} GR lines</span>
      {run.skipped_no_vendor > 0 && <span>{Number(run.skipped_no_vendor).toLocaleString()} skipped (no vendor)</span>}
      {run.skipped_consumed > 0 && <span>{Number(run.skipped_consumed).toLocaleString()} skipped (consumed)</span>}
      {run.renamed_number_collision > 0 && (
        // Not an error: the ERP reuses a document number across genuinely
        // different orders, and UniOps numbers must be unique. Shown because a
        // PO whose number is not the ERP's is a surprise when somebody goes
        // looking for it.
        <span title="The ERP number was already taken, so these orders were mirrored under a numbered suffix">
          {Number(run.renamed_number_collision).toLocaleString()} renumbered
        </span>
      )}
      {run.skipped_number_collision > 0 && (
        // This one IS an error: the order is not in UniOps at all. It used to
        // happen on every collision and reached nothing but a container log.
        <span className="text-danger-600"
              title="No free document number — these orders are NOT in UniOps">
          {Number(run.skipped_number_collision).toLocaleString()} skipped (no free number)
        </span>
      )}
    </div>
  )
}

/** Render an instant from the API in the reader's own timezone.
 *
 *  The API sends `timestamptz` values as ISO strings carrying +00:00, and this
 *  card used to print `started_at.slice(0, 16)` — the raw UTC text with the
 *  offset chopped off. On a UTC-4 plant that reads four hours into the future:
 *  a sync that ran at 09:17 said 13:17, right next to a "next run" line that
 *  was converted properly, so the same card disagreed with itself.
 *
 *  24-hour, no "a.m." — one format for every instant in this screen.
 *  ★ Only for instants. Date-only values must NOT go through `new Date()` (see
 *  the project-wide UTC-4 off-by-one-day bug); `cutover` is deliberately still
 *  rendered by slicing its string. */
function localTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-CA', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function RunSummary({ run, label }: { run: NcPurchaseSyncRun; label: string }) {
  return (
    <div className="rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
      <span className="font-medium text-neutral-700">{label}:</span>{' '}
      {run.mode} · {run.status}
      {run.started_at && ` · ${localTime(run.started_at)}`}
      {run.watermark_to && <> · watermark {run.watermark_to}</>}
      {run.status !== 'running' && <RunCounters run={run} />}
      {run.error && <div className="mt-1 text-red-600">{run.error}</div>}
    </div>
  )
}

export function NcPurchaseSyncSection() {
  const qc = useQueryClient()
  const [mode, setMode] = useState<'incremental' | 'full'>('incremental')
  const [confirm, setConfirm] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [cutoverEdit, setCutoverEdit] = useState<string | null>(null)
  const [cutoverSaving, setCutoverSaving] = useState(false)

  const { data: status } = useQuery({
    queryKey: ['nc-purchase-sync-status'],
    queryFn: () => epmsApi.get<NcPurchaseSyncStatus>('/admin/nc-purchase-sync/status'),
    refetchInterval: (q) => (q.state.data?.current_run ? 2000 : false),
  })

  async function saveCutover() {
    if (!cutoverEdit) return
    setCutoverSaving(true)
    setErr(null)
    try {
      await epmsApi.patch('/admin/nc-purchase-sync/cutover', { cutover: cutoverEdit })
      setCutoverEdit(null)
      qc.invalidateQueries({ queryKey: ['nc-purchase-sync-status'] })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save cutover')
    } finally {
      setCutoverSaving(false)
    }
  }
  const running = status?.current_run ?? null

  const start = async () => {
    setErr(null); setStarting(true)
    try {
      await epmsApi.post<{ run_id: string }>('/admin/nc-purchase-sync', {
        mode, confirm: mode === 'full' ? confirm : undefined,
      })
      setConfirm('')
      await qc.invalidateQueries({ queryKey: ['nc-purchase-sync-status'] })
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setStarting(false)
    }
  }

  const canStart = !!status?.configured && !!status?.can_sync && !running && !starting &&
    (mode === 'incremental' || confirm === FULL_CONFIRM)

  return (
    <div>
      <SectionHeader
        title="NC Purchase Sync"
        description="Import purchase orders and goods receipts from NC65 into UniOps for downstream payment processing."
      />

      <div className="max-w-lg space-y-3">
        {!status ? (
          <div className="py-10 text-center text-sm text-neutral-400">Loading…</div>
        ) : !status.configured ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            NC purchase sync is not configured. Contact an administrator to set up the NC65 connection.
          </div>
        ) : (
          <>
            <div className="rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium text-neutral-700">Cutover date:</span>
                <span>{(status.cutover ?? '—').slice(0, 10)}</span>
                {status.can_set_cutover && (
                  <>
                    <span className="mx-1 text-neutral-300">|</span>
                    <input
                      type="date"
                      className="rounded border border-neutral-300 px-2 py-1 text-xs"
                      value={cutoverEdit ?? (status.cutover ?? '').slice(0, 10)}
                      onChange={(e) => setCutoverEdit(e.target.value)}
                    />
                    <button
                      type="button"
                      onClick={saveCutover}
                      disabled={cutoverSaving || !cutoverEdit || cutoverEdit === (status.cutover ?? '').slice(0, 10)}
                      className="rounded bg-primary-600 px-2 py-1 text-xs font-medium text-white disabled:opacity-40"
                    >
                      {cutoverSaving ? 'Saving…' : 'Save'}
                    </button>
                  </>
                )}
              </div>
              <p className="mt-1 text-[11px] text-neutral-400">
                Only NC purchase orders with an order date on/after this are imported. Run a Full reload after changing it.
              </p>
            </div>

            {status.last_run && !running && <RunSummary run={status.last_run} label="Last sync" />}

            {running ? (
              <div className="rounded-lg border border-neutral-200 px-3 py-3 text-sm">
                <div className="flex items-center gap-2 text-neutral-700">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Sync in progress ({running.mode})…
                </div>
                <RunCounters run={running} />
              </div>
            ) : (
              <>
                <div className="space-y-2 text-sm">
                  <label className="flex items-start gap-2">
                    <input type="radio" checked={mode === 'incremental'}
                           onChange={() => setMode('incremental')} className="mt-0.5" />
                    <span>
                      <span className="font-medium">Incremental</span>
                      <span className="block text-xs text-neutral-500">
                        Import only purchase orders and goods receipts that are new or changed in NC
                        since the last sync watermark.
                      </span>
                    </span>
                  </label>
                  <label className="flex items-start gap-2">
                    <input type="radio" checked={mode === 'full'}
                           onChange={() => setMode('full')} className="mt-0.5" />
                    <span>
                      <span className="font-medium">Full reload</span>
                      <span className="block text-xs text-neutral-500">
                        Re-import all NC purchase orders and goods receipts since the cutover date.
                      </span>
                    </span>
                  </label>
                </div>

                {mode === 'full' && (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2">
                    <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-red-700">
                      <AlertTriangle className="h-3.5 w-3.5" />
                      Destructive: re-imports every NC purchase order and goods receipt.
                      Type {FULL_CONFIRM} to enable.
                    </div>
                    <input value={confirm} onChange={(e) => setConfirm(e.target.value)}
                           placeholder={FULL_CONFIRM} className={cn(inputCls, 'w-full font-mono')} />
                  </div>
                )}

                {err && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}

                <div className="flex justify-end border-t border-neutral-100 pt-3">
                  <button onClick={start} disabled={!canStart} className={primaryBtn}>
                    {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <DatabaseZap className="h-4 w-4" />}
                    Start Sync
                  </button>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
