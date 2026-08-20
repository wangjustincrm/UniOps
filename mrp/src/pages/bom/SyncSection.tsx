// Sync from NC — design spec §6.6's Sync-button sub-section. "Last synced"
// (gated GET /sync-state, mrp.report.view) is shown to every viewer who can
// read it; the Sync button itself is gated separately and narrower
// (mdm.bom.write) — "权限分离" in the spec. A 403 on the state read (a
// viewer without mrp.report.view) fails silent: the strip just doesn't
// render, rather than surfacing a scary page-level error for what the spec
// treats as a nice-to-have freshness indicator, not the page's core value.
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Loader2, ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react'
import { Button } from '@uniops/shell'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { bomApi, NC_NOT_CONFIGURED_DETAIL, type BomSyncStats } from './bomApi'
import type { useToasts } from '@/hooks/useToasts'

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function hoursAgo(iso: string): number {
  return (Date.now() - new Date(iso).getTime()) / 3_600_000
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-CA', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export function SyncSection({ canSync, toasts }: { canSync: boolean; toasts: ReturnType<typeof useToasts> }) {
  const queryClient = useQueryClient()
  const [syncing, setSyncing] = useState(false)
  const [result, setResult] = useState<BomSyncStats | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)

  const stateQuery = useQuery({
    queryKey: ['bom-sync-state'],
    queryFn: () => bomApi.syncState(),
    retry: false,
  })

  // Best-effort inference, not a live check — mdm-api has no "is NC
  // configured right now" read endpoint (nc_configured() is only enforced
  // inside POST /sync, see bomApi.ts's NC_NOT_CONFIGURED_DETAIL comment).
  // If the last recorded attempt failed with exactly that 503 detail,
  // disable the button up front instead of making the planner click to
  // find out; a click still re-verifies live (handleSync below).
  const ncLikelyNotConfigured = stateQuery.data?.last_error === NC_NOT_CONFIGURED_DETAIL

  async function handleSync() {
    setSyncing(true)
    try {
      const stats = await bomApi.sync()
      setResult(stats)
      setDetailOpen(true)
      // warnings/skipped are surfaced in the toast headline itself, not just
      // buried in the expandable panel — "must be visible" per the brief.
      const parts = [`${stats.boms} BOMs, ${stats.lines} lines, ${stats.substitutes} substitutes synced`]
      if (stats.warnings > 0) parts.push(`${stats.warnings} warning(s)`)
      if (stats.skipped > 0) parts.push(`${stats.skipped} skipped`)
      toasts.success(parts.join(' — ') + '.')
      await queryClient.invalidateQueries({ queryKey: ['bom-sync-state'] })
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toasts.error('Another sync is already running — try again shortly.')
      } else if (err instanceof ApiError && err.status === 503) {
        toasts.error('NC connection is not configured — cannot sync.')
        await queryClient.invalidateQueries({ queryKey: ['bom-sync-state'] })
      } else {
        toasts.error(errMsg(err, 'Sync failed — please retry.'))
      }
    } finally {
      setSyncing(false)
    }
  }

  const lastSuccess = stateQuery.data?.last_success_at
  const stale = lastSuccess ? hoursAgo(lastSuccess) > 24 : false

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        {!stateQuery.isError && (
          <span className={cn('text-xs', stale ? 'font-medium text-amber-700' : 'text-neutral-500')}>
            {lastSuccess
              ? `Last synced: ${formatDateTime(lastSuccess)} (${Math.round(hoursAgo(lastSuccess))}h ago)`
              : 'Never synced'}
          </span>
        )}
        {canSync && (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={handleSync}
            disabled={syncing || ncLikelyNotConfigured}
            title={ncLikelyNotConfigured ? 'NC connection not configured — the last sync attempt failed for this reason.' : undefined}
          >
            {syncing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            {syncing ? 'Syncing from NC…' : 'Sync from NC'}
          </Button>
        )}
      </div>

      {canSync && ncLikelyNotConfigured && !syncing && (
        <p className="text-xs text-amber-700">NC connection not configured.</p>
      )}

      {canSync && result && (
        <div className="mt-1 w-full max-w-sm rounded-md border border-neutral-200 bg-white text-xs shadow-sm">
          <button
            type="button"
            onClick={() => setDetailOpen((v) => !v)}
            className="flex w-full items-center justify-between px-3 py-1.5 font-medium text-neutral-700 hover:bg-neutral-50"
            aria-expanded={detailOpen}
          >
            Last sync result
            {detailOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
          {detailOpen && (
            <div className="border-t border-neutral-100 px-3 py-2">
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
                <Stat label="BOMs" value={result.boms} />
                <Stat label="Lines" value={result.lines} />
                <Stat label="Substitutes" value={result.substitutes} />
                <Stat label="Tombstoned" value={result.tombstoned} />
                <Stat label="Skipped" value={result.skipped} warn={result.skipped > 0} />
                <Stat label="Warnings" value={result.warnings} warn={result.warnings > 0} />
              </dl>
              {result.tombstone_skipped.length > 0 && (
                <p className="mt-2 flex items-start gap-1 text-amber-700">
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                  Tombstone skipped for: {result.tombstone_skipped.join(', ')} (resolved set came back empty this run —
                  existing rows were kept rather than risking a wipe).
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <>
      <dt className="text-neutral-500">{label}</dt>
      <dd className={cn('text-right font-semibold tabular-nums', warn ? 'text-amber-700' : 'text-neutral-900')}>{value}</dd>
    </>
  )
}
