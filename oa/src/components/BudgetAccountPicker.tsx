import { useState, useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, ChevronLeft } from 'lucide-react'
import { cn, currentYearLocal, formatAmount } from '@/lib/utils'
import { budgetApi } from '@/lib/api'

import { Button, DropdownPortal, Input, MenuItem, PickerTrigger, useAnchorRect } from '@uniops/shell'
// Shared by the General Expense (EXP) and Travel (TRV) claim forms so both pick
// budget accounts the same way: L1 category first, then an account within it.

export interface BudgetAccount {
  id: string
  code: string
  name: string
  l1_id: string
  l1_code: string
  l1_name: string
  annual_budget: number
  committed: number
  actual_spent: number
  available: number
}

export function useBudgetAccounts() {
  // Budget catalog + per-account figures now live in budget-api (:8007); the old
  // expense-api /budget/accounts route was removed. /actuals/summary returns every
  // active account with annual_budget/committed/actual_spent/available for the year.
  const fiscalYear = currentYearLocal()
  return useQuery<BudgetAccount[]>({
    queryKey: ['budget-accounts', fiscalYear],
    queryFn: async () => {
      // /actuals/summary carries account figures + l1_id/l1_code; /l1 carries l1 names.
      const [res, l1s] = await Promise.all([
        budgetApi.get<{
          accounts: Array<{
            account_id: string; account_code: string; account_name: string
            l1_id: string; l1_code: string
            annual_budget: number; committed: number; actual_spent: number; available: number
          }>
        }>(`/actuals/summary?fiscal_year=${fiscalYear}`),
        budgetApi.get<Array<{ id: string; code: string; name: string }>>('/l1'),
      ])
      const l1NameById = new Map(l1s.map((l) => [l.id, l.name]))
      // Decimal columns arrive as JSON strings — coerce before arithmetic.
      return res.accounts.map((a) => ({
        id: a.account_id,
        code: a.account_code,
        name: a.account_name,
        l1_id: a.l1_id,
        l1_code: a.l1_code,
        l1_name: l1NameById.get(a.l1_id) ?? a.l1_code,
        annual_budget: Number(a.annual_budget),
        committed: Number(a.committed),
        actual_spent: Number(a.actual_spent),
        available: Number(a.available),
      }))
    },
  })
}

// ── Budget account picker ─────────────────────────────────────────────────────

interface L1Group {
  l1_id: string
  l1_code: string
  l1_name: string
  accounts: BudgetAccount[]
}

