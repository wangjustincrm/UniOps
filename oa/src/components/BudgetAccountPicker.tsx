import { useState, useMemo, useRef, useEffect, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, ChevronLeft } from 'lucide-react'
import { cn, currentYearLocal, formatAmount } from '@/lib/utils'
import { budgetApi } from '@/lib/api'

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
  value, onChange, accounts, className,
}: {
  value: string
  onChange: (acc: BudgetAccount) => void
  accounts: BudgetAccount[]
  // Trigger sizing — EXP's compact table cell uses the default; TRV's line cards pass text-sm.
  className?: string
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
  const [pos, setPos] = useState<{ left: number; width: number; top?: number; bottom?: number } | null>(null)

  const reposition = () => {
    const el = triggerRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const width = 320
    const estH = 300
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8))
    // Flip above the trigger when there isn't room below.
    const openUp = r.bottom + 4 + estH > window.innerHeight && r.top > estH
    setPos(openUp
      ? { left, width, bottom: window.innerHeight - r.top + 4 }
      : { left, width, top: r.bottom + 4 })
  }

  const openPicker = () => {
    // Jump straight into the selected account's category when re-opening.
    setActiveL1(selected ? selected.l1_id : null)
    setSearch('')
    reposition()
    setOpen(true)
  }
  const close = () => { setOpen(false); setSearch('') }

  useLayoutEffect(() => { if (open) reposition() }, [open, activeL1])
  useEffect(() => {
    if (!open) return
    const handler = () => reposition()
    window.addEventListener('resize', handler)
    window.addEventListener('scroll', handler, true)
    return () => {
      window.removeEventListener('resize', handler)
      window.removeEventListener('scroll', handler, true)
    }
  }, [open])

  const q = search.toLowerCase()
  const filteredL1 = l1Groups.filter((g) => `${g.l1_code} ${g.l1_name}`.toLowerCase().includes(q))
  const filteredAccts = (activeGroup?.accounts ?? []).filter((a) =>
    `${a.code} ${a.name}`.toLowerCase().includes(q)
  )

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close() : openPicker())}
        className={cn(
          'w-full rounded border bg-white px-2 py-1.5 text-left text-xs truncate',
          selected ? 'border-neutral-200 text-neutral-800' : 'border-neutral-200 text-neutral-400',
          className,
        )}
      >
        {selected ? `${selected.l1_code} › ${selected.code} — ${selected.name}` : 'Select account…'}
      </button>
      {open && pos && createPortal(
        <>
          <div className="fixed inset-0 z-40" onClick={close} />
          <div
            className="fixed z-50 rounded-lg border border-neutral-200 bg-white shadow-lg overflow-hidden"
            style={{ top: pos.top, bottom: pos.bottom, left: pos.left, width: pos.width }}
          >
            {/* Header: breadcrumb / back when inside an L1 */}
            <div className="border-b border-neutral-100 p-2">
              {activeGroup && (
                <button
                  type="button"
                  onClick={() => { setActiveL1(null); setSearch('') }}
                  className="mb-2 flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-700"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  <span className="truncate">{activeGroup.l1_name && activeGroup.l1_name !== activeGroup.l1_code ? `${activeGroup.l1_code} — ${activeGroup.l1_name}` : activeGroup.l1_code}</span>
                </button>
              )}
              <input
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={activeGroup ? 'Search accounts…' : 'Search categories…'}
                className="w-full rounded border border-neutral-200 px-2 py-1.5 text-xs focus:outline-none focus:border-primary-400"
              />
            </div>

            <div className="max-h-48 overflow-y-auto">
              {/* Level 1: L1 categories */}
              {!activeGroup && filteredL1.map((g) => (
                <button
                  key={g.l1_id}
                  type="button"
                  onClick={() => { setActiveL1(g.l1_id); setSearch('') }}
                  className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-neutral-50 border-b border-neutral-50"
                >
                  <span className="text-xs font-medium text-neutral-800 truncate">
                    <span className="font-mono text-neutral-500">{g.l1_code}</span>{g.l1_name && g.l1_name !== g.l1_code ? <span className="ml-1">{g.l1_name}</span> : null}
                  </span>
                  <span className="flex items-center gap-1 shrink-0 text-[10px] text-neutral-400">
                    {g.accounts.length}
                    <ChevronRight className="h-3.5 w-3.5" />
                  </span>
                </button>
              ))}
              {!activeGroup && filteredL1.length === 0 && (
                <p className="py-4 text-center text-xs text-neutral-400">No categories found</p>
              )}

              {/* Level 2: accounts within the chosen L1 */}
              {activeGroup && filteredAccts.map((a) => {
                const pct = a.annual_budget > 0
                  ? Math.min(100, Math.round(((a.committed + a.actual_spent) / a.annual_budget) * 100))
                  : 0
                const isLow = a.available < 0
                return (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() => { onChange(a); close() }}
                    className="flex w-full flex-col px-3 py-2 text-left hover:bg-neutral-50 border-b border-neutral-50"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-neutral-800">{a.code} — {a.name}</span>
                      <span className={cn('text-xs font-mono', isLow ? 'text-danger-600' : 'text-neutral-500')}>
                        {formatAmount(a.available)}
                      </span>
                    </div>
                    <div className="mt-1 h-1 w-full rounded-full bg-neutral-100">
                      <div
                        className={cn('h-1 rounded-full', pct >= 90 ? 'bg-danger-400' : pct >= 70 ? 'bg-warning-400' : 'bg-primary-400')}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <p className="mt-0.5 text-[10px] text-neutral-400">
                      Budget: {formatAmount(a.annual_budget)} · Used: {pct}%
                    </p>
                  </button>
                )
              })}
              {activeGroup && filteredAccts.length === 0 && (
                <p className="py-4 text-center text-xs text-neutral-400">No accounts found</p>
              )}
            </div>
          </div>
        </>,
        document.body,
      )}
    </div>
  )
}
