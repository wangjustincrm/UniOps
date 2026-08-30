/**
 * Route registration — the single source for both the router and the tab shell.
 */
import type { RouteDef } from '@uniops/shell'
import ActionDetailPage from '@/pages/ActionDetailPage'
import ComplianceCalendarPage from '@/pages/ComplianceCalendarPage'
import FirstAidLogPage from '@/pages/FirstAidLogPage'
import IncidentDetailPage from '@/pages/IncidentDetailPage'
import IncidentListPage from '@/pages/IncidentListPage'
import IncidentReportPage from '@/pages/IncidentReportPage'
import MyActionsPage from '@/pages/MyActionsPage'
import TrainingGapsPage from '@/pages/TrainingGapsPage'
import LocationsPage from '@/pages/settings/LocationsPage'
import RulesPage from '@/pages/settings/RulesPage'
import VocabulariesPage from '@/pages/settings/VocabulariesPage'

export const ehsRoutes: RouteDef[] = [
  {
    path: '/report',
    element: <IncidentReportPage />,
    tab: { title: 'Report', icon: 'TriangleAlert', keyStrategy: 'static' },
  },
  {
    path: '/actions/mine',
    element: <MyActionsPage />,
    tab: { title: 'My actions', icon: 'ClipboardCheck', keyStrategy: 'static', pinned: true },
  },
  {
    path: '/actions/:id',
    element: <ActionDetailPage />,
    tab: { title: (p) => `Action ${String(p.id).slice(0, 8)}`, icon: 'ClipboardCheck',
           keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/incidents',
    element: <IncidentListPage />,
    tab: { title: 'Incidents', icon: 'TriangleAlert', keyStrategy: 'static' },
  },
  {
    path: '/incidents/:id',
    element: <IncidentDetailPage />,
    tab: { title: (p) => `Incident ${String(p.id).slice(0, 8)}`, icon: 'TriangleAlert',
           keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/first-aid',
    element: <FirstAidLogPage />,
    tab: { title: 'First aid', icon: 'HeartPulse', keyStrategy: 'static' },
  },
  {
    path: '/calendar',
    element: <ComplianceCalendarPage />,
    tab: { title: 'Calendar', icon: 'CalendarClock', keyStrategy: 'static' },
  },
  {
    path: '/training',
    element: <TrainingGapsPage />,
    tab: { title: 'Training', icon: 'GraduationCap', keyStrategy: 'static' },
  },
  {
    path: '/settings/lists',
    element: <VocabulariesPage />,
    tab: { title: 'Lists', icon: 'List', keyStrategy: 'static' },
  },
  {
    path: '/settings/areas',
    element: <LocationsPage />,
    tab: { title: 'Plant areas', icon: 'Map', keyStrategy: 'static' },
  },
  {
    path: '/settings/rules',
    element: <RulesPage />,
    tab: { title: 'Rules', icon: 'Settings', keyStrategy: 'static' },
  },
]
