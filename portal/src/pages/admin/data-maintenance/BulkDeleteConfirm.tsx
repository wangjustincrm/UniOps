import { useState } from 'react'
import type { EntitySchema, CascadeSummary } from '@/services/adminApi'
import { useAdminBulkDelete } from '@/hooks/useAdmin'

interface Props {
  schema: EntitySchema
  ids: string[]
  onClose: () => void
  onDone: () => void
}

export function BulkDeleteConfirm({ schema, ids, onClose, onDone }: Props) {
  const [error, setError] = useState('')
  const [result, setResult] = useState<CascadeSummary | null>(null)
  const bulk = useAdminBulkDelete(schema.system, schema.key)

  const confirm = async () => {
    setError('')
    try {
      const res = await bulk.mutateAsync(ids)
      setResult(res.cascade)
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Bulk delete failed')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h3 className="text-base font-semibold text-red-700">
          Delete {ids.length} {schema.label} record{ids.length === 1 ? '' : 's'}
        </h3>
        <p className="mt-2 text-sm text-neutral-600">
          Each record is cascade-deleted across the shared database (downstream documents,
          tasks and approval events included). This cannot be undone.
        </p>

        {result && (
          <div className="mt-3 rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-sm">
            <p className="mb-1 font-medium text-neutral-700">Removed:</p>
            <ul className="space-y-1">
              {Object.entries(result).map(([table, n]) => (
                <li key={table} className="flex justify-between">
                  <span className="text-neutral-700">{table}</span>
                  <span className="font-semibold text-red-700">{n}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {error && <p className="mt-3 text-xs text-red-600">{error}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-100">
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result && (
            <button onClick={confirm} disabled={bulk.isPending || ids.length === 0}
              className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50">
              {bulk.isPending ? `Deleting ${ids.length}…` : `Delete ${ids.length} permanently`}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