export function AccountPicker({
  value, onChange, accounts, className, invalid,
}: {
  value: string
  onChange: (acc: BudgetAccount) => void
  accounts: BudgetAccount[]
  // Trigger sizing — EXP's compact table cell uses the default; TRV's line cards pass text-sm.
  className?: string
  // Outline the trigger in red: the last save found this line without an account.
  invalid?: boolean
}) {
  const [search, setSearch] = useState('')
  const [open, setOpen] = useState(false)
  // Two-level drill-down: pick an L1 category first, then an account within it.
  const [activeL1, setActiveL1] = useState<string | null>(null)
  const selected = accounts.find((a) => a.id === value)

  const l1Groups = useMemo<L1Group[]>(() => {
    const m = new Map<string, L1Group>()
    for (const a of accounts) {
      if (!m.has(a.l1_id)) {
        m.set(a.l1_id, { l1_id: a.l1_id, l1_code: a.l1_code, l1_name: a.l1_name, accounts: [] })
      }
      m.get(a.l1_id)!.accounts.push(a)
    }
    return Array.from(m.values()).sort((x, y) => x.l1_code.localeCompare(y.l1_code))
  }, [accounts])

  const activeGroup = l1Groups.find((g) => g.l1_id === activeL1) ?? null

  // The popover renders in a portal on <body> with fixed positioning so it can
  // escape the Line Items card (overflow-hidden) and the table's overflow-x-auto.
  const triggerRef = useRef<HTMLButtonElement>(null)
  const triggerRect = useAnchorRect(open, triggerRef)

  const openPicker = () => {
    // Jump straight into the selected account's category when re-opening.
    setActiveL1(selected ? selected.l1_id : null)
    setSearch('')
    setOpen(true)
  }
  const close = () => { setOpen(false); setSearch('') }


  const q = search.toLowerCase()
  const filteredL1 = l1Groups.filter((g) => `${g.l1_code} ${g.l1_name}`.toLowerCase().includes(q))
  const filteredAccts = (activeGroup?.accounts ?? []).filter((a) =>
    `${a.code} ${a.name}`.toLowerCase().includes(q)
  )
  // Searching at the category level also finds accounts directly, so the user
  // need not know which category holds e.g. "Training" (ISS-0200). One click on
  // a hit selects it, the same as picking it inside its category.
  const matchedAccts = !activeGroup && q
    ? accounts
        .filter((a) => `${a.code} ${a.name}`.toLowerCase().includes(q))
        .sort((x, y) => x.l1_code.localeCompare(y.l1_code) || x.code.localeCompare(y.code))
    : []

  const l1Label = (code: string, name: string) => (name && name !== code ? `${code} ${name}` : code)

  // An L2 account row: available balance and % used (OA §2.2). `withL1` prefixes
  // the category for search hits listed outside their category.
  const accountRow = (a: BudgetAccount, withL1: boolean) => {
    const pct = a.annual_budget > 0
      ? Math.min(100, Math.round(((a.committed + a.actual_spent) / a.annual_budget) * 100))
      : 0
    const isLow = a.available < 0
    return (
      <MenuItem
        key={a.id}
        active={selected?.id === a.id}
        onClick={() => { onChange(a); close() }}
        className="flex-col items-stretch gap-0 border-b border-line-subtle"
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium text-fg">
            {withL1 && <span className="text-fg-muted">{l1Label(a.l1_code, a.l1_name)} › </span>}
            {a.code} — {a.name}
          </span>
          <span className={cn('shrink-0 text-xs font-mono', isLow ? 'text-danger-fg' : 'text-fg-muted')}>
            {formatAmount(a.available)}
          </span>
        </div>
        <div className="mt-1 h-1 w-full rounded-full bg-surface-sunken">
          <div
            className={cn('h-1 rounded-full', pct >= 90 ? 'bg-danger-solid' : pct >= 70 ? 'bg-warning-solid' : 'bg-accent')}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="mt-0.5 text-2xs text-fg-muted">
          Budget: {formatAmount(a.annual_budget)} · Used: {pct}%
        </p>
      </MenuItem>
    )
  }

  return (
    <div className="relative">
      <PickerTrigger
        ref={triggerRef}
        pickerSize="sm"
        error={invalid}
        aria-invalid={invalid || undefined}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => (open ? close() : openPicker())}
        className={cn('text-xs', className)}
        placeholder="Select account…"
      >
        {selected ? `${selected.l1_code} › ${selected.code} — ${selected.name}` : null}
      </PickerTrigger>
      {open && triggerRect && (
        <DropdownPortal anchorRect={triggerRect} onClose={close} width={320} flipHeight={300}
          className="max-h-none overflow-hidden py-0">
            {/* Header: breadcrumb / back when inside an L1 */}
            <div className="border-b border-line-subtle p-2">
              {activeGroup && (
                <Button variant="link"
                  type="button"
                  onClick={() => { setActiveL1(null); setSearch('') }}
                  className="mb-2 text-xs"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  <span className="truncate">{activeGroup.l1_name && activeGroup.l1_name !== activeGroup.l1_code ? `${activeGroup.l1_code} — ${activeGroup.l1_name}` : activeGroup.l1_code}</span>
                </Button>
              )}
              <Input inputSize="sm"
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={activeGroup ? 'Search accounts…' : 'Search categories or accounts…'}
                className="w-full"
              />
            </div>

            <div className="max-h-48 overflow-y-auto">
              {/* Level 1: L1 categories */}
              {!activeGroup && filteredL1.map((g) => (
                <MenuItem
                  key={g.l1_id}
                  onClick={() => { setActiveL1(g.l1_id); setSearch('') }}
                  className="justify-between border-b border-line-subtle"
                >
                  <span className="text-xs font-medium text-fg truncate">
                    <span className="font-mono text-fg-muted">{g.l1_code}</span>{g.l1_name && g.l1_name !== g.l1_code ? <span className="ml-1">{g.l1_name}</span> : null}
                  </span>
                  <span className="flex items-center gap-1 shrink-0 text-2xs text-fg-muted">
                    {g.accounts.length}
                    <ChevronRight className="h-3.5 w-3.5" />
                  </span>
                </MenuItem>
              ))}
              {/* Search hits among all L2 accounts, shown with their category */}
              {matchedAccts.length > 0 && (
                <p className="bg-surface-subtle px-3 py-1 text-2xs font-medium text-fg-muted">
                  Accounts
                </p>
              )}
              {matchedAccts.map((a) => accountRow(a, true))}
              {!activeGroup && filteredL1.length === 0 && matchedAccts.length === 0 && (
                <p className="py-4 text-center text-xs text-fg-muted">
                  {q ? 'No categories or accounts found' : 'No categories found'}
                </p>
              )}

              {/* Level 2: accounts within the chosen L1 */}
              {activeGroup && filteredAccts.map((a) => accountRow(a, false))}
              {activeGroup && filteredAccts.length === 0 && (
                <p className="py-4 text-center text-xs text-fg-muted">No accounts found</p>
              )}
            </div>
        </DropdownPortal>
      )}
    </div>
  )
}
