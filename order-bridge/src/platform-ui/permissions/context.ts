import { createContext, useContext } from 'react'

// The business code decides who may do what (permission matrix, the
// system_admin rule, ...) and passes one function in. Nothing here knows a
// permission key.
export type Can = (key: string) => boolean

export const PermissionContext = createContext<Can>(() => false)

export function useCan(key: string | undefined): boolean {
  const can = useContext(PermissionContext)
  return key === undefined ? true : can(key)
}
