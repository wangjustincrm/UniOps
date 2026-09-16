/**
 * JV Validation — posted voucher lines that cannot reach a budget cell.
 *
 * The Budget Dashboard's NC actual only counts lines carrying a resolved cost
 * center AND a resolved income-expense item. A line failing either is not shown
 * as wrong anywhere; it is simply absent, which is why a cost center can read 0
 * against a real budget and why the cost centers stop adding up to the company
 * total. This page is where those lines become visible.
 *
 * Read-only. Each row drills into its voucher; the fix is in NC, in the
 * budget-actual cost center map, or in the budget account catalog.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Download, Loader2 } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const linkBtn = 'text-xs font-medium text-[#085E5E] hover:underline'

interface RuleSummary {
  rule: string; label: string
  drops_from_dashboard: boolean; needs_decision: boolean; by_policy: boolean
  lines: number; vouchers: number; debit: string; credit: string
}
interface SummaryResp { period_from: string; period_to: string; rules: RuleSummary[] }

interface LineRow {
  rule: string; label: string
  jv_id: string; jv_number: string; fiscal_period: string
  voucher_date: string | null; voucher_summary: string | null
  line_no: number; account_code: string; category: string; line_summary: string | null
  debit: string; credit: string
  cost_center_code: string | null; nc_cost_center_code: string | null
  department_code: string | null; expected_cost_center_code: string | null
  budget_account_code: string | null; income_expense_text: string | null
}
interface LinesResp { total: number; limit: number; offset: number; rows: LineRow[] }

const PAGE = 200

// What each finding means in the cell, and what closes it. The rule codes come
// from finance-api/app/services/jv_validation.py's RULES.
const RULE_HELP: Record<string, string> = {
  cost_center_no_map_for_department:
    'The budget-actual cost center map has NO row for this account × department, so there is nothing to correct until someone decides which cost center this spend belongs to. Until then the amount sits outside every cost-center budget. Fix: finance rules on the placement, then the mapping row is added and the vouchers are re-synced.',
  cost_center_code_unmatched:
    'The map knows this account × department, but none of its rows match the cost center code NC put on the voucher (e.g. the map lists Q01 and NC filled QA), and there is no ALL wildcard to fall back on. Fix: add the mapping row for that NC code, or widen the existing row to ALL, then re-sync.',
  income_expense_unresolved:
    'The voucher carries an income-expense code that has no budget account with that exact code (NC codes carry a CRM prefix; the catalog must match). Fix: add or rename the budget account, then re-sync.',
  income_expense_missing:
    'The line has no income-expense dimension at all, so there is no budget account to post it against. Fix: fill it in NC.',
  category_mismatch:
    'The account belongs to one expense category and the cost center to another (e.g. a 5101 manufacturing line in a GA-* cost center). The money lands in the other category’s budget.',
  department_mismatch:
    'The cost center’s department segment differs from the department on the line, and the mapping table does not declare that pairing on purpose.',
  excluded_by_policy:
    'Not a defect. Payroll (CRM007), Depreciation (CRM004) and shut-down loss (CRM09912) are kept out of the per-cost-center dashboard on purpose — payroll and depreciation are tracked at category level, and shut-down loss is not budgeted per cost center. Listed here only so the amount stays visible and nothing is silently missing from both places.',
  cc_map_drift:
    'Re-syncing today would place this line in a DIFFERENT cost center than the one stored on it — the mapping table changed after the line was imported. Use this to preview a mapping change before running a full re-sync.',
}

function money(v: string) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisYear() { return new Date().getFullYear() }

function csvEscape(v: unknown) {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export default function JvValidationPage() {
  const { user } = useAuthStore()
  const [from, setFrom] = useState(`${thisYear()}-01`)
  const [to, setTo] = useState(`${thisYear()}-12`)
  const [rule, setRule] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [jvId, setJvId] = useState<string | null>(null)

  const range = `period_from=${from}&period_to=${to}`

  const { data: summary, isFetching: loadingSummary } = useQuery({
    queryKey: ['jv-validation', from, to],
    queryFn: () => financeApi.get<SummaryResp>(`/gl/jv-validation?${range}`),
  })

  const { data: lines, isFetching: loadingLines } = useQuery({
    queryKey: ['jv-validation-lines', from, to, rule, offset],
    queryFn: () => financeApi.get<LinesResp>(
      `/gl/jv-validation/lines?${range}&limit=${PAGE}&offset=${offset}` +
      (rule ? `&rule=${rule}` : '')),
  })

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })

  const dropped = useMemo(
    () => (summary?.rules ?? []).filter((r) => r.drops_from_dashboard),
    [summary])
  const droppedDebit = dropped.reduce((s, r) => s + Number(r.debit), 0)
  const droppedLines = dropped.reduce((s, r) => s + r.lines, 0)

  const pick = (code: string | null) => { setRule(code); setOffset(0) }

  // Export what is on screen. Deliberately the CURRENT filter, not everything:
  // the file exists to be worked through, and "everything" for a two-year range
  // is thousands of lines nobody triages in one sitting.
  const exportCsv = async () => {
    const all: LineRow[] = []
    for (let off = 0; ; off += 1000) {
      const page = await financeApi.get<LinesResp>(
        `/gl/jv-validation/lines?${range}&limit=1000&offset=${off}` +
        (rule ? `&rule=${rule}` : ''))
      all.push(...page.rows)
      if (all.length >= page.total || page.rows.length === 0) break
    }
    const head = ['Rule', 'Voucher', 'Period', 'Date', 'Line', 'Account', 'Category',
                  'Debit', 'Credit', 'Cost Center', 'NC Cost Center', 'Expected Cost Center',
                  'Department', 'Budget Account', 'NC Income/Expense', 'Summary']
    const body = all.map((r) => [
      r.rule, r.jv_number, r.fiscal_period, r.voucher_date ?? '', r.line_no,
      r.account_code, r.category, r.debit, r.credit,
      r.cost_center_code ?? '', r.nc_cost_center_code ?? '',
      r.expected_cost_center_code ?? '', r.department_code ?? '',
      r.budget_account_code ?? '', r.income_expense_text ?? '',
      r.line_summary ?? '',
    ])
    const csv = [head, ...body].map((row) => row.map(csvEscape).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `jv-validation-${from}_${to}${rule ? `-${rule}` : ''}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/jv-validation"
      title="JV Validation"
      subtitle="Posted voucher lines whose dimensions keep them out of the Budget Dashboard (CAD)"
    >
      <div className="mx-auto max-w-7xl">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <input type="month" value={from} onChange={(e) => { setFrom(e.target.value); setOffset(0) }}
                 className={cn(inputCls, 'w-40')} />
          <span className="text-sm text-neutral-400">to</span>
          <input type="month" value={to} onChange={(e) => { setTo(e.target.value); setOffset(0) }}
                 className={cn(inputCls, 'w-40')} />
          <button onClick={exportCsv}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50">
            <Download className="h-4 w-4" /> Export CSV
          </button>
        </div>

        {droppedLines > 0 && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <strong>{droppedLines.toLocaleString()} posted lines ({money(String(droppedDebit))} CAD debit)</strong>{' '}
            are missing from the Budget Dashboard for this range. Cost centers will not add
            up to the company total until these are placed.
          </div>
        )}

        {loadingSummary && !summary ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="mb-5 overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                  <th className="px-3 py-2 text-left font-medium">Finding</th>
                  <th className="px-3 py-2 text-right font-medium">Lines</th>
                  <th className="px-3 py-2 text-right font-medium">Vouchers</th>
                  <th className="px-3 py-2 text-right font-medium">Debit</th>
                  <th className="px-3 py-2 text-left font-medium">Effect</th>
                </tr>
              </thead>
              <tbody>
                <tr className={cn('border-t border-neutral-100', rule === null && 'bg-primary-50/60')}>
                  <td className="px-3 py-2">
                    <button className={linkBtn} onClick={() => pick(null)}>All findings</button>
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{lines?.total ?? '—'}</td>
                  <td className="px-3 py-2"></td>
                  <td className="px-3 py-2"></td>
                  <td className="px-3 py-2"></td>
                </tr>
                {(summary?.rules ?? []).map((r) => (
                  <tr key={r.rule}
                      className={cn('border-t border-neutral-100', rule === r.rule && 'bg-primary-50/60',
                                    r.lines === 0 && 'text-neutral-400')}>
                    <td className="px-3 py-2">
                      <button className={cn(linkBtn, r.lines === 0 && 'text-neutral-400 hover:no-underline')}
                              disabled={r.lines === 0} onClick={() => pick(r.rule)}
                              title={RULE_HELP[r.rule]}>
                        {r.label}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{r.lines.toLocaleString()}</td>
                    <td className="px-3 py-2 text-right font-mono">{r.vouchers.toLocaleString()}</td>
                    <td className="px-3 py-2 text-right font-mono">{money(r.debit)}</td>
                    <td className="px-3 py-2">
                      <span className={cn('rounded px-2 py-0.5 text-xs font-medium',
                        r.by_policy ? 'bg-neutral-100 text-neutral-600'
                          : r.drops_from_dashboard ? 'bg-red-50 text-red-700'
                          : 'bg-amber-50 text-amber-800')}>
                        {r.by_policy ? 'Excluded by policy'
                          : r.drops_from_dashboard ? 'Dropped from dashboard'
                          : 'Check placement'}
                      </span>
                      {/* A mapping gap is fixed by adding a row; a missing
                          placement rule is not fixable at all until a person
                          decides where the spend belongs. Different queue. */}
                      {r.needs_decision && r.lines > 0 && (
                        <span className="ml-1.5 rounded bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700">
                          Needs a decision
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {rule && (
          <p className="mb-3 rounded-lg bg-neutral-50 px-3 py-2 text-xs leading-relaxed text-neutral-600">
            {RULE_HELP[rule]}
          </p>
        )}

        <div className="overflow-hidden rounded-lg border border-neutral-200">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-neutral-100 bg-neutral-50 text-xs text-neutral-500">
                  <th className="px-3 py-2 text-left font-medium">Voucher</th>
                  <th className="px-3 py-2 text-left font-medium">Account</th>
                  <th className="px-3 py-2 text-right font-medium">Debit</th>
                  <th className="px-3 py-2 text-left font-medium">Cost Center</th>
                  <th className="px-3 py-2 text-left font-medium">NC / Dept</th>
                  <th className="px-3 py-2 text-left font-medium">Income / Expense</th>
                  <th className="px-3 py-2 text-left font-medium">Finding</th>
                  <th className="px-3 py-2 w-20"></th>
                </tr>
              </thead>
              <tbody>
                {loadingLines && !lines && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center">
                    <Loader2 className="mx-auto h-4 w-4 animate-spin text-neutral-400" /></td></tr>
                )}
                {lines && lines.rows.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-xs text-neutral-400">
                    Nothing flagged in this range. Every posted line reaches a budget cell.
                  </td></tr>
                )}
                {(lines?.rows ?? []).map((r, i) => (
                  <tr key={`${r.jv_id}-${r.line_no}-${r.rule}`}
                      className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                    <td className="px-3 py-2">
                      <span className="font-mono text-xs">{r.jv_number}</span>
                      <span className="ml-1 text-xs text-neutral-400">#{r.line_no}</span>
                      <div className="text-xs text-neutral-500">{r.fiscal_period}</div>
                    </td>
                    <td className="px-3 py-2">
                      <span className="font-mono text-xs">{r.account_code}</span>
                      <div className="text-xs text-neutral-500">{r.category}</div>
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{money(r.debit)}</td>
                    <td className="px-3 py-2">
                      {r.cost_center_code
                        ? <span className="font-mono text-xs">{r.cost_center_code}</span>
                        : <span className="text-xs text-red-600">(none)</span>}
                      {r.expected_cost_center_code && r.expected_cost_center_code !== r.cost_center_code && (
                        <div className="text-xs text-neutral-500">
                          would be <span className="font-mono">{r.expected_cost_center_code}</span>
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs text-neutral-600">
                      <span className="font-mono">{r.nc_cost_center_code ?? '—'}</span>
                      {' · '}
                      <span className="font-mono">{r.department_code ?? '—'}</span>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {r.budget_account_code
                        ? <span className="font-mono">{r.budget_account_code}</span>
                        : r.income_expense_text
                          ? <span className="font-mono text-red-600">{r.income_expense_text}</span>
                          : <span className="text-red-600">(none)</span>}
                    </td>
                    <td className="px-3 py-2 text-xs text-neutral-600">{r.label}</td>
                    <td className="px-3 py-2 text-right">
                      <button className={linkBtn} onClick={() => setJvId(r.jv_id)}>Voucher</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {lines && lines.total > PAGE && (
            <div className="flex items-center justify-between border-t border-neutral-100 px-3 py-2 text-xs text-neutral-500">
              <span>{offset + 1}–{Math.min(offset + PAGE, lines.total)} of {lines.total.toLocaleString()}</span>
              <span className="flex gap-2">
                <button className={linkBtn} disabled={offset === 0}
                        onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
                <button className={linkBtn} disabled={offset + PAGE >= lines.total}
                        onClick={() => setOffset(offset + PAGE)}>Next</button>
              </span>
            </div>
          )}
        </div>
      </div>

      {jvId && (
        <JvDetailModal jvId={jvId} canAct={perms?.can_act ?? false}
                       onClose={() => setJvId(null)} />
      )}
    </PortalChromeLayout>
  )
}
