import { useCallback, useEffect, useRef, useState } from 'react'
import { Database, Download, RefreshCw, Paperclip, AlertTriangle, CheckCircle2, Loader2, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn, formatDateTime } from '@/lib/utils'
import { Checkbox, MenuItem, Badge, Card } from '@uniops/shell'
import {
  pmsImportService as svc,
  type PmsConfig, type PmsRun, type PmsReport,
} from '@/services/pmsImport'

const fmt = (iso: string | null | undefined) =>
  iso ? formatDateTime(iso) : '—'

const phaseLabel = (p: PmsRun['phase']) =>
  p === 'full' ? 'Full import' : p === 'attachments' ? 'Import Attachments' : 'Incremental sync'

function CountGrid({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data || {}).filter(([, v]) => v)
  if (!entries.length) return null
  return (
    <div className="rounded-lg border border-line p-3">
      <div className="text-xs font-medium text-fg-muted mb-1.5">{title}</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {entries.map(([k, v]) => (
          <span key={k} className="tabular-nums"><b>{v}</b> <span className="text-fg-muted">{k}</span></span>
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
      <div className="text-xs text-fg-muted">{r.dry_run ? 'DRY-RUN — nothing was written' : 'COMMITTED'}{r.watermark_advanced_to ? ` · watermark → ${fmt(r.watermark_advanced_to)}` : ''}</div>
      <CountGrid title="Inserted" data={r.inserted} />
      <CountGrid title="Updated (incremental)" data={r.updated} />
      <CountGrid title="Skipped — locally edited in EPMS" data={r.skipped_conflict} />
      <CountGrid title="Skipped — already present" data={r.skipped_existing} />
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-fg-secondary">
        {r.created_vendors > 0 && <span><b>{r.created_vendors}</b> vendors auto-created</span>}
        {r.applier_fallback > 0 && <span><b>{r.applier_fallback}</b> docs → system user</span>}
        {r.pa_orphan_no_po > 0 && <span><b>{r.pa_orphan_no_po}</b> PAs skipped (no PO)</span>}
        {(r.invoices_discarded_no_po ?? 0) > 0 && <span><b>{r.invoices_discarded_no_po}</b> invoices discarded (no PO link)</span>}
        {r.invoices_no_vendor > 0 && <span><b>{r.invoices_no_vendor}</b> invoices w/o vendor</span>}
      </div>
      {r.attachments && (r.attachments.uploaded || r.attachments.skipped_existing || r.attachments.failed) ? (
        <div className="text-sm text-fg-secondary">
          <b>Invoice attachments</b> — {r.attachments.uploaded} uploaded, {r.attachments.skipped_existing} existing
          {r.attachments.failed ? `, ${r.attachments.failed} failed` : ''}
          {r.attachments.no_invoice ? `, ${r.attachments.no_invoice} no-invoice` : ''}
        </div>
      ) : null}
      {r.doc_attachments && (
        <div className="rounded-lg border border-line p-3">
          <div className="text-xs font-medium text-fg-muted mb-1.5">Document attachments</div>
          <div className="flex flex-col gap-0.5 text-sm text-fg-secondary">
            {(['pr', 'po', 'pa'] as const).map((k) => {
              const a = r.doc_attachments![k]
              if (!a) return null
              return (
                <span key={k} className="tabular-nums">
                  <span className="font-medium text-fg">{k.toUpperCase()}</span> — {a.uploaded} uploaded, {a.skipped_existing} existing
                  {a.no_doc ? `, ${a.no_doc} no-doc` : ''}{a.failed ? `, ${a.failed} failed` : ''}
                </span>
              )
            })}
          </div>
        </div>
      )}
      {warnings.filter(([, v]) => v).map(([label, v]) => (
        <div key={label} className="text-xs text-warning-fg bg-warning-soft rounded px-2 py-1">
          <b>{label}</b> — {v}
        </div>
      ))}
    </div>
  )
}

function StatusBadge({ s }: { s: PmsRun['status'] }) {
  const map = {
    running: ['blue', Loader2, 'Running'],
    success: ['success', CheckCircle2, 'Success'],
    error: ['danger', AlertTriangle, 'Error'],
  } as const
  const [tone, Icon, label] = map[s]
  return (
    <Badge variant={tone}>
      <Icon className={cn('h-3 w-3', s === 'running' && 'animate-spin')} /> {label}
    </Badge>
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

  const start = async (phase: 'full' | 'incremental' | 'attachments') => {
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
        <h2 className="text-lg font-semibold text-fg flex items-center gap-2">
          <Database className="h-5 w-5 text-primary-600" /> PMS Data Import
        </h2>
        <p className="mt-1 text-sm text-fg-muted">
          Import legacy Purchase Requests, Orders, Payments &amp; Invoices from the SharePoint PMS.
          {config && <> Source: <span className="text-fg-secondary">{config.site}</span></>}
        </p>
      </div>

      {noSp && (
        <div className="flex items-center gap-2 text-sm text-warning-fg bg-warning-soft rounded-lg px-3 py-2">
          <AlertTriangle className="h-4 w-4" /> SharePoint credentials are not configured (SP_USER / SP_PASSWORD). Import is disabled.
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 text-sm text-danger-fg bg-danger-soft rounded-lg px-3 py-2">
          <AlertTriangle className="h-4 w-4" /> {error}
        </div>
      )}

      {/* Controls */}
      <Card className="p-4 flex flex-col gap-3">
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-fg-secondary select-none">
            <Checkbox checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} disabled={running} />
            Dry-run (preview only, write nothing)
          </label>
          <span className="text-xs text-fg-muted">Last incremental watermark: {fmt(config?.last_sync)}</span>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button onClick={() => start('full')} disabled={running || busy || !!noSp}>
            <Download className="h-4 w-4 mr-1.5" /> Full import
          </Button>
          <Button variant="secondary" onClick={() => start('incremental')} disabled={running || busy || !!noSp || !config?.last_sync}>
            <RefreshCw className="h-4 w-4 mr-1.5" /> Incremental sync
          </Button>
          <Button variant="secondary" onClick={() => start('attachments')} disabled={running || busy || !!noSp}>
            <Paperclip className="h-4 w-4 mr-1.5" /> Import Attachments
          </Button>
          {!config?.last_sync && (
            <span className="self-center text-xs text-fg-muted">Run a committed full import first to enable incremental.</span>
          )}
        </div>
        <p className="text-xs text-fg-muted">
          Full = import everything (new docs only). Incremental = sync docs whose SharePoint
          <i> Modified</i> is newer than the last sync; existing docs get header-level updates,
          locally-edited docs are skipped.
        </p>
      </Card>

      {/* Current run */}
      {current && (
        <Card className="p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm">
              <StatusBadge s={current.status} />
              <span className="font-medium">{phaseLabel(current.phase)}</span>
              {current.dry_run && <span className="text-xs text-fg-muted">(dry-run)</span>}
              <span className="text-fg-muted">— {current.step}</span>
            </div>
            <span className="text-xs text-fg-muted">{fmt(current.started_at)}</span>
          </div>
          {current.error && <div className="mt-2 text-sm text-danger-fg">{current.error}</div>}
          {current.report && <ReportView r={current.report} />}
        </Card>
      )}

      {/* History */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-fg-secondary">Run history</h3>
          <Button variant="link" onClick={() => refresh()} className="text-xs">
            <RefreshCw className="h-3 w-3" /> Refresh
          </Button>
        </div>
        <Card className="divide-y divide-line-subtle">
          {runs.length === 0 && <div className="p-4 text-sm text-fg-muted">No runs yet.</div>}
          {runs.map((run) => (
            <div key={run.id}>
              <MenuItem
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
               >
                <StatusBadge s={run.status} />
                <span className="font-medium w-28">{phaseLabel(run.phase)}</span>
                {run.dry_run && <span className="text-xs text-fg-muted">dry-run</span>}
                <span className="text-fg-muted flex-1">{fmt(run.started_at)}</span>
                <span className="text-xs text-fg-muted">{run.triggered_by}</span>
                <ChevronDown className={cn('h-4 w-4 text-fg-muted transition-transform', expanded === run.id && 'rotate-180')} />
              </MenuItem>
              {expanded === run.id && (() => {
                const full = details[run.id] ?? run
                return (
                  <div className="px-4 pb-3">
                    {full.error && <div className="text-sm text-danger-fg mb-1">{full.error}</div>}
                    {full.report
                      ? <ReportView r={full.report} />
                      : <div className="text-xs text-fg-muted">{details[run.id] ? 'No report.' : 'Loading…'}</div>}
                  </div>
                )
              })()}
            </div>
          ))}
        </Card>
      </div>
    </div>
  )
}
