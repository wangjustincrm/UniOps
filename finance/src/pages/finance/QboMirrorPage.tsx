/**
 * QuickBooks Mirror (Finance) — read-only browser over the QBO mirror tables
 * synced by scripts/qbo_import. Sync panel (trigger full/incremental, watch
 * progress) + per-entity paged tables + a detail modal with lines/attachments.
 * Not a second book of record — view-only, gated client-side via the
 * `view_finance` nav permission (server is auth-only, see finance-api qbo.py).
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { qboApi } from '@/services/qboApi'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'

const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'
const dangerBtn = 'flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50'

export default function QboMirrorPage() {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const { data: status } = useQuery({
    queryKey: ['qbo-status'],
    queryFn: qboApi.status,
    refetchInterval: (q) => (q.state.data?.current_run ? 2000 : false),
  })
  const running = status?.current_run ?? null

  async function trigger(mode: 'full' | 'incremental') {
    await qboApi.triggerSync(mode, mode === 'full' ? 'RELOAD' : undefined)
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

function QboTabs() {
  return null  // replaced in Task 7
}
