import { useState } from 'react'
import type { EntitySchema, CascadeSummary } from '@/services/adminApi'
import { useAdminBulkDelete } from '@/hooks/useAdmin'
import { CascadeBreakdown, splitCascade } from './cascadeDisplay'

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
      <div className="w-full max-w-md rounded-xl bg-surface p-5 shadow-xl">
        <h3 className="text-base font-semibold text-danger-fg">
          Delete {ids.length} {schema.label} record{ids.length === 1 ? '' : 's'}
        </h3>
        <p className="mt-2 text-sm text-fg-secondary">
          Each record is cascade-deleted across the shared database (downstream documents,
          tasks and approval events included). This cannot be undone.
        </p>

        {result && (
          <div className="mt-3 rounded-lg border border-line bg-surface-subtle p-3 text-sm">
            <CascadeBreakdown parts={splitCascade(result)} removedLabel="Deleted" />
          </div>
        )}

        {error && <p className="mt-3 text-xs text-danger-fg">{error}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-fg-secondary hover:bg-surface-sunken">
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result && (
            <button onClick={confirm} disabled={bulk.isPending || ids.length === 0}
              className="rounded-lg bg-danger-solid px-3 py-1.5 text-sm font-semibold text-white hover:bg-danger-700 disabled:opacity-50">
              {bulk.isPending ? `Deleting ${ids.length}…` : `Delete ${ids.length} permanently`}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
