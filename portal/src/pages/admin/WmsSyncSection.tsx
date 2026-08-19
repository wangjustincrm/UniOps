/**
 * WMS Sync — how often the warehouse snapshot refreshes itself, how fresh it
 * is right now, and a way to refresh it on the spot.
 *
 * Why this section exists: mrp-api mirrors the Flux WMS into Postgres (the
 * Inventory screen, aging, and every purchase suggestion read that mirror, not
 * the warehouse). Until now nothing refreshed it on a schedule — the only
 * trigger was an HTTP endpoint with no button anywhere, so the mirror moved
 * when somebody remembered. On release day nobody did, and production ran for
 * two days on an empty locations table.
 *
 * The interval is a planning parameter in mrp-api (`wms_sync_interval_minutes`),
 * not an env var, so changing it here takes effect within one scheduler tick
 * instead of at the next redeploy. 0 turns the schedule off and leaves Sync now
 * as the only way the data moves.
 *
 * Rendered only inside AdminPanel, which is already gated to system_admin, so
 * no extra role check here. English-only copy per project convention.
 */
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, RefreshCw, Warehouse } from 'lucide-react'
import { mrpApi } from '@/lib/api'

const inputCls = 'h-9 w-24 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

const INTERVAL_KEY = 'wms_sync_interval_minutes'
const MAX_INTERVAL_MINUTES = 1440

export interface WmsSyncStatus {
  configured: boolean
  interval_minutes: number
  status: 'success' | 'failed' | 'empty_extract' | null
  /** Last attempt — moves even when the attempt failed. */
  last_synced_at: string | null
  /** Last attempt that actually replaced the mirror: the age of the data. */
  last_success_at: string | null
  row_count: number
  last_error: string | null
  next_due_at: string | null
  running: boolean
}

function SectionHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-6 border-b border-neutral-100 pb-4">
      <h2 className="text-lg font-semibold text-neutral-900">{title}</h2>
      <p className="mt-0.5 text-sm text-neutral-500">{description}</p>
    </div>
  )
}

/** An instant from the API, in the reader's own timezone.
 *
 *  The API sends UTC (`timestamptz` -> ISO with +00:00); on a UTC-4 plant a
 *  raw render reads four hours into the future. 24-hour to match the NC
 *  Purchase Sync card. */
