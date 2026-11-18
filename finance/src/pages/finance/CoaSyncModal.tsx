/**
 * COA sync — preview then apply. Two states, no polling: the sync is
 * synchronous (350 accounts read in ~1-2s), so preview returns the diff
 * directly and apply returns the counts.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { financeApi } from '@/lib/api'

export interface CoaSyncStatus {
  can_sync: boolean
  configured: boolean
  last_run: { started_at: string | null; error: string | null } | null
}

interface Change { [field: string]: [unknown, unknown] }
interface Preview {
  accounts: {
    to_insert: number; to_update: number; to_deactivate: number; unchanged: number
    updates: { code: string; reactivated: boolean; changes: Change }[]
    deactivations: { code: string; name: string }[]
  }
  aux_items: { to_insert: number; to_delete: number; unchanged: number }
  referenced_by_mappings: { account_code: string; mapping_type: string; source_code: string }[]
}
interface ApplyResult {
  accounts_inserted: number; accounts_updated: number; accounts_deactivated: number
  aux_items_inserted: number; aux_items_deleted: number
}

const btn = 'rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50'

export function CoaSyncModal({ onClose, onSynced }:
                             { onClose: () => void; onSynced: () => void }) {
  const qc = useQueryClient()
  const [preview, setPreview] = useState<Preview | null>(null)
  const [result, setResult] = useState<ApplyResult | null>(null)
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const runPreview = async () => {
    setBusy('preview'); setErr(null)
    try {
      setPreview(await financeApi.post<Preview>('/coa-sync/preview', {}))
    } catch (e) {
      setErr((e as Error).message)
    } finally { setBusy(null) }
  }

  const runApply = async () => {
    setBusy('apply'); setErr(null)
    try {
      setResult(await financeApi.post<ApplyResult>('/coa-sync/apply', {}))
      qc.invalidateQueries({ queryKey: ['coa'] })
    } catch (e) {
      setErr((e as Error).message)
    } finally { setBusy(null) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="max-h-[85vh] w-full max-w-3xl overflow-y-auto rounded-xl bg-surface p-5 shadow-xl">
        <h2 className="mb-1 text-lg font-semibold text-fg">Sync Chart of Accounts from NC</h2>
        <p className="mb-4 text-sm text-fg-muted">
          NC is the source of truth. Accounts NC no longer returns are deactivated, never deleted.
        </p>

        {err && (
          <div className="mb-3 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger-fg">{err}</div>
        )}

        {!preview && !result && (
          <button className={`${btn} bg-primary-700 text-white`} disabled={busy === 'preview'}
                  onClick={runPreview}>
            {busy === 'preview' ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Preview changes'}
          </button>
        )}

        {preview && !result && (
          <>
            <div className="mb-3 grid grid-cols-4 gap-2 text-sm">
              <Stat label="New" value={preview.accounts.to_insert} />
              <Stat label="Updated" value={preview.accounts.to_update} />
              <Stat label="Deactivated" value={preview.accounts.to_deactivate} />
              <Stat label="Unchanged" value={preview.accounts.unchanged} />
            </div>
            <p className="mb-3 text-sm text-fg-secondary">
              Aux dimensions: +{preview.aux_items.to_insert} / −{preview.aux_items.to_delete}
              {' '}(unchanged {preview.aux_items.unchanged})
            </p>

            {preview.referenced_by_mappings.length > 0 && (
              <div className="mb-3 rounded-lg bg-warning-soft px-3 py-2 text-sm text-warning-fg">
                <AlertTriangle className="mr-1 inline h-4 w-4" />
                {preview.referenced_by_mappings.length} account(s) about to be deactivated are
                still referenced by posting mappings:{' '}
                {preview.referenced_by_mappings
                  .map((m) => `${m.account_code} (${m.mapping_type}/${m.source_code})`).join(', ')}
              </div>
            )}

            {preview.accounts.updates.length > 0 && (
              <div className="mb-3 max-h-64 overflow-y-auto rounded-lg border border-line">
                <table className="w-full text-left text-xs">
                  <thead className="bg-surface-subtle text-fg-secondary">
                    <tr><th className="p-2">Account</th><th className="p-2">Field</th>
                        <th className="p-2">Before</th><th className="p-2">After</th></tr>
                  </thead>
                  <tbody>
                    {preview.accounts.updates.flatMap((u) =>
                      Object.entries(u.changes).map(([f, [before, after]]) => (
                        <tr key={`${u.code}-${f}`} className="border-t border-line-subtle">
                          <td className="p-2 font-mono">{u.code}{u.reactivated ? ' (reactivated)' : ''}</td>
                          <td className="p-2">{f}</td>
                          <td className="p-2 text-fg-muted">{String(before)}</td>
                          <td className="p-2 font-medium">{String(after)}</td>
                        </tr>
                      )))}
                  </tbody>
                </table>
              </div>
            )}

            {preview.accounts.deactivations.length > 0 && (
              <div className="mb-3">
                <p className="mb-1 text-xs font-medium text-fg-secondary">
                  Accounts to be deactivated (not deleted — historical postings keep resolving):
                </p>
                <div className="max-h-64 overflow-y-auto rounded-lg border border-line">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-surface-subtle text-fg-secondary">
                      <tr><th className="p-2">Code</th><th className="p-2">Name</th></tr>
                    </thead>
                    <tbody>
                      {preview.accounts.deactivations.map((d) => (
                        <tr key={d.code} className="border-t border-line-subtle">
                          <td className="p-2 font-mono">{d.code}</td>
                          <td className="p-2">{d.name}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="flex justify-end gap-2">
              <button className={`${btn} border border-line-strong`} onClick={onClose}>Cancel</button>
              <button className={`${btn} bg-primary-700 text-white`} disabled={busy === 'apply'}
                      onClick={runApply}>
                {busy === 'apply' ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Apply'}
              </button>
            </div>
          </>
        )}

        {result && (
          <>
            <div className="mb-4 rounded-lg bg-success-soft px-3 py-2 text-sm text-success-fg">
              Synced. Accounts: +{result.accounts_inserted} / ~{result.accounts_updated} /
              −{result.accounts_deactivated}. Aux: +{result.aux_items_inserted} /
              −{result.aux_items_deleted}.
            </div>
            <div className="flex justify-end">
              <button className={`${btn} bg-primary-700 text-white`} onClick={onSynced}>Close</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-line p-2 text-center">
      <div className="text-lg font-semibold text-fg">{value}</div>
      <div className="text-xs text-fg-muted">{label}</div>
    </div>
  )
}
