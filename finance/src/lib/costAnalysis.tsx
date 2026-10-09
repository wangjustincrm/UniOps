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
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, CheckCircle2, ChevronDown, ChevronRight, Loader2, RefreshCw } from 'lucide-react'
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

// ── Diagnosis: the conclusion first, the data as evidence ───────────────────

export interface Evidence { label: string; value: string; ref: string | null }
export interface RootHop { material: string; period: string; headline: string; conclusion?: string }
export interface Finding {
  kind: 'cost' | 'carry' | 'tail'; period: string; severity: 'high' | 'medium'
  title: string; conclusion: string; because: string[]; evidence: Evidence[]
  steps: { period: string; text: string }[]; related: string[]; root_chain?: RootHop[]
}
export interface Diagnosis {
  material: { code: string; name: string | null }; headline: string
  chain: { period: string; text: string }[]; findings: Finding[]
  root_chain: RootHop[]; related: string[]
}

function prevMonth(p: string): string {
  const y = Number(p.slice(0, 4)); const mo = Number(p.slice(5, 7))
  return mo === 1 ? `${y - 1}-12` : `${y}-${String(mo - 1).padStart(2, '0')}`
}
export function traceLink(code: string, from: string, to: string): string {
  return `/finance/analysis/trace/${encodeURIComponent(code)}?from=${from}&to=${to}`
}

function EvidenceTable({ rows }: { rows: Evidence[] }) {
  return (
    <table className="mt-1 w-full text-xs">
      <tbody>
        {rows.map((e, i) => (
          <tr key={i} className="border-t border-neutral-100">
            <td className="w-1/2 py-1 pr-3 text-neutral-500">{e.label}</td>
            <td className="py-1 pr-3 font-mono tabular-nums text-neutral-800">{e.value}</td>
            <td className="py-1 font-mono text-[11px] text-neutral-400">{e.ref ?? ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function FindingCard({ f }: { f: Finding }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={cn('rounded-lg border bg-white p-3',
      f.severity === 'high' ? 'border-red-200' : 'border-amber-200')}>
      <div className="flex items-start gap-2">
        <SeverityBadge severity={f.severity} />
        <p className="text-sm font-semibold text-neutral-900">{f.title}</p>
      </div>
      <p className="mt-1.5 text-sm leading-relaxed text-neutral-800">{f.conclusion}</p>
      {f.because.length > 0 && (
        <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm leading-relaxed text-neutral-700">
          {f.because.map((b, i) => <li key={i}>{b}</li>)}
        </ul>
      )}
      {f.related.length > 0 && (
        <p className="mt-1.5 text-xs text-neutral-500">
          Related:{' '}
          {f.related.map((r, i) => (
            <span key={r}>{i > 0 && ', '}
              <Link to={traceLink(r, prevMonth(f.period), f.period)} className="font-mono text-[#085E5E] underline">{r}</Link>
            </span>
          ))}
        </p>
      )}
      <button onClick={() => setOpen(!open)} className="mt-2 inline-flex items-center gap-1 text-xs text-[#085E5E]">
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        Evidence ({f.evidence.length})
      </button>
      {open && <EvidenceTable rows={f.evidence} />}
    </div>
  )
}

/** The analysis: headline, root cause upstream, what happened in order, and
 *  each finding with the figures behind it. */
export function DiagnosisPanel({ d, compact = false }: { d: Diagnosis; compact?: boolean }) {
  const ok = d.findings.length === 0
  return (
    <div className={cn('rounded-lg border p-4', ok ? 'border-neutral-200 bg-neutral-50' : 'border-[#085E5E]/30 bg-primary-50/40')}>
      <p className="text-xs font-semibold uppercase tracking-wide text-[#085E5E]">Summary</p>
      <p className="mt-1 text-base font-semibold leading-snug text-neutral-900">{d.headline}</p>

      {d.root_chain.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium text-neutral-600">Where it comes from</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="rounded bg-white px-1.5 py-0.5 font-mono font-semibold">{d.material.code}</span>
            {d.root_chain.map((h) => (
              <span key={h.material} className="inline-flex items-center gap-1.5">
                <ArrowLeft className="h-3 w-3 text-neutral-400" />
                <Link to={traceLink(h.material, prevMonth(h.period), h.period)} title={h.headline}
                      className="rounded bg-white px-1.5 py-0.5 font-mono text-[#085E5E] underline">
                  {h.material} <span className="text-neutral-400">{h.period}</span>
                </Link>
              </span>
            ))}
          </div>
          <p className="mt-1 text-xs text-neutral-600">{d.root_chain[d.root_chain.length - 1].headline}</p>
        </div>
      )}

      {d.chain.length > 0 && !compact && (
        <div className="mt-3">
          <p className="text-xs font-medium text-neutral-600">What happened, in order</p>
          <ol className="mt-1 space-y-1 text-sm">
            {d.chain.map((s, i) => (
              <li key={i} className="flex gap-2">
                <span className="w-5 shrink-0 text-right text-xs text-neutral-400">{i + 1}.</span>
                <span className="shrink-0 font-mono text-xs leading-5 text-neutral-500">{s.period}</span>
                <span className="text-neutral-800">{s.text}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {d.findings.length > 0 && (
        <div className="mt-3 space-y-2">
          {d.findings.map((f, i) => <FindingCard key={i} f={f} />)}
        </div>
      )}
      {!compact && (
        <p className="mt-3 text-[11px] text-neutral-500">
          Every figure above is computed from NC&rsquo;s own ledger and cost data; the tables below are the evidence.
        </p>
      )}
    </div>
  )
}
