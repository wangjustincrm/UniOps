/** Typed React Query hooks for vms-api + epms-api directory. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, epmsApi } from '@/lib/api'

// ── Types (mirror vms-api Pydantic schemas) ─────────────────────────────────-

export type VisitorType =
  | 'supplier' | 'contractor' | 'inspector' | 'auditor'
  | 'customer' | 'interviewee' | 'other'

export type VisitStatus =
  | 'pending_approval' | 'confirmed' | 'checked_in'
  | 'checked_out' | 'cancelled' | 'no_show'

export type AccessArea =
  | 'office' | 'warehouse' | 'production_non_gmp'
  | 'production_gmp' | 'laboratory' | 'all'

export type VisitPurpose =
  | 'meeting' | 'maintenance' | 'tour' | 'audit'
  | 'interview' | 'delivery' | 'other'

export type HealthDeclStatus = 'not_required' | 'passed' | 'failed' | 'restricted'

export interface Visitor {
  id: string
  first_name: string
  last_name: string
  company_name: string
  job_title: string | null
  phone: string
  email: string | null
  visitor_type: VisitorType
  id_verified: boolean
  safety_training_confirmed_at: string | null
  safety_training_confirmed_by: string | null
  ppe_issued_at: string | null
  ppe_issued_by: string | null
  created_at: string
  updated_at: string
}

export type ClothingSize = 'XS' | 'S' | 'M' | 'L' | 'XL' | 'XXL' | 'other'
export type ShoeSize = '7' | '8' | '9' | '10' | '11' | '12' | '13' | '14' | 'other'
export type FootwearKind = 'shoes' | 'shoe_covers'

/** Per-visitor PPE spec — one of these per person on the visit. */
export interface PpeItem {
  visitor_id: string
  clothing_size: ClothingSize
  clothing_size_other?: string | null
  footwear: FootwearKind
  shoe_size?: ShoeSize | null
  shoe_size_other?: string | null
}

export interface PpeRequest {
  items: PpeItem[]
  notes?: string | null
}

export interface Visit {
  id: string
  visitor_id: string
  additional_visitor_ids: string[]
  host_id: string
  created_by: string
  visit_date: string
  planned_arrival: string
  planned_departure: string | null
  actual_arrival: string | null
  actual_departure: string | null
  visit_purpose: VisitPurpose
  access_area: AccessArea
  status: VisitStatus
  health_decl_status: HealthDeclStatus | null
  safety_training_confirmed: boolean
  badge_returned: boolean
  ppe_issued: Record<string, unknown> | null
  accompanying_count: number | null
  vehicle_plate: string | null
  notes: string | null
  approval_step_idx: number | null
  submitted_at: string | null
  quality_approver_id: string | null
  ppe_requested: PpeRequest | null
  ppe_notified_at: string | null
  created_at: string
  updated_at: string
  // Populated by list endpoints + visit detail; null on responses that
  // don't bother to join (e.g. some post-mutation echoes).
  visitor: Visitor | null
  additional_visitors: Visitor[]
}

export interface UserBrief {
  id: string
  full_name: string
  email: string
  department_id: string | null
  department_name: string | null
}

export interface Page<T> {
  items: T[]
  total: number
}

// ── Visitors ─────────────────────────────────────────────────────────────────

export function useVisitors(search?: string) {
  return useQuery<Page<Visitor>>({
    queryKey: ['vms-visitors', search ?? ''],
    queryFn: () => {
      const qs = new URLSearchParams()
      if (search) qs.set('search', search)
      qs.set('page_size', '50')
      return api.get<Page<Visitor>>(`/api/v1/visitors?${qs.toString()}`)
    },
    staleTime: 30_000,
  })
}

export function useVisitor(visitorId: string | undefined) {
  return useQuery<Visitor>({
    queryKey: ['vms-visitor', visitorId],
    queryFn: () => api.get<Visitor>(`/api/v1/visitors/${visitorId}`),
    enabled: !!visitorId,
  })
}

export function useCreateVisitor() {
  const qc = useQueryClient()
  return useMutation<Visitor, Error, Partial<Visitor>>({
    mutationFn: (payload) => api.post<Visitor>('/api/v1/visitors', payload),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['vms-visitors'] }) },
  })
}

