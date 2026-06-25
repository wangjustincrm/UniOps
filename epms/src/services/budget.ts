/**
 * Budget service — now calls budget-api (:8007) directly.
 *
 * Budget data (catalog L1/L2, plans, balance, actuals) was moved out of epms-api
 * into the budget-api microservice in 2026-05. This client uses budgetApi (a
 * separate base URL pointing at budget-api).
 *
 * Notes on data model:
 *  - L1 catalog is now shared across all CostCenters (no cost_center_id on L1).
 *  - Per-CC annual_budget / committed / actual_spent are computed from
 *    plan_lines (annual_budget) and budget_ledger (committed/actual_spent).
 *    Use the /balance and /actuals/summary endpoints to get those numbers.
 */
import { budgetApi } from '@/lib/api'

// ── Catalog types ─────────────────────────────────────────────────────────────

export interface ApiBudgetAccount {
  id: string
  code: string
  l1_id: string
  /** Convenience: filled in by frontend when flattening the L1 hierarchy. */
  l1_code?: string
  name: string
  description?: string | null
  is_active: boolean
  decomposition_enabled: boolean
  sort_order: number
}

export interface ApiBudgetL1 {
  id: string
  code: string
  name: string
  description?: string | null
  is_active: boolean
  sort_order: number
  /** Present only when the GET /l1?include_accounts=true variant is called. */
  accounts?: ApiBudgetAccount[]
}

export interface BudgetOverviewResponse {
  l1_groups: ApiBudgetL1[]
  accounts: ApiBudgetAccount[]
}

/**
 * @deprecated Old shape used when budget data lived in epms-api.
 * Kept for back-compat with BudgetDashboard.tsx; new code should use ApiAccountSummary.
 */
export interface ApiL1Summary {
  l1_id: string
  l1_code: string
  l1_name: string
  annual_budget: number
  committed: number
  actual_spent: number
  available: number
}

/**
 * @deprecated Old shape returned by /budget/summary. The endpoint no longer exists;
 * use getActualsSummary() and reshape on the client if you need per-CC totals.
 */
export interface ApiCostCenterSummary {
  cost_center_id: string
  cost_center_code: string
  cost_center_name: string
  department_id: string
  department_code: string
  annual_budget: number
  committed: number
  actual_spent: number
  available: number
  l1_breakdown: ApiL1Summary[]
}

// ── Balance / Actuals types ───────────────────────────────────────────────────

export interface ApiBalance {
  cost_center_id: string
  account_id: string
  account_code: string
  fiscal_year: number
  annual_budget: number
  committed: number
  actual_spent: number
  available: number
}

export interface ApiAccountSummary {
  account_id: string
  account_code: string
  account_name: string
  l1_id: string
  l1_code: string
  annual_budget: number
  committed: number
  actual_spent: number
  available: number
  utilisation_pct: number
}

export interface ApiActualsSummary {
  cost_center_id: string | null
  fiscal_year: number
  accounts: ApiAccountSummary[]
}

export interface ApiMonthlyAccountSummary {
  account_id: string
  account_code: string
  account_name: string
  l1_id: string
  l1_code: string
  /** 1..12 → planned amount (current approved plan) */
  plan_by_month: Record<number, number>
  /** 1..12 → actual spent (ledger: actualize + book_expense + opening) */
  actual_by_month: Record<number, number>
  plan_year: number
  actual_year: number
}

export interface ApiMonthlyActualsSummary {
  cost_center_id: string | null
  fiscal_year: number
  accounts: ApiMonthlyAccountSummary[]
}

// ── CRUD body types ───────────────────────────────────────────────────────────

export interface CreateBudgetL1Body {
  code: string
  name: string
  description?: string
  sort_order?: number
}

export interface UpdateBudgetL1Body {
  name?: string
  description?: string
  sort_order?: number
  is_active?: boolean
}

export interface CreateBudgetAccountBody {
  code: string
  l1_id: string
  name: string
  description?: string
  sort_order?: number
  decomposition_enabled?: boolean
}

export interface UpdateBudgetAccountBody {
  name?: string
  description?: string
  sort_order?: number
  is_active?: boolean
  decomposition_enabled?: boolean
}

export interface BudgetImportResult {
  l1_created: number
  l1_updated: number
  accounts_created: number
  accounts_updated: number
  errors: string[]
}

export interface PlanImportResult {
  lines_updated: number
  accounts_touched: number
  accounts_skipped: number
  breakdowns_cleared: number
  errors: string[]
}

// ── Plan types ────────────────────────────────────────────────────────────────

