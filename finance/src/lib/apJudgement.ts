/**
 * What goes stale when finance judges an NC payable.
 *
 * Ignoring a bill is not a fact about the page the click happened on — it
 * changes the open balance, so it changes every page built on that balance.
 * Justin ignored the 2020-08-31 batch on AP Subledger Health and AP Cash Flow
 * carried on showing all 3M of it: the server was already excluding them, but
 * the cash-flow queries were never invalidated, and the tab shell keeps that
 * page mounted, so the stale answer stayed on screen indefinitely.
 *
 * The keys live here, in one list, rather than being repeated at each mutation.
 * Three call sites were already enumerating their own subset, which is exactly
 * how one of them ends up missing the page that matters.
 */
import type { QueryClient } from '@tanstack/react-query'

export const AP_JUDGEMENT_QUERY_KEYS = [
  // AP Subledger Health
  'ap-ledger-health-summary',
  'ap-ledger-health-items',
  'ap-ledger-supplier',
  'ap-ledger-bills',
  'ap-ledger-clusters',
  'ap-ledger-dismissals',
  // AP Cash Flow — every figure on it is the open balance less what was judged
  'ap-cash-flow-summary',
  'ap-cash-flow-items',
  'ap-cash-flow-missing',
] as const

/** Call after any dismiss or restore. */
export function invalidateApJudgement(qc: QueryClient) {
  for (const key of AP_JUDGEMENT_QUERY_KEYS) qc.invalidateQueries({ queryKey: [key] })
}