export function useConfirmTraining(visitorId: string) {
  const qc = useQueryClient()
  return useMutation<Visitor, Error, void>({
    mutationFn: () => api.post<Visitor>(`/api/v1/visitors/${visitorId}/confirm-training`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vms-visitor', visitorId] })
      qc.invalidateQueries({ queryKey: ['vms-visitors'] })
      qc.invalidateQueries({ queryKey: ['vms-my-tasks'] })
    },
  })
}

export function useConfirmPpe(visitorId: string) {
  const qc = useQueryClient()
  return useMutation<Visitor, Error, void>({
    mutationFn: () => api.post<Visitor>(`/api/v1/visitors/${visitorId}/confirm-ppe`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vms-visitor', visitorId] })
      qc.invalidateQueries({ queryKey: ['vms-visitors'] })
      qc.invalidateQueries({ queryKey: ['vms-my-tasks'] })
    },
  })
}

/** Same 12-month TTL as the backend `services/compliance.py`. */
const COMPLIANCE_TTL_MS = 365 * 24 * 60 * 60 * 1000
export function isComplianceFresh(timestamp: string | null): boolean {
  if (!timestamp) return false
  return Date.now() - new Date(timestamp).getTime() < COMPLIANCE_TTL_MS
}

// ── Visits ───────────────────────────────────────────────────────────────────

export interface VisitListFilters {
  status?: VisitStatus
  host_id?: string
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}

export function useVisits(filters: VisitListFilters = {}) {
  return useQuery<Page<Visit>>({
    queryKey: ['vms-visits', filters],
    queryFn: () => {
      const qs = new URLSearchParams()
      for (const [k, v] of Object.entries(filters)) {
        if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
      }
      return api.get<Page<Visit>>(`/api/v1/visits?${qs.toString()}`)
    },
    staleTime: 15_000,
  })
}

