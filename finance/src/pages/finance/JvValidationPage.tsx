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
 *
 * Not covered here at all: payroll (CRM007), depreciation (CRM004) and
 * shut-down loss (CRM09912). They are kept out of the per-cost-center dashboard
 * by decision, so holding them to rules about a placement they are never given
 * would report thousands of lines nobody can act on.
 */
import { useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Download, Loader2 } from 'lucide-react'
import { SortTh, sortParams, useSort, Input, TBody, THead, Table, Td, Th, Tr, Button } from '@uniops/shell'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn, todayISO } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-line-strong bg-surface px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'

interface RuleSummary {
  rule: string; label: string
  drops_from_dashboard: boolean; needs_decision: boolean
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
/** GET /gl/jv-validation/lines sort keys (finance-api jv_validation.ROW_SORT). */
type LineSortKey = 'jv_number' | 'account_code' | 'debit' | 'cost_center' | 'nc_dept'
  | 'income_expense' | 'finding'
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
    'The line has no income-expense dimension at all, so there is no budget account to post it against. Applies only to the families NC books by income-expense item — 6603 financial expenses are checked by account instead. Fix: fill it in NC.',
  category_mismatch:
    'The account belongs to one expense category and the cost center to another (e.g. a 5101 manufacturing line in a GA-* cost center). The money lands in the other category’s budget.',
  department_mismatch:
    'The cost center’s department segment differs from the department on the line, and the mapping table does not declare that pairing on purpose.',
  account_not_in_catalog:
    'Financial expenses (6603) are budgeted BY ACCOUNT — NC puts no income-expense item on them, and each account has a budget account of the same code (660301 Interest income, 660303 Bank Charge, …). This account has none. Fix: add a budget account with exactly this code.',
  cc_map_drift:
    'Re-syncing today would place this line in a DIFFERENT cost center than the one stored on it — the mapping table changed after the line was imported. Use this to preview a mapping change before running a full re-sync.',
}

