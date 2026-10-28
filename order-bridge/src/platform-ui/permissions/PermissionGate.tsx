import type { ReactNode } from 'react'
import { PermissionContext, useCan, type Can } from './context'

export function PermissionProvider({ can, children }: { can: Can; children: ReactNode }) {
  return <PermissionContext.Provider value={can}>{children}</PermissionContext.Provider>
}

export function PermissionGate({
  permission,
  fallback = null,
  children,
}: {
  permission: string
  fallback?: ReactNode
  children: ReactNode
}) {
  return useCan(permission) ? <>{children}</> : <>{fallback}</>
}