export function useActiveVisits() {
  return useQuery<Visit[]>({
    queryKey: ['vms-visits', 'active'],
    queryFn: () => api.get<Visit[]>('/api/v1/visits/active'),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
}

export function useVisit(visitId: string | undefined) {
  return useQuery<Visit>({
    queryKey: ['vms-visit', visitId],
    queryFn: () => api.get<Visit>(`/api/v1/visits/${visitId}`),
    enabled: !!visitId,
  })
}

export interface CreateVisitPayload {
  visitor_id: string
  additional_visitor_ids?: string[]
  host_id: string
  visit_date: string
  planned_arrival: string
  planned_departure?: string | null
  visit_purpose: VisitPurpose
  access_area: AccessArea
  accompanying_count?: number | null
  vehicle_plate?: string | null
  notes?: string | null
  ppe_requested?: PpeRequest | null
}

export function useCreateVisit() {
  const qc = useQueryClient()
  return useMutation<Visit, Error, CreateVisitPayload>({
    mutationFn: (payload) => api.post<Visit>('/api/v1/visits', payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vms-visits'] })
      qc.invalidateQueries({ queryKey: ['vms-visits', 'active'] })
    },
  })
}

export function useCancelVisit(visitId: string | undefined) {
  const qc = useQueryClient()
  return useMutation<Visit, Error, void>({
    mutationFn: () => api.post<Visit>(`/api/v1/visits/${visitId}/cancel`, {}),
    onSuccess: (visit) => {
      qc.invalidateQueries({ queryKey: ['vms-visits'] })
      qc.invalidateQueries({ queryKey: ['vms-visit', visit.id] })
    },
  })
}

// ── Visitor mutations (id_verified gate, etc.) ───────────────────────────────

export function usePatchVisitor(visitorId: string | undefined) {
  const qc = useQueryClient()
  return useMutation<Visitor, Error, Partial<Visitor>>({
    mutationFn: (body) => api.patch<Visitor>(`/api/v1/visitors/${visitorId}`, body),
    onSuccess: (v) => {
      qc.invalidateQueries({ queryKey: ['vms-visitor', v.id] })
      qc.invalidateQueries({ queryKey: ['vms-visitors'] })
    },
  })
}

/** Mark any visitor's ID as verified. The visitor id is passed per-call so a
 *  multi-visitor visit can verify each person (not just the primary). Refreshes
 *  the visit too, since companions are embedded in the visit response. */
export function useVerifyVisitorId() {
  const qc = useQueryClient()
  return useMutation<Visitor, Error, string>({
    mutationFn: (visitorId) =>
      api.patch<Visitor>(`/api/v1/visitors/${visitorId}`, { id_verified: true }),
    onSuccess: (v) => {
      qc.invalidateQueries({ queryKey: ['vms-visitor', v.id] })
      qc.invalidateQueries({ queryKey: ['vms-visitors'] })
      qc.invalidateQueries({ queryKey: ['vms-visit'] })
    },
  })
}

// ── Check-out ────────────────────────────────────────────────────────────────

export interface CheckOutPayload {
  badge_returned: boolean
  ppe_returned: boolean
  notes?: string | null
}

export function useCheckOutVisit(visitId: string | undefined) {
  const qc = useQueryClient()
  return useMutation<Visit, Error, CheckOutPayload>({
    mutationFn: (body) => api.post<Visit>(`/api/v1/visits/${visitId}/check-out`, body),
    onSuccess: (visit) => {
      qc.invalidateQueries({ queryKey: ['vms-visits'] })
      qc.invalidateQueries({ queryKey: ['vms-visits', 'active'] })
      qc.invalidateQueries({ queryKey: ['vms-visit', visit.id] })
      qc.invalidateQueries({ queryKey: ['vms-dashboard'] })
    },
  })
}

export interface BatchCheckOutResult {
  closed: number
  visit_ids: string[]
}

export function useBatchCheckOut() {
  const qc = useQueryClient()
  return useMutation<BatchCheckOutResult, Error, void>({
    mutationFn: () => api.post<BatchCheckOutResult>('/api/v1/visits/batch-checkout', {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vms-visits'] })
      qc.invalidateQueries({ queryKey: ['vms-visits', 'active'] })
      qc.invalidateQueries({ queryKey: ['vms-dashboard'] })
    },
  })
}

// ── Approval action (approve / reject / return) ────────────────────────────-

export type VisitApprovalAction = 'approve' | 'reject' | 'return'

export function useVisitAction(visitId: string) {
  const qc = useQueryClient()
  return useMutation<Visit, Error, { action: VisitApprovalAction; comment?: string }>({
    mutationFn: (body) => api.post<Visit>(`/api/v1/visits/${visitId}/action`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vms-visit', visitId] })
      qc.invalidateQueries({ queryKey: ['vms-visits'] })
      qc.invalidateQueries({ queryKey: ['vms-my-tasks'] })
    },
  })
}

// ── My VMS tasks (epms-api task inbox, filtered to vms_visit) ──────────────-

export interface EpmsTask {
  id: string
  type: string
  priority: string
  document_type: string
  document_id: string
  document_number: string
  assigned_role: string
  assigned_user_id: string | null
  title: string
  description: string | null
  due_date: string | null
  amount: number | null
  vendor: string | null
  is_completed: boolean
  completed_at: string | null
  created_at: string
}

const VMS_TASK_DOC_TYPES = new Set(['vms_visit', 'vms_train', 'vms_ppe'])

/** Pending VMS tasks for the current user — visit approvals, plus HR
 * training and Janitor PPE confirmations. Backed by epms-api `/tasks` so
 * Portal and VMS show the identical list. */
export function useMyVmsTasks() {
  return useQuery<EpmsTask[]>({
    queryKey: ['vms-my-tasks'],
    queryFn: async () => {
      const resp = await epmsApi.get<{ items: EpmsTask[]; total: number }>(
        '/api/v1/tasks?is_completed=false',
      )
      return resp.items.filter((t) => VMS_TASK_DOC_TYPES.has(t.document_type))
    },
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
}

// ── Dashboard ────────────────────────────────────────────────────────────────

export interface DashboardOverview {
  on_site_count: number
  today_count: number
  week_count: number
  overdue_count: number
  server_time: string
}

export function useDashboardOverview() {
  return useQuery<DashboardOverview>({
    queryKey: ['vms-dashboard'],
    queryFn: () => api.get<DashboardOverview>('/api/v1/dashboard/overview'),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}

export interface ComplianceMetrics {
  gmp_visits_this_month: number
  // null when there are no health-decl outcomes yet this month — render n/a.
  gmp_pass_rate: number | null
  unreturned_badges: number
  after_hours_visits_today: number
  as_of: string
}

export function useComplianceMetrics() {
  return useQuery<ComplianceMetrics>({
    queryKey: ['vms-dashboard-compliance'],
    queryFn: () => api.get<ComplianceMetrics>('/api/v1/dashboard/compliance'),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
}

// ── Visit attachments (proxied to file-api) ────────────────────────────────-

export interface VisitAttachment {
  id: string
  original_filename: string
  content_type: string
  file_size: number
  service: string
  doc_type: string
  doc_id: string
  uploaded_by: string | null
  created_at: string | null
  download_url: string
}

export function useVisitAttachments(visitId: string | undefined) {
  return useQuery<VisitAttachment[]>({
    queryKey: ['vms-visit-attachments', visitId],
    queryFn: () => api.get<VisitAttachment[]>(`/api/v1/visits/${visitId}/attachments`),
    enabled: !!visitId,
    staleTime: 10_000,
  })
}

/** Upload helper — multipart, so we bypass the JSON `api` client. */
export function useUploadVisitAttachment(visitId: string) {
  const qc = useQueryClient()
  return useMutation<VisitAttachment, Error, File>({
    mutationFn: async (file: File) => {
      const base = (import.meta.env.VITE_API_URL as string | undefined) || ''
      const token = (() => {
        for (const key of ['vms-auth', 'portal-auth']) {
          const raw = localStorage.getItem(key)
          const t = raw ? JSON.parse(raw)?.state?.token : null
          if (t) return t
        }
        return null
      })()
      const fd = new FormData()
      fd.append('file', file)
      const resp = await fetch(`${base}/api/v1/visits/${visitId}/attachments`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }))
        throw new Error(err.detail ?? `HTTP ${resp.status}`)
      }
      return resp.json() as Promise<VisitAttachment>
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vms-visit-attachments', visitId] })
    },
  })
}

// ── Reports (auditor / admin) ──────────────────────────────────────────────-

export type ReportKind = 'cfia-visit-log' | 'gmp-area-summary'

/** Trigger a CSV download by hitting the auditor-gated endpoint with the
 * caller's bearer token, then handing the resulting blob to the browser.
 * Returns the filename so the caller can show "Downloaded foo.csv". */
export async function downloadReport(
  kind: ReportKind,
  args: { dateFrom: string; dateTo: string },
): Promise<string> {
  const { dateFrom, dateTo } = args
  const base = (import.meta.env.VITE_API_URL as string | undefined) || ''
  const token = (() => {
    for (const key of ['vms-auth', 'portal-auth']) {
      const raw = localStorage.getItem(key)
      const t = raw ? JSON.parse(raw)?.state?.token : null
      if (t) return t
    }
    return null
  })()
  const url = `${base}/api/v1/reports/${kind}?from=${dateFrom}&to=${dateTo}`
  const resp = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail ?? `HTTP ${resp.status}`)
  }
  const blob = await resp.blob()
  const filename = `vms-${kind}-${dateFrom}-${dateTo}.csv`
  const downloadUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = downloadUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(downloadUrl)
  return filename
}

// ── Health declaration ──────────────────────────────────────────────────────

export interface HealthQuestion {
  id: string
  text: string
  fail_on: string
}

export interface HealthQuestionsTemplate {
  version: number
  questions: HealthQuestion[]
}

export interface HealthDeclarationAnswer {
  id: string
  text?: string
  answer: string
}

export interface HealthDeclaration {
  id: string
  visit_id: string
  visitor_id: string
  questionnaire_data: {
    template_version: number
    answers: HealthDeclarationAnswer[]
    computed_result: HealthDeclStatus
  }
  result: HealthDeclStatus
  signature: string | null
  created_at: string
  updated_at: string
}

export interface SubmitHealthDeclarationPayload {
  answers: { id: string; answer: string }[]
  safety_training_confirmed: boolean
  signature?: string | null
  /** Which visitor on the visit this declaration is for. Omit → primary. */
  visitor_id?: string
}

export function useHealthQuestions() {
  return useQuery<HealthQuestionsTemplate>({
    queryKey: ['vms-health-questions'],
    queryFn: () => api.get<HealthQuestionsTemplate>('/api/v1/health-questions'),
    staleTime: 10 * 60_000,
  })
}

/** All filed declarations for a visit — one per visitor who has signed. */
export function useHealthDeclarations(visitId: string | undefined) {
  return useQuery<HealthDeclaration[]>({
    queryKey: ['vms-health-decls', visitId],
    queryFn: () => api.get<HealthDeclaration[]>(`/api/v1/visits/${visitId}/health-declaration`),
    enabled: !!visitId,
    retry: false,
  })
}

export function useSubmitHealthDeclaration(visitId: string | undefined) {
  const qc = useQueryClient()
  return useMutation<HealthDeclaration, Error, SubmitHealthDeclarationPayload>({
    mutationFn: (body) =>
      api.post<HealthDeclaration>(`/api/v1/visits/${visitId}/health-declaration`, body),
    onSuccess: (decl) => {
      qc.invalidateQueries({ queryKey: ['vms-health-decls', visitId] })
      qc.invalidateQueries({ queryKey: ['vms-visit', decl.visit_id] })
      qc.invalidateQueries({ queryKey: ['vms-visits'] })
    },
  })
}

// ── Standalone declarations browser ─────────────────────────────────────────-

export interface HealthDeclarationListItem {
  id: string
  visit_id: string
  visitor_id: string
  visitor_name: string
  company: string
  visit_date: string
  host_name: string
  access_area: AccessArea
  result: HealthDeclStatus
  questionnaire_data: {
    template_version: number
    answers: HealthDeclarationAnswer[]
    computed_result: HealthDeclStatus
  }
  signature: string | null
  created_at: string
  updated_at: string
}

export interface HealthDeclarationListResponse {
  items: HealthDeclarationListItem[]
  total: number
}

export interface BrowseDeclarationsParams {
  from?: string
  to?: string
  q?: string
  page?: number
  page_size?: number
}

export function useBrowseHealthDeclarations(params: BrowseDeclarationsParams) {
  const qs = new URLSearchParams()
  if (params.from) qs.set('from', params.from)
  if (params.to) qs.set('to', params.to)
  if (params.q) qs.set('q', params.q)
  qs.set('page', String(params.page ?? 1))
  qs.set('page_size', String(params.page_size ?? 25))
  return useQuery<HealthDeclarationListResponse>({
    queryKey: ['vms-health-decls-browse', params],
    queryFn: () =>
      api.get<HealthDeclarationListResponse>(`/api/v1/health-declarations?${qs.toString()}`),
    staleTime: 30_000,
  })
}

// ── Audit log ────────────────────────────────────────────────────────────────

export interface AuditLog {
  id: number
  timestamp: string
  user_id: string
  user_name: string
  action_type: string
  entity_type: string
  entity_id: string
  old_value: Record<string, unknown> | null
  new_value: Record<string, unknown> | null
  ip_address: string
  user_agent: string | null
  notes: string | null
}

export interface AuditLogFilters {
  user_id?: string
  action_type?: string
  entity_type?: string
  entity_id?: string
  /** ISO timestamp */ from?: string
  /** ISO timestamp */ to?: string
  page?: number
  page_size?: number
}

function buildAuditQs(filters: AuditLogFilters): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  }
  return qs.toString()
}

export function useAuditLogs(filters: AuditLogFilters = {}) {
  const qs = buildAuditQs(filters)
  return useQuery<Page<AuditLog>>({
    queryKey: ['vms-audit-logs', filters],
    queryFn: () => api.get<Page<AuditLog>>(`/api/v1/audit-logs${qs ? '?' + qs : ''}`),
    staleTime: 15_000,
  })
}

/** Build a URL to the CSV export with the current filter state.
 *
 *  Note: the auth header is set via fetch in `lib/api.ts` — for CSV we let
 *  the browser navigate to the URL so it streams as a file download. Since
 *  the API is same-origin in dev (via Vite proxy) and CORS-allowed in
 *  production, attaching the Bearer token via fetch + Blob URL is the
 *  right approach. Caller decides which path to use.
 */
export function buildAuditCsvPath(filters: AuditLogFilters): string {
  const qs = buildAuditQs(filters)
  return `/api/v1/audit-logs/export${qs ? '?' + qs : ''}`
}

// ── Badge printing ───────────────────────────────────────────────────────────

export interface BadgePrint {
  id: string
  visit_id: string
  printed_by: string
  printed_at: string
  reprint_reason: string | null
  template_used: string
}

export interface PrintBadgePayload {
  template_used: string
  reprint_reason?: string | null
}

export function usePrintBadge(visitId: string | undefined) {
  const qc = useQueryClient()
  return useMutation<Visit, Error, PrintBadgePayload>({
    mutationFn: (body) => api.post<Visit>(`/api/v1/visits/${visitId}/print-badge`, body),
    onSuccess: (visit) => {
      qc.invalidateQueries({ queryKey: ['vms-visits'] })
      qc.invalidateQueries({ queryKey: ['vms-visits', 'active'] })
      qc.invalidateQueries({ queryKey: ['vms-visit', visit.id] })
      qc.invalidateQueries({ queryKey: ['vms-badge-history', visit.id] })
    },
  })
}

export function useBadgeHistory(visitId: string | undefined) {
  return useQuery<BadgePrint[]>({
    queryKey: ['vms-badge-history', visitId],
    queryFn: () => api.get<BadgePrint[]>(`/api/v1/visits/${visitId}/badge-history`),
    enabled: !!visitId,
  })
}

// ── User directory (epms-api) ────────────────────────────────────────────────

export function useUserDirectory(search?: string) {
  return useQuery<Page<UserBrief>>({
    queryKey: ['epms-user-directory', search ?? ''],
    queryFn: () => {
      const qs = new URLSearchParams()
      if (search) qs.set('search', search)
      qs.set('page_size', '20')
      return epmsApi.get<Page<UserBrief>>(`/api/v1/users/directory?${qs.toString()}`)
    },
    staleTime: 30_000,
    enabled: !!search && search.length >= 2,
  })
}

export function useUserBrief(userId: string | undefined) {
  return useQuery<UserBrief>({
    queryKey: ['epms-user-brief', userId],
    queryFn: () => epmsApi.get<UserBrief>(`/api/v1/users/directory/${userId}`),
    enabled: !!userId,
    staleTime: 5 * 60_000,
  })
}

// ── Admin (system_admin only) ────────────────────────────────────────────────

export interface QualityManagerRoster {
  user_ids: string[]
}

export function useQualityManagerRoster() {
  return useQuery<QualityManagerRoster>({
    queryKey: ['vms-admin-qm'],
    queryFn: () => api.get<QualityManagerRoster>('/api/v1/admin/quality-managers'),
    staleTime: 30_000,
  })
}

export function useSetQualityManagerRoster() {
  const qc = useQueryClient()
  return useMutation<QualityManagerRoster, Error, QualityManagerRoster>({
    mutationFn: (body) => api.put<QualityManagerRoster>('/api/v1/admin/quality-managers', body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['vms-admin-qm'] }) },
  })
}

