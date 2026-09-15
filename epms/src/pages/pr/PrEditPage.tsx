import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams, Link } from 'react-router-dom'
import { BackLink, useDocTabTitle } from '@/components/BackLink'
import { useReplaceTab } from '@uniops/shell'
import { epmsRoutes } from '@/app/routes'
import { ArrowLeft, X, Calendar, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { FormField } from '@/components/ui/form-field'
import { DropdownPortal, useAnchorRect } from '@/components/ui/DropdownPortal'
import { PrLineItems, lineItemsTotal, validateLineItems } from '@/components/pr/PrLineItems'
import { budgetAccountError } from '@/lib/prBudget'
import { OverBudgetWarning } from '@/components/pr/BudgetBalanceWidget'
import { useConfig } from '@/hooks/useConfig'
import { useBudgetOverview, useBalance, useFactors } from '@/hooks/useBudget'
import { useCostCenters } from '@/hooks/useCostCenters'
import { useDepartments } from '@/hooks/useDepartments'
import { usePr, useUpdatePr, usePrAction } from '@/hooks/usePrs'
import { usePrAttachments, useUploadAttachment, useDeleteAttachment } from '@/hooks/usePrAttachments'
import { prAttachmentService } from '@/services/prAttachments'
import { AttachmentsEditor } from '@/components/shared/AttachmentsEditor'
import { useVendors } from '@/hooks/useVendors'
import { useAuthStore } from '@/stores/auth.store'
import { userService, type ApiUserBrief } from '@/services/users'
import type { ApiVendor } from '@/services/vendors'
import type { ApiBudgetL1 } from '@/services/budget'
import type { PrLineItem, Currency, ProcurementType } from '@/types'
import { CURRENCIES } from '@/types'

const TYPE_LABELS: Record<number, string> = {
  1: 'Raw Mat./Pack.',
  2: 'Consumables',
  3: 'Spare Parts',
  4: 'Service',
  5: 'Fixed Asset',
  6: 'Project',
}

function defaultLine(): PrLineItem {
  return { id: crypto.randomUUID(), description: '', materialId: '', qty: 1, unit: 'pcs', unitPrice: 0, lineTotal: 0, notes: '' }
}

// Budget figures (annual_budget/committed/actual_spent) are per cost-center +
// fiscal year — they live behind /balance, not on the shared catalog account.
const currentFiscalYear = new Date().getUTCFullYear()

export default function PrEditPage() {
  const { id } = useParams()
  const replaceTab = useReplaceTab(epmsRoutes)
  const { user } = useAuthStore()
  const { data: config } = useConfig()
  const { data: pr, isLoading } = usePr(id ?? '')
  useDocTabTitle(pr?.number && `Edit ${pr.number}`)
  const { data: budgetData } = useBudgetOverview()
  const [selectedDepartmentId, setSelectedDepartmentId] = useState<string | undefined>(undefined)
  const { data: departmentsData } = useDepartments()
  const departments = (departmentsData?.items ?? []).filter((d) => d.is_active)
  const { data: costCentersData } = useCostCenters({
    department_id: selectedDepartmentId,
    active_only: true,
  })
  const updatePr = useUpdatePr()
  const prAction = usePrAction(id ?? '')
  // Attachments belong to a PR that already exists, so upload/delete hit the
  // server right away instead of being staged until Save (same semantics as the
  // Detail page's Attachments tab).
  const { data: attachments = [] } = usePrAttachments(id ?? '')
  const uploadAttachment = useUploadAttachment(id ?? '')
  const deleteAttachment = useDeleteAttachment(id ?? '')

  // ── form state ────────────────────────────────────────────────────────────
  const [title, setTitle] = useState('')
  const [currency, setCurrency] = useState<Currency>('CAD')
  const [requiredBy, setRequiredBy] = useState('')
  const [serviceCompletionDate, setServiceCompletionDate] = useState('')
  const [deliveryAddress, setDeliveryAddress] = useState('')
  const [notes, setNotes] = useState('')
  const [projectCode, setProjectCode] = useState('')
  const [isPrepaid, setIsPrepaid] = useState(false)
  const [justification, setJustification] = useState('')
  const [justificationError, setJustificationError] = useState<string | null>(null)
  const [factorCombo, setFactorCombo] = useState<Record<string, string>>({})
  const [factorComboError, setFactorComboError] = useState<string | null>(null)
  const [departmentError, setDepartmentError] = useState<string | null>(null)
  const [budgetError, setBudgetError] = useState<string | null>(null)
  const [lineItems, setLineItems] = useState<PrLineItem[]>([defaultLine()])
  const [lineErrors, setLineErrors] = useState<Record<string, { description?: string; qty?: string; unitPrice?: string }>>({})

  // vendor
  const [vendorQuery, setVendorQuery] = useState('')
  const [vendorOpen, setVendorOpen] = useState(false)
  const [selectedVendor, setSelectedVendor] = useState<ApiVendor | null>(null)

  // service/project owner — mirrors PrCreatePage's picker
  const ownerAnchorRef = useRef<HTMLDivElement>(null)
  const [ownerOpen, setOwnerOpen] = useState(false)
  const ownerRect = useAnchorRect(ownerOpen, ownerAnchorRef)
  const [ownerQuery, setOwnerQuery] = useState('')
  const [selectedOwner, setSelectedOwner] = useState<ApiUserBrief | null>(null)
  const { data: ownersData } = useQuery({
    queryKey: ['users', 'directory', ownerQuery],
    queryFn: () => userService.directory({ search: ownerQuery || undefined }),
    enabled: ownerOpen,
    staleTime: 30_000,
  })
  const owners = ownersData?.items ?? []
  // The saved owner is a bare id; owner_name off the PR response already
  // resolves it (INCLUDING the NULL-means-requester fallback), so the picker is
  // seeded from the two together and no extra lookup is needed. It therefore
  // holds a person from first render, and — like the Create page — cannot be
  // emptied afterwards.
  useEffect(() => {
    if (!pr || selectedOwner) return
    const ownerId = pr.owner_id ?? pr.created_by
    if (!ownerId) return
    setSelectedOwner({
      id: ownerId,
      full_name: pr.owner_name ?? pr.created_by_name ?? '—',
      email: '',
      role: '',
      department_id: null,
      department_name: null,
    })
  }, [pr, selectedOwner])

  // cost center + budget
  const [selectedCostCenter, setSelectedCostCenter] = useState('')
  const [selectedCostCenterId, setSelectedCostCenterId] = useState<string | undefined>(undefined)
  const [selectedL1, setSelectedL1] = useState('')
  const [selectedL1Obj, setSelectedL1Obj] = useState<ApiBudgetL1 | null>(null)
  const [selectedL2, setSelectedL2] = useState('')

  const [isSubmitting, setIsSubmitting] = useState(false)
  const [initialized, setInitialized] = useState(false)

  const costCenters = costCentersData ?? []
  const l1Groups = budgetData?.l1_groups ?? []
  // L1 catalog is shared across all cost centers (budget refactor: no cost_center on L1).
  const ccL1Groups = l1Groups.filter((l) => l.is_active)
  // Only active accounts are selectable; keep the current selection even if it went inactive.
  const ccL2Accounts = (selectedL1Obj?.accounts ?? []).filter((a) => a.is_active || a.code === selectedL2)

  const { data: vendorsData } = useVendors({
    active_only: true,
    search: vendorQuery || undefined,
    page_size: 50,
  })
  const vendors = vendorsData?.items ?? []

  // ── initialize from PR ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!pr || initialized || !budgetData || !costCentersData) return

    setTitle(pr.title)
    setCurrency(pr.currency as Currency)
    setRequiredBy(pr.required_by ?? '')
    setServiceCompletionDate(pr.service_completion_date ?? '')
    setDeliveryAddress(pr.delivery_address ?? '')
    setNotes(pr.notes ?? '')
    setProjectCode(pr.project_code ?? '')
    setIsPrepaid(pr.is_prepaid ?? false)
    setJustification(pr.over_budget_justification ?? '')
    setFactorCombo(pr.factor_combo ?? {})
    setSelectedDepartmentId(pr.department_id ?? user?.department_id ?? undefined)

    // line items
    if (pr.line_items.length > 0) {
      setLineItems(pr.line_items.map((item) => ({
        id: crypto.randomUUID(),
        description: item.description,
        materialId: item.material_id ?? '',
        supplierItemId: item.supplier_item_id ?? '',
        qty: Number(item.qty),
        unit: item.unit,
        unitPrice: Number(item.unit_price),
        lineTotal: Number(item.line_total),
        notes: item.notes ?? '',
      })))
    }

    // vendor — create a minimal object from stored name
    if (pr.vendor_id && pr.vendor_name) {
      setSelectedVendor({ id: pr.vendor_id, name: pr.vendor_name } as ApiVendor)
    }

    // cost center
    if (pr.cost_center_id) {
      const cc = costCentersData.find((c) => c.id === pr.cost_center_id)
      if (cc) {
        setSelectedCostCenter(cc.code)
        setSelectedCostCenterId(cc.id)
      }
    }

    // budget code → find L1 parent
    if (pr.budget_code) {
      setSelectedL2(pr.budget_code)
      for (const l1 of budgetData.l1_groups) {
        const found = l1.accounts.find((a) => a.code === pr.budget_code)
        if (found) {
          setSelectedL1(l1.code)
          setSelectedL1Obj(l1)
          break
        }
      }
    }

    setInitialized(true)
  }, [pr, initialized, budgetData, costCentersData, user])

  const procurementType = pr?.type as ProcurementType | undefined
  const hasMaterial = procurementType === 1 || procurementType === 3
  const estimatedAmount = lineItemsTotal(lineItems)

  // Over-budget computation — matches PrCreatePage logic
  const selectedBudgetAccount = selectedL2
    ? (ccL2Accounts.find((a) => a.code === selectedL2) ?? null)
    : null
  // Factors for the selected account — only fetched when decomposition is enabled.
  const factorsAccountId =
    selectedBudgetAccount && selectedBudgetAccount.decomposition_enabled
      ? selectedBudgetAccount.id
      : null
  const { data: accountFactorsRaw = [] } = useFactors(factorsAccountId)
  const accountFactors = accountFactorsRaw.filter((f) => f.is_active)
  // Budget figures come from /balance (scoped to the chosen cost center + account),
  // NOT the catalog account — the catalog carries no annual_budget/committed/actual_spent.
  const { data: budgetBalance } = useBalance(
    selectedCostCenterId && selectedL2
      ? { cost_center_id: selectedCostCenterId, fiscal_year: currentFiscalYear, account_code: selectedL2 }
      : null,
  )
  const annualBudget = Number(budgetBalance?.annual_budget ?? 0)
  const committed = Number(budgetBalance?.committed ?? 0)
  const actualSpent = Number(budgetBalance?.actual_spent ?? 0)
  const available = annualBudget - committed - actualSpent
  const isOverBudget = selectedBudgetAccount ? estimatedAmount > available && estimatedAmount > 0 : false
  const overage = isOverBudget ? estimatedAmount - available : 0
  const overBudgetMode = config?.budget_admin_config?.over_budget_mode ?? 'fm_gm_opm'
  const isHardBlock = isOverBudget && overBudgetMode === 'hard_block'

  const buildPayload = () => ({
    title: title.trim(),
    currency,
    vendor_id: selectedVendor?.id,
    cost_center_id: selectedCostCenterId,
    department_id: selectedDepartmentId,
    budget_code: selectedL2 || undefined,
    project_code: projectCode || undefined,
    // Only send the combo when every factor is picked (satisfies the server-side
    // shape check); lets a decomposition combo be corrected via Edit.
    factor_combo:
      accountFactors.length > 0 && accountFactors.every((f) => factorCombo[f.factor_code])
        ? factorCombo
        : undefined,
    required_by: requiredBy || undefined,
    service_completion_date: serviceCompletionDate || undefined,
    // Only for the service/project pair — the picker is not rendered otherwise,
    // and PATCH treats undefined as "leave alone". Within the pair it is always
    // sent: the combo box is seeded from owner_id ?? created_by, so it holds a
    // person from first render and what it shows is what gets saved.
    owner_id: (procurementType === 4 || procurementType === 6)
      ? (selectedOwner?.id ?? pr?.created_by)
      : undefined,
    delivery_address: deliveryAddress || undefined,
    notes: notes || undefined,
    is_prepaid: isPrepaid,
    over_budget_justification: isOverBudget ? justification.trim() || undefined : undefined,
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

  const handleSaveDraft = async () => {
    if (!title.trim() || !id) return
    setIsSubmitting(true)
    try {
      await updatePr.mutateAsync({ id, body: buildPayload() })
      replaceTab(`/pr/${id}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!id || !title.trim() || !requiredBy) return
    if ((procurementType === 4 || procurementType === 6) && !serviceCompletionDate) return
    const errs = validateLineItems(lineItems)
    if (Object.keys(errs).length > 0) { setLineErrors(errs); return }
    if (estimatedAmount === 0) { setLineErrors({ '0': { unitPrice: 'At least one line must have a price' } }); return }
    // Hard-block guard — Submit button is already disabled, but guard the
    // programmatic path in case it's invoked some other way.
    if (isHardBlock) return
    // Department is required at submit time: the approval engine raises when it
    // can't resolve a dept_manager for a NULL department.
    if (!selectedDepartmentId) {
      setDepartmentError('Department is required')
      return
    }
    // Budget account is required for every type except Type 1 — matches the
    // epms-api submit guard, which 409s without it. Legacy PRs saved before the
    // guard existed hit this on their next submit; Save as Draft stays lenient
    // so they can still be worked on.
    const budgetErr = budgetAccountError(pr?.type, selectedCostCenterId, selectedL2)
    if (budgetErr) {
      setBudgetError(budgetErr)
      return
    }
    // Factor combo: when the selected Account has decomposition factors, every
    // factor must have a value picked.
    if (accountFactors.length > 0) {
      const missing = accountFactors.filter((f) => !factorCombo[f.factor_code])
      if (missing.length > 0) {
        setFactorComboError(`Select a value for: ${missing.map((f) => f.factor_name).join(', ')}`)
        return
      }
    }
    // Over-budget but pre-approval allowed: require non-empty justification.
    if (isOverBudget && !isHardBlock && !justification.trim()) {
      setJustificationError('Justification is required for over-budget PRs')
      return
    }
    setJustificationError(null)
    setIsSubmitting(true)
    try {
      await updatePr.mutateAsync({ id, body: buildPayload() })
      await prAction.mutateAsync({ action: 'submit' })
      replaceTab(`/pr/${id}`)
    } finally {
      setIsSubmitting(false)
    }
  }

  if (isLoading) {
    return <div className="flex items-center justify-center py-24 text-sm text-neutral-400">Loading…</div>
  }

  if (!pr || !['draft', 'returned'].includes(pr.status)) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <p className="text-sm text-neutral-400">This PR cannot be edited.</p>
        <BackLink to={`/pr/${id}`} className="mt-4">
          <Button variant="secondary">Back to PR</Button>
        </BackLink>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link to={`/pr/${id}`}>
          <Button variant="ghost" size="icon-sm">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-neutral-900">Edit PR</h1>
          <p className="text-sm text-neutral-500">{pr.number} — Type {pr.type}: {TYPE_LABELS[pr.type]}</p>
        </div>
      </div>

      <form onSubmit={handleSubmit} noValidate>
        <div className="flex flex-col gap-6">
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-5">
            <h2 className="text-base font-semibold text-neutral-900">Requisition Details</h2>

            {/* Title */}
            <FormField label="PR Title / Description" required htmlFor="title">
              <Input
                id="title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Brief description of what you need"
              />
            </FormField>

            {/* Vendor */}
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium text-neutral-700">Preferred Vendor</label>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                <input
                  type="text"
                  placeholder="Search vendor by name…"
                  value={selectedVendor ? selectedVendor.name : vendorQuery}
                  onFocus={() => { setVendorOpen(true); if (selectedVendor) setVendorQuery('') }}
                  onChange={(e) => { setVendorQuery(e.target.value); setSelectedVendor(null); setVendorOpen(true) }}
                  className="h-10 w-full rounded-md border border-neutral-300 bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
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
                {vendorOpen && !selectedVendor && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setVendorOpen(false)} />
                    <div className="absolute z-20 mt-1 w-full rounded-lg border border-neutral-200 bg-white py-1 shadow-lg max-h-56 overflow-y-auto">
                      {vendors.map((v) => (
                        <button
                          key={v.id}
                          type="button"
                          className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left"
                          onClick={() => { setSelectedVendor(v); setVendorOpen(false); setVendorQuery('') }}
                        >
                          <span className="font-mono text-xs rounded bg-neutral-100 px-1.5 py-0.5 text-neutral-600">{v.code}</span>
                          {v.name}
                        </button>
                      ))}
                      {vendors.length === 0 && <p className="px-3 py-2 text-sm text-neutral-400">No vendors found</p>}
                    </div>
                  </>
                )}
              </div>
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

            {/* Cost Center + Budget */}
            <div className="flex flex-col gap-2">
              <label className="text-sm font-medium text-neutral-700">Budget Account</label>
              <select
                value={selectedCostCenter}
                onChange={(e) => {
                  const code = e.target.value
                  setSelectedCostCenter(code)
                  setSelectedCostCenterId(costCenters.find((cc) => cc.code === code)?.id)
                  setSelectedL1(''); setSelectedL1Obj(null); setSelectedL2('')
                  setFactorCombo({}); setFactorComboError(null)
                }}
                className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              >
                <option value="">Select Cost Center…</option>
                {costCenters.map((cc) => (
                  <option key={cc.id} value={cc.code}>{cc.name}</option>
                ))}
              </select>
              <select
                value={selectedL1}
                onChange={(e) => {
                  const code = e.target.value
                  setSelectedL1(code)
                  setSelectedL1Obj(ccL1Groups.find((l) => l.code === code) ?? null)
                  setSelectedL2('')
                  setFactorCombo({}); setFactorComboError(null)
                }}
                disabled={ccL1Groups.length === 0}
                className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400"
              >
                <option value="">Select L1 Category…</option>
                {ccL1Groups.map((l1) => (
                  <option key={l1.id} value={l1.code}>{l1.name}</option>
                ))}
              </select>
              <select
                value={selectedL2}
                onChange={(e) => { setSelectedL2(e.target.value); setFactorCombo({}); setFactorComboError(null); setBudgetError(null) }}
                disabled={ccL2Accounts.length === 0}
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
            {isOverBudget && selectedBudgetAccount && available > 0 && (
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
              <FormField label="Over-Budget Justification" required htmlFor="justification-edit" error={justificationError ?? undefined}>
                <textarea
                  id="justification-edit"
                  value={justification}
                  onChange={(e) => { setJustification(e.target.value); if (justificationError) setJustificationError(null) }}
                  placeholder="Explain why this expenditure is necessary despite exceeding budget…"
                  className="min-h-20 w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                />
              </FormField>
            )}

            {/* Currency */}
            <FormField label="Currency" required htmlFor="currency">
              <select
                id="currency"
                value={currency}
                onChange={(e) => setCurrency(e.target.value as Currency)}
                className="h-10 w-full rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              >
                {CURRENCIES.filter((c) => (config?.enabled_currencies ?? ['CAD', 'USD', 'EUR', 'RMB']).includes(c.value)).map((c) => (
                  <option key={c.value} value={c.value}>{c.value} — {c.label}</option>
                ))}
              </select>
            </FormField>

            {/* Delivery address */}
            <FormField label="Delivery Address / Location" htmlFor="delivery">
              <Input
                id="delivery"
                value={deliveryAddress}
                onChange={(e) => setDeliveryAddress(e.target.value)}
                placeholder={config?.delivery_address || 'e.g., Technical Warehouse, Building A'}
              />
            </FormField>

            {/* Required By */}
            <FormField label="Required By Date" required htmlFor="requiredBy">
              <div className="relative">
                <Input
                  id="requiredBy"
                  type="date"
                  value={requiredBy}
                  onChange={(e) => setRequiredBy(e.target.value)}
                />
                <Calendar className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
              </div>
            </FormField>

            {/* Service/Project Expected Completion Date — Types 4 and 6.
                Feeds app/tasks/service_gr_due.py, which nudges the requester to
                create a GR once this date passes. Required on submit server-side. */}
            {(procurementType === 4 || procurementType === 6) && (
              <FormField label="Service/Project Expected Completion Date" required htmlFor="serviceCompletionDate">
                <div className="relative">
                  <Input
                    id="serviceCompletionDate"
                    type="date"
                    value={serviceCompletionDate}
                    onChange={(e) => setServiceCompletionDate(e.target.value)}
                  />
                  <Calendar className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                </div>
              </FormField>
            )}

            {/* Service/Project Owner — Types 4 and 6. Whoever is named here
                receives the confirm-service-delivery task and email once the
                completion date passes, instead of the requester. */}
            {(procurementType === 4 || procurementType === 6) && (
              <div ref={ownerAnchorRef} className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-700" htmlFor="serviceOwner">
                  Service/Project Owner <span className="text-danger-600">*</span>
                </label>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
                  <input
                    id="serviceOwner"
                    type="text"
                    placeholder="Search people by name…"
                    value={ownerOpen ? ownerQuery : (selectedOwner?.full_name ?? '')}
                    onFocus={() => { setOwnerOpen(true); setOwnerQuery('') }}
                    onChange={(e) => { setOwnerQuery(e.target.value); setOwnerOpen(true) }}
                    className="h-10 w-full rounded-md border border-neutral-300 bg-white pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                  />
                </div>
                {ownerOpen && ownerRect && (
                  <DropdownPortal anchorRect={ownerRect} onClose={() => { setOwnerOpen(false); setOwnerQuery('') }}>
                    {owners.map((u) => (
                      <button
                        key={u.id}
                        type="button"
                        className="flex w-full items-center justify-between px-3 py-2 text-sm hover:bg-primary-50 text-left"
                        onClick={() => { setSelectedOwner(u); setOwnerOpen(false); setOwnerQuery('') }}
                      >
                        <span>{u.full_name}</span>
                        {u.department_name && <span className="text-xs text-neutral-400">{u.department_name}</span>}
                      </button>
                    ))}
                    {owners.length === 0 && (
                      <p className="px-3 py-2 text-sm text-neutral-400">No people found</p>
                    )}
                  </DropdownPortal>
                )}
                <p className="text-xs text-neutral-400">
                  Receives the task and email to confirm the service was delivered.
                </p>
              </div>
            )}

            {/* Project Code — Type 6 only */}
            {procurementType === 6 && (
              <FormField label="Project No." required htmlFor="projectCode">
                <input
                  id="projectCode"
                  type="text"
                  value={projectCode}
                  onChange={(e) => setProjectCode(e.target.value)}
                  placeholder="e.g. PROJ-2026-001"
                  className="h-9 w-full rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
                />
              </FormField>
            )}

            {/* Notes */}
            <FormField label="Requester Notes" htmlFor="notes">
              <textarea
                id="notes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Any additional context for the approver…"
                className="min-h-20 w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600"
              />
            </FormField>

            {/* Prepayment Required */}
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="prepayment-edit"
                checked={isPrepaid}
                onChange={(e) => setIsPrepaid(e.target.checked)}
                className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-600"
              />
              <label htmlFor="prepayment-edit" className="text-sm text-neutral-700 cursor-pointer">
                Prepayment Required
              </label>
            </div>
          </div>

          {/* Line Items */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6">
            <PrLineItems
              procurementType={procurementType ?? 2}
              items={lineItems}
              onChange={(items) => { setLineItems(items); setLineErrors({}) }}
              errors={lineErrors}
            />
            {Object.keys(lineErrors).length > 0 && (
              <p className="mt-2 text-xs text-danger-600">Please fix the errors in the line items above.</p>
            )}
          </div>

          {/* Attachments */}
          <div className="rounded-xl bg-white shadow-[0_1px_3px_rgba(10,124,124,0.08)] p-6 flex flex-col gap-3">
            <div>
              <h2 className="text-base font-semibold text-neutral-900">Attachments</h2>
              <p className="text-xs text-neutral-400 mt-1">Uploads and removals are saved immediately.</p>
            </div>
            <AttachmentsEditor
              inputId="pr-edit-file-upload"
              attachments={attachments}
              isUploading={uploadAttachment.isPending}
              isDeleting={deleteAttachment.isPending}
              onUpload={(file) => uploadAttachment.mutateAsync(file)}
              onDelete={(attId) => deleteAttachment.mutate(attId)}
              onDownload={(att) => { void prAttachmentService.download(id!, att.id, att.filename).catch(() => {}) }}
            />
          </div>

          {/* Action bar */}
          <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white px-6 py-4">
            <Link to={`/pr/${id}`}>
              <Button variant="secondary">Cancel</Button>
            </Link>
            <div className="flex items-center gap-3">
              <Button type="button" variant="secondary" disabled={isSubmitting || !title.trim()} onClick={handleSaveDraft}>
                Save as Draft
              </Button>
              <Button type="submit" disabled={isSubmitting || !title.trim() || !requiredBy || isHardBlock}>
                {isSubmitting ? 'Submitting…' : 'Submit for Approval'}
              </Button>
            </div>
          </div>
        </div>
      </form>
    </div>
  )
}
