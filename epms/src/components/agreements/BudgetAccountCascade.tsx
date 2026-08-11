import { useEffect, useRef, useState, type JSX } from 'react'
import { useBudgetOverview } from '@/hooks/useBudget'
import { useCostCenters } from '@/hooks/useCostCenters'
import type { ApiBudgetL1 } from '@/services/budget'

/**
 * The same Cost Center → L1 Category → L2 Sub-account cascade PrCreatePage
 * uses (see epms/src/pages/pr/PrCreatePage.tsx), lifted into a props-driven
 * component so the Agreement Create/Edit pages share one implementation
 * instead of drifting apart.
 *
 * Controlled by the parent: `costCenterId` and `budgetCode` are the only two
 * values that leave this component (via `onChange`) — the intermediate L1
 * selection is local UI state, same as PrCreatePage's `selectedL1`.
 */
export function BudgetAccountCascade(props: {
  departmentId: string | undefined
  costCenterId: string | undefined
  budgetCode: string
  onChange: (next: { costCenterId?: string; budgetCode: string }) => void
  disabled?: boolean
}): JSX.Element {
  const { departmentId, costCenterId, budgetCode, onChange, disabled } = props

  const { data: budgetData } = useBudgetOverview()
  const { data: costCentersData } = useCostCenters({
    department_id: departmentId,
    active_only: true,
  })

  const l1Groups = budgetData?.l1_groups ?? []
  const costCenters = costCentersData ?? []

  const selectedCostCenter = costCenters.find((cc) => cc.id === costCenterId)
  const selectedCostCenterCode = selectedCostCenter?.code ?? ''

  // L1 catalog is shared across all cost centers (no cost_center on L1) — show
  // every active L1 once a cost center is chosen, same gating PrCreatePage uses.
  const ccL1Groups = selectedCostCenterCode ? l1Groups.filter((l) => l.is_active) : []

  const [selectedL1, setSelectedL1] = useState('')
  const [selectedL1Obj, setSelectedL1Obj] = useState<ApiBudgetL1 | null>(null)

  // Resolve which L1 holds the currently-selected budgetCode — needed when the
  // parent mounts this component with a pre-existing selection (Edit page, or
  // a Create page prefilled by "copy from"). L1 catalog is global, so search
  // every group for the one holding this account, same lookup PrCreatePage
  // does for its copy-from prefill.
  useEffect(() => {
    if (!budgetCode || selectedL1 || !budgetData) return
    for (const l1 of budgetData.l1_groups ?? []) {
      if ((l1.accounts ?? []).some((a) => a.code === budgetCode)) {
        setSelectedL1(l1.code)
        setSelectedL1Obj(l1)
        break
      }
    }
  }, [budgetCode, selectedL1, budgetData])

  // Department changed → the previously-selected cost center may not belong
  // to the new department, so the whole cascade has to clear (leaving it set
  // would write a budget_code that doesn't belong to the chosen department).
  // The `prev === undefined` guard skips the *first* time departmentId goes
  // from unset to a value — that transition is the parent hydrating from
  // fetched data (Edit page), not the user switching departments, and it
  // typically lands in the same render as a correct costCenterId/budgetCode;
  // clearing here would stomp that prefill before it's ever shown.
  const prevDepartmentId = useRef(departmentId)
  useEffect(() => {
    const prev = prevDepartmentId.current
    prevDepartmentId.current = departmentId
    if (prev === departmentId || prev === undefined) return
    setSelectedL1('')
    setSelectedL1Obj(null)
    onChange({ costCenterId: undefined, budgetCode: '' })
    // onChange intentionally excluded — see PrCreatePage's identical pattern.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [departmentId])

  // Only active accounts are selectable; keep the current selection even if
  // it went inactive (same rule as PrCreatePage's ccL2Accounts).
  const ccL2Accounts = (selectedL1Obj?.accounts ?? []).filter(
    (a) => a.is_active || a.code === budgetCode,
  )

  return (
    <div className="flex flex-col gap-2">
      {/* Step 1: Cost Center */}
      <select
        value={selectedCostCenterCode}
        onChange={(e) => {
          const code = e.target.value
          const cc = costCenters.find((c) => c.code === code)
          setSelectedL1('')
          setSelectedL1Obj(null)
          onChange({ costCenterId: cc?.id, budgetCode: '' })
        }}
        disabled={disabled || !departmentId}
        className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400"
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
          onChange({ costCenterId, budgetCode: '' })
        }}
        disabled={disabled || !selectedCostCenterCode}
        className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400"
      >
        <option value="">Select L1 Category…</option>
        {ccL1Groups.map((l1) => (
          <option key={l1.id} value={l1.code}>{l1.name}</option>
        ))}
      </select>
      {/* Step 3: L2 Account */}
      <select
        value={budgetCode}
        onChange={(e) => onChange({ costCenterId, budgetCode: e.target.value })}
        disabled={disabled || !selectedL1}
        className="h-10 rounded-md border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600 disabled:bg-neutral-100 disabled:text-neutral-400"
      >
        <option value="">Select L2 Sub-account…</option>
        {ccL2Accounts.map((l2) => (
          <option key={l2.id} value={l2.code}>{l2.code} — {l2.name}</option>
        ))}
      </select>
    </div>
  )
}
