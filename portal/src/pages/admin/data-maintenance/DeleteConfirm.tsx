import { useEffect, useState } from 'react'
import type { EntitySchema, CascadeSummary } from '@/services/adminApi'
import { adminApi } from '@/services/adminApi'
import { useAdminDelete } from '@/hooks/useAdmin'
import { CascadeBreakdown, splitCascade } from './cascadeDisplay'

interface Props {
  schema: EntitySchema
  record: Record<string, unknown>
  onClose: () => void
}

export function DeleteConfirm({ schema, record, onClose }: Props) {
  const [cascade, setCascade] = useState<CascadeSummary | null>(null)
  const [error, setError] = useState('')
  const del = useAdminDelete(schema.system, schema.key)
  const id = String(record.id)

  useEffect(() => {
    adminApi.preview(schema.system, schema.key, id).then(setCascade).catch((e) =>
      setError(e instanceof Error ? e.message : 'Preview failed'))
  }, [schema.system, schema.key, id])

  const confirm = async () => {
    setError('')
    try { await del.mutateAsync(id); onClose() }
    catch (e) { setError(e instanceof Error ? e.message : 'Delete failed') }
  }

  const parts = cascade ? splitCascade(cascade) : null
  // A preview that already names a blocker is a delete the backend will refuse:
  // let the admin read why instead of finding out through a failed request.
  const isBlocked = !!parts && parts.blocked.length > 0

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h3 className="text-base font-semibold text-red-700">
          Delete {schema.label} · {String(record[schema.number_field] ?? '')}
        </h3>
        <p className="mt-2 text-sm text-neutral-600">
          This cascades across the shared database and cannot be undone.
        </p>
        <div className="mt-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-sm">
          {!cascade && !error && <p className="text-neutral-400">Computing impact…</p>}
          {parts && <CascadeBreakdown parts={parts} removedLabel="Will be deleted" />}
        </div>
        {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100">Cancel</button>
          <button onClick={confirm} disabled={del.isPending || !cascade || isBlocked}
            className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50">
            {del.isPending ? 'Deleting…' : 'Delete permanently'}
          </button>
        </div>
      </div>
    </div>
  )
}
