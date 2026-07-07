import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { budgetService } from '@/services/budget'
import { useConfig } from '@/hooks/useConfig'
import type {
  CreateBudgetL1Body, UpdateBudgetL1Body,
  CreateBudgetAccountBody, UpdateBudgetAccountBody,
} from '@/services/budget'

// ── Catalog ───────────────────────────────────────────────────────────────────

export function useBudgetOverview() {
  return useQuery({
    queryKey: ['budget', 'overview'],
    queryFn: () => budgetService.getOverview(),
  })
}

/** @deprecated retained for older pages — getSummary endpoint no longer exists */
export function useBudgetSummary() {
  return useQuery({
    queryKey: ['budget', 'summary'],
    queryFn: () => budgetService.getSummary(),
    staleTime: 60_000,
  })
}

/** @deprecated retained for older pages — call useBudgetOverview instead */
export function useBudgetCCDetail(costCenterId: string | null) {
  return useQuery({
    queryKey: ['budget', 'cc-detail', costCenterId],
    queryFn: () => budgetService.getL1ByCostCenter(costCenterId!),
    enabled: !!costCenterId,
    staleTime: 30_000,
  })
}

export function useBudgetAllDetail(enabled: boolean) {
  return useQuery({
    queryKey: ['budget', 'all-detail'],
    queryFn: () => budgetService.getOverview(),
    enabled,
    staleTime: 60_000,
  })
}

export function useBudgetAccounts() {
  return useQuery({
    queryKey: ['budget', 'accounts'],
    queryFn: () => budgetService.listAccounts({ is_active: true }),
  })
}

export function useBudgetL1List(opts?: { is_active?: boolean; include_accounts?: boolean }) {
  return useQuery({
    queryKey: ['budget', 'l1-list', opts],
    queryFn: () => budgetService.listL1(opts),
  })
}

export function useCreateBudgetL1() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateBudgetL1Body) => budgetService.createL1(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget'] }) },
  })
}

export function useUpdateBudgetL1() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateBudgetL1Body }) =>
      budgetService.updateL1(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget'] }) },
  })
}

export function useDeleteBudgetL1() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => budgetService.deleteL1(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget'] }) },
  })
}

export function useCreateBudgetAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateBudgetAccountBody) => budgetService.createAccount(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget'] }) },
  })
}

export function useUpdateBudgetAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateBudgetAccountBody }) =>
      budgetService.updateAccount(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget'] }) },
  })
}

export function useDeleteBudgetAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => budgetService.deleteAccount(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget'] }) },
  })
}

export function useImportBudget() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => budgetService.importCsv(file),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget'] }) },
  })
}

// ── Factors ───────────────────────────────────────────────────────────────────

export function useFactors(accountId: string | null) {
  return useQuery({
    queryKey: ['budget', 'factors', accountId],
    queryFn: () => budgetService.listFactors(accountId!),
    enabled: !!accountId,
  })
}

export function useCreateFactor() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ accountId, body }: { accountId: string; body: Parameters<typeof budgetService.createFactor>[1] }) =>
      budgetService.createFactor(accountId, body),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['budget', 'factors', vars.accountId] })
    },
  })
}

export function useUpdateFactor() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof budgetService.updateFactor>[1] }) =>
      budgetService.updateFactor(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'factors'] }) },
  })
}

export function useDeleteFactor() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => budgetService.deleteFactor(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'factors'] }) },
  })
}

export function useFactorTemplates(activeOnly = true) {
  return useQuery({
    queryKey: ['budget', 'factor-templates', activeOnly],
    queryFn: () => budgetService.listFactorTemplates({ active_only: activeOnly }),
    staleTime: 60_000,
  })
}

export function useCreateFactorFromTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      accountId, body,
    }: {
      accountId: string
      body: Parameters<typeof budgetService.createFactorFromTemplate>[1]
    }) => budgetService.createFactorFromTemplate(accountId, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'factors'] }) },
  })
}

export function useCreateFactorValue() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ factorId, body }: { factorId: string; body: Parameters<typeof budgetService.createFactorValue>[1] }) =>
      budgetService.createFactorValue(factorId, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'factors'] }) },
  })
}

export function useDeleteFactorValue() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => budgetService.deleteFactorValue(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'factors'] }) },
  })
}

export function useUpdateFactorValue() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: {
      id: string
      body: { value_name?: string; sort_order?: number; is_active?: boolean }
    }) => budgetService.updateFactorValue(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'factors'] }) },
  })
}

// ── Plans ─────────────────────────────────────────────────────────────────────

export function usePlans(filter?: { cost_center_id?: string; fiscal_year?: number; status?: string; include_history?: boolean }) {
  return useQuery({
    queryKey: ['budget', 'plans', filter],
    queryFn: () => budgetService.listPlans(filter),
  })
}

export function usePlan(id: string | null) {
  return useQuery({
    queryKey: ['budget', 'plan', id],
    queryFn: () => budgetService.getPlan(id!),
    enabled: !!id,
  })
}

export function useCreatePlan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { cost_center_id: string; fiscal_year: number; notes?: string }) =>
      budgetService.createPlan(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'plans'] }) },
  })
}

