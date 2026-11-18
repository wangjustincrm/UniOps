import { AlertTriangle } from 'lucide-react'
import { useState } from 'react'
import type { EntitySchema, CascadeSummary } from '@/services/adminApi'
import { useAdminBulkDelete } from '@/hooks/useAdmin'
import { CascadeBreakdown, splitCascade } from './cascadeDisplay'

import { Button, Modal } from '@uniops/shell'
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
    // Closes only by Cancel / Close (no Escape, no backdrop), as before.
    <Modal
      open
      onClose={onClose}
      closeOnEscape={false}
      icon={<AlertTriangle className="h-4 w-4 text-danger-fg" />}
      title={`Delete ${ids.length} ${schema.label} record${ids.length === 1 ? '' : 's'}`}
      description="Each record is cascade-deleted across the shared database (downstream documents, tasks and approval events included). This cannot be undone."
      size="full"
      className="max-w-md"
      footer={<>
        <Button variant="subtle" size="xs" onClick={onClose} className="text-sm">
          {result ? 'Close' : 'Cancel'}
        </Button>
        {!result && (
          <Button variant="danger" size="sm" onClick={confirm} disabled={bulk.isPending || ids.length === 0}>
            {bulk.isPending ? `Deleting ${ids.length}…` : `Delete ${ids.length} permanently`}
          </Button>
        )}
      </>}
    >
      <div className="flex flex-col gap-3">
        {result && (
          <div className="rounded-lg border border-line bg-surface-subtle p-3 text-sm">
            <CascadeBreakdown parts={splitCascade(result)} removedLabel="Deleted" />
          </div>
        )}
        {error && <p className="text-xs text-danger-fg">{error}</p>}
      </div>
    </Modal>
  )
}
