import { useCallback, useEffect, useRef, useState } from 'react'
import { Database, Download, RefreshCw, AlertTriangle, CheckCircle2, Loader2, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  pmsImportService as svc,
  type PmsConfig, type PmsRun, type PmsReport,
} from '@/services/pmsImport'

const fmt = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString() : '—'

function CountGrid({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data || {}).filter(([, v]) => v)
  if (!entries.length) return null
  return (
    <div className="rounded-lg border border-neutral-200 p-3">
      <div className="text-xs font-semibold text-neutral-500 uppercase mb-1.5">{title}</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {entries.map(([k, v]) => (
          <span key={k} className="tabular-nums"><b>{v}</b> <span className="text-neutral-500">{k}</span></span>
        ))}
      </div>
    </div>
  )
}

function ReportView({ r }: { r: PmsReport }) {
  const warn = (x: { count: number; sample: string[] }) =>
    x && x.count > 0 ? `${x.count}: ${x.sample.slice(0, 10).join(', ')}${x.count > 10 ? ' …' : ''}` : null
  const warnings: [string, string | null][] = [
    ['Unmatched vendors', warn(r.unmatched_vendors)],
    ['Unmapped cost-centers', warn(r.unmapped_cost_centers)],
    ['Appliers → system user', warn(r.unmatched_appliers)],
  ]
  return (
    <div className="flex flex-col gap-2 mt-2">
      <div className="text-xs text-neutral-500">{r.dry_run ? 'DRY-RUN — nothing was written' : 'COMMITTED'}{r.watermark_advanced_to ? ` · watermark → ${fmt(r.watermark_advanced_to)}` : ''}</div>
      <CountGrid title="Inserted" data={r.inserted} />
      <CountGrid title="Updated (incremental)" data={r.updated} />
      <CountGrid title="Skipped — locally edited in EPMS" data={r.skipped_conflict} />
      <CountGrid title="Skipped — already present" data={r.skipped_existing} />
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-neutral-600">
        {r.created_vendors > 0 && <span><b>{r.created_vendors}</b> vendors auto-created</span>}
        {r.applier_fallback > 0 && <span><b>{r.applier_fallback}</b> docs → system user</span>}
        {r.pa_orphan_no_po > 0 && <span><b>{r.pa_orphan_no_po}</b> PAs skipped (no PO)</span>}
        {r.invoices_no_vendor > 0 && <span><b>{r.invoices_no_vendor}</b> invoices w/o vendor</span>}
      </div>
      {r.attachments && (r.attachments.uploaded || r.attachments.skipped_existing || r.attachments.failed) ? (
        <div className="text-sm text-neutral-600">
          <b>Invoice attachments</b> — {r.attachments.uploaded} uploaded, {r.attachments.skipped_existing} existing
          {r.attachments.failed ? `, ${r.attachments.failed} failed` : ''}
          {r.attachments.no_invoice ? `, ${r.attachments.no_invoice} no-invoice` : ''}
        </div>
      ) : null}
      {warnings.filter(([, v]) => v).map(([label, v]) => (
        <div key={label} className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1">
          <b>{label}</b> — {v}
        </div>
      ))}
    </div>
  )
}

function StatusBadge({ s }: { s: PmsRun['status'] }) {
  const map = {
    running: ['bg-blue-50 text-blue-700', Loader2, 'Running'],
    success: ['bg-green-50 text-green-700', CheckCircle2, 'Success'],
    error: ['bg-red-50 text-red-700', AlertTriangle, 'Error'],
  } as const
  const [cls, Icon, label] = map[s]
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium', cls)}>
      <Icon className={cn('h-3 w-3', s === 'running' && 'animate-spin')} /> {label}
    </span>
  )
}

