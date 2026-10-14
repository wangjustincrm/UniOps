// Mirror freshness, the last run's figures and its tie-out, plus Refresh —
// one card per source: the NC mirrors, and the WMS transaction history.
//
// Age is measured from last_success_at (the last run that replaced the
// mirrors), never from the last attempt: during an NC outage the two diverge,
// and that is exactly when this card must not look reassuring.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Loader2, RefreshCw, XCircle } from 'lucide-react'
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatDateTimeToronto, formatDuration, formatNumber, relativeAge } from '@/lib/time'
import type { useToasts } from '@/hooks/useToasts'
import {
  WMS_TIEOUT_CHECKS, settingsApi, tieoutCheckLabel, type NcSyncStatus,
} from './settingsApi'

export type SyncSource = 'nc' | 'wms'

interface RunOutcome { tieout_ok: boolean; issues: number; duration_ms: number }

const SOURCES: Record<SyncSource, {
  title: string
  name: string
  description: string | null
  statusKey: string[]
  status: () => Promise<NcSyncStatus>
  run: () => Promise<RunOutcome>
  refreshTitle: string
}> = {
  nc: {
    title: 'NC sync status',
    name: 'NC',
    description: null,
    statusKey: ['inv-nc-sync-status'],
    status: settingsApi.syncStatus,
    run: async () => {
      const r = await settingsApi.runSync()
      return { tieout_ok: r.tieout_ok, duration_ms: r.duration_ms,
               issues: Object.values(r.tieout_issues).reduce((a, b) => a + b, 0) }
    },
    refreshTitle: 'Pull the NC mirrors now',
  },
  wms: {
    title: 'WMS history sync',
    name: 'WMS',
    description: 'WMS transaction history since go-live (2025-02-03) — used for WMS stock on past dates and batch trace. Current WMS stock itself comes from MRP\'s WMS sync.',
    statusKey: ['inv-wms-sync-status'],
    status: settingsApi.wmsSyncStatus,
    run: async () => {
      const r = await settingsApi.runWmsSync()
      return { tieout_ok: r.tieout_ok, duration_ms: r.duration_ms, issues: r.tieout_issues }
    },
    refreshTitle: 'Pull the WMS transaction history now',
  },
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-neutral-800">{children}</dd>
    </div>
  )
}