export interface ApiPlan {
  id: string
  cost_center_id: string
  fiscal_year: number
  status: 'draft' | 'submitted' | 'in_review' | 'approved' | 'returned' | 'rejected' | 'cancelled'
  submitted_at: string | null
  approved_at: string | null
  approval_step_idx: number
  notes: string | null
  created_at: string
  updated_at: string
  created_by: string
  // Revision lineage (Plan A)
  version: number
  parent_plan_id: string | null
  is_current: boolean
  revision_notes: string | null
}

export interface ApiBreakdown {
  id: string
  plan_line_id: string
  factor_combo: Record<string, string>
  amount: number
  notes: string | null
  created_at: string
}

export interface ApiAccountPlanRow {
  account_id: string
  account_code: string
  account_name: string
  l1_id: string
  l1_code: string
  l1_name: string
  decomposition_enabled: boolean
  months: Record<number, number>
  breakdowns_by_month: Record<number, ApiBreakdown[]>
  q1: number
  q2: number
  q3: number
  q4: number
  year_total: number
}

export interface ApiPlanGrid {
  plan: ApiPlan
  rows: ApiAccountPlanRow[]
  grand_total: number
}

// ── Factor types ──────────────────────────────────────────────────────────────

export interface ApiFactorValue {
  id: string
  factor_id: string
  value_code: string
  value_name: string
  sort_order: number
  is_active: boolean
}

export interface ApiFactor {
  id: string
  account_id: string
  factor_code: string
  factor_name: string
  sort_order: number
  is_active: boolean
  values: ApiFactorValue[]
}

// ── Factor Library (reusable templates) ───────────────────────────────────────

export interface ApiFactorTemplateValue {
  id: string
  template_id: string
  value_code: string
  value_name: string
  sort_order: number
  is_active: boolean
}

export interface ApiFactorTemplate {
  id: string
  factor_code: string
  factor_name: string
  description: string | null
  is_active: boolean
  values: ApiFactorTemplateValue[]
}

// ── Settings ──────────────────────────────────────────────────────────────────