export interface NotificationContacts {
  training_email: string | null
  ppe_email: string | null
}

export function useNotificationContacts() {
  return useQuery<NotificationContacts>({
    queryKey: ['vms-admin-notif'],
    queryFn: () => api.get<NotificationContacts>('/api/v1/admin/notification-contacts'),
    staleTime: 30_000,
  })
}

export function useSetNotificationContacts() {
  const qc = useQueryClient()
  return useMutation<NotificationContacts, Error, NotificationContacts>({
    mutationFn: (body) => api.put<NotificationContacts>('/api/v1/admin/notification-contacts', body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['vms-admin-notif'] }) },
  })
}

export interface HealthQuestionEntry {
  id: string
  text: string
  fail_on: string
}

export interface HealthQuestionsTemplateFull {
  version: number
  questions: HealthQuestionEntry[]
}

export function useHealthQuestionsAdmin() {
  return useQuery<HealthQuestionsTemplateFull>({
    queryKey: ['vms-admin-health-qs'],
    queryFn: () => api.get<HealthQuestionsTemplateFull>('/api/v1/admin/health-questions'),
    staleTime: 30_000,
  })
}

export function useSetHealthQuestions() {
  const qc = useQueryClient()
  return useMutation<HealthQuestionsTemplateFull, Error, HealthQuestionsTemplateFull>({
    mutationFn: (body) => api.put<HealthQuestionsTemplateFull>('/api/v1/admin/health-questions', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vms-admin-health-qs'] })
      qc.invalidateQueries({ queryKey: ['vms-health-questions'] })  // public template too
    },
  })
}

