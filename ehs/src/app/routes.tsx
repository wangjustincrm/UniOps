/**
 * Route registration — the single source for both the router and the tab shell.
 */
import type { RouteDef } from '@uniops/shell'
import ActionDetailPage from '@/pages/ActionDetailPage'
import AuditPackPage from '@/pages/AuditPackPage'
import ChemicalsPage from '@/pages/ChemicalsPage'
import ComplianceCalendarPage from '@/pages/ComplianceCalendarPage'
import FeedbackPage from '@/pages/FeedbackPage'
import CourseSessionPage from '@/pages/CourseSessionPage'
import DocumentDetailPage from '@/pages/DocumentDetailPage'
import DocumentsPage from '@/pages/DocumentsPage'
import EmergencyPage from '@/pages/EmergencyPage'
import HazardsPage from '@/pages/HazardsPage'
import HoursWorkedPage from '@/pages/settings/HoursWorkedPage'
import IncidentDetailPage from '@/pages/IncidentDetailPage'
import IncidentListPage from '@/pages/IncidentListPage'
import DashboardPage from '@/pages/DashboardPage'
import EquipmentPage from '@/pages/settings/EquipmentPage'
import FormBuilderPage from '@/pages/settings/FormBuilderPage'
import FormFillPage from '@/pages/FormFillPage'
import FormTemplatesPage from '@/pages/settings/FormTemplatesPage'
import FormsListPage from '@/pages/FormsListPage'
import InvestigationPage from '@/pages/InvestigationPage'
import JhscMeetingPage from '@/pages/JhscMeetingPage'
import JhscRecommendationPage from '@/pages/JhscRecommendationPage'
import JhscPage from '@/pages/JhscPage'
import JsaPage, { JsaDetailPage } from '@/pages/JsaPage'
import PermitsPage from '@/pages/PermitsPage'
import QrLandingPage from '@/pages/QrLandingPage'
import TaskRedirectPage from '@/pages/TaskRedirectPage'
import CurrentSdsPage from '@/pages/CurrentSdsPage'
import QuizPage, { EditQuizPage, TakeQuizPage } from '@/pages/QuizPage'
import SafetyEquipmentPage from '@/pages/SafetyEquipmentPage'
import ReportsPage from '@/pages/ReportsPage'
import ToolboxTalksPage from '@/pages/ToolboxTalksPage'
import IncidentReportPage from '@/pages/IncidentReportPage'
import MyActionsPage from '@/pages/MyActionsPage'
import TrainingPage from '@/pages/TrainingPage'
import LocationsPage from '@/pages/settings/LocationsPage'
import PositionsPage from '@/pages/settings/PositionsPage'
import MyTrainingPage from '@/pages/MyTrainingPage'
import AreaOwnersPage from '@/pages/settings/AreaOwnersPage'
import RulesPage from '@/pages/settings/RulesPage'
import PermitChecksPage from '@/pages/settings/PermitChecksPage'
import ExpiryNoticesPage from '@/pages/settings/ExpiryNoticesPage'
import StaffFilePage, { StaffFilePicker } from '@/pages/StaffFilePage'
import UndeliveredEmailPage from '@/pages/settings/UndeliveredEmailPage'
import ReassignPage from '@/pages/settings/ReassignPage'
import NotifyGroupsPage from '@/pages/settings/NotifyGroupsPage'
import VocabulariesPage from '@/pages/settings/VocabulariesPage'