export function PmsImportPanel() {
  const [config, setConfig] = useState<PmsConfig | null>(null)
  const [current, setCurrent] = useState<PmsRun | null>(null)
  const [runs, setRuns] = useState<PmsRun[]>([])
  const [dryRun, setDryRun] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  // /runs strips the heavy `report` payload; fetch the full run on expand.
  const [details, setDetails] = useState<Record<string, PmsRun>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    const [st, rs] = await Promise.all([svc.getStatus(), svc.listRuns()])
    setCurrent(st.current)
    setRuns(rs.runs)
    return st.current
  }, [])

  useEffect(() => {
    svc.getConfig().then(setConfig).catch((e) => setError(String(e)))
    refresh().catch((e) => setError(String(e)))
  }, [refresh])

  // Poll while a run is in progress.
  useEffect(() => {
    if (current?.status === 'running' && !timer.current) {
      timer.current = setInterval(() => {
        refresh().then((c) => {
          if (!c || c.status !== 'running') {
            if (timer.current) { clearInterval(timer.current); timer.current = null }
            svc.getConfig().then(setConfig)
          }
        })
      }, 2000)
    }
    return () => { if (timer.current) { clearInterval(timer.current); timer.current = null } }
  }, [current?.status, refresh])

  const start = async (phase: 'full' | 'incremental') => {
    setError(null); setBusy(true)
    try {
      const run = await svc.run(phase, dryRun)
      setCurrent(run)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const running = current?.status === 'running'
  const noSp = config && !config.sharepoint_configured

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-lg font-semibold text-neutral-900 flex items-center gap-2">
          <Database className="h-5 w-5 text-primary-600" /> PMS Data Import
        </h2>
        <p className="mt-1 text-sm text-neutral-500">
          Import legacy Purchase Requests, Orders, Payments &amp; Invoices from the SharePoint PMS.
          {config && <> Source: <span className="text-neutral-700">{config.site}</span></>}
        </p>
      </div>

      {noSp && (
        <div className="flex items-center gap-2 text-sm text-amber-800 bg-amber-50 rounded-lg px-3 py-2">
          <AlertTriangle className="h-4 w-4" /> SharePoint credentials are not configured (SP_USER / SP_PASSWORD). Import is disabled.
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 text-sm text-red-700 bg-red-50 rounded-lg px-3 py-2">
          <AlertTriangle className="h-4 w-4" /> {error}
        </div>
      )}

      {/* Controls */}
      <div className="rounded-xl border border-neutral-200 bg-white p-4 flex flex-col gap-3">
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-neutral-700 select-none">
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} disabled={running} />
            Dry-run (preview only, write nothing)
          </label>
          <span className="text-xs text-neutral-400">Last incremental watermark: {fmt(config?.last_sync)}</span>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button onClick={() => start('full')} disabled={running || busy || !!noSp}>
            <Download className="h-4 w-4 mr-1.5" /> Full import
          </Button>
          <Button variant="secondary" onClick={() => start('incremental')} disabled={running || busy || !!noSp || !config?.last_sync}>
            <RefreshCw className="h-4 w-4 mr-1.5" /> Incremental sync
          </Button>
          {!config?.last_sync && (
            <span className="self-center text-xs text-neutral-400">Run a committed full import first to enable incremental.</span>
          )}
        </div>
        <p className="text-xs text-neutral-400">
          Full = import everything (new docs only). Incremental = sync docs whose SharePoint
          <i> Modified</i> is newer than the last sync; existing docs get header-level updates,
          locally-edited docs are skipped.
        </p>
      </div>

      {/* Current run */}
      {current && (
        <div className="rounded-xl border border-neutral-200 bg-white p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm">
              <StatusBadge s={current.status} />
              <span className="font-medium">{current.phase === 'full' ? 'Full import' : 'Incremental sync'}</span>
              {current.dry_run && <span className="text-xs text-neutral-400">(dry-run)</span>}
              <span className="text-neutral-500">— {current.step}</span>
            </div>
            <span className="text-xs text-neutral-400">{fmt(current.started_at)}</span>
          </div>
          {current.error && <div className="mt-2 text-sm text-red-700">{current.error}</div>}
          {current.report && <ReportView r={current.report} />}
        </div>
      )}

      {/* History */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-neutral-700">Run history</h3>
          <button onClick={() => refresh()} className="text-xs text-primary-600 hover:underline flex items-center gap-1">
            <RefreshCw className="h-3 w-3" /> Refresh
          </button>
        </div>
        <div className="rounded-xl border border-neutral-200 bg-white divide-y divide-neutral-100">
          {runs.length === 0 && <div className="p-4 text-sm text-neutral-400">No runs yet.</div>}
          {runs.map((run) => (
            <div key={run.id}>
              <button
                onClick={async () => {
                  const next = expanded === run.id ? null : run.id
                  setExpanded(next)
                  if (next && !details[run.id]) {
                    try {
                      const full = await svc.getRun(run.id)
                      setDetails((d) => ({ ...d, [run.id]: full }))
                    } catch { /* fall back to the summary row */ }
                  }
                }}
                className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-neutral-50 text-sm">
                <StatusBadge s={run.status} />
                <span className="font-medium w-28">{run.phase === 'full' ? 'Full import' : 'Incremental'}</span>
                {run.dry_run && <span className="text-xs text-neutral-400">dry-run</span>}
                <span className="text-neutral-500 flex-1">{fmt(run.started_at)}</span>
                <span className="text-xs text-neutral-400">{run.triggered_by}</span>
                <ChevronDown className={cn('h-4 w-4 text-neutral-400 transition-transform', expanded === run.id && 'rotate-180')} />
              </button>
              {expanded === run.id && (() => {
                const full = details[run.id] ?? run
                return (
                  <div className="px-4 pb-3">
                    {full.error && <div className="text-sm text-red-700 mb-1">{full.error}</div>}
                    {full.report
                      ? <ReportView r={full.report} />
                      : <div className="text-xs text-neutral-400">{details[run.id] ? 'No report.' : 'Loading…'}</div>}
                  </div>
                )
              })()}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
