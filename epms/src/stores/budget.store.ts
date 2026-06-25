import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// ─── Types ─────────────────────────────────────────────────────────────────────

export interface BudgetL1 {
  code: string           // e.g. "CRM003"
  name: string
  costCenterCode: string // CostCenter.code, e.g. "CC-MKT-02"
}

export interface BudgetAccount {
  code: string      // e.g. "CRM003-01"
  l1Code: string    // parent L1 code
  name: string
  annualBudget: number
  committed: number
  actualSpent: number
}

// ─── Seed data ─────────────────────────────────────────────────────────────────

const SEED_L1: BudgetL1[] = [
  { code: 'CRM003', name: 'CRM & Customer Systems', costCenterCode: 'CC-MKT-02' },
  { code: 'IT001',  name: 'IT & Technology',        costCenterCode: 'CC-ITS-01' },
  { code: 'HR001',  name: 'Human Resources',         costCenterCode: 'CC-HRS-01' },
  { code: 'MKT001', name: 'Marketing',               costCenterCode: 'CC-MKT-01' },
]

const SEED_ACCOUNTS: BudgetAccount[] = [
  { code: 'CRM003-01', l1Code: 'CRM003', name: 'General CRM Expense', annualBudget: 200000, committed:  85000, actualSpent:  45000 },
  { code: 'CRM003-02', l1Code: 'CRM003', name: 'Implementation',       annualBudget: 200000, committed: 120000, actualSpent:  50000 },
  { code: 'CRM003-03', l1Code: 'CRM003', name: 'Licences',             annualBudget: 100000, committed:  60000, actualSpent:  15000 },
  { code: 'IT001-01',  l1Code: 'IT001',  name: 'General IT',           annualBudget: 300000, committed: 280000, actualSpent:  80000 },
  { code: 'IT001-02',  l1Code: 'IT001',  name: 'Software',             annualBudget: 300000, committed: 320000, actualSpent:  80000 },
  { code: 'IT001-03',  l1Code: 'IT001',  name: 'Hardware',             annualBudget: 200000, committed: 250000, actualSpent:  40000 },
  { code: 'HR001-01',  l1Code: 'HR001',  name: 'Staffing',             annualBudget: 200000, committed:  90000, actualSpent:  30000 },
  { code: 'HR001-02',  l1Code: 'HR001',  name: 'Training',             annualBudget: 100000, committed:  40000, actualSpent:  20000 },
  { code: 'MKT001-01', l1Code: 'MKT001', name: 'Advertising',         annualBudget: 150000, committed: 130000, actualSpent:  60000 },
  { code: 'MKT001-02', l1Code: 'MKT001', name: 'Events',              annualBudget:  80000, committed:  50000, actualSpent:  25000 },
]

// ─── Store ─────────────────────────────────────────────────────────────────────

interface BudgetStoreState {
  l1Groups: BudgetL1[]
  accounts: BudgetAccount[]

  // L1 CRUD
  addL1: (l1: BudgetL1) => void
  removeL1: (code: string) => void

  // Account CRUD
  addAccount: (account: BudgetAccount) => void
  updateAccount: (code: string, patch: Partial<Omit<BudgetAccount, 'code'>>) => void
  removeAccount: (code: string) => void
  /** Upsert a batch of accounts from CSV import; upserts L1 groups (create missing, update costCenterCode on existing) */
  importAccounts: (accounts: BudgetAccount[], l1Updates: Pick<BudgetL1, 'code' | 'costCenterCode'>[]) => void
}

export const useBudgetStore = create<BudgetStoreState>()(
  persist(
    (set) => ({
      l1Groups: SEED_L1,
      accounts: SEED_ACCOUNTS,

      addL1: (l1) =>
        set((s) => ({ l1Groups: [...s.l1Groups, l1] })),

      removeL1: (code) =>
        set((s) => ({
          l1Groups: s.l1Groups.filter((g) => g.code !== code),
          accounts: s.accounts.filter((a) => a.l1Code !== code),
        })),

      addAccount: (account) =>
        set((s) => ({ accounts: [...s.accounts, account] })),

      updateAccount: (code, patch) =>
        set((s) => ({
          accounts: s.accounts.map((a) => (a.code === code ? { ...a, ...patch } : a)),
        })),

      removeAccount: (code) =>
        set((s) => ({ accounts: s.accounts.filter((a) => a.code !== code) })),

      importAccounts: (incoming, l1Updates) =>
        set((s) => {
          // Full replacement: only keep data from the new CSV
          const existingL1Map = new Map(s.l1Groups.map((g) => [g.code, g]))
          const l1CcMap = new Map(l1Updates.map((u) => [u.code, u.costCenterCode]))
          const seenL1Codes = [...new Set(incoming.map((a) => a.l1Code))]
          const newL1Groups: BudgetL1[] = seenL1Codes.map((code) => ({
            code,
            // Preserve existing L1 name if known; otherwise use code as placeholder
            name: existingL1Map.get(code)?.name ?? code,
            costCenterCode: l1CcMap.get(code) ?? existingL1Map.get(code)?.costCenterCode ?? '',
          }))
          return {
            accounts: incoming,
            l1Groups: newL1Groups,
          }
        }),
    }),
    {
      name: 'epms-budget',
      merge: (persisted, current) => ({
        ...current,
        ...(persisted as BudgetStoreState),
      }),
    }
  )
)