export const ehsRoutes: RouteDef[] = [
  {
    path: '/dashboard',
    element: <DashboardPage />,
    tab: { title: 'Dashboard', icon: 'BarChart3', keyStrategy: 'static' },
  },
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
    path: '/feedback',
    element: <FeedbackPage />,
    tab: { title: 'Feedback', icon: 'MessageSquare', keyStrategy: 'static' },
  },
  {
    path: '/calendar',
    element: <ComplianceCalendarPage />,
    tab: { title: 'Calendar', icon: 'CalendarClock', keyStrategy: 'static' },
  },
  {
    path: '/training/mine',
    element: <MyTrainingPage />,
    tab: { title: 'My training', icon: 'GraduationCap', keyStrategy: 'static' },
  },
  {
    path: '/training',
    element: <TrainingPage />,
    tab: { title: 'Training', icon: 'GraduationCap', keyStrategy: 'static' },
  },
  {
    path: '/training/sessions/:id',
    element: <CourseSessionPage />,
    // Titled from the document number once loaded — a tab reading
    // "Session 8f3a2b1c" tells nobody which session it is.
    tab: { title: 'Session', icon: 'GraduationCap',
           keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/jhsc',
    element: <JhscPage />,
    tab: { title: 'JHSC', icon: 'Users', keyStrategy: 'static' },
  },
  {
    // Round 9: opened from the reply task by whoever it was sent to.
    path: '/jhsc/recommendations/:id',
    element: <JhscRecommendationPage />,
    tab: { title: 'Recommendation', icon: 'Users', keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/jhsc/meetings/:id',
    element: <JhscMeetingPage />,
    tab: { title: 'Meeting', icon: 'Users', keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/hazards',
    element: <HazardsPage />,
    tab: { title: 'Hazards', icon: 'ShieldAlert', keyStrategy: 'static' },
  },
  {
    path: '/jsa',
    element: <JsaPage />,
    tab: { title: 'Job analyses', icon: 'ClipboardList', keyStrategy: 'static' },
  },
  {
    path: '/jsa/:id',
    element: <JsaDetailPage />,
    tab: { title: 'Analysis', icon: 'ClipboardList', keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/audit',
    element: <AuditPackPage />,
    tab: { title: 'Audit evidence', icon: 'ClipboardCheck', keyStrategy: 'static' },
  },
  {
    path: '/emergency',
    element: <EmergencyPage />,
    tab: { title: 'Emergency', icon: 'Siren', keyStrategy: 'static' },
  },
  {
    path: '/permits',
    element: <PermitsPage />,
    tab: { title: 'Work permits', icon: 'ShieldCheck', keyStrategy: 'static' },
  },
  {
    // Scanning the board by a confined space lands on the permit list.
    path: '/permits/:id',
    element: <PermitsPage />,
    tab: { title: 'Work permits', icon: 'ShieldCheck', keyStrategy: 'static' },
  },
  {
    path: '/quizzes',
    element: <QuizPage />,
    tab: { title: 'Quizzes', icon: 'ClipboardList', keyStrategy: 'static' },
  },
  {
    // Where a scanned quiz code lands. One question per screen.
    path: '/quizzes/:id/take',
    element: <TakeQuizPage />,
    tab: { title: 'Quiz', icon: 'ClipboardList', keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/quizzes/:id/edit',
    element: <EditQuizPage />,
    tab: { title: 'Quiz', icon: 'ClipboardList', keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/chemicals',
    element: <ChemicalsPage />,
    tab: { title: 'Chemicals', icon: 'FlaskConical', keyStrategy: 'static' },
  },
  {
    // The code on a label: the product's data sheet in force today.
    path: '/chemicals/:id/sds',
    element: <CurrentSdsPage />,
    tab: { title: 'Data sheet', icon: 'FileText', keyStrategy: 'param', paramName: 'id' },
  },
  {
    // Scanning a drum lands on the inventory row for that product.
    path: '/chemicals/:id',
    element: <ChemicalsPage />,
    tab: { title: 'Chemicals', icon: 'FlaskConical', keyStrategy: 'static' },
  },
  {
    path: '/documents',
    element: <DocumentsPage />,
    tab: { title: 'Documents', icon: 'FileText', keyStrategy: 'static' },
  },
  {
    path: '/documents/:id',
    element: <DocumentDetailPage />,
    tab: { title: 'Document', icon: 'FileText', keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/equipment',
    element: <SafetyEquipmentPage />,
    tab: { title: 'Safety equipment', icon: 'Wrench', keyStrategy: 'static' },
  },
  {
    // From the Portal's inbox: Safety says where its own task opens.
    path: '/go/task/:id',
    element: <TaskRedirectPage />,
    tab: { title: 'Opening task', icon: 'ClipboardCheck', keyStrategy: 'param', paramName: 'id' },
  },
  {
    // Where a scanned label lands. No tab of its own: it forwards to whatever
    // the code points at, and a tab for the redirect would outlive its purpose.
    path: '/qr/:token',
    element: <QrLandingPage />,
    tab: { title: 'Scanned code', icon: 'QrCode', keyStrategy: 'param', paramName: 'token' },
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
    path: '/settings/hours',
    element: <HoursWorkedPage />,
    tab: { title: 'Hours worked', icon: 'CalendarClock', keyStrategy: 'static' },
  },
  {
    path: '/settings/positions',
    element: <PositionsPage />,
    tab: { title: 'Positions', icon: 'Users', keyStrategy: 'static' },
  },
  {
    path: '/settings/lists',
    element: <VocabulariesPage />,
    tab: { title: 'Lists', icon: 'List', keyStrategy: 'static' },
  },
  {
    path: '/settings/area-owners',
    element: <AreaOwnersPage />,
    tab: { title: 'Area responsibility', icon: 'MapPin', keyStrategy: 'static' },
  },
  {
    path: '/settings/areas',
    element: <LocationsPage />,
    tab: { title: 'Plant areas', icon: 'Map', keyStrategy: 'static' },
  },
  {
    path: '/settings/notify-groups',
    element: <NotifyGroupsPage />,
    tab: { title: 'Who is told', icon: 'Mail', keyStrategy: 'static' },
  },
  {
    path: '/settings/rules',
    element: <RulesPage />,
    tab: { title: 'Rules', icon: 'Settings', keyStrategy: 'static' },
  },
  {
    path: '/settings/permit-checks',
    element: <PermitChecksPage />,
    tab: { title: 'Permit checks', icon: 'ShieldCheck', keyStrategy: 'static' },
  },
  {
    path: '/settings/expiry-notices',
    element: <ExpiryNoticesPage />,
    tab: { title: 'Expiry notices', icon: 'Mail', keyStrategy: 'static' },
  },
  {
    path: '/staff-file',
    element: <StaffFilePicker />,
    tab: { title: 'Staff file', icon: 'Users', keyStrategy: 'static' },
  },
  {
    path: '/staff-file/:id',
    element: <StaffFilePage />,
    tab: { title: 'Staff file', icon: 'Users', keyStrategy: 'param', paramName: 'id' },
  },
  {
    path: '/settings/reassign',
    element: <ReassignPage />,
    tab: { title: 'Reassign a leaver', icon: 'Users', keyStrategy: 'static' },
  },
  {
    path: '/settings/undelivered-email',
    element: <UndeliveredEmailPage />,
    tab: { title: 'Undelivered email', icon: 'Mail', keyStrategy: 'static' },
  },
]
