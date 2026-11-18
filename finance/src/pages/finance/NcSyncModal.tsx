/**
 * NC Sync modal — trigger a full / incremental NC65 voucher import and watch
 * its progress (polls /nc-sync/status while a run is live). system_admin only
 * (the launcher button is gated by status.can_sync). English-only copy.
 */
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, DatabaseZap, Loader2 } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'

import { Input, Radio, Modal as ShellModal, Button } from '@uniops/shell'

const FULL_CONFIRM = 'FULL RELOAD'

export interface NcSyncRun {
  id: string; mode: string; status: string
  started_at: string | null; finished_at: string | null
  watermark_from: string | null; watermark_to: string | null
  vouchers_deleted: number; vouchers_inserted: number
  lines_inserted: number; dims_inserted: number
  unmapped_cc_count: number; error: string | null
}
export interface NcSyncStatus {
  can_sync: boolean; configured: boolean
  current_run: NcSyncRun | null; last_run: NcSyncRun | null
}

function RunSummary({ run, label }: { run: NcSyncRun; label: string }) {
  return (
    <div className="rounded-lg bg-surface-subtle px-3 py-2 text-xs text-fg-secondary">
      <span className="font-medium text-fg-secondary">{label}:</span>{' '}
      {run.mode} · {run.status}
      {run.started_at && ` · ${formatDateTime(run.started_at)}`}
      {run.status !== 'running' && (
        <> · inserted {Number(run.vouchers_inserted).toLocaleString()} vouchers
          {run.vouchers_deleted > 0 && `, deleted ${Number(run.vouchers_deleted).toLocaleString()}`}
          {run.unmapped_cc_count > 0 && `, ${Number(run.unmapped_cc_count).toLocaleString()} unmapped CC lines`}
        </>
      )}
      {run.watermark_to && <> · watermark {run.watermark_to}</>}
      {run.error && <div className="mt-1 text-danger-fg">{run.error}</div>}
    </div>
  )
}

export function NcSyncModal({ onClose, onSynced }: { onClose: () => void; onSynced: () => void }) {
  const qc = useQueryClient()
  const [mode, setMode] = useState<'incremental' | 'full'>('incremental')
  const [confirm, setConfirm] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [startedRunId, setStartedRunId] = useState<string | null>(null)

  const { data: status } = useQuery({
    queryKey: ['nc-sync-status'],
    queryFn: () => financeApi.get<NcSyncStatus>('/nc-sync/status'),
    refetchInterval: (q) => (q.state.data?.current_run ? 2000 : false),
  })
  const running = status?.current_run ?? null
  const finishedOurRun = startedRunId !== null && !running &&
    status?.last_run?.id === startedRunId

  // one-shot: when the run we started finishes, tell the page to refresh.
  // Must be an effect — calling the parent callback during render is illegal.
  useEffect(() => {
    if (finishedOurRun) {
      setStartedRunId(null)
      qc.invalidateQueries({ queryKey: ['account-balance'] })
      qc.invalidateQueries({ queryKey: ['ab-expand'] })
      qc.invalidateQueries({ queryKey: ['ab-vouchers'] })
      qc.invalidateQueries({ queryKey: ['budget-actual'] })
      onSynced()
    }
  }, [finishedOurRun])  // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setErr(null); setStarting(true)
    try {
      const r = await financeApi.post<{ run_id: string }>('/nc-sync', {
        mode, confirm: mode === 'full' ? confirm : undefined,
      })
      setStartedRunId(r.run_id)
      await qc.invalidateQueries({ queryKey: ['nc-sync-status'] })
    } catch (e) { setErr((e as Error).message) } finally { setStarting(false) }
  }

  const canStart = !running && !starting && (mode === 'incremental' || confirm === FULL_CONFIRM)

  return (
    <ShellModal open onClose={onClose} title="NC Voucher Sync" icon={<DatabaseZap className="h-4 w-4" />} closeOnBackdrop closeOnEscape={false} size="full" className="max-w-lg">
      <div className="space-y-3">
        {status?.last_run && !running && <RunSummary run={status.last_run} label="Last sync" />}

        {running ? (
          <div className="rounded-lg border border-line px-3 py-3 text-sm">
            <div className="flex items-center gap-2 text-fg-secondary">
              <Loader2 className="h-4 w-4 animate-spin" />
              Sync in progress ({running.mode})…
            </div>
            <div className="mt-2 font-mono text-xs text-fg-muted">
              {Number(running.vouchers_inserted).toLocaleString()} vouchers ·{' '}
              {Number(running.lines_inserted).toLocaleString()} lines ·{' '}
              {Number(running.dims_inserted).toLocaleString()} dims
            </div>
          </div>
        ) : (
          <>
            <div className="space-y-2 text-sm">
              <label className="flex items-start gap-2">
                <Radio checked={mode === 'incremental'}
                       onChange={() => setMode('incremental')} className="mt-0.5" />
                <span>
                  <span className="font-medium">Incremental</span>
                  <span className="block text-xs text-fg-muted">
                    Import only vouchers that are new in NC (by source pk, creationtime watermark).
                    Existing vouchers are never touched.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2">
                <Radio checked={mode === 'full'}
                       onChange={() => setMode('full')} className="mt-0.5" />
                <span>
                  <span className="font-medium">Full reload</span>
                  <span className="block text-xs text-fg-muted">
                    Delete ALL NC-sourced vouchers (~39.8k) and re-import everything.
                  </span>
                </span>
              </label>
            </div>

            {mode === 'full' && (
              <div className="rounded-lg border border-danger-line bg-danger-soft px-3 py-2">
                <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-danger-fg">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  Destructive: deletes every NC-sourced voucher before re-importing.
                  Type {FULL_CONFIRM} to enable.
                </div>
                <Input value={confirm} onChange={(e) => setConfirm(e.target.value)}
                       placeholder={FULL_CONFIRM} className="font-mono" />
              </div>
            )}

            {err && <div className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-fg">{err}</div>}

            <div className="flex justify-end gap-2 border-t border-line-subtle pt-3">
              <Button variant="secondary" size="md" onClick={onClose}>Close</Button>
              <Button size="md" onClick={start} disabled={!canStart}>
                {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <DatabaseZap className="h-4 w-4" />}
                Start Sync
              </Button>
            </div>
          </>
        )}
      </div>
    </ShellModal>
  )
}
