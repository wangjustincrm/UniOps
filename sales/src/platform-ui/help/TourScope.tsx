import { useMemo, useRef, type ReactNode } from 'react'
import { ScopeContext, type Stop } from './tourContext'

/** The boundary of one tour: a page (ListPage, a page shell) or a record (RecordSheet). The parts inside
 *  that are tour stops register here (useTourStop / TourMark) while they are mounted; the nearest scope
 *  wins, so a record's sheet keeps its stops out of its page's tour. */
export function TourScope({ id, kind = 'page', children }: { id: string; kind?: 'page' | 'record'; children: ReactNode }) {
  const stops = useRef(new Set<Stop>())
  const value = useMemo(() => ({
    id, kind,
    add: (s: Stop) => { stops.current.add(s); return () => { stops.current.delete(s) } },
    stops: () => [...stops.current],
  }), [id, kind])
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>
}
