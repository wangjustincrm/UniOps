/**
 * AI Financial Analysis — shared types, formatting and the sync status bar.
 *
 * Backend: finance-api app/api/v1/cost_analysis.py, gated on the
 * finance.analysis.view Access Control key (not view_finance). Spec:
 * docs/superpowers/specs/2026-10-09-ai-financial-analysis-design.md.
 *
 * No notifications anywhere in this module (decision D2) — people come here
 * to look.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'

export interface SyncRun {
  id: string; status: string; started_at: string | null; finished_at: string | null
  counts: Record<string, number> | null; tie_out_ok: boolean | null
  tie_out_diffs: Record<string, unknown> | null; error: string | null
}
export interface SyncStatus {
  can_sync: boolean; configured: boolean; scope_from: string
  current_run: SyncRun | null; last_run: SyncRun | null; last_success: SyncRun | null
  interval_minutes: number; thresholds: Record<string, number>
}

export interface NextMonthFlow {
  period: string; mo_code: string | null; qty: number | null; amount: number | null
  unit: number | null; products: { material_code: string; material_name: string | null }[]
}
export interface Anomaly {
  rule: string; severity: 'high' | 'medium'; title: string; period: string | null
  material_code: string | null; material_name: string | null
  metrics: Record<string, any>; refs: string[]; detail: string | null
  period_closed: boolean | null; next_month?: NextMonthFlow[]
}
export interface AnomaliesResp {
  range: { from: string; to: string }; thresholds: Record<string, number>
  open_periods: string[]; rules: Record<string, { severity: string; title: string }>
  counts: Record<string, number>; anomalies: Anomaly[]
}

export const RULE_HELP: Record<string, string> = {
  A1: 'A production receipt came in at zero or negative unit cost.',
  A2: 'Production unit cost moved more than the threshold against the previous producing month.',
  A3: 'Month-end stock with zero quantity but value left on it, or value below zero on hand.',
  A4: 'A provisional receipt was settled at a different price and the variance is large against what the material consumed that month.',
  A5: 'A warehouse transfer whose in and out legs do not pair on the transfer order they came from.',
  A6: 'A single issue adjustment (IA) above the threshold — NC moved value onto a consumption after the fact.',
  A8: 'The mirror does not agree with NC: a closed month that does not land on NC’s balance, or a sync that did not tie out.',
}

/** YYYY-MM of the local calendar month. Never via toISOString (UTC shifts the month). */
export function thisMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}
export function qty(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return v.toLocaleString(undefined, { maximumFractionDigits: 3 })
}
export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${(v * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`
}
/** Server timestamps are full ISO datetimes — safe to parse; shown in local time. */
export function when(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString()
}

export function SeverityBadge({ severity }: { severity: string }) {
  return (
    <span className={cn('inline-block rounded border px-1.5 py-0.5 text-[11px] font-medium',
      severity === 'high' ? 'border-red-200 bg-red-50 text-red-700'
                          : 'border-amber-200 bg-amber-50 text-amber-800')}>
      {severity === 'high' ? 'High' : 'Medium'}
    </span>
  )
}

export function OpenPeriodBadge() {
  return (
    <span title="NC has not closed this month yet — its figures can still change."
          className="ml-1 inline-block rounded border border-neutral-200 bg-neutral-50 px-1.5 py-0.5 text-[10px] text-neutral-500">
      Not closed in NC
    </span>
  )
}

export function useSyncStatus() {
  return useQuery({
    queryKey: ['cost-analysis-status'],
    queryFn: () => financeApi.get<SyncStatus>('/cost-analysis/status'),
    refetchInterval: (q) => (q.state.data?.current_run ? 3000 : false),
  })
}

/** Last sync, whether it tied out against NC, and (system admin) a sync button. */
export function SyncBar() {
  const qc = useQueryClient()
  const { data, error } = useSyncStatus()
  const sync = useMutation({
    mutationFn: () => financeApi.post('/cost-analysis/sync', {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cost-analysis-status'] }),
  })
  if (error) return null
  if (!data) return <div className="mb-4 h-10" />
  const running = !!data.current_run || sync.isPending
  const last = data.last_success
  const failed = data.last_run && data.last_run.status === 'failed' ? data.last_run : null
  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-xs text-neutral-600">
      {!data.configured ? (
        <span className="text-neutral-500">NC connection is not configured on this server.</span>
      ) : last ? (
        <>
          <span>NC data as of <strong className="text-neutral-800">{when(last.finished_at)}</strong></span>
          {last.tie_out_ok ? (
            <span className="inline-flex items-center gap-1 text-[#085E5E]">
              <CheckCircle2 className="h-3.5 w-3.5" /> Ties out with NC
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 font-medium text-red-700">
              <AlertTriangle className="h-3.5 w-3.5" /> Does not tie out with NC — see Anomalies (A8)
            </span>
          )}
          <span className="text-neutral-400">From {data.scope_from} · syncs every {data.interval_minutes ? `${data.interval_minutes} min` : '— (off)'}</span>
        </>
      ) : (
        <span className="text-neutral-500">Not synced from NC yet.</span>
      )}
      {failed && (
        <span className="text-red-700" title={failed.error ?? ''}>Last sync failed {when(failed.finished_at)}</span>
      )}
      {data.can_sync && data.configured && (
        <button onClick={() => sync.mutate()} disabled={running}
                className="ml-auto inline-flex items-center gap-1 rounded-lg border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50">
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          {running ? 'Syncing…' : 'Sync now'}
        </button>
      )}
      {sync.error && <span className="text-red-700">{(sync.error as Error).message}</span>}
    </div>
  )
}

/** A 403 from this module means the Access Control key is missing — say which. */
export function AccessMessage({ error }: { error: unknown }) {
  const msg = (error as Error)?.message ?? ''
  const denied = /403|permission/i.test(msg)
  return (
    <div className={cn('rounded-lg border px-4 py-3 text-sm',
      denied ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-red-200 bg-red-50 text-red-700')}>
      {denied
        ? 'AI Financial Analysis needs the “AI Financial Analysis” permission (finance.analysis.view). It is granted per role in Portal → Access Control.'
        : msg}
    </div>
  )
}