export interface ApiBudgetSettings {
  max_factors_per_account: number
  updated_at: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function normalizeAccount(a: ApiBudgetAccount, l1_code?: string): ApiBudgetAccount {
  return { ...a, l1_code: l1_code ?? a.l1_code }
}

// ── Service ───────────────────────────────────────────────────────────────────

export const budgetService = {
  // ── Catalog ────────────────────────────────────────────────────────────────
  listL1: (params?: { is_active?: boolean; include_accounts?: boolean }) =>
    budgetApi.get<ApiBudgetL1[]>('/l1', params),

  listAccounts: (params?: { l1_id?: string; is_active?: boolean; decomposition_enabled?: boolean }) =>
    budgetApi.get<ApiBudgetAccount[]>('/accounts', params),

  createL1: (body: CreateBudgetL1Body) =>
    budgetApi.post<ApiBudgetL1>('/l1', body),

  updateL1: (id: string, body: UpdateBudgetL1Body) =>
    budgetApi.patch<ApiBudgetL1>(`/l1/${id}`, body),

  deleteL1: (id: string) => budgetApi.delete(`/l1/${id}`),

  createAccount: (body: CreateBudgetAccountBody) =>
    budgetApi.post<ApiBudgetAccount>('/accounts', body),

  updateAccount: (id: string, body: UpdateBudgetAccountBody) =>
    budgetApi.patch<ApiBudgetAccount>(`/accounts/${id}`, body),

  deleteAccount: (id: string) => budgetApi.delete(`/accounts/${id}`),

  // Convenience: returns full L1 tree with accounts inline
  getOverview: async (): Promise<BudgetOverviewResponse> => {
    const l1_groups = await budgetApi.get<ApiBudgetL1[]>('/l1', { include_accounts: true })
    const accounts: ApiBudgetAccount[] = []
    for (const l1 of l1_groups) {
      for (const a of (l1.accounts ?? [])) {
        accounts.push(normalizeAccount(a, l1.code))
      }
    }
    return { l1_groups, accounts }
  },

  // ── Balance / Actuals (replaces old summary endpoint) ─────────────────────
  getBalance: (params: { cost_center_id: string; fiscal_year: number; account_code?: string; account_id?: string }) =>
    budgetApi.get<ApiBalance>('/balance', params),

  getActualsSummary: (params: { fiscal_year: number; cost_center_id?: string }) =>
    budgetApi.get<ApiActualsSummary>('/actuals/summary', params),

  getMonthlyActualsSummary: (params: { fiscal_year: number; cost_center_id?: string }) =>
    budgetApi.get<ApiMonthlyActualsSummary>('/actuals/monthly-summary', params),

  // ── Plans ─────────────────────────────────────────────────────────────────
  listPlans: (params?: { cost_center_id?: string; fiscal_year?: number; status?: string; include_history?: boolean }) =>
    budgetApi.get<ApiPlan[]>('/plans', params),

  getPlan: (id: string) => budgetApi.get<ApiPlanGrid>(`/plans/${id}`),

  createPlan: (body: { cost_center_id: string; fiscal_year: number; notes?: string }) =>
    budgetApi.post<ApiPlan>('/plans', body),

  deletePlan: (planId: string) => budgetApi.delete(`/plans/${planId}`),

  updatePlanLine: (planId: string, accountId: string, month: number, body: { amount: number; notes?: string }) =>
    budgetApi.patch(`/plans/${planId}/lines/${accountId}/${month}`, body),

  planAction: (planId: string, action: string, comment?: string) =>
    budgetApi.post<ApiPlan>(`/plans/${planId}/action`, { action, comment }),

  copyFromPrev: (planId: string) =>
    budgetApi.post<{ lines_copied: number }>(`/plans/${planId}/copy-from-prev`),

  // Revise an approved plan → returns the new draft v+1
  revisePlan: (planId: string, body: { revision_notes?: string }) =>
    budgetApi.post<ApiPlan>(`/plans/${planId}/revise`, body),

  // List all versions for a (CC, fiscal_year) — newest first
  listPlanVersions: (costCenterId: string, fiscalYear: number) =>
    budgetApi.get<ApiPlan[]>(`/plans/by-cc-year/${costCenterId}/${fiscalYear}/versions`),

  // Download this plan as CSV (triggers browser save dialog).
  exportPlan: async (planId: string, filename?: string): Promise<void> => {
    const { useAuthStore } = await import('@/stores/auth.store')
    const token = useAuthStore.getState().token
    const { BUDGET_BASE } = await import('@/lib/api')
    const res = await fetch(`${BUDGET_BASE}/api/v1/plans/${planId}/export`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`
      try {
        const body = await res.json()
        if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      } catch { /* ignore */ }
      throw new Error(`Plan export failed: ${detail}`)
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const cd = res.headers.get('content-disposition') ?? ''
    const match = cd.match(/filename="([^"]+)"/)
    a.download = filename ?? match?.[1] ?? 'budget-plan.csv'
    a.click()
    URL.revokeObjectURL(url)
  },

  // Upload CSV to bulk-update plan lines. Plan must be in draft/returned.
  importPlan: async (planId: string, file: File): Promise<PlanImportResult> => {
    const { useAuthStore } = await import('@/stores/auth.store')
    const token = useAuthStore.getState().token
    const { BUDGET_BASE } = await import('@/lib/api')
    const formData = new FormData()
    formData.append('file', file)
    const res = await fetch(`${BUDGET_BASE}/api/v1/plans/${planId}/import`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: formData,
    })
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`
      try {
        const body = await res.json()
        if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      } catch { /* ignore */ }
      throw new Error(`Plan import failed: ${detail}`)
    }
    return res.json()
  },

  // ── Breakdowns (decomposition) ────────────────────────────────────────────
  listBreakdowns: (planId: string, accountId: string, month: number) =>
    budgetApi.get<ApiBreakdown[]>(`/plans/${planId}/lines/${accountId}/${month}/breakdowns`),

  createBreakdown: (planId: string, accountId: string, month: number, body: { factor_combo: Record<string, string>; amount: number; notes?: string }) =>
    budgetApi.post<ApiBreakdown>(`/plans/${planId}/lines/${accountId}/${month}/breakdowns`, body),

  updateBreakdown: (id: string, body: { factor_combo?: Record<string, string>; amount?: number; notes?: string }) =>
    budgetApi.patch<ApiBreakdown>(`/breakdowns/${id}`, body),

  deleteBreakdown: (id: string) => budgetApi.delete(`/breakdowns/${id}`),

  /** Atomic bulk replace of all breakdowns for one (plan, account, month) cell.
   * Used by the Matrix editor on Save — DELETE existing + INSERT all + recompute
   * line.amount in a single transaction. Body is the full new list. */
  replaceBreakdowns: (
    planId: string, accountId: string, month: number,
    items: Array<{ factor_combo: Record<string, string>; amount: number; notes?: string }>,
  ) => budgetApi.put<{ breakdowns: ApiBreakdown[]; line_amount: number }>(
    `/plans/${planId}/lines/${accountId}/${month}/breakdowns`,
    { breakdowns: items },
  ),

  /** Historical reference data for the matrix sidebar (last year + actuals). */
  getBaseline: (planId: string, accountId: string, month: number) =>
    budgetApi.get<{
      last_year_same_month: number | null
      current_month_actual: number | null
      ytd_actual: number | null
      last_year_breakdowns: ApiBreakdown[]
    }>(`/plans/${planId}/lines/${accountId}/${month}/baseline`),

  // ── Factors ───────────────────────────────────────────────────────────────
  listFactors: (accountId: string) =>
    budgetApi.get<ApiFactor[]>(`/accounts/${accountId}/factors`),

  createFactor: (accountId: string, body: { factor_code: string; factor_name: string; sort_order?: number; values?: Array<{ value_code: string; value_name: string; sort_order?: number }> }) =>
    budgetApi.post<ApiFactor>(`/accounts/${accountId}/factors`, body),

  updateFactor: (id: string, body: { factor_name?: string; sort_order?: number; is_active?: boolean }) =>
    budgetApi.patch<ApiFactor>(`/factors/${id}`, body),

  deleteFactor: (id: string) => budgetApi.delete(`/factors/${id}`),

  createFactorValue: (factorId: string, body: { value_code: string; value_name: string; sort_order?: number }) =>
    budgetApi.post<ApiFactorValue>(`/factors/${factorId}/values`, body),

  updateFactorValue: (id: string, body: { value_name?: string; sort_order?: number; is_active?: boolean }) =>
    budgetApi.patch<ApiFactorValue>(`/factor-values/${id}`, body),

  deleteFactorValue: (id: string) => budgetApi.delete(`/factor-values/${id}`),

  // ── Factor Library (read-only from EPMS — managed in Portal) ─────────────
  listFactorTemplates: (opts?: { active_only?: boolean }) => {
    const qs = opts?.active_only ? '?active_only=true' : ''
    return budgetApi.get<ApiFactorTemplate[]>(`/factor-templates${qs}`)
  },

  createFactorFromTemplate: (
    accountId: string,
    body: { template_id: string; factor_code?: string; factor_name?: string; sort_order?: number },
  ) =>
    budgetApi.post<ApiFactor>(`/accounts/${accountId}/factors/from-template`, body),

  // ── Settings ──────────────────────────────────────────────────────────────
  getSettings: () => budgetApi.get<ApiBudgetSettings>('/settings'),

  updateSettings: (body: { max_factors_per_account?: number }) =>
    budgetApi.patch<ApiBudgetSettings>('/settings', body),

  // ── Back-compat shims (deprecated — pages should migrate to new API) ──────
  /** @deprecated Use listAccounts() + getActualsSummary() */
  getAccounts: async (): Promise<ApiBudgetAccount[]> => {
    return budgetApi.get<ApiBudgetAccount[]>('/accounts')
  },

  /**
   * @deprecated Use listL1({include_accounts: true}) directly.
   * Returns flattened {l1_groups, accounts} shape compatible with old hooks.
   */
  getL1ByCostCenter: async (_costCenterId: string): Promise<BudgetOverviewResponse> => {
    // L1/L2 catalog is now shared — cost_center_id filter no longer applies at catalog level.
    // Per-CC balance/plan numbers should come from getBalance() / getActualsSummary().
    const l1_groups = await budgetApi.get<ApiBudgetL1[]>('/l1', { include_accounts: true })
    const accounts: ApiBudgetAccount[] = []
    for (const l1 of l1_groups) {
      for (const a of (l1.accounts ?? [])) accounts.push(normalizeAccount(a, l1.code))
    }
    return { l1_groups, accounts }
  },

  /**
   * @deprecated Use getActualsSummary() and reshape — this shim returns an empty list.
   * The old per-CC pre-aggregated summary endpoint no longer exists. Callers should
   * call getActualsSummary({fiscal_year}) and group by cost_center on the frontend.
   */
  getSummary: async (): Promise<unknown[]> => {
    return []
  },

  // ── CSV Import / Export ───────────────────────────────────────────────────
  importCsv: async (file: File): Promise<BudgetImportResult> => {
    const { useAuthStore } = await import('@/stores/auth.store')
    const token = useAuthStore.getState().token
    const { BUDGET_BASE } = await import('@/lib/api')
    const formData = new FormData()
    formData.append('file', file)
    const response = await fetch(`${BUDGET_BASE}/api/v1/catalog/import`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: formData,
    })
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`
      try {
        const body = await response.json()
        if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      } catch { /* ignore */ }
      throw new Error(`Budget import failed: ${detail}`)
    }
    return response.json()
  },
}