export function useDeletePlan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (planId: string) => budgetService.deletePlan(planId),
    onSuccess: (_, planId) => {
      qc.invalidateQueries({ queryKey: ['budget', 'plans'] })
      qc.invalidateQueries({ queryKey: ['budget', 'plan', planId] })
      qc.invalidateQueries({ queryKey: ['budget', 'plan-versions'] })
    },
  })
}

export function useUpdatePlanLine() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ planId, accountId, month, body }: {
      planId: string; accountId: string; month: number; body: { amount: number; notes?: string }
    }) => budgetService.updatePlanLine(planId, accountId, month, body),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['budget', 'plan', vars.planId] })
    },
  })
}

export function usePlanAction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ planId, action, comment }: { planId: string; action: string; comment?: string }) =>
      budgetService.planAction(planId, action, comment),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['budget', 'plans'] })
      qc.invalidateQueries({ queryKey: ['budget', 'plan', vars.planId] })
    },
  })
}

export function useCopyPlanFromPrev() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (planId: string) => budgetService.copyFromPrev(planId),
    onSuccess: (_, planId) => {
      qc.invalidateQueries({ queryKey: ['budget', 'plan', planId] })
    },
  })
}

export function useRevisePlan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ planId, revision_notes }: { planId: string; revision_notes?: string }) =>
      budgetService.revisePlan(planId, { revision_notes }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['budget', 'plans'] })
    },
  })
}

export function useImportPlan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ planId, file }: { planId: string; file: File }) =>
      budgetService.importPlan(planId, file),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['budget', 'plan', vars.planId] })
    },
  })
}

export function usePlanVersions(
  costCenterId: string | null, fiscalYear: number | null,
) {
  return useQuery({
    queryKey: ['budget', 'plan-versions', costCenterId, fiscalYear],
    queryFn: () => budgetService.listPlanVersions(costCenterId!, fiscalYear!),
    enabled: !!costCenterId && fiscalYear != null,
  })
}

export function useCreateBreakdown() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ planId, accountId, month, body }: {
      planId: string; accountId: string; month: number;
      body: { factor_combo: Record<string, string>; amount: number; notes?: string }
    }) => budgetService.createBreakdown(planId, accountId, month, body),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['budget', 'plan', vars.planId] })
    },
  })
}

export function useUpdateBreakdown(planId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof budgetService.updateBreakdown>[1] }) =>
      budgetService.updateBreakdown(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'plan', planId] }) },
  })
}

export function useDeleteBreakdown(planId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => budgetService.deleteBreakdown(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'plan', planId] }) },
  })
}

export function useReplaceBreakdowns(planId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      accountId, month, items,
    }: {
      accountId: string
      month: number
      items: Array<{ factor_combo: Record<string, string>; amount: number; notes?: string }>
    }) => budgetService.replaceBreakdowns(planId, accountId, month, items),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'plan', planId] }) },
  })
}

export function useBaseline(planId: string | null, accountId: string | null, month: number | null) {
  return useQuery({
    queryKey: ['budget', 'baseline', planId, accountId, month],
    queryFn: () => budgetService.getBaseline(planId!, accountId!, month!),
    enabled: !!planId && !!accountId && month != null,
    staleTime: 60_000,
  })
}

// ── Balance / Actuals ─────────────────────────────────────────────────────────

export function useActualsSummary(params: { fiscal_year: number; cost_center_id?: string }) {
  return useQuery({
    queryKey: ['budget', 'actuals-summary', params],
    queryFn: () => budgetService.getActualsSummary(params),
    staleTime: 30_000,
  })
}

export function useMonthlyActualsSummary(params: { fiscal_year: number; cost_center_id?: string }) {
  return useQuery({
    queryKey: ['budget', 'monthly-actuals-summary', params],
    queryFn: () => budgetService.getMonthlyActualsSummary(params),
    staleTime: 30_000,
  })
}

export function useBalance(params: {
  cost_center_id: string; fiscal_year: number;
  account_code?: string; account_id?: string;
} | null) {
  return useQuery({
    queryKey: ['budget', 'balance', params],
    queryFn: () => budgetService.getBalance(params!),
    enabled: !!params && !!params.cost_center_id && !!(params.account_code || params.account_id),
    staleTime: 30_000,
  })
}

// ── Fiscal years ──────────────────────────────────────────────────────────────

const currentYear = new Date().getUTCFullYear()
/** Fallback if CompanyConfig.budget_admin_config.available_fiscal_years is unset. */
const FALLBACK_YEAR_OPTIONS = [currentYear + 1, currentYear, currentYear - 1, currentYear - 2]

/**
 * Fiscal years selectable in budget UIs (dashboard, plan list, …).
 * Reads CompanyConfig.budget_admin_config.available_fiscal_years, newest first.
 */
export function useAvailableFiscalYears(): number[] {
  const { data: config } = useConfig()
  const stored = config?.budget_admin_config?.available_fiscal_years
  if (Array.isArray(stored) && stored.length > 0) {
    // Configured order is ascending; the dropdown / filter prefers newest first.
    return [...stored].sort((a, b) => b - a)
  }
  return FALLBACK_YEAR_OPTIONS
}

// ── Settings ──────────────────────────────────────────────────────────────────

export function useBudgetSettings() {
  return useQuery({
    queryKey: ['budget', 'settings'],
    queryFn: () => budgetService.getSettings(),
  })
}

export function useUpdateBudgetSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { max_factors_per_account?: number }) => budgetService.updateSettings(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budget', 'settings'] }) },
  })
}