function money(v: string) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisYear() { return Number(todayISO().slice(0, 4)) }   // the plant's year

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
  // Paged by the API, so sorted by the API; a new sort starts at the top.
  const [sort, onSort] = useSort<LineSortKey>(null, () => setOffset(0))
  const { sort: sortKey, dir: sortDir } = sortParams(sort)
  const sortSuffix = sortKey ? `&sort=${sortKey}&dir=${sortDir}` : ''

  const { data: summary, isFetching: loadingSummary } = useQuery({
    queryKey: ['jv-validation', from, to],
    queryFn: () => financeApi.get<SummaryResp>(`/gl/jv-validation?${range}`),
  })

  const { data: lines, isFetching: loadingLines } = useQuery({
    queryKey: ['jv-validation-lines', from, to, rule, offset, sort],
    queryFn: () => financeApi.get<LinesResp>(
      `/gl/jv-validation/lines?${range}&limit=${PAGE}&offset=${offset}` +
      (rule ? `&rule=${rule}` : '') + sortSuffix),
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
        (rule ? `&rule=${rule}` : '') + sortSuffix)
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
          <Input type="month" value={from} onChange={(e) => { setFrom(e.target.value); setOffset(0) }}
                 className={cn(inputCls, 'w-40')} />
          <span className="text-sm text-fg-muted">to</span>
          <Input type="month" value={to} onChange={(e) => { setTo(e.target.value); setOffset(0) }}
                 className={cn(inputCls, 'w-40')} />
          <Button variant="secondary" size="sm" onClick={exportCsv}
                  className="ml-auto">
            <Download className="h-4 w-4" /> Export CSV
          </Button>
        </div>

        <p className="mb-3 text-xs text-fg-muted">
          Payroll, depreciation and shut-down loss are not validated here — they are
          kept out of the per-cost-center dashboard on purpose.
        </p>

        {droppedLines > 0 && (
          <div className="mb-4 rounded-lg border border-warning-line bg-warning-soft px-4 py-3 text-sm text-warning-fg">
            <strong>{droppedLines.toLocaleString()} posted lines ({money(String(droppedDebit))} CAD debit)</strong>{' '}
            are missing from the Budget Dashboard for this range. Cost centers will not add
            up to the company total until these are placed.
          </div>
        )}

        {loadingSummary && !summary ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-fg-muted" /></div>
        ) : (
          <div className="mb-5 overflow-hidden rounded-lg border border-line">
            <Table density="compact">
              <THead>
                <Tr className="bg-surface-subtle text-xs text-fg-muted">
                  <Th>Finding</Th>
                  <Th align="right">Lines</Th>
                  <Th align="right">Vouchers</Th>
                  <Th align="right">Debit</Th>
                  <Th>Effect</Th>
                </Tr>
              </THead>
              <TBody>
                <Tr className={cn('border-t border-line-subtle', rule === null && 'bg-primary-50/60')}>
                  <Td>
                    <Button variant="link" size="xs" onClick={() => pick(null)}>All findings</Button>
                  </Td>
                  <Td align="right" className="font-mono">{lines?.total ?? '—'}</Td>
                  <Td></Td>
                  <Td></Td>
                  <Td></Td>
                </Tr>
                {(summary?.rules ?? []).map((r) => (
                  <Tr key={r.rule}
                      className={cn('border-t border-line-subtle', rule === r.rule && 'bg-primary-50/60',
                                    r.lines === 0 && 'text-fg-muted')}>
                    <Td>
                      <Button variant="link" size="xs" className={cn(r.lines === 0 && 'text-fg-muted hover:no-underline')}
                              disabled={r.lines === 0} onClick={() => pick(r.rule)}
                              title={RULE_HELP[r.rule]}>
                        {r.label}
                      </Button>
                    </Td>
                    <Td align="right" className="font-mono">{r.lines.toLocaleString()}</Td>
                    <Td align="right" className="font-mono">{r.vouchers.toLocaleString()}</Td>
                    <Td align="right" className="font-mono">{money(r.debit)}</Td>
                    <Td>
                      <span className={cn('rounded px-2 py-0.5 text-xs font-medium',
                        r.drops_from_dashboard ? 'bg-danger-soft text-danger-fg'
                          : 'bg-warning-soft text-warning-fg')}>
                        {r.drops_from_dashboard ? 'Dropped from dashboard' : 'Check placement'}
                      </span>
                      {/* A mapping gap is fixed by adding a row; a missing
                          placement rule is not fixable at all until a person
                          decides where the spend belongs. Different queue. */}
                      {r.needs_decision && r.lines > 0 && (
                        <span className="ml-1.5 rounded bg-cat4-soft px-2 py-0.5 text-xs font-medium text-cat4-fg">
                          Needs a decision
                        </span>
                      )}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
        )}

        {rule && (
          <p className="mb-3 rounded-lg bg-surface-subtle px-3 py-2 text-xs leading-relaxed text-fg-secondary">
            {RULE_HELP[rule]}
          </p>
        )}

        <div className="overflow-hidden rounded-lg border border-line">
          <div className="overflow-x-auto">
            <Table density="compact">
              <THead>
                <Tr className="bg-surface-subtle text-xs text-fg-muted">
                  <SortTh label="Voucher" sortKey="jv_number" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
                  <SortTh label="Account" sortKey="account_code" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
                  <SortTh label="Debit" sortKey="debit" sort={sort} onSort={onSort} align="right" className="px-3 py-2 font-medium" />
                  <SortTh label="Cost Center" sortKey="cost_center" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
                  <SortTh label="NC / Dept" sortKey="nc_dept" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
                  <SortTh label="Income / Expense" sortKey="income_expense" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
                  <SortTh label="Finding" sortKey="finding" sort={sort} onSort={onSort} className="px-3 py-2 font-medium text-left" />
                  <Th className="w-20"></Th>
                </Tr>
              </THead>
              <TBody>
                {loadingLines && !lines && (
                  <Tr><Td align="center" colSpan={8}>
                    <Loader2 className="mx-auto h-4 w-4 animate-spin text-fg-muted" /></Td></Tr>
                )}
                {lines && lines.rows.length === 0 && (
                  <Tr><Td align="center" colSpan={8} className="text-xs text-fg-muted">
                    Nothing flagged in this range. Every posted line reaches a budget cell.
                  </Td></Tr>
                )}
                {(lines?.rows ?? []).map((r, i) => (
                  <Tr key={`${r.jv_id}-${r.line_no}-${r.rule}`}
                      className={cn('border-t border-line-subtle', i % 2 && 'bg-surface-subtle/40')}>
                    <Td>
                      <span className="font-mono text-xs">{r.jv_number}</span>
                      <span className="ml-1 text-xs text-fg-muted">#{r.line_no}</span>
                      <div className="text-xs text-fg-muted">{r.fiscal_period}</div>
                    </Td>
                    <Td>
                      <span className="font-mono text-xs">{r.account_code}</span>
                      <div className="text-xs text-fg-muted">{r.category}</div>
                    </Td>
                    <Td align="right" className="font-mono">{money(r.debit)}</Td>
                    <Td>
                      {r.cost_center_code
                        ? <span className="font-mono text-xs">{r.cost_center_code}</span>
                        : <span className="text-xs text-danger-fg">(none)</span>}
                      {r.expected_cost_center_code && r.expected_cost_center_code !== r.cost_center_code && (
                        <div className="text-xs text-fg-muted">
                          would be <span className="font-mono">{r.expected_cost_center_code}</span>
                        </div>
                      )}
                    </Td>
                    <Td className="text-xs text-fg-secondary">
                      <span className="font-mono">{r.nc_cost_center_code ?? '—'}</span>
                      {' · '}
                      <span className="font-mono">{r.department_code ?? '—'}</span>
                    </Td>
                    <Td className="text-xs">
                      {r.budget_account_code
                        ? <span className="font-mono">{r.budget_account_code}</span>
                        : r.income_expense_text
                          ? <span className="font-mono text-danger-fg">{r.income_expense_text}</span>
                          : <span className="text-danger-fg">(none)</span>}
                    </Td>
                    <Td className="text-xs text-fg-secondary">{r.label}</Td>
                    <Td align="right">
                      <Button variant="link" size="xs" onClick={() => setJvId(r.jv_id)}>Voucher</Button>
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
          {lines && lines.total > PAGE && (
            <div className="flex items-center justify-between border-t border-line-subtle px-3 py-2 text-xs text-fg-muted">
              <span>{offset + 1}–{Math.min(offset + PAGE, lines.total)} of {lines.total.toLocaleString()}</span>
              <span className="flex gap-2">
                <Button variant="link" size="xs"  disabled={offset === 0}
                        onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
                <Button variant="link" size="xs"  disabled={offset + PAGE >= lines.total}
                        onClick={() => setOffset(offset + PAGE)}>Next</Button>
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