// ── Badge templates (W5 stub admin endpoints) ────────────────────────────────

// ── VMS SMTP settings (admin) ──────────────────────────────────────────────-

export interface SmtpSettings {
  host: string | null
  port: number | null
  user: string | null
  password: string | null
  use_tls: boolean | null
  from_email: string | null
}

export function useSmtpSettings() {
  return useQuery<SmtpSettings>({
    queryKey: ['vms-admin-smtp-settings'],
    queryFn: () => api.get<SmtpSettings>('/api/v1/admin/smtp-settings'),
    staleTime: 30_000,
  })
}

export function useSetSmtpSettings() {
  const qc = useQueryClient()
  return useMutation<SmtpSettings, Error, SmtpSettings>({
    mutationFn: (body) => api.put<SmtpSettings>('/api/v1/admin/smtp-settings', body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['vms-admin-smtp-settings'] }) },
  })
}

export interface SmtpTestResult {
  delivered: boolean
  detail: string
}

export function useSendSmtpTest() {
  return useMutation<SmtpTestResult, Error, { to_email: string }>({
    mutationFn: (body) => api.post<SmtpTestResult>('/api/v1/admin/smtp-test', body),
  })
}

// ── Badge config (structured, replaces HTML/CSS templates) ──────────────────-

export interface BadgeAreaStyle { label: string; bg: string; fg: string; risk: string }
export interface BadgeBand {
  title: string
  show_zone: boolean
  show_risk: boolean
  risk_suffix: string
  areas: Record<AccessArea, BadgeAreaStyle>
}
export interface BadgeIdentity { name_uppercase: boolean; show_company: boolean }
export type BadgeMetaKey =
  | 'visit_date' | 'host' | 'valid_until'
  | 'vehicle_plate' | 'accompanying_count' | 'visit_purpose'