function when(iso: string | null): string {
  if (!iso) return 'never'
  return new Date(iso).toLocaleString('en-CA', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

export function WmsSyncSection() {
  const qc = useQueryClient()
  const [intervalInput, setIntervalInput] = useState<string>('')
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const { data: status } = useQuery({
    queryKey: ['wms-sync-status'],
    queryFn: () => mrpApi.get<WmsSyncStatus>('/admin/wms-sync/status'),
    // Fast while a run is in flight (one takes about five seconds), lazy
    // otherwise — this is a single-row read, but nothing here changes by the
    // second when nobody is doing anything.
    refetchInterval: (q) => (q.state.data?.running ? 2000 : 30000),
  })

  // Seed the input from the server once, then leave it alone: refetching every
  // 30s must not overwrite what an admin is halfway through typing.
  useEffect(() => {
    if (status && intervalInput === '') setIntervalInput(String(status.interval_minutes))
  }, [status, intervalInput])

  async function saveInterval() {
    const minutes = Number(intervalInput)
    if (!Number.isInteger(minutes) || minutes < 0 || minutes > MAX_INTERVAL_MINUTES) {
      setErr('Interval must be a whole number of minutes between 0 and ' + MAX_INTERVAL_MINUTES + '.')
      return
    }
    setSaving(true); setErr(null); setNote(null)
    try {
      await mrpApi.put('/params/' + INTERVAL_KEY, { value: minutes })
      setNote(minutes === 0
        ? 'Automatic sync is off. The mirror will only refresh when somebody presses Sync now.'
        : 'Saved. The warehouse snapshot will refresh every ' + minutes + ' minute(s).')
      await qc.invalidateQueries({ queryKey: ['wms-sync-status'] })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save the interval')
    } finally {
      setSaving(false)
    }
  }

  async function syncNow() {
    setSyncing(true); setErr(null); setNote(null)
    try {
      const r = await mrpApi.post<{ lots: number; locations?: number; skipped?: boolean }>(
        '/admin/wms-sync', {})
      setNote(r.skipped
        // The service refuses to replace a real snapshot with an empty extract,
        // so reporting "synced" here would misdescribe what is on screen.
        ? 'WMS returned no rows — the previous snapshot was kept.'
        : 'Synced ' + r.lots.toLocaleString() + ' batches'
          + (r.locations !== undefined ? ', ' + r.locations.toLocaleString() + ' locations.' : '.'))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setSyncing(false)
      await qc.invalidateQueries({ queryKey: ['wms-sync-status'] })
    }
  }

  const failed = status?.status === 'failed'

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-6">
      <SectionHeader
        title="WMS Sync"
        description="Flux warehouse stock is mirrored into UniOps and read by MRP Inventory, aging and purchase suggestions. This is how often that mirror refreshes."
      />

      {status && !status.configured && (
        <p className="mb-4 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          The WMS connection is not configured for this deployment (WMS_* is
          blank), so nothing can be synced. The schedule below is still saved
          and takes effect once it is configured.
        </p>
      )}

      {/* ── Freshness ─────────────────────────────────────────────────── */}
      <dl className="mb-6 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        <div className="flex justify-between gap-3 border-b border-neutral-100 py-1.5">
          <dt className="text-neutral-500">Snapshot on screen</dt>
          {/* last_success_at, not last_synced_at: with WMS down the two
              diverge, and that is exactly when this must not read as fresh. */}
          <dd className="font-medium text-neutral-900">{when(status?.last_success_at ?? null)}</dd>
        </div>
        <div className="flex justify-between gap-3 border-b border-neutral-100 py-1.5">
          <dt className="text-neutral-500">Batches in it</dt>
          <dd className="font-medium text-neutral-900">
            {status ? status.row_count.toLocaleString() : '—'}
          </dd>
        </div>
        <div className="flex justify-between gap-3 border-b border-neutral-100 py-1.5">
          <dt className="text-neutral-500">Last attempt</dt>
          <dd className={failed ? 'font-medium text-red-600' : 'font-medium text-neutral-900'}>
            {when(status?.last_synced_at ?? null)}
            {status?.status && <span className="ml-1 text-neutral-500">· {status.status}</span>}
          </dd>
        </div>
        <div className="flex justify-between gap-3 border-b border-neutral-100 py-1.5">
          <dt className="text-neutral-500">Next automatic run</dt>
          <dd className="font-medium text-neutral-900">
            {status?.running
              ? 'running now'
              : status?.interval_minutes === 0
                ? 'off'
                : when(status?.next_due_at ?? null)}
          </dd>
        </div>
      </dl>

      {status?.last_error && (
        <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          {status.last_error}
        </p>
      )}

      {/* ── The schedule ──────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm text-neutral-700">
          Sync every (minutes)
          <input
            type="number" min={0} max={MAX_INTERVAL_MINUTES} step={1}
            value={intervalInput}
            onChange={(e) => setIntervalInput(e.target.value)}
            className={inputCls}
          />
        </label>
        <button type="button" className={primaryBtn} onClick={saveInterval} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Warehouse className="h-4 w-4" />}
          Save
        </button>
        <button type="button" className={secondaryBtn} onClick={syncNow}
                disabled={syncing || status?.running || status?.configured === false}>
          {syncing || status?.running
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : <RefreshCw className="h-4 w-4" />}
          Sync now
        </button>
      </div>

      <p className="mt-2 text-xs text-neutral-500">
        One sync reads the whole warehouse and takes about five seconds. 0 turns
        automatic sync off — the mirror then only moves when somebody presses
        Sync now, which is how a three-week-old snapshot goes unnoticed.
      </p>

      {note && <p className="mt-3 text-sm text-emerald-700">{note}</p>}
      {err && <p className="mt-3 text-sm text-red-600">{err}</p>}
    </div>
  )
}
