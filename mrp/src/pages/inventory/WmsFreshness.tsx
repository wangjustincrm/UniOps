// How old is what I am looking at — and a way to fix that on the spot.
//
// Every figure on this page comes from a snapshot of the warehouse taken at
// some past moment. The page used to show only `as of <today>`, which is the
// shelf-life reference date; three weeks after the last sync it still said
// today. This strip states the real thing, and turns amber when the snapshot
// is older than the schedule promises.
//
// Age is measured from `last_success_at` (the last run that actually replaced
// the mirror), never from the last ATTEMPT: with a WMS outage the two diverge,
// and it is precisely then that the header must not look reassuring.
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { usePermissions } from '@/hooks/usePermissions'
import { freshness, relativeAge, syncApi } from './syncApi'
import type { useToasts } from '@/hooks/useToasts'

/** The tooltip's absolute times, in the reader's own timezone and 24-hour —
 *  the same clock the Admin screens use. The strip itself says "6 min ago",
 *  which needs no timezone at all; this is what somebody checks when the
 *  relative age is not precise enough. */
function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-CA', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

export function WmsFreshness({ toasts }: { toasts: ReturnType<typeof useToasts> }) {
  const queryClient = useQueryClient()
  const [syncing, setSyncing] = useState(false)
  const perms = usePermissions()
  const canSync = perms.data?.permissions?.['mrp.param.write'] === true

  const statusQuery = useQuery({
    queryKey: ['wms-sync-status'],
    queryFn: () => syncApi.wmsStatus(),
    // Cheap single-row read. Polling keeps "6 min ago" honest on a screen
    // somebody leaves open all afternoon, and shows a scheduled sync landing
    // without a manual refresh.
    refetchInterval: 60_000,
    retry: false,
  })

  const status = statusQuery.data
  // A 403 (a viewer without mrp.report.view) fails silent, the way the BOM
  // page's freshness strip does: no scary page-level error for what is a
  // secondary indicator.
  if (!status) return null
  // WMS_* left blank is a deployment that has the feature switched off, not a
  // fault to report on the planner's screen.
  if (!status.configured) return null

  const level = freshness(status)
  const age = relativeAge(status.last_success_at)

  async function handleSync() {
    setSyncing(true)
    try {
      const result = await syncApi.runWmsSync()
      if (result.skipped) {
        // The service refuses to replace a real snapshot with an empty
        // extract; saying "synced" here would be a lie about the data.
        toasts.error('WMS returned no rows — the previous snapshot was kept.')
      } else {
        toasts.success(`WMS synced — ${result.lots.toLocaleString('en-US')} batches.`)
      }
      await queryClient.invalidateQueries({ queryKey: ['wms-sync-status'] })
      // The numbers on screen came from the old snapshot; leaving them there
      // after a successful sync is exactly the staleness this strip exists to
      // end.
      // Every list on the page is keyed 'inventory-*' (batches, aging,
      // materials, open PO lines) — match on the prefix rather than listing
      // them, so a tab added later is not silently left showing old numbers.
      await queryClient.invalidateQueries({
        predicate: (q) => typeof q.queryKey[0] === 'string'
          && (q.queryKey[0] as string).startsWith('inventory'),
      })
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toasts.error('A sync is already running — it will finish shortly.')
      } else if (err instanceof ApiError && err.status === 503) {
        toasts.error('WMS connection is not configured.')
      } else {
        toasts.error(err instanceof ApiError ? err.message : 'WMS sync failed.')
      }
      await queryClient.invalidateQueries({ queryKey: ['wms-sync-status'] })
    } finally {
      setSyncing(false)
    }
  }

  const schedule = status.interval_minutes > 0
    ? `Refreshes automatically every ${status.interval_minutes} min.`
    : 'Automatic refresh is off — this data only moves when somebody refreshes it.'

  const tone = {
    fresh: 'text-neutral-500',
    stale: 'text-amber-700',
    error: 'text-red-700',
    unknown: 'text-neutral-400',
  }[level]

  return (
    <div className="flex items-center gap-2">
      <span
        className={cn('flex items-center gap-1.5 text-xs', tone)}
        title={[
          status.last_success_at
            ? `Snapshot taken ${formatDateTime(status.last_success_at)}`
            : 'The mirror has never been synced.',
          status.last_synced_at && status.last_synced_at !== status.last_success_at
            ? `Last attempt ${formatDateTime(status.last_synced_at)} (${status.status})`
            : null,
          `${status.row_count.toLocaleString('en-US')} batches in the last snapshot`,
          schedule,
          status.last_error,
        ].filter(Boolean).join('\n')}
      >
        {(level === 'stale' || level === 'error') && (
          <AlertTriangle aria-hidden className="h-3.5 w-3.5 shrink-0" />
        )}
        {status.running
          ? 'Syncing from WMS…'
          : level === 'error'
            ? `WMS data ${age ? `from ${age}` : 'never synced'} — last sync failed`
            : `WMS data ${age ?? 'never synced'}`}
      </span>
      {canSync && (
        <Button
          variant="secondary" size="sm" onClick={handleSync}
          disabled={syncing || status.running}
          title="Fetch the current warehouse snapshot now"
        >
          {syncing || status.running
            ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
            : <RefreshCw aria-hidden className="h-3.5 w-3.5" />}
          Refresh
        </Button>
      )}
    </div>
  )
}
