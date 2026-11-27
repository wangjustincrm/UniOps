import { useMemo, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { TabBackLink, useBackTab, useTabTitle } from '@uniops/shell'
import { RecordContainerContext, type RecordContainer } from '@/platform-ui'
import { backOf } from './backLinks'
import { RECORD_TYPES } from './recordTypes'
import { useModuleRoutes } from './moduleRoutesContext'

/** A record's own tab (/orders/:id, /items/:code, /lots/:lot): its record view as a page, with "← Back to …" —
 *  to the tab that opened it, else its list — which closes this tab and goes there (the shell's TabBackLink /
 *  useBackTab, as in EPMS); the tab's title is the record's name once it is read. */
export function RecordRoute({ type, title, children }: { type: string; title?: string; children: (close: () => void) => ReactNode }) {
  const routes = useModuleRoutes()
  const { pathname } = useLocation()
  const back = backOf(pathname) ?? RECORD_TYPES[type].list
  const goBack = useBackTab(routes)
  useTabTitle(routes, title)
  const container = useMemo<RecordContainer>(() => ({
    kind: 'tab',
    close: () => goBack(back.path),
    back: <TabBackLink routes={routes} to={back.path} className="text-primary-700 hover:underline">← Back to {back.title}</TabBackLink>,
  }), [back.path, back.title, goBack, routes])
  return <RecordContainerContext.Provider value={container}>{children(container.close)}</RecordContainerContext.Provider>
}
