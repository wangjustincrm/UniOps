import { useContext, useMemo, useSyncExternalStore } from 'react'
import { ManagerContext, ScopeContext } from './tourContext'
import { planStops, type PlannedStop } from './tourStops'

const none = () => () => {}
const zero = () => 0

/** The nearest scope's stops in tour order, current: planned again whenever a stop comes or goes (the
 *  scope announces it once per frame) and when `again` changes (e.g. the drawer opens: what is shown now). */
export function useScopePlan(again?: unknown): PlannedStop[] {
  const scope = useContext(ScopeContext)
  const manager = useContext(ManagerContext)
  const version = useSyncExternalStore(scope ? scope.subscribe : none, scope ? scope.version : zero, zero)
  return useMemo(() => {
    void version; void again  // re-plan on each change and on demand
    return planStops(scope?.stops() ?? [], manager?.order)
  }, [scope, manager, version, again])
}
