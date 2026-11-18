import { AlertTriangle } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { EntitySchema, CascadeSummary } from '@/services/adminApi'
import { adminApi } from '@/services/adminApi'
import { useAdminDelete } from '@/hooks/useAdmin'
import { CascadeBreakdown, splitCascade } from './cascadeDisplay'

import { Button, Modal as ShellModal } from '@uniops/shell'
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
    <ShellModal open onClose={onClose} title={<>Delete {schema.label} · {String(record[schema.number_field] ?? '')}</>} description="This cascades across the shared database and cannot be undone."
      icon={<AlertTriangle className="h-4 w-4 text-danger-fg" />} closeOnEscape={false} size="full" className="max-w-md"
      footer={<>
        <Button variant="subtle" size="xs" onClick={onClose} className="text-sm">Cancel</Button>
        <Button variant="danger" size="sm" onClick={confirm} disabled={del.isPending || !cascade || isBlocked}>
          {del.isPending ? 'Deleting…' : 'Delete permanently'}
        </Button>
      </>}>
      <div className="flex flex-col gap-3">
        <div className="rounded-lg border border-line bg-surface-subtle p-3 text-sm">
          {!cascade && !error && <p className="text-fg-muted">Computing impact…</p>}
          {parts && <CascadeBreakdown parts={parts} removedLabel="Will be deleted" />}
        </div>
        {error && <p className="text-xs text-danger-fg">{error}</p>}
      </div>
    </ShellModal>
  )
}
