// NC mirror freshness, the last run's figures and its tie-out, plus Refresh.
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
import { settingsApi, type NcSyncStatus } from './settingsApi'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium uppercase tracking-wide text-neutral-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-neutral-800">{children}</dd>
    </div>
  )
}

function CountsTable({ status }: { status: NcSyncStatus }) {
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
            <th className="py-2 text-right font-medium" title="Rows NC returned that were dropped as unusable (duplicates, missing keys)">
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

function TieoutIssuesTable() {
  const q = useQuery({ queryKey: ['inv-tieout-issues'], queryFn: settingsApi.tieoutIssues })
  if (q.isLoading) return <p className="text-sm text-neutral-500">Loading tie-out issues…</p>
  if (q.isError) {
    return <p className="text-sm text-danger-600">Could not load tie-out issues: {(q.error as Error).message}</p>
  }
  const data = q.data!
  if (data.items.length === 0) return <p className="text-sm text-neutral-500">No issues stored.</p>
  return (
    <div className="space-y-2">
      <p className="text-xs text-neutral-500">
        By check: {Object.entries(data.by_check).map(([k, n]) => `${k} ${n}`).join(' · ')}
        {data.items.length < data.total_stored && ` — showing ${data.items.length} of ${data.total_stored}`}
      </p>
      <div className="max-h-96 overflow-auto rounded-lg border border-neutral-200">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-neutral-50">
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-500">
              <th className="px-3 py-2 font-medium">Check</th>
              <th className="px-3 py-2 font-medium">Measure</th>
              <th className="px-3 py-2 font-medium">Label</th>
              <th className="px-3 py-2 text-right font-medium">Mirror</th>
              <th className="px-3 py-2 text-right font-medium">NC</th>
              <th className="px-3 py-2 text-right font-medium">Diff</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((i) => (
              <tr key={`${i.check}|${i.measure}|${i.key}`} className="border-b border-neutral-100 last:border-0">
                <td className="px-3 py-1.5 font-mono text-xs">{i.check}</td>
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

export function SyncStatusCard({ canAdmin, toasts }: {
  canAdmin: boolean
  toasts: ReturnType<typeof useToasts>
}) {
  const queryClient = useQueryClient()
  const [showIssues, setShowIssues] = useState(false)

  const statusQuery = useQuery({
    queryKey: ['inv-nc-sync-status'],
    queryFn: settingsApi.syncStatus,
    // Keeps "6 min ago" honest on a screen left open, and shows a scheduled
    // run landing without a manual reload.
    refetchInterval: 60_000,
  })

  const refresh = useMutation({
    mutationFn: settingsApi.runSync,
    onSuccess: (r) => {
      const issues = Object.values(r.tieout_issues).reduce((a, b) => a + b, 0)
      if (r.tieout_ok) toasts.success(`NC synced in ${formatDuration(r.duration_ms)} — tie-out OK.`)
      else toasts.error(`NC synced, but the tie-out found ${issues} issue${issues === 1 ? '' : 's'}.`)
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        toasts.error('An NC sync is already running — wait for it to finish.')
      } else if (err instanceof ApiError && err.status === 503) {
        toasts.error('The NC connection is not configured on this server.')
      } else {
        toasts.error(err instanceof Error ? err.message : 'NC sync failed.')
      }
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ['inv-nc-sync-status'] })
      await queryClient.invalidateQueries({ queryKey: ['inv-tieout-issues'] })
      await queryClient.invalidateQueries({ queryKey: ['inv-warehouse-classes'] })
    },
  })

  const s = statusQuery.data
  const busy = refresh.isPending || !!s?.running
  const age = relativeAge(s?.last_success_at ?? null)

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <CardTitle>NC sync status</CardTitle>
          <p className="mt-0.5 text-xs text-neutral-500">Times are shown on Toronto time.</p>
        </div>
        {canAdmin && (
          <Button
            variant="secondary" size="sm"
            onClick={() => refresh.mutate()}
            disabled={busy || (s != null && !s.configured)}
            title={s && !s.configured ? 'The NC connection is not configured on this server' : 'Pull the NC mirrors now'}
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
                The NC connection is not configured on this server, so the mirrors cannot be refreshed.
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
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-neutral-400">Tie-out to NC</p>
              {s.tieout_ok == null ? (
                <p className="text-sm text-neutral-500">Not run yet.</p>
              ) : s.tieout_ok ? (
                <p className="flex items-center gap-1.5 text-sm text-success-700">
                  <CheckCircle2 aria-hidden className="h-4 w-4" /> Mirrors tie out to NC.
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
                  {showIssues && <TieoutIssuesTable />}
                </div>
              )}
            </div>

            <CountsTable status={s} />
          </>
        )}
      </CardContent>
    </Card>
  )
}
