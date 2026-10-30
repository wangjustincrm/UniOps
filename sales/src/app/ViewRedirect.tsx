import { useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import { requestView } from '@/platform-ui'

/** An old address that became a view of another list (e.g. /review → Orders,
 *  Needs review): asks the list for that view, then goes there. The list
 *  applies it whether its tab is new or already open. */
export function ViewRedirect({ to, resource, view }: { to: string; resource: string; view: string }) {
  useEffect(() => { requestView(resource, view) }, [resource, view])
  return <Navigate to={to} replace />
}
