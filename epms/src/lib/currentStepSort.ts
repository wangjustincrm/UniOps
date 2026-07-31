import type { CurrentStep } from '@/types'

// Mirrors ROLE_ORDER in epms-api/app/crud/current_step.py — chain order, not alphabetical.
const ROLE_ORDER: Record<string, number> = {
  supervisor: 10, dept_manager: 20, director: 30, procurement_manager: 35,
  gm_or_opm: 40, finance_bp: 50, finance_manager: 60, vendor_manager: 70,
}

type Rowish = { status: string; current_step?: CurrentStep | null }

/** Status (locale) → step role (chain order, in_review only) → approver name (null last). */
export function compareByStatusThenStep(a: Rowish, b: Rowish): number {
  const s = String(a.status).localeCompare(String(b.status))
  if (s !== 0) return s
  if (a.status !== 'in_review') return 0   // secondary/tertiary apply only within in_review

  const ao = ROLE_ORDER[a.current_step?.role ?? ''] ?? 999
  const bo = ROLE_ORDER[b.current_step?.role ?? ''] ?? 999
  if (ao !== bo) return ao - bo

  const an = a.current_step?.approver_name
  const bn = b.current_step?.approver_name
  if (an && bn) return an.localeCompare(bn)
  if (an) return -1   // named approver before role-pool (null) within the same role
  if (bn) return 1
  return 0
}
