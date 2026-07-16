import { useState } from 'react'
import { useAuthStore } from '@/store/auth'
import { useAdminEntities } from '@/hooks/useAdmin'
import type { EntitySchema } from '@/services/adminApi'
import { PortalPageLayout } from '@/components/layout/PortalPageLayout'
import { EntityTable } from './data-maintenance/EntityTable'
import { RecordEditForm } from './data-maintenance/RecordEditForm'
import { DeleteConfirm } from './data-maintenance/DeleteConfirm'

export default function DataMaintenance() {
  const { user } = useAuthStore()
  const { data: entities, isLoading } = useAdminEntities()
  const [activeKey, setActiveKey] = useState<string>('')
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null)
  const [deleting, setDeleting] = useState<Record<string, unknown> | null>(null)

  // UI-level gate; the backend independently enforces the data_maintenance permission.
  if (user?.role !== 'system_admin') {
    return <p className="p-6 text-sm text-red-600">You do not have access to Data Maintenance.</p>
  }

  if (isLoading) return <p className="p-6 text-neutral-400">Loading…</p>

  const active: EntitySchema | undefined = entities?.find((e) => e.key === (activeKey || entities[0]?.key))
  const systems = Array.from(new Set((entities ?? []).map((e) => e.system)))

  return (
    <PortalPageLayout activeKey="portal:/admin/data-maintenance" title="Data Maintenance">
      <div className="flex flex-col gap-5">
        <div>
          <h1 className="text-lg font-semibold">Data Maintenance</h1>
          <p className="text-sm text-neutral-500">Browse, edit, and cascade-delete records. Every action is audited.</p>
        </div>

        {systems.map((sys) => (
          <div key={sys} className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold uppercase text-neutral-400">{sys}</span>
            {entities!.filter((e) => e.system === sys).map((e) => (
              <button key={e.key} onClick={() => setActiveKey(e.key)}
                className={`rounded-lg px-3 py-1.5 text-sm ${active?.key === e.key ? 'bg-primary-600 text-white' : 'bg-neutral-100 text-neutral-700 hover:bg-neutral-200'}`}>
                {e.label}
              </button>
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
