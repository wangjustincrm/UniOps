import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { ArrowLeft, Upload, X, Calendar, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { ProcurementTypeSelector } from '@/components/pr/ProcurementTypeSelector'
import { BudgetBalanceWidget, OverBudgetWarning } from '@/components/pr/BudgetBalanceWidget'
import { PrLineItems, lineItemsTotal, validateLineItems } from '@/components/pr/PrLineItems'
import { budgetAccountError, requiresBudgetAccount } from '@/lib/prBudget'
import { useAuthStore } from '@/stores/auth.store'
import { useConfig } from '@/hooks/useConfig'
import { useBudgetOverview, useFactors, useBalance } from '@/hooks/useBudget'
import { useCostCenters } from '@/hooks/useCostCenters'
import { useDepartments } from '@/hooks/useDepartments'
import { useCreatePr, usePr } from '@/hooks/usePrs'
import { prService } from '@/services/pr'
import { api } from '@/lib/api'
import { prAttachmentService } from '@/services/prAttachments'
import { useVendors } from '@/hooks/useVendors'
import type { ApiVendor } from '@/services/vendors'
import type { ProcurementType, PrLineItem, Currency } from '@/types'
import { CURRENCIES } from '@/types'

const prSchema = z.object({
  title: z.string().min(3, 'Title is required'),
  procurementType: z.number().min(1).max(6, 'Please select a procurement type'),
  vendorSearch: z.string().optional(),
  vendorId: z.string().optional(),
  budgetL1: z.string().optional(),
  budgetL2: z.string().optional(),
  justification: z.string().optional(),
  fixedAssetId: z.string().optional(),
  projectCode: z.string().optional(),
  serviceCompletionDate: z.string().optional(),
  prepaymentRequired: z.boolean().default(false),
  deliveryAddress: z.string().optional(),
  requiredBy: z.string().min(1, 'Required by date is needed'),
  notes: z.string().optional(),
})

type PrForm = z.infer<typeof prSchema>

function defaultLine(): PrLineItem {
  return { id: crypto.randomUUID(), description: '', materialId: '', qty: 1, unit: 'pcs', unitPrice: 0, lineTotal: 0, notes: '' }
}

// Budget figures (annual_budget/committed/actual_spent) are per cost-center +
// fiscal year — they live behind /balance, not on the shared catalog account.
const currentFiscalYear = new Date().getUTCFullYear()

export default function PrCreatePage() {
  const replaceTab = useReplaceTab(epmsRoutes)
  const { user } = useAuthStore()
  const { data: config } = useConfig()
  const { data: budgetData } = useBudgetOverview()
  const [selectedDepartmentId, setSelectedDepartmentId] = useState<string | undefined>(user?.department_id ?? undefined)
  const { data: departmentsData } = useDepartments()
  const departments = (departmentsData?.items ?? []).filter((d) => d.is_active)
  const { data: costCentersData } = useCostCenters({
    department_id: selectedDepartmentId,
    active_only: true,
  })

  const l1Groups = budgetData?.l1_groups ?? []
  const budgetAccounts = budgetData?.accounts ?? []
  const costCenters = costCentersData ?? []

  const [selectedType, setSelectedType] = useState<ProcurementType | null>(null)
  const [selectedCostCenter, setSelectedCostCenter] = useState('')
  const [selectedCostCenterId, setSelectedCostCenterId] = useState<string | undefined>(undefined)
  // L1 catalog is shared across all cost centers (budget refactor: no cost_center on L1).
  // Show every active L1 once a cost center is chosen — the CC scopes the budget/plan,
  // not the catalog. (Filtering by a now-nonexistent cost_center_code left this empty.)
  const ccL1Groups = selectedCostCenter
    ? l1Groups.filter((l) => l.is_active)
    : []
  // L2 accounts scoped to the selected L1 (use nested accounts from L1, avoids missing l1_code field)
  const [selectedL1Obj, setSelectedL1Obj] = useState<(typeof ccL1Groups)[number] | null>(null)
  const [vendorQuery, setVendorQuery] = useState('')
  const [vendorOpen, setVendorOpen] = useState(false)
  const [selectedVendor, setSelectedVendor] = useState<ApiVendor | null>(null)
  const [vendorError, setVendorError] = useState<string | null>(null)
  const [selectedL1, setSelectedL1] = useState('')
  const [selectedL2, setSelectedL2] = useState('')
  // Only active accounts are selectable; keep the current selection even if it went inactive.
  // (Declared after selectedL2 so it isn't referenced before initialization.)
  const ccL2Accounts = (selectedL1Obj?.accounts ?? []).filter((a) => a.is_active || a.code === selectedL2)
  const [factorCombo, setFactorCombo] = useState<Record<string, string>>({})
  const [factorComboError, setFactorComboError] = useState<string | null>(null)
  const [departmentError, setDepartmentError] = useState<string | null>(null)
  const [budgetError, setBudgetError] = useState<string | null>(null)
  const [attachments, setAttachments] = useState<File[]>([])
  const [currency, setCurrency] = useState<Currency>('CAD')
  const [lineItems, setLineItems] = useState<PrLineItem[]>([defaultLine()])
  const [lineErrors, setLineErrors] = useState<Record<string, { description?: string; qty?: string; unitPrice?: string }>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  const createPr = useCreatePr()

  const [searchParams] = useSearchParams()
  const copyFromId = searchParams.get('copyFrom') ?? ''
  const { data: sourcePr } = usePr(copyFromId)
  const [copyFilled, setCopyFilled] = useState(false)
  const [copyVendorId, setCopyVendorId] = useState<string | null>(null)

  const { data: vendorsData } = useVendors({
    active_only: true,
    search: vendorQuery || undefined,
    page_size: 50,
  })
  const vendors = vendorsData?.items ?? []

  const {
    register,
    handleSubmit,
    getValues,
    setValue,
    setError,
    formState: { errors },
  } = useForm<PrForm>({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    resolver: zodResolver(prSchema) as any,
    defaultValues: {
      deliveryAddress: '',
    },
  })

  useEffect(() => {
    if (config?.delivery_address) {
      setValue('deliveryAddress', config.delivery_address)
    }
  }, [config?.delivery_address, setValue])

  useEffect(() => {
    if (!sourcePr || copyFilled) return
    if (!vendorsData || !costCentersData || !budgetData) return

    setSelectedType(sourcePr.type as ProcurementType)
    setValue('title', `Copy of ${sourcePr.title}`)
    setValue('notes', sourcePr.notes ?? '')
    setValue('deliveryAddress', sourcePr.delivery_address ?? config?.delivery_address ?? '')
    setValue('prepaymentRequired', sourcePr.is_prepaid)
    if (sourcePr.project_code) setValue('projectCode', sourcePr.project_code)
    setCurrency(sourcePr.currency as Currency)

    if (sourcePr.line_items.length > 0) {
      setLineItems(
        sourcePr.line_items.map((item) => ({
          id: crypto.randomUUID(),
          description: item.description,
          materialId: item.material_id ?? '',
          supplierItemId: item.supplier_item_id ?? '',
          qty: Number(item.qty),
          unit: item.unit,
          unitPrice: Number(item.unit_price),
          lineTotal: Number(item.line_total),
          notes: item.notes ?? '',
        }))
      )
    }

    if (sourcePr.vendor_id) {
      setCopyVendorId(sourcePr.vendor_id)
      setVendorQuery(sourcePr.vendor_name ?? '')
    }

    if (sourcePr.cost_center_id) {
      const cc = costCentersData.find((c) => c.id === sourcePr.cost_center_id)
      if (cc) {
        setSelectedCostCenter(cc.code)
        setSelectedCostCenterId(cc.id)
        if (sourcePr.budget_code) {
          setSelectedL2(sourcePr.budget_code)
          // L1 catalog is global — search all L1 groups for the one holding this account.
          const ccL1 = budgetData.l1_groups ?? []
          for (const l1 of ccL1) {
            if ((l1.accounts ?? []).some((a: { code: string }) => a.code === sourcePr.budget_code)) {
              setSelectedL1(l1.code)
              setSelectedL1Obj(l1)
              break
            }
          }
        }
      }
    }

    setCopyFilled(true)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourcePr, copyFilled, vendorsData, costCentersData, budgetData])

  const estimatedAmount = lineItemsTotal(lineItems)
  // Look up from ccL2Accounts (already scoped to the selected CC + L1) to avoid matching a
  // same-code account from a different cost center that may have a different budget value.
  const selectedBudgetAccount = selectedL2 ? (ccL2Accounts.find((a) => a.code === selectedL2) ?? null) : null
  // Fetch factors for the selected account; only fires when the account has decomposition enabled.
  const factorsAccountId =
    selectedBudgetAccount && selectedBudgetAccount.decomposition_enabled
      ? selectedBudgetAccount.id
      : null
  const { data: accountFactorsRaw = [] } = useFactors(factorsAccountId)
  const accountFactors = accountFactorsRaw.filter((f) => f.is_active)
  // Reset the factor combo when the user switches account.
  useEffect(() => {
    setFactorCombo({})
    setFactorComboError(null)
  }, [selectedBudgetAccount?.id])
  // Budget figures come from /balance (scoped to the chosen cost center + account),
  // NOT the catalog account — the catalog carries no annual_budget/committed/actual_spent.
  const { data: budgetBalance } = useBalance(
    selectedCostCenterId && selectedL2
      ? { cost_center_id: selectedCostCenterId, fiscal_year: currentFiscalYear, account_code: selectedL2 }
      : null,
  )
  const annualBudget = Number(budgetBalance?.annual_budget ?? 0)
  const committed    = Number(budgetBalance?.committed ?? 0)
  const actualSpent  = Number(budgetBalance?.actual_spent ?? 0)
  const available = annualBudget - committed - actualSpent
  // Local /balance subtraction — kept for the "available" display and as a
  // fallback if the authoritative budget-check call hasn't answered / errored.
  const localOverBudget = selectedBudgetAccount ? estimatedAmount > available && estimatedAmount > 0 : false
  // Authoritative over-budget decision: ask the backend the same question it
  // will answer at persist time, so the UI gate (hard-block / justification)
  // matches what the server stores. Falls back to the local calc on error.
  const budgetCheckEnabled = Boolean(
    selectedBudgetAccount && selectedCostCenterId && selectedL2 && estimatedAmount > 0,
  )
  const { data: budgetCheck, isError: budgetCheckError } = useQuery({
    queryKey: [
      'pr-budget-check',
      selectedCostCenterId,
      user?.department_id,
      selectedL2,
      factorCombo,
      getValues('projectCode') || null,
      estimatedAmount,
    ],
    queryFn: () =>
      api.post<{ over_budget: boolean; available: string }>('/pr/budget-check', {
        cost_center_id: selectedCostCenterId,
        department_id: user?.department_id ?? undefined,
        factor_combo: accountFactors.length > 0 ? factorCombo : undefined,
        project_code: getValues('projectCode') || undefined,
        amount: estimatedAmount,
      }),
    enabled: budgetCheckEnabled,
  })
  const isOverBudget = selectedBudgetAccount
    ? (budgetCheck && !budgetCheckError ? budgetCheck.over_budget : localOverBudget)
    : false
  const overage = isOverBudget ? Math.max(estimatedAmount - available, 0) : 0
  // Over-Budget Approval Mode (single source of truth = Admin → Budget Config)
  const overBudgetMode = config?.budget_admin_config?.over_budget_mode ?? 'fm_gm_opm'
  const isHardBlock = isOverBudget && overBudgetMode === 'hard_block'

  const filteredVendors = vendors

  const onSubmit = async (data: unknown) => {
    const formData = data as { title: string; requiredBy: string; deliveryAddress?: string; notes?: string; fixedAssetId?: string; projectCode?: string; serviceCompletionDate?: string; prepaymentRequired?: boolean; justification?: string }
    // Vendor is required on Submit — the field lives in local state (not RHF), so
    // guard it here (drafts stay lenient, matching handleDraftSave).
    if (!selectedVendor) {
      setVendorError('Please select a vendor')
      return
    }
    // Validate line items
    const errs = validateLineItems(lineItems)
    if (Object.keys(errs).length > 0) {
      setLineErrors(errs)
      return
    }
    if (estimatedAmount === 0) {
      setLineErrors({ '0': { unitPrice: 'At least one line must have a price' } })
      return
    }
    // Hard-block guard — Submit button is already disabled, but guard the
    // programmatic path in case it's invoked some other way.
    if (isHardBlock) return
    // Department is required at submit time: the approval engine raises when it
    // can't resolve a dept_manager for a NULL department. Default is pre-filled
    // from the user's own department, so this only bites when cleared/unset.
    if (!selectedDepartmentId) {
      setDepartmentError('Department is required')
      return
    }
    // Budget account is required for every type except Type 1. Without it the
    // server's budget check short-circuits, so an unbudgeted PR would sail past
    // even a `hard_block` config. epms-api rejects this with a 409 — catch it
    // here so the user gets a field-level message instead.
    const budgetErr = budgetAccountError(selectedType, selectedCostCenterId, selectedL2)
    if (budgetErr) {
      setBudgetError(budgetErr)
      return
    }
    // Factor combo: when the selected Account has decomposition factors, every
    // factor must have a value picked (matches the per-PR Required policy).
    if (accountFactors.length > 0) {
      const missing = accountFactors.filter((f) => !factorCombo[f.factor_code])
      if (missing.length > 0) {
        setFactorComboError(
          `Select a value for: ${missing.map((f) => f.factor_name).join(', ')}`,
        )
        return
      }
    }
    // Over-budget but pre-approval allowed: require non-empty justification.
    const justification = (formData.justification ?? '').trim()
    if (isOverBudget && !isHardBlock && !justification) {
      setError('justification', { type: 'manual', message: 'Justification is required for over-budget PRs' })
      return
    }
    setIsSubmitting(true)
    try {
      const newPr = await createPr.mutateAsync({
        title: formData.title,
        type: selectedType!,
        currency,
        vendor_id: selectedVendor?.id,
        is_prepaid: formData.prepaymentRequired ?? false,
        project_code: formData.projectCode || undefined,
        cost_center_id: selectedCostCenterId,
        department_id: selectedDepartmentId,
        budget_code: selectedL2 || undefined,
        factor_combo: accountFactors.length > 0 ? factorCombo : undefined,
        required_by: formData.requiredBy,
        delivery_address: formData.deliveryAddress || config?.delivery_address || undefined,
        notes: formData.notes,
        over_budget_justification: isOverBudget ? justification : undefined,
        // amount / vendor_name / line_total are recomputed server-side.
        line_items: lineItems.map((item) => ({
          description: item.description,
          material_id: item.materialId || undefined,
          supplier_item_id: item.supplierItemId || undefined,
          qty: item.qty,
          unit: item.unit,
          unit_price: item.unitPrice,
          notes: item.notes || undefined,
        })),
      })
      await Promise.all(attachments.map((f) => prAttachmentService.upload(newPr.id, f)))
      await prService.action(newPr.id, { action: 'submit' })
      replaceTab(`/pr/${newPr.id}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleDraftSave = async () => {
    const values = getValues()
    if (!values.title?.trim() || !selectedType) return
    setIsSubmitting(true)
    try {
      const draftJustification = (values.justification ?? '').trim()
      const newPr = await createPr.mutateAsync({
        title: values.title.trim(),
        type: selectedType,
        currency,
        vendor_id: selectedVendor?.id,
        cost_center_id: selectedCostCenterId,
        department_id: selectedDepartmentId,
        budget_code: selectedL2 || undefined,
        // Drafts may have a partial combo; only send when all factors are picked
        // to satisfy the server-side shape check (drop empty values otherwise).
        factor_combo:
          accountFactors.length > 0 &&
          accountFactors.every((f) => factorCombo[f.factor_code])
            ? factorCombo
            : undefined,
        required_by: values.requiredBy || undefined,
        delivery_address: values.deliveryAddress || config?.delivery_address || undefined,
        notes: values.notes,
        over_budget_justification: draftJustification || undefined,
        // amount / vendor_name / line_total are recomputed server-side.
        line_items: lineItems
          .filter((item) => item.description.trim())
          .map((item) => ({
            description: item.description,
            material_id: item.materialId || undefined,
            supplier_item_id: item.supplierItemId || undefined,
            qty: item.qty,
            unit: item.unit,
            unit_price: item.unitPrice,
            notes: item.notes || undefined,
          })),
      })
      await Promise.all(attachments.map((f) => prAttachmentService.upload(newPr.id, f)))
      replaceTab(`/pr/${newPr.id}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    setAttachments((prev) => [...prev, ...files])
    e.target.value = ''
  }

  useEffect(() => {
    if (!copyVendorId || selectedVendor) return
    const match = vendorsData?.items.find((v) => v.id === copyVendorId)
    if (match) {
      setSelectedVendor(match)
      setVendorQuery('')
    }
  }, [copyVendorId, vendorsData, selectedVendor])

  useEffect(() => {
    if (selectedType) setValue('procurementType', selectedType)
  }, [selectedType, setValue])

  const requiresBudget = requiresBudgetAccount(selectedType)
  const requiresFixedAsset = selectedType === 5
  const requiresProject = selectedType === 6
  const requiresServiceDate = selectedType === 4

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <Link to="/pr">
            <Button variant="ghost" size="icon-sm">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <h1 className="text-2xl font-bold text-neutral-900">
            {sourcePr ? `New Purchase Requisition — Copy of ${sourcePr.number}` : 'New Purchase Requisition'}
          </h1>
        </div>
        {sourcePr && (
          <div className="rounded-lg border border-info-200 bg-info-50 px-4 py-2.5 text-sm text-info-700">
            Copied from <span className="font-medium">{sourcePr.number}</span> — review all fields before submitting. Required By date has been cleared.
          </div>
        )}
      </div>

      <form onSubmit={handleSubmit(onSubmit as Parameters<typeof handleSubmit>[0])} noValidate>
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          {/* Main form */}
          <div className="lg:col-span-9 flex flex-col gap-6">

            {/* Procurement Type */}
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
              <h2 className="text-base font-semibold text-neutral-900 mb-4">
                Select Procurement Type <span className="text-danger-600">*</span>
              </h2>
              <ProcurementTypeSelector value={selectedType} onChange={setSelectedType} />
              {errors.procurementType && (
                <p className="mt-2 text-xs text-danger-600">{errors.procurementType.message}</p>
              )}
            </div>

            {/* Basic info + Line Items */}
            {selectedType && (
              <>
                <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
                  <h2 className="text-base font-semibold text-neutral-900">Requisition Details</h2>

                  {/* PR Title */}
                  <FormField label="PR Title / Description" required htmlFor="title" error={errors.title?.message}>
                    <Input
                      id="title"
                      placeholder="Brief description of what you need"
                      error={!!errors.title}
                      {...register('title')}
                    />
                  </FormField>

                  {/* Vendor search */}
                  <div className="flex flex-col gap-1.5">
                    <label className="text-sm font-medium text-neutral-700">
                      Preferred Vendor <span className="text-danger-600">*</span>
                    </label>
                    <div className="relative">
                      <div className="relative">
                        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                        <input
                          type="text"
                          placeholder="Search vendor by name or POID…"
                          value={selectedVendor ? selectedVendor.name : vendorQuery}
                          onFocus={() => { setVendorOpen(true); if (selectedVendor) setVendorQuery('') }}
                          onChange={(e) => { setVendorQuery(e.target.value); setSelectedVendor(null); setVendorOpen(true) }}
                          className={`h-10 w-full rounded-md border bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 ${vendorError && !selectedVendor ? 'border-danger-500' : 'border-neutral-300'}`}
                        />
                        {selectedVendor && (
                          <button
                            type="button"
                            onClick={() => { setSelectedVendor(null); setVendorQuery('') }}
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-neutral-400 hover:text-neutral-600"
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                      {vendorOpen && !selectedVendor && (
                        <>
                          <div className="fixed inset-0 z-10" onClick={() => setVendorOpen(false)} />
                          <div className="absolute z-20 mt-1 w-full rounded-lg border border-neutral-200 bg-white py-1 shadow-lg">
                            {filteredVendors.map((v) => (
                              <button
                                key={v.id}
                                type="button"
                                className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left"
                                onClick={() => { setSelectedVendor(v); setVendorOpen(false); setVendorQuery(''); setVendorError(null) }}
                              >
                                <span className="font-mono text-xs rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-600">{v.code}</span>
                                {v.name}
                              </button>
                            ))}
                            {filteredVendors.length === 0 && (
                              <p className="px-3 py-2 text-sm text-neutral-400">No vendors found</p>
                            )}
                            <div className="border-t border-neutral-100 px-3 py-2">
                              <button type="button" className="text-xs text-primary-600 hover:underline">
                                + Add new vendor
                              </button>
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                    {selectedVendor && (
                      <p className="text-xs text-success-600">✓ {selectedVendor.name} ({selectedVendor.code})</p>
                    )}
                    {vendorError && !selectedVendor && (
                      <p className="text-xs text-danger-600">{vendorError}</p>
                    )}
                  </div>

                  {/* Department — drives cost center / budget filtering and approval routing */}
                  <div className="flex flex-col gap-1.5">
                    <label className="text-sm font-medium text-neutral-700">
                      Department <span className="text-danger-600">*</span>
                    </label>
                    <select
                      value={selectedDepartmentId ?? ''}
                      onChange={(e) => {
                        setSelectedDepartmentId(e.target.value || undefined)
                        setDepartmentError(null)
                        // department changed → clear cost-center cascade (cc belongs to the old dept)
                        setSelectedCostCenter(''); setSelectedCostCenterId(undefined)
                        setSelectedL1(''); setSelectedL1Obj(null); setSelectedL2('')
                        setFactorCombo({}); setFactorComboError(null)
                      }}
                      className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                    >
                      <option value="">Select Department…</option>
                      {departments.map((d) => (
                        <option key={d.id} value={d.id}>{d.name}</option>
                      ))}
                    </select>
                    {departmentError && (
                      <p className="text-xs text-danger-600">{departmentError}</p>
                    )}
                  </div>

                  {/* Budget — hidden for Type 1 */}
                  {requiresBudget && (
                    <div className="flex flex-col gap-2">
                      <label className="text-sm font-medium text-neutral-700">
                        Budget Account <span className="text-danger-600">*</span>
                      </label>
                      {/* Step 1: Cost Center */}
                      <select
                        value={selectedCostCenter}
                        onChange={(e) => {
                          const code = e.target.value
                          setSelectedCostCenter(code)
                          setSelectedCostCenterId(costCenters.find((cc) => cc.code === code)?.id)
                          setSelectedL1(''); setSelectedL1Obj(null); setSelectedL2('')
                        }}
                        className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                      >
                        <option value="">Select Cost Center…</option>
                        {costCenters.map((cc) => (
                          <option key={cc.id} value={cc.code}>{cc.name}</option>
                        ))}
                      </select>
                      {/* Step 2: L1 Category (scoped to cost center) */}
                      <select
                        value={selectedL1}
                        onChange={(e) => {
                          const code = e.target.value
                          setSelectedL1(code)
                          setSelectedL1Obj(ccL1Groups.find((l) => l.code === code) ?? null)
                          setSelectedL2('')
                        }}
                        disabled={!selectedCostCenter}
                        className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400"
                      >
                        <option value="">Select L1 Category…</option>
                        {ccL1Groups.map((l1) => (
                          <option key={l1.id} value={l1.code}>{l1.name}</option>
                        ))}
                      </select>
                      {/* Step 3: L2 Account */}
                      <select
                        value={selectedL2}
                        onChange={(e) => { setSelectedL2(e.target.value); setBudgetError(null) }}
                        disabled={!selectedL1}
                        className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400"
                      >
                        <option value="">Select L2 Sub-account…</option>
                        {ccL2Accounts.map((l2) => (
                          <option key={l2.id} value={l2.code}>{l2.code} — {l2.name}</option>
                        ))}
                      </select>
                      {budgetError && (
                        <p className="text-xs text-danger-600">{budgetError}</p>
                      )}
                    </div>
                  )}

                  {/* Factor selectors — shown when the chosen Budget Account has factors configured */}
                  {accountFactors.length > 0 && (
                    <div className="rounded-lg border border-primary-200 bg-primary-50/40 p-3">
                      <div className="mb-2 flex items-baseline justify-between gap-2">
                        <p className="text-xs font-semibold text-primary-800">
                          Decomposition Factors — required
                        </p>
                        <p className="text-[11px] text-primary-700/80">
                          Pick a value for each factor so this PR's budget impact maps to the right breakdown line.
                        </p>
                      </div>
                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                        {accountFactors.map((f) => {
                          const activeValues = f.values.filter((v) => v.is_active)
                          const current = factorCombo[f.factor_code] ?? ''
                          return (
                            <div key={f.id} className="flex flex-col gap-1">
                              <label className="text-[11px] font-medium text-neutral-700">
                                {f.factor_name}
                                <span className="ml-1 font-mono text-neutral-400">({f.factor_code})</span>
                              </label>
                              <select
                                value={current}
                                onChange={(e) => {
                                  const v = e.target.value
                                  setFactorCombo((prev) => {
                                    const next = { ...prev }
                                    if (v) next[f.factor_code] = v
                                    else delete next[f.factor_code]
                                    return next
                                  })
                                  setFactorComboError(null)
                                }}
                                className="h-9 rounded-md border border-neutral-300 bg-white px-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                              >
                                <option value="">Select…</option>
                                {activeValues.map((v) => (
                                  <option key={v.id} value={v.value_code}>
                                    {v.value_code} — {v.value_name}
                                  </option>
                                ))}
                              </select>
                            </div>
                          )
                        })}
                      </div>
                      {factorComboError && (
                        <p className="mt-2 text-xs text-danger-600">{factorComboError}</p>
                      )}
                    </div>
                  )}

                  {/* Over-budget warning */}
                  {isOverBudget && selectedBudgetAccount && (
                    <OverBudgetWarning
                      accountCode={selectedL2}
                      overage={overage}
                      pct={(overage / available) * 100}
                    />
                  )}

                  {/* Hard-block notice — replaces justification when Budget Config = hard_block */}
                  {isHardBlock && (
                    <div className="rounded-md border border-danger-300 bg-danger-50 px-4 py-3 text-sm text-danger-700">
                      <p className="font-medium">Submission blocked — over budget</p>
                      <p className="mt-1 text-danger-600">
                        Budget Config is set to <strong>Hard block</strong>. This PR cannot be submitted while it exceeds the available budget for account {selectedL2}. Reduce the budget code's commitment or split the PR into smaller items.
                      </p>
                    </div>
                  )}

                  {/* Justification — required when over-budget AND mode allows pre-approval (fm_only / fm_gm_opm) */}
                  {isOverBudget && !isHardBlock && (
                    <FormField label="Over-Budget Justification" required htmlFor="justification" error={errors.justification?.message}>
                      <textarea
                        id="justification"
                        {...register('justification')}
                        placeholder="Explain why this expenditure is necessary despite exceeding budget…"
                        className="min-h-20 w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                      />
                    </FormField>
                  )}

                  {requiresFixedAsset && (
                    <FormField label="Fixed Asset ID" required htmlFor="fixedAssetId" error={errors.fixedAssetId?.message}>
                      <Input id="fixedAssetId" placeholder="e.g., FA-2026-0001" {...register('fixedAssetId')} />
                    </FormField>
                  )}

                  {requiresProject && (
                    <FormField label="Project Code" required htmlFor="projectCode" error={errors.projectCode?.message}>
                      <Input id="projectCode" placeholder="Search project…" {...register('projectCode')} />
                    </FormField>
                  )}

                  {requiresServiceDate && (
                    <FormField label="Service Expected Completion Date" required htmlFor="serviceDate" error={errors.serviceCompletionDate?.message}>
                      <div className="relative">
                        <Input id="serviceDate" type="date" {...register('serviceCompletionDate')} />
                        <Calendar className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                      </div>
                    </FormField>
                  )}

                  <div className="flex items-center gap-2">
                    <input
                      id="prepayment"
                      type="checkbox"
                      className="h-4 w-4 rounded border-neutral-300 text-primary-600"
                      {...register('prepaymentRequired')}
                    />
                    <label htmlFor="prepayment" className="text-sm text-neutral-700">Prepayment Required</label>
                  </div>

                  {/* Currency */}
                  <FormField label="Currency" required htmlFor="currency">
                    <select
                      id="currency"
                      value={currency}
                      onChange={(e) => setCurrency(e.target.value as Currency)}
                      className="h-10 w-full rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                    >
                      {CURRENCIES.filter((c) => (config?.enabled_currencies ?? ['CAD','USD','EUR','RMB']).includes(c.value)).map((c) => (
                        <option key={c.value} value={c.value}>{c.value} — {c.label}</option>
                      ))}
                    </select>
                  </FormField>

                  {/* Delivery address — optional */}
                  <FormField
                    label="Delivery Address / Location"
                    htmlFor="delivery"
                    error={errors.deliveryAddress?.message}
                    hint={config?.delivery_address ? `Default: ${config.delivery_address}` : 'Leave blank to use company default'}
                  >
                    <Input
                      id="delivery"
                      placeholder={config?.delivery_address || 'e.g., Technical Warehouse, Building A'}
                      {...register('deliveryAddress')}
                    />
                  </FormField>

                  <FormField label="Required By Date" required htmlFor="requiredBy" error={errors.requiredBy?.message}>
                    <div className="relative">
                      <Input id="requiredBy" type="date" error={!!errors.requiredBy} {...register('requiredBy')} />
                      <Calendar className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                    </div>
                  </FormField>
                </div>

                {/* Line Items — separate card */}
                <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
                  <PrLineItems
                    procurementType={selectedType}
                    items={lineItems}
                    onChange={(items) => { setLineItems(items); setLineErrors({}) }}
                    errors={lineErrors}
                  />
                  {Object.keys(lineErrors).length > 0 && (
                    <p className="mt-2 text-xs text-danger-600">Please fix the errors in the line items above.</p>
                  )}
                </div>

                {/* Supporting documents + notes */}
                <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
                  <h2 className="text-base font-semibold text-neutral-900">Supporting Documents &amp; Notes</h2>

                  <div className="flex flex-col gap-2">
                    <label className="text-sm font-medium text-neutral-700">Attachments</label>
                    <label
                      htmlFor="file-upload"
                      className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed border-neutral-300 p-6 text-center hover:border-primary-400 hover:bg-primary-50 transition-colors"
                    >
                      <Upload className="h-8 w-8 text-neutral-400" />
                      <div>
                        <p className="text-sm font-medium text-neutral-700">Drag &amp; drop or click to upload</p>
                        <p className="text-xs text-neutral-400 mt-1">Any format · Max 25 MB per file</p>
                      </div>
                      <input id="file-upload" type="file" multiple className="sr-only" onChange={handleFileInput} />
                    </label>
                    {attachments.map((f, i) => (
                      <div key={i} className="flex items-center gap-2 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm">
                        <span className="flex-1 truncate text-neutral-700">📎 {f.name}</span>
                        <span className="text-neutral-400 text-xs">{(f.size / 1024 / 1024).toFixed(1)} MB</span>
                        <button
                          type="button"
                          onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                          className="text-neutral-400 hover:text-danger-600"
                          aria-label="Remove file"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>

                  <FormField label="Requester Notes" htmlFor="notes">
                    <textarea
                      id="notes"
                      {...register('notes')}
                      placeholder="Any additional context for the approver…"
                      className="min-h-20 w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                    />
                  </FormField>
                </div>

                {/* Action bar — inside 8-col column so it aligns with cards above */}
                <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-6 py-4">
                  <Link to="/pr">
                    <Button variant="secondary">Cancel</Button>
                  </Link>
                  <div className="flex items-center gap-3">
                    <Button type="button" variant="secondary" disabled={isSubmitting} onClick={handleDraftSave}>Save as Draft</Button>
                    <Button type="submit" disabled={!selectedType || isSubmitting || isHardBlock}>
                      {isSubmitting ? 'Submitting…' : 'Submit for Approval'}
                    </Button>
                  </div>
                </div>
              </>
            )}

            {/* Action bar fallback when no type selected yet */}
            {!selectedType && (
              <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-6 py-4">
                <Link to="/pr">
                  <Button variant="secondary">Cancel</Button>
                </Link>
                <Button type="submit" disabled>Submit for Approval</Button>
              </div>
            )}
          </div>

          {/* Sidebar */}
          <div className="lg:col-span-3 flex flex-col gap-4">
            {/* Budget balance widget */}
            {selectedL2 && selectedBudgetAccount && (
              <BudgetBalanceWidget
                data={{
                  accountCode: selectedL2,
                  accountName: selectedBudgetAccount.name,
                  annualBudget: annualBudget,
                  committed: committed,
                  actualSpent: actualSpent,
                }}
                thisAmount={estimatedAmount}
              />
            )}

            {/* Tips card */}
            <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-4">
              <h3 className="text-sm font-semibold text-neutral-700 mb-3">💡 Tips</h3>
              <ul className="space-y-2 text-xs text-neutral-500">
                <li>• Select the procurement type that best matches your purchase</li>
                <li>• Type 1 (Raw Materials) does not require a budget code</li>
                <li>• Add all items in the Line Items table — totals update automatically</li>
                <li>• Types 1 &amp; 3 include a Material ID field per line item</li>
                <li>• If over-budget, provide a detailed justification</li>
                <li>• Attach vendor quotes to speed up approval</li>
              </ul>
            </div>
          </div>
        </div>
      </form>
    </div>
  )
}