function CountsTable({ status, name }: { status: NcSyncStatus; name: string }) {
  const tables = Array.from(new Set([
    ...Object.keys(status.row_counts), ...Object.keys(status.filtered_counts),
  ])).sort()
  if (tables.length === 0) return null
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-400">
            <th className="py-2 pr-4 font-medium">Mirror table</th>
            <th className="py-2 pr-4 text-right font-medium">Rows</th>
            <th className="py-2 text-right font-medium" title={`Rows ${name} returned that were dropped as unusable (duplicates, missing keys)`}>
              Filtered out
            </th>
          </tr>
        </thead>
        <tbody>
          {tables.map((t) => {
            const filtered = status.filtered_counts[t] ?? 0
            return (
              <tr key={t} className="border-b border-neutral-100 last:border-0">
                <td className="py-1.5 pr-4 font-mono text-xs text-neutral-700">{t}</td>
                <td className="py-1.5 pr-4 text-right tabular-nums">{formatNumber(status.row_counts[t] ?? 0, 0)}</td>
                <td className={cn('py-1.5 text-right tabular-nums', filtered > 0 ? 'text-warning-600' : 'text-neutral-400')}>
                  {formatNumber(filtered, 0)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function diffOf(mirror: string | null, nc: string | null): string {
  if (mirror == null || nc == null) return '—'
  const d = Number(mirror) - Number(nc)
  return Number.isFinite(d) ? formatNumber(d, 4) : '—'
}

function TieoutIssuesTable({ source }: { source: SyncSource }) {
  // The endpoint serves every source's findings. The WMS card asks for its
  // one check; the NC card takes the rest (filtered here, as the endpoint
  // filters by a single check only).
  const q = useQuery({
    queryKey: ['inv-tieout-issues', source],
    queryFn: () => settingsApi.tieoutIssues(source === 'wms' ? WMS_TIEOUT_CHECKS[0] : undefined),
  })
  if (q.isLoading) return <p className="text-sm text-neutral-500">Loading tie-out issues…</p>
  if (q.isError) {
    return <p className="text-sm text-danger-600">Could not load tie-out issues: {(q.error as Error).message}</p>
  }
  const data = q.data!
  const mine = (check: string) => (source === 'wms') === WMS_TIEOUT_CHECKS.includes(check)
  const items = data.items.filter((i) => mine(i.check))
  const byCheck = Object.entries(data.by_check).filter(([k]) => mine(k))
  const totalStored = byCheck.reduce((a, [, n]) => a + n, 0)
  const hidden = data.amount_issues_hidden > 0 && (
    <p className="text-xs text-neutral-500">
      {data.amount_issues_hidden} amount-based issue{data.amount_issues_hidden === 1 ? ' is' : 's are'} hidden
      {' '}— requires the ‘View Inventory Amounts’ permission.
    </p>
  )
  if (items.length === 0) {
    return hidden || <p className="text-sm text-neutral-500">No issues stored.</p>
  }
  return (
    <div className="space-y-2">
      {hidden}
      <p className="text-xs text-neutral-500">
        By check: {byCheck.map(([k, n]) => `${tieoutCheckLabel(k)} ${n}`).join(' · ')}
        {items.length < totalStored && ` — showing ${items.length} of ${totalStored}`}
      </p>
      <div className="max-h-96 overflow-auto rounded-lg border border-neutral-200">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-neutral-50">
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="px-3 py-2 font-medium">Check</th>
              <th className="px-3 py-2 font-medium">Measure</th>
              <th className="px-3 py-2 font-medium">Label</th>
              <th className="px-3 py-2 text-right font-medium">Mirror</th>
              <th className="px-3 py-2 text-right font-medium">{source === 'wms' ? 'WMS' : 'NC'}</th>
              <th className="px-3 py-2 text-right font-medium">Diff</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={`${i.check}|${i.measure}|${i.key}`} className="border-b border-neutral-100 last:border-0">
                <td className="px-3 py-1.5 text-xs" title={i.check}>{tieoutCheckLabel(i.check)}</td>
                <td className="px-3 py-1.5">{i.measure}</td>
                <td className="px-3 py-1.5" title={i.key}>{i.label || i.key}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{formatNumber(i.mirror_value, 4)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{formatNumber(i.nc_value, 4)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-danger-600">{diffOf(i.mirror_value, i.nc_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function SyncStatusCard({ canAdmin, toasts, source = 'nc' }: {
  canAdmin: boolean
  toasts: ReturnType<typeof useToasts>
  source?: SyncSource
}) {
  const cfg = SOURCES[source]
  const queryClient = useQueryClient()
  const [showIssues, setShowIssues] = useState(false)

  const statusQuery = useQuery({
    queryKey: cfg.statusKey,
    queryFn: cfg.status,
    // Keeps "6 min ago" honest on a screen left open, and shows a scheduled
    // run landing without a manual reload.
    refetchInterval: 60_000,
  })

  const refresh = useMutation({
    mutationFn: cfg.run,
    onSuccess: (r) => {
      if (r.tieout_ok) toasts.success(`${cfg.name} synced in ${formatDuration(r.duration_ms)} — tie-out OK.`)
      else toasts.error(`${cfg.name} synced, but the tie-out found ${r.issues} issue${r.issues === 1 ? '' : 's'}.`)
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        // NC and WMS syncs share one lock: either one running blocks both.
        toasts.error(err.message || 'A sync is already running — wait for it to finish.')
      } else if (err instanceof ApiError && err.status === 503) {
        toasts.error(`The ${cfg.name} connection is not configured on this server.`)
      } else {
        toasts.error(err instanceof Error ? err.message : `${cfg.name} sync failed.`)
      }
    },
    onSettled: async () => {
      // Both cards: the two syncs share a lock, so "running" moves on both.
      await queryClient.invalidateQueries({ queryKey: ['inv-nc-sync-status'] })
      await queryClient.invalidateQueries({ queryKey: ['inv-wms-sync-status'] })
      await queryClient.invalidateQueries({ queryKey: ['inv-tieout-issues'] })
      await queryClient.invalidateQueries({ queryKey: ['inv-warehouse-classes'] })
      // NC Inventory page (freshness strip and every table).
      await queryClient.invalidateQueries({ queryKey: ['nc'] })
    },
  })

  const s = statusQuery.data
  const busy = refresh.isPending || !!s?.running
  const age = relativeAge(s?.last_success_at ?? null)

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>{cfg.title}</CardTitle>
          {cfg.description && <p className="mt-0.5 max-w-2xl text-xs text-neutral-600">{cfg.description}</p>}
          <p className="mt-0.5 text-xs text-neutral-500">Times are shown on Toronto time.</p>
        </div>
        {canAdmin && (
          <Button
            variant="secondary" size="sm"
            onClick={() => refresh.mutate()}
            disabled={busy || (s != null && !s.configured)}
            title={s && !s.configured ? `The ${cfg.name} connection is not configured on this server` : cfg.refreshTitle}
          >
            {busy
              ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
              : <RefreshCw aria-hidden className="h-3.5 w-3.5" />}
            {busy ? 'Syncing…' : 'Refresh'}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-5">
        {statusQuery.isLoading && <p className="text-sm text-neutral-500">Loading…</p>}
        {statusQuery.isError && (
          <p className="text-sm text-danger-600">Could not load sync status: {(statusQuery.error as Error).message}</p>
        )}
        {s && (
          <>
            {!s.configured && (
              <div className="flex items-start gap-2 rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-700">
                <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                The {cfg.name} connection is not configured on this server, so {source === 'wms' ? 'the history' : 'the mirrors'} cannot be refreshed.
              </div>
            )}
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Last successful sync">
                {s.last_success_at ? (
                  <>
                    <span className="font-medium">{age}</span>
                    <span className="block text-xs text-neutral-500">{formatDateTimeToronto(s.last_success_at)}</span>
                  </>
                ) : 'Never'}
              </Field>
              <Field label="Last attempt">
                {s.running ? (
                  <Badge variant="info">Running</Badge>
                ) : s.status === 'success' ? (
                  <Badge variant="success">Success</Badge>
                ) : s.status === 'failed' ? (
                  <Badge variant="danger">Failed</Badge>
                ) : (
                  <Badge>None yet</Badge>
                )}
                {s.last_synced_at && s.last_synced_at !== s.last_success_at && (
                  <span className="block text-xs text-neutral-500">{formatDateTimeToronto(s.last_synced_at)}</span>
                )}
              </Field>
              <Field label="Duration">{formatDuration(s.duration_ms)}</Field>
              <Field label="Automatic sync">
                {s.interval_minutes > 0 ? `Every ${s.interval_minutes} min` : 'Off'}
                {s.next_due_at && (
                  <span className="block text-xs text-neutral-500">Next due {formatDateTimeToronto(s.next_due_at)}</span>
                )}
              </Field>
            </dl>

            {s.status === 'failed' && s.last_error && (
              <div className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-700">
                <p className="font-medium">Last sync failed</p>
                <p className="mt-1 whitespace-pre-wrap break-words font-mono text-xs">{s.last_error}</p>
              </div>
            )}

            <div>
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-neutral-400">Tie-out to {cfg.name}</p>
              {s.tieout_ok == null ? (
                <p className="text-sm text-neutral-500">Not run yet.</p>
              ) : s.tieout_ok ? (
                <p className="flex items-center gap-1.5 text-sm text-success-700">
                  <CheckCircle2 aria-hidden className="h-4 w-4" /> {source === 'wms' ? 'Transactions tie out to current WMS lots.' : 'Mirrors tie out to NC.'}
                </p>
              ) : (
                <div className="space-y-3">
                  <button
                    type="button"
                    onClick={() => setShowIssues((v) => !v)}
                    className="flex items-center gap-1.5 text-sm font-medium text-danger-600 hover:underline"
                    aria-expanded={showIssues}
                  >
                    <XCircle aria-hidden className="h-4 w-4" />
                    {s.tieout_issue_count} issue{s.tieout_issue_count === 1 ? '' : 's'}
                    {showIssues
                      ? <ChevronDown aria-hidden className="h-3.5 w-3.5" />
                      : <ChevronRight aria-hidden className="h-3.5 w-3.5" />}
                  </button>
                  {showIssues && <TieoutIssuesTable source={source} />}
                </div>
              )}
            </div>

            <CountsTable status={s} name={cfg.name} />
          </>
        )}
      </CardContent>
    </Card>
  )
}
