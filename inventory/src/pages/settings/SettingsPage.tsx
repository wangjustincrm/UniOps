// Inventory → Settings (design §7.5). Everyone with inventory.view can read
// it (the comparison depends on these choices, so they must be visible);
// every write control — Refresh, Save, Add, Edit, Remove — appears only with
// inventory.admin, which is also what inventory-api enforces.
import { ShieldAlert } from 'lucide-react'
import { ToastStack } from '@/components/Toast'
import { useToasts } from '@/hooks/useToasts'
import { usePermissions, PERM_ADMIN } from '@/hooks/usePermissions'
import { SyncStatusCard } from './SyncStatusCard'
import { ParametersCard } from './ParametersCard'
import { WarehouseClassesCard } from './WarehouseClassesCard'
import { NoBatchMaterialsCard } from './NoBatchMaterialsCard'

export default function SettingsPage() {
  const toasts = useToasts()
  const perms = usePermissions()
  const canAdmin = perms.can(PERM_ADMIN)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-neutral-900">Settings</h1>
        {!canAdmin && (
          <p className="mt-1 flex items-center gap-1.5 text-sm text-neutral-500">
            <ShieldAlert aria-hidden className="h-4 w-4" />
            Read-only — changing these settings needs inventory.admin.
          </p>
        )}
      </div>
      <SyncStatusCard canAdmin={canAdmin} toasts={toasts} />
      <ParametersCard canAdmin={canAdmin} toasts={toasts} />
      <WarehouseClassesCard canAdmin={canAdmin} toasts={toasts} />
      <NoBatchMaterialsCard canAdmin={canAdmin} toasts={toasts} />
      <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />
    </div>
  )
}
