import { api } from '@/lib/api'

export interface WorkflowConfig {
  escalation_threshold_cad: number
  po_low_value_bypass_enabled: boolean
  po_low_value_bypass_cad: number
  approval_reminder_days: number
  approval_auto_escalation_days: number
  // NOTE: over_budget_mode lives on BudgetAdminConfig — see below.
  consolidate_gm_opm_approval: boolean
}

export interface ServiceGrSlaConfig {
  reminder_days: number
  manager_escalation_days: number
  gm_opm_escalation_days: number
  fm_alert_days: number
}

export interface GrNotificationSlaConfig {
  reminder_days: number
  manager_escalation_days: number
}

export interface PrepaymentConfig {
  max_prepayment_pct: number
  settlement_sla_days: number
  settlement_manager_escalation_days: number
  settlement_gm_opm_escalation_days: number
  block_po_closure_on_unsettled: boolean
}

export interface BudgetAdminConfig {
  yellow_threshold_pct: number
  red_threshold_pct: number
  over_budget_mode: 'fm_gm_opm' | 'fm_only' | 'hard_block'
  /**
   * Fiscal years offered in the "New Budget Plan" dropdown and plan-list
   * year filter. Edited in Portal → Budget Config. May be missing on
   * legacy CompanyConfig rows — callers should fall back to a sensible
   * default centered on the current year.
   *
   * NB: The system assumes fiscal year == calendar year. There is no
   * configurable start/end month boundary.
   */
  available_fiscal_years?: number[]
}

export interface CollectionConfig {
  collection_required: boolean
  reminder_days: number
  manager_escalation_days: number
  fm_alert_days: number
}

export interface PdfTemplateSettings {
  show_logo: boolean
  header_note: string
  footer_note: string
  show_terms: boolean
  terms_text: string
}

export interface WorkflowNodeDef {
  id: string
  label: string
  role: string
}

/** permission_key → bool for one role */
export type RolePermissions = Record<string, boolean>

/** role_code → RolePermissions */
export type RolePermissionMatrix = Record<string, RolePermissions>

/** Current user's effective permissions (primary ∪ additional roles). */
export interface MyPermissions {
  permissions: Record<string, boolean>
  roles: string[]
}

export interface CustomRole {
  code: string
  name: string
  description: string
  is_active: boolean
  is_builtin: boolean
}

export interface CreateCustomRoleBody {
  code: string
  name: string
  description?: string
}

export interface UpdateCustomRoleBody {
  name?: string
  description?: string
  is_active?: boolean
}

export interface UpdateRolePermissionsBody {
  permissions: RolePermissionMatrix
}

export interface CompanyConfig {
  role_permissions: RolePermissionMatrix
  custom_roles: CustomRole[]
  email_templates: Record<string, EmailTemplate>
  notification_settings: NotificationSettings
  vendor_categories: string[]
  name: string
  tagline: string
  module_taglines: Record<string, string>
  logo_data_url: string | null
  logo_file_name: string | null
  delivery_address: string
  default_currency: string
  enabled_currencies: string[]
  custom_currencies: { value: string; label: string; symbol: string }[]
  mfa_enabled: boolean
  password_expiry_days: number | null
  smtp_host: string | null
  smtp_port: number | null
  smtp_user: string | null
  smtp_password: string | null
  smtp_use_tls: boolean | null
  smtp_from: string | null
  // PO-to-vendor SMTP profile (falls back to smtp_* above per-field when unset)
  po_smtp_host: string | null
  po_smtp_port: number | null
  po_smtp_user: string | null
  po_smtp_password: string | null
  po_smtp_use_tls: boolean | null
  po_smtp_from: string | null
  po_email_subject: string
  po_email_body: string
  pdf_templates: {
    pr: PdfTemplateSettings
    po: PdfTemplateSettings
    gr: PdfTemplateSettings
    pa: PdfTemplateSettings
  }
  workflow_config: WorkflowConfig
  dept_gm_opm_mapping: Record<string, 'gm' | 'opm'>
  dept_supervisor_enabled: Record<string, boolean>
  dept_director_mapping: Record<string, string>   // deptId -> directorUserId
  service_gr_sla: ServiceGrSlaConfig
  gr_notification_sla: GrNotificationSlaConfig
  prepayment_config: PrepaymentConfig
  budget_admin_config: BudgetAdminConfig
  collection_config: CollectionConfig
  workflow_defs: {
    pr: WorkflowNodeDef[]
    po: WorkflowNodeDef[]
    pa: WorkflowNodeDef[]
  }
}

export interface NotificationSettings {
  default_channel: 'email_only' | 'teams_only' | 'both' | 'none'
  teams_webhook_url: string | null
  followup_time: string  // "HH:MM" UTC
  system_url?: string
}

export interface EmailTemplate {
  subject: string
  body: string
}

export type UpdateConfigBody = Partial<CompanyConfig>

export const configService = {
  get: () => api.get<CompanyConfig>('/config'),

  update: (body: UpdateConfigBody) =>
    api.patch<CompanyConfig>('/config', body),

  testSmtp: (to: string, kind: 'task' | 'po' = 'task') =>
    api.post<{ message: string }>('/config/test-smtp', { to, kind }),

  // Role permissions
  getRolePermissions: () =>
    api.get<RolePermissionMatrix>('/config/role-permissions'),

  updateRolePermissions: (body: UpdateRolePermissionsBody) =>
    api.patch<RolePermissionMatrix>('/config/role-permissions', body),

  getLockedPermissions: () =>
    api.get<Record<string, string[]>>('/config/locked-permissions'),

  getPermissionKeys: () =>
    api.get<string[]>('/config/permission-keys'),

  // Current user's effective permissions (primary ∪ additional roles).
  getMyPermissions: () =>
    api.get<MyPermissions>('/config/me/permissions'),

  // Every user's ADDITIONAL roles only (identity user_roles assignments,
  // no primary role mixed in). Same proxy Portal's Access Control page uses.
  // ADMIN-ONLY (proxies identity's system_admin-gated /authz/user-roles) —
  // do not call this from a page a non-admin viewer can reach; use
  // getMyAssignedRoles below for a self-scoped, ungated read.
  getUserRoles: () =>
    api.get<{ user_roles: Record<string, string[]> }>('/config/user-roles'),

  // The caller's OWN additional roles only — no admin gate (same-DB read
  // scoped to the JWT sub by construction). Use this instead of
  // getUserRoles() whenever the caller only needs to resolve their own
  // role assignment (e.g. BudgetDashboard's isFinanceBpAssigned).
  getMyAssignedRoles: () =>
    api.get<{ role_codes: string[] }>('/config/me/assigned-roles'),

  // Custom roles
  listRoles: () =>
    api.get<CustomRole[]>('/config/roles'),
}
