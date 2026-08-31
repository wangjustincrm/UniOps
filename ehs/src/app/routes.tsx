/**
 * Route registration — the single source for both the router and the tab shell.
 */
import type { RouteDef } from '@uniops/shell'
import ActionDetailPage from '@/pages/ActionDetailPage'
import ComplianceCalendarPage from '@/pages/ComplianceCalendarPage'
import FirstAidLogPage from '@/pages/FirstAidLogPage'
import IncidentDetailPage from '@/pages/IncidentDetailPage'
import IncidentListPage from '@/pages/IncidentListPage'
import EquipmentPage from '@/pages/settings/EquipmentPage'
import FormBuilderPage from '@/pages/settings/FormBuilderPage'
import FormFillPage from '@/pages/FormFillPage'
import FormTemplatesPage from '@/pages/settings/FormTemplatesPage'
import FormsListPage from '@/pages/FormsListPage'
import InvestigationPage from '@/pages/InvestigationPage'
import ReportsPage from '@/pages/ReportsPage'
import ToolboxTalksPage from '@/pages/ToolboxTalksPage'
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
    path: '/incidents/:id/investigate',
    element: <InvestigationPage />,
    tab: { title: (p) => `Investigation ${String(p.id).slice(0, 8)}`, icon: 'TriangleAlert',
           keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/incidents/:id',
    element: <IncidentDetailPage />,
    tab: { title: (p) => `Incident ${String(p.id).slice(0, 8)}`, icon: 'TriangleAlert',
           keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/forms',
    element: <FormsListPage initialTab="due" />,
    tab: { title: 'Forms', icon: 'ClipboardCheck', keyStrategy: 'static' },
  },
  {
    path: '/forms/submissions',
    element: <FormsListPage initialTab="done" />,
    tab: { title: 'Forms', icon: 'ClipboardCheck', keyStrategy: 'static' },
  },
  {
    path: '/forms/submissions/:id',
    element: <FormFillPage />,
    tab: { title: (p) => `Form ${String(p.id).slice(0, 8)}`, icon: 'ClipboardCheck',
           keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/reports',
    element: <ReportsPage />,
    tab: { title: 'Reports', icon: 'CalendarClock', keyStrategy: 'static' },
  },
  {
    path: '/toolbox-talks',
    element: <ToolboxTalksPage />,
    tab: { title: 'Toolbox talks', icon: 'GraduationCap', keyStrategy: 'static' },
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
    path: '/settings/forms',
    element: <FormTemplatesPage />,
    tab: { title: 'Forms', icon: 'ClipboardCheck', keyStrategy: 'static' },
  },
  {
    path: '/settings/forms/:id',
    element: <FormBuilderPage />,
    tab: { title: (p) => `Builder ${String(p.id).slice(0, 8)}`, icon: 'ClipboardCheck',
           keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/settings/equipment',
    element: <EquipmentPage />,
    tab: { title: 'Equipment', icon: 'Settings', keyStrategy: 'static' },
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