export interface BadgeMetaField { key: BadgeMetaKey; label: string; visible: boolean }
export interface BadgeQr { show: boolean; label: string }
export interface BadgeFooter { show: boolean; lines: string[] }
export interface BadgeStyle {
  name_size_pt: number
  band_title_size_pt: number
  text_color: string
  company_color: string
  footer_color: string
  name_align: 'left' | 'center' | 'right'
}
export interface BadgeConfig {
  version: number
  band: BadgeBand
  identity: BadgeIdentity
  meta_fields: BadgeMetaField[]
  qr: BadgeQr
  footer: BadgeFooter
  style: BadgeStyle
}

/** Mirror of vms-api DEFAULT_BADGE_CONFIG — used as the render fallback and
 *  the starting point for the admin form before the server responds. */
export const BADGE_CONFIG_DEFAULTS: BadgeConfig = {
  version: 1,
  band: {
    title: 'VISITOR',
    show_zone: true,
    show_risk: true,
    risk_suffix: 'RISK',
    areas: {
      office:             { label: 'OFFICE',           bg: '#10B981', fg: '#FFFFFF', risk: 'LOW' },
      warehouse:          { label: 'WAREHOUSE',        bg: '#F59E0B', fg: '#1A2730', risk: 'MEDIUM' },
      production_non_gmp: { label: 'PRODUCTION',       bg: '#EA580C', fg: '#FFFFFF', risk: 'MEDIUM' },
      production_gmp:     { label: 'PRODUCTION (GMP)', bg: '#DC2626', fg: '#FFFFFF', risk: 'HIGH' },
      laboratory:         { label: 'LABORATORY',       bg: '#DC2626', fg: '#FFFFFF', risk: 'HIGH' },
      all:                { label: 'ALL ZONES',        bg: '#DC2626', fg: '#FFFFFF', risk: 'HIGH' },
    },
  },
  identity: { name_uppercase: true, show_company: true },
  meta_fields: [
    { key: 'visit_date',         label: 'Date',         visible: true },
    { key: 'host',               label: 'Host',         visible: true },
    { key: 'valid_until',        label: 'Valid until',  visible: true },
    { key: 'vehicle_plate',      label: 'Vehicle',      visible: false },
    { key: 'accompanying_count', label: 'Accompanying', visible: false },
    { key: 'visit_purpose',      label: 'Purpose',      visible: false },
  ],
  qr: { show: true, label: 'Scan to check out' },
  footer: { show: true, lines: ['Must be accompanied by Host at all times', 'Please return badge when leaving'] },
  style: {
    name_size_pt: 22, band_title_size_pt: 18,
    text_color: '#1A2730', company_color: '#4E6070', footer_color: '#4E6070',
    name_align: 'left',
  },
}

export function useBadgeConfig() {
  return useQuery<BadgeConfig>({
    queryKey: ['vms-badge-config'],
    queryFn: () => api.get<BadgeConfig>('/api/v1/admin/badge-config'),
    staleTime: 30_000,
  })
}

export function useUpdateBadgeConfig() {
  const qc = useQueryClient()
  return useMutation<BadgeConfig, Error, BadgeConfig>({
    mutationFn: (body) => api.put<BadgeConfig>('/api/v1/admin/badge-config', body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['vms-badge-config'] }) },
  })
}