// ─── Selectors / helpers ───────────────────────────────────────────────────────

/** Aggregate totals for a set of accounts */
export function sumAccounts(accounts: BudgetAccount[]) {
  return accounts.reduce(
    (acc, a) => ({
      annualBudget: acc.annualBudget + a.annualBudget,
      committed:    acc.committed    + a.committed,
      actualSpent:  acc.actualSpent  + a.actualSpent,
    }),
    { annualBudget: 0, committed: 0, actualSpent: 0 }
  )
}

/** Utilisation % = (committed + actualSpent) / annualBudget × 100 */
export function utilisationPct(committed: number, actualSpent: number, annualBudget: number): number {
  if (annualBudget === 0) return 0
  return Math.round(((committed + actualSpent) / annualBudget) * 100)
}

// ─── CSV helpers ──────────────────────────────────────────────────────────────

const CSV_HEADER = 'code,l1Code,costCenterCode,name,annualBudget,committed,actualSpent'

/** Serialise all accounts to a CSV string (includes costCenterCode derived from l1Groups) */
export function exportBudgetCsv(accounts: BudgetAccount[], l1Groups: BudgetL1[]): string {
  const l1Map = new Map(l1Groups.map((l) => [l.code, l.costCenterCode]))
  const rows = accounts.map((a) =>
    [a.code, a.l1Code, l1Map.get(a.l1Code) ?? '', `"${a.name.replace(/"/g, '""')}"`, a.annualBudget, a.committed, a.actualSpent].join(',')
  )
  return [CSV_HEADER, ...rows].join('\n')
}

export interface BudgetImportResult {
  upserted: number
  errors: number
  /** All L1 groups seen in the import (used to upsert costCenterCode) */
  l1Updates: Pick<BudgetL1, 'code' | 'costCenterCode'>[]
  /** L1 codes that were not already in l1Groups (for display only) */
  newL1Codes: string[]
}

/** Parse a CSV string and return upsert-ready accounts + error count.
 *  Column format: code, l1Code, costCenterCode, name, annualBudget, committed, actualSpent
 *  Also accepts legacy 6-col format (no costCenterCode). */
export function parseBudgetCsv(
  text: string,
  existingL1Groups: BudgetL1[]
): { accounts: BudgetAccount[]; result: BudgetImportResult } {
  const lines = text.split(/\r?\n/).filter((l) => l.trim())
  const dataLines = lines[0]?.toLowerCase().startsWith('code') ? lines.slice(1) : lines

  const accounts: BudgetAccount[] = []
  let errors = 0
  const knownL1 = new Set(existingL1Groups.map((g) => g.code))
  const newL1Codes = new Set<string>()
  const l1CcMap = new Map<string, string>() // l1Code → costCenterCode

  for (const line of dataLines) {
    const parts = line.match(/("(?:[^"]|"")*"|[^,]*),?/g)?.map((p) =>
      p.replace(/,$/, '').replace(/^"|"$/g, '').replace(/""/g, '"').trim()
    ) ?? []

    // Detect 7-col (with costCenterCode) vs 6-col (legacy) by checking if col[2] is non-numeric
    const hasExtraCol = parts.length >= 7 || (parts.length >= 3 && isNaN(Number(parts[2])) && !parts[2].startsWith('"'))
    const [code, l1Code, maybeExtra, ...rest] = parts
    const costCenterCode = hasExtraCol ? maybeExtra : ''
    const [nameRaw, annualBudgetStr, committedStr, actualSpentStr] = hasExtraCol ? rest : [maybeExtra, ...rest]

    const annualBudget = Number(annualBudgetStr)
    const committed = Number(committedStr)
    const actualSpent = Number(actualSpentStr)

    if (!code || !l1Code || !nameRaw || isNaN(annualBudget) || isNaN(committed) || isNaN(actualSpent)) {
      errors++
      continue
    }
    if (!knownL1.has(l1Code)) newL1Codes.add(l1Code)
    if (costCenterCode) l1CcMap.set(l1Code, costCenterCode)
    accounts.push({ code, l1Code, name: nameRaw, annualBudget, committed, actualSpent })
  }

  const l1Updates = [...new Set(accounts.map((a) => a.l1Code))].map((l1Code) => ({
    code: l1Code,
    costCenterCode: l1CcMap.get(l1Code) ?? '',
  }))

  return {
    accounts,
    result: { upserted: accounts.length, errors, l1Updates, newL1Codes: [...newL1Codes] },
  }
}
