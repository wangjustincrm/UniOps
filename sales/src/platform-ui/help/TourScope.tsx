import { useMemo, useRef, type ReactNode } from 'react'
import { ScopeContext, type Stop } from './tourContext'

/** The boundary of one tour: a page (ListPage, a page shell) or a record (RecordSheet). The parts inside
 *  that are tour stops register here (useTourStop / TourMark) while they are mounted; the nearest scope
 *  wins, so a record's sheet keeps its stops out of its page's tour. Each change (a stop comes or goes:
 *  rows loaded, a pager shown) is announced once per frame, so the drawer, the hint and the counts that
 *  read the scope (useScopePlan) are always current. */
export function TourScope({ id, kind = 'page', children }: { id: string; kind?: 'page' | 'record'; children: ReactNode }) {
  const stops = useRef(new Set<Stop>())
  const listeners = useRef(new Set<() => void>())
  const version = useRef(0)
  const pending = useRef(0)
  const value = useMemo(() => {
    const announce = () => { version.current += 1; listeners.current.forEach((l) => l()) }
    const changed = () => {
      if (pending.current) return
      const later = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (f: () => void) => setTimeout(f, 16)
      pending.current = later(() => { pending.current = 0; announce() }) as number
    }
    return {
      id, kind,
      add: (s: Stop) => { stops.current.add(s); changed(); return () => { stops.current.delete(s); changed() } },
      stops: () => [...stops.current],
      subscribe: (l: () => void) => { listeners.current.add(l); return () => { listeners.current.delete(l) } },
      version: () => version.current,
    }
  }, [id, kind])
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>
}
