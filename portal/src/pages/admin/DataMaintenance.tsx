import { useState } from 'react'
import { useAuthStore } from '@/store/auth'
import { useRolePermissions } from '@/hooks/useRolePermissions'
import { useAdminEntities } from '@/hooks/useAdmin'
import type { EntitySchema } from '@/services/adminApi'
import { PortalPageLayout } from '@/components/layout/PortalPageLayout'
import { EntityTable } from './data-maintenance/EntityTable'
import { RecordEditForm } from './data-maintenance/RecordEditForm'
import { DeleteConfirm } from './data-maintenance/DeleteConfirm'
import { Button } from '@uniops/shell'

export default function DataMaintenance() {
  const { user } = useAuthStore()
  const perms = useRolePermissions().data?.permissions
  const { data: entities, isLoading } = useAdminEntities()
  const [activeKey, setActiveKey] = useState<string>('')
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null)
  const [deleting, setDeleting] = useState<Record<string, unknown> | null>(null)

  // UI-level gate; the backend independently enforces the data_maintenance permission.
  // Matches the sidebar gate (navConfig): the data_maintenance permission — granted
  // via the Access Control matrix — drives visibility, not a hardcoded role.
  if (user?.role !== 'system_admin' && !perms?.data_maintenance) {
    return <p className="p-6 text-sm text-danger-fg">You do not have access to Data Maintenance.</p>
  }

  if (isLoading) return <p className="p-6 text-fg-muted">Loading…</p>

  const active: EntitySchema | undefined = entities?.find((e) => e.key === (activeKey || entities[0]?.key))
  const systems = Array.from(new Set((entities ?? []).map((e) => e.system)))

  return (
    <PortalPageLayout activeKey="portal:/admin/data-maintenance">
      <div className="flex flex-col gap-5">
        <div>
          <h1 className="text-lg font-semibold">Data Maintenance</h1>
          <p className="text-sm text-fg-muted">Browse, edit, and cascade-delete records. Every action is audited.</p>
        </div>

        {systems.map((sys) => (
          <div key={sys} className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-fg-muted">{sys.toUpperCase()}</span>
            {entities!.filter((e) => e.system === sys).map((e) => (
              <Button key={e.key} size="sm" variant={active?.key === e.key ? 'primary' : 'subtle'}
                aria-pressed={active?.key === e.key} onClick={() => setActiveKey(e.key)}
                className={active?.key === e.key ? undefined : 'bg-surface-sunken'}>
                {e.label}
              </Button>
            ))}
          </div>
        ))}

        {active && (
          <EntityTable schema={active} onEdit={setEditing} onDelete={setDeleting} />
        )}

        {active && editing && (
          <RecordEditForm schema={active} record={editing} onClose={() => setEditing(null)} />
        )}
        {active && deleting && (
          <DeleteConfirm schema={active} record={deleting} onClose={() => setDeleting(null)} />
        )}
      </div>
    </PortalPageLayout>
  )
}
